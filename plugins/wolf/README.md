# Wolf RPG 正式实验桥

已通过正式验收的版本为官方中文范例 Windows x86 `Game.exe`，SHA-256：

```text
90800e5dff9fa992a1fa78be5b2d5158baf2e9cc9603a45d964358e919f6868a
```

沿用协议 v1／INI／现有 MCP，不依赖 Frida。只读识别返回 `adapter=wolf`、`control_verified=false`，识别结果本身不能证明已连接或兼容。未知哈希直接拒绝。

另登记商业构建 SHA-256 `2b811e3db991f45113087f979e3f375da7e46769a01f6e554584ed7c100b2f27` 的独立循环／键盘设备地址，使用显式日文启动。**该商业样本已在同一公开入口批次通过启动、标题输入、真实 MCP 新游戏／难度切换及安全生命周期验收**；CP932 生效，启动 9.672 秒，原 15 秒门槛不变。后续修复高 DPI 截图裁切及实时控制文件阻塞后，`commercial-gameplay-13` 已逐图验证姓名提交、开场推进、教室内实际位移与游戏内菜单往返，并通过安全生命周期与真实 MCP 验收。结论仅覆盖这个构建的已测范围；战斗、存读档和长期稳定性仍未验。设计和实际边界见 [日文启动兼容设计](../../docs/superpowers/specs/2026-11-10-wolf-japanese-startup-design.md)。

## 构建与准备

使用已安装的 Windows SDK／MSVC x86 工具链，不自动安装或下载依赖。准备入口拒绝已有工作区、目录交叠及未知 EXE；不复制原 `Save`／`SaveData`／`Save_MTool`、`Tool` 和 `MTool_*`。

```powershell
python plugins/wolf/build.py --output E:/DevData/wolf-build/GameQaWolf.dll
python plugins/wolf/build.py --injector --output E:/DevData/wolf-build/GameQaWolfInjector.exe
python plugins/wolf/prepare.py "<已核查范例目录>" E:/DevData/wolf-copy-01
```

DLL 与加载器分别构建。准备结果和测试数据记录位于新工作区；准备副本不等于启动、连接或输入验收。运行全部实验时，直接使用下一节的烟测脚本创建另一个全新工作区。

加载器核对会话、PID、共同宿主、程序路径及 Job 归属，只用于宿主新建的独立桌面挂起进程。它先完成 Windows 模块初始化，在游戏入口仍挂起时加载 DLL；宿主等待插件就绪后才恢复游戏。DLL 再核对 EXE 哈希与两个原 CALL 目标；失败时由宿主回收整个自有进程树。不能向正在使用的原游戏附加。

## 真实验收

在已核查范例的隔离副本中运行；源码目录只读。`--workspace` 必须尚不存在。

```powershell
python tests/wolf_smoke.py --source "<已核查范例目录>" --workspace E:/DevData/wolf-native-01 --dll E:/DevData/wolf-build/GameQaWolf.dll --injector E:/DevData/wolf-build/GameQaWolfInjector.exe --safety --mcp
```

默认步骤为标题等待、下移、上移。`--steps <JSON文件>` 可覆盖协议直连步骤；`--mcp-steps <JSON文件>` 将步骤经真实 MCP 文件路径入口执行，并核对每条动作回执和截图哈希。步骤是数组，例如：

```json
[
  {"name":"title-ready","action":"wait","frames":60},
  {"name":"title-down","action":"move","direction":"down","frames":12},
  {"name":"title-up","action":"move","direction":"up","frames":12},
  {"name":"start","action":"confirm","frames":1}
]
```

传 `--steps` 指向空数组可只做初始恢复与 MCP 步骤。步骤中的等待不是就绪判定，须查看实际画面；不同阶段需要正常推进对话后再移动。`result.json`、`status-trace.json`、`bridge/results/`、`bridge/screenshots/`、来源前后哈希与运行副本哈希均保留在工作区。结束后核对证据与来源哈希，再回收运行副本。

## 显式日文启动（商业构建实验）

复用已经安装且哈希匹配的 Locale Emulator 两份 DLL，不安装软件、不更改全局设置、不提权。另构建日文宿主：

```powershell
python plugins/wolf/build.py --locale-host --output E:/DevData/wolf-build/WolfLocaleHost.exe
python tests/wolf_smoke.py --source "<已核查商业目录>" --workspace E:/DevData/wolf-locale-01 --dll E:/DevData/wolf-build/GameQaWolf.dll --injector E:/DevData/wolf-build/GameQaWolfInjector.exe --locale-host E:/DevData/wolf-build/WolfLocaleHost.exe --locale-dir "<已安装Locale Emulator目录>"
```

`launch.py` 将宿主和已核实 DLL 放入新工作区的 `launcher/`，以独立桌面／Job 启动。宿主加载区域模拟前启用写入守卫；目标主入口挂起，引导期由调试拦截拒绝文件和注册表修改，之后在进程内守卫与控制桥就绪后才恢复。仅放行已实证的系统控制台／密码设备连接，不放宽文件写入范围。目标须自行报告 CP932；握手持续校验会话、宿主、PID 和路径，失败回收自有进程树。

`launch.ini` 的短时文件占用会在既有 15 秒握手期限内重试；初始化输入入口仍使用原 15 秒门槛。旧诊断曾量到 16.406 秒，原超时记录保留。后续栈采样与单变量对照确认，调试创建附带的额外堆检查显著增加初始化开销；宿主在创建目标前仅设置本进程环境 `_NO_DEBUG_HEAP=1`，不改系统环境。对照启动耗时从 11.50 秒降至 4.39 秒，证据见 `<Wolf验证目录>/startup-diagnosis/诊断与修复.md`。

后续内存计时与确定性回归确认：状态文件发布原先持有游戏输入共用锁，慢文件替换会阻塞游戏输入、人工暂停和租约检查。现在锁内只生成状态快照，锁外原子发布；发布失败仍暂停并释放按键。原首帧 1 秒、租约和其他期限不变。修复后 `public-fixed-02` 同批逐图验证标题与难度选择，并完成 15 次真实 MCP 调用及安全分支；原失败不追认，也不能唯一归因所有历史偶发故障。证据见 `<Wolf验证目录>/frame-diagnosis/诊断与修复.md`。

截图临时采用目标窗口的 DPI 感知上下文，完成后恢复调用线程设置。固定使用 DPI 不感知上下文曾将商业样本 120 DPI 下的 1024×576 裁为 819×460，现已修复；自有真实窗口回归覆盖两种 DPI 感知模式、右下角标记和上下文恢复。商业姓名界面底部「決定」已逐图确认完整可见。

后续 `commercial-gameplay-04` 正常输入验证姓名提交与开场对白；`05/06` 再次租约过期，`08` 记录到原始文件调用秒级等待。系统取证确认部分百毫秒级控制文件回调位于火绒 `sysdiag.sys`，不能完全归因历史数秒停顿。未修改安全软件设置或刷盘策略。

当前 Wolf 使用显式协商的 `control_transport=wolf-shm-v1`：请求、租约、暂停、实时状态及最新回执走 Windows 命名共享内存；连接描述、命令历史、截图及回执证据仍落盘。共享区绑定 PID／随机会话，仅桥创建，访问限当前用户与 SYSTEM，拒绝同名占用及未知传输，不静默回退。每槽独立零等待互斥与提交序号，半写、持锁或发布者退出不能阻塞游戏线程；原期限仍负责停止。控制循环与证据写盘分离；客户端有限动作调用期间由独立线程续租，截图读取阻塞不再拖延续租。成功回执仍须先完成证据发布。

升级时须同时更新客户端与 Wolf DLL；已有其他引擎继续默认文件传输，统一动作及安全语义不变。回滚须成对恢复客户端和 DLL、重建隔离会话，不能复用旧共享区。

修复后 `commercial-gameplay-13` 在 10.610 秒就绪，147 条正常动作及 147 次恢复全部成功，逐图验证教室内位移、菜单打开和返回；同批 10 次真实 MCP 调用及暂停、租约到期释放、旧会话／旧代次拒绝、去重、截图失败停止和显式恢复通过。早期并行回归负载下的 `native_frame_stalled` 批次保留，不追认为通过。最终 134 项 Python／16 项 JavaScript 全绿且无跳过；852 个原目录文件零差异，运行副本和三个记录进程已回收，307 张截图可读、304 处已有摘要一致。证据见 `<Wolf验证目录>/session-review/修复与验收.md`、`final-audit.json`。

## 动作与安全边界

- `move`、`confirm`、`cancel`、`press`、`release`、`wait`；按钮限 `up/down/left/right/ok/cancel`，输入轮次为 1–120。不声明点击、Shift、鼠标或实体手柄能力。
- 每帧经游戏原 DirectInput 键盘通路读取合成键状态。完整游戏循环才计一帧；同轮多次输入轮询只作消费证据。普通动作到请求轮次释放，再执行两轮观察；回执总增量因此包含这两轮。
- 初始暂停、输入为空。新动作接收和截图均在完整帧边界；运行中暂停可能发生在当前轮尚未结束时，须等 `paused=true`、`held=[]`、`at_frame_boundary=true` 后比较帧与轮询数。
- 会话、暂停代次、去重、租约和截图时限沿用现有协议。失败停止并释放合成键，查询原命令编号后显式恢复，不重发未知结果，不抢前台或使用系统键鼠输入。
- 截图使用独立桌面中的客户区兼容位图，经有效性检查后原子发布；文件和结果只发布完整内容。暂停心跳线程不推进游戏循环。
- 副本内文件写入允许，副本外原生文件写入与注册表写入拦截并记入 `write-guard.json`。这是本范例的写入防护，不是完整文件系统或系统沙箱；独立桌面本身不隔离文件、注册表或网络。
- 地图移动按格子执行。松键后角色会走完当前格，须等位置稳定再判断停止；菜单打开／关闭有动画，一轮短按不一定在该阶段产生界面变化。

## 已测范围

正式桥已逐图验证标题上下切项、确认入图、正常推进开场对话、地图左右位移、松键后停止、菜单打开／切项／完全关闭。最终地图批次 `mcp-gameplay-02` 经真实 MCP 完成 37 次调用，其中 30 条正常输入步骤，操作后截图逐项验哈希；过程前台采样未出现目标副本。

`native-15`、`gameplay-02` 另外验证初始暂停、完整轮次计数、长按中优先暂停、250 毫秒租约到期释放、旧会话／旧暂停代次拒绝、重复查询不重执行、截图保存失败停止及显式恢复。具体日志与图像索引保存在 `<Wolf验证目录>/正式桥验收.md`。早期加载失败、栈溢出、截图超时和验收脚本失败原件分别保留，不混算为通过。

常规回归使用：

```powershell
python -W error::ResourceWarning -m unittest discover -s tests -v
node --test tests/rpgmaker_bridge.test.js
```

`tests/test_wolf_control.py` 编译执行独立 C++ 安全核心，并验证准备边界。安全核心覆盖精确轮次、重复输入轮询、暂停／租约、未消费输入、停帧／回退／跳帧、截图失败和小栈写入守卫。`BridgeThreadTests.cpp` 编译真实桥循环与输入处理代码，在状态发布被人为阻塞时验证输入、暂停和租约检查仍可执行，并验证发布失败安全停止。`CaptureTests.cpp` 编译生产截图函数，验证完整客户区、右下角像素和线程 DPI 上下文恢复。

`tests/test_wolf_ipc.py` 使用真实 x86 发布者验证 Python／C++ 交换、半写与超大消息、持锁／发布者退出、会话隔离、未知通道拒绝，以及截图读取阻塞 1.8 秒时独立续租。桥线程回归还覆盖控制文件读取、回执写盘阻塞与原 3 秒截图超时。

其他构建、存读档、战斗、全流程、音频、鼠标、实体手柄、最小化及长期稳定性均未验。商业样本的启动、教室位移、菜单往返、MCP 与安全分支实证不扩大为完整游戏支持。没有宣称完整数据沙箱、固定显示帧率或任意 Wolf 游戏一键控制。
