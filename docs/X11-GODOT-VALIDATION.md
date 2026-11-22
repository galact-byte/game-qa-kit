# X11 产品接入与 Godot macOS 自写样本验收

## 当前状态

用户已批准 Godot 4.5.1 官方 macOS universal 精确资产下载、X11 隔离辅助进程与冻结内部入口实施，以及向独立验证分支推送并运行云端验收，要求收尾删除可再生临时产物。云端运行 `44997438877`（提交 `c181a0d`）的 X11 源码／冻结产品链与故障回收通过，Godot 在第一次输入前因目标成为前台应用停止。本批整体未通过，不修改 Metal 原失败结论。

本机为 Windows，没有可用的 WSL Linux 发行版或 Swift 编译器。原生结果来自既有 GitHub Actions Ubuntu 22.04 x64／macOS arm64 临时环境；独立分支为 `experiment/x11-godot-product`，未合并主线或发布。X11 默认入口仍关闭，已验证范围仅为 Xvfb 自写窗口，不外推普通桌面、物理 GPU、Wayland 或真实游戏。

## 已实现的范围

- X11：`gameqakit/x11_capture.py`、`x11_helper.py`、`x11_native.py`，通过现有有界帧管道连接公共采集会话。原生 Xlib 仅在隔离辅助进程加载。
- 来源绑定 DISPLAY、X server 身份、XID、PID、进程启动 tick 和可执行路径；截图前后复核，尺寸变化重建 pixmap。命名 pixmap 缺少 RGB mask 时使用已确认窗口的 TrueColor visual。
- 截图仍采用现有 3 秒期限，支持取消和辅助进程回收；不添加输入、焦点切换、整屏裁切回退或 X 认证修改。
- Linux 平台分派、CLI／MCP／采集观察入口和 Unix 冻结内部入口已接入验证分支；默认入口保持关闭，仅原生验证任务通过 `GAMEQA_X11_VALIDATION=1` 启用。未合并或发布；本轮通过仅覆盖下述自写窗口与临时云端环境，不扩展为所有 Linux 桌面支持。
- Godot 仅复用既有自写场景及 `plugins/godot/GameQaBridge.gd`。新夹具只补充实际按键状态和位置记录，不改变桥协议。此轮为独立桥／运行时试点，尚未迁入 macOS 正式受管 MCP 游戏入口。

## 原生验收入口

独立工作流为 `.github/workflows/x11-godot-product.yml`，只对 `experiment/x11-godot-product` 分支 push 自动执行。不要推送原 `feature/macos-arm-capture-product` 分支，否则会触发旧 Metal 产品任务。工作流只有只读仓库权限，不创建发布或标签。

| 线路 | 环境与动作 | 通过所需证据 |
| --- | --- | --- |
| X11 源码与冻结 MCP | Ubuntu 22.04 x64、Xvfb、现有 X11／Composite／GL 系统库；分别运行 CPU 和 Mesa 软件 OpenGL 自写窗口 | 完整遮挡桌面图、实际计数逐张增长、冻结反例、遮挡中改尺寸、持续观察与并发单次截图、人工标记、归档来源、目标退出、正常回收 |
| X11 故障 | 每个场景独立启动 X server；身份错配、取消映射、辅助进程退出、服务器卡住及断连 | 有限错误、取消响应、辅助进程结束与回收；不停止继承的桌面服务器 |
| Godot macOS | macOS arm64，仅下载已批准的官方 4.5.1 universal 资产 | 实际字节数及 SHA-256、MIT 来源、Mach-O arm64 与版本；自写场景右移 40 像素及截图变化、左移、等待、暂停松键、租约到期松键、独立桌面遮挡证明及退出回收 |

精确 Godot 资产为 `Godot_v4.5.1-stable_macos.universal.zip`，云端实测 160,646,527 字节，SHA-256 `65c27959d02aaacfc131ec7ecb90179ba8045200cb02982bf2be96d117010b8a`，与批准值一致。下载记录、MIT 许可、`x86_64 arm64` 架构及 `4.5.1.stable.official.f62fdbde1` 版本已取回核对；实际运行 arm64。普通桌面授权、真实 GPU、商业游戏和其他架构均未验。

## 云端结果及独立复核

最新运行：[44997438877](https://github.com/galact-byte/game-qa-kit/actions/runs/44997438877)。原始产物与独立报告均保存在 `<macOS产品实施目录>/native-44997438877/`，不随源码提交。

- X11 的源码／冻结 × CPU／Mesa 软件 OpenGL 四组通过真实 MCP 的单次截图、持续观察、人工标记、归档来源、完整遮挡新图、冻结反例、遮挡中改尺寸和目标退出检查。逐张独立核对 96 张 PNG 的计数、尺寸与 SHA-256，桌面目标区域共 3,379,200 像素全部为遮挡色，正常截图最慢 312.483 毫秒；各组焦点保持，目标及遮挡进程正常退出。归档记录分别为源码 CPU 18／OpenGL 19、冻结 CPU 13／OpenGL 13 帧。
- 两种形态各 5 项故障检查通过：来源 PID 变化、取消映射、辅助进程退出、X server 卡住与断连。卡住组按原 3 秒截图期限报错，含辅助进程回收的实测总耗时为 3.0121／3.0129 秒；未改变期限。全部辅助进程与独立 X server 均记录退出。复核入口为 `verify_native_evidence.py`，报告 `independent-x11-review.json`。
- Godot 成功加载自写 PCK，初始暂停、无持键，首图含预期方块；独立复算 85,760 个桌面遮挡像素，零差异。但目标 PID 与前台应用 PID 同为 3270，违反后台运行约束；在首次输入前停止，动作数为 0。移动、暂停松键、失租释放和正式受管 MCP 入口均未验。遮挡进程正常退出，目标进程终止并等待回收；此回收不冒认为正常游戏退出验收。报告 `independent-godot-review.json`，原始 `godot/proof.json` 与 `godot/result.json` 保留失败。
- 4.5.1 的 [`platform/macos/display_server_macos.mm`](https://github.com/godotengine/godot/blob/4.5.1-stable/platform/macos/display_server_macos.mm#L1859) 中，`DisplayServerMacOS::show_window()` 对主窗口先调用 `GodotApp activateApplication`，随后才检查 `wd.no_focus`。项目设置 `display/window/size/no_focus` 不足以阻止这条启动激活路径。此前错误使用不存在的 `--no-focus` CLI 参数已删除；不能将这次失败归因于遮挡不足，也不以重新抢回焦点作为修复。
- 首轮 `44996785949` 停在 Linux 缺 `libEGL.so.1`、Swift 浮点 `.1` 编译错误；已修正安装依赖与 `0.1` 写法。第二轮 `44997094081` 的 X11 源码通过，冻结构建缺中文字体，Godot 使用了错误参数；已补构建所需字体并改为真实项目设置。失败批次全部保留，不追认通过。

当前 Godot 精确官方运行时不满足本批后台启动判据；继续需要先确定并批准运行时／原生启动适配的实际变更范围，不能在现有精确资产授权下自行替换构建或修改系统设置。已否定的参数路径不重复运行。

## 本地验证与原失败保留

- X11 新增 9 项聚焦用例已通过；最初 7 项 RED 因后端缺失失败，随后同组转绿，另补 pixmap visual 和默认关闭用例。默认关闭用例先实际失败，再加入显式验证开关转绿。
- 受影响回归原 44 项通过，日志为 `<macOS产品实施目录>/next-stage-regression.log`。补齐默认关闭后，相关 50 项中 33 项通过、17 项环境跳过、零失败，6.909 秒，日志 `next-stage-gate-green.log`。
- 首次全量 974 项中 3 项 Bakin CLR 测试失败、314 项环境跳过，日志 `next-stage-full-regression.log` 必须保留。其中记录过文件 flush 超过原期限；不延长期限。
- 失败 3 项隔离运行通过，Bakin 完整模块 31 项通过；串行全量复核 974 项，660 通过、314 环境跳过、零失败，188.480 秒。日志 `next-stage-full-serial.log`，退出码 0 见 `next-stage-full-serial-result.json`。当前只能说明复核未复现，根因未定，不能追认首轮全量通过。
- 最终补齐默认关闭后再次串行全量：975 项，661 通过、314 环境跳过、零失败，200.032 秒；日志 `next-stage-gated-full.log`，退出码 0 见 `next-stage-gated-full-result.json`。原首轮 Bakin 失败仍保留待查。
- 静态检查覆盖 17 份改动 Python 文件、工作流 YAML、14 段 shell 及内嵌 Python 和 `git diff --check`。两处含冒号的 pip 命令已改为 YAML 块标量；这些静态结果不代替 Swift 编译和原生执行。

- 本次结果记录收尾仅修改文档；重新运行独立 X11／Godot 停止证据复核、工作流静态检查（2 份 Python、15 段脚本）与 `git diff --check` 均通过。Godot 复核通过表示停止证据一致，验收结果仍为失败；未因文档更新重跑原生批次。

## 清理与回退

本机仅清理本批可再生缓存，保留所有回归日志、失败证据、源码和交接。云端最新批次的清理步骤成功：删除运行时压缩包及展开目录共 523,117,374 字节，以及临时编译探针 115,888 字节；证据已上传并取回。删除操作和完整路径见云端日志 `Remove approved temporary runtime download after recording evidence`，不删 `validation-output/`。

回退时撤销 X11 平台分派、Unix 内部入口和新增 X11 模块，恢复此前 Linux 公共功能范围；Godot 可独立撤销新夹具与探针。既有 Windows／macOS 后端、原游戏及存档不需迁移。工作流不属于正式发布入口。
