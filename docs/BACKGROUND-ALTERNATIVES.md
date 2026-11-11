# 轻量后台控制方案调研

## 结论

`CreateDesktopW` 不是产品的必选技术。ROADMAP 的目标是让 AI 在后台操作游戏，同时用户仍可正常使用键鼠；需分别验证后台运行、新画面、输入不互扰、暂停释放与数据写入边界。现有生产桥检查独立桌面身份，替换后端需要适配能力声明与安全校验，不能仅删除检查。

已完成 **Proto Input／Nucleus 维护分支的软件输入入口核查**：内部确有每进程键鼠状态与软件生成 RawInput 的路径，但核对的公开 DLL API／管道协议没有提交任意键鼠动作的接口。维护分支新增的手柄转键鼠仍以控制器状态为来源，不能据此承诺 Python 直接控制。源码证据与固定提交见下节。

建议保留已验通路，暂不把 Proto Input 接入主线。继续这条路线需要扩展原生输入接口、补齐暂停／断线释放和截图证据，再通过自有窗口与游戏样本验收；这已超出直接包装一个 Python 库的范围。没有任何候选方案在本项目中通过新的游戏兼容验收。

本轮只调研和运行自有窗口探针，没有安装依赖或驱动，没有注入游戏、修改系统功能或切换用户的输入桌面。VMware 不再作为默认下一步；Sandbox 受当前 Windows 家庭版限制。

## 方案比较

| 方案 | 不依赖 `CreateDesktopW` 的可能性 | 一手资料确认的能力 | 对本项目的限制与判断 |
|---|---|---|---|
| pyvda 的 `VirtualDesktop` | 是 | 管理 Windows 任务视图桌面、移动窗口、切换桌面 | 没有提供第二套键鼠；单独使用不足以完成目标 [1] |
| pywinauto／UI Automation／窗口消息 | 是 | `click()` 发 WM 消息；`send_keystrokes()` 面向非活动窗口；UIA 操作目标暴露的控件模式 | 适合标准控件及愿意处理消息的游戏；UIA 依赖提供者，文档明确后台按键可能只部分有效。不能改用会影响实体输入的 `click_input()`／`type_keys()` 冒充后台通过 [2][3] |
| Proto Input | 是，可在目标进程内处理焦点和输入接口 | 可选 RawInput、键盘状态、光标、XInput、DirectInput 等 hook；C 风格 API 可供其他语言调用 | 已核查 Nucleus 登记版本与维护分支：内部有独立状态，但公开 API／管道没有任意软件键鼠动作入口；接入需原生扩展和生命周期补齐，暂不接入主线 [4][5][14]–[21] |
| Nucleus Co-op | 是 | 集成 Proto Input 和按游戏配置的 handler，支持后台多实例输入 | 属于可参考的成熟集成；完整工具会管理窗口、游戏文件与实例，不能直接作为本项目的透明后端；不照搬锁定用户输入的模式 [6] |
| Python `vgamepad` | 是 | 创建 Xbox 360／DS4 虚拟手柄，Python 更新状态 | 仅适合接受手柄的游戏；不能保证后台继续消费或独占路由，不能补足任意键鼠操作；Windows 依赖 ViGEmBus 驱动，默认安装流程会运行驱动安装器，需另行批准 [7] |
| Sandboxie Plus | 可不启用其独立桌面功能 | 文件／注册表沙箱；可选 Isolated Desktop 在用户会话中创建新 desktop object | 数据隔离与输入控制是两项能力；其 Isolated Desktop 仍属于同类独立桌面，不能据名称宣称有第二套后台硬件输入。本轮未试装 [8] |
| Agent Control Fabric | 可选 | 封装 PostMessage、UIA、PrintWindow，也提供 CreateDesktop | 没有消除底层消息和截图的兼容限制；新项目，不能用 README 的焦点安全承诺替代游戏实测，不建议为了换封装迁移现有实现 [9] |
| OBS Game Capture | 是 | 捕获 DirectX／OpenGL 游戏，可选择指定窗口 | 只解决画面一侧；官方说明切走全屏游戏可能停止渲染。不能单靠它完成输入隔离 [10] |
| 独立机器／受支持的远程会话 | 是 | 把运行与输入放入另一环境 | 独立机器是备选；普通远控当前桌面不等于获得额外输入会话。本机 Windows Home 不支持作为微软远程桌面主机 [11] |
| Windows Sandbox | 是，以隔离来宾为后端 | 临时虚拟化环境，支持虚拟 GPU；关闭会删除内部软件、文件与状态 | Windows Home 不受支持，本机未发现可用入口；不是当前可以直接启用的兜底，数据导出和后台控制仍需单独验收 [12] |

## Proto Input 源码核查结果

### 来源与版本

核查固定到 Git 提交，未用仓库活跃度替代代码证据：

| 来源 | 固定提交 | 已核对范围 |
|---|---|---|
| `SplitScreen-Me/splitscreenme-nucleus` | `3d22c0e8a6dd624f08a5cce286cd594c8abf264d` | `.gitmodules`、C# 包装层、配置与启动器，共 4 文件 |
| Nucleus 登记的 Proto Input 子模块 | `dd29bf8d22349605d0e1950c1c5dc4893d72670e` | Loader／Hooks 等 91 文件；公开头文件 34 个导出声明，管道 25 类命令 |
| `SplitScreen-Me/splitscreenme-protoInput` 维护分支快照 | `e18e06bb345b86540e7464f7355c649bfdd17f38` | Loader／Hooks 等 115 文件；公开头文件 53 个导出声明，管道 44 类命令 |

Nucleus 的子模块地址指向其维护分支，但登记提交与该分支快照不同 [14]。不能把 C# 包装层、子模块源码和某个发布包中的 DLL 默认当作同一版本；本轮没有下载发布包或核对其二进制导出。206 份 Proto Input 文件与 4 份 Nucleus 文件均按固定 Git 树的 blob 摘要复核，并另留 SHA-256 清单。这是源码完整性审计，不是运行兼容性测试。

### 输入到底从哪里来

| 问题 | 源码证据 | 对本项目的含义 |
|---|---|---|
| 是否确有独立输入状态？ | `FakeMouseKeyboard.cpp` 的 `ReceivedKeyPressOrRelease` 更新 DLL 内的按键位；`GetAsyncKeyStateHook.cpp` 从该状态返回结果 [17] | 内核有可复用的进程内状态基础，但函数属于内部实现，没有在公开加载器 API 中暴露 |
| 默认状态来源是什么？ | `RawInput.cpp` 先按所选设备句柄筛选，再处理键鼠事件并向目标窗口转发 `WM_INPUT` [18] | 主要解决物理多设备分流；分配设备不等于 AI 能写入任意动作 |
| 最新维护分支有没有软件生成路径？ | `TranslateXtoMKB.cpp` 读取 `OpenXInputGetState`，再更新内部键鼠状态；`RawInput::InjectFakeRawInput` 将内部生成的数据送到已登记窗口 [18][19] | 证明进程内合成可行的实现基础存在；当前入口仍依赖控制器状态，未变成 Python 的任意键鼠接口 |
| Python 能否只包一层就提交动作？ | 完整核对上述两版 `protoloader.h` 与 `pipeinclude.h`；接口提供 hook、设备、焦点、映射和冻结配置，没有任意按键／位置／按钮状态提交命令 [15][16] | `ctypes` 可以调用已有导出，不能自动调用未导出的内部 C++ 函数。需要改造协议和原生实现，或额外提供虚拟设备来源 |

`SetXinputtoMKBkeys` 配置的是手柄到键位的映射，`SetControllerIndex` 选择控制器；两者都不是提交一次软件输入。不能通过全局 `SendInput` 填补缺口后宣称键鼠隔离成立。

### 截图与生命周期仍缺什么

- **冻结不等于释放。** `SetExternalFreezeFakeInput` 的管道分支只修改 `externalFreezeInput`；RawInput 用该标志停止事件转发，`GetAsyncKeyState` hook 仍读取原按键状态。所查路径没有随冻结清空按下位。因此按下后冻结不能当作已通过“全部释放”；这是源码推导，未做游戏卡键实测 [17][18][20]。
- **断线没有本项目所需的闭环。** 管道读取失败后退出读取循环、关闭句柄；`ProtoPerformCleanup` 只调用焦点消息循环清理。上述路径没有本项目的动作租约、过期拒绝、暂停代次及断线清键语义，不能直接作为现有安全生命周期的替代 [20]。
- **存在内部截图代码，尚无可复用的证据接口。** 维护分支 `ScanThread.cpp` 通过 `GetDC(hwnd)` 与 `BitBlt`／`StretchBlt` 抓取窗口，用于图像匹配。公开加载器与管道没有对应的截图结果接口；这也未证明被遮挡、最小化或特定 GPU 游戏能持续给出新帧 [21]。
- **桌面与数据边界要另验。** 进程内 hook 与定向消息提供不创建独立 Win32 Desktop 的候选路线，但不自动保证双向输入隔离，不隔离文件、注册表或存档。当前生产桥的独立桌面身份检查仍需按新后端适配。

### 推进建议与进入条件

本轮只读核查已经完成，结论为：**有可复用的原生实现基础，没有现成满足本项目要求的软件输入后端；暂不接入主线。** 当前明确阻碍是缺少软件动作入口与所需生命周期，不是尚未找全 Python 包名。没有用未实测的工期或性能数字承诺改造成本。

如果后续决定承担原生扩展，再先评审一个独立小样本方案：固定维护分支版本，仅复用输入 hook；用软件动作更新目标进程状态并产生对应消息；补暂停／断线释放；复用现有截图通路并逐帧核验。只在专用研究目录和自有窗口上验证，先通过真实按键、鼠标点击、新画面、双向输入不串扰与进程回收，再考虑游戏样本和现有桥接入。新依赖／驱动安装、注入对象、写入与还原范围及架构选择需先明确，不能由这次只读核查推定已获实现批准。

本轮未复制 Nucleus GPL 代码进入产品；所查 Proto Input 根许可证为 MIT，未来实际复用时仍需保留声明并逐项核对所带依赖。来源快照、清单与审计脚本留在 `<隔离预检目录>/proto-input-review/`，不随源码入库。

## 本机最小验证

原始脚本、截图和日志保存在 `<隔离预检目录>/`，不随源码提交。

- `desktop-unit-tests.log`：以 `ResourceWarning` 为错误运行 `test_desktop.py`，6 项通过，覆盖进程树回收、正常退出、桌面与当前输入桌面分离等既有生命周期行为。此结果不等于任意游戏输入通过。
- `desktop-probe-01/`：复用现有 `DesktopProcess` 启动自有 Tk 窗口，未调用 `SwitchDesktop` 或 `SendInput`。窗口报告实际桌面名与新建桌面一致、不同于宿主输入桌面。
- 基线与键盘消息之后均通过现有截图函数取得 240×140 图像，中心像素从 `(204, 34, 34)` 变为 `(34, 68, 204)`；自有窗口确实消费一次 A 键事件。事件内 `GetAsyncKeyState(A)` 的按下位为假。微软文档也说明非活动桌面可能返回零，不能据这个读数推断消息能改变游戏轮询输入 [13]。
- 随后鼠标点击没有被 Tk 绑定消费，探针超时，未生成完整成功结果。失败原因未确定；不计鼠标输入通过，也不计这批完成双向隔离验收。失败日志和局部截图保留。
- 因探针未走到最后的前台对照与完整退出结果写入，不能引用不存在的 `result.json` 宣称全程焦点不变或探针收口成功。既有 6 项回归单独计数。

## 项目状态与维护信号

GitHub API 本轮只读快照，数值随时间变化；`pushed_at` 仅表示仓库有推送，不能代表 hook 本身被维护或兼容性已验证。

| 仓库 | Stars / Forks | `pushed_at`（UTC） |
|---|---|---|
| Ilyaki/ProtoInput | 82 / 20 | 2022-02-13T04:21:33Z |
| SplitScreen-Me/splitscreenme-nucleus | 2152 / 98 | 2026-11-10T12:51:25Z |
| mirober/pyvda | 105 / 16 | 2026-07-14T19:28:03Z |
| yannbouteiller/vgamepad | 266 / 34 | 2026-10-27T05:48:40Z |
| wszx11/agent-control-fabric | 0 / 0 | 2026-11-02T12:43:39Z |

Proto Input 上游较久未推送；现已另外核查 Nucleus 所登记子模块及其维护分支，源码结论见前节。MIT 的 Proto Input 与 GPL-3.0 的 Nucleus 需分清来源，接入前核对实际复用文件许可。

## 后续实验的验收

只读源码核查已确认缺口：内部合成输入有实现基础，但公开接口不能直接接收任意 AI 键鼠动作。是否增加原生扩展属于下一项方案选择，不继续把本轮状态写成“尚待核查”。

若后续确定改造，再提出本地小样本方案：说明注入对象、依赖、写入范围和还原方式；下载安装、驱动及新的架构实现按项目批准边界执行。最小实验必须同时证明实际按键／点击效果、持续新画面、用户键鼠不进入测试目标且 AI 动作不进入用户窗口、断线／暂停释放、进程回收。任一项失败，不能提高兼容性声明。

## 来源

1. [pyvda 官方 README](https://github.com/mirober/pyvda)
2. [pywinauto HwndWrapper 官方文档](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.hwndwrapper.html)
3. [Microsoft：UI Automation Control Patterns](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview)
4. [Proto Input 官方 README](https://github.com/Ilyaki/ProtoInput)
5. [Proto Input 公开加载器头文件](https://github.com/Ilyaki/ProtoInput/blob/master/src/ProtoInput/ProtoInputLoader/include/protoloader.h)
6. [Nucleus Co-op 官方仓库](https://github.com/SplitScreen-Me/splitscreenme-nucleus)
7. [vgamepad 官方仓库与安装说明](https://github.com/yannbouteiller/vgamepad)
8. [Sandboxie Plus：Isolated Desktop](https://sandboxie-plus.com/sandboxie-desktop/)
9. [Agent Control Fabric 官方仓库](https://github.com/wszx11/agent-control-fabric)
10. [OBS：Game Capture Source](https://obsproject.com/kb/game-capture-source)
11. [Microsoft：Remote Desktop 支持版本](https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remotepc/remote-desktop-allow-access)
12. [Microsoft：Windows Sandbox 及版本限制](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
13. [Microsoft：GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate)
14. [Nucleus 固定版本的子模块配置](https://github.com/SplitScreen-Me/splitscreenme-nucleus/blob/3d22c0e8a6dd624f08a5cce286cd594c8abf264d/.gitmodules)
15. [登记版本：公开加载器 API](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/dd29bf8d22349605d0e1950c1c5dc4893d72670e/src/ProtoInput/ProtoInputLoader/include/protoloader.h)、[管道协议](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/dd29bf8d22349605d0e1950c1c5dc4893d72670e/src/ProtoInput/ProtoInputHooks/pipeinclude/pipeinclude.h)
16. [维护分支快照：公开加载器 API](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputLoader/include/protoloader.h)、[管道协议](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/pipeinclude/pipeinclude.h)
17. [维护分支：按键状态写入](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/FakeMouseKeyboard.cpp#L199)、[按键状态查询 hook](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/GetAsyncKeyStateHook.cpp#L9)
18. [维护分支：RawInput 筛选与内部注入](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/RawInput.cpp#L503)
19. [维护分支：控制器转键鼠](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/TranslateXtoMKB.cpp#L424)
20. [维护分支：管道读取与冻结](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/PipeCommunication.cpp#L357)、[退出清理](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/Cleanup.cpp)
21. [维护分支：窗口图像采集](https://github.com/SplitScreen-Me/splitscreenme-protoInput/blob/e18e06bb345b86540e7464f7355c649bfdd17f38/src/ProtoInput/ProtoInputHooks/ScanThread.cpp#L451)
