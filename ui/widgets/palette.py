"""Keyboard-first quick launcher using the same virtual entry surface."""
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QVBoxLayout, QHBoxLayout, QPushButton

from heapq import nsmallest
from core.search import SearchIndex
from ui.widgets.entry_view import EntryView, ENTRY_ROLE


class LaunchPalette(QDialog):
    launchRequested = Signal(object)
    actionRequested = Signal(str, object)

    def __init__(self, entries, notes, icon_for, dark, parent=None, *, index=None):
        super().__init__(parent)
        self.entries, self.notes = entries, notes
        self.index = index if index is not None else SearchIndex()
        self.setWindowTitle("快速启动")
        self.resize(680, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        title = QLabel("快速启动")
        title.setObjectName("ContentTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索工具、拼音或别名 · 标签:扫描 · 语言:python · 类型:文件")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(44)
        layout.addWidget(self.search)
        self.view = EntryView(icon_for, dark)
        self.view.set_grid(False)
        self.view.launchRequested.connect(self._launch)
        self.view.actionRequested.connect(self._action)
        layout.addWidget(self.view, 1)
        actions = QHBoxLayout()
        for label, action in [("启动方案", "plans"), ("浏览文件", "files"), ("带参数启动", "args"), ("打开目录", "folder")]:
            button = QPushButton(label)
            button.setObjectName("GhostBtn")
            button.clicked.connect(lambda _, a=action: self._selected_action(a))
            actions.addWidget(button)
        actions.addStretch(1)
        runtime = QPushButton("运行环境")
        runtime.setObjectName("GhostBtn")
        runtime.clicked.connect(self._runtime)
        actions.addWidget(runtime)
        layout.addLayout(actions)
        self.hint = QLabel()
        self.hint.setObjectName("ContentSub")
        layout.addWidget(self.hint)
        self.timer = QTimer(self, singleShot=True, interval=60)
        self.timer.timeout.connect(self._refresh)
        self.search.textChanged.connect(lambda: self.timer.start())
        self.search.installEventFilter(self)
        self._refresh()
        self.search.setFocus()

    def _refresh(self):
        query = self.search.text().strip()
        if query:
            self.index.ensure(self.entries, self.notes)
            ranked = self.index.ranked(query)
        else:
            ranked = [(entry, 0) for entry in self.entries]
        if query:
            top = nsmallest(80, ranked, key=lambda p: (-p[1], not p[0].pinned, -p[0].runs, p[0].name.casefold()))
        else:
            top = sorted(ranked, key=lambda p: p[0].name.casefold())
            top.sort(key=lambda p: (p[0].last_run or "") if not p[0].recent_hidden else "", reverse=True)
            top.sort(key=lambda p: not p[0].pinned)
            top = top[:80]
        self.view.setProperty("searchQuery", query)
        self.view.entries_model.replace([entry for entry, _ in top])
        if ranked:
            self.view.setCurrentIndex(self.view.model().index(0, 0))
        self.hint.setText(f"{len(ranked)} 个结果" + (" · 显示前 80 项" if len(ranked) > 80 else "")
                          if ranked else "无搜索结果")

    def _launch(self, entry):
        self.accept()
        self.launchRequested.emit(entry)

    def _selected_action(self, action):
        entry = self.view.currentIndex().data(ENTRY_ROLE)
        if entry: self._action(action, entry)

    def _runtime(self):
        entry = self.view.currentIndex().data(ENTRY_ROLE)
        if entry:
            self._action("runtime", entry)

    def _action(self, action, entry):
        self.accept()
        self.actionRequested.emit(action, entry)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.timer.isActive():
                    self.timer.stop()
                    self._refresh()
                entry = self.view.currentIndex().data(ENTRY_ROLE)
                if entry:
                    modifiers = event.modifiers()
                    if modifiers & Qt.KeyboardModifier.ControlModifier:
                        self._action("admin", entry)
                    elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self._action("args", entry)
                    else:
                        self._launch(entry)
                return True
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.timer.isActive():
                    self.timer.stop()
                    self._refresh()
                row = self.view.currentIndex().row()
                row += 1 if event.key() == Qt.Key.Key_Down else -1
                row = max(0, min(row, self.view.model().rowCount()-1))
                self.view.setCurrentIndex(self.view.model().index(row, 0))
                return True
        return super().eventFilter(obj, event)
