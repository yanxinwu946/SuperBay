"""Named commands and explicit, previewable parameter entry."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import QSignalBlocker, Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QListWidget, QPushButton, QComboBox,
    QCheckBox, QMessageBox, QFileDialog, QDialogButtonBox, QWidget, QSplitter, QInputDialog, QScrollArea, QFrame)
from core.command_templates import fields, render


class PathInput(QLineEdit):
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and len(event.mimeData().urls()) == 1 and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            self.setText(event.mimeData().urls()[0].toLocalFile())
            event.acceptProposedAction()
        else: super().dropEvent(event)


class ParametersDialog(QDialog):
    def __init__(self, command, parent=None, defaults=None, plan_name=""):
        super().__init__(parent)
        self.setWindowTitle('运行方案' + (' · ' + plan_name if plan_name else ''))
        self.resize(620, 400)
        self.command = command
        self.inputs = {}
        layout = QVBoxLayout(self)
        title = QLabel(plan_name or '填写本次参数'); title.setObjectName('ContentTitle')
        layout.addWidget(title)
        guide = QLabel('上次填写的内容会保留；文件和目录也可以直接拖入。')
        guide.setWordWrap(True); guide.setObjectName('ContentSub'); layout.addWidget(guide)
        form_body = QWidget()
        form = QFormLayout(form_body)
        form.setContentsMargins(0, 4, 0, 4)
        for name, kind in fields(command):
            row = QHBoxLayout()
            edit = PathInput()
            edit.setText((defaults or {}).get(name, ""))
            edit.setPlaceholderText({'text':'请输入' + name, 'file':'选择或拖入文件', 'dir':'选择或拖入目录'}[kind])
            self.inputs[name] = edit
            row.addWidget(edit)
            if kind in ('file', 'dir'):
                button = QPushButton('选择…')
                button.setAutoDefault(False)
                button.clicked.connect(lambda _, e=edit, k=kind: self.pick(e, k))
                row.addWidget(button)
            edit.textChanged.connect(self.preview)
            form.addRow(name, row)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(form_body)
        layout.addWidget(scroll, 2)
        self.validation = QLabel(); self.validation.setWordWrap(True)
        self.validation.setObjectName('ContentSub'); layout.addWidget(self.validation)
        layout.addWidget(QLabel('将执行的命令'))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('运行')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('返回修改')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.preview()

    def pick(self, edit, kind):
        value = (QFileDialog.getExistingDirectory(self, '选择目录') if kind == 'dir'
                 else QFileDialog.getOpenFileName(self, '选择文件')[0])
        if value: edit.setText(value)

    def preview(self):
        try:
            self.values = {k: v.text() for k, v in self.inputs.items()}
            for name, kind in fields(self.command):
                value = self.values[name].strip()
                if kind == 'file' and not Path(value).is_file(): raise ValueError(f'{name}：请选择存在的文件')
                if kind == 'dir' and not Path(value).is_dir(): raise ValueError(f'{name}：请选择存在的目录')
            self.result_command = render(self.command, self.values)
            self.output.setPlainText(self.result_command)
            self.validation.setText('参数已就绪')
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as error:
            self.output.setPlainText(self.command)
            self.validation.setText(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)


class LaunchPlansDialog(QDialog):
    plansChanged = Signal(object)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle(f'启动方案 · {entry.name}')
        self.resize(900, 620)
        self.setMinimumSize(760, 520)
        self.plans = deepcopy(entry.launch_plans)
        self.active = -1
        self.run_plan = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        title = QLabel(entry.name + ' 的启动方案')
        title.setObjectName('ContentTitle')
        layout.addWidget(title)
        hint = QLabel('把常用命令保存成不同用法，运行时只需填写目标或选择文件。')
        hint.setObjectName('ContentSub'); hint.setWordWrap(True)
        layout.addWidget(hint)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        left = QWidget(); left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 12, 12, 0)
        self.list = QListWidget(); self.list.setObjectName('ToolHealth')
        self.list.addItems([p['name'] for p in self.plans])
        left_layout.addWidget(self.list, 1)
        self.new_button = self.button('新建方案', self.add)
        left_layout.addWidget(self.new_button)
        small = QHBoxLayout()
        self.copy_button = self.button('复制', self.duplicate)
        self.delete_button = self.button('删除', self.remove)
        small.addWidget(self.copy_button); small.addWidget(self.delete_button)
        left_layout.addLayout(small)
        split.addWidget(left)
        right = QWidget(); right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 12, 0, 0)
        self.empty = QWidget(); empty_layout = QVBoxLayout(self.empty)
        empty_layout.addStretch(1)
        empty_title = QLabel('先保存一种常用用法')
        empty_title.setObjectName('ContentTitle'); empty_layout.addWidget(empty_title)
        empty_note = QLabel('例如「查看帮助」「常用扫描」或「导出结果」。\n可从工具当前命令开始，再按需添加参数。')
        empty_note.setWordWrap(True); empty_layout.addWidget(empty_note)
        empty_layout.addWidget(self.button('从当前命令创建', self.add))
        empty_layout.addStretch(1)
        right_layout.addWidget(self.empty, 1)
        self.editor = QWidget(); editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(QLabel('方案名称'))
        self.name = QLineEdit(); self.name.setPlaceholderText('例如：常用扫描、查看帮助')
        editor_layout.addWidget(self.name)
        editor_layout.addWidget(QLabel('启动命令'))
        self.command = QPlainTextEdit()
        self.command.setPlaceholderText('从当前启动命令开始，或粘贴一条已验证可用的命令。')
        editor_layout.addWidget(self.command, 1)
        inserts = QHBoxLayout()
        inserts.addWidget(QLabel('插入参数'))
        for label, kind in [('文本', 'text'), ('文件', 'file'), ('目录', 'dir')]:
            inserts.addWidget(self.button(label, lambda _, k=kind: self.insert_parameter(k)))
        editor_layout.addLayout(inserts)
        self.parameters_hint = QLabel()
        self.parameters_hint.setObjectName('ContentSub'); self.parameters_hint.setWordWrap(True)
        editor_layout.addWidget(self.parameters_hint)
        self.advanced_button = self.button('高级选项', self.toggle_advanced)
        editor_layout.addWidget(self.advanced_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced = QWidget(); advanced_layout = QFormLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.shell = QComboBox(); self.shell.addItems(['cmd', 'powershell'])
        self.admin = QCheckBox('以管理员身份运行')
        advanced_layout.addRow('运行方式', self.shell); advanced_layout.addRow('', self.admin)
        editor_layout.addWidget(self.advanced); self.advanced.hide()
        row = QHBoxLayout()
        self.save_button = self.button('保存方案', self.save)
        self.run_button = self.button('保存并运行', self.run)
        self.run_button.setObjectName('PrimaryBtn'); self.run_button.setDefault(True)
        row.addWidget(self.save_button); row.addWidget(self.run_button)
        editor_layout.addLayout(row)
        right_layout.addWidget(self.editor, 1)
        split.addWidget(right); split.setSizes([230, 620])
        footer = QHBoxLayout()
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setObjectName('ContentSub')
        footer.addWidget(self.status, 1)
        footer.addWidget(self.button('完成', self.reject))
        layout.addLayout(footer)
        self.list.currentRowChanged.connect(self.select)
        self.command.textChanged.connect(self.changed)
        self.name.textChanged.connect(self.changed)
        self.shell.currentTextChanged.connect(self.changed)
        self.admin.toggled.connect(self.changed)
        if self.plans: self.list.setCurrentRow(0)
        else: self.load(-1)

    @staticmethod
    def button(label, slot):
        button = QPushButton(label); button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def snapshot(self):
        return (self.name.text().strip(), self.command.toPlainText().strip(), self.shell.currentText(), self.admin.isChecked())

    def dirty(self):
        if not 0 <= self.active < len(self.plans): return False
        saved = self.plans[self.active]
        if saved.get('_draft'): return True
        return self.snapshot() != (saved.get('name', ''), saved.get('command', ''), saved.get('shell_type', 'cmd'), saved.get('admin', False))

    def changed(self, *_):
        names = [name for name, _ in fields(self.command.toPlainText())]
        self.parameters_hint.setText('运行时填写：' + '、'.join(names) if names else '点「插入参数」，把每次变化的目标、文件或目录留到运行时填写。')
        self.status.setText('有未保存的修改' if self.dirty() else '')

    def confirm_edit(self):
        if not self.dirty(): return True
        answer = QMessageBox.question(self, '保存修改', '当前方案有未保存的修改，是否保存？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save: return bool(self.save())
        return answer == QMessageBox.StandardButton.Discard

    def reject(self):
        if self.confirm_edit(): super().reject()

    def select(self, row):
        if not self.confirm_edit():
            with QSignalBlocker(self.list): self.list.setCurrentRow(self.active)
            return
        self.load(row)

    def load(self, row):
        self.active = row
        has_plan = 0 <= row < len(self.plans)
        self.editor.setVisible(has_plan); self.empty.setVisible(not has_plan)
        self.copy_button.setEnabled(has_plan); self.delete_button.setEnabled(has_plan)
        plan = self.plans[row] if has_plan else {}
        self.name.setText(plan.get('name', ''))
        self.command.setPlainText(plan.get('command', ''))
        cursor = self.command.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.command.setTextCursor(cursor)
        self.shell.setCurrentText(plan.get('shell_type', self.entry.shell_type))
        self.admin.setChecked(plan.get('admin', self.entry.admin))
        self.changed()

    def unique_name(self, base):
        names = {p['name'] for p in self.plans}
        if base not in names: return base
        number = 2
        while f'{base} {number}' in names: number += 1
        return f'{base} {number}'

    def append(self, plan):
        plan['_draft'] = True
        self.plans.append(plan)
        with QSignalBlocker(self.list):
            self.list.addItem(plan['name']); self.list.setCurrentRow(len(self.plans)-1)
        self.load(len(self.plans)-1)
        self.name.setFocus(); self.name.selectAll()
        self.status.setText('填写命令后点击「保存方案」')

    def add(self):
        if not self.confirm_edit(): return
        command = self.entry.command or ''
        if not command:
            from core.toolbox import suggestions
            try:
                choices = suggestions(self.entry)
                if len(choices) == 1: command = choices[0][1]
            except OSError: pass
        self.append(dict(name=self.unique_name('默认启动' if not self.plans else '新方案'),
            command=command, shell_type=self.entry.shell_type, admin=self.entry.admin))

    def duplicate(self):
        if self.active < 0 or not self.confirm_edit(): return
        plan = deepcopy(self.plans[self.active])
        plan['name'] = self.unique_name(plan['name'] + ' 副本')
        self.append(plan)
        self.status.setText('已复制，修改名称或参数后保存')

    def publish(self):
        self.plansChanged.emit(deepcopy([p for p in self.plans if p.get('name') and p.get('command') and not p.get('_draft')]))

    def remove(self):
        if self.active < 0: return
        row = self.active
        if self.plans[row].get('command') and QMessageBox.question(self, '删除方案',
                f'删除「{self.plans[row]["name"]}」方案？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self.plans.pop(row); self.active = -1
        with QSignalBlocker(self.list): self.list.takeItem(row)
        self.load(self.list.currentRow()); self.publish()
        self.status.setText('已删除方案')

    def insert_parameter(self, kind):
        if self.active < 0: return
        label = {'text':'目标', 'file':'文件', 'dir':'目录'}[kind]
        name, ok = QInputDialog.getText(self, '添加参数', '给这个参数起个名字', text=label)
        if not ok: return
        name = name.strip()
        marker = '{' + name + (':' + kind if kind != 'text' else '') + '}'
        if fields(marker) != [(name, kind)]:
            self.status.setText('参数名请使用中文、字母、数字或下划线'); return
        if kind != 'text': marker = '"' + marker + '"'
        cursor = self.command.textCursor()
        text = self.command.toPlainText()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        if start and not text[start-1].isspace() and text[start-1] not in '=\"\'': marker = ' ' + marker
        if end < len(text) and not text[end].isspace(): marker += ' '
        cursor.insertText(marker); self.command.setTextCursor(cursor); self.command.setFocus()

    def toggle_advanced(self):
        show = self.advanced.isHidden()
        self.advanced.setVisible(show)
        self.advanced_button.setText('收起高级选项' if show else '高级选项')

    def save(self):
        if self.active < 0: return False
        name, command, shell, admin = self.snapshot()
        if not name or not command:
            self.status.setText('请填写方案名称和启动命令')
            (self.name if not name else self.command).setFocus()
            return False
        if any(i != self.active and p['name'] == name for i, p in enumerate(self.plans)):
            self.status.setText('这个方案名称已存在，请换一个名称'); self.name.setFocus(); return False
        self.plans[self.active] = dict(name=name, command=command, shell_type=shell, admin=admin,
            values=self.plans[self.active].get('values', {}))
        self.list.item(self.active).setText(name)
        self.publish(); self.status.setText('已保存 · ' + name)
        return True

    def run(self):
        if self.save():
            self.run_plan = self.plans[self.active]
            self.accept()
