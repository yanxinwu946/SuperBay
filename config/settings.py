"""设置读写：config/config.yaml，人类可读、可手改。"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEFAULTS = {
    "workspace_root": "",      # 空则启动时探测/询问
    "workspace_presets": None,   # 目录快捷入口 [{name, path}]
    "theme": "system",         # system | light | dark
    "view": "list",            # grid | list
    "detail_visible": False,   # 详情面板按需打开，给结果保留更多空间
    "show_tags": False,        # 侧栏标签云默认隐藏
    "hotkey": "Ctrl+Alt+K",
    "scoop_root": "",          # 空则自动发现当前 Windows 用户的 Scoop 安装
    "terminal": "",            # 终端程序路径；空则使用系统控制台
    "terminal_args": "",       # 终端参数模板，{cwd} / {command}
    "scan_depth": 2,           # 目录层级，工作区根目录不计入
    "window": {"w": 1440, "h": 860, "x": None, "y": None, "maximized": False},  # x/y 为空则交给系统摆放
}

def _desktop() -> Path:
    return Path.home() / "Desktop"

def _candidate_workspaces() -> list[Path]:
    """通用候选：用户目录和各盘符根下的 Tools / Toolbox，谁存在用谁。

    个人路径别写在这里（会进仓库），固定工作区直接配 config.yaml。
    """
    cands = [Path.home() / "Tools"]
    for drive in "CDEF":
        for name in ("Tools", "Toolbox"):
            cands.append(Path(f"{drive}:/{name}"))
    return cands


def default_workspace_presets() -> list[dict]:
    """顶栏两个工作区按钮的默认预设：工具=探测到的 Tools 目录，桌面=桌面。"""
    desktop = _desktop()
    tools = next((p for p in _candidate_workspaces() if p.is_dir()), desktop)
    return [{"name": "工具", "path": str(tools)},
            {"name": "桌面", "path": str(desktop)}]


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._data = dict(DEFAULTS)
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                # 深合并一层（window 这种嵌套 dict）
                for k, v in loaded.items():
                    if isinstance(v, dict) and isinstance(self._data.get(k), dict):
                        self._data[k].update(v)
                    else:
                        self._data[k] = v
            except (yaml.YAMLError, OSError) as e:
                log.error("读取设置失败，使用默认值: %s", e)
        # 标题栏统一走系统原生、启动配置已移除：清理旧配置。
        if any(key in self._data for key in ("titlebar", "launch_presets")):
            self._data.pop("titlebar", None)
            self._data.pop("launch_presets", None)
            self.save()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def update(self, values: dict) -> None:
        self._data.update(values)
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
        except OSError as e:
            log.error("保存设置失败: %s", e)

    def get_presets(self) -> list[dict]:
        """顶栏工作区按钮预设（固定两条）。未配置或字段缺失时用默认值补齐。"""
        raw = self.get("workspace_presets")
        if raw is None:
            return default_workspace_presets()
        presets = []
        for value in raw:
            if isinstance(value, dict) and value.get("path"):
                presets.append({"name": str(value.get("name") or Path(value["path"]).name),
                                "path": str(value["path"])})
        return presets

    def set_presets(self, presets: list[dict]) -> None:
        self.set("workspace_presets", presets)

    def detect_workspace(self) -> str:
        """返回已配置的工作区；未配置则探测常见位置，最后兜底桌面。"""
        root = self.get("workspace_root")
        if root and Path(root).is_dir():
            return root
        for candidate in _candidate_workspaces():
            if candidate.is_dir():
                self.set("workspace_root", str(candidate))
                return str(candidate)
        desktop = _desktop()
        if desktop.is_dir():
            self.set("workspace_root", str(desktop))
            return str(desktop)
        return ""
