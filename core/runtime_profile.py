"""Resolve per-tool runtime pins without changing any global environment."""
import os
import re
from pathlib import Path

from core.scoop import BINARIES, valid_root


def profile_environment(profile: dict) -> dict[str, str]:
    versions = profile.get("versions")
    root = profile.get("root")
    if not isinstance(versions, dict) or not versions or not isinstance(root, str) or not root:
        raise ValueError("运行环境配置无效，请重新绑定。")
    root = Path(root)
    if not valid_root(root):
        raise ValueError("Scoop 目录不可用，请重新绑定。")
    env = dict(os.environ)
    prefixes = []
    for family, ref in versions.items():
        if (not isinstance(ref, dict) or not isinstance(ref.get("package"), str)
                or not isinstance(ref.get("version"), str)):
            raise ValueError("运行环境配置无效，请重新绑定。")
        relative = BINARIES.get(family)
        if not relative or not all(re.fullmatch(r"[A-Za-z0-9._+\-]+", ref[key])
                                   and ref[key] not in (".", "..", "current") for key in ("package", "version")):
            raise ValueError("运行环境配置无效，请重新绑定。")
        directory = root / "apps" / ref["package"] / ref["version"]
        executable = directory / relative
        if (not executable.is_file() or not any((directory / name).is_file()
                for name in ("manifest.json", "scoop-manifest.json"))):
            raise ValueError(f"绑定的 {family} 版本已不可用，请重新选择运行环境。")
        prefixes.append(str(executable.parent))
        if family == "Java":
            env["JAVA_HOME"] = str(directory)
        elif family == "Python":
            prefixes.append(str(directory / "Scripts"))
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
    env["PATH"] = os.pathsep.join(prefixes + [env.get("PATH", "")])
    return env
