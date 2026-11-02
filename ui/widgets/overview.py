"""A lightweight overview matching the compact tool-card hierarchy."""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from core.models import TYPE_TOOL


class Overview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = (0, 0, 0)
        self.total = 0
        self.setFixedHeight(86)

    def set_entries(self, entries):
        tools = sum(e.entry_type == TYPE_TOOL for e in entries)
        self.values = (tools, len(entries) - tools, sum(e.pinned for e in entries))
        self.total = len(entries)
        self.update()

    def paintEvent(self, event):
        dark = QApplication.instance().property('superguiTheme') == 'dark'
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap = 12
        width = (self.width() - gap * 2) / 3
        for index, (label, value, accent) in enumerate(zip(
                ('工具', '资料与文件', '已固定'), self.values,
                ('#3cb56f', '#4798ee', '#f0b342'))):
            rect = QRectF(index * (width + gap), 1, width - 1, self.height() - 2)
            painter.setPen(QPen(QColor('#343941' if dark else '#e8ebee'), 1))
            painter.setBrush(QColor('#292d34' if dark else '#ffffff'))
            painter.drawRoundedRect(rect, 10, 10)
            font = QFont('Microsoft YaHei UI'); font.setPixelSize(12)
            painter.setFont(font); painter.setPen(QColor('#9aa3af' if dark else '#6b7380'))
            painter.drawText(rect.adjusted(16, 9, -12, -54), label)
            font.setPixelSize(25); font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font); painter.setPen(QColor('#e6e9ee' if dark else '#2b323d'))
            painter.drawText(rect.adjusted(16, 27, -12, -14), str(value))
            track = QRectF(rect.x() + 16, rect.bottom() - 12, rect.width() - 32, 3)
            painter.setPen(QPen(QColor('#343941' if dark else '#edf0f3'), 3))
            painter.drawLine(track.topLeft(), track.topRight())
            if value:
                painter.setPen(QPen(QColor(accent), 3))
                end = track.topLeft(); end.setX(end.x() + track.width() * value / max(1, self.total))
                painter.drawLine(track.topLeft(), end)
