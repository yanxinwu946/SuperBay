from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QGuiApplication
from ui import native_frame


class NativeBackdropTests(unittest.TestCase):
    def test_windows10_keeps_opaque_window_without_requesting_native_handles(self):
        window = Mock()
        with (patch.object(native_frame.sys, "platform", "win32"),
              patch.object(native_frame.sys, "getwindowsversion", return_value=SimpleNamespace(build=19045), create=True),
              patch.object(QGuiApplication, "platformName", return_value="windows")):
            native_frame.prepare_backdrop(window)
            self.assertFalse(native_frame.apply_backdrop(window))
        window.setAttribute.assert_not_called()
        window.winId.assert_not_called()

    def test_non_native_platform_does_not_enable_transparency(self):
        window = Mock()
        with patch.object(QGuiApplication, "platformName", return_value="offscreen"):
            native_frame.prepare_backdrop(window)
            self.assertFalse(native_frame.apply_backdrop(window))
        window.setAttribute.assert_not_called()
        window.winId.assert_not_called()


if __name__ == "__main__":
    unittest.main()
