"""Small language/form pills shared by the entry list, cards and detail panel."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from core.entry_kind import label

BADGE_HEIGHT = 17
BADGE_FONT_PX = 10

# (light background, light text, dark background, dark text)
_COLORS = {
    "python": ("#e8f1fb", "#245c8f", (79, 140, 214, 46), "#8fc2ff"),
    "go": ("#e3f6f7", "#0b6a75", (48, 177, 190, 46), "#74e0e9"),
    "node": ("#e8f7ec", "#237a43", (92, 190, 125, 46), "#94e0ab"),
    "java": ("#fdf0e3", "#92561a", (222, 150, 72, 46), "#f2b979"),
    "rust": ("#faecea", "#8b4436", (206, 110, 86, 50), "#efa28f"),
    "dotnet": ("#eeeafb", "#574099", (150, 126, 230, 50), "#c3b3f7"),
    "php": ("#f0eefa", "#5a4a9e", (146, 128, 224, 50), "#c0b4f5"),
    "ruby": ("#fbe9ef", "#8f2f4c", (220, 100, 140, 46), "#f2a1ba"),
    "exe": ("#eef1f4", "#46545f", (160, 175, 190, 42), "#c7d0da"),
    "script": ("#fbf4df", "#7a621a", (210, 180, 80, 46), "#e8d48a"),
    "shortcut": ("#eef1f4", "#46545f", (160, 175, 190, 42), "#c7d0da"),
    "other": ("#edf0f4", "#54637a", (150, 165, 185, 40), "#b6c1cf"),
    "data": ("#f0f2f5", "#5b6673", (150, 160, 175, 36), "#aab4c0"),
}
_DEFAULT = _COLORS["data"]


def is_dark() -> bool:
    app = QApplication.instance()
    return bool(app and app.property("superguiTheme") == "dark")


def colors(kind: str, dark: bool) -> tuple[QColor, QColor]:
    light_bg, light_fg, dark_bg, dark_fg = _COLORS.get(kind, _DEFAULT)
    if dark:
        return QColor(*dark_bg), QColor(dark_fg)
    return QColor(light_bg), QColor(light_fg)


def badge_font() -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(BADGE_FONT_PX)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def badge_width(kind: str, font: QFont | None = None) -> int:
    metrics = QFontMetrics(font or badge_font())
    return metrics.horizontalAdvance(label(kind)) + 13


def _draw_pill(painter: QPainter, right: int, center_y: int, text: str,
               kind: str, dark: bool) -> int:
    font = badge_font()
    painter.setFont(font)
    width = QFontMetrics(font).horizontalAdvance(text) + 13
    rect = QRectF(right - width, center_y - BADGE_HEIGHT / 2, width, BADGE_HEIGHT)
    background, foreground = colors(kind, dark)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(background)
    painter.drawRoundedRect(rect, BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)
    painter.setPen(foreground)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()
    return width + 4


def draw_badge(painter: QPainter, right: int, center_y: int, kind: str, dark: bool) -> int:
    """Draw one pill right-aligned; return the consumed width including gap."""
    return _draw_pill(painter, right, center_y, label(kind), kind, dark)


def draw_count_badge(painter: QPainter, right: int, center_y: int,
                     count: int, dark: bool) -> int:
    """Neutral pill for a folder overview card, e.g. `12 项`."""
    return _draw_pill(painter, right, center_y, f"{count} 项", "other", dark)


def draw_badges(painter: QPainter, kinds, right: int, center_y: int,
                dark: bool, max_width: int = 240) -> int:
    """Draw up to two badges right-to-left; the primary kind stays leftmost.

    When both do not fit, keep the primary one instead of the secondary.
    """
    kinds = tuple(kinds)[:2]
    if not kinds:
        return 0
    widths = [badge_width(kind) for kind in kinds]
    if sum(widths) + 4 * (len(kinds) - 1) > max_width:
        kinds = kinds[:1]
    used = 0
    painter.save()
    for kind in reversed(kinds):
        used += draw_badge(painter, right - used, center_y, kind, dark)
    painter.restore()
    return used


class KindBadge(QWidget):
    """Detail-panel pill; repaints from the current app theme."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(badge_width(kind), BADGE_HEIGHT)
        self.setToolTip(f"语言 / 形态：{label(kind)}")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background, foreground = colors(self.kind, is_dark())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()), BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)
        painter.setFont(badge_font())
        painter.setPen(foreground)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, label(self.kind))
