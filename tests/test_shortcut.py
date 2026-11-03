"""Desktop shortcut creation for tools."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from core.models import Entry
from core.shortcut import app_shortcut_spec, create_app_shortcut, create_desktop_shortcut, shortcut_spec


@unittest.skipUnless(sys.platform == "win32", "Windows shell links")
class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_shortcut_and_sets_admin_flag(self):
        tool = self.root / "tool"
        tool.mkdir()
        (tool / "demo.exe").write_bytes(b"")
        entry = Entry("演示 工具", tool, "cat", "分类", True)
        entry.command = "echo hello"
        entry.admin = True
        link = create_desktop_shortcut(entry, self.root / "desktop")
        self.assertTrue(link.is_file())
        self.assertEqual(link.name, "演示 工具.lnk")
        flags = struct.unpack_from("<I", link.read_bytes(), 20)[0]
        self.assertTrue(flags & 0x1000)
        link2 = create_desktop_shortcut(entry, self.root / "desktop")
        self.assertEqual(link2.name, "演示 工具 (2).lnk")

    def test_current_program_shortcut(self):
        target, args, cwd, icon = app_shortcut_spec()
        self.assertTrue(Path(target).is_file())
        self.assertTrue(Path(cwd).is_dir())
        link = create_app_shortcut(self.root / "desktop")
        self.assertTrue(link.is_file())
        self.assertEqual(link.name, "SuperBay.lnk")

    def test_folder_without_exe_opens_in_explorer(self):
        folder = self.root / "资料"
        folder.mkdir()
        target, args, cwd, _, admin = shortcut_spec(Entry("资料", folder, "cat", "分类", True))
        self.assertEqual(target, str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe"))
        self.assertEqual(args, f'"{folder}"')
        self.assertEqual(cwd, str(folder))
        self.assertFalse(admin)


if __name__ == "__main__":
    unittest.main()
