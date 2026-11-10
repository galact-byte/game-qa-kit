# Wolf RPG 正式实验桥

仅支持已测官方中文范例的 Windows x86 `Game.exe`，SHA-256：

```text
90800e5dff9fa992a1fa78be5b2d5158baf2e9cc9603a45d964358e919f6868a
```

沿用协议 v1／INI／现有 MCP，不依赖 Frida。只读识别返回 `adapter=wolf`、`control_verified=false`，识别结果本身不能证明已连接或兼容。其他 Wolf 构建与商业游戏需要重新核查，哈希不符直接拒绝。

## 构建与准备

使用已安装的 Windows SDK／MSVC x86 工具链，不自动安装或下载依赖。准备入口拒绝已有工作区、目录交叠及未知 EXE，不复制原 `Save`／`SaveData`。

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

`tests/test_wolf_control.py` 编译执行独立 C++ 安全核心，并验证准备边界。安全核心覆盖精确轮次、重复输入轮询、暂停／租约、未消费输入、停帧／回退／跳帧、截图失败和小栈写入守卫。

其他构建、商业游戏、存读档、战斗、全流程、音频、鼠标、实体手柄、最小化及长期稳定性均未验。没有宣称完整数据沙箱、固定显示帧率或任意 Wolf 游戏一键控制。
