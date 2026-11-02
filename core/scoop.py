"""User-scoped Scoop discovery and installed runtime inventory (no shell scans)."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil


@dataclass(frozen=True)
class RuntimeVersion:
    family: str
    package: str
    version: str
    directory: Path
    executable: Path
    current: bool
    default: bool


BINARIES = {"Java": "bin/java.exe", "Python": "python.exe", "Node": "node.exe"}


def scoop_script(root: Path) -> Path:
    return root / "apps/scoop/current/bin/scoop.ps1"


def valid_root(root: Path) -> bool:
    return (root / "apps").is_dir() and scoop_script(root).is_file()


def detect_root() -> Path | None:
    candidates = []
    if os.environ.get("SCOOP"):
        candidates.append(Path(os.environ["SCOOP"]))
    shim = shutil.which("scoop") or shutil.which("scoop.ps1")
    if shim:
        candidates.append(Path(shim).parent.parent)
    candidates.append(Path.home() / "scoop")
    return next((root for root in candidates if valid_root(root)), None)


def user_environment() -> dict[str, str]:
    """Read persisted user configuration, rather than the launcher's stale PATH."""
    env = {key.upper(): value for key, value in os.environ.items()}
    if os.name != "nt":
        return env
    import winreg
    paths = []
    for hive, subkey in ((winreg.HKEY_LOCAL_MACHINE,
                         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                        (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if isinstance(value, str):
                        if name.upper() == "PATH":
                            paths.append(value)
                        else:
                            env[name.upper()] = value
        except OSError:
            continue
    if paths:
        env["PATH"] = ";".join(paths)
    for _ in range(5):
        env = {key: re.sub(r"%([^%]+)%", lambda m: env.get(m[1].upper(), m[0]), value)
               for key, value in env.items()}
    return env


def default_binary(family: str, env: dict[str, str]) -> Path | None:
    if family == "Java" and env.get("JAVA_HOME"):
        candidate = Path(env["JAVA_HOME"]) / "bin/java.exe"
        if candidate.is_file():
            return candidate.resolve()
    binary = Path(BINARIES[family]).name
    for folder in env.get("PATH", "").split(os.pathsep):
        candidate = Path(folder.strip('"')) / binary
        if candidate.is_file():
            return candidate.resolve()
    return None


def scan(root: Path, env: dict[str, str] | None = None) -> list[RuntimeVersion]:
    if not valid_root(root):
        raise ValueError("请选择包含 apps、shims 的 Scoop 安装根目录。")
    env = user_environment() if env is None else env
    defaults = {family: default_binary(family, env) for family in BINARIES}
    result = []
    for package in sorted((root / "apps").iterdir()):
        if not package.is_dir() or package.is_symlink():
            continue
        try:
            current = (package / "current").resolve()
            for version in package.iterdir():
                if version.name == "current" or not version.is_dir() or version.is_symlink():
                    continue
                if not any((version / name).is_file() for name in ("manifest.json", "scoop-manifest.json")):
                    continue
                for family, relative in BINARIES.items():
                    executable = version / relative
                    if executable.is_file():
                        result.append(RuntimeVersion(family, package.name, version.name, version,
                                                     executable, version.resolve() == current,
                                                     executable.resolve() == defaults[family]))
        except OSError:
            continue
    return sorted(result, key=lambda item: (item.family, not item.default, item.package, item.version))


def reset_arguments(root: Path, runtime: RuntimeVersion) -> list[str]:
    """Use the bound installation's CLI; never build a shell command from input."""
    if not valid_root(root):
        raise ValueError("Scoop 安装目录不可用。")
    for value in (runtime.package, runtime.version):
        if not re.fullmatch(r"[A-Za-z0-9._+\-]+", value):
            raise ValueError("包名或版本格式不受支持。")
    expected = root / "apps" / runtime.package / runtime.version
    if expected.resolve() != runtime.directory.resolve() or not runtime.executable.is_file():
        raise ValueError("版本目录已变化，请刷新后重试。")
    return ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(scoop_script(root)), "reset", f"{runtime.package}@{runtime.version}"]


def terminal_environment(runtime: RuntimeVersion) -> dict[str, str]:
    env = user_environment()
    env["PATH"] = str(runtime.executable.parent) + os.pathsep + env.get("PATH", "")
    if runtime.family == "Java":
        env["JAVA_HOME"] = str(runtime.directory)
    elif runtime.family == "Python":
        env["PATH"] = str(runtime.directory / "Scripts") + os.pathsep + env["PATH"]
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
    return env
