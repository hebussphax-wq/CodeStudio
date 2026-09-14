"""Single request JSON bridge. Deliberately exposes proposals only to host apps."""
import json
import pathlib
import sys
import tempfile
from core import CodeStudioCore
from safety import digest, canonical, redact

def main():
    try:
        raw = sys.stdin.buffer.readline(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('Request zu groß')
        request = json.loads(raw)
        if request.get('schema') != 'codestudio.propose.v1': raise ValueError('Unbekannter Request')
        root = pathlib.Path(__file__).resolve().parent
        cfg = json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
        cfg.update(workspace=request['workspace'], timeout_sec=120, tests=[])
        transport = None
        if request.get('transport') == 'main':
            sequence = 0
            def transport(command, payload):
                nonlocal sequence
                sequence += 1
                message = {'event':'model_request','request_id':request['request_id'],
                    'call_id':sequence,'command':command,'payload':payload}
                print(json.dumps(message,ensure_ascii=False),flush=True)
                line = sys.stdin.buffer.readline(3*1024*1024+1)
                if not line or len(line)>3*1024*1024: raise ValueError('Hosttransport unterbrochen.')
                answer = json.loads(line)
                if answer.get('request_id')!=request['request_id'] or answer.get('call_id')!=sequence:
                    raise ValueError('Hostantwort passt nicht zum Modellauftrag.')
                if answer.get('ok') is not True: raise ValueError(answer.get('error','Hostauftrag fehlgeschlagen.'))
                return answer['result']
        # Host owns durable proposal receipts; temporary model context is removed.
        with tempfile.TemporaryDirectory(prefix='codestudio-proposal-') as state:
            cfg['state_dir']=state
            core=CodeStudioCore(root,cfg,transport=transport)
            result=core.analyze(request['task'],request['model'])
            response={'schema':'codestudio.proposal.v1','status':'proposed','request_id':request['request_id'],
                      'project_id':request['project_id'],'task_id':request['task_id'],'plan':result.plan,
                      'diff':result.diff,'binding':result.binding,
                      'engine_sha256':digest(canonical({p:digest((root/p).read_bytes()) for p in ['core.py','safety.py','bridge.py']}))}
        print(json.dumps(response,ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({'schema':'codestudio.proposal.v1','status':'failed','error':redact(str(exc))},ensure_ascii=False)); return 1

if __name__=='__main__': raise SystemExit(main())
