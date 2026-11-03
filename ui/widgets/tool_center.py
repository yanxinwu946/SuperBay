"""Workspace, diagnostics and portability interfaces."""
import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, QRect, QRectF
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,
    QLineEdit,QFormLayout,QTabWidget,QPlainTextEdit,QFileDialog,QInputDialog,
    QMessageBox,QTreeWidget,QTreeWidgetItem,QAbstractItemView,QFrame,QListWidgetItem,QStyledItemDelegate,QStyle,QApplication,
    QHeaderView)
from ui.misc import WRAP_FLAGS, wrapped_text_height
from ui.theme import resolve_theme
from core import toolbox,launch_history
from PySide6.QtGui import QColor,QFont,QFontMetrics,QPainter,QPen


def button(text, callback, layout):
    item=QPushButton(text);item.setObjectName('GhostBtn');item.clicked.connect(callback);layout.addWidget(item);return item


def heading(dialog,title,subtitle):
    dialog.setWindowTitle(title);dialog.resize(840,620)
    layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
    text=QLabel(title);text.setObjectName('ContentTitle');layout.addWidget(text)
    note=QLabel(subtitle);note.setWordWrap(True);note.setObjectName('ContentSub');layout.addWidget(note)
    from ui.native_frame import apply_native_frame
    settings=dialog.parent().settings
    apply_native_frame(dialog,resolve_theme(settings.get('theme','system'))=='dark')
    return layout


class _ShortcutDelegate(QStyledItemDelegate):
    def _layout(self, option, value):
        widget=option.widget or self.parent()
        width=widget.viewport().width() if widget else 720
        status='当前' if value['active'] else ('不可用' if not value['available'] else '')
        status_font=QFont(option.font);status_font.setPixelSize(12)
        status_width=QFontMetrics(status_font).horizontalAdvance(status)+20 if status else 0
        left=52
        title_width=max(1,width-left-status_width-24)
        name_font=QFont(option.font);name_font.setPixelSize(13);name_font.setWeight(QFont.Weight.DemiBold)
        name_height=wrapped_text_height(value['name'],title_width,name_font)
        path_width=max(1,width-left-16)
        path_font=QFont(option.font);path_font.setPixelSize(12)
        path_height=wrapped_text_height(value['path'],path_width,path_font)
        height=max(76,10+name_height+4+path_height+10)
        return status,status_width,title_width,name_font,name_height,path_width,path_font,path_height,height

    def sizeHint(self, option, index):
        value=index.data(Qt.ItemDataRole.UserRole)
        return QSize(240, self._layout(option, value)[-1] if value else 76)

    def paint(self, painter, option, index):
        value=index.data(Qt.ItemDataRole.UserRole)
        if not value:return
        dark=QApplication.instance().property('superguiTheme')=='dark'
        text=QColor('#e6e9ee' if dark else '#2b323d')
        muted=QColor('#9aa3af' if dark else '#6b7380')
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect=option.rect.adjusted(4,3,-4,-3)
        status,status_width,title_width,name_font,name_height,path_width,path_font,path_height,_=self._layout(option,value)
        painter.save();painter.setClipRect(option.rect);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(QPen(QColor(255,255,255,25) if dark else QColor(43,50,61,20),1) if selected else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255,255,255,26 if dark else 210) if selected else QColor(255,255,255,14 if dark else 120))
            painter.drawRoundedRect(QRectF(rect),12,12)
        from ui.misc import svg_icon,SVG_FOLDER
        svg_icon(SVG_FOLDER,muted.name(),20).paint(painter,QRect(rect.x()+16,rect.center().y()-10,20,20))
        left=rect.x()+52
        status_font=QFont(option.font);status_font.setPixelSize(12);painter.setFont(status_font)
        painter.setPen(muted)
        if status:painter.drawText(QRect(rect.right()-status_width-12,rect.y()+12,status_width,22),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,status)
        painter.setFont(name_font);painter.setPen(text)
        title_rect=QRect(left,rect.y()+10,title_width,name_height)
        painter.drawText(title_rect,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|WRAP_FLAGS,value['name'])
        painter.setFont(path_font);painter.setPen(muted)
        path_rect=QRect(left,rect.y()+10+name_height+4,path_width,path_height)
        painter.drawText(path_rect,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|WRAP_FLAGS,value['path'])
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QPen(muted,1))
            painter.drawRoundedRect(QRectF(rect.adjusted(1,1,-1,-1)),11,11)
        painter.restore()


class WorkspaceDialog(QDialog):
    def __init__(self, main):
        super().__init__(main);self.main=main
        self.setObjectName('ShortcutManager')
        layout=heading(self,'快捷入口','把常用目录放在手边。切换入口时，只打开对应目录。')
        self.resize(720,540);self.setMinimumSize(560,400)
        card=QFrame();card.setObjectName('ShortcutCard')
        content=QVBoxLayout(card);content.setContentsMargins(12,12,12,12);content.setSpacing(8)
        toolbar=QHBoxLayout();toolbar.setContentsMargins(8,0,8,0)
        self.count=QLabel();self.count.setObjectName('ShortcutCount');toolbar.addWidget(self.count);toolbar.addStretch()
        button('添加入口',self.add,toolbar);content.addLayout(toolbar)
        self.list=QListWidget();self.list.setObjectName('ShortcutList')
        self.list.setFrameShape(QFrame.Shape.NoFrame);self.list.setItemDelegate(_ShortcutDelegate(self.list))
        self.list.setMouseTracking(True);self.list.setSpacing(2)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setUniformItemSizes(False)
        content.addWidget(self.list,1)
        self.empty=QLabel('还没有快捷入口\n添加一个常用目录，随时切换。');self.empty.setObjectName('ShortcutEmpty');self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.empty,1);layout.addWidget(card,1)
        footer=QHBoxLayout()
        self.path_button=button('更改路径',self.change_path,footer)
        self.rename_button=button('重命名',self.rename,footer)
        self.remove_button=button('移除',self.remove,footer)
        footer.addStretch();button('取消',self.reject,footer)
        save=button('保存',self.save,footer);save.setObjectName('PrimaryBtn');save.setDefault(True)
        layout.addLayout(footer)
        self.list.currentRowChanged.connect(self.selection_changed)
        self.list.itemDoubleClicked.connect(lambda _:self.rename())
        self.shortcuts=[dict(p) for p in main.settings.get_presets()]
        self._active_shortcut=next((value for value in self.shortcuts
                                   if main._same_path(value['path'],main.workspace)),None)
        self.refresh()
        if self._active_shortcut is not None:
            self.list.setCurrentRow(next(i for i,value in enumerate(self.shortcuts)
                                        if value is self._active_shortcut))
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'list'):
            self.list.doItemsLayout()  # 行高依赖列表宽度，换行后重新计算
    def selection_changed(self,*_):
        enabled=self.list.currentRow()>=0
        self.path_button.setEnabled(enabled);self.rename_button.setEnabled(enabled);self.remove_button.setEnabled(enabled)
    def refresh(self):
        selected=self.list.currentRow();self.list.clear()
        for value in self.shortcuts:
            data=dict(value,active=value is self._active_shortcut,available=Path(value['path']).is_dir())
            item=QListWidgetItem(value['name']);item.setData(Qt.ItemDataRole.UserRole,data)
            item.setToolTip(value['name']+'\n'+value['path']);self.list.addItem(item)
        if self.shortcuts:self.list.setCurrentRow(min(max(0,selected),len(self.shortcuts)-1))
        self.count.setText(f'{len(self.shortcuts)} 个入口')
        self.empty.setVisible(not self.shortcuts);self.list.setVisible(bool(self.shortcuts));self.selection_changed()
    def add(self):
        path=QFileDialog.getExistingDirectory(self,'添加目录入口')
        if path and not any(self.main._same_path(path,p['path']) for p in self.shortcuts):
            self.shortcuts.append({'name':Path(path).name,'path':path});self.refresh();self.list.setCurrentRow(len(self.shortcuts)-1)
    def rename(self):
        row=self.list.currentRow()
        if row<0:return
        name,ok=QInputDialog.getText(self,'入口名称','名称',text=self.shortcuts[row]['name'])
        if ok and name.strip():self.shortcuts[row]['name']=name.strip();self.refresh();self.list.setCurrentRow(row)
    def change_path(self):
        row=self.list.currentRow()
        if row<0:return
        path=QFileDialog.getExistingDirectory(self,'更改入口路径',self.shortcuts[row]['path'])
        if not path:return
        if not Path(path).is_dir():
            QMessageBox.information(self,'目录不可用','请选择有效目录。');return
        if any(i!=row and self.main._same_path(path,value['path']) for i,value in enumerate(self.shortcuts)):
            QMessageBox.information(self,'入口已存在','此目录已有快捷入口。');return
        self.shortcuts[row]['path']=path
        self.refresh();self.list.setCurrentRow(row)
    def remove(self):
        row=self.list.currentRow()
        if row>=0:self.shortcuts.pop(row);self.refresh()
    def save(self):
        if (self._active_shortcut is not None and any(value is self._active_shortcut for value in self.shortcuts)
                and not self.main._same_path(self._active_shortcut['path'],self.main.workspace)):
            target=Path(self._active_shortcut['path'])
            if not target.is_dir():
                QMessageBox.information(self,'目录不可用','入口目录已不可用，请重新选择。');return
            self.main._switch_workspace(target)
            if not self.main._same_path(self.main.workspace,target):return
        self.main.settings.set_presets(self.shortcuts);self.main._refresh_workspace_buttons();self.accept()


class ToolCenter(QDialog):
    def __init__(self, main):
        super().__init__(main);self.main=main
        layout=heading(self,'工具管理','目录、运行记录与配置备份')
        tabs=QTabWidget();layout.addWidget(tabs,1)
        from PySide6.QtWidgets import QWidget
        logs=QWidget();ll=QVBoxLayout(logs)
        actions=QHBoxLayout();button('快捷入口',lambda:WorkspaceDialog(main).exec(),actions);button('刷新',self.refresh,actions);button('清理所选',lambda:self.clear_logs(False),actions);button('清理已结束',lambda:self.clear_logs(True),actions);actions.addStretch();ll.addLayout(actions)
        self.history=QTreeWidget();self.history.setObjectName('ToolHistory');self.history.setHeaderLabels(['工具','状态','开始时间']);self.history.setRootIsDecorated(False);self.history.setTextElideMode(Qt.TextElideMode.ElideNone);self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);ll.addWidget(self.history)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);self.output.setPlaceholderText('选择运行记录查看输出');self.output.setMaximumHeight(190);ll.addWidget(self.output)
        self.history.currentItemChanged.connect(self.log);tabs.addTab(logs,'运行记录')
        checks=QWidget();cl=QVBoxLayout(checks);self.checks=QTreeWidget();self.checks.setObjectName('ToolHealth');self.checks.setHeaderLabels(['工具 / 目录','检查结果']);self.checks.setRootIsDecorated(False);self.checks.setWordWrap(True);self.checks.setTextElideMode(Qt.TextElideMode.ElideNone);self.checks.header().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);self.checks.header().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);cl.addWidget(self.checks)
        ca=QHBoxLayout();button('重新检查',self.check,ca);button('修复所选工具',self.repair,ca);button('绑定运行环境',self.runtime,ca);cl.addLayout(ca);tabs.addTab(checks,'健康检查')
        backup=QWidget();bl=QVBoxLayout(backup);note=QLabel('备份包含目录设置、标签、启动命令、参数配置、环境绑定和使用记录。\n工具程序与运行日志不打包。导入前会自动备份当前配置。');note.setWordWrap(True);bl.addWidget(note)
        form=QFormLayout();self.old=QLineEdit();self.new=QLineEdit();self.old.setPlaceholderText('可选：原电脑上的目录');self.new.setPlaceholderText('可选：本机对应目录');form.addRow('原目录',self.old);form.addRow('新目录',self.new);bl.addLayout(form)
        hint=QLabel('迁移会替换目录前缀。自定义命令中写死的路径需在导入后重新识别或编辑。');hint.setWordWrap(True);bl.addWidget(hint)
        ba=QHBoxLayout();button('导出备份',self.export,ba);button('导入备份',self.restore,ba);bl.addLayout(ba);bl.addStretch();tabs.addTab(backup,'备份与迁移')
        self.timer=QTimer(self,interval=1500);self.timer.timeout.connect(self.refresh);self.timer.start();self.refresh();self.check()
    def refresh(self):
        selected=self.history.currentItem();key=selected.data(0,Qt.ItemDataRole.UserRole)['id'] if selected else None
        self.history.blockSignals(True);self.history.clear()
        for record in launch_history.history(self.main.app_dir/'config/launches'):
            item=QTreeWidgetItem([record['name'],launch_history.LABELS.get(record['state'],record['state']),datetime.datetime.fromtimestamp(record['started']).strftime('%m-%d %H:%M:%S')]);item.setData(0,Qt.ItemDataRole.UserRole,record);self.history.addTopLevelItem(item)
            if record['id']==key:self.history.setCurrentItem(item)
        if not self.history.currentItem() and self.history.topLevelItemCount():self.history.setCurrentItem(self.history.topLevelItem(0))
        self.history.blockSignals(False)
        for i in range(3):
            self.history.resizeColumnToContents(i)
            self.history.setColumnWidth(i,max(self.history.columnWidth(i),[180,150,160][i]))
        self.log()
    def clear_logs(self, all_finished):
        item = self.history.currentItem()
        if not all_finished and item is None: return
        text = '清理全部已结束的启动记录及其日志？' if all_finished else '清理所选启动记录及其日志？'
        if QMessageBox.question(self, '清理运行记录', text + '\n正在运行、启动中和状态未知的记录会保留。') != QMessageBox.StandardButton.Yes: return
        ids = None if all_finished else {item.data(0, Qt.ItemDataRole.UserRole)['id']}
        try:
            removed, skipped = launch_history.clear_history(self.main.app_dir/'config/launches', ids)
            self.output.clear()
            self.refresh()
            QMessageBox.information(self, '清理完成', f'已清理 {removed} 条，保留 {skipped} 条活动或状态未知记录。')
        except (OSError, ValueError) as error:
            self.refresh()
            QMessageBox.warning(self, '清理未完成', str(error))

    def log(self,*_):
        item=self.history.currentItem()
        if not item:
            self.output.clear();return
        record=item.data(0,Qt.ItemDataRole.UserRole);path=self.main.app_dir/'config/launches'/(record['id']+'.log')
        content=''
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(max(0,path.stat().st_size-100000));data=stream.read()
                try:content=data.decode('utf-8')
                except UnicodeDecodeError:content=data.decode('gb18030',errors='replace')
        self.output.setPlainText(record['command']+'\n'+record.get('error','')+'\n'+content)
        bar=self.output.verticalScrollBar();bar.setValue(bar.maximum())
    def check(self):
        self.checks.clear()
        for root in toolbox.workspace_roots(self.main.settings,self.main.workspace):
            if not root.is_dir():self.checks.addTopLevelItem(QTreeWidgetItem([str(root),'目录不可用，请在目录管理中重新添加']))
        entries={str(e.path):e for e in self.main.entries}
        from core.models import Entry
        for path,meta in self.main.db.all_meta().items():
            if path not in entries and self.main.db._in_workspace(self.main.workspace,path) and not Path(path).exists():
                entry=Entry(Path(path).name,Path(path),'','',not bool(Path(path).suffix));entry.command=meta['command'];entry.runtime_profile=meta['runtime_profile'];entries[path]=entry
        for entry in entries.values():
            issues=toolbox.health(entry);result='；'.join(issues) if issues else '可用'
            item=QTreeWidgetItem([entry.name,result]);item.setData(0,Qt.ItemDataRole.UserRole,entry);item.setToolTip(0,str(entry.path));item.setToolTip(1,result);self.checks.addTopLevelItem(item)
        self.checks.resizeColumnToContents(0)
    def repair(self):
        item=self.checks.currentItem();entry=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if not entry:return
        if not entry.path.exists():
            path=(QFileDialog.getExistingDirectory(self,'重新定位工具') if entry.is_dir else
                  QFileDialog.getOpenFileName(self,'重新定位工具')[0])
            if not path:return
            self.main._relocate_entry(entry,Path(path));self.check();return
        self.main._detect_entry(entry);self.check()
    def runtime(self):
        item=self.checks.currentItem();entry=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if entry:self.main._edit_runtime_profile(entry);self.check()
    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'导出备份','SuperBay-backup.zip','备份 (*.zip)')
        if path:
            try:toolbox.export_backup(path,self.main.settings,self.main.db);QMessageBox.information(self,'备份完成',path)
            except Exception as error:QMessageBox.warning(self,'备份失败',str(error))
    def restore(self):
        path,_=QFileDialog.getOpenFileName(self,'导入备份','','备份 (*.zip)')
        if not path:return
        if bool(self.old.text().strip()) != bool(self.new.text().strip()):QMessageBox.warning(self,'目录映射','原目录与新目录需要一起填写。');return
        if QMessageBox.question(self,'导入配置','同路径的工具配置会被备份中的配置替换，继续导入？')!=QMessageBox.StandardButton.Yes:return
        try:
            rescue=self.main.app_dir/'config'/('before-import-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
            toolbox.export_backup(rescue,self.main.settings,self.main.db)
            toolbox.import_backup(path,self.main.settings,self.main.db,self.old.text().strip(),self.new.text().strip())
            root=self.main.settings.get('workspace_root')
            if root:self.main.workspace=Path(root)
            self.main._apply_theme(self.main.settings.get('theme','system'));self.main._set_view(self.main.settings.get('view','grid'));self.main._refresh_workspace_buttons();self.main.reload();self.check()
            QMessageBox.information(self,'导入完成','已导入配置，原配置已自动备份。快捷键与窗口位置重启后生效。')
        except Exception as error:QMessageBox.warning(self,'导入失败',str(error))
