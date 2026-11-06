"""Run the suite with a deterministic exit code on headless Windows runners."""
import faulthandler
import os
from pathlib import Path
import sys
import traceback
import unittest

faulthandler.enable()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _terminate(code: int) -> None:
    """Exit without Qt/PySide DLL detach, which can crash on headless Windows."""
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code & 0xFFFFFFFF)
    os._exit(code)


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
        # Windows runner after the suite has already passed. TerminateProcess
        # skips DLL detach and keeps the reported result tied to the tests.
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except OSError:
            pass
        _terminate(code)


if __name__ == "__main__":
    sys.exit(main())
