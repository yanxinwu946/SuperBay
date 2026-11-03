"""Directory change checks without persistent Windows directory handles."""
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal


class DirectoryWatcher(QObject):
    directoryChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = {}
        self._timer = QTimer(self, interval=1500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    @staticmethod
    def _stamp(path):
        try:
            return Path(path).stat().st_mtime_ns
        except OSError:
            return None

    def directories(self):
        return list(self._paths)

    def removePaths(self, paths):
        for path in paths:
            self._paths.pop(path, None)

    def addPaths(self, paths):
        for path in paths:
            self._paths[path] = self._stamp(path)

    def _poll(self):
        for path, previous in list(self._paths.items()):
            current = self._stamp(path)
            self._paths[path] = current
            if current != previous:
                self.directoryChanged.emit(path)
