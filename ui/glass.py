"""Cached material canvas. Only navigation chrome is translucent; content stays legible.

Hierarchy: search and navigation float above a continuous results surface.
Tokens: existing neutral/green palette; spacing 8/12/16/24; radii 12/18/20.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


class GlassCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GlassCanvas")
        self._key = None
        self._material = None

    def paintEvent(self, event):
        dark = QApplication.instance().property("superguiTheme") == "dark"
        native = bool(self.window().property("nativeBackdrop"))
        key = (self.width(), self.height(), self.devicePixelRatioF(), dark, native)
        if key != self._key:
            self._key = key
            dpr = self.devicePixelRatioF()
            material = QPixmap(round(self.width()*dpr), round(self.height()*dpr))
            material.setDevicePixelRatio(dpr)
            material.fill(Qt.GlobalColor.transparent)
            painter = QPainter(material)
            base = QColor("#1e2126" if dark else "#f4f6f8")
            base.setAlpha((200 if dark else 210) if native else 255)
            painter.fillRect(self.rect(), base)
            highlight = QLinearGradient(0, 0, self.width(), self.height())
            highlight.setColorAt(0, QColor(255, 255, 255, 14 if dark else 64))
            highlight.setColorAt(0.5, QColor(255, 255, 255, 0))
            highlight.setColorAt(1, QColor(60, 181, 111, 9 if dark else 12))
            painter.fillRect(self.rect(), highlight)
            painter.end()
            self._material = material
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._material)
