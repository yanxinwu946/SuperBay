"""Consistent list popups rather than platform menu-style combo popups."""
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QPainter, QFont, QFontMetrics
from PySide6.QtWidgets import (QApplication, QComboBox, QListView, QStyledItemDelegate,
                              QStyle, QAbstractItemView)


class _OptionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        metrics = QFontMetrics(self.parent().view().font())
        return QSize(max(180, metrics.horizontalAdvance(str(index.data() or '')) + 72), 36)

    def paint(self, painter, option, index):
        dark = QApplication.instance().property('superguiTheme') == 'dark'
        current = index.row() == self.parent().currentIndex()
        focused = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 2, -4, -2)
        material = self.parent().objectName() == 'WorkspaceSelector'
        if current or hovered or focused:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(('#2f5d43' if dark else '#e9f7ef') if current
                                    else ('#343941' if dark else '#f4f6f8')))
            if material:
                painter.setBrush(QColor(255,255,255,26 if dark else 210) if current
                                 else QColor(255,255,255,14 if dark else 120))
                if current:
                    painter.setPen(QColor(255,255,255,25) if dark else QColor(43,50,61,20))
            painter.drawRoundedRect(QRectF(rect), 8 if material else 6, 8 if material else 6)
        font = QFont(self.parent().view().font())
        if current:
            font.setWeight(QFont.Weight.Medium if material else QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor('#e6e9ee' if dark else '#2b323d'))
        text_rect = rect.adjusted(12, 0, -12, 0)
        text = QFontMetrics(font).elidedText(str(index.data() or ''), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        # A focused row has a quiet inset outline; selection uses fill and weight.
        if focused and self.parent().view().hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor('#9aa3af' if dark else '#6b7380'))
            painter.drawRoundedRect(QRectF(rect.adjusted(1, 1, -1, -1)), 5, 5)
        painter.restore()


class ModernComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        view.setFrameShape(QListView.Shape.NoFrame)
        view.setUniformItemSizes(True)
        view.setMouseTracking(True)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setView(view)
        self.setItemDelegate(_OptionDelegate(self))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(13)
        self.setFont(font)
        view.setFont(font)
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setMaxVisibleItems(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Disable Qt's platform menu popup, including its top/bottom scrollers.
        self.setStyleSheet('QComboBox { combobox-popup: 0; }')

    def showPopup(self):
        # The popup may be wider than its trigger. Measure actual option text,
        # plus symmetric padding, frame and scrollbar allowance.
        view = self.view()
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(13)
        view.setFont(font)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        text_width = max((metrics.horizontalAdvance(self.itemText(i))
                          for i in range(self.count())), default=0)
        wanted = max(self.width(), 180, text_width + 88)
        screen = self.screen().availableGeometry()
        view.setMinimumWidth(min(wanted, max(100, screen.width() - 24)))
        super().showPopup()
        popup = view.window()
        width = min(max(popup.width(), wanted), screen.width() - 24)
        popup.resize(width, popup.height())
        x = max(screen.left() + 12, min(popup.x(), screen.right() - width - 12))
        popup.move(x, popup.y())
