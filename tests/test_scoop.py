import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from config.settings import Settings
from core import scoop
from core.runtime_profile import profile_environment
from ui.widgets.scoop_dialog import ScoopDialog


class ScoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        script = scoop.scoop_script(self.root)
        script.parent.mkdir(parents=True)
        script.touch()
        for package, version, binary, manifest in (
            ("temurin21-jdk", "21.0.7", "bin/java.exe", "scoop-manifest.json"),
            ("python", "3.12.1", "python.exe", "manifest.json"),
            ("python", "3.13.1", "python.exe", "scoop-manifest.json"),
            ("nodejs-lts", "22.1.0", "node.exe", "manifest.json"),
        ):
            folder = self.root / "apps" / package / version
            executable = folder / binary
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.touch()
            (folder / manifest).write_text("{}")
        self.env = {"JAVA_HOME": str(self.root / "apps/temurin21-jdk/21.0.7"),
                    "PATH": os.pathsep.join([str(self.root / "apps/python/3.13.1"),
                                             str(self.root / "apps/nodejs-lts/22.1.0")])}

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_and_defaults(self):
        rows = scoop.scan(self.root, self.env)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r.family for r in rows}, {"Java", "Python", "Node"})
        self.assertEqual(sum(r.default for r in rows), 3)
        self.assertFalse(next(r for r in rows if r.version == "3.12.1").default)

    def test_bound_cli_and_argument_validation(self):
        runtime = scoop.scan(self.root, self.env)[0]
        args = scoop.reset_arguments(self.root, runtime)
        self.assertEqual(args[-2:], ["reset", f"{runtime.package}@{runtime.version}"])
        self.assertIn(str(scoop.scoop_script(self.root)), args)
        from dataclasses import replace
        with self.assertRaises(ValueError):
            scoop.reset_arguments(self.root, replace(runtime, package="python;whoami"))

    def test_terminal_isolation(self):
        runtime = next(r for r in scoop.scan(self.root, self.env) if r.family == "Java")
        before = dict(os.environ)
        with patch.object(scoop, "user_environment", lambda: dict(self.env)):
            env = scoop.terminal_environment(runtime)
        self.assertEqual(env["JAVA_HOME"], str(runtime.directory))
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(runtime.executable.parent))
        self.assertEqual(dict(os.environ), before)

    def test_dialog_binding_filter_and_reset_without_execution(self):
        settings = Settings(self.root / "config.yaml")
        settings.set("scoop_root", str(self.root))
        with patch.object(scoop, "user_environment", lambda: dict(self.env)):
            dialog = ScoopDialog(settings)
        self.assertEqual(dialog.table.topLevelItemCount(), 4)
        dialog.family.setCurrentText("Python")
        self.assertEqual(dialog.table.topLevelItemCount(), 2)
        dialog.search.setText("3.12")
        self.assertEqual(dialog.table.topLevelItemCount(), 1)
        self.assertTrue(dialog.activate.isEnabled())
        with patch.object(dialog.process, "start", lambda: None):
            dialog._activate()
        self.assertEqual(dialog.process.arguments()[-1], "python@3.12.1")
        self.assertEqual(dialog.process.processEnvironment().value("SCOOP"), str(self.root))
        dialog.reject()
        dialog.deleteLater()

    def test_package_hold_checks_actual_state(self):
        import json
        from PySide6.QtCore import QProcess
        from ui.widgets.scoop_packages import ScoopPackagesDialog
        current = self.root / 'apps/python/current'
        current.mkdir()
        (current / 'install.json').write_text(json.dumps({'hold': True}))
        (current / 'manifest.json').write_text(json.dumps({'version': '3.12'}))
        (current / 'scoop-install.json').write_text(json.dumps({'hold': True}))
        dialog = ScoopPackagesDialog(self.root)
        dialog.last_request = ('hold', ['python'])
        dialog.process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertIn('命令已完成', dialog.status.text())
        self.assertFalse(dialog.retry.isEnabled())
        (current / 'scoop-install.json').write_text(json.dumps({'hold': False}))
        dialog.last_request = ('hold', ['python'])
        dialog.process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertIn('未达到预期状态', dialog.status.text())
        self.assertTrue(dialog.retry.isEnabled())
        dialog.deleteLater()

    def test_package_log_removes_split_ansi_sequences(self):
        from ui.widgets.scoop_packages import ScoopPackagesDialog
        dialog = ScoopPackagesDialog(self.root)
        with patch.object(dialog.process, 'readAllStandardOutput', side_effect=[b'\x1b[3', b'4mJAVA_HOME\x1b[0m']):
            dialog.read_output()
            dialog.read_output()
        self.assertEqual(dialog.log.toPlainText(), 'JAVA_HOME')
        dialog.deleteLater()

    def test_invalid_root(self):
        with self.assertRaises(ValueError):
            scoop.scan(self.root / "missing", self.env)

    def test_combined_profile_and_missing_version(self):
        profile = {"root": str(self.root), "versions": {
            "Java": {"package": "temurin21-jdk", "version": "21.0.7"},
            "Python": {"package": "python", "version": "3.12.1"},
            "Node": {"package": "nodejs-lts", "version": "22.1.0"},
        }}
        before = dict(os.environ)
        env = profile_environment(profile)
        self.assertIn("temurin21-jdk", env["JAVA_HOME"])
        self.assertIn(str(self.root / "apps/python/3.12.1"), env["PATH"])
        self.assertIn(str(self.root / "apps/nodejs-lts/22.1.0"), env["PATH"])
        self.assertEqual(dict(os.environ), before)
        profile["versions"]["Python"]["version"] = "missing"
        with self.assertRaisesRegex(ValueError, "Python"):
            profile_environment(profile)

    def test_profile_dialog_roundtrip_and_clear(self):
        from core.models import Entry
        from ui.widgets.runtime_profile import RuntimeProfileDialog
        entry = Entry("tool", self.root / "tool", "tools", "工具", True)
        entry.runtime_profile = {"root": str(self.root), "versions": {
            "Python": {"package": "python", "version": "3.12.1"}}}
        dialog = RuntimeProfileDialog(entry, Settings(self.root / "config.yaml"))
        self.assertEqual(dialog.profile(), entry.runtime_profile)
        dialog._clear()
        self.assertEqual(dialog.profile(), {})
        dialog.reject()
        dialog.deleteLater()

    def test_verify_uses_selected_executable(self):
        settings = Settings(self.root / "config.yaml")
        settings.set("scoop_root", str(self.root))
        with patch.object(scoop, "user_environment", lambda: dict(self.env)):
            dialog = ScoopDialog(settings)
        runtime = dialog._selected()
        with patch.object(dialog.process, "start", lambda: None):
            dialog._verify()
        self.assertEqual(dialog.process.program(), str(runtime.executable))
        self.assertEqual(dialog.process.arguments(), ["-version"])
        dialog.verify_timer.stop()
        dialog.reject()
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
