"""Frozen executable bootstrap; workers share the same standalone executable."""
import sys


def entry():
    if len(sys.argv) == 3 and sys.argv[1] == '--launch-worker':
        if sys.platform == 'win32' and sys.stdout is None:
            import ctypes
            ctypes.windll.kernel32.AllocConsole()
            sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
            sys.stderr = sys.stdout
            sys.stdin = open('CONIN$', 'r', encoding='utf-8')
        from core.launch_worker import execute
        if sys.platform == 'win32' and getattr(sys, 'frozen', False):
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        execute(sys.argv[2])
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        return self_test(sys.argv[2])
    from main import main
    return main()


def self_test(output):
    import json
    import tempfile
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from core.app_paths import RESOURCE_DIR
    from config.settings import Settings
    from ui.main_window import MainWindow
    from ui.theme import apply_theme
    from ui.misc import make_logo_icon
    app = QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        tool = folder / 'tools/A-tools/demo'
        tool.mkdir(parents=True)
        (tool / 'main.py').write_text('pass')
        settings = Settings(folder / 'settings.yaml')
        settings.set('workspace_root', str(folder / 'tools'))
        settings.set('layout_version', 2)
        settings.set('view', 'grid')
        MainWindow._build_ipc = lambda self: None
        apply_theme(app, RESOURCE_DIR, 'dark')
        window = MainWindow(folder, settings)
        window.show()
        elapsed = [0]
        def finish():
            elapsed[0] += 1
            if window._reloading and elapsed[0] < 100:
                QTimer.singleShot(100, finish)
                return
            result = {'entries': len(window.entries), 'icon': not make_logo_icon().isNull(),
                      'styles': (RESOURCE_DIR / 'ui/themes/glass.qss').is_file(),
                      'frozen': bool(getattr(sys, 'frozen', False))}
            Path(output).write_text(json.dumps(result), encoding='utf-8')
            window._tray_quit = True
            window.close()
            app.quit()
        QTimer.singleShot(200, finish)
        app.exec()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(entry())
    except Exception:
        if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
            import json
            import traceback
            from pathlib import Path
            Path(sys.argv[2]).write_text(json.dumps({'error': traceback.format_exc()}), encoding='utf-8')
            raise SystemExit(1)
        raise
