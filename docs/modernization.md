# 开发与性能

## 结构

- `core/`：扫描、搜索索引、启动与运行环境解析。
- `config/`：设置、SQLite 元数据与迁移。
- `ui/`：Qt 模型视图、交互与主题。

条目以文件系统为数据源。固定、标签、命令、运行环境与使用记录存入 SQLite；当前 schema 为 v4。

## 性能策略

- 目录与笔记后台扫描，完成后构建搜索索引。
- 名称、标签、路径与笔记预处理一次；标签或笔记更新时只替换对应索引记录。
- 主界面与快速启动共享索引；快速启动仅选出前 80 项，不对全部命中结果排序。
- 结果顺序未变化时保留视图模型与选中状态；视图模式未变化时跳过布局配置。
- 系统图标按可见条目读取并缓存，同一条目的详情更新复用笔记内容。
- 工具专属环境直接校验指定版本，不枚举其他软件、不读取默认环境注册表。
- 搜索防抖：主界面 80 ms，快速启动 60 ms。无每秒定时刷新。

## 本机基准

10,000 条模拟记录，每条附约 1 KB 笔记；索引构建与搜索分开计时。下表为本轮修改前后测量，单位 ms。

| 场景 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 精确名称 | 35.97 | 8.08 |
| 多关键词 | 40.13 | 11.93 |
| 笔记命中 | 37.59 | 8.52 |
| 英文缩写 | 43.10 | 14.20 |
| 无匹配 | 34.75 | 5.73 |
| 3 种绑定环境解析 | 80.26 | 0.71 |

首次构建索引约 42.71 ms。搜索数据包含命中结果排序，不包含防抖等待、磁盘扫描与界面绘制；环境解析使用本机已安装版本。结果随设备和工作区变化。

复测：

```powershell
python tools/benchmark.py --entries 10000 --repeats 5
python -m unittest discover -s tests -v
```

测试覆盖搜索、索引更新、环境隔离、旧库迁移、笔记保护与界面交互。Windows 子进程环境传递和本机 Python 版本验证已实测；未修改用户默认版本，未实际弹出管理员提升窗口。

## 行为约定

- 固定版本缺失时停止启动并提示重新配置，不自动回退。
- 绑定环境在子进程内生效。目录工具通过启动命令使用绑定环境；`.py`、`.jar`、`.js` 可直接运行。
- Scoop 默认切换采用官方 `reset 包名@版本`，后台执行并重新验证状态。
- 运行环境验证超时为 15 秒，只结束验证进程。
- 扫描与工作区切换保留未保存笔记确认流程。

## 交互参考

[Flow Launcher](https://github.com/Flow-Launcher/Flow.Launcher) · [PowerToys Command Palette](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/overview) · [Scoop 多版本说明](https://github.com/ScoopInstaller/Scoop/wiki/Switching-Ruby%2C-Python-and-PHP-Versions)

## 材质与界面

参考 [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials) 的导航材质与内容层次原则：玻璃用于工具栏和导航，内容保持接近不透明，确保阅读与批量浏览效率。

- 默认使用 48 px 紧凑列表，按名称、位置、环境 / 标签组织信息；保留网格切换。
- 移除「分类」标题，侧栏直接显示目录入口；修复窄侧栏中的计数裁切。
- 材质画布按尺寸、缩放比例、主题与背景模式缓存，无连续动画或 CPU 模糊滤镜。
- Windows 11 使用 [DWM Desktop Acrylic](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type)。
- 仅在 Windows 11 22H2 或以上的系统原生背景接口可用时启用材质。Windows 10 及接口不可用时直接回退为不透明窗口；不使用兼容模糊接口或手工模糊标题栏。
- 尊重系统透明度和高对比度设置；不支持材质时采用不透明回退。
- 这不是 Apple 平台的 Liquid Glass 实现；采用其视觉层次，在 Windows 上使用本机合成能力。

本机 Windows 10 采用不透明回退。浅色、深色和 1100 px 窗口均经过渲染检查；Windows 11 路径尚未在该系统实测。
