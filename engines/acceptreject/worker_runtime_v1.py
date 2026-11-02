"""Standard-library memory/ownership checks, copied unchanged from the reviewed Stage2 supervisor.

Kept locally so Stage1 has no runtime dependency on a filtering scientific adapter.
No signal processing is performed here.
"""
import json
import os
from pathlib import Path
import time

def _available_memory():
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in
                ('total_physical','available_physical','total_page','available_page','total_virtual','available_virtual','extended')]
        status=Memory(); status.length=ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError('Available memory could not be checked')
        return int(status.available_physical)
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'): return int(line.split()[1])*1024
    except OSError: pass
    return int(os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE'))


def _process_info(pid):
    """Read process creation identity using only the bundled Python standard library."""
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        handle=kernel.OpenProcess(0x1000|0x100000,False,int(pid))
        if not handle:
            error=ctypes.get_last_error()
            if error in (87,1168): return None
            raise OSError(error,'Cannot verify previous waveform-worker process identity')
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)): raise ctypes.WinError(ctypes.get_last_error())
            if code.value!=259: return None
            creation,exit_time,system,user=(wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(system),ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            buffer=ctypes.create_unicode_buffer(32768); size=wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
            return dict(pid=int(pid),created=(int(creation.dwHighDateTime)<<32)|int(creation.dwLowDateTime),executable=buffer.value)
        finally: kernel.CloseHandle(handle)
    try:
        data=Path(f'/proc/{int(pid)}/stat').read_text().split(') ',1)[1].split()
        if data[0]=='Z': return None
        return dict(pid=int(pid),created=int(data[19]),executable=os.readlink(f'/proc/{int(pid)}/exe'))
    except FileNotFoundError: return None


def worker_limit(requested,count):
    available=_available_memory()
    if available<4*1024**3:
        raise RuntimeError('Less than 4 GiB RAM is available. Close other applications before starting waveform workers.')
    memory_limit=max(1,int((available-4*1024**3)//(2.5*1024**3)))
    return max(1,min(int(requested or 1),os.cpu_count() or 1,memory_limit,count or 1))


def _json_object(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise ValueError('Checkpoint must contain an object')
    return value


def _worker_is_closed(folder):
    process_file=folder/'process.json'
    if process_file.exists():
        # An unreadable ownership record deliberately raises: never guess that
        # a writer is gone merely because its state cannot be read.
        return _owned_process(process_file) is None
    if (folder/'launch_pending.json').exists(): return False
    exit_file=folder/'worker_exit.json'
    if not exit_file.exists(): return False
    try:
        result=_json_object(exit_file)
        if not isinstance(result.get('exit_code'),int): return False
        inputs=[p for p in (folder/'job.json',folder/'worker_config.json') if p.exists()]
        return bool(inputs) and exit_file.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in inputs)
    except (OSError,ValueError,TypeError): return False


def _owned_process(process_file):
    """Recognize a surviving owned R process; PID reuse never establishes ownership."""
    if not process_file.exists(): return None
    try:
        item=json.loads(process_file.read_text())
        process=_process_info(int(item['pid']))
        if process is None or process['created']!=item['created']: return None
        if Path(process['executable']).resolve()!=Path(item['executable']).resolve(): return None
        return process
    except (OSError,ValueError,KeyError) as exc:
        raise RuntimeError('Previous waveform-worker ownership record is unreadable; automatic relaunch was blocked.') from exc


def _drain_owned_workers(root,emit):
    survivors=[(p,_owned_process(p)) for p in root.glob('*/process.json')]
    for path,process in survivors:
        if process is None: continue
        emit({'type':'recover_worker','message':'Waiting for a previously started waveform worker to reach its checkpoint.',
              'worker_pid':process['pid']})
        while _owned_process(path) is not None:
            time.sleep(.5)
