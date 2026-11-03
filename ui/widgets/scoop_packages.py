"""Installed user Scoop packages and explicit batch actions."""
import json
import os
import re
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QPushButton, QPlainTextEdit, QLabel, QMessageBox, QComboBox, QProgressBar)
from core.scoop import valid_root, scoop_script
from ui.flowlayout import FlowLayout


class ScoopPackagesDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.last_request = None
        self._ansi_pending = ""
        self._pending_packages = []
        self._batch_failed = False
        self._cancelled = False
        self.setWindowTitle('Scoop 软件管理')
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('筛选已安装软件')
        title = QLabel('Scoop 软件管理')
        title.setObjectName('ContentTitle')
        layout.addWidget(title)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        self.state_filter = QComboBox()
        self.state_filter.addItems(['全部状态', '允许更新', '已冻结'])
        self.state_filter.currentIndexChanged.connect(self.filter)
        filters.addWidget(self.state_filter)
        self.bucket_filter = QComboBox()
        self.bucket_filter.addItem('全部来源')
        self.bucket_filter.currentIndexChanged.connect(self.filter)
        filters.addWidget(self.bucket_filter)
        layout.addLayout(filters)
        self.table = QTreeWidget()
        self.table.setObjectName("RuntimeTable")
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setHeaderLabels(['软件', '当前版本', '更新状态', '来源'])
        self.table.setRootIsDecorated(False)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)
        row = FlowLayout(h_spacing=8, v_spacing=8)
        self.buttons = []
        for label, slot in [('全选可见', lambda: self.check(True)), ('取消勾选', lambda: self.check(False)),
                            ('刷新', self.refresh), ('检查更新', lambda: self.run('status')),
                            ('更新所选', lambda: self.run('update')), ('更新全部', self.update_all),
                            ('冻结所选', lambda: self.run('hold')), ('解除冻结', lambda: self.run('unhold'))]:
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(row)
        feedback = QHBoxLayout()
        self.selection_summary = QLabel()
        feedback.addWidget(self.selection_summary, 1)
        self.retry = QPushButton('重试上次操作')
        self.retry.setEnabled(False)
        self.retry.clicked.connect(self.retry_last)
        feedback.addWidget(self.retry)
        self.cancel_button = QPushButton('中断操作')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_operation)
        feedback.addWidget(self.cancel_button)
        self.log_toggle = QPushButton('显示日志')
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.toggle_log)
        feedback.addWidget(self.log_toggle)
        layout.addLayout(feedback)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel('冻结后 Scoop 将跳过该软件的常规更新。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        layout.addWidget(self.log)
        self.log.hide()
        self.table.itemChanged.connect(self.update_counts)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.error)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.force_stop)
        self.search.textChanged.connect(self.filter)
        self.refresh()

    def busy(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def refresh(self):
        if self.busy(): return
        checked = {self.table.topLevelItem(i).text(0) for i in range(self.table.topLevelItemCount())
                   if self.table.topLevelItem(i).checkState(0) == Qt.CheckState.Checked}
        self.table.blockSignals(True)
        self.table.clear()
        self.table.blockSignals(False)
        if not self.root or not valid_root(self.root):
            self.status.setText('请先绑定有效 Scoop 目录。'); return
        for package in sorted((self.root / 'apps').iterdir()):
            if package.name == 'scoop': continue
            current = package / 'current'
            try:
                info_path = current / 'scoop-install.json'
                if not info_path.is_file(): info_path = current / 'install.json'
                manifest_path = current / 'scoop-manifest.json'
                if not manifest_path.is_file(): manifest_path = current / 'manifest.json'
                info = json.loads(info_path.read_text(encoding='utf-8-sig'))
                manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError): continue
            held = bool(info.get('hold'))
            item = QTreeWidgetItem([package.name, str(manifest.get('version', current.resolve().name)),
                                   '已冻结' if held else '允许更新', str(info.get('bucket', ''))])
            item.setCheckState(0, Qt.CheckState.Checked if package.name in checked else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, held)
            self.table.addTopLevelItem(item)
        for col in range(4): self.table.resizeColumnToContents(col)
        old_bucket = self.bucket_filter.currentText()
        buckets = sorted({self.table.topLevelItem(i).text(3) for i in range(self.table.topLevelItemCount())} - {''})
        self.bucket_filter.blockSignals(True)
        self.bucket_filter.clear()
        self.bucket_filter.addItems(['全部来源', *buckets])
        self.bucket_filter.setCurrentIndex(max(0, self.bucket_filter.findText(old_bucket)))
        self.bucket_filter.blockSignals(False)
        self.filter()

    def filter(self, *_):
        query = self.search.text().casefold().strip()
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            held = bool(item.data(0, Qt.ItemDataRole.UserRole))
            match_state = self.state_filter.currentIndex() == 0 or held == (self.state_filter.currentIndex() == 2)
            match_bucket = self.bucket_filter.currentIndex() == 0 or item.text(3) == self.bucket_filter.currentText()
            item.setHidden(not (match_state and match_bucket and query in ' '.join(item.text(c) for c in range(4)).casefold()))
        self.update_counts()

    def update_counts(self, *_):
        items = [self.table.topLevelItem(i) for i in range(self.table.topLevelItemCount())]
        held = sum(bool(i.data(0, Qt.ItemDataRole.UserRole)) for i in items)
        checked = [i for i in items if i.checkState(0) == Qt.CheckState.Checked]
        self.summary.setText(f'已安装 {len(items)} · 已冻结 {held} · 允许更新 {len(items)-held}')
        self.selection_summary.setText(f'显示 {sum(not i.isHidden() for i in items)} 项 · 已勾选 {len(checked)} 项（隐藏 {sum(i.isHidden() for i in checked)} 项）')

    def toggle_log(self, visible):
        self.log.setVisible(visible)
        self.log_toggle.setText('收起日志' if visible else '显示日志')

    def retry_last(self):
        if self.last_request:
            self.run(self.last_request[0], list(self.last_request[1]))

    def check(self, value):
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            if not value or not item.isHidden():
                item.setCheckState(0, Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)

    def update_all(self):
        if self.busy(): return
        if QMessageBox.question(self, '更新全部', '更新当前 Scoop 安装中的全部软件？已冻结的软件会跳过。') == QMessageBox.StandardButton.Yes:
            self.run('update', ['*'])

    def run(self, action, packages=None):
        if self.busy() or not self.root or not valid_root(self.root): return
        if packages is None:
            packages = [self.table.topLevelItem(i).text(0) for i in range(self.table.topLevelItemCount())
                        if self.table.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
        if action == 'status': packages = []
        elif not packages:
            self.status.setText('请先勾选软件。'); return
        if action not in {'status', 'update', 'hold', 'unhold'}: return
        if any(p != '*' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]*', p) for p in packages):
            self.status.setText('软件名称无效，请刷新。'); return
        if action == 'update' and packages != ['*']:
            held = {self.table.topLevelItem(i).text(0) for i in range(self.table.topLevelItemCount())
                    if self.table.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)}
            packages = [p for p in packages if p not in held]
            if not packages:
                self.status.setText('所选软件均已冻结，请先解除冻结。'); return
        self.last_request = (action, list(packages))
        self.retry.setEnabled(False)
        self.progress.show()
        self.log_toggle.setChecked(True)
        self.log.clear()
        self._ansi_pending = ""
        env = QProcessEnvironment.systemEnvironment()
        env.insert('SCOOP', str(self.root))
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.root))
        self.process.setProgram('powershell.exe')
        # Some Scoop versions stop their unhold pipeline when an app is already unheld.
        # Run each unhold independently so one package cannot skip the rest.
        self._pending_packages = list(packages) if action == 'unhold' else []
        self._batch_failed = False
        self._cancelled = False
        self.cancel_button.setEnabled(True)
        first = [self._pending_packages.pop(0)] if self._pending_packages else packages
        self._set_process_arguments(action, first)
        names = '全部软件' if packages == ['*'] else f'{len(packages)} 个软件'
        self.status.setText(('正在检查更新，结果显示在日志中…' if action == 'status' else f'正在执行 {action}：{names}'))
        for button in self.buttons: button.setEnabled(False)
        self.table.setEnabled(False)
        self.process.start()

    def cancel_operation(self):
        if not self.busy(): return
        self._cancelled = True
        self._pending_packages.clear()
        self.cancel_button.setEnabled(False)
        self.status.setText('正在中断，已完成的操作会保留…')
        if os.name == 'nt':
            started, _ = QProcess.startDetached('taskkill.exe', ['/PID', str(self.process.processId()), '/T', '/F'])
            if not started: self.process.kill()
        else:
            self.process.terminate()
        self.kill_timer.start(3000)

    def force_stop(self):
        if self.busy(): self.process.kill()

    def _set_process_arguments(self, action, packages):
        self.process.setArguments(['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                   '-File', str(scoop_script(self.root)), action, *packages])

    def read_output(self):
        data = bytes(self.process.readAllStandardOutput())
        try: text = data.decode('utf-8')
        except UnicodeDecodeError: text = data.decode('mbcs', errors='replace')
        text = self._ansi_pending + text
        self._ansi_pending = ""
        incomplete = re.search(r"\x1b(?:\[[0-?]*[ -/]*)?$", text)
        if incomplete:
            self._ansi_pending = text[incomplete.start():]
            text = text[:incomplete.start()]
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(text)

    def _remaining_packages(self, action, requested):
        expected = action == 'hold'
        states = {self.table.topLevelItem(i).text(0): bool(self.table.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole))
                  for i in range(self.table.topLevelItemCount())}
        return [name for name in requested if name not in states or states[name] != expected]

    def process_finished(self, code, status):
        self.kill_timer.stop()
        self.read_output()
        self._batch_failed |= code != 0 or status != QProcess.ExitStatus.NormalExit
        if self._pending_packages:
            self._set_process_arguments('unhold', [self._pending_packages.pop(0)])
            self.process.start()
            return
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.retry.setEnabled(self._batch_failed or self._cancelled)
        for button in self.buttons: button.setEnabled(True)
        self.table.setEnabled(True)
        self.refresh()
        success = not self._batch_failed
        if self._cancelled:
            if self.last_request and self.last_request[0] in {'hold', 'unhold'}:
                action, requested = self.last_request
                remaining = self._remaining_packages(action, requested)
                self.last_request = (action, remaining)
                self.retry.setEnabled(bool(remaining))
                self.status.setText(f'操作已中断，状态已刷新；{len(remaining)} 项尚未达到预期状态。')
            else:
                self.status.setText('操作已中断，状态已刷新；已完成的操作会保留。')
            return
        if self.last_request and self.last_request[0] in {'hold', 'unhold'}:
            action, requested = self.last_request
            failed = self._remaining_packages(action, requested)
            if failed:
                self.last_request = (action, failed)
                self.retry.setEnabled(True)
                self.status.setText(f'以下软件未达到预期状态：{"、".join(failed)}。请查看日志并重试。')
                return
        self.status.setText('命令已完成，状态已刷新。' if success else '操作未完成，请查看日志。')

    def error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._pending_packages.clear()
            self.cancel_button.setEnabled(False)
            self.progress.hide()
            self.retry.setEnabled(True)
            for button in self.buttons: button.setEnabled(True)
            self.table.setEnabled(True)
            self.status.setText('无法启动 PowerShell。')

    def reject(self):
        if self.busy():
            self.status.setText('操作正在进行，请完成后关闭。'); return
        super().reject()

    def closeEvent(self, event):
        if self.busy(): event.ignore()
        else: super().closeEvent(event)
