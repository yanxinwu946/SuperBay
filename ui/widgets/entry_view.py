"""Virtualized entry browser: no QWidget per entry, icons resolved on paint.

Hierarchy (§2.1): tool name and launch action; location stays in the detail panel.
Spacing (§3.2): 8/12/16/24; radius 8; existing light/dark theme colors retained.
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextLayout, QTextCharFormat
from PySide6.QtCore import QPointF
import re
from PySide6.QtWidgets import QApplication, QListView, QStyle, QStyledItemDelegate, QAbstractItemView, QWidget

from core import entry_kind
from core.models import Entry
from ui.misc import WRAP_FLAGS as WRAP
from ui.misc import wrapped_text_height
from ui.widgets.kind_badge import draw_badges, draw_count_badge

def paint_badges(painter, entry, right, center_y, dark, max_width):
    painter.save()
    if entry.group:
        if entry.group_count:
            draw_count_badge(painter, right, center_y, entry.group_count, dark)
    else:
        draw_badges(painter, entry_kind.kinds_of(entry), right, center_y, dark, max_width)
    painter.restore()

def draw_name(painter, rect, text, query, dark, center=False):
    layout = QTextLayout(text, painter.font())
    formats = []
    for token in query.split():
        if ':' in token: continue
        for match in re.finditer(re.escape(token), text, re.IGNORECASE):
            span = QTextLayout.FormatRange()
            span.start, span.length = match.start(), match.end()-match.start()
            style = QTextCharFormat()
            style.setBackground(QColor('#346144' if dark else '#ccedd8'))
            style.setForeground(QColor('#b4f5c9' if dark else '#185c32'))
            style.setFontWeight(QFont.Weight.Bold)
            span.format = style
            formats.append(span)
    layout.setFormats(formats)
    layout.beginLayout()
    height = 0
    while True:
        line = layout.createLine()
        if not line.isValid(): break
        line.setLineWidth(rect.width())
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    y = rect.y() + (max(0, (rect.height()-height)/2) if center else 0)
    layout.draw(painter, QPointF(rect.x(), y))


ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
GRID_SIZE = QSize(168, 104)
LIST_SIZE = QSize(200, 48)
GRID_ICON_AREA = 50  # 图标区高度；名称从这里开始
GRID_BOTTOM_PAD = 10
BADGE_COLUMN_WIDTH = 92  # 列表视图右侧语言标签列宽（不含启动箭头）


def list_columns(rect):
    return (QRect(rect.x()+48, rect.y(),
                  max(1, rect.width()-88-BADGE_COLUMN_WIDTH), rect.height()),)


def badge_column(rect):
    return QRect(rect.right()-40-BADGE_COLUMN_WIDTH, rect.y(),
                 BADGE_COLUMN_WIDTH, rect.height())


class EntryHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)

    def paintEvent(self, event):
        dark = QApplication.instance().property("superguiTheme") == "dark"
        painter = QPainter(self)
        painter.setPen(QColor("#9aa3af" if dark else "#6b7380"))
        for rect, title in zip(list_columns(self.rect()), ("名称",)):
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, title)
        painter.drawText(badge_column(self.rect()).adjusted(6, 0, 0, 0),
                         Qt.AlignmentFlag.AlignVCenter, "语言")
        painter.setPen(QColor("#343941" if dark else "#e8ebee"))
        painter.drawLine(0, self.height()-1, self.width(), self.height()-1)


def launch_rect(rect):
    return QRect(rect.right()-32, rect.y()+14 if rect.height()>80 else rect.center().y()-12, 24, 24)


class EntryModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[Entry] = []

    def replace(self, entries):
        if len(entries) == len(self.entries) and all(a is b for a, b in zip(entries, self.entries)):
            return False
        self.beginResetModel()
        self.entries = list(entries)
        self.endResetModel()
        return True

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role == ENTRY_ROLE:
            return entry
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.name
        if role == Qt.ItemDataRole.ToolTipRole:
            versions = entry.runtime_profile.get("versions", {})
            runtime = " · ".join(f"{family} {ref.get('version', '')}" for family, ref in versions.items())
            kinds = " · ".join(entry_kind.label(kind) for kind in entry_kind.kinds_of(entry))
            return (entry.name + (f"\n语言 / 形态：{kinds}" if kinds else "")
                    + (f"\n固定环境：{runtime}" if runtime else ""))


class EntryDelegate(QStyledItemDelegate):
    def __init__(self, view, icon_for, dark):
        super().__init__(view)
        self.view, self.icon_for, self.dark = view, icon_for, dark

    def sizeHint(self, option, index):
        if self.view.grid:
            return self.view.grid_cell_size
        entry = index.data(ENTRY_ROLE)
        font = QFont(option.font)
        font.setWeight(QFont.Weight.Medium)
        text = (("★ " if entry.pinned else "") + entry.name) if entry else ""
        height = wrapped_text_height(text, self.view.viewport().width() - 88 - BADGE_COLUMN_WIDTH, font)
        return QSize(max(LIST_SIZE.width(), self.view.viewport().width()),
                     max(LIST_SIZE.height(), height + 16))

    def paint(self, painter: QPainter, option, index):
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return
        if not self.view.grid:
            self._paint_row(painter, option, entry)
            return
        dark = self.dark()
        bg = QColor("#2b2f36" if dark else "#ffffff")
        ink = QColor("#e6e9ee" if dark else "#2b323d")
        accent = QColor("#5fd08e" if dark else "#287c4b")
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(3, 3, -3, -3)
        painter.save()
        painter.setClipRect(option.rect, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = QColor("#3a3f47" if dark else "#e5e9ef")
        painter.setPen(QPen(accent if selected or hover else edge, 1))
        painter.setBrush(QColor("#2f5d43" if dark else "#e9f7ef") if selected else bg)
        painter.drawRoundedRect(rect, 8, 8)
        self.icon_for(entry).paint(painter, QRect(rect.x()+14, rect.y()+12, 28, 28))
        right = rect.right()-35  # keep the launch arrow area clear
        paint_badges(painter, entry, right, rect.y()+26, dark,
                     max(30, right-(rect.x()+50)))
        left, top = rect.x()+14, rect.y()+GRID_ICON_AREA
        width = rect.width()-28
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(ink)
        draw_name(painter, QRect(left, top, width, max(1, rect.height()-GRID_ICON_AREA-GRID_BOTTOM_PAD)),
                  entry.name, self.view.property("searchQuery") or "", dark)
        if hover or selected:
            action = launch_rect(option.rect)
            painter.setBrush(QColor("#2f5d43" if dark else "#e9f7ef"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(action, 6, 6)
            painter.setPen(accent)
            painter.drawText(action, Qt.AlignmentFlag.AlignCenter, "↗")
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)
        painter.restore()

    def _paint_row(self, painter, option, entry):
        dark = self.dark()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        # Leave space around the focus stroke, including at the viewport edge.
        rect = option.rect.adjusted(4, 3, -4, -3)
        painter.save()
        painter.setClipRect(option.rect, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            painter.setBrush(QColor("#2f5d43" if dark else "#e9f7ef") if selected
                             else QColor(255, 255, 255, 12 if dark else 140))
            painter.drawRoundedRect(rect, 10, 10)
        self.icon_for(entry).paint(painter, QRect(rect.x()+12, rect.center().y()-10, 20, 20))
        font = QFont(option.font)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        columns = list_columns(option.rect)
        texts = (("★ " if entry.pinned else "") + entry.name,)
        for column, text in zip(columns, texts):
            painter.setPen(QColor("#e6e9ee" if dark else "#2b323d") if column == columns[0]
                           else QColor("#9aa3af" if dark else "#6b7380"))
            draw_name(painter, column, text, self.view.property("searchQuery") or "", dark, center=True)
        paint_badges(painter, entry, rect.right()-36,
                     rect.center().y(), dark, BADGE_COLUMN_WIDTH)
        painter.setPen(QColor(154, 163, 175, 24))
        painter.drawLine(columns[0].left(), rect.bottom()+1, rect.right(), rect.bottom()+1)
        if hovered or selected:
            painter.setPen(QColor("#5fd08e" if dark else "#287c4b"))
            painter.drawText(launch_rect(option.rect), Qt.AlignmentFlag.AlignCenter, "↗")
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#5fd08e" if dark else "#287c4b"), 1))
            painter.drawRoundedRect(rect, 10, 10)
        painter.restore()


class EntryView(QListView):
    entrySelected = Signal(object)
    launchRequested = Signal(object)
    contextRequested = Signal(object, object)
    actionRequested = Signal(str, object)

    def __init__(self, icon_for, dark, parent=None):
        super().__init__(parent)
        self.grid = None
        self.grid_cell_size = QSize(GRID_SIZE)
        self.setObjectName("EntryView")
        self.entries_model = EntryModel(self)
        self.setModel(self.entries_model)
        self.setItemDelegate(EntryDelegate(self, icon_for, dark))
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setMouseTracking(True)
        self.setToolTip("双击或 Enter 启动 · 右侧箭头单击启动 · 右键更多操作")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.activated.connect(lambda i: self.launchRequested.emit(i.data(ENTRY_ROLE)))
        self.customContextMenuRequested.connect(self._context)
        self.set_grid(True)

    def set_grid(self, grid):
        if self.grid == grid:
            if grid:
                self._sync_grid_geometry()
            return
        self.grid = grid
        self.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.setMovement(QListView.Movement.Static)
        self.setWrapping(grid)
        # List rows grow with wrapped names; grid cells share one computed height.
        self.setUniformItemSizes(bool(grid))
        # Reserve the actual scrollbar width. AsNeeded makes QListView reserve
        # a style-metric width that can differ from the styled scrollbar.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if grid
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if grid:
            self._sync_grid_geometry()
        else:
            self.setGridSize(QSize())
        self.doItemsLayout()

    def _sync_grid_geometry(self):
        width = self.viewport().width()
        if width <= 0:
            return
        # QListView's wrap boundary is exclusive; reserve one pixel so an
        # exactly divisible viewport does not unexpectedly wrap the last cell.
        available = max(1, width-1)
        columns = max(1, available // GRID_SIZE.width())
        cell_width = available // columns
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        title_height = max((wrapped_text_height(entry.name, cell_width-28, font)
                            for entry in self.entries_model.entries),
                           default=font.pixelSize())
        # +8 covers the card's 3px top/bottom insets so the last line is never clipped.
        height = max(GRID_SIZE.height(), GRID_ICON_AREA + title_height + GRID_BOTTOM_PAD + 8)
        size = QSize(cell_width, height)
        if size != self.grid_cell_size or size != self.gridSize():
            self.grid_cell_size = size
            self.setGridSize(size)

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            if getattr(self, "grid", False):
                self._sync_grid_geometry()
            else:
                # List row heights depend on the viewport width; re-query
                # delegates after a resize instead of keeping stale heights.
                self.scheduleDelayedItemsLayout()
        return result

    def _context(self, pos):
        index = self.indexAt(pos)
        if index.isValid():
            self.contextRequested.emit(index.data(ENTRY_ROLE), self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event):
        entry = self.currentIndex().data(ENTRY_ROLE)
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        action = None
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            action = "admin" if ctrl else ("args" if shift else None)
        elif ctrl and shift and key == Qt.Key.Key_C:
            action = "copy"
        elif ctrl and shift and key == Qt.Key.Key_R:
            action = "runtime"
        elif ctrl and key == Qt.Key.Key_D:
            action = "pin"
        elif ctrl and key == Qt.Key.Key_E:
            action = "folder"
        elif key == Qt.Key.Key_F2:
            action = "command"
        if action and entry:
            self.actionRequested.emit(action, entry)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.currentIndex().isValid():
                self.launchRequested.emit(self.currentIndex().data(ENTRY_ROLE))
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and index.isValid()
                and launch_rect(self.visualRect(index)).contains(event.position().toPoint())):
            self.launchRequested.emit(index.data(ENTRY_ROLE))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        if current.isValid():
            self.entrySelected.emit(current.data(ENTRY_ROLE))
