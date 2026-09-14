"""Project-bound TobyKi session; never falls back to a direct model endpoint."""
import hashlib
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Hosttransport darf nicht umgeleitet werden.')

class HostTransport:
    def __init__(self, binding, workspace):
        self.file = pathlib.Path(binding)
        if not self.file.is_absolute() or self.file.is_symlink():
            raise ValueError('Absoluter Hostbindungspfad erforderlich.')
        raw = self.file.read_bytes()
        if len(raw)>16384: raise ValueError('Hostbindung zu groß.')
        self.digest = hashlib.sha256(raw).digest()
        self.data = json.loads(raw)
        d = self.data
        endpoint = urllib.parse.urlsplit(d.get('endpoint',''))
        if d.get('schema')!='codestudio.host-session.v1' or endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port or endpoint.path!='/model' or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password:
            raise ValueError('Ungültige lokale TobyKi-Hostsitzung.')
        self.workspace = pathlib.Path(workspace).resolve(strict=True)
        if pathlib.Path(d.get('workspace','')).resolve(strict=True)!=self.workspace:
            raise ValueError('TobyKi-Hostsitzung gehört zu anderem Projekt.')
        if not re.fullmatch('[a-f0-9]{64}',d.get('token','')) or not isinstance(d.get('expires_at'),(int,float)):
            raise ValueError('Ungültige TobyKi-Sitzungsbindung.')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.check()

    def check(self):
        if time.time()*1000>=self.data['expires_at'] or hashlib.sha256(self.file.read_bytes()).digest()!=self.digest:
            raise ValueError('TobyKi-Hostsitzung abgelaufen oder verändert. Erneut aus TobyKi öffnen.')

    def __call__(self, command, payload):
        self.check()
        if command not in ('models','chat'): raise ValueError('Unzulässiger Hostaufruf.')
        request={'session_id':self.data['session_id'],'workspace':self.data['workspace'],
                 'call_id':str(uuid.uuid4()),'command':command,'payload':payload}
        body=json.dumps(request).encode('utf8')
        if len(body)>3*1024*1024: raise ValueError('Hostauftrag zu groß.')
        req=urllib.request.Request(self.data['endpoint'],data=body,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.data['token']},method='POST')
        try:
            with self.opener.open(req,timeout=300) as response:
                raw=response.read(3*1024*1024+1)
        except urllib.error.HTTPError as exc:
            try: message=json.loads(exc.read(4096)).get('error','Hostauftrag abgelehnt.')
            except (ValueError,TypeError): message='Hostauftrag abgelehnt.'
            raise ValueError(message) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise ValueError('TobyKi-Hostverbindung unterbrochen. Erneut aus TobyKi öffnen.') from None
        self.check()
        if len(raw)>3*1024*1024: raise ValueError('Hostantwort zu groß.')
        answer=json.loads(raw)
        if answer.get('ok') is not True: raise ValueError(answer.get('error','Hostauftrag fehlgeschlagen.'))
        return answer['result']
