"""Run shell commands inside a user-chosen terminal emulator."""
from __future__ import annotations

import os
import struct
import subprocess
import re
import shutil
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _quoted(path: Path) -> str:
    return subprocess.list2cmdline([str(path)])


def _resolve_shim(path: Path) -> Path:
    """Scoop shims are console wrappers; use the real executable when known."""
    shim = path.with_suffix(".shim")
    if shim.is_file():
        try:
            for line in shim.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "path":
                    real = Path(value.strip().strip('"'))
                    if real.is_file():
                        path = real
                        break
        except OSError:
            pass
    # wezterm.exe is the console launcher; wezterm-gui.exe opens the window itself.
    if path.name.lower() == "wezterm.exe":
        gui = path.with_name("wezterm-gui.exe")
        if gui.is_file():
            path = gui
    return path


def pe_subsystem(path: Path) -> int | None:
    try:
        with Path(path).open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return None
            offset = struct.unpack_from("<I", header, 0x3C)[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                return None
            stream.seek(offset + 24 + 68)  # COFF header + optional header Subsystem
            data = stream.read(2)
            return struct.unpack("<H", data)[0] if len(data) == 2 else None
    except (OSError, struct.error):
        return None


def is_gui_exe(path: Path) -> bool:
    return pe_subsystem(path) == 2


def gui_command(command: str, cwd: Path, environment=None, shell='cmd'):
    """Recognize a direct GUI executable invocation, never a shell expression."""
    text = command.strip()
    if shell == 'powershell' and text.startswith('& '): text = text[2:].lstrip()
    # Expansion, pipelines and redirection must keep their original shell semantics.
    quote = None
    for char in text:
        if char in '$%`\r\n': return None
        if char in ('"', "'"):
            if quote == char: quote = None
            elif quote is None: quote = char
        elif quote is None and char in '&|<>;^': return None
    if quote: return None
    match = re.match(r'''^(?:"([^"]+)"|'([^']+)'|([^\s]+))(?:\s+(.*))?$''', text, re.S)
    if not match: return None
    if match[2] and shell != 'powershell': return None
    name = match[1] or match[2] or match[3]
    path = Path(name)
    if not path.is_absolute(): path = cwd / path
    if not path.is_file():
        found = shutil.which(name, path=(environment if environment is not None else os.environ).get('PATH', ''))
        if not found: return None
        path = Path(found)
    if path.suffix.lower() != '.exe' or not is_gui_exe(path): return None
    return path, match[4] or ''


def terminal_argv(terminal: str, template: str, cwd: Path, command: list[str]) -> tuple[str, str]:
    """返回 (终端程序, 参数)；终端由 launch_terminal 隐藏控制台启动。"""
    path = _resolve_shim(Path(terminal).expanduser())
    if not path.is_file():
        raise RuntimeError(f"终端程序不存在：{terminal}")
    text = template.strip()
    if text:
        if "{command}" not in text:
            raise RuntimeError("终端参数模板缺少 {command} 占位符。")
        args = (text.replace("{cwd}", str(cwd))
                    .replace("{command}", subprocess.list2cmdline(command)))
        return str(path), args
    name = path.name.lower()
    cmdline = subprocess.list2cmdline(command)
    if name.startswith("wt"):
        return str(path), f"-d {_quoted(cwd)} {cmdline}"
    if name.startswith("wezterm"):
        return str(path), f"start --cwd {_quoted(cwd)} -- {cmdline}"
    if name.startswith("alacritty"):
        return str(path), f"--working-directory {_quoted(cwd)} -e {cmdline}"
    # Most remaining terminals follow the `-e <program> [args]` convention.
    return str(path), f"-e {cmdline}"


def launch_terminal(terminal: str, template: str, cwd: Path, command: list[str]) -> None:
    """启动终端且不留下启动器自己的控制台窗口。"""
    target, arguments = terminal_argv(terminal, template, cwd, command)
    line = subprocess.list2cmdline([target])
    if arguments:
        line += " " + arguments
    try:
        subprocess.Popen(line, cwd=str(cwd), creationflags=CREATE_NO_WINDOW,
                         close_fds=True)
    except OSError as error:
        raise RuntimeError(f"无法启动终端：{error}") from error
