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
        # Keep valid own receipt; else adopt matching profile exe on port; else no ownership claim.
        _receipt,own=_own_receipt(path,name,endpoint,raw)
        if not own:
            try:_adopt_running_if_ours(path,name,endpoint,raw,profile)
            except (OSError,ValueError,TypeError,KeyError):pass
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

def post_json(endpoint,route,body,timeout=10):
    """POST JSON to a local tool endpoint; returns parsed object or None."""
    data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    req=urllib.request.Request(endpoint+route,data=data,method='POST',headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(req,timeout=timeout) as response:
            raw=response.read(1000000)
            if not raw:return {}
            return json.loads(raw)
    except (OSError,ValueError):return None

def _validate_managed(config_path,name,endpoint):
    """Same profile gate as ensure_tool. Returns (path, raw, cfg, profile, route) or status dict."""
    if not config_path:return {'status':'externally_managed'}
    path=pathlib.Path(config_path)
    if not path.is_file():return {'status':'externally_managed'}
    raw=path.read_bytes()
    if len(raw)>20000:raise ValueError('Werkzeugkonfiguration zu gro\u00df.')
    cfg=json.loads(raw); profile=cfg.get('tools',{}).get(name)
    if not profile or profile.get('endpoint')!=endpoint:return {'status':'externally_managed'}
    if cfg.get('schema')!='codestudio.local-tools.v1' or profile.get('enabled') is not True:raise ValueError('Werkzeugprofil nicht freigegeben.')
    parsed=urllib.parse.urlsplit(endpoint)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:raise ValueError('Nur lokale Werkzeugdienste.')
    route={'ollama':'/api/tags','comfyui':'/system_stats'}.get(name)
    if not route:raise ValueError('Unbekanntes lokales Werkzeug.')
    return path,raw,cfg,profile,route

def _healthy(name,value):
    return isinstance(value,dict) and ('models' in value if name=='ollama' else 'system' in value and 'devices' in value)

def _own_receipt(path,name,endpoint,raw):
    """Load start receipt only when tool/endpoint/config_sha256 and live process_stamp match."""
    target=path.parent/(name+'-start.json')
    if not target.is_file():return None,False
    try:
        receipt=json.loads(target.read_bytes())
        stamp=receipt.get('process_stamp');pid=receipt.get('pid')
        own=(receipt.get('tool')==name and receipt.get('endpoint')==endpoint
             and receipt.get('config_sha256')==digest(raw)
             and stamp and pid is not None and process_stamp(pid)==stamp)
        return receipt,bool(own)
    except (OSError,ValueError,KeyError,TypeError):
        return None,False

def _clear_own_lock(path,name,receipt):
    lock=path.parent/(name+'.starting')
    if not lock.is_file() or not isinstance(receipt,dict):return
    try:
        token=receipt.get('lock_token')
        if isinstance(token,str) and lock.read_text(encoding='utf-8')==token:
            lock.unlink(missing_ok=True)
    except OSError:
        pass

def _endpoint_port(endpoint):
    """Local TCP port from an http(s) endpoint URL."""
    parsed=urllib.parse.urlsplit(endpoint)
    if parsed.port is not None:return int(parsed.port)
    if parsed.scheme=='https':return 443
    return 80

def _pids_listening_on_port(port):
    """PIDs with LISTENING TCP socket on local port. Prefer ctypes MIB table; no shell."""
    if os.name!='nt':return []
    port=int(port)
    try:
        import ctypes
        from ctypes import wintypes
        class MIB_TCPROW_OWNER_PID(ctypes.Structure):
            _fields_=[('dwState',wintypes.DWORD),('dwLocalAddr',wintypes.DWORD),('dwLocalPort',wintypes.DWORD),
                      ('dwRemoteAddr',wintypes.DWORD),('dwRemotePort',wintypes.DWORD),('dwOwningPid',wintypes.DWORD)]
        class MIB_TCPTABLE_OWNER_PID(ctypes.Structure):
            _fields_=[('dwNumEntries',wintypes.DWORD),('table',MIB_TCPROW_OWNER_PID*1)]
        iphlpapi=ctypes.WinDLL('iphlpapi',use_last_error=True)
        GetExtendedTcpTable=iphlpapi.GetExtendedTcpTable
        GetExtendedTcpTable.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),wintypes.BOOL,wintypes.ULONG,ctypes.c_int,wintypes.ULONG]
        GetExtendedTcpTable.restype=wintypes.ULONG
        AF_INET=2;TCP_TABLE_OWNER_PID_ALL=5;MIB_TCP_STATE_LISTEN=2
        size=wintypes.DWORD(0)
        # First call queries required buffer size (ERROR_INSUFFICIENT_BUFFER=122).
        GetExtendedTcpTable(None,ctypes.byref(size),True,AF_INET,TCP_TABLE_OWNER_PID_ALL,0)
        if size.value==0:return []
        buf=(ctypes.c_ubyte*size.value)()
        rc=GetExtendedTcpTable(ctypes.byref(buf),ctypes.byref(size),True,AF_INET,TCP_TABLE_OWNER_PID_ALL,0)
        if rc!=0:raise OSError(rc)
        num=ctypes.cast(buf,ctypes.POINTER(wintypes.DWORD))[0]
        row_size=ctypes.sizeof(MIB_TCPROW_OWNER_PID)
        # Header is one DWORD; rows follow.
        base=ctypes.addressof(buf)+ctypes.sizeof(wintypes.DWORD)
        pids=[];seen=set()
        for i in range(int(num)):
            row=ctypes.cast(base+i*row_size,ctypes.POINTER(MIB_TCPROW_OWNER_PID)).contents
            if int(row.dwState)!=MIB_TCP_STATE_LISTEN:continue
            # dwLocalPort is network byte order (big-endian) in the low 16 bits.
            local_port=socket_ntohs(int(row.dwLocalPort)&0xFFFF)
            if local_port!=port:continue
            pid=int(row.dwOwningPid)
            if pid>0 and pid not in seen:
                seen.add(pid);pids.append(pid)
        return pids
    except (OSError,ValueError,TypeError,AttributeError,OverflowError):
        pass
    # Fallback: absolute netstat.exe as argv list (no shell).
    netstat=r'C:\Windows\System32\netstat.exe'
    try:
        completed=subprocess.run([netstat,'-ano','-p','tcp'],check=False,capture_output=True,text=True,encoding='utf-8',errors='replace',stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError:
        return []
    want=str(port);pids=[];seen=set()
    for line in (completed.stdout or '').splitlines():
        parts=line.split()
        if len(parts)<5 or parts[0].upper()!='TCP':continue
        if 'LISTENING' not in parts:continue
        if parts[1].rsplit(':',1)[-1]!=want:continue
        try:pid=int(parts[-1])
        except ValueError:continue
        if pid>0 and pid not in seen:
            seen.add(pid);pids.append(pid)
    return pids

def socket_ntohs(value):
    """Host-order port from network-order 16-bit value without importing socket."""
    return ((value & 0xFF)<<8) | ((value>>8)&0xFF)

def _process_image_path(pid):
    """Absolute executable path for PID (QueryFullProcessImageNameW), or None."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return None
    try:
        size=wintypes.DWORD(32768)
        buf=ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)):return None
        return buf.value or None
    finally:
        kernel.CloseHandle(handle)

def _parent_pid(pid):
    """Parent PID via Toolhelp32, or None."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
                  ('th32DefaultHeapID',ctypes.POINTER(ctypes.c_ulong)),('th32ModuleID',wintypes.DWORD),
                  ('cntThreads',wintypes.DWORD),('th32ParentProcessID',wintypes.DWORD),
                  ('pcPriClassBase',ctypes.c_long),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype=wintypes.BOOL
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snap=kernel.CreateToolhelp32Snapshot(0x2,0)
    if not snap or snap==wintypes.HANDLE(-1).value:return None
    try:
        pe=PROCESSENTRY32W();pe.dwSize=ctypes.sizeof(PROCESSENTRY32W)
        if not kernel.Process32FirstW(snap,ctypes.byref(pe)):return None
        target=int(pid)
        while True:
            if int(pe.th32ProcessID)==target:
                ppid=int(pe.th32ParentProcessID)
                return ppid if ppid>0 else None
            if not kernel.Process32NextW(snap,ctypes.byref(pe)):break
        return None
    finally:
        kernel.CloseHandle(snap)

def _same_executable(profile_exe,candidate):
    """True when candidate path is the same file as profile executable."""
    if not candidate or not profile_exe:return False
    try:
        return pathlib.Path(profile_exe).resolve()==pathlib.Path(candidate).resolve()
    except (OSError,RuntimeError,ValueError):
        try:
            return os.path.normcase(os.path.abspath(str(profile_exe)))==os.path.normcase(os.path.abspath(str(candidate)))
        except OSError:
            return False

def _adopt_running_if_ours(path,name,endpoint,raw,profile):
    """If port listener (or its parent) matches profile exe, write/update start receipt. No kills."""
    exe=profile.get('executable','')
    if not isinstance(exe,str) or not exe:return None
    try:port=_endpoint_port(endpoint)
    except (TypeError,ValueError):return None
    try:pids=_pids_listening_on_port(port)
    except (OSError,ValueError,TypeError):return None
    candidates=[]
    for pid in pids:
        candidates.append(int(pid))
        try:
            parent=_parent_pid(pid)
            if parent:candidates.append(int(parent))
        except (OSError,ValueError,TypeError):
            pass
    seen=set()
    for pid in candidates:
        if not pid or pid in seen:continue
        seen.add(pid)
        try:image=_process_image_path(pid)
        except (OSError,ValueError,TypeError):continue
        if not _same_executable(exe,image):continue
        stamp=process_stamp(pid)
        if not stamp:continue
        receipt={'tool':name,'pid':int(pid),'endpoint':endpoint,'config_sha256':digest(raw),
                 'status':'running','process_stamp':stamp,'adopted_running':True}
        target=path.parent/(name+'-start.json')
        try:
            if target.is_file():
                prev=json.loads(target.read_bytes())
                token=prev.get('lock_token')
                if isinstance(token,str) and token:receipt['lock_token']=token
                if isinstance(prev.get('log'),str):receipt['log']=prev['log']
        except (OSError,ValueError,TypeError,KeyError):
            pass
        atomic_bytes(target,canonical(receipt))
        return receipt
    return None

def stop_tool(config_path,name,endpoint,timeout=30):
    """Stop only our managed tool process tree; never a foreign service on the port."""
    validated=_validate_managed(config_path,name,endpoint)
    if isinstance(validated,dict):return validated
    path,raw,cfg,profile,route=validated
    value=probe(endpoint,route)
    receipt,own=_own_receipt(path,name,endpoint,raw)
    target=path.parent/(name+'-start.json')
    if not _healthy(name,value):
        if own and receipt is not None:
            receipt=dict(receipt);receipt['status']='stopped'
            atomic_bytes(target,canonical(receipt))
            _clear_own_lock(path,name,receipt)
            return {'status':'stopped','tool':name,'endpoint':endpoint,'pid':receipt.get('pid')}
        return {'status':'already_stopped','tool':name,'endpoint':endpoint}
    if not own:
        raise RuntimeError('Fremder Dienst am Werkzeug-Port; kein Stop.')
    pid=int(receipt['pid'])
    taskkill=r'C:\Windows\System32\taskkill.exe'
    # No shell: terminate process tree via absolute taskkill.
    subprocess.run([taskkill,'/PID',str(pid),'/T','/F'],check=False,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if not _healthy(name,probe(endpoint,route)):
            break
        time.sleep(.25)
    else:
        if _healthy(name,probe(endpoint,route)):
            raise RuntimeError('Werkzeugstop dauert l\u00e4nger; vorhandenen Prozess/Beleg pr\u00fcfen.')
    receipt=dict(receipt);receipt['status']='stopped'
    atomic_bytes(target,canonical(receipt))
    _clear_own_lock(path,name,receipt)
    return {'status':'stopped','tool':name,'endpoint':endpoint,'pid':pid}

def unload_tool(config_path,name,endpoint,model=None):
    """Unload models/memory from a managed tool without stopping the server."""
    validated=_validate_managed(config_path,name,endpoint)
    if isinstance(validated,dict):return validated
    path,raw,cfg,profile,route=validated
    if name=='ollama':
        models=[]
        if isinstance(model,str) and model.strip():
            models=[model.strip()]
        else:
            ps=probe(endpoint,'/api/ps')
            if isinstance(ps,dict) and isinstance(ps.get('models'),list):
                for entry in ps['models']:
                    if not isinstance(entry,dict):continue
                    label=entry.get('name') or entry.get('model')
                    if isinstance(label,str) and label:models.append(label)
            else:
                tags=probe(endpoint,'/api/tags')
                if isinstance(tags,dict) and isinstance(tags.get('models'),list):
                    for entry in tags['models']:
                        if isinstance(entry,dict) and isinstance(entry.get('name'),str) and entry['name']:
                            models.append(entry['name'])
        actions=[]
        for label in models:
            # keep_alive:0 asks Ollama to unload the model from memory.
            result=post_json(endpoint,'/api/generate',{'model':label,'keep_alive':0,'prompt':''})
            actions.append({'model':label,'ok':result is not None})
        return {'status':'unloaded','tool':name,'endpoint':endpoint,'models':models,'actions':actions}
    if name=='comfyui':
        interrupt_ok=post_json(endpoint,'/interrupt',{}) is not None
        # Tolerate missing /free on older ComfyUI builds.
        free_ok=post_json(endpoint,'/free',{'unload_models':True,'free_memory':True}) is not None
        return {'status':'unloaded','tool':name,'endpoint':endpoint,'interrupt':interrupt_ok,'free':free_ok}
    raise ValueError('Unbekanntes lokales Werkzeug.')
