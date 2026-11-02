"""Tracked launches and disk-backed history without GUI-owned console handles."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from core import launcher
from core.toolbox import suggestions

LABELS={'starting':'正在启动','running':'运行中','finished':'已退出','failed':'启动或运行失败','unknown':'状态未知'}


def start(entry, args, environment, admin, folder):
    command=entry.command
    if not command:
        candidates=suggestions(entry)
        if len(candidates)==1: command=candidates[0][1]
    if not command:
        return None, launcher.launch(entry,admin=admin,args=args,environment=environment)
    full=command+(' '+args.strip() if args.strip() else '')
    argv=(['powershell.exe','-NoLogo','-NoProfile','-Command',full]
          if entry.shell_type=='powershell' else ['cmd.exe','/d','/s','/c',full])
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    identifier=uuid.uuid4().hex
    request_path=folder/(identifier+'.json')
    request={'id':identifier,'name':entry.name,'path':str(entry.path),'started':time.time(),
             'command':full,'argv':argv,'cwd':str(entry.path if entry.is_dir else entry.path.parent)}
    # Only runtime differences are persisted, rather than the entire parent environment.
    request['environment']={k:v for k,v in (environment or {}).items() if os.environ.get(k)!=v}
    if environment is not None:
        request['environment'].update({k:None for k in os.environ if k not in environment})
    request_path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
    state={k:request[k] for k in ('id','name','path','started','command')};state['state']='starting'
    status=request_path.with_suffix('.status.json');status.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    python=Path(sys.executable)
    if python.name.lower()=='pythonw.exe': python=python.with_name('python.exe')
    worker=Path(__file__).with_name('launch_worker.py')
    try:
        launcher._shell_execute_w('runas' if admin else 'open',str(python),
                                 subprocess.list2cmdline([str(worker),str(request_path)]),worker.parent)
    except Exception:
        request_path.unlink(missing_ok=True);status.unlink(missing_ok=True)
        raise
    return identifier, f'正在启动：{entry.name}'


def alive(pid):
    if os.name != 'nt':
        try: os.kill(pid, 0); return True
        except OSError: return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        return ctypes.get_last_error()==5  # An elevated process may deny access.
    try:
        code=wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
    finally: kernel.CloseHandle(handle)


def history(folder):
    records=[]
    for path in Path(folder).glob('*.status.json'):
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            if record.get('state')=='starting' and time.time()-record['started']>45:
                record=dict(record,state='unknown',error='未收到启动反馈，请查看日志')
            if record.get('state')=='running' and not alive(record.get('pid',0)):
                record=dict(record,state='unknown',error='进程已结束，未收到退出码')
            records.append(record)
        except (OSError,ValueError,KeyError): pass
    return sorted(records,key=lambda r:r['started'],reverse=True)
