# Single-file Windows build

From the repository root:

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller==6.22.3
python -m PyInstaller --noconfirm --distpath release --workpath .build/pyinstaller packaging/SuperBay.spec
```

The deliverable is `release/SuperBay.exe`. Python, Qt libraries, icons and theme files are included. The executable does not include personal settings, tool files or saved credentials.

The frozen executable stores settings, metadata and launch logs in `%LOCALAPPDATA%/SuperBay`. On first run, data from the previous `%LOCALAPPDATA%/SuperGUI` directory is copied if the new directory does not exist; the old directory is retained. Source runs continue using the repository's `config` directory. Read-only bundle assets are resolved separately so settings survive extraction cleanup.

Tracked tool launches run the same executable with an internal worker mode; tools do not need a second SuperBay executable or a separately installed Python interpreter for the worker. Python-based tools themselves still need their chosen Python runtime, as before.

Before opening the packaged version, exit the old version from its tray menu to release the single-instance lock.
