# X11 产品接入与 Godot macOS 自写样本验收

## 当前状态

用户已批准 Godot 4.5.1 官方 macOS universal 精确资产下载、X11 隔离辅助进程与冻结内部入口实施，并要求收尾删除可再生临时产物。源码实现和验收入口已准备；原生执行尚未发生，不能计为产品支持。本文不修改 Metal 原失败结论。

本机为 Windows，没有可用的 WSL Linux 发行版或 Swift 编译器。原生验收需使用已有 GitHub Actions Linux／macOS 临时环境。本批代码尚未推送到远端，也未触发原生任务；下载许可和架构许可不等于远端推送许可。

## 已实现的范围

- X11：`gameqakit/x11_capture.py`、`x11_helper.py`、`x11_native.py`，通过现有有界帧管道连接公共采集会话。原生 Xlib 仅在隔离辅助进程加载。
- 来源绑定 DISPLAY、X server 身份、XID、PID、进程启动 tick 和可执行路径；截图前后复核，尺寸变化重建 pixmap。命名 pixmap 缺少 RGB mask 时使用已确认窗口的 TrueColor visual。
- 截图仍采用现有 3 秒期限，支持取消和辅助进程回收；不添加输入、焦点切换、整屏裁切回退或 X 认证修改。
- Linux 平台分派、CLI／MCP／采集观察入口和 Unix 冻结内部入口已接入验证分支；默认入口保持关闭，仅原生验证任务通过 `GAMEQA_X11_VALIDATION=1` 启用。未合并或发布，原生验收通过前不得据此宣布 Linux 产品采集支持。
- Godot 仅复用既有自写场景及 `plugins/godot/GameQaBridge.gd`。新夹具只补充实际按键状态和位置记录，不改变桥协议。此轮为独立桥／运行时试点，尚未迁入 macOS 正式受管 MCP 游戏入口。

## 原生验收入口

独立工作流为 `.github/workflows/x11-godot-product.yml`，只对 `experiment/x11-godot-product` 分支 push 自动执行。不要推送原 `feature/macos-arm-capture-product` 分支，否则会触发旧 Metal 产品任务。工作流只有只读仓库权限，不创建发布或标签。

| 线路 | 环境与动作 | 通过所需证据 |
| --- | --- | --- |
| X11 源码与冻结 MCP | Ubuntu 22.04 x64、Xvfb、现有 X11／Composite／GL 系统库；分别运行 CPU 和 Mesa 软件 OpenGL 自写窗口 | 完整遮挡桌面图、实际计数逐张增长、冻结反例、遮挡中改尺寸、持续观察与并发单次截图、人工标记、归档来源、目标退出、正常回收 |
| X11 故障 | 每个场景独立启动 X server；身份错配、取消映射、辅助进程退出、服务器卡住及断连 | 有限错误、取消响应、辅助进程结束与回收；不停止继承的桌面服务器 |
| Godot macOS | macOS arm64，仅下载已批准的官方 4.5.1 universal 资产 | 实际字节数及 SHA-256、MIT 来源、Mach-O arm64 与版本；自写场景右移 40 像素及截图变化、左移、等待、暂停松键、租约到期松键、独立桌面遮挡证明及退出回收 |

精确 Godot 资产为 `Godot_v4.5.1-stable_macos.universal.zip`，160,646,527 字节，SHA-256 `65c27959d02aaacfc131ec7ecb90179ba8045200cb02982bf2be96d117010b8a`。当前只有上游登记值，尚未取得文件，不能声称摘要已实测。来源与许可由工作流写入证据；普通桌面授权、真实 GPU、商业游戏和其他架构均未验。

## 本地验证与原失败保留

- X11 新增 9 项聚焦用例已通过；最初 7 项 RED 因后端缺失失败，随后同组转绿，另补 pixmap visual 和默认关闭用例。默认关闭用例先实际失败，再加入显式验证开关转绿。
- 受影响回归原 44 项通过，日志为 `<macOS产品实施目录>/next-stage-regression.log`。补齐默认关闭后，相关 50 项中 33 项通过、17 项环境跳过、零失败，6.909 秒，日志 `next-stage-gate-green.log`。
- 首次全量 974 项中 3 项 Bakin CLR 测试失败、314 项环境跳过，日志 `next-stage-full-regression.log` 必须保留。其中记录过文件 flush 超过原期限；不延长期限。
- 失败 3 项隔离运行通过，Bakin 完整模块 31 项通过；串行全量复核 974 项，660 通过、314 环境跳过、零失败，188.480 秒。日志 `next-stage-full-serial.log`，退出码 0 见 `next-stage-full-serial-result.json`。当前只能说明复核未复现，根因未定，不能追认首轮全量通过。
- 最终补齐默认关闭后再次串行全量：975 项，661 通过、314 环境跳过、零失败，200.032 秒；日志 `next-stage-gated-full.log`，退出码 0 见 `next-stage-gated-full-result.json`。原首轮 Bakin 失败仍保留待查。
- 静态检查覆盖 17 份改动 Python 文件、工作流 YAML、14 段 shell 及内嵌 Python 和 `git diff --check`。两处含冒号的 pip 命令已改为 YAML 块标量；这些静态结果不代替 Swift 编译和原生执行。

## 清理与回退

本机仅清理本批 `next-stage-temp/` 可再生缓存，保留所有回归日志、失败证据、源码和交接。云端 Godot 任务在上传证据后，即使失败也删除精确运行时压缩包、展开目录与临时编译探针，不删 `validation-output/`。

回退时撤销 X11 平台分派、Unix 内部入口和新增 X11 模块，恢复此前 Linux 公共功能范围；Godot 可独立撤销新夹具与探针。既有 Windows／macOS 后端、原游戏及存档不需迁移。工作流不属于正式发布入口。
