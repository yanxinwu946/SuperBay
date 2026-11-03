"""Create a desktop shortcut for SuperBay itself.

Usage:
    python tools/create_shortcut.py [directory]

Without a directory, the current user's Desktop is used.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.shortcut import create_app_shortcut


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    link = create_app_shortcut(folder)
    print(link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
