import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.app_paths import data_dir

class DataDirectoryTests(unittest.TestCase):
    def test_migrates_old_data_once_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'LOCALAPPDATA': temp}), \
                patch('sys.frozen', True, create=True):
            old = Path(temp) / 'SuperGUI' / 'config'
            old.mkdir(parents=True)
            (old / 'config.yaml').write_text('old', encoding='utf-8')
            new = data_dir()
            self.assertEqual((new / 'config/config.yaml').read_text(encoding='utf-8'), 'old')
            (new / 'config/config.yaml').write_text('new', encoding='utf-8')
            self.assertEqual((data_dir() / 'config/config.yaml').read_text(encoding='utf-8'), 'new')
            self.assertEqual((old / 'config.yaml').read_text(encoding='utf-8'), 'old')
