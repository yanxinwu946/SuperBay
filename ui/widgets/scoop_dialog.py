"""Compact, user-scoped runtime manager for an existing Scoop installation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)
from ui.widgets.modern_combo import ModernComboBox as QComboBox


from core import launcher, scoop


class ScoopDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("RuntimeDialog")
        from ui.native_frame import apply_native_frame
        from ui.theme import resolve_theme
        apply_native_frame(self, resolve_theme(settings.get("theme", "system")) == "dark")
        configured = settings.get("scoop_root", "")
        self.root = Path(configured) if configured else scoop.detect_root()
        self.runtimes = []
        self.pending = None
        self.operation = "switch"
        self.output = b""
        self.setWindowTitle("运行环境 · Scoop")
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        title = QLabel("运行环境")
        title.setObjectName("ContentTitle")
        layout.addWidget(title)
        binding = QHBoxLayout()
        self.path = QLineEdit(str(self.root) if self.root else "")
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("Scoop 安装目录")
        binding.addWidget(self.path, 1)
        self.bind = QPushButton("更改目录")
        self.bind.setObjectName("GhostBtn")
        self.bind.clicked.connect(self._bind)
        binding.addWidget(self.bind)
        self.refresh = QPushButton("刷新")
        self.refresh.setObjectName("GhostBtn")
        self.refresh.clicked.connect(self._refresh)
        binding.addWidget(self.refresh)
        self.packages = QPushButton("Scoop 软件管理")
        self.packages.setAutoDefault(False)
        self.packages.clicked.connect(self._packages)
        binding.addWidget(self.packages)
        layout.addLayout(binding)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索环境或版本")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate)
        filters.addWidget(self.search, 1)
        self.family = QComboBox()
        self.family.addItems(["全部环境", "Java", "Python", "Node"])
        self.family.currentIndexChanged.connect(self._populate)
        filters.addWidget(self.family)
        layout.addLayout(filters)
        self.table = QTreeWidget()
        self.table.setObjectName("RuntimeTable")
        self.table.setHeaderLabels(["环境", "发行版", "版本", "状态", "路径"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.activate = QPushButton("设为默认")
        self.activate.setObjectName("PrimaryBtn")
        self.activate.clicked.connect(self._activate)
        actions.addWidget(self.activate)
        self.terminal = QPushButton("打开终端")
        self.terminal.setObjectName("GhostBtn")
        self.terminal.clicked.connect(self._terminal)
        actions.addWidget(self.terminal)
        self.verify = QPushButton("验证版本")
        self.verify.setObjectName("GhostBtn")
        self.verify.clicked.connect(self._verify)
        actions.addWidget(self.verify)
        self.folder = QPushButton("打开目录")
        self.folder.setObjectName("GhostBtn")
        self.folder.clicked.connect(self._folder)
        actions.addWidget(self.folder)
        self.copy = QPushButton("复制路径")
        self.copy.setObjectName("GhostBtn")
        self.copy.clicked.connect(self._copy)
        actions.addWidget(self.copy)
        actions.addStretch(1)
        layout.addLayout(actions)
        for button in (self.bind, self.refresh, self.activate, self.terminal, self.verify, self.folder, self.copy):
            button.setAutoDefault(False)
        self.status = QLabel()
        self.status.setObjectName("ContentSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.activate.setToolTip("更改用户默认版本，新终端生效。")
        self.terminal.setToolTip("使用所选版本打开独立终端。")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.hide()
        layout.addWidget(self.log)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.verify_timer = QTimer(self, singleShot=True, interval=15000)
        self.verify_timer.timeout.connect(self._verify_timeout)
        self._refresh()

    def _packages(self):
        if self._busy(): return
        from ui.widgets.scoop_packages import ScoopPackagesDialog
        ScoopPackagesDialog(self.root, self).exec()
        self._refresh()

    def _selected(self):
        item = self.table.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _busy(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def _selection_changed(self):
        selected = self._selected()
        for button in (self.activate, self.terminal, self.verify, self.folder, self.copy):
            button.setEnabled(selected is not None and not self._busy())
        self.activate.setEnabled(bool(selected and not selected.default and not self._busy()))

    def _bind(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Scoop 根目录", str(self.root or Path.home()))
        if path:
            root = Path(path)
            if not scoop.valid_root(root):
                self.status.setText("请选择有效的 Scoop 安装目录。")
                return
            self.root = root
            self.settings.set("scoop_root", str(root))
            self.path.setText(str(root))
            self._refresh()

    def _refresh(self):
        if self._busy():
            return
        try:
            self.runtimes = scoop.scan(self.root) if self.root else []
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            self.runtimes = []
            self._populate()
            return
        self._populate()
        self.status.setText(f"{len(self.runtimes)} 个版本"
                            if self.runtimes else "未发现运行环境。请检查 Scoop 目录。")

    def _populate(self):
        selected = self._selected()
        self.table.clear()
        query = self.search.text().strip().casefold()
        for runtime in self.runtimes:
            if self.family.currentIndex() and runtime.family != self.family.currentText():
                continue
            if query and query not in f"{runtime.family} {runtime.package} {runtime.version}".casefold():
                continue
            state = "默认" if runtime.default else "可用"
            item = QTreeWidgetItem([runtime.family, runtime.package, runtime.version, state, str(runtime.directory)])
            item.setData(0, Qt.ItemDataRole.UserRole, runtime)
            for column in range(5):
                item.setToolTip(column, str(runtime.directory))
            self.table.addTopLevelItem(item)
            if selected and (selected.package, selected.version) == (runtime.package, runtime.version):
                self.table.setCurrentItem(item)
        if not self.table.currentItem() and self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))
        self._selection_changed()

    def _activate(self):
        runtime = self._selected()
        if not runtime or self._busy():
            return
        try:
            arguments = scoop.reset_arguments(self.root, runtime)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.operation = "switch"
        self.pending = runtime
        self.output = b""
        self.log.clear()
        self.log.show()
        env = QProcessEnvironment.systemEnvironment()
        env.insert("SCOOP", str(self.root))
        self.process.setProcessEnvironment(env)
        self.process.setProgram("powershell.exe")
        self.process.setArguments(arguments)
        self.status.setText(f"正在切换 {runtime.package} @ {runtime.version}…")
        self.bind.setEnabled(False)
        self.refresh.setEnabled(False)
        self.process.start()
        self._selection_changed()

    def _verify(self):
        runtime = self._selected()
        if not runtime or self._busy():
            return
        if not runtime.executable.is_file():
            self.status.setText("版本程序已不存在，请刷新后重新选择。")
            return
        self.operation = "verify"
        self.pending = runtime
        self.output = b""
        self.log.clear()
        self.log.show()
        env = QProcessEnvironment()
        for key, value in scoop.terminal_environment(runtime).items():
            env.insert(key, value)
        self.process.setProcessEnvironment(env)
        self.process.setProgram(str(runtime.executable))
        self.process.setArguments(["-version" if runtime.family == "Java" else "--version"])
        self.status.setText(f"正在验证 {runtime.package} @ {runtime.version}…")
        self.bind.setEnabled(False)
        self.refresh.setEnabled(False)
        self.process.start()
        self.verify_timer.start()
        self._selection_changed()

    def _verify_timeout(self):
        if self.operation == "verify" and self._busy():
            self.operation = "verify_timeout"
            self.process.kill()

    def _read_output(self):
        self.output = (self.output + bytes(self.process.readAllStandardOutput()))[-65536:]
        try:
            output = self.output.decode("utf-8")
        except UnicodeDecodeError:
            output = self.output.decode("mbcs" if os.name == "nt" else "utf-8", "replace")
        self.log.setPlainText(output)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _finished(self, exit_code, exit_status):
        self.verify_timer.stop()
        self._read_output()
        self.bind.setEnabled(True)
        self.refresh.setEnabled(True)
        requested = self.pending
        self.pending = None
        self._refresh()
        if self.operation.startswith("verify"):
            ok = exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
            self.status.setText("验证超时" if self.operation == "verify_timeout" else
                                ("验证通过" if ok else "验证失败，请查看日志。"))
            return
        switched = requested and any(r.package == requested.package and r.version == requested.version
                                     and r.current and r.default for r in self.runtimes)
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and switched:
            # Newly launched tools must see the chosen runtime, even if this launcher
            # was started before Scoop updated the persisted user environment.
            env = scoop.terminal_environment(requested)
            os.environ["PATH"] = env["PATH"]
            if requested.family == "Java":
                os.environ["JAVA_HOME"] = env["JAVA_HOME"]
            self.status.setText(f"已切换：{requested.family} · {requested.package} @ {requested.version}。重新打开终端后生效。")
        else:
            self.status.setText("切换未完成，请查看日志。")

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.verify_timer.stop()
            self.pending = None
            self.bind.setEnabled(True)
            self.refresh.setEnabled(True)
            self._selection_changed()
            self.status.setText("无法启动验证程序，请检查版本是否完整。" if self.operation == "verify" else "无法启动 Windows PowerShell，请检查系统环境。")

    def _terminal(self):
        runtime = self._selected()
        if runtime:
            try:
                env = scoop.terminal_environment(runtime)
                env["SCOOP"] = str(self.root)
                subprocess.Popen(["powershell.exe", "-NoLogo", "-NoExit", "-NoProfile"],
                                 cwd=runtime.directory, env=env,
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
                self.status.setText(f"已打开 {runtime.package} @ {runtime.version} 终端。")
            except OSError as error:
                self.status.setText(f"打开终端失败：{error}")

    def _folder(self):
        runtime = self._selected()
        if runtime:
            launcher.open_in_explorer(runtime.directory)

    def _copy(self):
        runtime = self._selected()
        if runtime:
            QGuiApplication.clipboard().setText(str(runtime.directory))
            self.status.setText("已复制版本目录。")

    def reject(self):
        if self._busy():
            self.status.setText("操作正在进行，请等待完成后关闭。")
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy():
            event.ignore()
            return
        super().closeEvent(event)
