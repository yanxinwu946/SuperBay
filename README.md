# SuperGUI

让散落的工具，成为顺手的工作台。

SuperGUI 是面向 Windows 的本地工具启动器。查找工具、选择运行环境、保存启动配置、记录使用心得，都在一个安静清晰的界面中完成。

## 一个入口，接住日常工作

- **想到就能找到**：搜索名称、标签和笔记，固定常用工具，快速回到最近使用。
- **顺手启动**：识别启动入口，保存多套参数和命令，需要时以管理员身份运行。
- **环境各得其所**：连接 Scoop，为工具选择 Python、Java 或 Node 版本。
- **目录自由切换**：保存常用目录快捷入口，专注当前工作区。
- **经验留在手边**：Markdown 笔记随工具保存，运行记录方便回看。
- **换电脑也从容**：导出配置备份，迁移目录与环境绑定。

浅色与深色主题采用统一的 Glass UI 视觉。原生透明材质取决于 Windows 版本与系统设置，不支持时自动回退。

## 开始使用

需要 Windows 10 / 11、Python 3.10+。

```powershell
git clone https://github.com/yanxinwu946/SuperGUI.git
cd SuperGUI
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

已有 Python 环境也可安装依赖后运行 `python main.py`，或双击 `start.vbs`（使用系统 Python）。

首次启动选择工具目录：一级文件夹作为分类，分类内的文件与文件夹作为工具条目。添加工具后自动更新，也可按 `F5` 刷新。

## 常用操作

| 快捷键 | 操作 |
| --- | --- |
| Alt+Space | 显示 / 隐藏窗口（可在设置中修改） |
| Ctrl+K | 搜索 |
| Ctrl+P | 快速启动 |
| Enter | 启动 |
| Ctrl+Enter | 管理员启动 |
| Shift+Enter | 带参数启动 |
| Ctrl+D | 固定 / 取消固定 |
| Ctrl+Shift+R | 设置运行环境 |
| Ctrl+B | 显示 / 隐藏详情 |
| Ctrl+Shift+F | 清除筛选 |

条目快捷键在结果区生效。通过「工具管理」管理目录入口、启动记录、健康检查和备份；通过工具右键菜单选择启动入口与运行环境。

## 数据与迁移

工具文件和笔记留在原目录。设置、工具元数据和使用记录保存在本地 `config/`，运行日志不会上传到仓库。

建议通过「工具管理 → 备份与迁移」导出配置。备份不包含工具文件和运行日志；更换电脑时另行复制工具目录并安装所需环境。

## 开发

```powershell
python -m unittest discover -s tests -q
python tools/benchmark.py --help
```

| 目录 | 职责 |
| --- | --- |
| `main.py` | 应用入口、单实例与全局热键 |
| `config/` | 设置与数据库访问 |
| `core/` | 扫描、搜索、启动、环境与备份逻辑 |
| `ui/main_window.py` | 主窗口与交互协调 |
| `ui/theme.py`、`ui/themes/` | 主题加载、共享控件与 Glass 材质 |
| `ui/widgets/` | 独立界面组件 |
| `assets/` | 图标与控件资源 |
| `tests/`、`tools/` | 自动测试与性能工具 |
| `docs/` | 使用说明与开发资料 |

[使用说明与目录命名](docs/user-guide.md) · [使用与配置](docs/reference.md) · [工具工作台](docs/toolbox-features.md) · [开发与性能](docs/modernization.md)

## 许可证

[MIT License](LICENSE)。
