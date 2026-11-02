"""Workspace aggregation, entry suggestions, checks and portable configuration."""
from pathlib import Path
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from core import scanner
from core.runtime_profile import profile_environment


def workspace_roots(settings, current):
    # Shortcuts are navigation targets, never additional scan roots.
    return [Path(current)]


def scan_roots(roots):
    seen, categories = set(), []
    multiple = len(roots) > 1
    for root in roots:
        for cat in scanner.scan(root):
            cat.entries = [e for e in cat.entries if os.path.normcase(str(e.path.resolve())) not in seen]
            for e in cat.entries:
                seen.add(os.path.normcase(str(e.path.resolve())))
            if not cat.entries:
                continue
            if multiple:
                cat.cat_id = str(cat.path.resolve())
                cat.name = f'{cat.name} · {root.name}'
                for e in cat.entries:
                    e.cat_id, e.cat_name = cat.cat_id, cat.name
            categories.append(cat)
    return categories


def suggestions(entry):
    root = entry.path
    if not root.exists():
        return []
    files = list(root.iterdir()) if root.is_dir() else [root]
    commands = []
    priority = {'main.py': 0, 'app.py': 1, 'cli.py': 2, 'index.js': 3, 'main.js': 4}
    for path in sorted(files, key=lambda p: (priority.get(p.name.lower(), 10), p.name.lower())):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        import subprocess
        quoted = subprocess.list2cmdline([str(path)])
        if ext == '.py':
            venv = path.parent / '.venv/Scripts/python.exe'
            python = subprocess.list2cmdline([str(venv)]) if venv.is_file() else 'python'
            command = f'{python} -u {quoted}'
        elif ext == '.jar': command = f'java -jar {quoted}'
        elif ext in ('.js', '.mjs', '.cjs'): command = f'node {quoted}'
        elif ext in ('.exe', '.bat', '.cmd'): command = quoted
        elif ext == '.ps1': command = f'powershell -NoProfile -File {quoted}'
        else: continue
        if any(word in path.stem.lower() for word in ('uninstall', 'setup', 'update')):
            continue
        commands.append((path.name, command))
    return commands


def health(entry):
    issues = []
    if not entry.path.exists(): return ['路径不存在：重新定位工具或恢复目录']
    try:
        environment = profile_environment(entry.runtime_profile) if entry.runtime_profile else dict(os.environ)
    except ValueError as error:
        issues.append(str(error)); environment = dict(os.environ)
    choices = suggestions(entry)
    command = entry.command or (choices[0][1] if len(choices) == 1 else '')
    if not entry.command and not choices and entry.is_dir:
        issues.append('未找到启动入口：编辑命令，或作为资料目录打开')
    if not entry.command and len(choices) > 1:
        issues.append('存在多个入口：使用“识别启动入口”选择')
    if command:
        import re
        token = re.match(r'^\s*(?:"([^"]+)"|([^\s]+))', command)
        exe = (token.group(1) or token.group(2)) if token else ''
        if exe and not Path(exe).is_file() and not shutil.which(exe, path=environment.get('PATH')):
            issues.append(f'找不到 {exe}：绑定运行环境或修改命令')
        # Check quoted paths from automatically generated or user-saved commands.
        for value in re.findall(r'"([^"]+)"', command):
            if Path(value).is_absolute() and not Path(value).exists():
                issues.append(f'入口路径不存在：{value}')
    return issues


def export_backup(target, settings, db):
    # SQLite backup captures a consistent snapshot while the application is open.
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / 'data.db'
        conn = sqlite3.connect(snapshot)
        try: db._conn.backup(conn)
        finally: conn.close()
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'SuperGUI', 'version': 1}))
            archive.writestr('settings.json', json.dumps(settings._data, ensure_ascii=False))
            archive.write(snapshot, 'data.db')


def import_backup(source, settings, db, old_root='', new_root=''):
    # Read only known members; never extract arbitrary archive paths.
    with zipfile.ZipFile(source) as archive:
        if any(archive.getinfo(name).file_size > 50_000_000 for name in ['manifest.json','settings.json','data.db']):
            raise ValueError('备份文件过大')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest != {'format':'SuperGUI','version':1}: raise ValueError('不支持的备份格式')
        config=json.loads(archive.read('settings.json'))
        data=archive.read('data.db')
    if not isinstance(config, dict): raise ValueError('设置格式无效')
    with tempfile.TemporaryDirectory() as temp:
        snapshot=Path(temp)/'data.db';snapshot.write_bytes(data)
        incoming=sqlite3.connect(snapshot)
        incoming.row_factory=sqlite3.Row
        try:
            if incoming.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('数据库损坏')
            tables={r[0] for r in incoming.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'meta','stats'} <= tables: raise ValueError('缺少配置数据')
            rows={table:[dict(r) for r in incoming.execute('SELECT * FROM '+table)] for table in ('meta','stats')}
        finally: incoming.close()
    def remap(value):
        if isinstance(value, dict): return {remap(k):remap(v) for k,v in value.items()}
        if isinstance(value, list): return [remap(v) for v in value]
        if isinstance(value, str) and old_root and new_root:
            # Prefix-only migration avoids rewriting arbitrary commands or unrelated text.
            try: return str(Path(new_root)/Path(value).relative_to(Path(old_root)))
            except ValueError: pass
        return value
    config=remap(config)
    with db._conn:
        for table, records in rows.items():
            allowed={r[1] for r in db._conn.execute('PRAGMA table_info('+table+')')}
            for record in records:
                record={k:remap(v) for k,v in record.items() if k in allowed}
                if not isinstance(record.get('path'),str): raise ValueError('无效条目路径')
                if record.get('runtime_profile'):
                    record['runtime_profile'] = json.dumps(remap(json.loads(record['runtime_profile'])), ensure_ascii=False)
                fields=list(record)
                db._conn.execute('INSERT OR REPLACE INTO '+table+' ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',list(record.values()))
    settings._data.update(config);settings.save()
