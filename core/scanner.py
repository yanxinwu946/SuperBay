"""Scan nested categories while keeping recognized tool projects intact."""
from __future__ import annotations
import logging
from pathlib import Path
from .models import TYPE_DIR, TYPE_FILE, TYPE_TOOL, Category, Entry, strip_prefix
from .notes import find_note, note_excerpt

log = logging.getLogger(__name__)
SKIP_CAT_NAMES = {'supergui', 'node_modules', '__pycache__', 'venv', 'vendor'}
SKIP_FILE_NAMES = {'00.txt', 'note.md', 'note.txt', 'readme.md', 'readme.txt',
    'readme.zh-cn.md', 'license', 'license.md', 'license.txt', 'contributing.md',
    'changelog.md', 'desktop.ini', 'thumbs.db', '.ds_store'}
TOOL_SUFFIXES = {'.exe','.bat','.cmd','.lnk','.ps1','.py','.jar','.js','.mjs','.cjs'}
PROJECT_MARKERS = {'pyproject.toml','package.json','cargo.toml','go.mod','requirements.txt','.git'}
DEFAULT_SCAN_DEPTH = 2
MAX_SCAN_DEPTH = 16


def normalize_scan_depth(value):
    try:
        return max(1, min(MAX_SCAN_DEPTH, int(value)))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_SCAN_DEPTH


def _children(path):
    try:
        return sorted((p for p in path.iterdir() if not p.name.startswith('.')
                       and p.name.lower() not in SKIP_CAT_NAMES),
                      key=lambda p: (not p.is_dir(), p.name.casefold()))
    except OSError as error:
        log.warning('无法读取目录 %s: %s', path, error)
        return []


def _is_tool(path, children):
    return (any(p.is_file() and (p.suffix.lower() in TOOL_SUFFIXES or p.name.lower() in PROJECT_MARKERS)
                for p in children) or (path / '.git').exists())


def _entry_type(path, is_dir, children=None):
    if not is_dir: return TYPE_FILE
    return TYPE_TOOL if _is_tool(path, _children(path) if children is None else children) else TYPE_DIR


def scan(root: Path, max_depth=DEFAULT_SCAN_DEPTH) -> list[Category]:
    root = Path(root)
    max_depth = normalize_scan_depth(max_depth)
    if not root.is_dir():
        log.error('工具目录不存在: %s', root)
        return []
    categories = []
    resolved_root = root.resolve()
    visited = {resolved_root}

    def add_entry(category, path, children=None):
        directory = path.is_dir()
        note = find_note(path) if directory else None
        entry = Entry(path.name, path, category.cat_id, category.name, directory,
                      entry_type=_entry_type(path, directory, children), note_path=note)
        entry.desc = note_excerpt(note) or f'{category.name} / {path.name}'
        category.entries.append(entry)

    def visit(path, parent=None, depth=0):
        key = path.relative_to(root).as_posix()
        category = Category(key, strip_prefix(path.name), path, parent_id=parent, depth=depth)
        categories.append(category)
        for child in _children(path):
            if child.is_dir():
                try:
                    resolved = child.resolve()
                    if resolved in visited or not resolved.is_relative_to(resolved_root): continue
                    visited.add(resolved)
                except OSError: continue
                children = _children(child)
                nested = any(p.is_dir() or p.name.lower() not in SKIP_FILE_NAMES for p in children)
                if depth+1 < max_depth and nested and not _is_tool(child, children):
                    visit(child, key, depth+1)
                else:
                    add_entry(category, child, children)
            elif child.name.lower() not in SKIP_FILE_NAMES:
                add_entry(category, child)

    root_items = _children(root)
    files = [p for p in root_items if p.is_file() and p.name.lower() not in SKIP_FILE_NAMES]
    if files:
        category = Category('__root__', strip_prefix(root.name), root)
        categories.append(category)
        for path in files: add_entry(category, path)
    for directory in root_items:
        if directory.is_dir():
            try:
                resolved = directory.resolve()
                if resolved in visited or not resolved.is_relative_to(resolved_root): continue
                visited.add(resolved)
            except OSError: continue
            visit(directory)
    log.info('扫描完成: %d 个分类, %d 个条目',len(categories),sum(len(c.entries) for c in categories))
    return categories
