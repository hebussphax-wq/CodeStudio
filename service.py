"""Local line-JSON service for the VS Code extension. One workspace per process."""
import argparse
import json
import pathlib
import sys
from core import CodeStudioCore
from safety import canonical, redact, safe_path

class StudioService:
    def __init__(self, root, config, emit, transport=None):
        self.emit = emit
        self.transport = transport
        self.core = CodeStudioCore(root, config, log=lambda text: emit({'event':'log','text':redact(text)}),transport=transport)
        self.proposals = {}

    def handle(self, request):
        command = request.get('command')
        if command == 'models':
            if self.transport:
                return {**self.transport('models',{}),'workspace':str(self.core.workspace)}
            return {'models':self.core.installed_models(),'workspace':str(self.core.workspace)}
        if command == 'configure':
            context = request.get('context_tokens',16384)
            tests = request.get('test_argv',[])
            if type(context) is not int or not 1024 <= context <= 262144:
                raise ValueError('Kontext muss zwischen 1024 und 262144 Tokens liegen.')
            if not isinstance(tests,list) or not all(isinstance(x,str) and x for x in tests):
                raise ValueError('Tests benötigen eine Argumentliste.')
            if self.transport:
                bound=self.transport('models',{})
                context=bound['options']['num_ctx']
            self.core.config.setdefault('options',{})['num_ctx'] = context
            self.core.config['tests'] = [{'argv':tests,'timeout_sec':300}] if tests else []
            self.proposals.clear(); self.core.pending.clear()
            return {'context_tokens':context,'tests_configured':bool(tests)}
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
    def emit(value):
        sys.stdout.buffer.write(canonical(value)+b'\n');sys.stdout.buffer.flush()
    transport = None
    if args.host_binding:
        from hosttransport import HostTransport
        transport=HostTransport(args.host_binding,args.workspace)
    service = StudioService(root,config,emit,transport=transport)
    while True:
        line = sys.stdin.buffer.readline(1024*1024+1)
        if not line: return 0
        if len(line)>1024*1024:
            emit({'ok':False,'error':'CodeStudio-Anfrage zu groß.'});return 1
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request,dict): raise ValueError('Objekt als Anfrage erforderlich.')
            value = service.handle(request)
            emit({'id':request.get('id'),'ok':True,'result':value})
        except Exception as exc:
            emit({'id':request.get('id') if isinstance(request,dict) else None,'ok':False,'error':redact(str(exc))})

if __name__ == '__main__': raise SystemExit(main())
