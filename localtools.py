"""Start only explicitly installed local tool profiles; no downloads or global settings."""
import json,os,pathlib,subprocess,time,urllib.request,urllib.parse
from comfyassets import NoRedirect
from safety import atomic_bytes,canonical,digest

def probe(endpoint,route):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(endpoint+route,timeout=2) as response:return json.loads(response.read(1000000))
    except (OSError,ValueError):return None

def ensure_tool(config_path,name,endpoint,timeout=60):
    if not config_path:return {'status':'externally_managed'}
    path=pathlib.Path(config_path)
    if not path.is_file():return {'status':'externally_managed'}
    raw=path.read_bytes()
    if len(raw)>20000:raise ValueError('Werkzeugkonfiguration zu groß.')
    cfg=json.loads(raw); profile=cfg.get('tools',{}).get(name)
    if not profile or profile.get('endpoint')!=endpoint:return {'status':'externally_managed'}
    if cfg.get('schema')!='codestudio.local-tools.v1' or profile.get('enabled') is not True:raise ValueError('Werkzeugprofil nicht freigegeben.')
    parsed=urllib.parse.urlsplit(endpoint)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:raise ValueError('Nur lokale Werkzeugdienste.')
    route={'ollama':'/api/tags','comfyui':'/system_stats'}.get(name)
    if not route:raise ValueError('Unbekanntes lokales Werkzeug.')
    def healthy(value):return isinstance(value,dict) and ('models' in value if name=='ollama' else 'system' in value and 'devices' in value)
    value=probe(endpoint,route)
    if healthy(value):return {'status':'running','endpoint':endpoint}
    if value is not None:raise RuntimeError('Fremder Dienst am Werkzeug-Port; kein Start.')
    exe=pathlib.Path(profile.get('executable',''));cwd=pathlib.Path(profile.get('cwd',''))
    argv=profile.get('args');environment=profile.get('env',{})
    if not exe.is_absolute() or not exe.is_file() or exe.suffix.lower()!='.exe' or not cwd.is_absolute() or not cwd.is_dir():raise ValueError('Installiertes Werkzeugprogramm und Arbeitsordner erforderlich.')
    if not isinstance(argv,list) or len(argv)>40 or not all(isinstance(x,str) for x in argv):raise ValueError('Ungültige Werkzeugargumente.')
    if not isinstance(environment,dict) or not all(isinstance(k,str) and isinstance(v,str) and k.startswith(('OLLAMA_','HF_','TRANSFORMERS_')) for k,v in environment.items()):raise ValueError('Ungültige Werkzeugumgebung.')
    lock=path.parent/(name+'.starting');path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise RuntimeError('Werkzeugstart bereits aktiv; Startbeleg prüfen.')
    keep_lock=False
    try:
        # No shell, global environment modification, or inherited model stdout.
        logpath=path.parent/(name+'.log')
        with logpath.open('ab') as log:
            child=subprocess.Popen([str(exe),*argv],cwd=cwd,env={**os.environ,**environment},stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
        keep_lock=True
        receipt={'tool':name,'pid':child.pid,'endpoint':endpoint,'config_sha256':digest(raw),'status':'starting','log':str(logpath)}
        target=path.parent/(name+'-start.json');atomic_bytes(target,canonical(receipt))
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if child.poll() is not None:
                keep_lock=False
                raise RuntimeError('Werkzeugstart fehlgeschlagen; '+str(logpath))
            if healthy(probe(endpoint,route)):
                receipt['status']='running';atomic_bytes(target,canonical(receipt));keep_lock=False;return receipt
            time.sleep(.25)
        keep_lock=True
        receipt['status']='start_timeout';atomic_bytes(target,canonical(receipt))
        raise RuntimeError('Werkzeugstart dauert länger; vorhandenen Prozess/Beleg prüfen, kein automatischer Zweitstart.')
    finally:
        os.close(fd)
        if not keep_lock:lock.unlink()
