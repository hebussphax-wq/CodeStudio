"""Bounded test process trees. A worker waits for its Windows Job before launch."""
from __future__ import annotations
import ctypes
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

class ProcessTreeUncertain(RuntimeError):
    pass

class WindowsJob:
    def __init__(self):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p,w.LPCWSTR]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE,w.HANDLE]
        self.api.TerminateJobObject.argtypes = [w.HANDLE,w.UINT]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.QueryInformationJobObject.argtypes = [w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p]
        self.api.SetInformationJobObject.argtypes = [w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.handle = self.api.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        limits=ctypes.create_string_buffer(144 if ctypes.sizeof(ctypes.c_void_p)==8 else 112)
        limits[16:20]=(0x2000).to_bytes(4,'little')  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,limits,len(limits)):
            self.close();raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, child):
        if not self.api.AssignProcessToJobObject(self.handle,int(child._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def stop(self):
        if not self.api.TerminateJobObject(self.handle,124):
            raise ctypes.WinError(ctypes.get_last_error())
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: 4 LARGE_INTEGER + 4 DWORD.
        accounting = ctypes.create_string_buffer(48)
        deadline = time.monotonic()+5
        while True:
            if not self.api.QueryInformationJobObject(self.handle,1,accounting,48,None):
                raise ctypes.WinError(ctypes.get_last_error())
            active = int.from_bytes(accounting.raw[40:44],'little')
            if active == 0: return
            if time.monotonic()>deadline: raise RuntimeError('Test-Prozessbaum endet nicht; kein automatischer Rollback')
            time.sleep(.01)

    def close(self): self.api.CloseHandle(self.handle)

def worker_main():
    # No test command is started before parent has assigned this worker to a Job.
    request=json.loads(sys.stdin.buffer.readline(1024*1024))
    return subprocess.call(request['argv'],cwd=request['cwd'],shell=False,
                           stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr,
                           creationflags=0x08000000 if os.name=='nt' else 0)

def run_command(argv, cwd, timeout, max_bytes=256000, cancel_event=None):
    command=([sys.executable,'--test-worker'] if getattr(sys,'frozen',False)
             else [sys.executable,'-u',str(pathlib.Path(__file__).resolve()),'--test-worker'])
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           creationflags=0x08000000 if os.name=='nt' else 0,start_new_session=os.name!='nt')
    job=None
    output=bytearray(); output_lock=threading.Lock(); overflow=threading.Event()
    def read(stream):
        while True:
            chunk=stream.read(4096)
            if not chunk: return
            with output_lock:
                room=max_bytes-len(output)
                output.extend(chunk[:max(0,room)])
                if len(chunk)>room: overflow.set()
    threads=[threading.Thread(target=read,args=(s,),daemon=True) for s in (child.stdout,child.stderr)]
    try:
        if os.name=='nt':
            job=WindowsJob();job.assign(child)
        for thread in threads: thread.start()
        child.stdin.write(json.dumps({'argv':argv,'cwd':str(cwd)}).encode('utf-8')+b'\n');child.stdin.close()
        deadline=time.monotonic()+timeout
        status='passed'
        while child.poll() is None:
            if cancel_event is not None and cancel_event.is_set(): status='cancelled';break
            if overflow.is_set(): status='output_limit';break
            if time.monotonic()>=deadline: status='timed_out';break
            time.sleep(.01)
        code=child.poll()
        # Even on success, terminate any surviving watcher/service before readback.
        if job:
            try: job.stop()
            except Exception as exc: raise ProcessTreeUncertain('Test-Prozessbaum nicht bestätigt beendet') from exc
        else:
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        child.wait(timeout=5)
        for thread in threads: thread.join(timeout=5)
        if any(t.is_alive() for t in threads): raise RuntimeError('Test-Ausgabekanal nicht geschlossen')
        if overflow.is_set(): status='output_limit'
        if status=='cancelled': code=130
        elif status=='timed_out': code=124
        elif status=='output_limit': code=125
        elif code!=0: status='failed'
        return {'status':status,'returncode':code,'output':bytes(output).decode('utf-8',errors='replace'),
                'process_tree_stopped':True,'output_truncated':overflow.is_set()}
    finally:
        if child.poll() is None: child.kill();child.wait(timeout=5)
        if job: job.close()
        for stream in (child.stdout,child.stderr): stream.close()

if __name__=='__main__': raise SystemExit(worker_main())
