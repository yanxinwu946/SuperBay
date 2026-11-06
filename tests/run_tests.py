"""Run the suite with a deterministic exit code on headless Windows runners."""
import os
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    code = 1
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        code = 0 if result.wasSuccessful() else 1
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        # Qt teardown for the offscreen platform occasionally crashes on the
        # Windows runner after the suite has already passed. Exiting the
        # process directly keeps the reported result tied to the tests.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
