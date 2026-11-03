"""主窗口：顶栏 + 侧边导航 + 卡片区 + 详情面板 + 状态栏 + 托盘。

widgets 只管发信号，数据怎么流（scanner → db 合并 → 重建 UI）
统一在 reload() 里处理。
"""
from __future__ import annotations

import ctypes
import datetime
import hashlib
import logging
import os
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QFileSystemWatcher, QObject, QPoint, QRect, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from ui.widgets.modern_combo import ModernComboBox as QComboBox


from core import launcher, notes, scanner
from core.runtime_profile import profile_environment
from core.search import SearchIndex
from ui.widgets.entry_view import EntryView, EntryHeader, ENTRY_ROLE
from ui.glass import GlassCanvas
from ui.widgets.panel_splitter import PanelSplitter
from core.models import Category, Entry
from core.version import __version__
from config.db import DB
from config.settings import Settings
from ui.misc import (
    ICON_COLOR,
    SVG_GRID,
    SVG_LIST,
    SVG_MOON,
    SVG_PANEL,
    SVG_REFRESH,
    SVG_SEARCH,
    SVG_SETTINGS,
    SVG_SUN,
    make_logo_icon,
    svg_icon,
)
from ui.widgets.detail import CommandDialog, DetailPanel
from ui.theme import apply_theme, resolve_theme
from ui.widgets.settings_dialog import SettingsDialog
from ui.widgets.workspace_sidebar import WorkspaceSideBar as SideBar

log = logging.getLogger(__name__)

WINDOW_TITLE = "SuperBay"
NOTE_CACHE_BYTES = 8192          # 笔记搜索缓存只取前 8KB，够命中又不拖慢扫描
NAV_TITLES = {"all": "全部", "star": "固定", "recent": "最近"}
DEFAULT_WINDOW_SIZE = {"w": 1440, "h": 860}
def _ipc_name() -> str:
    """单实例 IPC 名带上当前用户标识：命名管道是机器级的，多用户同机不能串台。"""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    return "supergui-ipc-" + hashlib.md5(user.encode("utf-8", "ignore")).hexdigest()[:8]


IPC_NAME = _ipc_name()           # 单实例唤起的本地 socket 名；main.py 的第二实例也连它

# ── 无边框窗口的原生边框缩放 ──
WM_NCHITTEST = 0x0084
RESIZE_MARGIN = 6   # 边框判定宽度（像素），视觉上不可见
# 命中码：左10 右11 上12 左上13 右上14 下15 左下16 右下17（Windows 固定值）
HT_LEFT, HT_RIGHT, HT_TOP, HT_BOTTOM = 10, 11, 12, 15
HT_TOPLEFT, HT_TOPRIGHT, HT_BOTTOMLEFT, HT_BOTTOMRIGHT = 13, 14, 16, 17


class MSG(ctypes.Structure):
    """Win32 MSG 结构，从原生事件指针还原消息。main.py 的热键过滤器也复用它。"""

    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class _ScanWorker(QObject):
    """后台扫描：目录遍历 + 笔记缓存读取，结果回主线程后再碰 DB / UI。

    系统图标必须在主线程创建（QPixmap 限制），所以不在这里做。
    """

    done = Signal(object, object)   # categories, note_cache
    failed = Signal(str)

    def __init__(self, workspace: Path, roots=None, max_depth=scanner.DEFAULT_SCAN_DEPTH):
        super().__init__()
        self._workspace = Path(workspace)
        self._roots = roots or [self._workspace]
        self._max_depth = max_depth

    def run(self) -> None:
        try:
            from core.toolbox import scan_roots
            categories = scan_roots(self._roots, max_depth=self._max_depth)
            entries = [e for c in categories for e in c.entries]
            note_cache = {
                str(e.path): notes.read_note_head(e.note_path, NOTE_CACHE_BYTES).lower()
                for e in entries if e.note_path
            }
        except Exception as e:
            log.exception("后台扫描失败")
            self.failed.emit(str(e))
            return
        self.done.emit(categories, note_cache)


class MainWindow(QMainWindow):
    def __init__(self, app_dir: Path, settings: Settings):
        super().__init__()
        self.setAcceptDrops(True)
        self.app_dir = Path(app_dir)
        self.settings = settings  # 全局唯一 Settings 实例，由 main.py 创建并共享
        from ui.native_frame import prepare_backdrop
        prepare_backdrop(self)
        if self.settings.get("layout_version", 0) < 2:
            self.settings.update({"view": "list", "layout_version": 2})
        self.db = DB(self.app_dir / "config" / "data.db")

        # ── 视图状态（reload/rebuild 都读这些） ──
        self.categories: list[Category] = []
        self.entries: list[Entry] = []
        self._search_index = SearchIndex()
        self.nav = "all"
        self._nav_back = []
        self._nav_forward = []
        self.tag: str | None = None
        self.query = ""
        self.kind = "all"
        self.sort_order = "smart"
        self.view = self.settings.get("view", "grid")
        self.selected: Entry | None = None
        self._icons: dict[str, QIcon] = {}       # path → 系统图标，reload 时统一取
        self._note_cache: dict[str, str] = {}    # path → 笔记小写缓存，搜索用
        self._tray_quit = False                  # 托盘点了「退出」才置位，closeEvent 靠它放行
        self._reloading = False                  # reload 重入守卫，扫描中再按 F5 先记为 pending
        self._scans: list[tuple[QThread, _ScanWorker]] = []  # 持引用到线程结束，防 GC
        self._scan_workspace: Path | None = None  # 本次扫描对应的根目录（切工作区时判过期）
        self._reload_pending = False             # 扫描期间的请求，完成后补一次
        self._restore_path: str | None = None     # 自动重扫前记下选中项，扫完恢复

        self.workspace = self._resolve_workspace()

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(make_logo_icon())
        self.setWindowFlags(Qt.WindowType.Window)
        win = self.settings.get("window", DEFAULT_WINDOW_SIZE)
        self.resize(win.get("w", DEFAULT_WINDOW_SIZE["w"]),
                    win.get("h", DEFAULT_WINDOW_SIZE["h"]))
        self.setMinimumSize(1100, 680)
        self._restore_position(win)
        self._start_maximized = bool(win.get("maximized", False))  # 首次 show 时恢复

        self._build_topbar()
        self._build_central()
        self._build_statusbar()
        self._build_tray()
        self._build_shortcuts()
        self._launch_states = {}
        self._launch_timer = QTimer(self, interval=1500)
        self._launch_timer.timeout.connect(self._poll_launches)
        self._launch_timer.start()
        self._applied_theme = resolve_theme(self.settings.get("theme", "system"))
        self._theme_timer = QTimer(self, interval=2000)
        self._theme_timer.timeout.connect(self._sync_system_theme)
        self._theme_timer.start()
        self._build_ipc()
        self._build_watcher()

        QTimer.singleShot(0, self.reload)  # 先让窗口出来，扫描等事件循环跑起来再做

    def _restore_position(self, win: dict) -> None:
        """恢复上次窗口位置；显示器变化导致越界时忽略，交给系统摆放。"""
        x, y = win.get("x"), win.get("y")
        if x is None or y is None:
            return
        rect = QRect(int(x), int(y), self.width(), self.height())
        if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
            self.move(int(x), int(y))

    def _resolve_workspace(self) -> Path:
        """探测工作区根目录，探测不到就弹框问；用户取消就直接退出。"""
        root = self.settings.detect_workspace()
        if not root:
            root = QFileDialog.getExistingDirectory(
                self, "选择工具目录（分类文件夹所在位置）")
            if not root:
                QMessageBox.critical(self, "无法启动", "未选择工作区目录")
                raise SystemExit(2)
            self.settings.set("workspace_root", root)
        return Path(root)

    # ── UI 搭建 ──
    def _build_topbar(self) -> None:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)

        brand = QLabel("SuperBay")
        brand.setObjectName("Brand")
        lay.addWidget(brand)

        self._workspace_selector = QComboBox()
        self._workspace_selector.setObjectName("WorkspaceSelector")
        self._workspace_selector.setFixedHeight(36)
        self._workspace_selector.setMinimumWidth(144)
        self._workspace_selector.setMaximumWidth(420)
        self._workspace_selector.setToolTip("目录快捷入口 · 一次打开一个目录")
        self._workspace_selector.currentIndexChanged.connect(self._switch_workspace_by_index)
        lay.addWidget(self._workspace_selector)
        self._refresh_workspace_buttons()

        runtime_btn = QPushButton("运行环境")
        runtime_btn.setObjectName("WorkspaceBtn")
        runtime_btn.setToolTip("管理 Java、Python、Node 版本")
        runtime_btn.clicked.connect(self._show_runtimes)
        lay.addWidget(runtime_btn)
        manage_btn = QPushButton("工具管理")
        manage_btn.setObjectName("WorkspaceBtn")
        manage_btn.clicked.connect(self._show_tool_center)
        lay.addWidget(manage_btn)

        lay.addStretch(1)
        lay.addWidget(self._make_search_box(), 1)
        lay.addStretch(1)

        self._grid_btn = self._top_icon_btn(SVG_GRID, "网格视图", checkable=True)
        self._list_btn = self._top_icon_btn(SVG_LIST, "列表视图", checkable=True)
        self._grid_btn.setChecked(self.view == "grid")
        self._list_btn.setChecked(self.view == "list")
        self._grid_btn.clicked.connect(lambda: self._set_view("grid"))
        self._list_btn.clicked.connect(lambda: self._set_view("list"))
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self._grid_btn)
        self._view_group.addButton(self._list_btn)
        lay.addWidget(self._grid_btn)
        lay.addWidget(self._list_btn)

        cur_theme = resolve_theme(self.settings.get("theme", "system"))
        self._theme_btn = self._top_icon_btn(
            SVG_SUN if cur_theme == "dark" else SVG_MOON, "外观主题")
        self._theme_btn.clicked.connect(self._show_theme_menu)

        self._panel_btn = self._top_icon_btn(SVG_PANEL, "折叠/展开详情面板 (Ctrl+B)",
                                             checkable=True)
        self._panel_btn.setChecked(self.settings.get("detail_visible", False))
        self._panel_btn.clicked.connect(self._toggle_panel)
        lay.addWidget(self._panel_btn)

        organize = QPushButton("整理工具")
        organize.setObjectName("GhostBtn")
        organize.clicked.connect(self._manage_files)
        lay.insertWidget(4, organize)

        refresh_btn = self._top_icon_btn(SVG_REFRESH, "重新扫描 (F5)")
        refresh_btn.clicked.connect(self.reload)
        lay.addWidget(refresh_btn)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFixedHeight(20)
        separator.setObjectName('ToolbarDivider')
        lay.addWidget(separator)
        lay.addWidget(self._theme_btn)

        set_btn = self._top_icon_btn(SVG_SETTINGS, "设置")
        set_btn.clicked.connect(self._show_settings)
        lay.addWidget(set_btn)

        self._topbar = bar  # 这个在 _build_central 里加到 central 顶部

    def _make_search_box(self) -> QWidget:
        """搜索框：150ms 防抖，停手了才触发重建。"""
        wrap = QWidget()
        wrap.setMinimumWidth(240)
        wrap.setMaximumWidth(430)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setObjectName("SearchBar")
        self._search.setPlaceholderText("搜索   Ctrl+K")
        self._search.setToolTip("支持拼音、别名、标签:扫描、类型:文件；Enter 启动，↓ 选择。")
        self._search.setClearButtonEnabled(True)
        self._search.installEventFilter(self)
        self._search.addAction(svg_icon(SVG_SEARCH, ICON_COLOR),
                               QLineEdit.ActionPosition.LeadingPosition)
        self._search_timer = QTimer(self, singleShot=True, interval=80)
        self._search_timer.timeout.connect(self._apply_query)
        self._search.textChanged.connect(lambda _: self._search_timer.start())
        lay.addWidget(self._search)
        return wrap

    def _top_icon_btn(self, svg: str, tooltip: str, checkable: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("TopIconBtn")
        btn.setProperty("actionIcon", True)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setIcon(svg_icon(svg, ICON_COLOR))
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(34, 34)
        return btn

    def _build_central(self) -> None:
        central = GlassCanvas()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 4)
        root.setSpacing(8)
        root.addWidget(self._topbar)

        body = PanelSplitter()
        self._splitter = body

        self._sidebar = SideBar()
        self._sidebar.navChanged.connect(self._on_nav)
        self._sidebar.tagChanged.connect(self._on_tag)
        body.addWidget(self._sidebar)

        self._content_panel = self._make_content()
        self._content_panel.setMinimumWidth(420)
        body.addWidget(self._content_panel)

        self._detail = DetailPanel()
        self._detail.setMinimumWidth(280)
        self._detail.setMaximumWidth(640)
        self._detail.setVisible(self._panel_btn.isChecked())
        self._detail.launchRequested.connect(lambda e: self._launch(e, False))
        self._detail.openDirRequested.connect(lambda e: self._open_entry_folder(e))
        self._detail.aliasesRequested.connect(self._edit_aliases)
        self._detail.planRunRequested.connect(self._run_launch_plan)
        self._detail.plansRequested.connect(self._launch_plans)
        self._detail.filesRequested.connect(self._browse_files)
        self._detail.pinToggled.connect(self._toggle_pin)
        self._detail.commandEdited.connect(self._edit_command)
        self._detail.tagsEdited.connect(self._edit_tags)
        self._detail.noteSaved.connect(self._on_note_saved)
        self._detail.runtimeRequested.connect(self._edit_runtime_profile)
        self._detail.detectRequested.connect(self._detect_entry)
        self._detail.shortcutRequested.connect(self._create_desktop_shortcut)
        body.addWidget(self._detail)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        widths = self.settings.get("panel_widths", {})
        self._preferred_detail_width = max(280, min(640, int(widths.get("detail", 332))))
        body.layoutCommitted.connect(self._save_panel_widths)
        body.resetRequested.connect(self._reset_panel_widths)
        root.addWidget(body, 1)
        QTimer.singleShot(0, lambda: self._restore_panel_widths(initial=True))
        self.setCentralWidget(central)
        self._apply_action_icons()
        from ui.widgets.action_glow import ActionGlow
        for button in self.findChildren(QPushButton):
            if button.property('actionIcon'):
                button._action_glow = ActionGlow(button)

    def _apply_action_icons(self):
        paths = {
            '←': 'M13 8H3m4-4L3 8l4 4',
            '→': 'M3 8h10M9 4l4 4-4 4',
            '↑': 'M8 13V3M4 7l4-4 4 4',
            '复制路径': 'M6 5V2h7v9h-3M3 5h7v9H3Z',
            '快速启动': 'M9 1 3 9h4l-1 6 7-9H9Z',
            '运行环境': 'M2 3h12v10H2ZM4 6l2 2-2 2m4 0h3',
            '工具管理': 'M2 5h12v9H2ZM5 5V2h6v3M2 8h12M7 8v2h2V8',
            '整理工具': 'M2 4h5l2 2h5v8H2ZM5 10h6m-3-2v4',
            '清空最近': 'M3 4h10M6 4V2h4v2M4 4l1 10h6l1-10M7 7v4m2-4v4',
            '清除筛选': 'M2 3h12l-5 6v4l-2 1V9ZM10 10l4 4m0-4-4 4',
        }
        for button in self.findChildren(QPushButton):
            label = button.text()
            path = paths.get(label)
            if path is None: continue
            svg = '<svg viewBox="0 0 16 16" fill="none"><path d="' + path + '" stroke="{c}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>'
            button.setIcon(svg_icon(svg, ICON_COLOR))
            button.setAccessibleName(button.toolTip() or label)
            if not button.toolTip(): button.setToolTip(label)
            button.setText('')
            button.setObjectName('ActionIconButton')
            button.setProperty('actionIcon', True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setAutoDefault(False)
            button.setFixedSize(34, 34)



    def _make_content(self) -> QFrame:
        """中间内容区：标题行 + 统计卡片 + 卡片滚动区。"""
        content = QFrame()
        content.setObjectName("Content")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(10)

        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self._back_btn = QPushButton('←')
        self._forward_btn = QPushButton('→')
        self._up_btn = QPushButton('↑')
        for button, tip, slot in ((self._back_btn, '后退 · Alt+Left', lambda: self._history_move(False)),
                                  (self._forward_btn, '前进 · Alt+Right', lambda: self._history_move(True)),
                                  (self._up_btn, '上一级 · Alt+Up', self._navigate_up)):
            button.setObjectName('GhostBtn')
            button.setFixedSize(28, 28)
            button.setFlat(True)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            navigation.addWidget(button)
        self._breadcrumbs = QLabel()
        self._breadcrumbs.setObjectName('ContentSub')
        self._breadcrumbs.setWordWrap(False)
        self._breadcrumbs.setMinimumWidth(0)
        self._breadcrumbs.setTextFormat(Qt.TextFormat.PlainText)
        self._breadcrumbs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        navigation.addWidget(self._breadcrumbs, 1)
        self._copy_location = QPushButton('复制路径')
        self._copy_location.setObjectName('MiniBtn')
        self._copy_location.setFixedHeight(28)
        self._copy_location.clicked.connect(self._copy_current_location)
        navigation.addWidget(self._copy_location)
        lay.addLayout(navigation)
        QShortcut(QKeySequence('Alt+Left'), self, activated=lambda: self._history_move(False))
        QShortcut(QKeySequence('Alt+Right'), self, activated=lambda: self._history_move(True))
        QShortcut(QKeySequence('Alt+Up'), self, activated=self._navigate_up)
        header = QHBoxLayout()
        self._title = QLabel("全部")
        self._title.setObjectName("ContentTitle")
        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("ContentSub")
        header.addWidget(self._title)
        header.addWidget(self._count_lbl)
        header.addStretch(1)
        quick = QPushButton("快速启动")
        self._quick_start = quick
        quick.setObjectName("GhostBtn")
        quick.clicked.connect(self._open_palette)
        # §2.1/§3.6: one compact toolbar reserves space for results.
        filters = header
        self._kind_filter = QComboBox()
        for label, value in (("全部类型", "all"), ("工具", "tool"), ("资料目录", "dir"), ("文件", "file")):
            self._kind_filter.addItem(label, value)
        self._kind_filter.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self._kind_filter)
        self._sort_filter = QComboBox()
        for label, value in (("智能排序", "smart"), ("名称 A → Z", "name"), ("最常使用", "runs"), ("最近启动", "recent"), ("名称 Z → A", "name_desc"), ("使用次数最少", "runs_asc"), ("类型", "type"), ("最近修改", "modified"), ("最早修改", "modified_asc"), ("文件最大", "size"), ("文件最小", "size_asc")):
            self._sort_filter.addItem(label, value)
        self._sort_filter.setCurrentIndex(max(0, self._sort_filter.findData(self.settings.get("sort_order", "smart"))))
        self.sort_order = self._sort_filter.currentData()
        self._sort_filter.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self._sort_filter)
        filters.addWidget(quick)
        self._clear_recent_btn = QPushButton("清空最近")
        self._clear_recent_btn.setObjectName("GhostBtn")
        self._clear_recent_btn.clicked.connect(self._clear_recent)
        self._clear_recent_btn.hide()
        lay.addLayout(header)
        scope_row = QHBoxLayout()
        self._scope_hint = QLabel()
        self._scope_hint.setObjectName("ContentSub")
        self._scope_hint.setWordWrap(True)
        self._scope_hint.hide()
        self._recursive_scope = QCheckBox("包含子目录")
        self._recursive_scope.setToolTip("关闭时只显示、搜索当前目录的直接内容")
        self._recursive_scope.toggled.connect(lambda _: self._rebuild_grid())
        header.insertWidget(3, self._recursive_scope)
        self._filter_reset = QPushButton("清除筛选")
        self._filter_reset.setObjectName("MiniBtn")
        self._filter_reset.clicked.connect(self._reset_filters)
        self._filter_reset.hide()
        cleanup = QHBoxLayout()
        cleanup.addWidget(self._filter_reset)
        cleanup.addWidget(self._clear_recent_btn)
        cleanup.addStretch(1)
        lay.addLayout(cleanup)

        from ui.widgets.overview import Overview
        self._overview = Overview()
        lay.addWidget(self._overview)

        self._list_header = EntryHeader()
        lay.addWidget(self._list_header)
        self._browser = EntryView(
            self._icon_for,
            lambda: QApplication.instance().property("superguiTheme") == "dark")
        self._browser.entrySelected.connect(self._on_select)
        self._browser.launchRequested.connect(lambda e: self._launch(e, False))
        self._browser.contextRequested.connect(self._show_context)
        self._browser.actionRequested.connect(self._entry_action)
        lay.addWidget(self._browser, 1)
        self._empty_tip = QLabel()
        self._empty_tip.setObjectName("EmptyTip")
        self._empty_tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_tip.setWordWrap(True)
        lay.addWidget(self._empty_tip, 1)
        content.installEventFilter(self)
        return content

    def _build_statusbar(self) -> None:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(24)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(16)

        self._status_ok = QLabel("● 就绪")
        self._status_ok.setObjectName("StatusOk")
        self._status_path = QLabel(str(self.workspace))
        self._status_path.setObjectName("StatusText")
        self._status_path.setToolTip(str(self.workspace))
        lay.addWidget(self._status_ok)
        lay.addWidget(self._status_path)
        lay.addStretch(1)

        self.centralWidget().layout().addWidget(bar)  # 加到 central 底部

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(make_logo_icon(64))
        self._tray.setToolTip(f"SuperBay v{__version__}")
        menu = QMenu()
        menu.addAction("显示主界面", self._show_from_tray)
        menu.addAction("重新扫描", self.reload)
        menu.addSeparator()
        menu.addAction("退出", self._quit_from_tray)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self, activated=self._reset_filters)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_panel)
        QShortcut(QKeySequence("F5"), self, activated=self.reload)
        QShortcut(QKeySequence("Escape"), self, activated=self._clear_query)

    # ── 单实例唤起 & 目录监听 ──
    def _build_ipc(self) -> None:
        """单实例唤起通道：第二实例连进来 = 请求唤起主窗口。"""
        self._ipc = QLocalServer(self)
        self._ipc.newConnection.connect(self._on_ipc)
        if not self._ipc.listen(IPC_NAME):
            # 上次异常退出可能残留 socket 文件，清掉重听
            QLocalServer.removeServer(IPC_NAME)
            self._ipc.listen(IPC_NAME)

    def _on_ipc(self) -> None:
        """第二实例启动 = 用户明确要用：确保窗口显示并聚焦搜索框（不是 toggle，热键才是 toggle）。"""
        while self._ipc.hasPendingConnections():
            conn = self._ipc.nextPendingConnection()
            self._show_from_tray()
            self._focus_search()
            conn.disconnectFromServer()
            conn.deleteLater()

    def _build_watcher(self) -> None:
        """工作区目录监听：条目增删/改名后去抖 1s 自动重扫，免得解压工具包时刷爆。"""
        from core.directory_watcher import DirectoryWatcher
        self._watcher = DirectoryWatcher(self)
        self._watch_timer = QTimer(self, singleShot=True, interval=1000)
        self._watch_timer.timeout.connect(self._auto_reload)
        self._watcher.directoryChanged.connect(lambda _p: self._watch_timer.start())

    def _refresh_watcher(self) -> None:
        """重铺监听路径：根目录 + 当前所有分类目录（条目目录不监听，写笔记不会触发）。"""
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        from core.toolbox import workspace_roots
        paths = workspace_roots(self.settings, self.workspace) + [c.path for c in self.categories]
        self._watcher.addPaths(list(dict.fromkeys(str(p) for p in paths if p.is_dir())))

    def _auto_reload(self) -> None:
        """目录变化触发的自动重扫：编辑笔记时跳过；重扫前记下选中项，扫完恢复。"""
        if self._detail.has_unsaved_note():
            self._set_status("有未保存的笔记，已跳过自动重扫", ok=False)
            return
        if self.selected:
            self._restore_path = str(self.selected.path)
        self.reload()

    # ── 数据流（扫描 → 合并 db → 重建） ──
    def reload(self) -> None:
        """后台重扫工作区，完成后合并 db 元数据并重建 UI（不阻塞主线程）。"""
        if getattr(self, "_closing", False):
            return
        if self._reloading:
            self._reload_pending = True   # 扫描期间的请求，完成后补一次
            return
        if self._detail.has_unsaved_note() and not self._detail.confirm_leave():
            return
        self._reloading = True
        self._scan_workspace = self.workspace
        self._set_status("扫描中…", ok=False)

        from core.toolbox import workspace_roots
        self._scanning_roots = tuple(str(p) for p in workspace_roots(self.settings, self.workspace))
        self._scanning_depth = scanner.normalize_scan_depth(self.settings.get("scan_depth", 2))
        worker = _ScanWorker(self.workspace, [Path(p) for p in self._scanning_roots], self._scanning_depth)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_scan_done)
        worker.failed.connect(self._on_scan_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda t=thread: self._drop_scan(t))
        self._scans.append((thread, worker))
        thread.start()

    def _drop_scan(self, thread: QThread) -> None:
        self._scans = [pair for pair in self._scans if pair[0] is not thread]

    def _on_scan_done(self, categories: list[Category], note_cache: dict[str, str]) -> None:
        if getattr(self, "_closing", False):
            return
        self._reloading = False
        if self._scan_workspace is not None \
                and not self._same_path(self._scan_workspace, self.workspace):
            # 扫描期间切了工作区：丢弃这次结果，按新工作区重扫
            self._scan_workspace = None
            self._reload_pending = False
            self.reload()
            return
        if getattr(self, "_scanning_depth", 2) != scanner.normalize_scan_depth(self.settings.get("scan_depth", 2)):
            self._reload_pending = False
            self.reload()
            return
        from core.toolbox import workspace_roots
        if getattr(self, "_scanning_roots", ()) != tuple(str(p) for p in workspace_roots(self.settings, self.workspace)):
            self.reload()
            return
        self._scan_workspace = None
        self.categories = categories
        self.entries = [e for c in categories for e in c.entries]
        self._note_cache = note_cache
        self._merge_db_meta()
        self._search_index.reset(self.entries, self._note_cache)
        # Keep missing-tool metadata available to health checks and relocation.
        self._build_icons()

        if self._detail.confirm_leave():
            self._detail.set_entry(None, None, {})
            self.selected = None
        else:
            # 扫描期间用户开始编辑笔记且选择继续编辑：保留选中项与编辑内容（卡片
            # 高亮按路径匹配不受影响）；pending 不再立即补扫，避免连环弹窗
            self._restore_path = None
            self._reload_pending = False

        if self.nav not in ("all", "star", "recent") and not any(c.cat_id == self.nav for c in categories):
            self.nav = "all"
            self._sidebar.set_current("all")
        self._rebuild_sidebar()
        self._rebuild_grid()
        self._set_status("就绪")
        from core.toolbox import workspace_roots
        roots = workspace_roots(self.settings, self.workspace)
        status_path = str(self.workspace) if len(roots) == 1 else f"{len(roots)} 个目录"
        self._status_path.setText(status_path)
        self._status_path.setToolTip(status_path)
        self._refresh_watcher()

        keep = self._restore_path
        self._restore_path = None
        if keep:
            entry = next((e for e in self.entries if str(e.path) == keep), None)
            if entry:
                self._on_select(entry)

        if self._reload_pending:
            self._reload_pending = False
            self.reload()

    def _on_scan_failed(self, msg: str) -> None:
        if getattr(self, "_closing", False):
            return
        self._reloading = False
        self._scan_workspace = None
        log.error("重新扫描失败: %s", msg)
        self._set_status(f"扫描失败: {msg}", ok=False)
        if self._reload_pending:
            self._reload_pending = False
            QTimer.singleShot(0, self.reload)

    def _merge_db_meta(self) -> None:
        """把 db 里的元数据（固定/标签/自定义命令/参数记忆/统计）按路径合进扫描结果。"""
        self._directory_entry_cache = {}
        metas = self.db.all_meta()
        stats = self.db.all_stats()
        for e in self.entries:
            key = str(e.path)
            if m := metas.get(key):
                e.pinned, e.tags = m["pinned"], m["tags"]
                e.command, e.shell_type = m["command"], m["shell_type"]
                e.last_args, e.admin = m["last_args"], m["admin"]
                e.runtime_profile = m["runtime_profile"]
                e.aliases, e.launch_plans = m["aliases"], m["launch_plans"]
            if s := stats.get(key):
                e.runs, e.last_run = s["runs"], s["last_run"]
                e.recent_hidden = s.get("recent_hidden", False)

    def _prune_stale_meta(self) -> None:
        """清理已消失条目的元数据（仅在当前工作区内、父目录仍在时删）。"""
        try:
            removed = self.db.prune(self.workspace, {str(e.path) for e in self.entries})
        except Exception:
            log.exception("清理失效元数据失败")
            return
        if removed:
            log.info("已清理 %d 条失效元数据", removed)

    def _build_icons(self) -> None:
        """系统图标缓存：沿用上一轮的，只给新条目取图标（省 shell 查询）。"""
        valid = {str(e.path) for e in self.entries}
        self._icons = {p: icon for p, icon in self._icons.items() if p in valid}
        self._icon_provider = QFileIconProvider()
        self._icon_provider.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)

    def _icon_for(self, entry: Entry) -> QIcon:
        key = str(entry.path)
        if key not in self._icons:
            provider = getattr(self, "_icon_provider", None)
            self._icons[key] = (provider.icon(QFileInfo(key)) if provider else
                               self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        return self._icons[key]

    def _rebuild_sidebar(self) -> None:
        entries = self.entries
        cats = [c for c in self.categories if c.entries or any(e.cat_id.startswith(c.cat_id + "/") for e in entries)]
        self._sidebar.rebuild(
            cats, len(entries),
            star_count=sum(1 for e in entries if e.pinned),
            recent_count=sum(1 for e in entries if e.runs > 0 and not e.recent_hidden),
            tags=sorted({t for e in entries for t in e.tags})
                 if self.settings.get("show_tags", False) else [])
        # A long title must wrap inside the sidebar, including scrollbar space.
        available = self._sidebar.width() - 20 - self._sidebar._cat_scroll.verticalScrollBar().sizeHint().width()
        for key, button in self._sidebar._nav_buttons.items():
            if key not in ("all", "star", "recent"):
                category = next((c for c in cats if c.cat_id == key), None)
                indent = min(category.depth, 3)*12 if category else 0
                button.setMaximumWidth(max(96, available-18-indent))


    def _filtered(self) -> list[Entry]:
        """当前视图状态（nav/tag/query）下的条目列表。

        常规视图已固定优先、按名称排序；「最近使用」按时间倒序
        （固定项不特别置顶，否则就失去「最近」语义）。
        """
        items = self.entries
        if self.nav == "star":
            items = [e for e in items if e.pinned]
        elif self.nav == "recent":
            items = [e for e in items if e.runs > 0 and not e.recent_hidden]
        elif self.nav != "all":
            recursive = self._recursive_scope.isChecked()
            items = [e for e in items if e.cat_id == self.nav or (recursive and e.cat_id.startswith(self.nav + "/"))]
            if not recursive:
                for category in self.categories:
                    if category.parent_id == self.nav:
                        cache = getattr(self, '_directory_entry_cache', None)
                        if cache is None: self._directory_entry_cache = cache = {}
                        folder = cache.get(category.path)
                        if folder is None:
                            folder = Entry(category.path.name, category.path, self.nav, category.name, True, entry_type="dir")
                            folder.desc = "子目录 · 打开查看此目录内容"
                            cache[category.path] = folder
                        items.append(folder)
        if self.tag:
            items = [e for e in items if self.tag in e.tags]
        if self.kind != "all":
            items = [e for e in items if e.entry_type == self.kind]
        self._search_index.ensure(self.entries, self._note_cache)
        for entry in items:
            if id(entry) not in self._search_index.records: self._search_index.update(entry)
        ranks = {}
        if self.query:
            self._search_index.ensure(self.entries, self._note_cache)
            ranked = self._search_index.ranked(self.query, items)
            ranks = {id(entry): rank for entry, rank in ranked}
            items = [entry for entry, _ in ranked]
        if self.sort_order == "recent" or (self.sort_order == "smart" and self.nav == "recent" and not self.query):
            return sorted(items, key=lambda e: (e.last_run, e.name.casefold()), reverse=True)
        if self.sort_order == "runs":
            return sorted(items, key=lambda e: (-e.runs, e.name.casefold()))
        if self.sort_order == "name_desc":
            return sorted(items, key=lambda e: e.name.casefold(), reverse=True)
        if self.sort_order == "runs_asc":
            return sorted(items, key=lambda e: (e.runs, e.name.casefold()))
        if self.sort_order == "type":
            return sorted(items, key=lambda e: (not e.is_dir, e.path.suffix.casefold(), e.name.casefold()))
        if self.sort_order in {"modified", "modified_asc", "size", "size_asc"}:
            def file_value(entry):
                try:
                    info = entry.path.stat()
                    value = info.st_mtime_ns if self.sort_order.startswith('modified') else (0 if entry.is_dir else info.st_size)
                except OSError:
                    value = 0
                return value
            ordered = sorted(items, key=lambda e: e.name.casefold())
            return sorted(ordered, key=file_value, reverse=not self.sort_order.endswith('_asc'))
        if self.sort_order == "name":
            return sorted(items, key=lambda e: e.name.casefold())
        return sorted(items, key=lambda e: (-ranks.get(id(e), 0), not e.pinned, -e.runs, e.name.casefold()))

    def _filter_changed(self) -> None:
        self.kind = self._kind_filter.currentData()
        self.sort_order = self._sort_filter.currentData()
        self.settings.set("sort_order", self.sort_order)
        self._rebuild_grid()

    def _reset_filters(self) -> None:
        self._search_timer.stop()
        self.nav, self.tag, self.query, self.kind = "all", None, "", "all"
        with QSignalBlocker(self._search), QSignalBlocker(self._kind_filter):
            self._search.clear()
            self._kind_filter.setCurrentIndex(0)
        self._sidebar.set_current("all")
        self._rebuild_grid()
        self._focus_search()

    def _entry_action(self, action: str, entry: Entry) -> None:
        actions = {
            "admin": lambda: self._launch(entry, True),
            "args": lambda: self._launch_with_args(entry),
            "pin": lambda: self._toggle_pin(entry, not entry.pinned),
            "copy": lambda: QGuiApplication.clipboard().setText(str(entry.path)),
            "folder": lambda: self._open_entry_folder(entry),
            "command": lambda: self._popup_command(entry),
            "runtime": lambda: self._edit_runtime_profile(entry),
            "plans": lambda: self._launch_plans(entry),
            "files": lambda: self._browse_files(entry),
        }
        if action in actions:
            actions[action]()

    def _edit_runtime_profile(self, entry: Entry) -> None:
        from ui.widgets.runtime_profile import RuntimeProfileDialog
        dialog = RuntimeProfileDialog(entry, self.settings, self)
        if dialog.exec():
            profile = dialog.profile()
            self.db.set_runtime_profile(entry.path, profile)
            entry.runtime_profile = profile
            if self.selected and self.selected.path == entry.path:
                self._on_select(entry)
            self._browser.viewport().update()
            self._set_status(f"已更新运行环境: {entry.name}")

    def _current_title(self) -> str:
        if self.tag:
            return f"#{self.tag}"
        cat = next((c for c in self.categories if c.cat_id == self.nav), None)
        return cat.name if cat else NAV_TITLES.get(self.nav, "全部")

    def _rebuild_grid(self) -> None:
        items = self._filtered()
        category = next((c for c in self.categories if c.cat_id == self.nav), None)
        self._update_navigation()
        self._recursive_scope.setVisible(category is not None)
        if category:
            scope = "当前目录及子目录" if self._recursive_scope.isChecked() else "仅当前目录"
            self._scope_hint.setText(f"{scope} · {category.path}")
        else:
            self._scope_hint.setText("跨目录汇总 · 当前工作区的全部条目" if self.nav == "all" else "当前工作区 · " + self._current_title())
        self._clear_recent_btn.setVisible(self.nav == "recent")
        self._overview.set_entries(items)
        self._overview.setVisible(self.view == "grid" and bool(items))
        self._filter_reset.setVisible(bool(self.query or self.tag or self.nav != "all" or self.kind != "all"))
        title = self._current_title()
        self._title.setText(title)
        self._title.setToolTip(title)
        self._count_lbl.setText(f"{len(items)} 项" if len(items) == len(self.entries)
                                else f"{len(items)} / {len(self.entries)}")

        self._list_header.setVisible(self.view == "list" and bool(items))
        self._browser.set_grid(self.view == "grid")
        _selection_guard = QSignalBlocker(self._browser)
        self._browser.setProperty("searchQuery", self.query)
        self._browser.viewport().update()
        if not self._browser.entries_model.replace(items):
            self._browser.viewport().update()
        self._browser.setVisible(bool(items))
        self._empty_tip.setVisible(not items)
        self._empty_tip.setText(
            "工作区为空\n添加工具后刷新。"
            if not self.entries else "无搜索结果\n调整关键词或清除筛选。")
        if self.selected:
            for row, entry in enumerate(items):
                if entry.path == self.selected.path:
                    self._browser.setCurrentIndex(self._browser.model().index(row, 0))
                    break

    def activate_launcher(self) -> None:
        # A global invocation always brings the workspace forward.
        self._show_from_tray()
        self._focus_search()

    def _open_palette(self) -> None:
        if getattr(self, "_palette", None) is not None:
            self._palette.reject()
            return
        from ui.widgets.palette import LaunchPalette
        dialog = LaunchPalette(
            self.entries, self._note_cache, self._icon_for,
            lambda: QApplication.instance().property("superguiTheme") == "dark",
            self, index=self._search_index)
        dialog.launchRequested.connect(lambda e: self._launch(e, False))
        dialog.actionRequested.connect(self._entry_action)
        self._palette = dialog
        # Place explicitly: Qt can place dialogs of hidden parents at (0, 0).
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            dialog.resize(min(760, max(320, bounds.width() - 48)), min(500, max(240, bounds.height() - 48)))
            dialog.ensurePolished()
            center = self.frameGeometry().center() if self.isVisible() and not self.isMinimized() else bounds.center()
            x = max(bounds.left(), min(center.x() - dialog.width() // 2, bounds.right() - dialog.width() + 1))
            y = max(bounds.top(), min(center.y() - dialog.height() // 2, bounds.bottom() - dialog.height() + 1))
            dialog.move(x, y)
        try:
            dialog.exec()
        finally:
            self._palette = None

    # ── 交互 ──
    def _update_navigation(self):
        category = next((c for c in self.categories if c.cat_id == self.nav), None)
        chain = []
        current = category
        seen = set()
        while current and current.cat_id not in seen:
            seen.add(current.cat_id)
            chain.append(current)
            current = next((c for c in self.categories if c.cat_id == current.parent_id), None)
        location = str(category.path if category else self.workspace)
        self._breadcrumbs.setText(location.replace('\\', ' / '))
        self._breadcrumbs.setToolTip(str(category.path if category else self.workspace))
        valid = {'all', 'star', 'recent', *(c.cat_id for c in self.categories)}
        self._nav_back[:] = [key for key in self._nav_back if key in valid]
        self._nav_forward[:] = [key for key in self._nav_forward if key in valid]
        self._back_btn.setEnabled(bool(self._nav_back))
        self._forward_btn.setEnabled(bool(self._nav_forward))
        self._up_btn.setEnabled(category is not None)

    def _copy_current_location(self):
        category = next((c for c in self.categories if c.cat_id == self.nav), None)
        QApplication.clipboard().setText(str(category.path if category else self.workspace))
        self._set_status("当前目录路径已复制")

    def _navigate_up(self):
        category = next((c for c in self.categories if c.cat_id == self.nav), None)
        if category: self._on_nav(category.parent_id or 'all')

    def _history_move(self, forward):
        source = self._nav_forward if forward else self._nav_back
        target = self._nav_back if forward else self._nav_forward
        if not source or not self._detail.confirm_leave(): return
        target.append(self.nav)
        self._set_navigation(source.pop())

    def _set_navigation(self, key):
        self._search_timer.stop()
        self._search.clear()
        self.query = ''
        self.nav = key
        self.tag = None
        self._sidebar.set_current(key)
        self._rebuild_grid()

    def _on_nav(self, key: str) -> None:
        from urllib.parse import unquote
        key = unquote(key)
        if key == self.nav and not self.tag: return
        if not self._detail.confirm_leave():
            self._sidebar.set_current(self.nav)
            return
        self._nav_back.append(self.nav)
        self._nav_forward.clear()
        self._set_navigation(key)

    def _on_tag(self, tag: str | None) -> None:
        self.tag = tag
        if tag:
            self.nav = "all"
        self._rebuild_grid()

    def _apply_query(self) -> None:
        query = self._search.text().strip()
        if query != self.query:
            self.query = query
            self._rebuild_grid()

    def _clear_query(self) -> None:
        if not self.query and not self._search.text():
            return
        self._search_timer.stop()
        with QSignalBlocker(self._search):
            self._search.clear()
        self.query = ""
        self._rebuild_grid()

    def _focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_content_panel", None) and event.type() == QEvent.Type.Resize:
            self._quick_start.setVisible(event.size().width() >= 600)
        if obj is self._search and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._search_timer.isActive():
                    self._search_timer.stop()
                    self._apply_query()
                index = self._browser.currentIndex()
                if not index.isValid() and self._browser.model().rowCount():
                    index = self._browser.model().index(0, 0)
                if index.isValid():
                    if event.key() == Qt.Key.Key_Down:
                        self._browser.setFocus()
                        self._browser.setCurrentIndex(index)
                    else:
                        entry = index.data(ENTRY_ROLE)
                        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                            self._entry_action("admin", entry)
                        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            self._entry_action("args", entry)
                        else:
                            self._launch(entry, False)
                return True
        return super().eventFilter(obj, event)

    def _on_select(self, entry: Entry) -> bool:
        if self.selected is not None and entry is not self.selected \
                and not self._detail.confirm_leave():
            _guard = QSignalBlocker(self._browser)
            for row, current in enumerate(self._browser.entries_model.entries):
                if current.path == self.selected.path:
                    self._browser.setCurrentIndex(self._browser.model().index(row, 0))
                    break
            return False  # 用户取消：保持原选中，不吃掉未保存的笔记
        self.selected = entry
        stats = self.db.get_stats(entry.path)
        self._detail.set_entry(entry, self._icon_for(entry), stats)
        self._status_path.setText(str(entry.path))
        self._status_path.setToolTip(str(entry.path))
        return True

    def _launch(self, entry: Entry, admin: bool, args: str = "", planned_entry=None) -> None:
        category = next((c for c in self.categories if c.path == entry.path), None)
        if category and entry.entry_type == "dir" and planned_entry is None:
            self._search_timer.stop()
            self._search.clear()
            self.query = ""
            self._on_nav(category.cat_id)
            self._sidebar.set_current(category.cat_id)
            return
        try:
            # admin 记忆生效：右键点了「启动」也会按记忆提权
            environment = profile_environment(entry.runtime_profile) if entry.runtime_profile else None
            from core.launch_history import start
            identifier, msg = start(
                planned_entry or entry, args, environment, admin or entry.admin, self.app_dir / "config/launches",
                terminal=self.settings.get("terminal", ""),
                terminal_args=self.settings.get("terminal_args", ""))
            if identifier:
                self._launch_states[identifier] = "starting"
        except (RuntimeError, ValueError, OSError) as e:
            self._set_status(f"启动失败: {e}", ok=False)
            QMessageBox.warning(self, "启动失败", str(e))
            return
        self.db.record_run(entry.path)
        entry.runs += 1
        entry.recent_hidden = False
        entry.last_run = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._set_status(msg)
        self._status_path.setText(str(entry.path))
        self._status_path.setToolTip(str(entry.path))
        self._rebuild_sidebar()
        self._rebuild_grid()
        self._on_select(entry)  # 顺便刷新详情面板的统计

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self._manage_files(paths)

    def _manage_files(self, paths=None):
        if not self._detail.confirm_leave(): return
        from ui.widgets.file_manager import FileManagerDialog
        dialog = FileManagerDialog(self.workspace, self.categories, self.entries, self.db, self)
        if isinstance(paths, list):
            QTimer.singleShot(0, lambda: dialog.import_items(paths))
        # Delay scans until the filesystem and metadata operation has completed.
        self._watch_timer.stop()
        self._watcher.blockSignals(True)
        try:
            dialog.exec()
        finally:
            self._watcher.blockSignals(False)
        if dialog.changed: self.reload()

    def _browse_files(self, entry):
        from ui.widgets.file_manager import ToolFilesDialog
        ToolFilesDialog(entry, self.settings, self).exec()
        self.reload()

    def _clear_recent(self):
        entries = [e for e in self.entries if e.runs > 0 and not e.recent_hidden]
        if not entries: return
        if QMessageBox.question(self, "清空最近记录", f"清空当前工作区的 {len(entries)} 项最近记录？\n累计启动次数与运行日志会保留。",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._remove_recent(entries)

    def _remove_recent(self, entries):
        self.db.clear_recent([e.path for e in entries])
        for entry in entries: entry.recent_hidden = True
        self._rebuild_sidebar()
        self._rebuild_grid()
        self._set_status(f"已清理 {len(entries)} 项最近记录")

    def _edit_aliases(self, entry):
        text, ok = QInputDialog.getText(self, "搜索别名", "多个别名用逗号分隔", text=", ".join(entry.aliases))
        if not ok: return
        entry.aliases = list(dict.fromkeys(t.strip() for t in text.replace('，', ',').split(',') if t.strip()))
        self.db.set_aliases(entry.path, entry.aliases)
        self._search_index.update(entry, self._note_cache.get(str(entry.path), ""))
        self._rebuild_grid()
        self._on_select(entry)
        self._set_status("已保存搜索别名")

    def _launch_plans(self, entry):
        from ui.widgets.launch_plans import LaunchPlansDialog
        dialog = LaunchPlansDialog(entry, self)
        def save_plans(plans):
            self.db.set_launch_plans(entry.path, plans)
            entry.launch_plans = plans
        dialog.plansChanged.connect(save_plans)
        dialog.exec()
        self._on_select(entry)
        if dialog.run_plan: self._run_launch_plan(entry, dialog.run_plan)

    def _run_launch_plan(self, entry, plan):
        from dataclasses import replace
        from ui.widgets.launch_plans import ParametersDialog
        from core.command_templates import fields
        command = plan['command']
        if fields(command):
            params = ParametersDialog(command, self, defaults=plan.get("values", {}), plan_name=plan["name"])
            if not params.exec(): return
            command = params.result_command
            plan["values"] = params.values
            for saved in entry.launch_plans:
                if saved['name'] == plan['name']: saved['values'] = params.values
            self.db.set_launch_plans(entry.path, entry.launch_plans)
        planned = replace(entry, command=command, shell_type=plan['shell_type'], admin=plan['admin'])
        self._launch(entry, plan['admin'], planned_entry=planned)

    def _open_entry_folder(self, entry: Entry, parent: bool = False) -> None:
        path = entry.path.parent if parent else entry.path
        try:
            launcher.open_in_explorer(path)
        except (OSError, RuntimeError) as error:
            self._set_status(f"打开失败: {error}", ok=False)
            QMessageBox.warning(self, "无法打开目录", str(error))
            return
        self._set_status(f"已打开: {path}")

    def _show_context(self, entry: Entry, pos: QPoint) -> None:
        if not self._on_select(entry):
            return
        menu = QMenu(self)
        directory = entry.entry_type == 'dir'
        menu.addAction('进入目录' if directory else '启动\tEnter', lambda: self._launch(entry, False))
        menu.addAction('打开目录\tCtrl+E' if entry.is_dir else '打开所在文件夹\tCtrl+E', lambda: self._open_entry_folder(entry))
        menu.addAction('复制路径\tCtrl+Shift+C', lambda: self._copy_entry_path(entry))
        menu.addAction('取消固定\tCtrl+D' if entry.pinned else '固定\tCtrl+D', lambda: self._toggle_pin(entry, not entry.pinned))
        menu.addSeparator()
        if not directory:
            launch = menu.addMenu('启动选项')
            launch.addAction('带参数启动…\tShift+Enter', lambda: self._launch_with_args(entry))
            launch.addAction('管理员启动\tCtrl+Enter', lambda: self._launch(entry, True))
            always_admin = launch.addAction('始终以管理员启动')
            always_admin.setCheckable(True)
            always_admin.setChecked(entry.admin)
            always_admin.triggered.connect(lambda checked: self._toggle_admin(entry, checked))
            launch.addSeparator()
            launch.addAction('启动方案…', lambda: self._launch_plans(entry))
            launch.addAction('编辑启动…\tF2', lambda: self._popup_command(entry))
            launch.addAction('运行环境…\tCtrl+Shift+R', lambda: self._edit_runtime_profile(entry))
            launch.addAction('识别启动入口…', lambda: self._detect_entry(entry))
        manage = menu.addMenu('管理')
        manage.addAction('浏览文件…', lambda: self._browse_files(entry))
        manage.addAction('编辑搜索别名…', lambda: self._edit_aliases(entry))
        manage.addAction('编辑标签…', lambda: self._detail._edit_tags())
        manage.addAction('整理工具…', self._manage_files)
        manage.addAction('检查工具…', lambda: self._check_entry(entry))
        more = menu.addMenu('更多')
        more.addAction('打开上级文件夹', lambda: self._open_entry_folder(entry, parent=True))
        more.addAction('复制名称', lambda: QGuiApplication.clipboard().setText(entry.path.name))
        more.addAction('创建桌面快捷方式', lambda: self._create_desktop_shortcut(entry))
        if entry.runs > 0 and not entry.recent_hidden:
            more.addAction('移出最近记录', lambda: self._remove_recent([entry]))
        menu.exec(pos)

    def _copy_entry_path(self, entry):
        QApplication.clipboard().setText(str(entry.path))
        self._set_status('已复制路径：' + entry.name)

    def _launch_with_args(self, entry: Entry) -> None:
        """弹框输入本次启动的追加参数；参数记住后下次回填。

        渗透工具换目标是最高频动作，参数对自定义命令和主程序都生效。
        """
        args, ok = QInputDialog.getText(
            self, f"启动参数 · {entry.name}",
            "参数", text=entry.last_args)
        if not ok:
            return
        args = args.strip()
        self.db.set_last_args(entry.path, args)
        entry.last_args = args
        self._launch(entry, False, args=args)

    def _toggle_admin(self, entry: Entry, admin: bool) -> None:
        self.db.set_admin(entry.path, admin)
        entry.admin = admin
        self._set_status(
            ("已记住管理员启动: " if admin else "已取消管理员启动: ") + entry.name)

    def _popup_command(self, entry: Entry) -> None:
        dlg = CommandDialog(entry.command or "", entry.shell_type, self, entry)
        if dlg.exec():
            cmd, shell = dlg.result()
            self._edit_command(entry, cmd, shell)

    def _toggle_pin(self, entry: Entry, pinned: bool) -> None:
        self.db.set_pinned(entry.path, pinned)
        entry.pinned = pinned
        self._rebuild_sidebar()
        self._rebuild_grid()
        self._on_select(entry)   # 详情面板的按钮文案要跟着变
        self._set_status(("已固定: " if pinned else "取消固定: ") + entry.name)

    def _edit_command(self, entry: Entry, command: str, shell_type: str) -> None:
        self.db.set_command(entry.path, command, shell_type)
        entry.command, entry.shell_type = command or None, shell_type
        self._set_status(f"已保存启动命令: {entry.name}")
        if self.selected and entry.path == self.selected.path:
            self._on_select(entry)

    def _edit_tags(self, entry: Entry, tags: list[str]) -> None:
        self.db.set_tags(entry.path, tags)
        entry.tags = tags
        self._search_index.update(entry, self._note_cache.get(str(entry.path), ""))
        self._rebuild_sidebar()
        self._rebuild_grid()
        self._on_select(entry)
        self._set_status(f"已更新标签: {entry.name}")

    def _on_note_saved(self, entry: Entry) -> None:
        # 笔记首行作为卡片摘要；同步刷新搜索缓存和卡片文字
        entry.desc = notes.note_excerpt(entry.note_path) or f"{entry.cat_name} / {entry.name}"
        if entry.note_path:
            self._note_cache[str(entry.path)] = \
                notes.read_note_head(entry.note_path, NOTE_CACHE_BYTES).lower()
        self._search_index.update(entry, self._note_cache.get(str(entry.path), ""))
        self._rebuild_grid()
        self._set_status(f"笔记已保存: {entry.name}")

    def _change_workspace(self) -> None:
        root = QFileDialog.getExistingDirectory(
            self, "选择工具目录", str(self.workspace))
        if root:
            self._switch_workspace(Path(root))

    @staticmethod
    def _same_path(a, b) -> bool:
        """Windows 路径比较：归一化分隔符并忽略大小写。"""
        return os.path.normcase(os.path.normpath(str(a))) == \
               os.path.normcase(os.path.normpath(str(b)))

    def _switch_workspace_by_index(self, i: int) -> None:
        """顶栏预设按钮点击直达：按序号取当前预设（设置里改过也能拿到最新值）。"""
        path = self._workspace_selector.itemData(i)
        if path:
            self._switch_workspace(Path(path))
            self._refresh_workspace_buttons()

    def _switch_workspace(self, root: Path) -> None:
        """切换到指定工作区；换工作区后筛选状态全部重置。"""
        if self._same_path(root, self.workspace):
            return
        if not root.is_dir():
            self._set_status(f"工作区不存在: {root}", ok=False)
            return
        if self._detail.has_unsaved_note() and not self._detail.confirm_leave():
            return  # 取消切换，别把编辑中的笔记带丢
        self.workspace = root
        self.settings.set("workspace_root", str(root))
        self.nav, self.tag, self.query = "all", None, ""
        self._kind_filter.setCurrentIndex(0)
        self._sort_filter.setCurrentIndex(0)
        self._search.clear()
        self._sidebar.set_current("all")
        self._refresh_workspace_buttons()
        self.reload()

    def _refresh_workspace_buttons(self) -> None:
        """按 settings 里的预设刷新顶栏按钮的名称/路径提示/当前激活态。"""
        if not hasattr(self, "_workspace_selector"):
            return
        selector = self._workspace_selector
        with QSignalBlocker(selector):
            selector.clear()
            presets = self.settings.get_presets()
            for preset in presets:
                selector.addItem(preset["name"], preset["path"])
            current = next((i for i,p in enumerate(presets) if self._same_path(p["path"], self.workspace)), -1)
            if current < 0:
                selector.addItem(self.workspace.name, str(self.workspace))
                current = selector.count()-1
            selector.setCurrentIndex(current)
            needed = max((selector.fontMetrics().horizontalAdvance(selector.itemText(i))
                          for i in range(selector.count())), default=144) + 60
            selector.setMinimumWidth(min(420, max(144, needed)))
            selector.setToolTip(selector.currentText() + "\n" + str(selector.currentData() or ""))

    def _set_view(self, view: str) -> None:
        self.view = view
        self.settings.set("view", view)
        # 设置对话框也能改视图，顶栏切换按钮的选中态要同步
        self._grid_btn.setChecked(view == "grid")
        self._list_btn.setChecked(view == "list")
        self._rebuild_grid()

    def _set_show_tags(self, show: bool) -> None:
        """侧栏标签云显隐（设置对话框）。隐藏时清掉进行中的标签筛选。"""
        self.settings.set("show_tags", show)
        if not show and self.tag is not None:
            self.tag = None
            self._rebuild_grid()
        self._rebuild_sidebar()

    def _apply_theme(self, theme: str) -> None:
        self.settings.set("theme", theme)
        apply_theme(QApplication.instance(), self.app_dir, theme)
        self._applied_theme = resolve_theme(theme)
        dark = self._applied_theme == "dark"
        self._theme_btn.setIcon(svg_icon(SVG_SUN if dark else SVG_MOON, ICON_COLOR))
        modes = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
        self._theme_btn.setToolTip(
            f"外观主题：{modes.get(theme, '跟随系统')}（当前{'深色' if dark else '浅色'}）")
        self._browser.viewport().update()
        self._sidebar.update()
        self._detail.update()
        if self.centralWidget():
            self.centralWidget().update()
        self._apply_native_frame()

    def _show_theme_menu(self) -> None:
        menu = QMenu(self)
        current = self.settings.get("theme", "system")
        for value, label in (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == value)
            action.triggered.connect(lambda _=False, v=value: self._apply_theme(v))
        menu.exec(self._theme_btn.mapToGlobal(self._theme_btn.rect().bottomLeft()))

    def _sync_system_theme(self) -> None:
        """「跟随系统」时，系统亮暗切换后自动重载主题。"""
        if self.settings.get("theme", "system") != "system":
            return
        if resolve_theme("system") != self._applied_theme:
            self._apply_theme("system")

    def _panel_widths(self) -> dict:
        if not self._detail.isHidden():
            self._preferred_detail_width = self._detail.width()
        return {"sidebar": self._sidebar.width(), "detail": self._preferred_detail_width}

    def _save_panel_widths(self) -> None:
        self.settings.set("panel_widths", self._panel_widths())

    def _restore_panel_widths(self, initial=False) -> None:
        stored = self.settings.get("panel_widths", {})
        side = stored.get("sidebar", 232) if initial else self._sidebar.width()
        side = max(200, min(420, int(side)))
        detail = 0 if self._detail.isHidden() else self._preferred_detail_width
        handles = self._splitter.handleWidth() * (1 if self._detail.isHidden() else 2)
        self._splitter.setSizes([side, max(420, self._splitter.width()-side-detail-handles), detail])

    def _reset_panel_widths(self) -> None:
        self._preferred_detail_width = 332
        self.settings.set("panel_widths", {"sidebar": 232, "detail": 332})
        self._restore_panel_widths(initial=True)
        self._save_panel_widths()

    def _toggle_panel(self) -> None:
        visible = self._detail.isHidden()
        if not visible:
            self._preferred_detail_width = self._detail.width()
        self._detail.setVisible(visible)
        self._panel_btn.setChecked(visible)
        self._restore_panel_widths()
        self.settings.update({"detail_visible": visible, "panel_widths": self._panel_widths()})

    def _apply_native_frame(self) -> None:
        from ui.native_frame import apply_native_frame, apply_backdrop
        active = apply_backdrop(self)
        # Caption colors must be applied after the backdrop, otherwise DWM
        # resets the non-client frame during theme changes.
        apply_native_frame(self, resolve_theme(self.settings.get("theme", "system")) == "dark")
        if self.property("nativeBackdrop") != active:
            self.setProperty("nativeBackdrop", active)
            self.style().unpolish(self)
            self.style().polish(self)
            if self.centralWidget():
                self.centralWidget().update()

    def _show_tool_center(self):
        from ui.widgets.tool_center import ToolCenter
        ToolCenter(self).exec()

    def _create_desktop_shortcut(self, entry: Entry) -> None:
        try:
            from core.shortcut import create_desktop_shortcut
            link = create_desktop_shortcut(entry)
        except (RuntimeError, OSError) as error:
            self._set_status(f"创建快捷方式失败: {error}", ok=False)
            QMessageBox.warning(self, "创建桌面快捷方式", str(error))
            return
        self._set_status(f"已创建桌面快捷方式: {link.name}")
        if self._tray.isVisible():
            self._tray.showMessage("SuperBay", f"已创建桌面快捷方式：{link.name}",
                                   QSystemTrayIcon.MessageIcon.Information, 2000)

    def _detect_entry(self, entry):
        from core.toolbox import suggestions
        candidates = suggestions(entry)
        if not candidates:
            QMessageBox.information(self, "启动入口", "未找到支持的入口，请手动设置命令。")
            self._popup_command(entry)
            return
        labels = [name + "  ·  " + command for name, command in candidates]
        choice, ok = QInputDialog.getItem(self, "启动入口", "选择入口并保存启动命令", labels, 0, False)
        if ok:
            command = candidates[labels.index(choice)][1]
            self.db.set_command(entry.path, command, "cmd")
            entry.command, entry.shell_type = command, "cmd"
            self._on_select(entry)
            self._set_status("已保存启动入口：" + entry.name)

    def _check_entry(self, entry):
        from core.toolbox import health
        issues = health(entry)
        QMessageBox.information(self, "工具检查 · " + entry.name, "\n".join(issues) if issues else "路径、启动入口和运行环境可用。")

    def _relocate_entry(self, entry, new_path):
        old = str(entry.path)
        with self.db._conn:
            for table in ("meta", "stats"):
                self.db._conn.execute("UPDATE OR REPLACE " + table + " SET path=? WHERE path=?", (str(new_path), old))
        entry.path = new_path
        entry.is_dir = new_path.is_dir()
        if not new_path.is_relative_to(self.workspace):
            self._switch_workspace(new_path.parent.parent)
        self._detect_entry(entry)
        self.reload()

    def _poll_launches(self):
        if not self._launch_states:
            return
        from core.launch_history import history, LABELS
        for record in history(self.app_dir / "config/launches"):
            identifier = record["id"]
            if identifier not in self._launch_states:
                continue
            state = record["state"]
            if state != self._launch_states[identifier]:
                self._set_status(record["name"] + " · " + LABELS.get(state, state) +
                                 (" · 工具管理中查看日志" if state == "failed" else ""), state != "failed")
                self._launch_states[identifier] = state
            if state in ("failed", "finished", "unknown"):
                self._launch_states.pop(identifier, None)

    def _show_runtimes(self) -> None:
        from ui.widgets.scoop_dialog import ScoopDialog
        dialog = ScoopDialog(self.settings, self)
        dialog.exec()

    def _set_scan_depth(self, depth: int) -> None:
        depth = scanner.normalize_scan_depth(depth)
        if self.settings.get("scan_depth", 2) != depth:
            self.settings.set("scan_depth", depth)
            self.reload()

    def _show_settings(self) -> None:
        """设置对话框：外观 / 工作区 / 热键 / 数据位置，widgets 只发信号。"""
        dlg = SettingsDialog(self.app_dir, self.settings, self)
        dlg.themeChanged.connect(self._apply_theme)
        dlg.viewChanged.connect(self._set_view)
        dlg.tagCloudChanged.connect(self._set_show_tags)
        dlg.hotkeyChanged.connect(lambda text: self.settings.set("hotkey", text))
        dlg.windowSizeChanged.connect(self._set_window_size)
        dlg.workspaceChangeRequested.connect(
            lambda: (self._change_workspace(), dlg.refresh_workspace()))
        dlg.presetsChanged.connect(self._on_presets_changed)
        dlg.scanDepthChanged.connect(self._set_scan_depth)
        dlg.exec()

    def _on_presets_changed(self, presets: list) -> None:
        """设置里改了工作区预设：落盘 + 立即刷新顶栏按钮。"""
        self.settings.set_presets(presets)
        self._refresh_workspace_buttons()

    def _set_window_size(self, w: int, h: int) -> None:
        """应用设置里的界面尺寸：写 settings + 立即 resize（受最小尺寸钳制）。"""
        win = dict(self.settings.get("window", DEFAULT_WINDOW_SIZE))
        win.update({"w": w, "h": h})   # 合并写入，别把已记住的 x/y 抹掉
        self.settings.set("window", win)
        self.resize(w, h)

    # ── 托盘与窗口 ──
    def _show_from_tray(self) -> None:
        # 最小化过才 showNormal；只是隐藏的窗口用 show()，保住最大化和尺寸状态
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._show_from_tray()

    def _quit_from_tray(self) -> None:
        self._tray_quit = True
        # 走 close() 而不是直接 quit()：保证 closeEvent 里的保存尺寸、关库、
        # 等后台扫描、笔记未保存询问都会执行；用户取消时不退出
        if self.close():
            QApplication.quit()
        else:
            self._tray_quit = False

    def _stop_scan(self) -> None:
        """退出前等后台扫描收尾，避免 QThread 运行中被销毁。"""
        from shiboken6 import isValid
        for thread, _worker in list(self._scans):
            if isValid(thread):
                thread.quit()
        for thread, _worker in list(self._scans):
            if isValid(thread):
                thread.wait()
        self._scans.clear()

    def toggle_visibility(self) -> None:
        """全局热键 / 第二实例唤起：显示时顺便聚焦搜索框，直接打字即搜。"""
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self._show_from_tray()
            self._focus_search()

    def nativeEvent(self, eventType, message) -> tuple[bool, int]:
        """无边框窗口没有边框缩放，拦 WM_NCHITTEST 把四边四角报给 Windows，
        缩放、阴影光标、Win11 贴靠布局就全回来了。最大化时跳过（边框在屏幕外）。"""
        return super().nativeEvent(eventType, message)

    def _border_hit(self, pos: QPoint) -> int:
        """窗口内坐标 → 边框命中码，不在边上返回 0。"""
        m, w, h = RESIZE_MARGIN, self.width(), self.height()
        left, right = 0 <= pos.x() < m, w - m <= pos.x() < w
        top, bottom = 0 <= pos.y() < m, h - m <= pos.y() < h
        if top and left:
            return HT_TOPLEFT
        if top and right:
            return HT_TOPRIGHT
        if bottom and left:
            return HT_BOTTOMLEFT
        if bottom and right:
            return HT_BOTTOMRIGHT
        if left:
            return HT_LEFT
        if right:
            return HT_RIGHT
        if top:
            return HT_TOP
        if bottom:
            return HT_BOTTOM
        return 0

    def showEvent(self, event) -> None:
        """首次显示时恢复上次的最大化状态（window.maximized）。"""
        super().showEvent(event)
        self._apply_native_frame()
        if self._start_maximized:
            self._start_maximized = False
            self.showMaximized()

    def closeEvent(self, e: QCloseEvent) -> None:
        # 托盘还开着的话，关窗只最小化到托盘；真正退出只能走托盘菜单「退出」
        if self._tray_quit or not self._tray.isVisible():
            if self._detail.has_unsaved_note() and not self._detail.confirm_leave():
                e.ignore()
                return
            self._save_panel_widths()
            self._closing = True
            self._stop_scan()
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            self.settings.set("window", {"w": geo.width(), "h": geo.height(),
                                         "x": geo.x(), "y": geo.y(),
                                         "maximized": self.isMaximized()})
            self.db.close()
            super().closeEvent(e)
            return
        e.ignore()
        self.hide()
        hotkey = self.settings.get("hotkey", "Ctrl+Alt+K")
        self._tray.showMessage("SuperBay", f"已最小化到托盘，{hotkey} 可唤起",
                               QSystemTrayIcon.MessageIcon.Information, 2000)

    def _set_status(self, text: str, ok: bool = True) -> None:
        self._status_ok.setText(("● " if ok else "✕ ") + text)
        self._status_ok.setToolTip(text)
        self._status_ok.setStyleSheet("" if ok else "color: #f0524f;")
