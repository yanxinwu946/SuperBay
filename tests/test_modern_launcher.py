import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from config.settings import Settings
from core.models import Entry
from core.search import score
from ui.main_window import MainWindow
from ui.theme import apply_theme
from ui.widgets.palette import LaunchPalette
from ui.widgets.entry_view import GRID_SIZE, launch_rect


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.entry = Entry("Flow-Launcher", Path("C:/tools/Flow-Launcher"), "tools", "效率工具", True,
                           tags=["启动器"])

    def test_rank_and_multi_token(self):
        self.assertGreater(score(self.entry, "Flow-Launcher"), score(self.entry, "launcher"))
        self.assertIsNotNone(score(self.entry, "flow 启动器"))
        self.assertIsNone(score(self.entry, "flow 不存在"))

    def test_subsequence_and_unicode(self):
        self.assertIsNotNone(score(self.entry, "fl"))
        self.assertIsNotNone(score(self.entry, "ＦＬＯＷ"))
        self.assertIsNotNone(score(self.entry, "说明", "使用说明"))


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        workspace = root / "workspace"
        (workspace / "A-效率工具" / "Flow-Launcher").mkdir(parents=True)
        (workspace / "A-效率工具" / "notes.txt").write_text("demo")
        self.settings = Settings(root / "config/config.yaml")
        self.settings.set("workspace_root", str(workspace))
        self.settings.set("workspace_presets", [{"name": "测试", "path": str(workspace)}])
        with patch.object(MainWindow, "_build_ipc", lambda self: None):
            self.window = MainWindow(root, self.settings)
        self.window.show()
        for _ in range(200):
            QTest.qWait(10)
            if self.window.entries and not self.window._reloading:
                break
        self.assertEqual(len(self.window.entries), 2)

    def tearDown(self):
        self.window._tray_quit = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_filter_and_large_model(self):
        self.window._kind_filter.setCurrentIndex(3)
        self.assertEqual(self.window._browser.model().rowCount(), 1)
        self.window._kind_filter.setCurrentIndex(0)
        entries = [Entry(f"Tool {i}", Path(f"C:/tools/{i}"), "tools", "工具", True) for i in range(10000)]
        self.window.entries = entries
        self.window._rebuild_grid()
        self.assertEqual(self.window._browser.model().rowCount(), 10000)
        # Virtualization must not create a child widget for every entry.
        self.assertLess(len(self.window._browser.findChildren(QWidget)), 30)
        self.window._set_view("list")
        self.assertFalse(self.window._browser.grid)

    def test_palette_keyboard_selection(self):
        dialog = LaunchPalette(self.window.entries, {}, self.window._icon_for, lambda: False)
        launched = []
        dialog.launchRequested.connect(launched.append)
        dialog.show()
        QTest.keyClick(dialog.search, Qt.Key.Key_Down)
        selected = dialog.view.entries_model.entries[1]
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertEqual(launched, [selected])
        dialog.deleteLater()

    def test_main_search_enter_uses_latest_query(self):
        launched = []
        self.window._launch = lambda entry, admin: launched.append(entry)
        self.window._search.setText("notes")
        QTest.keyClick(self.window._search, Qt.Key.Key_Return)
        self.assertEqual([entry.name for entry in launched], ["notes.txt"])

    def test_cancel_selection_restores_highlight(self):
        browser = self.window._browser
        browser.setCurrentIndex(browser.model().index(0, 0))
        previous = self.window.selected
        self.window._detail.confirm_leave = lambda: False
        browser.setCurrentIndex(browser.model().index(1, 0))
        self.assertIs(self.window.selected, previous)
        self.assertEqual(browser.currentIndex().row(), 0)
        self.window._detail.confirm_leave = lambda: True

    def test_compact_layout_and_quick_launch_button(self):
        self.assertFalse(hasattr(self.window, "_status_time"))
        self.assertLessEqual(GRID_SIZE.height(), 72)
        browser = self.window._browser
        browser.doItemsLayout()
        launched = []
        browser.launchRequested.connect(launched.append)
        index = browser.model().index(0, 0)
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton,
                         pos=launch_rect(browser.visualRect(index)).center())
        self.assertEqual(launched, [browser.entries_model.entries[0]])

    def test_theme_render(self):
        root = Path(__file__).resolve().parents[1]
        for theme in ("light", "dark"):
            self.settings.set("theme", theme)
            apply_theme(self.app, root, theme)
            self.app.processEvents()
            self.assertFalse(self.window.grab().isNull())

    def test_reset_all_filters(self):
        self.window.nav, self.window.tag = "star", "missing"
        self.window._kind_filter.setCurrentIndex(3)
        self.window._search.setText("missing")
        self.window._apply_query()
        self.assertEqual(self.window._browser.model().rowCount(), 0)
        self.window._reset_filters()
        self.assertEqual(self.window._browser.model().rowCount(), 2)
        self.assertEqual(self.window._search.text(), "")
        self.assertEqual(self.window.nav, "all")
        self.assertIsNone(self.window.tag)

    def test_same_entry_refresh_preserves_unsaved_note(self):
        entry = next(e for e in self.window.entries if e.is_dir)
        self.window._on_select(entry)
        detail = self.window._detail
        detail._toggle_mode()
        detail._note_edit.setPlainText("尚未保存的重要笔记")
        self.assertTrue(detail.has_unsaved_note())
        self.window._toggle_pin(entry, True)
        self.assertEqual(detail._note_edit.toPlainText(), "尚未保存的重要笔记")
        self.assertTrue(detail.has_unsaved_note())
        self.assertTrue(detail._editing)
        detail._note_dirty = False

    def test_entry_shortcuts_emit_actions(self):
        browser = self.window._browser
        browser.setCurrentIndex(browser.model().index(0, 0))
        actions = []
        # Prevent application actions such as launching or prompting in this test.
        browser.actionRequested.disconnect(self.window._entry_action)
        browser.actionRequested.connect(lambda action, entry: actions.append(action))
        QTest.keyClick(browser, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(browser, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(browser, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(browser, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(actions, ["admin", "args", "pin", "runtime"])

    def test_index_updates_after_tag_and_note_changes(self):
        entry = next(e for e in self.window.entries if e.is_dir)
        self.window.query = "new-unique-tag"
        self.window._rebuild_grid()
        self.assertEqual(self.window._browser.model().rowCount(), 0)
        self.window._edit_tags(entry, ["new-unique-tag"])
        self.assertEqual(self.window._browser.model().rowCount(), 1)
        self.window._on_select(entry)
        self.window.query = "new-note-token"
        self.window._rebuild_grid()
        self.assertEqual(self.window._browser.model().rowCount(), 0)
        self.window._detail._note_edit.setPlainText("new-note-token")
        self.assertTrue(self.window._detail._save_note())
        self.assertEqual(self.window._browser.model().rowCount(), 1)

    def test_refining_query_preserves_unchanged_results(self):
        self.window.query = "txt"
        self.window._rebuild_grid()
        browser = self.window._browser
        browser.setCurrentIndex(browser.model().index(0, 0))
        resets = []
        browser.model().modelReset.connect(lambda: resets.append(True))
        self.window.query = "notes"
        self.window._rebuild_grid()
        self.assertEqual(resets, [])
        self.assertEqual(browser.currentIndex().data(), "notes.txt")

    def test_glass_layout_has_no_category_heading_and_reuses_material(self):
        from PySide6.QtWidgets import QLabel
        self.assertEqual(self.window.view, "list")
        labels = [label.text() for label in self.window._sidebar.findChildren(QLabel)]
        self.assertNotIn("分 类", labels)
        self.window.grab()
        canvas = self.window.centralWidget()
        key = canvas._material.cacheKey()
        self.window.grab()
        self.assertEqual(key, canvas._material.cacheKey())
        for name, button in self.window._sidebar._nav_buttons.items():
            if name not in ("all", "star", "recent"):
                self.assertLessEqual(button.width(), self.window._sidebar._cat_scroll.viewport().width())

    def test_selected_row_leaves_clear_edge_gutter(self):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        browser = self.window._browser
        browser.set_grid(False)
        option = QStyleOptionViewItem()
        option.rect = QRect(8, 8, 240, 48)
        option.font = browser.font()
        option.state = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
        untouched = QColor("#c254a6")
        pixmap = QPixmap(280, 80)
        pixmap.fill(untouched)
        painter = QPainter(pixmap)
        browser.itemDelegate().paint(painter, option, browser.model().index(0, 0))
        painter.end()
        image = pixmap.toImage()
        for x in range(option.rect.left(), option.rect.left()+3):
            for y in range(option.rect.top(), option.rect.bottom()+1):
                self.assertEqual(image.pixelColor(x, y), untouched)
        self.assertNotEqual(image.pixelColor(option.rect.center()), untouched)


if __name__ == "__main__":
    unittest.main()
