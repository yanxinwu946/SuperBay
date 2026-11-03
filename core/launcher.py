"""启动器：按优先级执行条目。

优先级：db 自定义命令 > 目录内主程序启发式 > 打开目录。
支持 cmd / powershell / 直接运行 / 管理员提权 / 追加参数。
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path

from .models import DEFAULT_SHELL, Entry
from .process_env import powershell_arguments

log = logging.getLogger(__name__)

EXCLUDE_EXE_HINTS = ("uninstall", "unins", "卸载", "setup", "update")


def find_main_exe(folder: Path) -> Path | None:
    """在目录里找主程序：同名 exe/lnk > 唯一候选 > None（交给打开目录）。"""
    try:
        exes = [p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in {".exe", ".lnk"}
                and not any(h in p.name.lower() for h in EXCLUDE_EXE_HINTS)]
    except OSError:
        return None
    if not exes:
        return None
    same_name = [e for e in exes if e.stem.lower() == folder.name.lower()]
    if same_name:
        return same_name[0]
    if len(exes) == 1:
        return exes[0]
    return None


def _run_custom(shell_type: str, command: str, cwd: Path) -> None:
    # ShellExecute creates independent console standard handles for GUI launches.
    if shell_type == "powershell":
        _shell_execute_w("open", "powershell.exe", f"-NoExit -Command {command}", cwd)
    else:
        _shell_execute_w("open", "cmd.exe", '/d /s /k "' + command + '"', cwd)


def _shell_execute_w(verb: str, file: str, args: str | None, cwd: Path, show: int = 1) -> None:
    """ShellExecuteW 启动；返回值 ≤32 就是错误码，不抛异常。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        set_directory = ctypes.windll.kernel32.SetDllDirectoryW
        set_directory.argtypes = [ctypes.c_wchar_p]
        set_directory(None)
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, verb, file, args, str(cwd), show)
    finally:
        if bundle:
            set_directory(bundle)
    if ret <= 32:
        raise RuntimeError(f"启动失败（错误码 {ret}）")


def _run_as_admin(file: str, args: str | None, cwd: Path) -> None:
    """runas 提权启动。"""
    _shell_execute_w("runas", file, args, cwd)


def _shell_execute(path: Path, admin: bool, cwd: Path, args: str | None = None) -> None:
    if admin:
        _run_as_admin(str(path), args, cwd)
    else:
        # 统一走 ShellExecuteW：无参/带参启动共用同一个工作目录与错误码
        _shell_execute_w("open", str(path), args, cwd)


def launch(entry: Entry, command: str | None = None,
           shell_type: str = DEFAULT_SHELL, admin: bool = False, args: str = "",
           environment: dict[str, str] | None = None) -> str:
    """启动条目，返回用于状态栏展示的动作描述。抛 RuntimeError 表示失败。

    args：追加参数，对自定义命令和主程序/文件都生效；纯目录直开忽略。
    """
    path = entry.path
    if not path.exists():
        raise RuntimeError(f"路径不存在: {path}")

    cwd = path if entry.is_dir else path.parent
    arg_text = args.strip()
    suffix = f" {arg_text}" if arg_text else ""

    if environment is not None:
        if command:
            target = "powershell.exe" if shell_type == "powershell" else "cmd.exe"
            full = command + suffix
            parameters = ("-NoLogo -NoProfile -NoExit -Command " + full if shell_type == "powershell"
                          else '/d /s /k "' + full + '"')
        else:
            executable = find_main_exe(path) if entry.is_dir else path
            if executable is None:
                _shell_execute(path, False, cwd)
                return f"已打开目录: {entry.name}"
            runners = {".py": "python.exe", ".jar": "java.exe", ".js": "node.exe"}
            ext = executable.suffix.lower()
            if ext in runners:
                target = runners[ext]
                parameters = ("-jar " if ext == ".jar" else "") + subprocess.list2cmdline([str(executable)]) + suffix
            elif ext in (".cmd", ".bat"):
                target = "cmd.exe"
                parameters = '/d /s /k "' + subprocess.list2cmdline([str(executable)]) + suffix + '"'
            elif ext == ".ps1":
                target = "powershell.exe"
                parameters = "-NoLogo -NoProfile -NoExit -File " + subprocess.list2cmdline([str(executable)]) + suffix
            elif ext == ".exe":
                target, parameters = str(executable), arg_text
            else:
                raise RuntimeError("此文件类型不支持绑定运行环境，请配置自定义启动命令。")
        try:
            encoded = powershell_arguments(target, parameters, cwd, environment)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        _shell_execute_w("runas" if admin else "open", "powershell.exe", encoded, cwd)
        return f"已使用绑定环境启动: {entry.name}"

    if command:
        full = command + suffix
        if admin:
            # 提权也要按配置的运行方式走，别把 PowerShell 命令塞给 cmd
            if shell_type == "powershell":
                _run_as_admin("powershell.exe", f"-NoExit -Command {full}", cwd)
            else:
                _run_as_admin("cmd.exe", f"/k {full}", cwd)
        else:
            _run_custom(shell_type, full, cwd)
        log.info("自定义命令启动 %s: %s", entry.name, full)
        return f"已执行命令: {entry.name}{suffix}"

    if entry.is_dir:
        exe = find_main_exe(path)
        if exe:
            _shell_execute(exe, admin, cwd, arg_text or None)
            log.info("启动主程序 %s -> %s%s", entry.name, exe.name, suffix)
            return f"已启动: {exe.name}{suffix}"
        _shell_execute(path, False, cwd)
        return f"已打开目录: {entry.name}"

    _shell_execute(path, admin, cwd, arg_text or None)
    return f"已打开: {entry.name}{suffix}"


def open_in_explorer(path: Path) -> None:
    """在资源管理器中打开（文件则定位到它）。"""
    path = Path(path).absolute()
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{path}")
    if os.name != 'nt':
        raise OSError('此操作需要 Windows 资源管理器')
    # Ask the running Windows shell to open and activate the folder directly.
    # Spawning explorer.exe hands off to an existing process and can lose focus.
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.OleDLL('ole32')
    shell.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.c_void_p]
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
    shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    shell.ILFindLastID.argtypes = [ctypes.c_void_p]
    shell.ILFindLastID.restype = ctypes.c_void_p
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    initialized = ole.CoInitialize(None)
    folder = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        directory = path if path.is_dir() else path.parent
        result = shell.SHParseDisplayName(str(directory), None, ctypes.byref(folder), 0, None)
        if result < 0: raise OSError(f'无法定位目录（{result:#x}）：{directory}')
        children = None
        count = 0
        if not path.is_dir():
            result = shell.SHParseDisplayName(str(path), None, ctypes.byref(item), 0, None)
            if result < 0: raise OSError(f'无法定位文件（{result:#x}）：{path}')
            children = (ctypes.c_void_p * 1)(shell.ILFindLastID(item))
            count = 1
        result = shell.SHOpenFolderAndSelectItems(folder, count, children, 0)
        if result < 0: raise OSError(f'无法打开资源管理器（{result:#x}）')
    finally:
        if item.value: ole.CoTaskMemFree(item)
        if folder.value: ole.CoTaskMemFree(folder)
        if initialized >= 0: ole.CoUninitialize()
