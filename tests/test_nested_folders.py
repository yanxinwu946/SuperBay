import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from core.scanner import scan

class NestedFolderTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def file(self,relative):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('demo');return path
    def test_nested_groups_and_project_boundary(self):
        self.file('A-tools/Recon/Web/demo/main.py')
        self.file('A-tools/Recon/Web/demo/src/internal.py')
        self.file('A-tools/Recon/DNS/lookup/main.py')
        self.file('A-tools/Repo/package.json')
        self.file('A-tools/Repo/src/index.js')
        self.file('A-tools/Repo/node_modules/dep/index.js')
        categories=scan(self.root,max_depth=4);mapping={c.cat_id:c for c in categories}
        self.assertEqual(mapping['A-tools/Recon/Web'].parent_id,'A-tools/Recon')
        self.assertEqual(mapping['A-tools/Recon/Web'].depth,2)
        from ui.main_window import _ScanWorker
        result=[]
        worker=_ScanWorker(self.root)
        worker.done.connect(lambda categories,notes:result.extend(categories))
        worker.run()
        self.assertIn('A-tools/Recon',{c.cat_id for c in result})

        paths={e.path for c in categories for e in c.entries}
        self.assertIn(self.root/'A-tools/Recon/Web/demo',paths)
        self.assertIn(self.root/'A-tools/Repo',paths)
        self.assertFalse(any('internal.py' in str(p) or 'node_modules' in str(p) for p in paths))
    def test_root_files_and_nested_documents(self):
        direct=self.file('read.pdf');deep=self.file('A-docs/Network/Manuals/guide.pdf')
        entries=[e for c in scan(self.root,max_depth=4) for e in c.entries]
        self.assertIn(direct,{e.path for e in entries});self.assertIn(deep,{e.path for e in entries})
    def test_tree_collapse_and_parent_selection(self):
        self.file('A-tools/Recon/Web/demo/main.py')
        from PySide6.QtWidgets import QApplication
        from ui.widgets.workspace_sidebar import WorkspaceSideBar as SideBar
        app=QApplication.instance() or QApplication([])
        bar=SideBar();categories=scan(self.root,max_depth=4);bar.rebuild(categories,1,0,0,[]);bar.show();app.processEvents()
        events=[];bar.navChanged.connect(events.append)
        bar._nav_buttons['A-tools'].click()
        self.assertEqual(events,['A-tools'])
        bar._toggle_category('A-tools');app.processEvents()
        self.assertFalse(bar._category_rows['A-tools/Recon'][0].isHidden())
        self.assertTrue(bar._category_rows['A-tools/Recon/Web'][0].isHidden())
        bar._toggle_category('A-tools');app.processEvents()
        self.assertTrue(bar._category_rows['A-tools/Recon'][0].isHidden())
        bar.close();bar.deleteLater();app.processEvents()

    def test_default_two_levels_and_custom_limit(self):
        self.file('Group/Level2/Level3/Level4/demo/main.py')
        limited=scan(self.root)
        self.assertEqual({c.cat_id for c in limited},{'Group','Group/Level2'})
        self.assertIn(self.root/'Group/Level2/Level3',{e.path for c in limited for e in c.entries})
        self.assertEqual({c.cat_id for c in scan(self.root,max_depth=1)},{'Group'})
        deeper=scan(self.root,max_depth=4)
        self.assertIn(self.root/'Group/Level2/Level3/Level4/demo',{e.path for c in deeper for e in c.entries})

    def test_default_tree_expands_only_one_level_and_preserves_user_choice(self):
        self.file('Group/Level2/Level3/demo/main.py')
        from PySide6.QtWidgets import QApplication
        from ui.widgets.workspace_sidebar import WorkspaceSideBar as SideBar
        app=QApplication.instance() or QApplication([])
        categories=scan(self.root,max_depth=4)
        bar=SideBar();bar.rebuild(categories,1,0,0,[])
        self.assertFalse(bar._category_rows['Group'][0].isHidden())
        self.assertTrue(bar._category_rows['Group/Level2'][0].isHidden())
        bar._toggle_category('Group')
        self.assertFalse(bar._category_rows['Group/Level2'][0].isHidden())
        self.assertTrue(bar._category_rows['Group/Level2/Level3'][0].isHidden())
        bar.rebuild(categories,1,0,0,[])
        self.assertFalse(bar._category_rows['Group/Level2'][0].isHidden())
        self.assertTrue(bar._category_rows['Group/Level2/Level3'][0].isHidden())
        bar.close();bar.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
