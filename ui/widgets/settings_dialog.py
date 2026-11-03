"""设置对话框：外观 / 界面尺寸 / 工作区 / 全局热键 / 数据位置，分区卡片式布局。

按项目约定只发信号，落地动作（应用主题、重建视图、换工作区、写 settings）
由 MainWindow 接线处理。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QListWidget,
    QStackedWidget,
    QScrollArea,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from ui.widgets.modern_combo import ModernComboBox as QComboBox


from config.settings import Settings
from core.hotkey import parse_hotkey
from core.version import __version__

THEMES = (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色"))
VIEWS = (("grid", "网格"), ("list", "列表"))
TAG_CLOUD = ((False, "隐藏"), (True, "显示"))

HOTKEY_SYNTAX = ("使用 Ctrl、Alt、Shift 或 Win 搭配按键。"
                 "例如 Ctrl+Alt+K、Ctrl+Alt+K。F1–F12 可单独使用。")

# 推荐窗口尺寸：(显示名, (宽, 高))，None 表示自定义
WINDOW_PRESETS = (
    ("紧凑  ·  1280 × 760", (1280, 760)),
    ("标准  ·  1440 × 860", (1440, 860)),
    ("宽敞  ·  1600 × 950", (1600, 950)),
    ("全高清  ·  1920 × 1080", (1920, 1080)),
    ("高分辨率  ·  2560 × 1440", (2560, 1440)),
    ("自定义", None),
)
WINDOW_MIN_W, WINDOW_MIN_H = 1100, 680   # 与 MainWindow.setMinimumSize 保持一致


class SettingsDialog(QDialog):
    themeChanged = Signal(str)               # "system" | "light" | "dark"
    viewChanged = Signal(str)                # "grid" | "list"
    tagCloudChanged = Signal(bool)           # 侧栏标签云显隐
    hotkeyChanged = Signal(str)              # 校验通过的新热键
    windowSizeChanged = Signal(int, int)     # 校验通过的窗口尺寸 (宽, 高)
    workspaceChangeRequested = Signal()      # 让 MainWindow 弹目录选择
    presetsChanged = Signal(list)            # 工作区快捷按钮预设 [{name, path}] ×2
    scanDepthChanged = Signal(int)

    def __init__(self, app_dir: Path, settings: Settings,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.app_dir = app_dir
        self.settings = settings
        self.setWindowTitle("设置")
        self.setObjectName("SettingsDialog")
        self.resize(960, 680)
        self.setMinimumSize(840, 580)
        # Hierarchy: navigation -> page heading -> grouped controls -> quiet hints.
        # Existing theme colors, 8/12px radii, 8/16/24/32px spacing.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("SettingsRail")
        rail.setFixedWidth(192)
        nav_layout = QVBoxLayout(rail)
        nav_layout.setContentsMargins(16, 28, 16, 24)
        nav_layout.setSpacing(16)
        brand = QLabel("偏好设置")
        brand.setObjectName("SettingsBrand")
        nav_layout.addWidget(brand)
        self._nav = QListWidget()
        self._nav.setObjectName("SettingsNav")
        self._nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        nav_layout.addWidget(self._nav, 1)
        version = QLabel(f"SuperBay  {__version__}")
        version.setObjectName("SettingsHint")
        nav_layout.addWidget(version)
        layout.addWidget(rail)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages, 1)
        sections = [
            ("外观", [self._build_appearance]),
            ("窗口", [self._build_window_size]),
            ("工作区", [self._build_workspace]),
            ("启动", [self._build_launch]),
            ("快捷键", [self._build_hotkey]),
            ("关于", [self._build_data]),
        ]
        for title, builders in sections:
            self._nav.addItem(title)
            scroll = QScrollArea()
            scroll.setObjectName("SettingsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            page = QWidget()
            page.setObjectName("SettingsPage")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(32, 32, 32, 32)
            page_layout.setSpacing(24)
            heading = QVBoxLayout()
            heading.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("SettingsPageTitle")
            heading.addWidget(label)
            page_layout.addLayout(heading)
            for builder in builders:
                page_layout.addWidget(builder())
            page_layout.addStretch(1)
            scroll.setWidget(page)
            self._pages.addWidget(scroll)
        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._nav.setCurrentRow(0)
        root.addWidget(body, 1)
        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(24, 12, 24, 12)
        note = QLabel("更改会自动保存")
        note.setObjectName("SettingsHint")
        foot.addWidget(note)
        foot.addStretch()
        done = QPushButton("完成")
        done.setObjectName("PrimaryBtn")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        foot.addWidget(done)
        root.addWidget(footer)
        from ui.native_frame import apply_native_frame
        from ui.theme import resolve_theme
        apply_native_frame(self, resolve_theme(settings.get("theme", "system")) == "dark")

    # ── 布局工具 ──
    def _card(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """分区标题 + 圆角卡片，返回 (容器, 卡片内布局)。"""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("SettingsSection")
        outer.addWidget(head)
        card = QFrame()
        card.setObjectName("SettingsCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 24, 24, 24)
        inner.setSpacing(20)
        outer.addWidget(card)
        return box, inner

    def _row(self, key: str) -> tuple[QWidget, QHBoxLayout]:
        """卡片里的一行：左侧固定宽度的键名 + 右侧控件区。"""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        k = QLabel(key)
        k.setObjectName("SettingsKey")
        k.setMinimumWidth(80)
        k.setWordWrap(True)
        lay.addWidget(k)
        return row, lay

    def _path_label(self, text: str) -> QLabel:
        """路径完整显示；放不下时由标签换行，tooltip 保留原文。"""
        lab = QLabel()
        lab.setObjectName("SettingsValue")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._set_path(lab, text)
        return lab

    @staticmethod
    def _set_path(lab: QLabel, text: str) -> None:
        lab.setText(text)
        lab.setToolTip(text)

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("SettingsHint")
        lab.setWordWrap(True)
        return lab

    # ── 各分区 ──
    def _build_appearance(self) -> QWidget:
        box, inner = self._card("外观")

        row, lay = self._row("主题")
        self._theme_combo = self._make_combo(THEMES, self.settings.get("theme", "system"))
        self._theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        lay.addWidget(self._theme_combo, 1)
        inner.addWidget(row)

        row, lay = self._row("默认视图")
        self._view_combo = self._make_combo(VIEWS, self.settings.get("view", "grid"))
        self._view_combo.currentIndexChanged.connect(
            lambda i: self.viewChanged.emit(self._view_combo.itemData(i)))
        lay.addWidget(self._view_combo, 1)
        inner.addWidget(row)

        row, lay = self._row("侧栏标签")
        self._tags_combo = self._make_combo(
            TAG_CLOUD, self.settings.get("show_tags", False))
        self._tags_combo.currentIndexChanged.connect(
            lambda i: self.tagCloudChanged.emit(self._tags_combo.itemData(i)))
        lay.addWidget(self._tags_combo, 1)
        inner.addWidget(row)
        return box

    def _on_theme_selected(self, index: int) -> None:
        theme = self._theme_combo.itemData(index)
        self.themeChanged.emit(theme)
        # This dialog owns a native caption too; refresh it after the app theme.
        from ui.native_frame import apply_native_frame
        from ui.theme import resolve_theme
        apply_native_frame(self, resolve_theme(theme) == "dark")

    @staticmethod
    def _make_combo(items: tuple[tuple[object, str], ...], current) -> QComboBox:
        combo = QComboBox()
        for key, label in items:
            combo.addItem(label, key)
        idx = combo.findData(current)
        if idx >= 0:  # 先定位再连信号，初始化不会误触发
            combo.setCurrentIndex(idx)
        return combo

    def _build_window_size(self) -> QWidget:
        """界面尺寸：推荐预设下拉框选中即生效；「自定义」展开宽高手动输入。"""
        box, inner = self._card("窗口大小")
        cur = self.settings.get("window", {"w": 1440, "h": 860})
        cur_size = (int(cur.get("w", 1440)), int(cur.get("h", 860)))

        row, lay = self._row("布局尺寸")
        self._size_combo = QComboBox()
        for label, size in WINDOW_PRESETS:
            self._size_combo.addItem(label, size)
        idx = self._size_combo.findData(cur_size)
        # 当前尺寸不在预设里 → 停在「自定义」并回填当前值
        self._size_combo.setCurrentIndex(idx if idx >= 0 else len(WINDOW_PRESETS) - 1)
        lay.addWidget(self._size_combo, 1)
        inner.addWidget(row)

        custom_row, clay = self._row("自定义")
        self._w_edit = QLineEdit(str(cur_size[0]))
        self._h_edit = QLineEdit(str(cur_size[1]))
        self._w_edit.setFixedWidth(72)
        self._h_edit.setFixedWidth(72)
        clay.addWidget(self._w_edit)
        clay.addWidget(QLabel("×"))
        clay.addWidget(self._h_edit)
        apply_btn = QPushButton("应用")
        apply_btn.setObjectName("MiniBtn")
        apply_btn.clicked.connect(self._apply_custom_size)
        clay.addWidget(apply_btn)
        clay.addStretch(1)
        inner.addWidget(custom_row)
        self._custom_row = custom_row

        self._size_hint = self._hint(
            f"选择后立即应用。自定义尺寸至少 {WINDOW_MIN_W} × {WINDOW_MIN_H}")
        inner.addWidget(self._size_hint)

        self._size_combo.currentIndexChanged.connect(self._on_size_preset)
        self._sync_custom_row()
        return box

    def _sync_custom_row(self) -> None:
        """只有选中「自定义」才显示宽高输入行。"""
        self._custom_row.setVisible(self._size_combo.currentData() is None)

    def _on_size_preset(self, i: int) -> None:
        self._sync_custom_row()
        size = self._size_combo.itemData(i)
        if size is not None:
            self.windowSizeChanged.emit(*size)

    def _apply_custom_size(self) -> None:
        """校验自定义宽高，非法输入标红提示，不写 settings。"""
        try:
            w, h = int(self._w_edit.text()), int(self._h_edit.text())
        except ValueError:
            self._set_size_state(False, "宽和高必须是整数，未保存")
            return
        if w < WINDOW_MIN_W or h < WINDOW_MIN_H:
            self._set_size_state(
                False, f"尺寸太小，宽 ≥ {WINDOW_MIN_W}、高 ≥ {WINDOW_MIN_H}，未保存")
            return
        self._set_size_state(True, "已应用")
        self.windowSizeChanged.emit(w, h)

    def _set_size_state(self, valid: bool, hint: str) -> None:
        self._w_edit.setProperty("invalid", "" if valid else "true")
        self._h_edit.setProperty("invalid", "" if valid else "true")
        self._size_hint.setProperty("invalid", "" if valid else "true")
        self._size_hint.setText(hint)
        for w in (self._w_edit, self._h_edit, self._size_hint):  # 动态属性要刷新样式
            w.style().unpolish(w)
            w.style().polish(w)

    def _build_workspace(self) -> QWidget:
        box, inner = self._card("工作区")
        manage = QPushButton("管理快捷入口")
        manage.setObjectName("GhostBtn")
        def open_directories():
            from ui.widgets.tool_center import WorkspaceDialog
            WorkspaceDialog(self.parent()).exec()
        manage.clicked.connect(open_directories)
        inner.addWidget(manage)
        row, lay = self._row("工具目录")
        self._ws_label = self._path_label("")
        self.refresh_workspace()
        lay.addWidget(self._ws_label, 1)
        btn = QPushButton("更改…")
        btn.setObjectName("MiniBtn")
        btn.clicked.connect(self.workspaceChangeRequested)
        lay.addWidget(btn)
        inner.addWidget(row)
        inner.addWidget(self._hint("从此目录发现工具，更改后自动刷新"))
        from core.scanner import MAX_SCAN_DEPTH, normalize_scan_depth
        row, lay = self._row("嵌套层数")
        self._scan_depth = QSpinBox()
        self._scan_depth.setRange(1, MAX_SCAN_DEPTH)
        self._scan_depth.setSuffix(" 级")
        self._scan_depth.setValue(normalize_scan_depth(self.settings.get("scan_depth", 2)))
        self._scan_depth.setKeyboardTracking(False)
        self._scan_depth.valueChanged.connect(self.scanDepthChanged.emit)
        lay.addWidget(self._scan_depth, 1)
        inner.addWidget(row)
        inner.addWidget(self._hint("默认 2 级。达到上限的文件夹保留为条目；菜单默认只显示第 1 级。"))

        return box

    def refresh_workspace(self) -> None:
        """换工作区后由 MainWindow 回调，刷新路径显示。"""
        root = self.settings.get("workspace_root", "") or "（未设置）"
        self._set_path(self._ws_label, root)

    def _build_launch(self) -> QWidget:
        box, inner = self._card("启动终端")
        row, lay = self._row("终端程序")
        self._terminal_edit = QLineEdit(self.settings.get("terminal", ""))
        self._terminal_edit.setPlaceholderText("留空使用系统控制台")
        self._terminal_edit.setClearButtonEnabled(True)
        self._terminal_edit.editingFinished.connect(self._save_terminal)
        lay.addWidget(self._terminal_edit, 1)
        browse = QPushButton("浏览…")
        browse.setObjectName("MiniBtn")
        browse.clicked.connect(self._browse_terminal)
        lay.addWidget(browse)
        inner.addWidget(row)
        self._terminal_hint = self._hint(
            "支持 Windows Terminal、WezTerm、Alacritty。管理员启动仍使用系统控制台。")
        inner.addWidget(self._terminal_hint)

        row, lay = self._row("参数模板")
        self._terminal_args = QLineEdit(self.settings.get("terminal_args", ""))
        self._terminal_args.setPlaceholderText('留空自动识别；例如 -d "{cwd}" {command}')
        self._terminal_args.editingFinished.connect(self._save_terminal)
        lay.addWidget(self._terminal_args, 1)
        inner.addWidget(row)
        inner.addWidget(self._hint(
            "占位符：{cwd} 工作目录，{command} 要执行的命令。不同终端参数不同，留空按软件自动识别。"))
        return box

    def _browse_terminal(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择终端程序", "", "程序 (*.exe);;所有文件 (*)")
        if path:
            self._terminal_edit.setText(path)
            self._save_terminal()

    def _save_terminal(self) -> None:
        path = self._terminal_edit.text().strip().strip('"')
        args = self._terminal_args.text().strip()
        path_ok = not path or Path(path).is_file()
        args_ok = not args or "{command}" in args
        for widget, ok in ((self._terminal_edit, path_ok), (self._terminal_args, args_ok)):
            widget.setProperty("invalid", "" if ok else "true")
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if not (path_ok and args_ok):
            self._terminal_hint.setText(
                "终端程序不存在，或参数模板缺少 {command}，未保存。")
            return
        self._terminal_hint.setText(
            "支持 Windows Terminal、WezTerm、Alacritty。管理员启动仍使用系统控制台。")
        self.settings.update({"terminal": path, "terminal_args": args})

    def _build_hotkey(self) -> QWidget:
        box, inner = self._card("快速唤起")
        row, lay = self._row("唤起窗口")
        self._hk_edit = QLineEdit(self.settings.get("hotkey", "Ctrl+Alt+K"))
        self._hk_edit.setPlaceholderText("例如 Ctrl+Alt+K")
        self._hk_edit.editingFinished.connect(self._save_hotkey)
        lay.addWidget(self._hk_edit, 1)
        inner.addWidget(row)
        inner.addWidget(self._hint("全局快捷键唤起工作台并聚焦搜索；快速启动从工作台按钮打开。"))
        self._hk_hint = self._hint(f"重启后生效。{HOTKEY_SYNTAX}")
        inner.addWidget(self._hk_hint)
        return box

    def _save_hotkey(self) -> None:
        """失焦时校验并保存；非法输入标红提示，不写 settings。"""
        text = self._hk_edit.text().strip()
        old = self.settings.get("hotkey", "Ctrl+Alt+K")
        if not text or text.lower() == old.lower():
            self._hk_edit.setText(old)
            return
        if parse_hotkey(text) is None:
            self._set_hotkey_state(False, f"格式无效，未保存。{HOTKEY_SYNTAX}")
        else:
            self._set_hotkey_state(True, "已保存，重启后生效")
            self.hotkeyChanged.emit(text)

    def _set_hotkey_state(self, valid: bool, hint: str) -> None:
        self._hk_edit.setProperty("invalid", "" if valid else "true")
        self._hk_hint.setProperty("invalid", "" if valid else "true")
        self._hk_hint.setText(hint)
        for w in (self._hk_edit, self._hk_hint):  # 动态属性要刷新样式
            w.style().unpolish(w)
            w.style().polish(w)

    def _build_data(self) -> QWidget:
        box, inner = self._card("应用信息")
        actions = QHBoxLayout()
        actions.setSpacing(8)
        backup = QPushButton("备份与迁移")
        backup.setObjectName("GhostBtn")
        def open_backups():
            from ui.widgets.tool_center import ToolCenter
            dialog = ToolCenter(self.parent())
            from PySide6.QtWidgets import QTabWidget
            dialog.findChild(QTabWidget).setCurrentIndex(2)
            dialog.exec()
        backup.clicked.connect(open_backups)
        actions.addWidget(backup)
        shortcut = QPushButton("创建桌面快捷方式")
        shortcut.setObjectName("GhostBtn")
        shortcut.clicked.connect(self._create_app_shortcut)
        actions.addWidget(shortcut)
        actions.addStretch(1)
        inner.addLayout(actions)
        self._data_hint = QLabel()
        self._data_hint.setObjectName("SettingsHint")
        self._data_hint.setWordWrap(True)
        inner.addWidget(self._data_hint)
        row, lay = self._row("版本")
        ver = QLabel(f"SuperBay v{__version__}")
        ver.setObjectName("SettingsValue")
        lay.addWidget(ver)
        lay.addStretch(1)
        inner.addWidget(row)
        for key, path in (("配置文件", self.app_dir / "config" / "config.yaml"),
                          ("元数据库", self.app_dir / "config" / "data.db")):
            row, lay = self._row(key)
            lay.addWidget(self._path_label(str(path)), 1)
            btn = QPushButton("打开位置")
            btn.setObjectName("MiniBtn")
            btn.clicked.connect(lambda _=False, p=path: self._open_in_explorer(p))
            lay.addWidget(btn)
            inner.addWidget(row)
        return box

    def _create_app_shortcut(self) -> None:
        try:
            from core.shortcut import create_app_shortcut
            link = create_app_shortcut()
        except (RuntimeError, OSError) as error:
            self._data_hint.setText(f"创建失败：{error}")
            return
        self._data_hint.setText(f"已创建快捷方式：{link}")

    @staticmethod
    def _open_in_explorer(path: Path) -> None:
        """资源管理器定位文件；文件还没生成则打开所在目录。"""
        try:
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif path.parent.is_dir():
                os.startfile(path.parent)
        except OSError:
            pass
