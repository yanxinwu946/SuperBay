import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.db import DB
from config.settings import Settings
from core.file_management import relocate, import_paths, valid_name
from core.command_templates import fields, render
from core.models import Entry
from core.search import score
from core.toolbox import export_backup, import_backup


class WorkspaceWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = DB(self.root/'data.db')
        self.workspace = self.root/'workspace'; self.workspace.mkdir()

    def tearDown(self):
        self.db.close(); self.temp.cleanup()

    def test_move_preserves_nested_commands_plans_and_statistics(self):
        source = self.workspace/'old'; source.mkdir()
        tool = source/'tool'; tool.mkdir()
        self.db.set_command(tool, f'python "{tool}/main.py"')
        self.db.set_tags(tool, ['扫描']); self.db.record_run(tool)
        self.db.set_aliases(tool, ['scan'])
        self.db.set_launch_plans(tool, [{'name':'常用', 'command':f'python "{tool}/main.py"'}])
        target = self.workspace/'new'
        relocate(self.db, self.workspace, [(source, target)])
        moved = target/'tool'; metadata = self.db.all_meta()[str(moved)]
        self.assertFalse(source.exists()); self.assertTrue(moved.is_dir())
        self.assertIn(str(moved), metadata['command'])
        self.assertIn(str(moved), metadata['launch_plans'][0]['command'])
        self.assertEqual(metadata['aliases'], ['scan'])
        self.assertEqual(self.db.get_stats(moved)['runs'], 1)

    def test_stale_destination_metadata_rolls_back_files_and_db(self):
        source = self.workspace/'old'; source.mkdir()
        target = self.workspace/'new'
        self.db.set_tags(source, ['source']); self.db.set_tags(target, ['keep'])
        with self.assertRaises(ValueError): relocate(self.db, self.workspace, [(source, target)])
        self.assertTrue(source.is_dir()); self.assertFalse(target.exists())
        self.assertEqual(self.db.all_meta()[str(target)]['tags'], ['keep'])

    def test_batch_collision_changes_nothing(self):
        a = self.workspace/'a'; a.mkdir()
        b = self.workspace/'b'; b.mkdir()
        with self.assertRaises(ValueError):
            relocate(self.db, self.workspace, [(a, self.workspace/'c'), (b, self.workspace/'c')])
        self.assertTrue(a.exists()); self.assertTrue(b.exists())

    def test_outside_workspace_and_self_move_are_rejected(self):
        a = self.workspace/'a'; a.mkdir()
        for target in (self.root/'outside', a/'child'):
            with self.assertRaises(ValueError): relocate(self.db, self.workspace, [(a, target)])
        self.assertTrue(a.exists())

    def test_import_copies_without_modifying_source(self):
        source = self.root/'external'; source.mkdir()
        (source/'main.py').write_text('print(1)')
        import_paths(self.workspace, self.workspace, [source])
        self.assertTrue((source/'main.py').exists())
        self.assertEqual((self.workspace/'external/main.py').read_text(), 'print(1)')
        with self.assertRaises(ValueError): import_paths(self.workspace, self.workspace, [source])

    def test_project_import_prunes_dependencies_and_keeps_manifests(self):
        source = self.root/'node-project'; source.mkdir()
        (source/'node_modules/pkg').mkdir(parents=True)
        (source/'node_modules/pkg/index.js').write_text('dependency')
        (source/'package.json').write_text('{}')
        (source/'package-lock.json').write_text('{}')
        (source/'main.js').write_text('app')
        skipped = set()
        with patch.object(Path, 'rglob', side_effect=AssertionError('Do not prewalk dependency trees')):
            import_paths(self.workspace, self.workspace, [source], skipped=skipped)
        target = self.workspace/source.name
        self.assertFalse((target/'node_modules').exists())
        self.assertTrue((target/'package-lock.json').exists())
        self.assertTrue((target/'main.js').exists())
        self.assertEqual(skipped, {'node_modules'})
        other = self.workspace/'full'; other.mkdir()
        import_paths(self.workspace, other, [source], skip_dependencies=False)
        self.assertTrue((other/source.name/'node_modules/pkg/index.js').exists())

    def test_failed_import_leaves_no_visible_partial_tool(self):
        source = self.root/'source'; source.mkdir()
        with patch('core.file_management.shutil.copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): import_paths(self.workspace, self.workspace, [source])
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_windows_reserved_names_and_traversal(self):
        for name in ('../bad', 'CON', 'nul.txt', 'bad:name', 'x.', ''):
            with self.assertRaises(ValueError): valid_name(name)

    def test_template_requires_fields_and_preserves_nonfields(self):
        command = 'python main.py --target {目标} --file "{字典:file}" --out "{输出:dir}"'
        self.assertEqual(fields(command), [('目标','text'), ('字典','file'), ('输出','dir')])
        with self.assertRaises(ValueError): render(command, {'目标':'example.test'})
        result = render(command, {'目标':'example.test', '字典':'D:/My Files/list.txt', '输出':'D:/out'})
        self.assertIn('"D:/My Files/list.txt"', result)
        self.assertEqual(render('echo { a = 1 }', {}), 'echo { a = 1 }')

    def test_chinese_search_aliases_and_filters(self):
        entry = Entry('信息收集', self.workspace/'tool', 'cat', '分类', True,
                      tags=['扫描'], aliases=['recon'])
        for query in ('xinxishouji', 'xxsj', 'recon', '标签:扫描 类型:工具'):
            self.assertIsNotNone(score(entry, query), query)
        self.assertIsNone(score(entry, '类型:文件'))
        self.assertIsNone(score(entry, '标签:recon'))

    def test_new_metadata_survives_backup(self):
        path = self.workspace/'tool'
        self.db.set_launch_plans(path, [{'name':'默认', 'command':'echo {目标}', 'values':{'目标':'test'}}])
        self.db.set_aliases(path, ['alias'])
        settings = Settings(self.root/'settings.yaml')
        backup = self.root/'backup.zip'; export_backup(backup, settings, self.db)
        self.db.set_aliases(path, [])
        import_backup(backup, settings, self.db)
        self.assertEqual(self.db.all_meta()[str(path)]['aliases'], ['alias'])
        self.assertEqual(self.db.all_meta()[str(path)]['launch_plans'][0]['values']['目标'], 'test')

    def test_clear_recent_is_persistent_scoped_and_relaunch_restores_it(self):
        first, other = self.workspace/'first', self.root/'other'
        self.db.record_run(first); self.db.record_run(other)
        self.db.clear_recent([first])
        self.db.close(); self.db = DB(self.root/'data.db')
        self.assertTrue(self.db.all_stats()[str(first)]['recent_hidden'])
        self.assertFalse(self.db.all_stats()[str(other)]['recent_hidden'])
        self.assertEqual(self.db.get_stats(first)['runs'], 1)
        self.db.record_run(first)
        self.assertFalse(self.db.all_stats()[str(first)]['recent_hidden'])
        self.assertEqual(self.db.get_stats(first)['runs'], 2)

    def test_old_database_upgrades_without_losing_commands(self):
        path = self.workspace/'tool'
        self.db.set_command(path, 'python main.py'); self.db.record_run(path)
        self.db._conn.executescript('ALTER TABLE meta DROP COLUMN aliases; ALTER TABLE meta DROP COLUMN launch_plans; ALTER TABLE stats DROP COLUMN recent_hidden; PRAGMA user_version=4;')
        self.db.close(); self.db = DB(self.root/'data.db')
        self.assertEqual(self.db.all_meta()[str(path)]['command'], 'python main.py')
        self.assertEqual(self.db.all_meta()[str(path)]['launch_plans'], [])
        self.assertFalse(self.db.all_stats()[str(path)]['recent_hidden'])


class WorkflowUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_parameters_disable_execute_until_complete(self):
        from PySide6.QtWidgets import QDialogButtonBox
        from ui.widgets.launch_plans import ParametersDialog
        dialog = ParametersDialog('echo {目标}', defaults={'目标':'example.test'})
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.inputs['目标'].clear()
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.close()

    def test_plans_save_and_run_does_not_mutate_original(self):
        from ui.widgets.launch_plans import LaunchPlansDialog
        entry = Entry('tool', Path('tool'), 'cat', '分类', True)
        dialog = LaunchPlansDialog(entry)
        dialog.add(); dialog.name.setText('帮助'); dialog.command.setPlainText('python main.py --help')
        self.assertTrue(dialog.save()); self.assertEqual(entry.launch_plans, [])
        dialog.run(); self.assertEqual(dialog.run_plan['name'], '帮助')

    def test_friendly_plan_editor_duplicates_inserts_and_publishes(self):
        from ui.widgets.launch_plans import LaunchPlansDialog
        entry = Entry('tool', Path('tool'), 'cat', '分类', True, command='python main.py')
        dialog = LaunchPlansDialog(entry)
        published = []; dialog.plansChanged.connect(published.append)
        self.assertTrue(dialog.editor.isHidden())
        dialog.add()
        self.assertEqual(dialog.command.toPlainText(), 'python main.py')
        self.assertTrue(dialog.save())
        dialog.duplicate()
        self.assertIn('副本', dialog.name.text())
        with patch('ui.widgets.launch_plans.QInputDialog.getText', return_value=('字典', True)):
            dialog.insert_parameter('file')
        self.assertIn('"{字典:file}"', dialog.command.toPlainText())
        self.assertTrue(dialog.save())
        self.assertEqual(len(published[-1]), 2)
        self.assertTrue(all('_draft' not in p for p in published[-1]))
        dialog.close()

    def test_file_browser_is_read_only_and_preview_is_bounded(self):
        from ui.widgets.file_manager import ToolFilesDialog
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root/'large.txt'; path.write_text('x'*100000)
            entry = Entry('tool', root, 'cat', '分类', True)
            dialog = ToolFilesDialog(entry, Settings(root/'settings.yaml'))
            with patch.object(dialog, 'selected', return_value=path): dialog.show_preview()
            self.assertTrue(dialog.model.isReadOnly())
            self.assertLess(len(dialog.preview.toPlainText()), 66000)
            self.assertIn('截断', dialog.preview.toPlainText())
            dialog.close()

    def test_side_panel_tabs_preserve_unsaved_note(self):
        from PySide6.QtGui import QIcon
        from ui.widgets.detail import DetailPanel
        with tempfile.TemporaryDirectory() as temp:
            entry = Entry('tool', Path(temp), 'cat', '分类', True)
            dialog = DetailPanel()
            dialog.set_entry(entry, QIcon(), {'runs':0, 'last_run':''})
            self.assertEqual([dialog._tabs.tabText(i) for i in range(dialog._tabs.count())], ['启动', '笔记', '信息'])
            dialog._tabs.setCurrentIndex(1)
            dialog._toggle_mode(); dialog._note_edit.setPlainText('保留未保存的内容')
            dialog._tabs.setCurrentIndex(0); dialog._tabs.setCurrentIndex(1)
            dialog.set_entry(entry, QIcon(), {'runs':1, 'last_run':'today'})
            self.assertEqual(dialog._note_edit.toPlainText(), '保留未保存的内容')
            self.assertTrue(dialog.has_unsaved_note())
            dialog.close()
