"""右侧详情面板：图标、路径、标签、操作按钮、启动命令、Markdown 笔记、使用统计。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QScrollArea,
    QTabWidget,
    QSizePolicy,
    QApplication,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from ui.widgets.modern_combo import ModernComboBox as QComboBox


from core import entry_kind, notes
from core.models import DEFAULT_SHELL, Entry
from ui.widgets.kind_badge import KindBadge


class CommandDialog(QDialog):
    """编辑启动方式：命令、shell 与最终执行预览。"""

    def __init__(self, command: str, shell_type: str, parent=None, entry: Entry | None = None):
        super().__init__(parent)
        self._entry = entry
        self.setWindowTitle("编辑启动")
        self.setObjectName("CmdDialog")
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(9)

        title = QLabel("编辑启动")
        title.setObjectName("CmdDialogTitle")
        lay.addWidget(title)
        hint = QLabel("点击「启动」时执行的命令。留空则自动识别主程序，找不到时打开目录。")
        hint.setObjectName("CmdDialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addSpacing(4)

        mode = QHBoxLayout()
        mode.setSpacing(12)
        mode_label = QLabel("运行方式")
        mode_label.setObjectName("CmdFieldLabel")
        mode.addWidget(mode_label)
        segment = QFrame()
        segment.setObjectName("Segmented")
        seg_lay = QHBoxLayout(segment)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)
        self._shell_group = QButtonGroup(self)
        current = shell_type or DEFAULT_SHELL
        for text, value in (("cmd", "cmd"), ("PowerShell", "powershell")):
            button = QPushButton(text)
            button.setObjectName("SegBtn")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("shellValue", value)
            button.setChecked(value == current)
            button.toggled.connect(self._refresh_preview)
            self._shell_group.addButton(button)
            seg_lay.addWidget(button)
        if self._shell_group.checkedButton() is None:
            self._shell_group.buttons()[0].setChecked(True)
        mode.addWidget(segment)
        mode.addStretch(1)
        lay.addLayout(mode)

        cmd_label = QLabel("启动命令")
        cmd_label.setObjectName("CmdFieldLabel")
        lay.addWidget(cmd_label)
        self.cmd_edit = QLineEdit(command or "")
        self.cmd_edit.setObjectName("CommandInput")
        self.cmd_edit.setPlaceholderText('例如：python -u "main.py"，留空自动识别')
        self.cmd_edit.setClearButtonEnabled(True)
        self.cmd_edit.textChanged.connect(self._refresh_preview)
        lay.addWidget(self.cmd_edit)

        self._suggestions = []
        if entry and entry.is_dir and entry.path.exists():
            from core.toolbox import suggestions
            self._suggestions = suggestions(entry)
        self.detect_combo = QComboBox()
        self.detect_combo.setObjectName("DetectCombo")
        self.detect_combo.addItem("从目录识别入口…", None)
        for name, detected in self._suggestions:
            self.detect_combo.addItem(f"{name}  ·  {detected}", detected)
        self.detect_combo.currentIndexChanged.connect(self._pick_detected)
        self.detect_combo.setVisible(bool(self._suggestions))
        lay.addWidget(self.detect_combo)

        preview_label = QLabel("最终执行")
        preview_label.setObjectName("CmdFieldLabel")
        lay.addWidget(preview_label)
        self.preview = QLabel()
        self.preview.setObjectName("CmdPreview")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.preview)
        self.workdir = QLabel()
        self.workdir.setObjectName("CmdDialogHint")
        self.workdir.setWordWrap(True)
        lay.addWidget(self.workdir)

        lay.addSpacing(6)
        buttons = QHBoxLayout()
        reset = QPushButton("恢复自动")
        reset.setObjectName("GhostBtn")
        reset.setToolTip("清空命令，保存后恢复自动识别")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        cancel = QPushButton("取消")
        cancel.setObjectName("GhostBtn")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("PrimaryBtn")
        save.setDefault(True)
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.accept)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)
        self._refresh_preview()

    def _shell(self) -> str:
        button = self._shell_group.checkedButton()
        return str(button.property("shellValue")) if button else DEFAULT_SHELL

    def _set_shell(self, value: str) -> None:
        for button in self._shell_group.buttons():
            button.setChecked(button.property("shellValue") == value)

    def _pick_detected(self, index: int) -> None:
        detected = self.detect_combo.itemData(index)
        if not detected:
            return
        self.cmd_edit.setText(detected)
        self._set_shell("cmd")
        self.detect_combo.blockSignals(True)
        self.detect_combo.setCurrentIndex(0)
        self.detect_combo.blockSignals(False)

    def _reset(self) -> None:
        self.cmd_edit.clear()
        self._set_shell(DEFAULT_SHELL)
        self.cmd_edit.setFocus()

    def _cwd(self) -> str:
        path = self._entry.path
        return str(path if self._entry.is_dir else path.parent)

    def _auto_text(self) -> str:
        entry = self._entry
        if entry is None:
            return "自动：优先运行目录内主程序，未找到时打开目录"
        if not entry.is_dir:
            return f"自动：使用系统默认程序打开 {entry.name}"
        if not entry.path.exists():
            return "自动：路径不存在"
        from core.launcher import find_main_exe
        exe = find_main_exe(entry.path)
        if exe:
            return f"自动：运行 {exe.name}（尚未保存启动命令）"
        if self._suggestions:
            return f"自动：打开工具目录（识别到 {len(self._suggestions)} 个入口，可从上方填入命令）"
        return "自动：打开工具目录（未发现可运行入口）"

    def _refresh_preview(self, *_) -> None:
        command = self.cmd_edit.text().strip()
        if command:
            self.preview.setText(f'cmd.exe /d /s /k "{command}"' if self._shell() == "cmd"
                                 else f"powershell.exe -NoExit -Command {command}")
        else:
            self.preview.setText(self._auto_text())
        self.preview.setToolTip(self.preview.text())
        self.workdir.setText(f"工作目录：{self._cwd()}" if self._entry else "")

    def result(self) -> tuple[str, str]:
        return self.cmd_edit.text().strip(), self._shell()


class DetailPanel(QFrame):
    launchRequested = Signal(object)          # Entry
    openDirRequested = Signal(object)         # Entry
    pinToggled = Signal(object, bool)         # Entry, pinned
    commandEdited = Signal(object, str, str)  # Entry, command, shell_type
    tagsEdited = Signal(object, list)         # Entry, tags
    noteSaved = Signal(object)                # Entry
    runtimeRequested = Signal(object)
    detectRequested = Signal(object)
    shortcutRequested = Signal(object)
    plansRequested = Signal(object)
    filesRequested = Signal(object)
    planRunRequested = Signal(object, object)
    aliasesRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DetailPanel")
        self.setMinimumWidth(280)
        self.setMaximumWidth(640)
        self._entry: Entry | None = None
        self._editing = False
        self._note_dirty = False     # 编辑器里有未保存改动
        self._loading_note = False   # set_entry 回填内容时抑制 textChanged

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # 没选中条目时只显示这句提示
        self._empty = QLabel("选择条目查看详情")
        self._empty.setObjectName("DetailEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._root.addWidget(self._empty)

        self._body = QWidget()
        self._body.setVisible(False)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(16, 14, 16, 14)
        body_lay.setSpacing(12)

        self._head = QLabel()
        self._head.setObjectName("DetailTitle")
        self._head.setWordWrap(True)
        body_lay.addWidget(self._head)

        self._build_hero(body_lay)
        self._build_actions(body_lay)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("DetailTabs")
        body_lay.addWidget(self._tabs, 1)

        launch_page = QWidget()
        launch_layout = QVBoxLayout(launch_page)
        launch_layout.setContentsMargins(0, 12, 0, 0)
        launch_layout.setSpacing(12)
        launch_hint = QLabel("先点击上方「启动」试用；需要自定义时，再调整下面的设置。")
        launch_hint.setObjectName("ContentSub")
        launch_hint.setWordWrap(True)
        launch_layout.addWidget(launch_hint)
        self._build_command_section(launch_layout)
        self._build_plans_section(launch_layout)
        launch_layout.addStretch(1)
        launch_scroll = QScrollArea()
        launch_scroll.setWidgetResizable(True)
        launch_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        launch_scroll.setFrameShape(QFrame.Shape.NoFrame)
        launch_scroll.setWidget(launch_page)
        self._tabs.addTab(launch_scroll, "启动")
        self._tabs.setTabToolTip(0, "查看启动方式，或保存常用的启动方案")

        note_page = QWidget()
        note_layout = QVBoxLayout(note_page)
        note_layout.setContentsMargins(0, 12, 0, 0)
        self._note_help = QLabel("记录使用步骤、常用参数或遇到的问题，方便下次继续。")
        self._note_help.setObjectName("ContentSub")
        self._note_help.setWordWrap(True)
        note_layout.addWidget(self._note_help)
        self._build_note_section(note_layout)
        self._tabs.addTab(note_page, "笔记")
        self._tabs.setTabToolTip(1, "记录并保存这个工具的使用心得")

        info_page = QWidget()
        info_layout = QVBoxLayout(info_page)
        info_layout.setContentsMargins(0, 12, 0, 0)
        info_layout.setSpacing(16)
        info_hint = QLabel("查看文件位置、标签和使用记录，也可以设置搜索别名。")
        info_hint.setObjectName("ContentSub")
        info_hint.setWordWrap(True)
        info_layout.addWidget(info_hint)
        info_layout.addWidget(self._section_label("完整路径"))
        self._full_path = QLabel()
        self._full_path.setWordWrap(True)
        self._full_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._full_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info_layout.addWidget(self._full_path)
        paths = QHBoxLayout()
        paths.addWidget(self._mini_btn("复制路径", lambda: self._entry and QApplication.clipboard().setText(str(self._entry.path))))
        paths.addWidget(self._mini_btn("打开目录", lambda: self._entry and self.openDirRequested.emit(self._entry)))
        info_layout.addLayout(paths)
        info_layout.addWidget(self._mini_btn("创建桌面快捷方式", lambda: self._entry and self.shortcutRequested.emit(self._entry)))
        self._build_tag_row(info_layout)
        self._aliases = QLabel()
        self._aliases.setWordWrap(True)
        info_layout.addWidget(self._aliases)
        info_layout.addWidget(self._mini_btn("编辑搜索别名", lambda: self._entry and self.aliasesRequested.emit(self._entry)))
        self._build_stats_section(info_layout)
        info_layout.addStretch(1)
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        info_scroll.setWidget(info_page)
        self._tabs.addTab(info_scroll, "信息")
        self._tabs.setTabToolTip(2, "查看路径、标签、搜索别名和使用记录")

        self._root.addWidget(self._body, 1)

    # ── 界面搭建 ──
    def _build_hero(self, lay: QVBoxLayout) -> None:
        """大图标 + 名称 + 完整路径。"""
        hero = QHBoxLayout()
        hero.setSpacing(13)
        self._icon = QLabel()
        self._icon.setFixedSize(52, 52)
        hero.addWidget(self._icon)

        text = QVBoxLayout()
        text.setSpacing(3)
        self._name = QLabel()
        self._name.setObjectName("DetailName")
        self._name.setWordWrap(True)
        self._path = QLabel()
        self._path.setObjectName("DetailPath")
        self._path.setWordWrap(True)
        self._path.setMaximumHeight(42)
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self._name)
        text.addWidget(self._path)
        self._kinds_row = QHBoxLayout()
        self._kinds_row.setContentsMargins(0, 3, 0, 0)
        self._kinds_row.setSpacing(4)
        text.addLayout(self._kinds_row)
        hero.addLayout(text, 1)
        lay.addLayout(hero)

    def _set_kinds(self, entry: Entry) -> None:
        while self._kinds_row.count():
            item = self._kinds_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for kind in entry_kind.kinds_of(entry):
            self._kinds_row.addWidget(KindBadge(kind))
        self._kinds_row.addStretch(1)

    def _build_tag_row(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._tags = QLabel()
        self._tags.setObjectName("DetailTags")
        self._tags.setWordWrap(True)
        row.addWidget(self._tags, 1)
        row.addWidget(self._mini_btn("编辑", self._edit_tags))
        lay.addLayout(row)

    def _build_actions(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)

        launch_btn = QPushButton("启动")
        launch_btn.setObjectName("PrimaryBtn")
        launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        launch_btn.clicked.connect(
            lambda: self._entry and self.launchRequested.emit(self._entry))

        open_btn = QPushButton("文件")
        open_btn.setToolTip("浏览文件、预览内容或打开终端")
        open_btn.setObjectName("GhostBtn")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: self.filesRequested.emit(self._entry))

        self._pin_btn = QPushButton("固定")
        self._pin_btn.setObjectName("GhostBtn")
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.clicked.connect(self._toggle_pin)

        row.addWidget(launch_btn, 1)  # Equal action widths keep narrow panels balanced.
        row.addWidget(open_btn, 1)
        row.addWidget(self._pin_btn, 1)
        lay.addLayout(row)

    def _build_plans_section(self, lay):
        head = QHBoxLayout()
        head.addWidget(self._section_label("启动方案"))
        head.addStretch(1)
        head.addWidget(self._mini_btn("管理", lambda: self._entry and self.plansRequested.emit(self._entry)))
        lay.addLayout(head)
        self._plan_select = QComboBox()
        self._plan_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._plan_select.setMinimumContentsLength(1)
        self._plan_select.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._plan_select)
        self._plan_run = QPushButton("执行所选方案")
        self._plan_run.setObjectName("GhostBtn")
        self._plan_run.clicked.connect(self._run_plan)
        lay.addWidget(self._plan_run)
        self._plan_hint = QLabel("常用命令可以保存为方案；需要时再填写目标、文件或输出目录。")
        self._plan_hint.setWordWrap(True)
        self._plan_hint.setObjectName("ContentSub")
        lay.addWidget(self._plan_hint)

    def _run_plan(self):
        plan = self._plan_select.currentData()
        if self._entry and plan: self.planRunRequested.emit(self._entry, plan)

    def _build_command_section(self, lay: QVBoxLayout) -> None:
        head = QHBoxLayout()
        head.addWidget(self._section_label("启动方式"))
        head.addStretch(1)
        detect_btn = self._mini_btn(
            "识别", lambda: self._entry and self.detectRequested.emit(self._entry))
        detect_btn.setToolTip("不知道启动哪个文件？扫描目录并选择入口")
        head.addWidget(detect_btn)
        edit_btn = self._mini_btn("编辑", self._edit_command)
        edit_btn.setObjectName("LaunchEditBtn")
        edit_btn.setToolTip("设置点击「启动」时执行的命令")
        head.addWidget(edit_btn)
        lay.addLayout(head)

        card = QFrame()
        card.setObjectName("LaunchCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(11, 9, 11, 9)
        box.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._badge = QLabel()
        self._badge.setObjectName("LaunchBadge")
        top.addWidget(self._badge)
        top.addStretch(1)
        self._shell = QLabel()
        self._shell.setObjectName("LaunchMeta")
        top.addWidget(self._shell)
        box.addLayout(top)
        self._cmd = QLabel()
        self._cmd.setObjectName("LaunchCmd")
        self._cmd.setWordWrap(True)
        self._cmd.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self._cmd)
        self._meta = QLabel()
        self._meta.setObjectName("LaunchMeta")
        self._meta.setWordWrap(True)
        box.addWidget(self._meta)
        lay.addWidget(card)

        runtime_row = QHBoxLayout()
        self._runtime = QLabel("运行环境：继承默认")
        self._runtime.setObjectName("ContentSub")
        self._runtime.setWordWrap(True)
        self._runtime.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        runtime_row.addWidget(self._runtime, 1)
        runtime_row.addWidget(self._mini_btn("绑定环境", self._edit_runtime))
        lay.addLayout(runtime_row)

    def _build_note_section(self, lay: QVBoxLayout) -> None:
        """笔记：预览（Markdown 渲染）和编辑（纯文本）共用一个 QStackedWidget。

        原文一直在 _note_edit 里，预览页只是它的渲染结果，
        保存永远从 _note_edit 取，跟当前显示哪页无关。
        """
        head = QHBoxLayout()
        self._note_label = self._section_label("笔记")
        head.addWidget(self._note_label)
        head.addStretch(1)
        self._mode_btn = self._mini_btn("编辑", self._toggle_mode)
        head.addWidget(self._mode_btn)
        self._save_btn = self._mini_btn("保存", self._save_note)
        head.addWidget(self._save_btn)
        lay.addLayout(head)

        self._note_view = QTextBrowser()
        self._note_view.setObjectName("NoteView")
        self._note_view.setOpenExternalLinks(True)
        self._note_view.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self._note_edit = QPlainTextEdit()
        self._note_edit.setObjectName("NoteEdit")
        self._note_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._note_edit.textChanged.connect(self._on_note_changed)

        self._note_stack = QStackedWidget()
        self._note_stack.addWidget(self._note_view)
        self._note_stack.addWidget(self._note_edit)
        self._note_stack.setMinimumHeight(150)
        lay.addWidget(self._note_stack, 1)

    def _build_stats_section(self, lay: QVBoxLayout) -> None:
        lay.addWidget(self._section_label("使用统计"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self._runs = self._stat_box(row, "启动次数")
        self._last = self._stat_box(row, "最近使用")
        lay.addLayout(row)

    # ── 小组件 ──
    @staticmethod
    def _mini_btn(text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("MiniBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DetailSection")
        return lbl

    @staticmethod
    def _stat_box(parent_row: QHBoxLayout, key: str) -> QLabel:
        """统计小卡片，返回数值 label，set_entry 时回写。"""
        box = QFrame()
        box.setObjectName("StatMini")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(1)
        value = QLabel("0")
        value.setObjectName("StatMiniValue")
        label = QLabel(key)
        label.setObjectName("StatMiniKey")
        lay.addWidget(value)
        lay.addWidget(label)
        parent_row.addWidget(box)
        return value

    # ── 对外接口 ──
    def set_entry(self, entry: Entry | None, icon: QIcon | None,
                  stats: dict) -> None:
        """切换展示的条目，None 回空态。切条目时强制退出编辑态，免得串到下一个条目。"""
        preserve_note = bool(entry and getattr(self, "_note_identity", None) == (entry.path, entry.note_path))
        self._entry = entry
        self._note_identity = (entry.path, entry.note_path) if entry else None
        if not preserve_note:
            self._editing = False
        if not preserve_note:
            self._note_dirty = False   # 切条目前 MainWindow 已 confirm_leave()
            self._note_stack.setCurrentWidget(self._note_view)
            self._mode_btn.setText("编辑")
        if not entry:
            self._empty.setVisible(True)
            self._body.setVisible(False)
            return
        self._empty.setVisible(False)
        self._body.setVisible(True)

        self._head.setText(f"详情 · {entry.cat_name}")
        # 笔记只支持目录条目：文件条目禁用保存/编辑，标签也说清楚
        note_ok = entry.is_dir or entry.note_path is not None
        self._save_btn.setEnabled(note_ok)
        self._mode_btn.setEnabled(note_ok)
        self._note_label.setText("笔记")
        self._note_help.setText("记录使用步骤、常用参数或遇到的问题，方便下次继续。" if note_ok
                                else "此文件没有关联笔记；目录条目可以编辑和保存笔记。")
        self._note_label.setToolTip(str(entry.note_path) if entry.note_path else "")
        if icon:
            self._icon.setPixmap(icon.pixmap(52, 52))
        self._name.setText(entry.name)
        self._name.setToolTip(entry.name)
        if entry.group:
            self._path.setText("分类文件夹" + (f" · {entry.group_count} 个条目" if entry.group_count else ""))
        else:
            self._path.setText("工具目录" if entry.is_dir else "文件条目")
        self._full_path.setText(str(entry.path))
        self._path.setToolTip(str(entry.path))
        self._set_kinds(entry)
        self._tags.setText("  ".join(f"#{t}" for t in entry.tags) or "未设置标签")
        self._pin_btn.setText("取消固定" if entry.pinned else "固定")
        self._aliases.setText("搜索别名：" + ("、".join(entry.aliases) or "未设置"))
        self._plan_select.clear()
        for plan in entry.launch_plans:
            self._plan_select.addItem(plan['name'], plan)
        if not entry.launch_plans: self._plan_select.addItem("还没有启动方案", None)
        self._plan_run.setEnabled(bool(entry.launch_plans))
        cmd, shell = entry.command, entry.shell_type or DEFAULT_SHELL
        if cmd:
            badge, tone, command_text = "自定义", "custom", cmd
            meta = "点击「启动」时按此命令执行"
        else:
            from core.toolbox import suggestions
            choices = suggestions(entry) if entry.is_dir else []
            exe = None
            if entry.is_dir and entry.path.exists():
                from core.launcher import find_main_exe
                exe = find_main_exe(entry.path)
            if exe:
                badge, tone = "自动", "auto"
                command_text = exe.name
                meta = "自动识别到目录内主程序"
            elif entry.is_dir:
                badge, tone = "自动", "auto"
                command_text = "打开工具目录"
                meta = (f"识别到 {len(choices)} 个可运行入口，可从「编辑启动」填入" if choices
                        else "未发现可运行入口")
            else:
                badge, tone = "自动", "auto"
                command_text = "使用系统默认程序打开"
                meta = "未设置自定义命令"
        self._badge.setText(badge)
        self._badge.setProperty("tone", tone)
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)
        self._cmd.setText(command_text)
        self._cmd.setToolTip(command_text)
        self._shell.setText(shell if cmd else "")
        self._meta.setText(meta)
        versions = entry.runtime_profile.get("versions", {})
        self._runtime.setText("运行环境：" + (" · ".join(
            f"{family} {ref.get('version', '')}" for family, ref in versions.items()) or "继承默认"))

        self._runs.setText(str(stats.get("runs", 0)))
        self._last.setText(stats.get("last_run") or "—")
        if preserve_note:
            return
        content = notes.read_note(entry.note_path) if entry.note_path else ""
        if not content:
            content = notes.DEFAULT_TEMPLATE.format(name=entry.name, time="")
        self._loading_note = True
        self._note_edit.setPlainText(content)
        self._loading_note = False
        self._note_dirty = False
        self._note_view.setMarkdown(content)

    # ── 内部动作 ──
    def _toggle_pin(self) -> None:
        if self._entry:
            self.pinToggled.emit(self._entry, not self._entry.pinned)

    def _edit_runtime(self) -> None:
        if self._entry:
            self.runtimeRequested.emit(self._entry)

    def _edit_command(self) -> None:
        if not self._entry:
            return
        dlg = CommandDialog(self._entry.command or "",
                            self._entry.shell_type or DEFAULT_SHELL, self, self._entry)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cmd, shell = dlg.result()
            self.commandEdited.emit(self._entry, cmd, shell)

    def _edit_tags(self) -> None:
        if not self._entry:
            return
        text, ok = QInputDialog.getText(
            self, "编辑标签", "多个标签用逗号分隔：",
            text=",".join(self._entry.tags))
        if ok:
            # 兼容中文逗号，去空白、去空项
            tags = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
            self.tagsEdited.emit(self._entry, tags)

    def _toggle_mode(self) -> None:
        self._editing = not self._editing
        if self._editing:
            self._note_stack.setCurrentWidget(self._note_edit)
            self._mode_btn.setText("预览")
        else:
            self._note_view.setMarkdown(self._note_edit.toPlainText())
            self._note_stack.setCurrentWidget(self._note_view)
            self._mode_btn.setText("编辑")

    def _on_note_changed(self) -> None:
        if not self._loading_note:
            self._note_dirty = True

    def has_unsaved_note(self) -> bool:
        """是否有未保存的笔记编辑（切条目前 / 退出前询问用）。"""
        return self._note_dirty and self._entry is not None and self._entry.is_dir

    def confirm_leave(self) -> bool:
        """离开当前条目前的确认；返回 False 表示用户取消本次操作。"""
        if not self.has_unsaved_note():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("笔记未保存")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"「{self._entry.name}」的笔记尚未保存，是否保存？")
        save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_btn:
            return self._save_note()
        if clicked is discard_btn:
            self._note_dirty = False
            return True
        return False

    def _save_note(self) -> bool:
        if not self._entry or not self._entry.is_dir:
            return False
        note_path = notes.ensure_note(self._entry.path, self._entry.name)
        content = self._note_edit.toPlainText()
        if not notes.save_note(note_path, content):
            QMessageBox.warning(self, "保存失败", f"无法写入: {note_path}")
            return False
        self._entry.note_path = note_path
        self._note_dirty = False
        self.noteSaved.emit(self._entry)
        return True
