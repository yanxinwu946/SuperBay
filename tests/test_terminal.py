"""Terminal routing: known CLI conventions and the launch hand-off."""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.launch_history import start
from core.models import Entry
from core.terminal import is_gui_exe, terminal_argv, launch_terminal, CREATE_NEW_CONSOLE


@unittest.skipUnless(sys.platform == "win32", "Windows terminal conventions")
class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def terminal(self, name):
        path = self.root / name
        path.write_bytes(b"")
        return path

    def jar(self, name, main_class, body):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF",
                             f"Manifest-Version: 1.0\r\nMain-Class: {main_class}\r\n\r\n")
            archive.writestr(main_class.replace(".", "/") + ".class", body)
        return path

    def test_empty_terminal_opens_system_console(self):
        with patch("core.terminal.subprocess.Popen") as process:
            launch_terminal("  ", "", self.root, ["cmd.exe", "/k"])
        process.assert_called_once_with(["cmd.exe", "/k"], cwd=str(self.root),
                                        creationflags=CREATE_NEW_CONSOLE, close_fds=True)
        with self.assertRaisesRegex(RuntimeError, "未配置终端"):
            terminal_argv("", "", self.root, ["cmd.exe"])

    def test_known_terminals_and_generic_fallback(self):
        command = ["cmd.exe", "/d", "/s", "/k", "echo hi"]
        quoted = subprocess.list2cmdline([str(self.root)])
        wt = self.terminal("wt.exe")
        _, args = terminal_argv(str(wt), "", self.root, command)
        self.assertTrue(args.startswith(f"-d {quoted}"))
        wez = self.terminal("wezterm.exe")
        _, args = terminal_argv(str(wez), "", self.root, command)
        self.assertTrue(args.startswith(f"start --cwd {quoted} -- "))
        alacritty = self.terminal("alacritty.exe")
        _, args = terminal_argv(str(alacritty), "", self.root, command)
        self.assertTrue(args.startswith(f"--working-directory {quoted} -e "))
        generic = self.terminal("other-terminal.exe")
        _, args = terminal_argv(str(generic), "", self.root, command)
        self.assertTrue(args.startswith("-e cmd.exe"))

    def test_template_and_missing_command(self):
        terminal = self.terminal("custom.exe")
        _, args = terminal_argv(str(terminal), '--dir "{cwd}" -- {command}', self.root,
                                ["cmd.exe", "/c", "echo hi"])
        self.assertEqual(args, f'--dir "{self.root}" -- cmd.exe /c "echo hi"')
        with self.assertRaises(RuntimeError):
            terminal_argv(str(terminal), '--dir "{cwd}"', self.root, ["cmd.exe"])

    def test_folio_uses_generic_fallback(self):
        folio = self.terminal("folio.exe")
        target, args = terminal_argv(str(folio), "", self.root, ["cmd.exe"])
        self.assertEqual(target, str(folio))
        self.assertTrue(args.startswith("-e "))

    def test_scoop_shim_and_wezterm_gui_are_resolved(self):
        real = self.root / "real.exe"
        real.write_bytes(b"")
        shim = self.root / "term.exe"
        shim.write_bytes(b"")
        shim.with_suffix(".shim").write_text(f'path = "{real}"', encoding="utf-8")
        target, _ = terminal_argv(str(shim), "", self.root, ["cmd.exe"])
        self.assertEqual(target, str(real))
        console = self.root / "wezterm.exe"
        console.write_bytes(b"")
        (self.root / "wezterm-gui.exe").write_bytes(b"")
        target, args = terminal_argv(str(console), "", self.root, ["cmd.exe"])
        self.assertEqual(target, str(self.root / "wezterm-gui.exe"))
        self.assertTrue(args.startswith("start "))

    def test_gui_jar_uses_javaw_and_cli_jar_stays_in_terminal(self):
        from core.terminal import gui_command, jar_is_gui
        java = self.terminal("java.exe")
        javaw = self.terminal("javaw.exe")
        gui = self.jar("gui.jar", "demo.Main", b"\xca\xfe\xba\xbe javafx/application/Application")
        cli = self.jar("cli.jar", "demo.Main", b"\xca\xfe\xba\xbe plain Main")
        self.assertTrue(jar_is_gui(gui))
        self.assertFalse(jar_is_gui(cli))

        spec = gui_command(f'"{java}" -Xmx2g -jar "{gui}" --flag', self.root)
        self.assertEqual(spec, (javaw, f'-Xmx2g -jar {subprocess.list2cmdline([str(gui)])} --flag'))
        self.assertIsNone(gui_command(f'"{java}" -jar "{cli}"', self.root))

    def test_gui_jar_skips_terminal_end_to_end(self):
        java = self.terminal("java.exe")
        javaw = self.terminal("javaw.exe")
        gui = self.jar("gui.jar", "demo.Main", b"\xca\xfe\xba\xbe javax/swing/JFrame")
        entry = Entry("gui", self.root, "cat", "分类", True)
        entry.command = f'"{java}" -jar "{gui}"'
        with patch("core.launch_history.launcher._shell_execute") as direct, \
                patch("core.terminal.launch_terminal") as external:
            identifier, message = start(entry, "", None, False, self.root / "launches",
                                        terminal=str(self.terminal("wezterm.exe")))
        self.assertIsNone(identifier)
        self.assertIn("已启动", message)
        external.assert_not_called()
        self.assertEqual(direct.call_args.args[0], javaw)

    def test_pe_subsystem_detection(self):
        self.assertTrue(is_gui_exe(Path(os.environ["SystemRoot"]) / "explorer.exe"))
        self.assertFalse(is_gui_exe(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"))

    def test_launch_uses_terminal_instead_of_worker(self):
        terminal = self.terminal("wezterm.exe")
        tool = self.root / "tool"
        tool.mkdir()
        entry = Entry("demo", tool, "cat", "分类", True)
        entry.command = "echo hi"
        with patch("core.terminal.launch_terminal") as launch:
            identifier, message = start(entry, "", None, False, self.root / "launches",
                                        terminal=str(terminal))
        self.assertIsNone(identifier)
        self.assertIn("已在终端启动", message)
        launch.assert_called_once()
        _, _, cwd, argv = launch.call_args[0]
        self.assertEqual(cwd, tool)
        self.assertIn("cmd.exe", argv)
        self.assertEqual(argv[-1], "echo hi")

    def test_gui_exe_skips_terminal(self):
        entry = Entry("explorer", Path(os.environ["SystemRoot"]) / "explorer.exe",
                      "cat", "分类", False)
        with patch("core.launch_history.launcher._shell_execute") as direct:
            _, message = start(entry, "", None, False, self.root / "launches",
                               terminal=str(self.terminal("wezterm.exe")))
        self.assertIn("已启动", message)
        direct.assert_called_once()

    def test_saved_gui_command_preserves_arguments_without_terminal_or_worker(self):
        exe = Path(os.environ['SystemRoot']) / 'explorer.exe'
        entry = Entry('GUI', self.root, 'cat', '分类', True)
        entry.command = f'"{exe}" "D:/My Files"'
        for terminal, admin in [('', False), ('wezterm.exe', False), ('', True)]:
            with patch('core.launch_history.launcher._shell_execute') as direct, patch('core.terminal.launch_terminal') as external:
                identifier, _ = start(entry, '/n', None, admin, self.root/'launches', terminal=terminal)
            self.assertIsNone(identifier)
            self.assertEqual(direct.call_args.args, (exe, admin, self.root, '"D:/My Files" /n'))
            external.assert_not_called()
        self.assertFalse((self.root/'launches').exists())

    def test_gui_with_environment_launches_directly(self):
        exe = Path(os.environ['SystemRoot']) / 'explorer.exe'
        entry = Entry('GUI', exe, 'cat', '分类', False, command=f'"{exe}" /n')
        environment = dict(os.environ, SUPERBAY_TEST='isolated')
        with patch('core.launch_history.subprocess.Popen') as process:
            start(entry, '', environment, False, self.root/'launches', terminal='wezterm.exe')
        self.assertEqual(process.call_args.kwargs['env'], environment)
        self.assertEqual(process.call_args.kwargs['cwd'], str(exe.parent))

    def test_gui_path_with_spaces_and_bound_admin_environment(self):
        import shutil
        folder = self.root / 'GUI Tool'; folder.mkdir()
        exe = folder / 'demo.exe'
        shutil.copyfile(Path(os.environ['SystemRoot'])/'explorer.exe', exe)
        entry = Entry('GUI', folder, 'cat', '分类', True, command='"demo.exe" /n')
        environment = dict(os.environ, SUPERBAY_TEST='bound')
        with patch('core.launch_history.powershell_arguments', return_value='encoded') as encode, \
                patch('core.launch_history.launcher._shell_execute_w') as shell:
            start(entry, '', environment, True, self.root/'launches', terminal='wezterm.exe')
        self.assertEqual(encode.call_args.args, (str(exe), '/n', folder, environment))
        self.assertEqual(shell.call_args.args, ('runas', 'powershell.exe', 'encoded', folder))
        self.assertEqual(shell.call_args.kwargs, {'show': 0})

    def test_gui_detection_keeps_shell_expressions_and_console_programs(self):
        from core.terminal import gui_command
        exe = Path(os.environ['SystemRoot']) / 'explorer.exe'
        for command in (f'"{exe}" > output.txt', f'"{exe}" && echo done', 'cmd.exe /c echo hi'):
            self.assertIsNone(gui_command(command, self.root))
        self.assertIsNotNone(gui_command(f'& "{exe}" /n', self.root, shell='powershell'))
        self.assertIsNotNone(gui_command('explorer.exe /n', exe.parent))

    def test_environment_uses_powershell_wrapper(self):
        terminal = self.terminal("wezterm.exe")
        tool = self.root / "tool"
        tool.mkdir()
        entry = Entry("demo", tool, "cat", "分类", True)
        entry.command = "echo hi"
        entry.runtime_profile = {"root": str(self.root), "versions": {}}
        with patch("core.launch_history.powershell_arguments",
                   return_value="-NoLogo -NoProfile -EncodedCommand QUJD"), \
                patch("core.terminal.launch_terminal") as launch:
            start(entry, "", {"PATH": "C:/python"}, False, self.root / "launches",
                  terminal=str(terminal))
        argv = launch.call_args[0][3]
        self.assertIn("powershell.exe", argv)
        self.assertIn("QUJD", argv)


if __name__ == "__main__":
    unittest.main()
