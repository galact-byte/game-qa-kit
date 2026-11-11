# 轻量后台控制方案调研

## 结论

`CreateDesktopW` 不是产品的必选技术。ROADMAP 的目标是让 AI 在后台操作游戏，同时用户仍可正常使用键鼠；需分别验证后台运行、新画面、输入不互扰、暂停释放与数据写入边界。现有生产桥检查独立桌面身份，替换后端需要适配能力声明与安全校验，不能仅删除检查。

建议保留已验通路，下一项只核查 **Proto Input／Nucleus 维护分支的进程内输入拦截能否接收软件动作**，再决定是否接入。它比任务视图桌面管理更接近游戏需求，但公开接口主要分流物理设备，尚不能证明 AI 可直接发送独立键鼠状态。没有任何候选方案在本项目中通过新的游戏兼容验收。

本轮只调研和运行自有窗口探针，没有安装依赖或驱动，没有注入游戏、修改系统功能或切换用户的输入桌面。VMware 不再作为默认下一步；Sandbox 受当前 Windows 家庭版限制。

## 方案比较

| 方案 | 不依赖 `CreateDesktopW` 的可能性 | 一手资料确认的能力 | 对本项目的限制与判断 |
|---|---|---|---|
| pyvda 的 `VirtualDesktop` | 是 | 管理 Windows 任务视图桌面、移动窗口、切换桌面 | 没有提供第二套键鼠；单独使用不足以完成目标 [1] |
| pywinauto／UI Automation／窗口消息 | 是 | `click()` 发 WM 消息；`send_keystrokes()` 面向非活动窗口；UIA 操作目标暴露的控件模式 | 适合标准控件及愿意处理消息的游戏；UIA 依赖提供者，文档明确后台按键可能只部分有效。不能改用会影响实体输入的 `click_input()`／`type_keys()` 冒充后台通过 [2][3] |
| Proto Input | 是，可在目标进程内处理焦点和输入接口 | 可选 RawInput、键盘状态、光标、XInput、DirectInput 等 hook；C 风格 API 可供其他语言调用 | 是最值得继续核查的游戏输入候选，但需要 DLL 注入与按游戏配置；公开头文件未见通用软件键鼠状态写入入口；不提供本项目证据闭环 [4][5] |
| Nucleus Co-op | 是 | 集成 Proto Input 和按游戏配置的 handler，支持后台多实例输入 | 属于可参考的成熟集成；完整工具会管理窗口、游戏文件与实例，不能直接作为本项目的透明后端；不照搬锁定用户输入的模式 [6] |
| Python `vgamepad` | 是 | 创建 Xbox 360／DS4 虚拟手柄，Python 更新状态 | 仅适合接受手柄的游戏；不能保证后台继续消费或独占路由，不能补足任意键鼠操作；Windows 依赖 ViGEmBus 驱动，默认安装流程会运行驱动安装器，需另行批准 [7] |
| Sandboxie Plus | 可不启用其独立桌面功能 | 文件／注册表沙箱；可选 Isolated Desktop 在用户会话中创建新 desktop object | 数据隔离与输入控制是两项能力；其 Isolated Desktop 仍属于同类独立桌面，不能据名称宣称有第二套后台硬件输入。本轮未试装 [8] |
| Agent Control Fabric | 可选 | 封装 PostMessage、UIA、PrintWindow，也提供 CreateDesktop | 没有消除底层消息和截图的兼容限制；新项目，不能用 README 的焦点安全承诺替代游戏实测，不建议为了换封装迁移现有实现 [9] |
| OBS Game Capture | 是 | 捕获 DirectX／OpenGL 游戏，可选择指定窗口 | 只解决画面一侧；官方说明切走全屏游戏可能停止渲染。不能单靠它完成输入隔离 [10] |
| 独立机器／受支持的远程会话 | 是 | 把运行与输入放入另一环境 | 独立机器是备选；普通远控当前桌面不等于获得额外输入会话。本机 Windows Home 不支持作为微软远程桌面主机 [11] |
| Windows Sandbox | 是，以隔离来宾为后端 | 临时虚拟化环境，支持虚拟 GPU；关闭会删除内部软件、文件与状态 | Windows Home 不受支持，本机未发现可用入口；不是当前可以直接启用的兜底，数据导出和后台控制仍需单独验收 [12] |

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

Proto Input 上游较久未推送，因此下一步先检查 Nucleus 当前集成的来源和改动，不能只拿旧上游评价现有实现。MIT 的 Proto Input 与 GPL-3.0 的 Nucleus 需分清来源，接入前核对实际复用文件许可。

## 下一项的进入条件与验收

先做只读源码核查，确认软件动作能否进入 Proto Input 的每实例输入状态，或是否仍需另加虚拟设备／改造。没有可用入口时就报告缺口，不以物理多键鼠分配替代 AI 控制。

若接口可行，再提出本地小样本方案：说明注入对象、依赖、写入范围和还原方式；下载安装、驱动及新的架构实现按项目批准边界执行。最小实验必须同时证明实际按键／点击效果、持续新画面、用户键鼠不进入测试目标且 AI 动作不进入用户窗口、断线／暂停释放、进程回收。任一项失败，不能提高兼容性声明。

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
