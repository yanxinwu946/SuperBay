"""Per-tool exact runtime pins; choosing a profile never resets user defaults."""
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from ui.widgets.modern_combo import ModernComboBox as QComboBox

from core import scoop


class RuntimeProfileDialog(QDialog):
    def __init__(self, entry, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"运行环境 · {entry.name}")
        self.resize(560, 320)
        saved = entry.runtime_profile
        configured = saved.get("root") or settings.get("scoop_root")
        self.root = Path(configured) if configured else scoop.detect_root()
        self.combos = {}
        self.missing = set()
        layout = QVBoxLayout(self)
        heading = QLabel(entry.name)
        heading.setObjectName("ContentTitle")
        heading.setWordWrap(True)
        heading.setToolTip(entry.name)
        layout.addWidget(heading)
        note = QLabel("选择此工具使用的版本。")
        note.setWordWrap(True)
        layout.addWidget(note)
        path_text = str(self.root) if self.root else "未设置 Scoop 目录"
        path = QLabel(path_text)
        path.setObjectName("ContentSub")
        path.setWordWrap(True)
        path.setToolTip(path_text)
        layout.addWidget(path)
        try:
            runtimes = scoop.scan(self.root) if self.root else []
            error = ""
        except (OSError, ValueError) as exc:
            runtimes, error = [], str(exc)
        form = QFormLayout()
        for family in scoop.BINARIES:
            combo = QComboBox()
            combo.addItem("继承默认", None)
            for runtime in runtimes:
                if runtime.family == family:
                    combo.addItem(f"{runtime.package}  ·  {runtime.version}",
                                  {"package": runtime.package, "version": runtime.version})
            previous = saved.get("versions", {}).get(family)
            if previous:
                index = combo.findData(previous)
                if index < 0:
                    combo.addItem(f"不可用：{previous.get('package')} @ {previous.get('version')}", previous)
                    index = combo.count()-1
                    self.missing.add((family, index))
                combo.setCurrentIndex(index)
            self.combos[family] = combo
            form.addRow(family, combo)
        layout.addLayout(form)
        self.hint = QLabel(error)
        self.hint.setVisible(bool(error))
        self.hint.setWordWrap(True)
        self.hint.setObjectName("ContentSub")
        layout.addWidget(self.hint)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
                                  | QDialogButtonBox.StandardButton.Reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("PrimaryBtn")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("GhostBtn")
        buttons.button(QDialogButtonBox.StandardButton.Reset).setText("清除绑定")
        buttons.button(QDialogButtonBox.StandardButton.Reset).setObjectName("GhostBtn")
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._clear)
        layout.addWidget(buttons)

    def _clear(self):
        for combo in self.combos.values():
            combo.setCurrentIndex(0)

    def profile(self):
        versions = {family: combo.currentData() for family, combo in self.combos.items() if combo.currentData()}
        return {"root": str(self.root), "versions": versions} if versions else {}

    def accept(self):
        if any(combo.currentData() and (family, combo.currentIndex()) in self.missing
               for family, combo in self.combos.items()):
            self.hint.setText("版本不可用，请重新选择。")
            self.hint.show()
            return
        super().accept()
