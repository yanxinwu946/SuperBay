"""左侧导航：快捷区（全部/已固定/最近）+ 分类树 + 标签云 + 底部占用卡。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QButtonGroup,
    QToolButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.models import Category
from ui.flowlayout import FlowLayout
from ui.misc import ICON_COLOR, SVG_CLOCK, SVG_FOLDER, SVG_GRID, SVG_STAR, svg_icon

ICON_COLOR_ACTIVE = "#2f9d5d"

# 快捷区写死三项：(nav key, 显示文本, 图标)，数量在 rebuild 时传进来
QUICK_NAVS = (
    ("all", "全部", SVG_GRID),
    ("star", "固定", SVG_STAR),
    ("recent", "最近", SVG_CLOCK),
)

TAG_CLOUD_MAX_H = 120  # 标签云限高，避免挤占分类区


class _NavigationButton(QPushButton):
    """Separate wrapping title and count; neither can paint over the other."""
    def __init__(self, text: str, count: int, parent=None):
        super().__init__(parent)
        self.setAccessibleName(f"{text}，{count} 个条目")
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self._glyph = QLabel()
        self._glyph.setFixedSize(18, 18)
        self._name = QLabel(text)
        self._name.setObjectName("NavName")
        self._name.setWordWrap(True)
        self._name.setMinimumWidth(0)
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._count = QLabel(str(count))
        self._count.setObjectName("NavCount")
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count.setFixedWidth(max(24, self._count.fontMetrics().horizontalAdvance(str(count)) + 8))
        for widget in (self._glyph, self._name, self._count):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._name, 1)
        row.addWidget(self._count, 0)

    def setIcon(self, icon):
        self._glyph.setPixmap(icon.pixmap(16, 16))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(max(36, self.layout().totalHeightForWidth(self.width())))

    def sizeHint(self):
        return QSize(200, max(36, self.layout().totalHeightForWidth(max(160, self.width()))))


class SideBar(QWidget):
    navChanged = Signal(str)        # "all" | "star" | "recent" | cat_id
    tagChanged = Signal(object)     # str | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SideBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(232)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(10, 14, 10, 10)
        self._lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        self._tag_buttons: dict[str, QPushButton] = {}
        self._collapsed = set()
        self._category_rows = {}
        self._current_nav = "all"
        self._current_tag: str | None = None

    # ── 重建 ──
    def rebuild(self, categories: list[Category], total: int,
                star_count: int, recent_count: int, tags: list[str]) -> None:
        """全量重建。选中状态存在 _current_nav/_current_tag，重建完 _sync_state 恢复。"""
        self._clear_layout(self._lay)
        for btn in list(self._group.buttons()):  # 清掉上一轮按钮，复用同一个按钮组避免累积
            self._group.removeButton(btn)
        self._nav_buttons.clear()
        self._tag_buttons.clear()

        self._build_quick_nav(total, star_count, recent_count)
        self._build_category_tree(categories)
        if tags:
            self._build_tag_cloud(tags)

        self._sync_state()
        self._cat_scroll.verticalScrollBar().setValue(0)  # 重建后分类区滚回顶部

    def _build_quick_nav(self, total: int, star_count: int, recent_count: int) -> None:
        counts = {"all": total, "star": star_count, "recent": recent_count}
        for key, text, svg in QUICK_NAVS:
            self._lay.addWidget(self._nav_btn(key, text, counts[key], svg))

    def _build_category_tree(self, categories: list[Category]) -> None:
        """分类列表，放滚动区里，吃掉剩余空间。"""
        self._lay.addSpacing(10)
        scroll, box = self._make_scroll()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._category_rows = {}
        parents = {cat.parent_id for cat in categories if cat.parent_id}
        counts = {cat.cat_id: len(cat.entries) for cat in categories}
        for cat in reversed(categories):
            if cat.parent_id in counts:
                counts[cat.parent_id] += counts[cat.cat_id]
        for cat in categories:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            indent = min(cat.depth, 3)*12
            row_layout.setContentsMargins(indent, 0, 0, 0)
            row_layout.setSpacing(2)
            arrow = QToolButton()
            arrow.setObjectName("CategoryExpand")
            arrow.setFixedSize(16, 28)
            arrow.setAutoRaise(True)
            if cat.cat_id in parents:
                arrow.clicked.connect(lambda _=False, key=cat.cat_id: self._toggle_category(key))
                arrow.setToolTip("展开或收起子分类")
            else:
                arrow.setEnabled(False)
            row_layout.addWidget(arrow, 0, Qt.AlignmentFlag.AlignTop)
            button = self._nav_btn(cat.cat_id, cat.name, counts[cat.cat_id], SVG_FOLDER)
            button.setMaximumWidth(max(96, self.width()-20-18-indent-14))
            button.setToolTip(str(cat.path))
            row_layout.addWidget(button, 1)
            self._category_rows[cat.cat_id] = (row, cat, arrow, cat.cat_id in parents)
            lay.addWidget(row)
        self._update_category_tree()
        lay.addStretch(1)
        scroll.setWidget(box)
        self._lay.addWidget(scroll, 1)
        self._cat_scroll = scroll

    def _toggle_category(self, key):
        if key in self._collapsed:
            self._collapsed.remove(key)
        else:
            self._collapsed.add(key)
        self._update_category_tree()

    def _update_category_tree(self):
        for key, (row, category, arrow, expandable) in self._category_rows.items():
            row.setVisible(not any(key.startswith(parent + "/") for parent in self._collapsed))
            arrow.setArrowType((Qt.ArrowType.RightArrow if key in self._collapsed else Qt.ArrowType.DownArrow)
                               if expandable else Qt.ArrowType.NoArrow)

    def _build_tag_cloud(self, tags: list[str]) -> None:
        """标签云：流式布局 + 限高滚动。标签和导航互斥，高亮逻辑在 _sync_state。"""
        self._lay.addSpacing(6)
        self._lay.addWidget(self._section_title("标 签"))
        scroll, cloud = self._make_scroll(max_height=TAG_CLOUD_MAX_H)
        flow = FlowLayout(cloud, margin=2, h_spacing=6, v_spacing=6)
        for tag in tags:
            btn = QPushButton(tag)
            btn.setObjectName("TagBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, t=tag: self._on_tag(t))
            self._tag_buttons[tag] = btn
            flow.addWidget(btn)
        scroll.setWidget(cloud)
        self._lay.addWidget(scroll)

    # ── 小组件 ──
    @staticmethod
    def _make_scroll(max_height: int | None = None) -> tuple[QScrollArea, QWidget]:
        """侧边栏通用滚动区：无边框、禁横向、视口透明（不然深色主题下有白块）。"""
        scroll = QScrollArea()
        scroll.setObjectName("SideScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        if max_height is not None:
            scroll.setMaximumHeight(max_height)
        box = QWidget()
        box.setObjectName("SideContent")  # objectName 给 QSS 用
        return scroll, box

    @staticmethod
    def _clear_layout(layout) -> None:
        """递归清空布局。"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                SideBar._clear_layout(item.layout())

    def _nav_btn(self, key: str, text: str, count: int, svg: str) -> QPushButton:
        btn = _NavigationButton(text, count)
        btn.setObjectName("NavButton")
        btn.setCheckable(True)
        btn.setMinimumHeight(30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(svg_icon(svg, ICON_COLOR))
        btn.setProperty("navKey", key)
        btn.setProperty("svg", svg)  # svg 存属性里，换色时直接取
        btn.clicked.connect(lambda _=False, k=key: self._on_nav(k))
        self._group.addButton(btn)
        self._nav_buttons[key] = btn
        return btn

    @staticmethod
    def _section_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("NavTitle")
        return lbl

    # ── 选中状态 ──
    def _on_nav(self, key: str) -> None:
        self._current_nav = key
        self._current_tag = None  # 导航和标签互斥
        self._sync_state()
        self.navChanged.emit(key)

    def _on_tag(self, tag: str) -> None:
        # 同一个标签再点一次就是取消筛选
        self._current_tag = None if self._current_tag == tag else tag
        if self._current_tag is not None:
            self._current_nav = "all"  # 标签和导航互斥
        self._sync_state()
        self.tagChanged.emit(self._current_tag)

    def _sync_state(self) -> None:
        """把当前选中落到按钮勾选态和图标颜色上。"""
        for key, btn in self._nav_buttons.items():
            active = key == self._current_nav and self._current_tag is None
            btn.setChecked(active)
            color = ICON_COLOR_ACTIVE if active else ICON_COLOR
            btn.setIcon(svg_icon(btn.property("svg"), color))
        for tag, btn in self._tag_buttons.items():
            btn.setChecked(tag == self._current_tag)

    def set_current(self, key: str) -> None:
        """给主窗口强制切导航用，不发信号，调用方自己会重建。"""
        self._current_nav = key
        self._current_tag = None
        self._sync_state()
