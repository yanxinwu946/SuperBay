"""Read-only bundle resources and persistent user data for single-file builds."""
import os
import sys
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parents[1]


def data_dir():
    if not getattr(sys, 'frozen', False):
        return RESOURCE_DIR
    folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'SuperGUI'
    folder.mkdir(parents=True, exist_ok=True)
    return folder
