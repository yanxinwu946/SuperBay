"""Read-only bundle resources and persistent user data for single-file builds."""
import os
import shutil
import tempfile
import sys
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parents[1]


def data_dir():
    if not getattr(sys, 'frozen', False):
        return RESOURCE_DIR
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    folder = base / 'SuperBay'
    old = base / 'SuperGUI'
    if not folder.exists() and old.is_dir():
        base.mkdir(parents=True, exist_ok=True)
        # Copy into a temporary directory first: an interrupted migration must
        # never be mistaken for a complete configuration on the next launch.
        with tempfile.TemporaryDirectory(prefix='.superbay-migration-', dir=base) as tmp:
            staged = Path(tmp) / 'data'
            shutil.copytree(old, staged)
            try:
                staged.rename(folder)
            except FileExistsError:
                pass  # Another instance completed migration first.
    folder.mkdir(parents=True, exist_ok=True)
    return folder
