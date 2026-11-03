"""Run the suite with an explicit lifetime for the shared Qt application."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    import shiboken6

    # Keep Qt alive throughout discovery and execution, then destroy it while
    # Python and the binding are still available, rather than at interpreter exit.
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        shiboken6.delete(app)


if __name__ == "__main__":
    sys.exit(main())
