"""Tracked launches and disk-backed history without GUI-owned console handles."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from core import launcher
from core.process_env import powershell_arguments
from core.toolbox import suggestions

LABELS={'starting':'正在启动','running':'运行中','finished':'已退出','failed':'启动或运行失败','unknown':'状态未知'}


def _shortcut_path(command):
    """命令本身就是一个 .lnk 路径时，直接交给 ShellExecute。"""
    text = command.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    path = Path(text)
    if path.suffix.lower() == '.lnk' and path.is_file():
        return path
    return None


def _terminal_argv(entry, full, environment):
    """终端里要运行的 argv；绑定环境时用 PowerShell 包装，保留环境差异。"""
    cwd = entry.path if entry.is_dir else entry.path.parent
    if environment is not None:
        shell = 'powershell.exe' if entry.shell_type == 'powershell' else 'cmd.exe'
        parameters = ('-NoLogo -NoProfile -NoExit -Command ' + full
                      if entry.shell_type == 'powershell'
                      else '/d /s /k "' + full + '"')
        encoded = powershell_arguments(shell, parameters, cwd, environment)
        return ['powershell.exe', '-NoLogo', '-NoProfile', '-EncodedCommand',
                encoded.rsplit(' ', 1)[-1]], cwd
    if entry.shell_type == 'powershell':
        return ['powershell.exe', '-NoLogo', '-NoProfile', '-NoExit', '-Command', full], cwd
    return ['cmd.exe', '/d', '/s', '/k', full], cwd


def start(entry, args, environment, admin, folder, terminal='', terminal_args=''):
    command=entry.command
    if not command:
        candidates=suggestions(entry)
        if len(candidates)==1:
            command=candidates[0][1]
    if not command:
        return None, launcher.launch(entry,admin=admin,args=args,environment=environment)
    full=command+(' '+args.strip() if args.strip() else '')
    shortcut=_shortcut_path(command)
    if shortcut is not None:
        launcher._shell_execute(shortcut, admin, shortcut.parent, args.strip() or None)
        return None, f'已打开快捷方式：{entry.name}'
    from core.terminal import gui_command
    cwd = entry.path if entry.is_dir else entry.path.parent
    gui = gui_command(full, cwd, environment, entry.shell_type)
    if gui is not None:
        executable, parameters = gui
        if environment is not None and not admin:
            subprocess.Popen(subprocess.list2cmdline([str(executable)]) + (' ' + parameters if parameters else ''),
                             cwd=str(cwd), env=environment,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        elif environment is not None:
            encoded = powershell_arguments(str(executable), parameters, cwd, environment)
            launcher._shell_execute_w('runas', 'powershell.exe', encoded, cwd, show=0)
        else:
            launcher._shell_execute(executable, admin, cwd, parameters or None)
        return None, f'已启动：{executable.name}'
    if terminal and not admin:
        from core.terminal import launch_terminal
        shell_argv, cwd = _terminal_argv(entry, full, environment)
        launch_terminal(terminal, terminal_args, cwd, shell_argv)
        return None, f'已在终端启动：{entry.name}'
    argv=(['powershell.exe','-NoLogo','-NoProfile','-Command',full]
          if entry.shell_type=='powershell' else ['cmd.exe','/d','/s','/c',full])
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    identifier=uuid.uuid4().hex
    request_path=folder/(identifier+'.json')
    cwd=entry.path if entry.is_dir else entry.path.parent
    request={'id':identifier,'name':entry.name,'path':str(entry.path),'started':time.time(),
             'command':full,'argv':argv,'cwd':str(cwd)}
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
    frozen = getattr(sys, 'frozen', False)
    parameters = ['--launch-worker', str(request_path.resolve())] if frozen else [str(worker), str(request_path.resolve())]
    previous_reset = os.environ.get('PYINSTALLER_RESET_ENVIRONMENT')
    try:
        if frozen: os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        try:
            launcher._shell_execute_w('runas' if admin else 'open', str(python),
                                     subprocess.list2cmdline(parameters), folder.resolve())
        finally:
            if previous_reset is None: os.environ.pop('PYINSTALLER_RESET_ENVIRONMENT', None)
            else: os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = previous_reset
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


def clear_history(folder, identifiers=None):
    """Delete completed launch artifacts only; never touch tool metadata."""
    import re
    root = Path(folder).resolve()
    removed = skipped = 0
    for record in history(root):
        identifier = record.get('id', '')
        if identifiers is not None and identifier not in identifiers: continue
        if not re.fullmatch(r'[0-9a-f]{32}', identifier): continue
        if record.get('state') in {'starting', 'running', 'unknown'}:
            skipped += 1
            continue
        files = [root / (identifier + suffix) for suffix in ('.log', '.json', '.status.json')]
        for path in files:
            if path.is_symlink() or path.resolve().parent != root:
                raise ValueError('日志路径异常，已停止清理')
        for path in files: path.unlink(missing_ok=True)
        removed += 1
    return removed, skipped
