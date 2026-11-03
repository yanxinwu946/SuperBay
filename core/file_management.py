"""Workspace file operations with collision checks and metadata preservation."""
from pathlib import Path
import json
import shutil
import re


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('请选择工作区内的子目录或文件')
    return path


def valid_name(name):
    name = name.strip()
    if (not name or name in {'.', '..'} or name.endswith(('.', ' '))
            or any(c in name for c in '<>:"/\\|?*')
            or any(ord(c) < 32 for c in name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
        raise ValueError('名称为空或含 Windows 不支持的字符')
    return name


def relocate(db, root, pairs):
    """Move on the same volume; roll back completed moves if metadata fails."""
    for a, _ in pairs:
        path = Path(a)
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('请操作原目录，不移动链接')
    for a, b in pairs:
        inside(root, a); inside(root, b)
    pairs = [(Path(a).absolute(), Path(b).absolute()) for a, b in pairs]
    sources = [a for a, _ in pairs]
    targets = [b for _, b in pairs]
    if len(set(targets)) != len(targets):
        raise ValueError('目标名称重复')
    for source, target in pairs:
        if not source.exists() or target.exists():
            raise ValueError(f'来源不存在或目标已存在：{target.name}')
        if not target.parent.is_dir() or target.is_relative_to(source):
            raise ValueError('不能移入自身或不存在的目录')
        if any(source != other and source.is_relative_to(other) for other in sources):
            raise ValueError('不能同时移动父目录和其中的条目')

    def remap(text):
        for source, target in pairs:
            if text == str(source) or text.startswith(str(source) + '\\') or text.startswith(str(source) + '/'):
                return str(target) + text[len(str(source)):]
        return text

    def rewrite_command(command):
        if not command: return command
        for source, target in pairs:
            variants = {str(source): str(target), source.as_posix(): target.as_posix()}
            for old, new in variants.items():
                command = re.sub(re.escape(old) + r'''(?=$|[\\/\s"'])''', lambda _: new, command)
        return command

    def remap_tree(value):
        if isinstance(value, dict): return {k: remap_tree(v) for k, v in value.items()}
        if isinstance(value, list): return [remap_tree(v) for v in value]
        return remap(value) if isinstance(value, str) else value

    done = []
    try:
        with db._conn:
            for source, target in pairs:
                source.rename(target)
                done.append((source, target))
            for table in ('meta', 'stats'):
                rows = list(db._conn.execute('SELECT * FROM ' + table))
                for row in rows:
                    new = remap(row['path'])
                    if new == row['path']:
                        continue
                    # Reuse a stale destination only after explicit repair, never overwrite it.
                    if db._conn.execute('SELECT 1 FROM '+table+' WHERE path=?', (new,)).fetchone():
                        raise ValueError('目标已有工具配置，请先选择其他位置')
                    db._conn.execute('UPDATE '+table+' SET path=? WHERE path=?', (new, row['path']))
                    if table == 'meta':
                        command = rewrite_command(row['command'])
                        items = remap_tree(json.loads(row['launch_plans'] or '[]'))
                        for item in items: item['command'] = rewrite_command(item.get('command', ''))
                        profile = remap_tree(json.loads(row['runtime_profile'] or '{}'))
                        db._conn.execute('UPDATE meta SET command=?, launch_plans=?, runtime_profile=? WHERE path=?',
                            (command, json.dumps(items, ensure_ascii=False), json.dumps(profile, ensure_ascii=False), new))
    except Exception:
        for source, target in reversed(done):
            target.rename(source)
        raise


IMPORT_SKIP_DIRS = {'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def import_paths(root, destination, paths, skip_dependencies=True, skipped=None):
    destination = Path(destination).resolve()
    if destination != Path(root).resolve():
        inside(root, destination)
    sources = [Path(p).resolve() for p in paths]
    targets = [destination / p.name for p in sources]
    if len(set(targets)) != len(targets):
        raise ValueError('待导入文件名称重复')
    for source, target in zip(sources, targets):
        if not source.exists() or source == target or target.exists():
            raise ValueError(f'来源无效或目标已存在：{target.name}')
        if target.is_relative_to(source):
            raise ValueError('不能把目录导入它自身的子目录')
    def ignore(directory, names):
        ignored = []
        for name in names:
            path = Path(directory) / name
            if skip_dependencies and name.casefold() in IMPORT_SKIP_DIRS and path.is_dir():
                ignored.append(name)
                if skipped is not None: skipped.add(name)
                continue
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('导入目录含链接，请先移除链接或使用目录快捷入口')
        return ignored
    # Copy into a staging directory so a failed copy leaves no incomplete visible tool.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='.superbay-import-', dir=destination) as temp:
        staged = []
        for source in sources:
            stage = Path(temp) / source.name
            if source.is_dir(): shutil.copytree(source, stage, ignore=ignore)
            else: shutil.copy2(source, stage)
            staged.append(stage)
        done = []
        try:
            for stage, target in zip(staged, targets):
                if target.exists(): raise ValueError(f'目标已存在：{target.name}')
                stage.rename(target)
                done.append((stage, target))
        except Exception:
            for stage, target in reversed(done): target.rename(stage)
            raise
    return targets
