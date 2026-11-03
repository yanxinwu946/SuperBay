"""Windows desktop shortcuts for tools. No third-party dependencies."""
from __future__ import annotations

import base64
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

from core.launcher import find_main_exe
from core.models import Entry
from core.process_env import powershell_arguments
from core.runtime_profile import profile_environment

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RUN_AS_ADMIN_FLAG = 0x00001000  # MS-SHLLINK LinkFlags.RunAsUser


def desktop_directory() -> Path:
    """系统桌面目录；OneDrive 重定向时由 Windows 返回真实位置。"""
    if sys.platform == "win32":
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) == 0:
            if buffer.value:
                return Path(buffer.value)
    return Path.home() / "Desktop"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(script: str) -> None:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=30,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"无法调用 PowerShell：{error}") from error
    if result.returncode:
        raw = result.stdout or result.stderr
        try:
            message = raw.decode("utf-8")
        except UnicodeDecodeError:
            message = raw.decode("gb18030", errors="replace")
        message = message.strip()
        if message.startswith("ERROR: "):
            message = message[7:]
        raise RuntimeError(message or "创建快捷方式失败")


def _runtime_command(entry: Entry, cwd: Path) -> tuple[str, str]:
    """与 core.launcher 的绑定环境分支保持一致。"""
    if entry.command:
        if entry.shell_type == "powershell":
            return "powershell.exe", "-NoLogo -NoProfile -NoExit -Command " + entry.command
        return "cmd.exe", '/d /s /k "' + entry.command + '"'
    executable = find_main_exe(entry.path) if entry.is_dir else entry.path
    if executable is None:
        raise RuntimeError("没有可自动识别的主程序，请先编辑启动命令。")
    ext = executable.suffix.lower()
    runners = {".py": "python.exe", ".jar": "java.exe", ".js": "node.exe"}
    if ext in runners:
        return runners[ext], ("-jar " if ext == ".jar" else "") + subprocess.list2cmdline([str(executable)])
    if ext in (".cmd", ".bat"):
        return "cmd.exe", '/d /s /k "' + subprocess.list2cmdline([str(executable)]) + '"'
    if ext == ".ps1":
        return "powershell.exe", "-NoLogo -NoProfile -NoExit -File " + subprocess.list2cmdline([str(executable)])
    if ext == ".exe":
        return str(executable), ""
    raise RuntimeError("此文件类型不支持绑定运行环境，请配置自定义启动命令。")


def shortcut_spec(entry: Entry) -> tuple[str, str, str, str, bool]:
    """返回 (目标, 参数, 工作目录, 图标位置, 是否管理员启动)。"""
    path = entry.path
    if not path.exists():
        raise RuntimeError(f"路径不存在：{path}")
    cwd = path if entry.is_dir else path.parent
    icon_source = None
    admin = entry.admin
    if entry.runtime_profile:
        environment = profile_environment(entry.runtime_profile)
        target, parameters = _runtime_command(entry, cwd)
        args = powershell_arguments(target, parameters, cwd, environment)
        target = "powershell.exe"
        icon_source = find_main_exe(path) if entry.is_dir else path
    elif entry.command:
        if entry.shell_type == "powershell":
            target, args = "powershell.exe", "-NoExit -Command " + entry.command
        else:
            target, args = "cmd.exe", '/d /s /k "' + entry.command + '"'
        icon_source = find_main_exe(path) if entry.is_dir else path
    elif entry.is_dir:
        executable = find_main_exe(path)
        if executable:
            target, args, icon_source = str(executable), "", executable
        else:
            # WScript 不接受目录作为 TargetPath；用 Explorer 打开。
            explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
            target, args = str(explorer), f'"{path}"'
            admin = False  # 打开文件夹不需要提权
    else:
        target, args, icon_source = str(path), "", path
    icon = (f"{icon_source},0" if icon_source and icon_source.exists()
            and icon_source.suffix.lower() in {".exe", ".ico", ".dll"} else "")
    return target, args, str(cwd), icon, admin


def _set_run_as_admin(link: Path) -> None:
    data = bytearray(link.read_bytes())
    if len(data) < 24 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        raise RuntimeError("快捷方式格式无效，无法设置管理员启动。")
    flags = struct.unpack_from("<I", data, 20)[0]
    struct.pack_into("<I", data, 20, flags | RUN_AS_ADMIN_FLAG)
    link.write_bytes(data)


def _unique_link(folder: Path, name: str) -> Path:
    stem = INVALID_NAME.sub("_", name).strip(" .") or "快捷方式"
    link = folder / f"{stem}.lnk"
    index = 2
    while link.exists():
        link = folder / f"{stem} ({index}).lnk"
        index += 1
    return link


def _write_shortcut(link: Path, target: str, args: str, cwd: str,
                    description: str, icon: str, admin: bool) -> None:
    # WScript may save filenames through the system ANSI code page. Create the
    # link under an ASCII filename, then let Python perform the Unicode rename.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="superbay-link-") as temporary:
        staged = Path(temporary) / "shortcut.lnk"
        _write_shortcut_file(staged, target, args, cwd, description, icon, admin)
        import shutil
        if link.exists():
            raise FileExistsError(str(link))
        with staged.open("rb") as source, link.open("xb") as destination:
            shutil.copyfileobj(source, destination)


def _write_shortcut_file(link: Path, target: str, args: str, cwd: str,
                         description: str, icon: str, admin: bool) -> None:
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "try {",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$shortcut = $shell.CreateShortcut({_ps_quote(str(link))})",
        f"$shortcut.TargetPath = {_ps_quote(target)}",
        f"$shortcut.Arguments = {_ps_quote(args)}",
        f"$shortcut.WorkingDirectory = {_ps_quote(cwd)}",
        f"$shortcut.Description = {_ps_quote(description)}",
    ]
    if icon:
        lines.append(f"$shortcut.IconLocation = {_ps_quote(icon)}")
    lines += [
        "$shortcut.Save()",
        "} catch {",
        "Write-Output ('ERROR: ' + $_.Exception.Message)",
        "exit 1",
        "}",
    ]
    _run_powershell("\n".join(lines))
    if not link.is_file():
        raise RuntimeError("快捷方式没有生成，请检查桌面目录权限。")
    if admin:
        _set_run_as_admin(link)


def create_desktop_shortcut(entry: Entry, directory: Path | None = None) -> Path:
    """在桌面（或指定目录）创建启动该工具的快捷方式，返回 .lnk 路径。"""
    target, args, cwd, icon, admin = shortcut_spec(entry)
    folder = Path(directory) if directory else desktop_directory()
    folder.mkdir(parents=True, exist_ok=True)
    link = _unique_link(folder, entry.name)
    description = entry.name if entry.name == entry.cat_name else f"{entry.cat_name} · {entry.name}"
    _write_shortcut(link, target, args, cwd, description, icon, admin)
    return link


def app_shortcut_spec() -> tuple[str, str, str, str]:
    """当前 SuperBay 的启动目标：(程序, 参数, 工作目录, 图标)。"""
    app_dir = Path(__file__).resolve().parents[1]
    icon = app_dir / "assets" / "supergui.ico"
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        # The bundled ico lives in the one-file temp dir; use the exe's own icon.
        return str(exe), "", str(exe.parent), f"{exe},0"
    main = app_dir / "main.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if pythonw.is_file():
        return str(pythonw), subprocess.list2cmdline([str(main)]), str(app_dir), str(icon)
    vbs = app_dir / "start.vbs"
    wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"
    if vbs.is_file() and wscript.is_file():
        return str(wscript), subprocess.list2cmdline([str(vbs)]), str(app_dir), str(icon)
    return (str(sys.executable), subprocess.list2cmdline([str(main)]), str(app_dir),
            str(icon) if icon.is_file() else "")


def create_app_shortcut(directory: Path | None = None) -> Path:
    """在桌面（或指定目录）创建 SuperBay 自身的快捷方式。"""
    target, args, cwd, icon = app_shortcut_spec()
    folder = Path(directory) if directory else desktop_directory()
    folder.mkdir(parents=True, exist_ok=True)
    link = _unique_link(folder, "SuperBay")
    _write_shortcut(link, target, args, cwd, "SuperBay 工具启动器", icon, False)
    return link
