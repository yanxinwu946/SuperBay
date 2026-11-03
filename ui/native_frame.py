"""Windows owns caption buttons, resizing, system menu and snap behavior."""
import ctypes
import sys
from ctypes import wintypes


def apply_native_frame(window, dark: bool) -> None:
    if sys.platform != 'win32':
        return
    dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
    dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.restype = ctypes.c_long
    hwnd = int(window.winId())
    def attribute(key, value):
        data = wintypes.DWORD(value)
        return dwm(hwnd, key, ctypes.byref(data), ctypes.sizeof(data))
    build = sys.getwindowsversion().build
    # Windows 10 caches the caption until the window is mapped again, so a
    # live theme switch needs one hide/show cycle. Windows 11 updates in place.
    refresh = (build < 22000 and window.isVisible()
               and getattr(window, "_native_frame_dark", dark) != dark)
    was_maximized = window.isMaximized()
    was_active = window.isActiveWindow()
    if refresh:
        window.hide()
    # Immersive dark mode is the only caption switch Windows 10 understands.
    attribute(20, int(dark))
    attribute(19, int(dark))
    if build >= 22000:
        attribute(33, 2)  # System rounded corners.
        attribute(35, 0x0026211e if dark else 0x00ffffff)  # Native caption color.
        attribute(36, 0x00eee9e6 if dark else 0x003d322b)  # Caption text color.
    # Mark before showing so the nested showEvent does not hide/show again.
    window._native_frame_dark = dark
    if refresh:
        window.show()
        if was_maximized:
            window.showMaximized()
        if was_active:
            window.raise_()
            window.activateWindow()
    ctypes.windll.dwmapi.DwmFlush()


def prepare_backdrop(window) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    if (sys.platform == "win32" and QGuiApplication.platformName() == "windows"
            and sys.getwindowsversion().build >= 22621):
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def apply_backdrop(window, enabled=True) -> bool:
    """Use the documented system backdrop only; no compatibility blur."""
    from PySide6.QtGui import QGuiApplication
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return False
    build = sys.getwindowsversion().build
    if build < 22621:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            enabled = enabled and bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        pass
    class HighContrast(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("flags", wintypes.DWORD), ("scheme", wintypes.LPWSTR)]
    contrast = HighContrast()
    contrast.size = ctypes.sizeof(contrast)
    if ctypes.windll.user32.SystemParametersInfoW(0x0042, contrast.size, ctypes.byref(contrast), 0):
        enabled = enabled and not bool(contrast.flags & 1)
    hwnd = wintypes.HWND(int(window.winId()))
    class Margins(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]
    value = wintypes.DWORD(3 if enabled else 1)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
    if result < 0 or not enabled:
        margins = Margins(0, 0, 0, 0)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        return False
    margins = Margins(-1, -1, -1, -1)
    if ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) >= 0:
        return True
    # A partial application must not leave a transparent frame behind.
    value = wintypes.DWORD(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
    margins = Margins(0, 0, 0, 0)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    return False
