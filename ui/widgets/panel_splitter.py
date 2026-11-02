"""Native panel resizing with a quiet grip and persistence on release."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QSplitter, QSplitterHandle


class _PanelHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("拖动调整宽度，双击恢复默认")

    def paintEvent(self, event):
        dark = QApplication.instance().property("superguiTheme") == "dark"
        color = QColor("#9aa3af" if dark else "#6b7380")
        color.setAlpha(180 if self.underMouse() else 80)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self.width()//2-1, self.height()//2-16, 2, 32, 1, 1)

    def event(self, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.update()
        return super().event(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.splitter().layoutCommitted.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.splitter().resetRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PanelSplitter(QSplitter):
    layoutCommitted = Signal()
    resetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("PanelSplitter")
        self.setHandleWidth(10)
        self.setChildrenCollapsible(False)
        self.setOpaqueResize(True)

    def createHandle(self):
        return _PanelHandle(self.orientation(), self)
