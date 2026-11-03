# -*- mode: python ; coding: utf-8 -*-


from pathlib import Path
ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / 'tools/frozen_entry.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'assets'), 'assets'), (str(ROOT / 'ui/themes'), 'ui/themes')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['qfluentwidgets', 'PyQt5', 'PyQt6', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
    optimize=0,
)
# Windows 10+ resolves API-set DLLs through the OS. Do not bundle stale
# compatibility shims discovered in unrelated third-party PATH directories.
import os
import site
import sys
library_roots = [Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve(),
                 Path(os.environ['SystemRoot']).resolve(),
                 *[Path(folder).resolve() for folder in site.getsitepackages()]]
a.binaries = [row for row in a.binaries
              if not Path(row[0]).name.lower().startswith(('api-ms-win-', 'ext-ms-'))
              and any(Path(row[1]).resolve().is_relative_to(root) for root in library_roots)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SuperBay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / 'assets/supergui.ico')],
)
