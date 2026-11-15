<div align="center">

<img src="assets/superbay-app.png" width="120" alt="SuperBay Logo">

# SuperBay

**让散落的工具，成为顺手的工作台。**

一个安静、快速的 Windows 本地工具启动器。
工具留在原地，SuperBay 负责让你随时找到它们、一键启动。

[项目官网](https://yanxinwu946.github.io/superbay/) · [使用说明](docs/user-guide.md) · [目录规划指南](docs/directory-planning.md)

</div>

---

## 为什么需要它

桌面堆满快捷方式、开始菜单翻不到、某个脚本上次放在哪已经想不起来——工具一多，找它们比用它们还费时间。

SuperBay 把这件事变简单：**指定一个工具目录，它自动扫描出所有可启动的程序和脚本**，之后搜索、启动、记录，都在一个窗口里完成。全程本地运行，不上传任何数据。

## 它能做什么

**找到工具**

- 自动扫描工作区，识别 `.exe`、脚本和各类工程项目，新增或移动目录后自动更新
- 支持按名称、拼音、标签、别名和笔记内容搜索
- 多工作区聚合搜索；规范命名的分类（如 `A-01-图像处理`）在侧栏以字母标识
- 列表 / 网格两种视图，方向键导航，`Space` 快速预览

**启动工具**

- 桌面程序直接打开；命令行程序和脚本可交给 WezTerm、Windows Terminal、Alacritty 或自定义终端
- 同一工具的多个启动入口可切换，常用命令可保存为「启动方案」，运行时再填参数
- 为工具绑定 Scoop 中的 Python、Node.js、Java 版本作为运行环境
- Python 脚本可无窗口后台运行，日志随手可查，退出时一并停止
- `Ctrl+Enter` 以管理员权限启动

**记住一切**

- 每个工具目录内直接写笔记（`NOTE.md`），停止输入即自动保存，跟着目录走
- 固定常用工具，最近使用一目了然
- 内置 Scoop 管理：批量安装、更新、冻结、切换版本、清理，一个界面管完

**安静常驻**

- 收起到托盘，`Alt+S` 随时唤回（快捷键可改），支持开机自启、启动时隐藏
- 主题、视图、关闭行为都在设置里，按你的习惯来

## 三步上手

1. **启动 SuperBay**，首次使用会出现引导页，介绍目录的推荐结构
2. **选择工作区**——指向存放工具的根目录即可，已有目录不需要移动任何文件
3. **开始搜索**——`Ctrl+K` 聚焦搜索框，回车启动

```text
D:\Tools\                    工作区根目录
├── A-01-图像处理\           分类
│   └── ImageTool\           工具目录（保持完整，不要拆散）
│       └── ImageTool.exe
└── B-02-视频处理\
    └── VideoTool\
```

一个工具保留一个完整文件夹，脚本、配置、输出都放在工具目录内。详细的规划建议见[目录规划指南](docs/directory-planning.md)。

## 数据在你手里

- 工具文件和笔记**始终留在原目录**，SuperBay 只读取，不搬运
- 设置、元数据和使用记录保存在本机（`%LOCALAPPDATA%\SuperBay`）
- 更换电脑：退出程序后复制 `config.yaml` 与 `data.db`，工具目录单独迁移即可

## 环境要求与安装

需要 Windows 10 / 11。从 [Releases](https://github.com/yanxinwu946/SuperBay/releases) 下载后直接运行，或双击 `start.vbs` 启动已构建的程序。

<details>
<summary><strong>从源码构建</strong></summary>

需要 Rust MSVC 工具链和 Visual Studio C++ Build Tools。

```powershell
cargo build --release --locked
.\target\release\superbay.exe
```

主要模块：`native/catalog.rs`（扫描识别）、`native/launch.rs`（启动与进程）、`native/scoop.rs`（Scoop 集成）、`native/storage.rs`（设置与数据库）、`native/view/`（界面与交互）。基于 Rust 与 GPUI 构建的 Windows 原生应用。

</details>

## 文档

| 文档 | 内容 |
| --- | --- |
| [使用说明](docs/user-guide.md) | 实际操作、启动方案、快捷键、迁移 |
| [目录规划指南](docs/directory-planning.md) | 如何组织工具目录（写给用户和 AI） |
| [启动、终端与 Scoop](docs/toolbox-features.md) | 外部终端、启动方案、运行环境、Scoop 管理 |

## 许可证

[MIT License](LICENSE)
