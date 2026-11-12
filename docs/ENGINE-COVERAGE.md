# 引擎控制覆盖与后续入口

这里列出后台输入适配的实际边界。识别引擎、桥源码存在、能截图和玩法验收是不同证据；`inspect_game` 的 `control_verified=false` 不会因识别成功自动变为通过。

| 引擎／运行环境 | 当前控制实现与已验范围 | 后续缺口与依据 |
|---|---|---|
| RPG Maker MV／MZ | 共用 JavaScript 桥；SDK 工程真实移动、菜单与协议安全检查 | 自定义插件、商业游戏仍逐款验收；见 [后台控制实证](BACKGROUND-CONTROL.md) |
| RPG Maker VX Ace／RGSS3 | 独立 Ruby 桥；SDK 和定制商业副本的正常读档、真实位移、菜单及暂停 | 不能直接用于 RGSS1／2；见 [后台控制实证](BACKGROUND-CONTROL.md) |
| Unity Mono／旧 Input | BepInEx 5；空白场景移动／菜单／UI，商业样本标题和对话 | 商业玩法及其他版本另验；见 [Unity 矩阵](../plugins/unity/README.md) |
| Unity Mono／新 Input System | 虚拟键鼠与设备隔离；Unity 6 空白场景真实位移、菜单和 UI | 商业样本及自定义设备另验 |
| Unity IL2CPP／新 Input System | BepInEx 6／Il2CppInterop；指定商业副本标题、难度与剧情 | 不代表 IL2CPP 旧 Input 或 Rewired 已支持，也未据此验商业角色位移 |
| Unity Mono／Rewired | 指定 Unity 5.6.7f1 商业副本正常推进至地图、左右实际位移与暂停 | Rewired API、其他中间件及动态程序集不自动覆盖 |
| Wolf RPG | 两个固定 EXE 哈希，原生桥；官方范例移动／菜单与战斗闭环；商业构建新游戏、移动／菜单、第 1 槽保存和跨进程读取，以及 30 轮移动／菜单重复测试 | 未测哈希拒绝；官方存读档、商业战斗、全流程及长期稳定性仍未验；见 [Wolf 记录](../plugins/wolf/README.md) |
| HSP3 | 官方 HSP 3.7 空白工程 getkey／stick、菜单／点击；八星帝外置字节码标题和读取菜单 | 原内嵌 EXE 不自动接入，商业玩法未验；见 [HSP 说明](../plugins/hsp/README.md) |
| DuckStation／PS1 | 一个固定 Windows x64 构建；原生帧计时、数字手柄，指定游戏移动、存读档和首战 | 这是模拟器适配；不是所有 PS1 模拟器通用。音频、换盘、其他构建未验；见 [说明](../plugins/duckstation/README.md) |
| SRPG Studio／x86 DirectInput | 一个固定商业 EXE 哈希 `8e123a4c07cfbc9143c266faf87bb66ef3600ab1254ee6d294fbaf685bb502a7`；`plugins/native_input/` 受限宿主／DLL，v2 显式毫秒动作，经真实 MCP 完成副本后台标题 → 环境设置 → 返回标题并逐图核对；自有窗口安全生命周期通过 | 自有窗口实体键鼠并发已通过并获参与者确认，不能替代正式游戏实体并发，后者仍未验；游戏鼠标、存档、玩法、其他构建及长期运行未验。只读档案识别不自动启动，也不将 `control_verified` 改为 true；见 [插件说明](../plugins/native_input/README.md) |
| RPG Maker XP／VX、2000／2003 | 仅按文件证据识别，无已验证的控制适配器 | XP／VX 当前缺本机可验运行样本；取得样本后先核查保存边界，再验证后台输入和取图 |
| Godot、Unreal、Ren'Py、TyranoScript、KiriKiri 等其他引擎 | 当前仓库没有已验控制通路 | 不继承汉化工具的支持清单，不宣称已适配 |

## 下一步的进入条件

- 现有桥的玩法扩展：使用已核查隔离副本，经统一 MCP 正常输入，并逐图验真实行为。战斗、存读档、重复操作分别记录，不用一次菜单成功替代。
- 新引擎或新输入后端：提供可运行样本和可核查保存位置；先做小范围后台运行、输入隔离、连续新画面实验，再决定实现方式。不得自动下载或安装未知运行时。
- 长期稳定性：有限次数循环只证明该批次。覆盖长时间运行、场景切换、失败恢复等需要单独定义工作负载与观测时长。
- 隔离兜底：独立 Win32 Desktop 已做实验；虚拟机与 Windows Sandbox 仍需确认现有环境、GPU、数据生命周期及授权。复制游戏目录本身不是数据沙箱。
- YOLO：先明确检测对象、样例和当前规则／OCR 的量化不足，再选择模型；不因存在待办就自动安装模型。

完整优先级与历史证据见 [ROADMAP](ROADMAP.md)，本页不构成任意游戏一键兼容承诺。
