import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest

from core.entry_kind import detect, kinds_of, matches
from core.models import Entry
from core.scanner import scan
from core.search import score


class EntryKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('demo')
        return path

    def test_single_file_kinds(self):
        self.assertEqual(detect(self.file('tool.exe'), False), ('exe',))
        self.assertEqual(detect(self.file('main.py'), False), ('python',))
        self.assertEqual(detect(self.file('main.go'), False), ('go',))
        self.assertEqual(detect(self.file('app.jar'), False), ('java',))
        self.assertEqual(detect(self.file('run.bat'), False), ('script',))
        self.assertEqual(detect(self.file('manual.pdf'), False), ('data',))

    def test_directory_uses_markers_and_entries(self):
        self.file('go-tool/go.mod')
        self.file('go-tool/main.go')
        self.assertEqual(detect(self.root / 'go-tool', True), ('go',))

        self.file('py-mixed/requirements.txt')
        self.file('py-mixed/run.exe')
        self.assertEqual(detect(self.root / 'py-mixed', True), ('python', 'exe'))

        self.file('docs/manual.pdf')
        self.assertEqual(detect(self.root / 'docs', True), ('data',))

        self.file('c-project/.git/config')
        self.assertEqual(detect(self.root / 'c-project', True, is_tool=True), ('other',))

    def test_scanned_entries_carry_kinds_and_filter(self):
        self.file('A-tools/go-tool/go.mod')
        self.file('A-tools/go-tool/main.go')
        self.file('A-tools/py-tool/requirements.txt')
        self.file('A-tools/py-tool/main.py')
        self.file('standalone.exe')
        entries = [entry for category in scan(self.root, max_depth=3)
                   for entry in category.entries]
        by_name = {entry.name: entry for entry in entries}
        self.assertEqual(by_name['go-tool'].kinds, ('go',))
        self.assertEqual(by_name['py-tool'].kinds, ('python',))
        self.assertEqual(by_name['standalone.exe'].kinds, ('exe',))
        self.assertTrue(matches(by_name['go-tool'], 'go'))
        self.assertFalse(matches(by_name['go-tool'], 'python'))
        self.assertTrue(matches(by_name['standalone.exe'], 'exe_file'))
        self.assertFalse(matches(by_name['py-tool'], 'exe_file'))
        self.assertTrue(matches(by_name['go-tool'], 'all'))

    def test_language_search_and_missing_kinds(self):
        entry = Entry('collector', self.root / 'collector', 'tools', '工具', True, kinds=('go',))
        self.assertIsNotNone(score(entry, '语言:go'))
        self.assertIsNotNone(score(entry, 'lang:go'))
        self.assertIsNotNone(score(entry, 'go'))
        self.assertIsNone(score(entry, '语言:python'))
        plain = Entry('notes', self.root / 'notes.txt', 'tools', '工具', False)
        self.assertEqual(kinds_of(plain), ('data',))
        group = Entry('侦察', self.root / 'A-侦察', 'A-侦察', '侦察', True,
                      entry_type='dir', group=True)
        self.assertEqual(kinds_of(group), ())
        self.assertFalse(matches(group, 'data'))
        self.assertTrue(matches(group, 'all'))


if __name__ == '__main__':
    unittest.main()
