"""Detect each entry's language or packaging form for badges and filtering.

Detection runs during scanning and only inspects the immediate directory
contents the scanner has already listed, so it adds no extra tree walk.
"""
from __future__ import annotations

from pathlib import Path

# Display order doubles as sort order; languages first, plain data last.
KIND_LABELS = {
    "python": "Python",
    "go": "Go",
    "node": "Node",
    "java": "Java",
    "rust": "Rust",
    "dotnet": ".NET",
    "php": "PHP",
    "ruby": "Ruby",
    "exe": "EXE",
    "script": "脚本",
    "shortcut": "快捷方式",
    "other": "其他",
    "data": "资料",
}
KIND_ORDER = tuple(KIND_LABELS)

# Extra filter-only value: files that run directly, no project directory.
FILTER_LABELS = {**KIND_LABELS, "exe_file": "单文件 EXE"}
FILTER_ORDER = ("python", "go", "node", "java", "rust", "dotnet", "php", "ruby",
                "exe", "exe_file", "script", "shortcut", "other", "data")

_FILE_KINDS = {
    ".exe": "exe", ".msi": "exe", ".com": "exe",
    ".py": "python", ".pyw": "python", ".pyz": "python",
    ".go": "go",
    ".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "node",
    ".tsx": "node", ".jsx": "node",
    ".jar": "java", ".war": "java",
    ".rs": "rust",
    ".cs": "dotnet", ".vb": "dotnet",
    ".php": "php",
    ".rb": "ruby",
    ".bat": "script", ".cmd": "script", ".ps1": "script", ".psm1": "script",
    ".sh": "script", ".bash": "script", ".vbs": "script",
    ".lnk": "shortcut", ".url": "shortcut",
}

_MARKER_KINDS = {
    "pyproject.toml": "python", "requirements.txt": "python", "setup.py": "python",
    "setup.cfg": "python", "pipfile": "python", "poetry.lock": "python", "uv.lock": "python",
    "go.mod": "go", "go.sum": "go", "go.work": "go",
    "package.json": "node", "package-lock.json": "node", "pnpm-lock.yaml": "node",
    "yarn.lock": "node", "tsconfig.json": "node",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
    "settings.gradle": "java", "settings.gradle.kts": "java",
    "cargo.toml": "rust", "cargo.lock": "rust",
    "composer.json": "php",
    "gemfile": "ruby",
}

_DATA = ("data",)
_OTHER = ("other",)


def file_kind(path: Path) -> str:
    return _FILE_KINDS.get(path.suffix.lower(), "data")


def detect(path: Path, is_dir: bool, children=None, is_tool: bool = False) -> tuple[str, ...]:
    """Return every recognizable kind, most specific first.

    An otherwise unrecognized tool project is "other" rather than "data":
    it is still something to launch, just in a language we did not identify.
    """
    if not is_dir:
        return (file_kind(path),)
    if children is None:
        try:
            children = list(path.iterdir())
        except OSError:
            return _OTHER if is_tool else _DATA
    found = set()
    for child in children:
        # Name/extension matching avoids extra stat calls on directories that
        # the scanner has already listed.
        if kind := _MARKER_KINDS.get(child.name.lower()):
            found.add(kind)
        if kind := _FILE_KINDS.get(child.suffix.lower()):
            found.add(kind)
    if not found:
        return _OTHER if is_tool else _DATA
    return tuple(kind for kind in KIND_ORDER if kind in found)


def kinds_of(entry) -> tuple[str, ...]:
    if getattr(entry, "group", False):
        return ()
    kinds = getattr(entry, "kinds", ())
    if kinds:
        return kinds
    is_tool = getattr(entry, "is_dir", False) and getattr(entry, "entry_type", "") == "tool"
    return _OTHER if is_tool else _DATA


def primary(entry) -> str:
    return kinds_of(entry)[0]


def label(value: str) -> str:
    return FILTER_LABELS.get(value, value)


def matches(entry, value: str | None) -> bool:
    """Filter helper for sidebar chips, the language combo and tests."""
    if value in (None, "", "all"):
        return True
    if getattr(entry, "group", False):
        return False  # navigation folders are never a language match
    kinds = kinds_of(entry)
    if value == "exe_file":
        return not entry.is_dir and "exe" in kinds
    return value in kinds


def sort_key(entry) -> int:
    kinds = kinds_of(entry)
    return KIND_ORDER.index(kinds[0]) if kinds else len(KIND_ORDER)


def search_text(entry) -> str:
    """English ids plus display labels, so `语言:go` and `go` both work."""
    kinds = kinds_of(entry)
    return " ".join(kinds) + " " + " ".join(FILTER_LABELS[k] for k in kinds)
