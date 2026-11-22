# macOS／Linux 窗口采集与人工盯屏预检

状态：公共采集会话已实现；macOS arm64 的 Swift 辅助进程及源码／冻结 AppKit 产品链已验证，Metal 默认产品链仍未通过。cloud-24 读回确认新纹理，cloud-28 两种过滤均失败；cloud-29 冻结后重新露出的窗口／桌面／GPU 内容一致，但不追认原失败或确定唯一根因。Linux X11 独立探针已完成，正式设计已批准且源码已接入验证分支，默认入口关闭，仅显式验证开关启用，原生／冻结产品验收尚未执行；Wayland 尚未实现。对应 [路线图第二阶段](ROADMAP.md)。下述 Qt 源码核查是前期依据，基于项目固定的 PyQt6／Qt 6.10.0，不外推到其他版本。

## 目标与已确认边界

用户手动玩游戏时，工具持续观察指定游戏窗口，复用现有去重、疑点分析、人工标记和证据复核。采集不发送游戏输入，不置前台，不移动用户光标。窗口被其他应用遮挡时仍须取得目标的新画面；最小化另行验收。

单窗口与整屏是不同采集范围。尤其在 Wayland 下，不能以桌面截图后裁切代替窗口共享，不能在窗口授权失败时自动切换成整屏。

前期预检只核查源码、依赖与一手资料；后续已实现公共会话边界，并在 macOS 和 Linux X11 云端环境运行自写窗口探针。macOS 还完成 AppKit 源码／冻结产品链与真实组件故障回收检查。详细证据分别见 [macOS 设计与进展](MACOS-ARM-CAPTURE-DESIGN.md)、[X11 探针结果](X11-CAPTURE-PROBE.md)。未变更游戏或存档，也没有执行跨平台后台控制。

## 为什么不能直接启用现有 Qt 窗口采集

项目已包含 PyQt6 6.10.0、Qt 6.10.0 和 Pillow 11.1.0，优先检查它们能否承担采集。核对 Qt 对应版本源码后，结果如下：

| 目标环境 | Qt 6.10.0 的实际路径 | 对本项目的影响 |
| --- | --- | --- |
| macOS | `QWindowCapture` 选择 `QCGWindowCapture`，内部调用 `CGWindowListCreateImage`；ScreenCaptureKit 的屏幕路径不能等同窗口路径 | 不能因 Qt 有 ScreenCaptureKit 代码就认定窗口采集已采用它。旧接口在新系统上的可用范围、授权及遮挡需原生实测 |
| Linux X11 | `QX11SurfaceCapture` 对窗口 drawable 调用 `XShmGetImage`；所查实现未使用 `XCompositeNameWindowPixmap` | 尚无依据保证遮挡后仍得到完整且更新的窗口内容；不能把公开接口存在当作验收通过 |
| Linux Wayland | Qt 的 PipeWire 屏幕采集在 portal `SelectSources` 中使用 `types=1`，即显示器共享；窗口创建路径没有对应 portal 窗口后端 | 此路径不满足只共享游戏窗口。不能直接借 `QScreenCapture` 将整屏作为窗口交付 |

这不表示 Qt 在所有系统上都无法截取窗口；结论是：**当前固定依赖不足以直接证明三种桌面环境均满足项目约束**。暂不增加未验证的 QtMultimedia 冻结依赖，也不修改平台支持标志。

## 推荐实施路径

沿用一套观察与证据处理，分开实现 macOS、Linux X11、Linux Wayland 的取图后端。先交付能够原生验证的环境；其他环境保持明确的未支持状态。无需同时移植引擎输入桥。

- macOS 优先验证系统 ScreenCaptureKit 的 `SCContentFilter(desktopIndependentWindow:)` 单窗口流。遵循系统屏幕录制授权，处理拒绝、撤销与窗口关闭；不请求音频或麦克风。使用系统框架是否通过独立原生辅助程序接入，须在验证编译、部署版本和应用身份后确定，不能先承诺最低系统版本。
- Linux X11 优先验证 XComposite 重定向窗口的命名 pixmap，读取目标的离屏内容。调整窗口尺寸或重新映射会产生新 pixmap，必须重新绑定；窗口关闭、未重定向、服务端不支持和资源释放分别检查。先验证桌面已有组件，不自动安装库或改变合成器配置。
- Linux Wayland 使用 ScreenCast portal 的窗口源 `types=2` 和用户选窗授权，再消费授权返回的 PipeWire 流。按实际 portal 版本协商能力；版本 6 起优先使用 `pipewire-serial`／`PW_KEY_TARGET_OBJECT` 绑定流，避免节点 ID 回收后串源，旧版本兼容另验。先检查 `AvailableSourceTypes` 的窗口能力；如果系统提供返回的 `source_type`，必须验证为窗口。缺少窗口能力、用户取消或无法确认范围时明确失败，不降级整屏。选窗结果由系统授权流程决定，不声称能按 PID 静默指定任意窗口。

不选择“只加一层 QtMultimedia 调用就启用三平台”，因为以上源码缺口仍然存在。也不为绕过这些缺口引入用户桌面截屏、前台激活或输入模拟。

macOS 已按批准方案实施 Swift 随包辅助进程与 SCScreenshotManager 单窗口截图，验收范围仍受上文限制；Linux X11 的正式辅助进程设计见 [接入设计](X11-CAPTURE-DESIGN.md)，已获批准并完成源码接入，原生结果待验；Wayland 仍为实施建议。新增原生加载器或依赖范围，须按项目约定批准实际选择。

## 已实现的公共采集会话边界

`gameqakit/capture_session.py` 定义采集来源、带时区的采集时间、帧序号、可取消的读取和关闭接口。`watch.watch(..., capture_session=...)` 将实现该契约的会话接入现有去重、裁切、异步分析与归档。macOS 已接入窗口选择、截图、观察和人工标记，后台控制保持关闭；Linux X11 已接入独立验证分支，尚未通过本轮原生／冻结验收，不计为产品支持，见[双线验证记录](X11-GODOT-VALIDATION.md)。

- 会话的每张新帧提供递增序号，图像所有权交给观察层；暂时没有新缓冲区返回 `None`，结束或撤销授权等终止情形抛出 `CaptureEnded`。已有序号不会再次计入新采集；相同内容但新序号仍计入采集，继续沿用画面去重决定是否留图。序号本身由后端负责，公共层不能据此证明原生缓冲区确实更新。
- 同一会话固定来源；来源变化会失败退出。Windows 包装保留原有查窗与同步截图调用，绑定后拒绝另一个 HWND，已绑定目标查找失败也会结束。它仍会复核选择结果，未改变 Win32 截图调用本身的阻塞限制，也不能据此保证 HWND 回收重用时的原生身份验证。
- 时间线与疑点元数据增加 `capture_source`、`frame_sequence`，保留旧 `hwnd` 字段及报告兼容；非 Windows 来源不伪造 HWND。证据使用后端给出的原始采集时间，不以归档时间覆盖。
- 参数、锁和输出目录检查通过后，观察循环接管传入会话；此前被拒绝的会话仍由调用方负责。观察退出、初次状态写入失败、运行中写入失败和终止异常均执行采集关闭；关闭失败明确报错，随后仍排空分析线程。采集退出不关闭用户游戏。
- 裁切边界算法移至公共模块，Win32 保留原导入入口。这些公共层改动未新增依赖或输入通路；后续 macOS 原生组件与平台声明的变更单独记录在上述设计文档。

本地回归覆盖无新缓冲区、重复序号、静止新帧、来源变化、终止错误、无效帧、预先取消、停止文件、状态写入失败及关闭失败。另在模拟 `darwin`／`linux` 且禁止导入 Win32 模块的子进程中，执行裁切与证据归档、来源变化终止和关闭失败清理。这些仅证明公共契约，不能代替原生采集；测试和完整回归结果见根目录 [CHANGES.md](../CHANGES.md) 与本地验证目录。

## 现有观察链的复用与最小改动面

预检时 `gameqakit/watch.py` 直接依赖 Windows 风格的 `find_window`、`grab_image`、`window_text` 等函数，`sessions.start_watch()` 要求 Windows。当前已增加上述可注入会话，默认 Windows 路径仍通过原函数取图；持续视频流的原生后端尚未接入，不能仅把 `require_windows()` 删除。

后端持有原生窗口／流资源；观察层继续持有会话锁、停止请求、去重、异步分析和证据文件。后续原生实现仍须验证以下契约：

- 目标标识包含平台、采集后端和会话内来源标识；Windows HWND 继续兼容旧证据，其他平台的 token 不伪装成 HWND。用户选中的窗口关闭后，不按标题悄悄绑定另一个窗口。
- 记录图像尺寸、来源和真实采集时间。读取缓存的时间不能冒充新帧时间；图像内容相同也不等于源停止更新。静止帧、没有新缓冲区、流失联三者分开记录。
- 不假定跨平台具有 Win32 控件文本；不可用时传空文本，保留图像分析与人工标记。
- 区域裁切、缩放、去重和分析继续沿用现有处理；要核对像素坐标、显示缩放与窗口装饰边界，避免裁错内容。
- 停止、异常、权限撤销均关闭本会话资源；人工观察的退出不终止用户游戏。输入权、控制租约及游戏桥守卫继续由独立控制链持有。
- 只有原生采集、入口与冻结包均验收后，才改变 `platform_support.info()` 的能力声明；不能仅按操作系统名字判为可采集。

## 验收顺序和可核对证据

先用自写计数／移动图形窗口验证采集，再使用允许测试的原生游戏。每个系统、桌面环境和架构分别登记，构建通过与桌面功能通过分别记录。

| 操作 | 通过依据 |
| --- | --- |
| 启动并选择自写窗口 | 首张图包含目标计数与标记；来源为该窗口，非整个显示器 |
| 将其他应用覆盖目标，再让目标自行更新 | 连续取到至少两个不同计数；没有切回游戏或改变用户焦点；覆盖应用内容未进入图像 |
| 让目标画面保持静止，再恢复动画 | 静止期间状态如实表达帧／流状态；恢复后取得新计数；不以重复读取旧缓存伪造新帧 |
| 调整尺寸、跨显示器或缩放 | 图像与区域坐标一致；X11 重新绑定当前 pixmap；无旧尺寸残留 |
| 拒绝／取消授权、撤销授权或关闭目标 | 明确记录原因并结束或等待用户重新授权；不扩大采集范围、不自动换目标 |
| 人工标记、生成报告、正常停止 | 标记关联正确证据；停止后采集资源释放，原游戏继续运行 |
| 另启新会话、双会话及异常退出 | 来源不串窗、不混证据、无遗留采集流；新会话不继承失效来源 |
| 启动实际冻结成品 | 在目标桌面重复上述关键步骤，确认框架、库、权限身份和辅助程序可用 |

以上是通用验收清单。macOS AppKit 的源码与冻结产品链已覆盖完全遮挡、冻结反例、改尺寸、人工标记、归档及正常退出，两种形态各核对 33 张 PNG；真实组件故障各通过 11 项并分别回收 12 个辅助进程。Metal 保留失败，首次授权／撤权、普通用户桌面与真实游戏未验。Linux X11 既有独立探针通过，正式接入的本轮源码／冻结产品链仍未执行。无图形桌面的测试、Windows mock 和无桌面 SSH 均不能代替上述原生验收；macOS 14、物理 GPU、Wayland 与四架构覆盖仍分别待证据。

## 当前阻碍与恢复入口

本机仍为 Windows；可复用的原生验证环境已扩展到 macOS 15 arm64 云端与 Ubuntu 22.04 x64/Xvfb。它们已用于自写窗口探针及上文限定的 macOS 产品链，不代表普通用户桌面、真实 GPU 游戏或系统首次授权已验收。

继续条件分别为：Metal 已收齐 GPU、过滤路径及冻结后重新露出的原生对照，仍需能区分呈现／合成／采集的新观测或真实 GPU 桌面；已失败的开关和过滤路径不重复，当前虚拟 GPU 属性不作为根因结论。X11 设计已批准且本地实现与回归就绪，需执行独立云端原生验收；Wayland 需有支持窗口源授权的真实 portal/PipeWire 桌面，并批准相应实现方案。已验证公共契约不能代替这些结果，也不能通过放宽守卫、整屏回退或擅自开启输入绕过它们。

本地原始资料、Qt 源码摘录摘要、环境记录与续接文件保存在 `<跨平台采集验证目录>/`，不随源码入库。远端 v0.4.3 构建结果仍由用户查看，本批未轮询发布结果。

## 一手资料

- [Qt 6.10.0 FFmpeg 平台后端选择](https://code.qt.io/cgit/qtmultimedia.git/tree/src/plugins/multimedia/ffmpeg/qffmpegmediaintegration.cpp?h=v6.10.0)：区分屏幕与窗口后端。
- [Qt 6.10.0 macOS 窗口采集](https://code.qt.io/cgit/qtmultimedia.git/tree/src/plugins/multimedia/ffmpeg/darwin/qcgwindowcapture.mm?h=v6.10.0)：`CGWindowListCreateImage` 调用。
- [Qt 6.10.0 X11 采集](https://code.qt.io/cgit/qtmultimedia.git/tree/src/plugins/multimedia/ffmpeg/qx11surfacecapture.cpp?h=v6.10.0)：窗口 drawable 与 `XShmGetImage`。
- [Qt 6.10.0 PipeWire helper](https://code.qt.io/cgit/qtmultimedia.git/tree/src/multimedia/pipewire/qpipewire_screencapturehelper.cpp?h=v6.10.0)：portal 的 `SelectSources` 请求。
- [Apple：Capturing screen content in macOS](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)：单窗口过滤器、系统授权及帧状态示例。
- [ScreenCast portal 官方接口](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)：源类型、用户授权和 PipeWire 流。
- [X.Org：XComposite](https://www.x.org/releases/current/doc/man/man3/Xcomposite.3.xhtml)：离屏存储、命名 pixmap、重映射／尺寸变化与错误条件。
