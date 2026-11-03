"""Tool organization and lazy file browsing using the existing Qt theme."""
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QDir
from PySide6.QtWidgets import QFileSystemModel
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QFileDialog, QInputDialog, QMessageBox, QTreeView, QPlainTextEdit, QSplitter, QCheckBox, QMenu, QApplication, QComboBox, QTabWidget)
from core.file_management import inside, valid_name, relocate, import_paths
from core import launcher
from ui.flowlayout import FlowLayout


class ImportThread(QThread):
    completed = Signal(str)

    def __init__(self, root, destination, paths, parent, skip_dependencies=True):
        super().__init__(parent)
        self.root, self.destination, self.paths = root, destination, paths
        self.skip_dependencies = skip_dependencies
        self.skipped = set()

    def run(self):
        try:
            import_paths(self.root, self.destination, self.paths, self.skip_dependencies, self.skipped)
            self.completed.emit('')
        except Exception as error:
            self.completed.emit(str(error))


class ToolFilesDialog(QDialog):
    def __init__(self, entry, settings, parent=None, db=None):
        super().__init__(parent)
        self.setWindowTitle(f'文件 · {entry.name}')
        self.resize(950, 650)
        self.root = entry.path if entry.is_dir else entry.path.parent
        self.settings = settings
        self.db = db if db is not None else getattr(parent, 'db', None)
        self.changed = False
        self.current_directory = self.root
        layout = QVBoxLayout(self)
        self.path_label = QLabel(str(self.root))
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        navigation = QHBoxLayout()
        self.up_button = QPushButton('上一级')
        self.up_button.clicked.connect(lambda: self.navigate(self.current_directory.parent))
        navigation.addWidget(self.up_button)
        root_button = QPushButton('根目录')
        root_button.clicked.connect(lambda: self.navigate(self.root))
        navigation.addWidget(root_button)
        enter_button = QPushButton('进入文件夹')
        enter_button.clicked.connect(self.enter_directory)
        navigation.addWidget(enter_button)
        navigation.addStretch(1)
        layout.addLayout(navigation)
        self.up_button.setEnabled(False)
        search = QLineEdit(); search.setPlaceholderText('筛选文件名，例如 *.json（展开目录查看文件）')
        layout.addWidget(search)
        split = QSplitter()
        self.tree = QTreeView()
        self.model = QFileSystemModel(self)
        self.model.setOption(QFileSystemModel.Option.DontWatchForChanges, True)
        self.model.setReadOnly(True)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilterDisables(False)
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.setRootPath(str(self.root)))
        self.tree.setColumnWidth(0, 310)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('选择文本文件预览；最多读取前 64 KB。')
        split.addWidget(self.tree); split.addWidget(self.preview)
        split.setStretchFactor(0, 7)
        split.setStretchFactor(1, 3)
        split.setSizes([700, 300])
        layout.addWidget(split, 1)
        search.textChanged.connect(lambda value: self.model.setNameFilters([value] if value else []))
        self.tree.selectionModel().currentChanged.connect(self.show_preview)
        row = QHBoxLayout()
        for name, slot in [('打开 / 定位', self.open), ('复制路径', self.copy), ('在此打开终端', self.terminal)]:
            button = QPushButton(name); button.clicked.connect(slot); row.addWidget(button)
        layout.addLayout(row)
        self.tree.doubleClicked.connect(self.enter_or_open)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.file_menu)
        self.tree.setSortingEnabled(True)
        sorting = QHBoxLayout()
        sorting.addWidget(QLabel('排序方式'))
        self.sort_field = QComboBox()
        for label, column in [('名称', 0), ('大小', 1), ('类型', 2), ('修改时间', 3)]:
            self.sort_field.addItem(label, column)
        self.sort_direction = QComboBox()
        self.sort_direction.addItems(['升序', '降序'])
        sorting.addWidget(self.sort_field)
        sorting.addWidget(self.sort_direction)
        sorting.addStretch(1)
        layout.addLayout(sorting)
        self.sort_field.setCurrentIndex(max(0, self.sort_field.findData(settings.get('file_sort_column', 0))))
        self.sort_direction.setCurrentIndex(int(bool(settings.get('file_sort_descending', False))))
        self.sort_field.currentIndexChanged.connect(self.apply_sort)
        self.sort_direction.currentIndexChanged.connect(self.apply_sort)
        self.tree.header().sortIndicatorChanged.connect(self.sync_sort)
        self.apply_sort()

        tools = QHBoxLayout()
        for label, slot in [('新建文件夹', lambda: self.create_item(True)),
                            ('新建文件', lambda: self.create_item(False)),
                            ('重命名', self.rename_file), ('刷新', self.refresh_files),
                            ('属性', self.file_properties)]:
            button = QPushButton(label); button.clicked.connect(slot); tools.addWidget(button)
        layout.addLayout(tools)

    def apply_sort(self, *_):
        self.tree.sortByColumn(self.sort_field.currentData(),
            Qt.SortOrder.DescendingOrder if self.sort_direction.currentIndex() else Qt.SortOrder.AscendingOrder)

    def sync_sort(self, column, order):
        self.sort_field.blockSignals(True)
        self.sort_direction.blockSignals(True)
        self.sort_field.setCurrentIndex(max(0, self.sort_field.findData(column)))
        descending = order == Qt.SortOrder.DescendingOrder
        self.sort_direction.setCurrentIndex(int(descending))
        self.sort_field.blockSignals(False)
        self.sort_direction.blockSignals(False)
        self.settings.update({'file_sort_column': column, 'file_sort_descending': descending})

    def navigate(self, path):
        path = Path(path)
        try:
            resolved = path.resolve()
            root = self.root.resolve()
            if not path.is_dir() or (resolved != root and not resolved.is_relative_to(root)):
                return
            self.current_directory = path
            self.tree.setRootIndex(self.model.setRootPath(str(path)))
            self.tree.clearSelection()
            self.tree.setCurrentIndex(self.model.index(''))
            self.preview.clear()
            self.path_label.setText(str(path))
            self.up_button.setEnabled(resolved != root)
        except OSError as error:
            QMessageBox.warning(self, '无法进入目录', str(error))

    def enter_directory(self):
        path = self.selected()
        if path.is_dir(): self.navigate(path)

    def enter_or_open(self, index):
        path = Path(self.model.filePath(index))
        if path.is_dir(): self.navigate(path)
        else: self.open()

    def refresh_files(self):
        # Recreate the root listing without enabling persistent filesystem watches.
        self.model.setRootPath('')
        self.tree.setRootIndex(self.model.setRootPath(str(self.current_directory)))

    def create_item(self, directory):
        selected = self.selected()
        destination = selected if selected.is_dir() else selected.parent
        name, ok = QInputDialog.getText(self, '新建文件夹' if directory else '新建文件', '名称')
        if not ok: return
        try:
            target = inside(self.root, destination / valid_name(name))
            if directory: target.mkdir()
            else:
                with target.open('x', encoding='utf-8'): pass
            self.changed = True
            self.refresh_files()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '未完成', str(error))

    def rename_file(self):
        path = self.selected()
        if path == self.root:
            QMessageBox.information(self, '重命名', '请选择目录中的文件或子文件夹。'); return
        name, ok = QInputDialog.getText(self, '重命名', '新名称', text=path.name)
        if not ok or name == path.name: return
        try:
            inside(self.root, path)
            source = path
            target = path.with_name(valid_name(name))
            inside(self.root, target)
            if target.exists(): raise ValueError('目标名称已存在')
            if self.db is not None: relocate(self.db, self.root, [(source, target)])
            else: source.rename(target)
            self.changed = True
            self.refresh_files()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '未完成', str(error))

    def file_properties(self):
        from datetime import datetime
        path = self.selected()
        try:
            info = path.stat()
            kind = '文件夹' if path.is_dir() else f'文件 · {info.st_size:,} 字节'
            modified = datetime.fromtimestamp(info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            QMessageBox.information(self, '属性', f'{path.name}\n{path}\n{kind}\n修改时间：{modified}')
        except OSError as error: QMessageBox.warning(self, '无法读取', str(error))

    def file_menu(self, point):
        index = self.tree.indexAt(point)
        if index.isValid(): self.tree.setCurrentIndex(index)
        menu = QMenu(self)
        for label, slot in [('打开 / 定位', self.open), ('复制路径', self.copy),
                            ('在此打开终端', self.terminal), ('重命名', self.rename_file),
                            ('新建文件夹', lambda: self.create_item(True)),
                            ('新建文件', lambda: self.create_item(False)),
                            ('刷新', self.refresh_files), ('属性', self.file_properties)]:
            menu.addAction(label, slot)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def selected(self):
        index = self.tree.currentIndex()
        return Path(self.model.filePath(index)) if index.isValid() else self.current_directory

    def show_preview(self, *_):
        path = self.selected()
        if not path.is_file(): self.preview.clear(); return
        if path.suffix.lower() not in {'.txt', '.md', '.json', '.yaml', '.yml', '.ini', '.toml', '.cfg', '.log', '.py', '.js', '.ps1', '.bat', '.cmd', '.xml', '.csv', '.conf'}:
            self.preview.setPlainText('此文件不支持文本预览。'); return
        try:
            with path.open('rb') as stream: data = stream.read(65537)
            text = data[:65536].decode('utf-8-sig', errors='replace')
            self.preview.setPlainText(text + ('\n…预览已截断' if len(data) > 65536 else ''))
        except OSError as error: self.preview.setPlainText(str(error))

    def open(self):
        try: launcher.open_in_explorer(self.selected())
        except OSError as error: QMessageBox.warning(self, '无法打开', str(error))

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(str(self.selected()))

    def terminal(self):
        from core.terminal import launch_terminal
        path = self.selected()
        cwd = path if path.is_dir() else path.parent
        try:
            launch_terminal(self.settings.get('terminal', ''), self.settings.get('terminal_args', ''), cwd, ['cmd.exe', '/k'])
        except (RuntimeError, OSError, ValueError) as error: QMessageBox.warning(self, '无法打开终端', str(error))


class FileManagerDialog(QDialog):
    def __init__(self, root, categories, entries, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle('文件管理')
        self.resize(920, 650)
        self.root, self.categories, self.entries, self.db = Path(root), categories, entries, db
        self.worker = None
        self.changed = False
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        self.hint = QLabel('选择多项批量整理；拖入文件或文件夹可复制到分类，原文件保留。')
        self.hint.setWordWrap(True); layout.addWidget(self.hint)
        self.skip_dependencies = QCheckBox('导入时跳过 node_modules 和 Python / 检查器缓存')
        self.skip_dependencies.setChecked(True)
        self.skip_dependencies.setToolTip('保留源码、package.json 和锁文件。需要直接使用已安装依赖时，请使用目录快捷入口，或取消此选项完整复制。')
        layout.addWidget(self.skip_dependencies)
        search = QLineEdit(); search.setPlaceholderText('搜索名称或路径')
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self.filter); layout.addWidget(search)
        self.search = search
        self.tree = QTreeWidget(); self.tree.setObjectName("ToolHealth"); self.tree.setHeaderLabels(['工具 / 分类', '路径', '标签'])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False); self.tree.setColumnWidth(0, 230); self.tree.setColumnWidth(1, 430)
        layout.addWidget(self.tree, 1)
        seen = set()
        for category in categories:
            if category.path != self.root:
                self.add_row(category.path, '分类', None); seen.add(category.path)
        for entry in entries:
            if entry.path not in seen: self.add_row(entry.path, ', '.join(entry.tags), entry)
        self.selection_hint = QLabel('选择条目后可重命名、移动或添加标签。')
        layout.addWidget(self.selection_hint)
        self.tree.itemSelectionChanged.connect(self.update_selection)
        row = FlowLayout(h_spacing=8, v_spacing=8)
        self.buttons = []
        for name, slot in [('导入文件', self.import_files), ('导入文件夹', self.import_folder),
                           ('新建分类', self.new_category), ('重命名', self.rename),
                           ('移动到分类', self.move), ('批量标签', self.tags)]:
            button = QPushButton(name); button.clicked.connect(slot); row.addWidget(button); self.buttons.append(button)
        layout.addLayout(row)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.update_selection()

    def update_selection(self):
        selected = self.selected()
        count = len(selected)
        self.selection_hint.setText(f'已选择 {count} 项' if count else '选择条目后可重命名、移动或添加标签。')
        self.buttons[3].setEnabled(count == 1)
        self.buttons[4].setEnabled(count > 0)
        self.buttons[5].setEnabled(any(entry is not None for _, entry in selected))

    def context_menu(self, point):
        item = self.tree.itemAt(point)
        if item is None: return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        for button in self.buttons[3:]:
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def add_row(self, path, tags, entry):
        item = QTreeWidgetItem([path.name, str(path), tags])
        item.setData(0, Qt.ItemDataRole.UserRole, (path, entry)); self.tree.addTopLevelItem(item)

    def filter(self, text):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            hidden = text.casefold() not in (item.text(0)+' '+item.text(1)).casefold()
            if hidden: item.setSelected(False)
            item.setHidden(hidden)

    def selected(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]

    def destination(self):
        paths = [self.root] + [c.path for c in self.categories
                               if c.path != self.root and c.path.is_dir()
                               and c.path.resolve().is_relative_to(self.root.resolve())]
        labels = ['工作区根目录'] + [str(p.relative_to(self.root)) for p in paths[1:]]
        label, ok = QInputDialog.getItem(self, '选择分类', '目标位置', labels, 0, False)
        return paths[labels.index(label)] if ok else None

    def operation(self, callback):
        try: callback()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '未完成', str(error)); return
        self.changed = True
        self.accept()

    def new_category(self):
        destination = self.destination()
        if destination is None: return
        name, ok = QInputDialog.getText(self, '新建分类', '分类名称')
        if ok: self.operation(lambda: inside(self.root, destination / valid_name(name)).mkdir())

    def rename(self):
        selected = self.selected()
        if len(selected) != 1:
            QMessageBox.information(self, '重命名', '请选择一项工具或分类。'); return
        path, _ = selected[0]
        name, ok = QInputDialog.getText(self, '重命名', '新名称', text=path.name)
        if ok and name != path.name:
            self.operation(lambda: relocate(self.db, self.root, [(path, path.with_name(valid_name(name)))]))

    def move(self):
        selected = self.selected()
        if not selected: return
        destination = self.destination()
        if destination is not None:
            self.operation(lambda: relocate(self.db, self.root, [(p, destination/p.name) for p, _ in selected]))

    def tags(self):
        entries = [entry for _, entry in self.selected() if entry]
        if not entries:
            QMessageBox.information(self, '批量标签', '请选择工具条目。'); return
        text, ok = QInputDialog.getText(self, '批量标签', '追加标签（逗号分隔，保留原标签）')
        if not ok: return
        tags = [t.strip() for t in text.replace('，', ',').split(',') if t.strip()]
        def save():
            for entry in entries: self.db.set_tags(entry.path, list(dict.fromkeys(entry.tags + tags)))
        self.operation(save)

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择要导入的文件')
        if paths: self.import_items(paths)

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择要导入的工具文件夹')
        if path: self.import_items([path])

    def import_items(self, paths):
        if self.worker is not None: return
        destination = self.destination()
        if destination is None: return
        self.hint.setText('正在复制，请等待完成…')
        for button in self.buttons: button.setEnabled(False)
        self.skip_dependencies.setEnabled(False)
        self.worker = ImportThread(self.root, destination, paths, self, self.skip_dependencies.isChecked())
        self.worker.completed.connect(self.import_done)
        self.worker.start()

    def import_done(self, error):
        self.worker.wait()
        skipped = self.worker.skipped
        self.worker.deleteLater(); self.worker = None
        self.skip_dependencies.setEnabled(True)
        if error:
            self.hint.setText('导入未完成，可调整后重试。')
            for button in self.buttons: button.setEnabled(True)
            self.update_selection()
            QMessageBox.warning(self, '导入未完成', error)
        else:
            if skipped:
                QMessageBox.information(self, '导入完成', '已跳过：' + '、'.join(sorted(skipped)) + '\n源码与依赖清单已保留，运行前请在新目录安装所需依赖。')
            self.changed = True; self.accept()

    def reject(self):
        if self.worker is None: super().reject()

    def closeEvent(self, event):
        if self.worker is not None: event.ignore()
        else: super().closeEvent(event)

    def dragEnterEvent(self, event):
        if self.worker is None and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.worker is None:
            self.import_items([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()


class UnifiedFileManager(QDialog):
    """One entry point for workspace organization and file browsing."""
    def __init__(self, root, categories, entries, db, settings, parent=None, entry=None):
        super().__init__(parent)
        from types import SimpleNamespace
        self.db = db
        self.setWindowTitle('文件管理')
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.organizer = FileManagerDialog(root, categories, entries, db, self)
        target = entry or SimpleNamespace(name=Path(root).name, path=Path(root), is_dir=True)
        self.browser = ToolFilesDialog(target, settings, self, db=db)
        for widget in (self.organizer, self.browser):
            widget.setWindowFlags(Qt.WindowType.Widget)
        self.tabs.addTab(self.browser, '浏览文件')
        self.tabs.addTab(self.organizer, '整理工作区')
        self.tabs.setCurrentWidget(self.browser if entry else self.organizer)
        self.organizer.accepted.connect(self.accept)
        self.tabs.currentChanged.connect(self._sync_tabs)

    def _sync_tabs(self, index):
        if self.tabs.widget(index) is not self.organizer or not self.browser.changed:
            return
        # Browser edits can invalidate the organizer's original paths. Reload on
        # close instead of offering stale paths for a destructive operation.
        self.organizer.setEnabled(False)
        self.organizer.hint.setText('文件已更改，请关闭并重新打开文件管理，继续整理。')

    @property
    def changed(self):
        return self.organizer.changed or self.browser.changed

    def import_items(self, paths):
        self.tabs.setCurrentWidget(self.organizer)
        self.organizer.import_items(paths)

    def reject(self):
        if self.organizer.worker is None:
            super().reject()

    def closeEvent(self, event):
        if self.organizer.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)
