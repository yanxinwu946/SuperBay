"""Run shell commands inside a user-chosen terminal emulator."""
from __future__ import annotations

import os
import struct
import subprocess
import re
import shutil
import zipfile
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


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


# Main-class constants are enough to tell a desktop Java app from a CLI jar.
_JAR_GUI_MARKERS = (b'javafx/', b'javax/swing', b'java/awt/Frame', b'java/awt/Window',
                    b'java/awt/Canvas', b'org/eclipse/swt', b'JFrame', b'JDialog', b'JOptionPane')
_JAR_ARG = re.compile(r'(?:^|\s)-jar\s+(?:"([^"]+)"|(\S+))(?:\s+(.*))?$',
                      re.IGNORECASE | re.DOTALL)


def _manifest_main_class(manifest: bytes) -> str | None:
    lines = manifest.decode('utf-8', 'replace').replace('\r\n', '\n').split('\n')
    joined: list[str] = []
    for line in lines:
        if line.startswith(' ') and joined:
            joined[-1] += line[1:]
        else:
            joined.append(line)
    for line in joined:
        key, _, value = line.partition(':')
        if key.strip().lower() == 'main-class':
            return value.strip()
    return None


def jar_is_gui(path: Path) -> bool:
    """Best effort: inspect the manifest's main class for desktop UI packages."""
    try:
        with zipfile.ZipFile(path) as archive:
            main = _manifest_main_class(archive.read('META-INF/MANIFEST.MF'))
            if not main:
                return False
            data = archive.read(main.replace('.', '/') + '.class')
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return any(marker in data for marker in _JAR_GUI_MARKERS)


def _javaw_for(java: Path, environment) -> Path | None:
    candidate = java.with_name('javaw.exe')
    if candidate.is_file():
        return candidate
    path = (environment if environment is not None else os.environ).get('PATH', '')
    found = shutil.which('javaw', path=path)
    return Path(found) if found else None


def _java_gui_spec(java: Path, parameters: str, cwd: Path, environment):
    """`java -jar` pointing at a desktop JAR becomes a windowless javaw launch."""
    match = _JAR_ARG.search(parameters)
    if not match:
        return None
    jar = Path(match[1] or match[2])
    if not jar.is_absolute():
        jar = cwd / jar
    if not jar.is_file() or jar.suffix.lower() != '.jar' or not jar_is_gui(jar):
        return None
    javaw = _javaw_for(java, environment)
    if javaw is None:
        return None
    prefix = parameters[:match.start()].strip()
    suffix = (match[3] or '').strip()
    command = (f'{prefix} ' if prefix else '') + '-jar ' + subprocess.list2cmdline([str(jar)])
    if suffix:
        command += ' ' + suffix
    return javaw, command


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
    if path.suffix.lower() == '.exe' and is_gui_exe(path):
        return path, match[4] or ''
    if path.stem.lower() == 'java':
        return _java_gui_spec(path, match[4] or '', cwd, environment)
    return None


def terminal_argv(terminal: str, template: str, cwd: Path, command: list[str]) -> tuple[str, str]:
    """返回 (终端程序, 参数)；终端由 launch_terminal 隐藏控制台启动。"""
    if not terminal or not terminal.strip():
        raise RuntimeError("未配置终端程序。")
    path = _resolve_shim(Path(terminal.strip()).expanduser())
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
    if not terminal or not terminal.strip():
        try:
            subprocess.Popen(command, cwd=str(cwd), creationflags=CREATE_NEW_CONSOLE,
                             close_fds=True)
        except OSError as error:
            raise RuntimeError(f"无法启动系统控制台：{error}") from error
        return
    target, arguments = terminal_argv(terminal, template, cwd, command)
    line = subprocess.list2cmdline([target])
    if arguments:
        line += " " + arguments
    try:
        subprocess.Popen(line, cwd=str(cwd), creationflags=CREATE_NO_WINDOW,
                         close_fds=True)
    except OSError as error:
        raise RuntimeError(f"无法启动终端：{error}") from error
