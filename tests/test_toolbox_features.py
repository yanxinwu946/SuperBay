"""Integration coverage for persistent workspace and execution features."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from config.db import DB
from config.settings import Settings
from core.models import Entry
from core.toolbox import workspace_roots,scan_roots,suggestions,health,export_backup,import_backup
from core.launch_history import start,history


class ToolboxFeatures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(self.root/'config.yaml');self.db=DB(self.root/'data.db')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def entry(self,path):return Entry(path.name,path,'category','分类',path.is_dir())
    def test_shortcuts_never_aggregate_other_directories(self):
        roots=[self.root/'one',self.root/'two']
        for root in roots:(root/'A-tools'/'same').mkdir(parents=True)
        self.settings.set('workspace_roots',[str(p) for p in roots]+[str(roots[0])])
        resolved=workspace_roots(self.settings,roots[0]);self.assertEqual(len(resolved),1)
        categories=scan_roots(resolved);self.assertEqual(len(categories),1)
        self.assertTrue(all(e.path.is_relative_to(roots[0]) for c in categories for e in c.entries))
        nested=scan_roots([roots[0],roots[0]])
        self.assertEqual(sum(len(c.entries) for c in nested),1)
    def test_detection_ambiguity_and_missing_interpreter(self):
        folder=self.root/'tool';folder.mkdir();(folder/'main.py').write_text('pass')
        entry=self.entry(folder);self.assertIn('python -u',suggestions(entry)[0][1])
        with patch('core.toolbox.shutil.which',return_value=None):self.assertTrue(any('找不到' in v for v in health(entry)))
        (folder/'cli.py').write_text('pass');self.assertTrue(any('多个入口' in v for v in health(entry)))
        (folder/'setup.exe').touch();self.assertEqual(len(suggestions(entry)),2)
    def test_backup_roundtrip_with_relocated_metadata_and_presets(self):
        old=self.root/'old';new=self.root/'new';path=old/'tools'/'main.py'
        self.settings.set('workspace_root',str(old));self.settings.set('workspace_roots',[str(old)])
        self.settings.set('launch_presets',{str(path):[{'name':'代理','args':'--proxy 127.0.0.1:8080'}]})
        self.db.set_tags(path,['test']);self.db.set_command(path,'python main.py');self.db.record_run(path)
        self.db.set_runtime_profile(path,{'root':str(old/'scoop'),'versions':{}})
        backup=self.root/'backup.zip';export_backup(backup,self.settings,self.db)
        import_backup(backup,self.settings,self.db,str(old),str(new))
        moved=str(new/'tools'/'main.py');self.assertIn(moved,self.db.all_meta());self.assertIn(moved,self.settings.get('launch_presets'))
        self.assertEqual(self.db.all_meta()[moved]['runtime_profile']['root'],str(new/'scoop'))
        self.assertEqual(self.db.get_stats(Path(moved))['runs'],1)
        self.assertEqual(self.settings.get('workspace_root'),str(new))
    def test_bad_backup_leaves_existing_configuration_untouched(self):
        import zipfile
        archive=self.root/'bad.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('manifest.json','{}');z.writestr('settings.json','{}');z.writestr('data.db','bad')
        self.db.set_tags(self.root/'tool',['keep'])
        with self.assertRaises(ValueError):import_backup(archive,self.settings,self.db)
        self.assertEqual(self.db.all_meta()[str(self.root/'tool')]['tags'],['keep'])
    def test_managed_launch_persists_only_environment_differences(self):
        tool=self.root/'tool';tool.mkdir();entry=self.entry(tool);entry.command='python main.py'
        env=dict(os.environ);env['TOOL_TEST_FLAG']='value'
        with patch('core.launch_history.launcher._shell_execute_w'):
            identifier,_=start(entry,'--target "two words"',env,False,self.root/'launches')
        request=json.loads((self.root/'launches'/(identifier+'.json')).read_text())
        self.assertEqual(request['environment'],{'TOOL_TEST_FLAG':'value'})
        self.assertIn('--target "two words"',request['argv'][-1])
        self.assertEqual(history(self.root/'launches')[0]['state'],'starting')
    def test_worker_records_real_failure_and_log(self):
        request=self.root/'run.json'
        request.write_text(json.dumps({'id':'run','name':'failure','path':str(self.root),'started':time.time(),'command':'test',
            'argv':[sys.executable,'-c','print("diagnostic");raise SystemExit(7)'],'cwd':str(self.root),'environment':{}}))
        worker=Path(__file__).resolve().parents[1]/'core/launch_worker.py'
        subprocess.run([sys.executable,str(worker),str(request)],check=True,capture_output=True)
        record=json.loads(request.with_suffix('.status.json').read_text())
        self.assertEqual(record['state'],'failed');self.assertEqual(record['exit_code'],7)
        self.assertIn('diagnostic',request.with_suffix('.log').read_text());self.assertFalse(request.exists())


class ToolboxDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_preset_persistence_palette_selection_and_diagnostics(self):
        from PySide6.QtTest import QTest
        from ui.main_window import MainWindow
        from ui.theme import apply_theme
        from ui.widgets.tool_center import PresetDialog,ToolCenter,WorkspaceDialog
        from ui.widgets.palette import LaunchPalette
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';tool=workspace/'A-tools'/'demo';tool.mkdir(parents=True);(tool/'main.py').write_text('pass')
            settings=Settings(root/'config.yaml');settings.set('workspace_root',str(workspace))
            with patch.object(MainWindow,'_build_ipc',lambda self:None):main=MainWindow(root,settings)
            main.show()
            for _ in range(200):
                QTest.qWait(10)
                if main.entries and not main._reloading:break
            self.assertEqual(len(main.entries),1);entry=main.entries[0]
            dialog=PresetDialog(main,entry);dialog.name.setText('代理配置');dialog.args.setText('--proxy http://localhost:8080');dialog.save()
            self.assertEqual(settings.get('launch_presets')[str(tool)][0]['name'],'代理配置')
            recipe_dialog=LaunchPalette(main.entries,{},main._icon_for,lambda:False,main)
            self.assertEqual(recipe_dialog.presets.count(),2)
            calls=[];recipe_dialog.recipeRequested.connect(lambda entry,value:calls.append(value))
            recipe_dialog.presets.setCurrentIndex(1);recipe_dialog._launch(entry)
            self.assertEqual(calls[0]['args'],'--proxy http://localhost:8080')
            center=ToolCenter(main);self.assertEqual(center.checks.topLevelItemCount(),1)
            roots=WorkspaceDialog(main);self.assertEqual(len(roots.shortcuts),2)
            dialog.close();recipe_dialog.close();center.close();roots.close()
            main._tray_quit=True;main.close();main.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
