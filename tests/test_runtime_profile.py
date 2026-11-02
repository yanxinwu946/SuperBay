import base64
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from config.db import DB, MIGRATIONS
from core import launcher
from core.models import Entry
from core.process_env import powershell_arguments


def decode_payload(arguments):
    script = base64.b64decode(arguments.split()[-1]).decode("utf-16-le")
    data = re.search(r"FromBase64String\('([^']+)'\)", script)[1]
    return json.loads(gzip.decompress(base64.b64decode(data)))


class ProfileTests(unittest.TestCase):
    def test_database_migration_preserves_existing_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data.db"
            conn = sqlite3.connect(path)
            for version in (1, 2, 3):
                conn.executescript(MIGRATIONS[version])
            conn.execute("INSERT INTO meta(path,pinned,tags,command) VALUES('tool',1,'java','java -jar app.jar')")
            conn.execute("PRAGMA user_version=3")
            conn.commit()
            conn.close()
            db = DB(path)
            profile = {"root": "D:/scoop", "versions": {"Java": {"package": "jdk", "version": "21"}}}
            db.set_runtime_profile(Path("tool"), profile)
            meta = db.all_meta()["tool"]
            self.assertEqual(meta["runtime_profile"], profile)
            self.assertTrue(meta["pinned"])
            self.assertEqual(meta["command"], "java -jar app.jar")
            db.close()

    def test_encoded_environment_is_lossless(self):
        env = {"PATH": "C:/a b;D:/语言", "QUOTED": "a'\";$()&%name%"}
        args = powershell_arguments("cmd.exe", '/k "echo hello"', Path("C:/a b"), env)
        payload = decode_payload(args)
        self.assertEqual(payload["environment"], env)
        self.assertEqual(payload["arguments"], '/k "echo hello"')

    def test_bound_python_file_and_admin_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "demo script.py"
            path.touch()
            entry = Entry(path.name, path, "tools", "工具", False)
            calls = []
            with patch.object(launcher, "_shell_execute_w", lambda *args: calls.append(args)):
                launcher.launch(entry, environment={"PATH": "C:/python"}, admin=True)
            verb, file, arguments, cwd = calls[0]
            payload = decode_payload(arguments)
            self.assertEqual(verb, "runas")
            self.assertEqual(file, "powershell.exe")
            self.assertEqual(payload["file"], "python.exe")
            self.assertIn(str(path), payload["arguments"])

    @unittest.skipUnless(os.name == "nt", "Windows process integration")
    def test_real_child_inherits_only_supplied_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            script = folder / "child.py"
            result = folder / "result.json"
            script.write_text("import os,json,pathlib; pathlib.Path('result.json').write_text(json.dumps(dict(os.environ)))")
            env = {"SystemRoot": os.environ["SystemRoot"], "PATH": str(Path(sys.executable).parent),
                   "TEST_VALUE": "中文 ' \" & $(); marker"}
            args = powershell_arguments(Path(sys.executable).name, subprocess.list2cmdline([str(script)]), folder, env)
            completed = subprocess.run(["powershell.exe", *args.split()], input="", capture_output=True,
                                       text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            actual = json.loads(result.read_text())
            self.assertEqual(actual["TEST_VALUE"], env["TEST_VALUE"])
            self.assertNotIn("SCOOP", actual)


if __name__ == "__main__":
    unittest.main()
