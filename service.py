from moduleflow import validate_profiles
"""Local line-JSON service for the VS Code extension. One workspace per process."""
import argparse
import os
from localtools import ensure_tool
import json
import pathlib
import sys
import threading
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from autonomy import AutonomousRun, fallback_models
from core import CodeStudioCore
from runmemory import load_candidate
from safety import canonical, redact, safe_path

def local_ollama_url(value):
    """Standalone provider selection never sends project context off this machine."""
    if not isinstance(value, str):
        raise ValueError('Lokale Ollama-Adresse erforderlich.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Ungültige Ollama-Adresse.') from exc
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or port == 0):
        raise ValueError('Ollama muss eine lokale HTTP-Adresse ohne Zugangsdaten oder Pfad sein.')
    return value.rstrip('/')

class StudioService:
    def __init__(self, root, config, emit, transport=None):
        self.emit = emit
        self.transport = transport
        self.core = CodeStudioCore(root, config, log=lambda text: emit({'event':'log','text':redact(text)}),transport=transport)
        self.proposals = {}
        self.active = None
        self.state_lock = threading.Lock()

    def reserve(self, request):
        with self.state_lock:
            if self.active: raise ValueError('Ein autonomer Auftrag läuft bereits.')
            if not self.transport: ensure_tool(self.core.config.get('local_tools_config'),'ollama',self.core.config.get('ollama_url','http://127.0.0.1:11434'))
            self.active = AutonomousRun(self.core.root,self.core.config,request,self.emit,self.transport)
            self.proposals.clear(); self.core.pending.clear()
            return self.active

    def handle(self, request):
        command = request.get('command')
        if command == 'status':
            with self.state_lock:
                run=self.active
                return {'run_id':run.id,'status':run.receipt['status'],'steps':len(run.receipt['steps']),'model_calls':run.core.model_calls,'receipt_path':str(run.path)} if run else {'status':'idle'}
        if command == 'history':
            entries=[]
            for file in sorted(self.core.runs.glob('autonomous-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
                try:
                    r=json.loads(file.read_text(encoding='utf8'))
                    if r.get('workspace')==str(self.core.workspace):entries.append({'run_id':r['run_id'],'status':r['status'],'task':r.get('task','')[:180],'receipt_path':str(file),'resumable':bool(r.get('candidate') and r.get('rollback_verified') and r.get('status') in ('stalled','failed','budget_exhausted','timed_out','cancelled','blocked'))})
                except (ValueError,OSError,KeyError): pass
            return {'runs':entries}
        if command == 'cancel':
            with self.state_lock:
                if not self.active or request.get('run_id') != self.active.id:
                    raise ValueError('Kein passender aktiver autonomer Auftrag.')
                self.active.cancel_event.set()
                return {'status':'cancelling','run_id':self.active.id}
        if command == 'autonomous':
            run = self.reserve(request)
            try: return run.execute()
            finally:
                with self.state_lock: self.active = None
        with self.state_lock:
            if self.active: raise ValueError('Autonomer Auftrag läuft; zuerst stoppen.')
        return self._handle(request)

    def _handle(self, request):
        command = request.get('command')
        if command == 'resume_info':
            p=load_candidate(self.core.runs,request.get('run_id'),self.core.workspace,self.core.config['tests'])
            return {'run_id':p['run_id'],'task':p['task'],'files':list(p['files']),
                'source_model':p['model'],'failure_analysis':p.get('failure_analysis',[])[-4:]}
        if command == 'test':
            if not self.core.config.get('tests'):
                raise ValueError('Zuerst Projekttests konfigurieren.')
            if self.transport:
                self.transport('models', {})
            return self.core.run_tests()
        if command == 'models':
            if self.transport:
                return {**self.transport('models',{}),'workspace':str(self.core.workspace)}
            ensure_tool(self.core.config.get('local_tools_config'),'ollama',self.core.config.get('ollama_url','http://127.0.0.1:11434'))
            return {'models':self.core.installed_models(),'workspace':str(self.core.workspace)}
        if command in ('graphics_models','graphics_submit','graphics_collect'):
            if self.transport: raise ValueError('Grafikwerkzeug benötigt einen eigenen Hostvertrag; derzeit nur Standalone.')
            ensure_tool(self.core.config.get('local_tools_config'),'comfyui',request.get('endpoint','http://127.0.0.1:8189'))
            from comfyassets import ComfyAssets
            assets=ComfyAssets(self.core.workspace,self.core.runs.parent,request.get('endpoint','http://127.0.0.1:8189'))
            if command=='graphics_models': return {'models':assets.models()}
            if command=='graphics_collect': return assets.collect(request.get('asset_id'))
            if request.get('approved') is not True: raise ValueError('Grafikauftrag zuerst freigeben.')
            return assets.submit(request.get('prompt'),request.get('target'),request.get('checkpoint'),request.get('seed',42))
        if command == 'configure':
            context = request.get('context_tokens',16384)
            tests = request.get('test_argv',[])
            output = request.get('output_tokens',self.core.config.get('options',{}).get('num_predict',4096))
            if type(output) is not int or not 256 <= output <= 32768:
                raise ValueError('Ausgabelimit muss zwischen 256 und 32768 Tokens liegen.')
            if self.transport and 'output_tokens' in request:
                raise ValueError('Ausgabelimit wird durch die Hostbindung bestimmt.')
            endpoint = None
            fallbacks = fallback_models(request.get('fallback_models',[]))
            if self.transport and fallbacks:
                raise ValueError('Hostgebundene Modelle werden nicht durch lokale Ersatzmodelle umgangen.')
            if 'ollama_url' in request:
                if self.transport:
                    raise ValueError('Modelladresse wird durch die Hostbindung bestimmt.')
                endpoint = local_ollama_url(request['ollama_url'])
            if type(context) is not int or not 1024 <= context <= 262144:
                raise ValueError('Kontext muss zwischen 1024 und 262144 Tokens liegen.')
            if not isinstance(tests,list) or not all(isinstance(x,str) and x for x in tests):
                raise ValueError('Tests benötigen eine Argumentliste.')
            profiles=validate_profiles(request['test_profiles']) if 'test_profiles' in request else ([{'argv':tests,'timeout_sec':300}] if tests else [])
            if self.transport:
                bound=self.transport('models',{})
                context=bound['options']['num_ctx']
            self.core.config.setdefault('options',{})['num_ctx'] = context
            if not self.transport: self.core.config['options']['num_predict'] = output
            self.core.config['tests'] = profiles
            self.core.config['autonomous_fallback_models'] = fallbacks
            if endpoint is not None:
                self.core.config['ollama_url'] = endpoint
            self.proposals.clear(); self.core.pending.clear()
            return {'context_tokens':context,'output_tokens':None if self.transport else output,'tests_configured':bool(profiles),
                    'test_profiles':profiles,
                    'ollama_url':None if self.transport else self.core.config.get('ollama_url'),
                    'fallback_models':fallbacks}
        if command == 'analyze':
            task, model = request.get('task'), request.get('model')
            if not isinstance(task,str) or not task.strip() or len(task)>24000:
                raise ValueError('Bitte eine begrenzte Coding-Aufgabe eingeben.')
            if not isinstance(model,str) or not model.strip():
                raise ValueError('Installiertes Ollama-Modell wählen.')
            self.proposals.clear(); self.core.pending.clear()
            result = self.core.analyze(task,model)
            tag = result.receipt['tag']
            changes = []
            for rel, after in result.final_state.items():
                file = safe_path(self.core.workspace,rel,True)
                before = file.read_bytes().decode('utf8') if file.is_file() else None
                changes.append({'path':rel,'before':before,'after':after})
            self.proposals[tag] = result
            return {'proposal_id':tag,'workspace':str(self.core.workspace),'plan':result.plan,
                    'diff':result.diff,'changes':changes,'binding':result.binding}
        if command == 'apply':
            tag = request.get('proposal_id')
            if request.get('approved') is not True or tag not in self.proposals:
                raise ValueError('Geprüften aktuellen Vorschlag freigeben.')
            if self.transport: self.transport('models',{})
            result = self.proposals.pop(tag)
            receipt = self.core.apply(result)
            return {'receipt':receipt,'receipt_path':str(self.core.runs/(receipt['tag']+'.json'))}
        if command == 'reject':
            self.proposals.clear(); self.core.pending.clear()
            return {'status':'discarded'}
        raise ValueError('Unbekannter CodeStudio-Befehl.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--state-dir',required=True)
    parser.add_argument('--host-binding')
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parent
    config = json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
    config.update(workspace=args.workspace,state_dir=args.state_dir)
    if getattr(sys,'frozen',False):
        config['local_tools_config']=str(pathlib.Path(os.environ.get('LOCALAPPDATA',pathlib.Path.home()/'AppData/Local'))/'CodeStudio/local-tools.json')
    output_lock = threading.Lock()
    def emit(value):
        with output_lock:
            sys.stdout.buffer.write(canonical(value)+b'\n');sys.stdout.buffer.flush()
    transport = None
    if args.host_binding:
        from hosttransport import HostTransport
        transport=HostTransport(args.host_binding,args.workspace)
    service = StudioService(root,config,emit,transport=transport)
    def dispatch(request, reserved=None):
        try:
            value = reserved.execute() if reserved else service.handle(request)
            if request.get('command')=='autonomous':
                # Full diffs live in the receipt file; do not exceed the protocol bound.
                value = {**value,'receipt':{k:v for k,v in value['receipt'].items() if k not in ('attempts','plan','task','protected_tests')}}
            emit({'id':request.get('id'),'ok':True,'result':value})
        except Exception as exc:
            emit({'id':request.get('id'),'ok':False,'error':redact(str(exc))})
        finally:
            if reserved:
                with service.state_lock: service.active=None
    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            while True:
                line = sys.stdin.buffer.readline(1024*1024+1)
                if not line: return 0
                if len(line)>1024*1024:
                    emit({'ok':False,'error':'CodeStudio-Anfrage zu groß.'});return 1
                request = {}
                try:
                    request = json.loads(line)
                    if not isinstance(request,dict): raise ValueError('Objekt als Anfrage erforderlich.')
                    if request.get('command') in ('cancel','status','history'): dispatch(request)
                    elif service.active:
                        emit({'id':request.get('id'),'ok':False,'error':'Autonomer Auftrag läuft; zuerst stoppen.'})
                    elif request.get('command')=='autonomous':
                        reserved=service.reserve(request)
                        worker.submit(dispatch,request,reserved)
                    else: worker.submit(dispatch,request)
                except Exception as exc:
                    emit({'id':request.get('id') if isinstance(request,dict) else None,'ok':False,'error':redact(str(exc))})
        finally:
            if service.active: service.active.cancel_event.set()

if __name__ == '__main__': raise SystemExit(main())
