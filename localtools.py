"""Start only explicitly installed local tool profiles; no downloads or global settings."""
import json,os,pathlib,subprocess,time,urllib.request,urllib.parse,uuid
from comfyassets import NoRedirect
from safety import atomic_bytes,canonical,digest

def process_stamp(pid):
    """Windows creation time identifies the process even after PID reuse."""
    if os.name!='nt': return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return None
    try:
        times=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in times]):return None
        return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
    finally:kernel.CloseHandle(handle)

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
    if healthy(value):
        lock=path.parent/(name+'.starting');target=path.parent/(name+'-start.json')
        # Reconcile only our own timed-out start, never a live starter or unknown PID.
        if lock.is_file() and target.is_file():
            try:
                previous=json.loads(target.read_bytes())
                token=previous.get('lock_token');stamp=previous.get('process_stamp')
                if (previous.get('status')=='start_timeout' and previous.get('config_sha256')==digest(raw)
                    and previous.get('endpoint')==endpoint and previous.get('tool')==name
                    and isinstance(token,str) and lock.read_text()==token and stamp
                    and process_stamp(previous['pid'])==stamp):
                    previous['status']='running';previous['late_start_reconciled']=True
                    atomic_bytes(target,canonical(previous))
                    if lock.read_text()==token:lock.unlink(missing_ok=True)
            except (OSError,ValueError,KeyError,TypeError):pass
        return {'status':'running','endpoint':endpoint}
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
        token=uuid.uuid4().hex;os.write(fd,token.encode());os.fsync(fd)
        # No shell, global environment modification, or inherited model stdout.
        logpath=path.parent/(name+'.log')
        with logpath.open('ab') as log:
            child=subprocess.Popen([str(exe),*argv],cwd=cwd,env={**os.environ,**environment},stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
        keep_lock=True
        receipt={'tool':name,'pid':child.pid,'endpoint':endpoint,'config_sha256':digest(raw),'status':'starting','log':str(logpath),'lock_token':token,'process_stamp':process_stamp(child.pid)}
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
