# Unity 后台控制插件（实验）

Windows 下的 Unity 控制桥，沿用协议 v1、INI 命令和现有 MCP 的 `control_status/action/result/pause/resume`。必须绑定独立 Win32 Desktop；不发送桌面键鼠，不直接改角色坐标、剧情标志或调用按钮事件。

## 输入后端与边界

| 运行时／输入 | 实现 | 验证范围 |
|---|---|---|
| Mono／旧 Input | BepInEx 5、Harmony 改写托管调用点和已绑定的标准输入委托 | Unity 2020 空白场景的真实位移、菜单、UI、暂停和租约释放；《Taimashi》Unity 2020.3.7f1 完整副本的标题点击、进剧情和推进对话 |
| Mono／新 Input System | 独立虚拟键盘和鼠标、标准状态事件、外部设备隔离 | Unity 6000.4.3f1 空白场景的 InputAction 位移、菜单、标准 UI 和额外设备隔离；固定《OmaNeko-Seikatsu》完整副本经受管 MCP 正常点击标题进入房间、推进对白与 Qt 当前预览 |
| IL2CPP／新 Input System | BepInEx 6 IL2CPP 入口、Il2CppInterop 类型转换和回调，复用控制核心 | 《Novice Succubus Lillia》Unity 2022.3.62f2 完整副本的标题、难度选择、进入剧情和对话推进 |
| Mono／Rewired | 读取游戏实际键位映射，适配 Player 动作查询、正负方向与边沿；鼠标和标准 UI 输入覆盖 | 《Night of Revenge》Unity 5.6.7f1 完整副本：标题→难度→开场／地图对话→实际左右移动；位置与操作后截图均核对，暂停释放通过 |

IL2CPP 不是输入后端。当前 IL2CPP 构建接新 Input System；IL2CPP 旧输入／Rewired、其他中间件、原生 DLL 输入、动态新增程序集、自定义设备与手柄仍需分别适配。发现 Rewired 不代表全部 API 已覆盖；未覆盖的输入查询会拒绝能力发现。暂停不能因“设备值回到中性”触发额外游戏动作。

旧 Input 后端同时处理直接调用与预先保存的 Input 委托；鼠标／触摸配置和未启用传感器提供中性状态，不能把传感器读取算作消费动作。新 Input System 后端在输入更新之前投递状态，安装时隔离既有设备，接管期间隔离新增的外部设备，卸载时清理自己的设备并恢复原设备状态。

## 安全与回执

- 初始暂停。核对精确路径、PID、会话和实际线程桌面；普通 `Default` 桌面不启用输入。
- 动作持续 1–120 帧，之后释放按键，在 `WaitForEndOfFrame` 后截图。状态包含场景、根对象位置、可读 UI 文本和实际输入后端。
- 回执发布前先刷新状态；无输入读取、截图失败、过期租约、优先暂停均停止输入。旧会话的暂停不能影响新会话。
- 高频心跳在独占文件锁内完整写入，读取方在现有文件读取时限内处理短暂共享冲突／替换间隙；租约和心跳新鲜度阈值不变。进程中断导致内容不完整时查询失败并停手。
- 独立桌面不隔离存档、注册表或网络。商业副本须先核查 PlayerPrefs、persistentDataPath、相对保存目录和硬编码路径；不能仅复制目录就启动未知游戏。

## 构建

使用本机已有编译器和与目标游戏匹配的程序集，不自动下载依赖。Mono 版本按游戏自带 `mscorlib` 编译，避免 Unity 5.x CLR 2.0 被 .NET 4 委托类型破坏。不同运行时的构建不可混用。

```bash
# Mono／旧 Input：输出 GameQaBridge.dll
python plugins/unity/build.py --game-root "<测试副本>" --output "<交付目录>/GameQaBridge.dll"

# Mono／新 Input System：另输出 GameQaInputSystem.dll
python plugins/unity/build.py --game-root "<测试副本>" --input-system --output "<交付目录>/GameQaBridge.dll"

# Mono／Rewired：另输出 GameQaRewired.dll
python plugins/unity/build.py --game-root "<测试副本>" --rewired --output "<交付目录>/GameQaBridge.dll"

# IL2CPP／新 Input System：使用现成 dotnet SDK、目标 BepInEx/core、interop 和 dotnet 目录
python plugins/unity/build.py --game-root "<IL2CPP 测试副本>" --il2cpp --output "<交付目录>/GameQaBridge.dll"
```

将同次构建的主桥及后端 DLL 一起放入副本 `BepInEx/plugins/GameQaKit/`。加载器本身须与游戏位数、Unity 版本和入口签名相符。老 Unity 的分辨率启动对话框要在测试副本关闭；停在对话框不代表加载器失败。离线入口实验不属于一键安装能力。

## 受管入口

主程序“AI 后台接入”、CLI 与 MCP 的 `game_*` 工具已迁入已登记哈希的《Taimashi》Unity 2020.3.7f1 Mono／旧 Input 构建与《OmaNeko-Seikatsu》Unity 6000.4.3f1 Mono／Input System 1.19 构建，操作顺序见[游戏接入说明](../../docs/GAME-ADMISSION.md)。Rewired 固定构建已补登记和准备，暂停标题正常，但新游戏和读档入图均超过现行 2 秒心跳判据，完整迁入未通过；IL2CPP／新输入固定构建已补离线准备；一批正常进入剧情但未完成后续验收，另一批在 Loading 期间触发原 2 秒心跳保护，松键未确认，完整受管整链未通过。

已有组件目录须采用以下结构，桥用本项目当前源码和该游戏的程序集构建，加载器与 BepInEx 核心须使用原已核查材料，不自动下载：

```text
<组件目录>/
  GameQaBridge.dll
  GameQaInputSystem.dll  # 仅新 Input System 构建需要
  winhttp.dll
  BepInEx/core/*.dll
```

Mono Rewired 固定构建改用 32 位 Doorstop，并在同一层提供 `GameQaRewired.dll`。IL2CPP 固定构建使用另一个独立组件目录：同层放 `GameQaBridge.dll`、`GameQaInputSystem.dll` 与 64 位 `winhttp.dll`，并包含 `BepInEx/core/`、`BepInEx/interop/`、`BepInEx/unity-libs/` 和 `dotnet/`；全部离线文件摘要进入准备指纹，互操作自动更新关闭。不把 Mono 和 IL2CPP 的桥或运行库混用。

入口只读核对程序、全部托管程序集或已登记 IL2CPP 原生组件与已选依赖摘要；仅修改 QA 副本的产品标识及已核查的启动设置，按构建排除原 `SaveData` 和旧插件。Rewired 固定构建例外复制 `SaveData`／`OtherConfigData`，保留原进度及首次初始化标记，避免缺失标记时触发 EpicPrefs 初始化异常；本次复制范围会在操作摘要披露。旧 Input 构建解析 PlayerSettings 修改标识；固定 Unity 6／IL2CPP 构建仅在 PlayerSettings 原始字节内唯一匹配并等长替换已核查的公司／产品标识，重复或缺失标记即拒绝准备；固定 Unity 6 构建启动使用已验 D3D11 参数。随机公司标识的 LocalLow 目录通过私有目录联接指向本次会话的 `persistent/`；结束先确认松键再回收自有目标，核对原目录、原存档与 PlayerPrefs 并移除本次联接。运行副本产生缓存或其他变化时保留现场，不自动删除新进度。退出核对完整读取文件摘要清单，控制文件的 128 KiB 上限保持。

桥在输入初始暂停时也于帧绘制结束后导出首图及观察图，更新 `observation_seq`；此操作不恢复输入，不暂停整个 Unity 仿真。取图失败会使绑定失效，worker 记录具体原因并收尾；无法取得松键确认时记为未确认，目标已退出不能代替确认。

受管启动在原有 15 秒内等待新观察序号自然产生有效首图。纯黑／纯白候选图按原判据拒绝并留在会话 `startup-candidates/`；序号刷新不延长等待，序号未变不重试放行。损坏图片、桥取图错误、目标退出、取消、身份／保存路径异常、未暂停松键及后端就绪后断开仍立即失败。初始化占位心跳早于后端加载时仍受原 15 秒期限限制，身份与松键检查不等待首图。只在启动阶段等待，不自动恢复输入，运行中的观察故障处理不变。

受管真实 MCP 批次已逐图验证：两个固定构建分别取得初始暂停首图、正常点击标题进入剧情，Qt 观察预览随当前图更新且不占动作权，观察者关闭后目标仍存活，最终由创建者停止并通过原数据核对。新 Input System 构建的有效首图为 Live2D 启动标志，零输入持续观察后自然到达标题；普通点击进入 MainScene 房间后再次点击可推进对白。范围只覆盖这些固定构建的菜单／剧情入口，商业位移、存读档、其他构建及完整玩法仍须另验。

## 截图文件异步发布

像素读取和 PNG 编码仍在 Unity 主线程完成，生成的 PNG 字节交给单槽工作线程发布文件；同一时刻最多保留一份不超过 16 MiB 的 PNG，完成结果被主线程取走前不接收下一份。等待期间主线程继续检查暂停、租约并发布真实状态，不接收下一条动作，也不延长动作的保持帧数。

观察序号只在文件成功发布后推进；动作截图和预览图均写完后才确认动作成功。回执保留实际取图帧及对应场景状态，不能用文件完成时的后续帧冒充取图帧。暂停或租约失效后的迟到结果不能把原动作改为成功。文件发布按单调时钟限制为 2 秒：进行中的发布检查已等待时长，已完成的发布检查从提交到完成的实际时长，不能因主线程稍后读取结果而把按时完成误判为超期。真正超过时限、编码或写入失败均使观察失效并停止输入；原 2 秒控制心跳与 15 秒启动期限不变。

退出时关闭发布槽，不在 Unity 主线程等待可能阻塞的文件系统调用；已开始的系统写入可能留下现场，但不再产生成功回执，后续文件写入会检查关闭标志。受管目标退出仍由独立桌面 Job 回收进程树，不保留独立写盘进程。该实现不改变加载等待或失败回收策略；商业加载是否通过须以各批实测为准。

## 目标侧松键读取证据

Unity 状态的 `release_observed` 与 `release_frame` 记录游戏实际读取相关输入松开的证据。核心保留尚待读取的输入令牌，新一次按下使旧证明失效；软件清空保持键、`Down=false`、`Up=false`、动作回执或没有查询均不生成证明。当前旧 Input／Rewired 接入相关保持态读取、实际抬起事件和轴归零读取；新 Input System 尚未接入这项证明，不得据此宣称其加载观察门槛已满足。

固定 Rewired 的临时加载探针已在两次实际 `LoadScene` 入口读到此前真实帧的松键证明；这只满足加载前的必要条件。该批最终仍心跳失联且未确认退出前松键。生产加载观察／延后回收未实现，完整加载时长与产品等待上限未测得，2 秒控制心跳、2 秒 PNG 发布期限及失败回收不变。进度与边界见[加载生命周期设计](../../docs/superpowers/specs/unity-loading-lifecycle-design.md)。

## 阶段耗时诊断

维护者排查主线程停顿时，可在启动受管 worker 的同一进程环境中显式设置 `GAMEQAKIT_UNITY_DIAGNOSTICS=1`；默认关闭。该变量由独立桌面子进程继承，不需要改游戏或控制协议。

开启后，桥在 `bridge/diagnostics.json` 记录当前阶段、阶段持续时间、最近／最大完成耗时、真实游戏帧与观察序号，并保留回调之间的间隔。阶段覆盖命令读取、租约／暂停读取、输入后端、场景状态查询、像素读取、PNG 编码及文件发布。后台定时器每 250 毫秒尝试发布，只读取托管计时快照，不调用 Unity API，也不更新控制心跳。每次记录最多 8 KiB，临时文件同限；单实例最多发布 480 次或运行 120 秒，先到即停止，不自动重启计时。

受管 worker 首次报告失联时，在原回收之前尝试写入会话 `disconnect.json`，限量保全读到的原始状态、连接、请求、租约、暂停及诊断文件，并记录宿主单调时钟、墙钟和可解析的心跳年龄。单个原文件最多保全 8 KiB；损坏内容原样标记，诊断失败不替换原错误、不跳过清理。诊断可能包含不同会话或过期文件，只供人工取证，不能证明输入就绪、松键或可恢复控制。

本项沿用原 2 秒心跳、15 秒启动期限和失败回收。它没有实施加载等待状态，也没有解除 Rewired／IL2CPP 受管入图阻碍。使用后取消环境变量即可关闭；回退本次源码提交不改游戏或存档。诊断回归入口为 `python -m unittest tests.test_unity_diagnostics tests.test_unity_control tests.test_control -v`。

## 接入与验证

1. 用 `gameqakit.desktop.DesktopProcess` 挂起创建副本；设置 `GAMEQAKIT_BRIDGE_DIR`、`GAMEQAKIT_SESSION_ID` 和 `GAMEQAKIT_INPUT_BACKEND`（`legacy`、`input_system`、`rewired`）。DesktopProcess 绑定实际独立桌面名称。
2. 恢复前写入同内容的 `connection.json`／`connection.ini`，包括 `protocol=1`、随机会话、实际 PID、精确路径、`engine=unity_mono` 或 `unity_il2cpp`、`command_format=ini`。
3. 等待 `connected=true`、`input_ready=true`，核对后端及 `capabilities`，再显式恢复。用同编号查询超时动作，不发送桌面输入。
4. 暂停验收必须同时满足 `connected=true`、`paused=true`、`held=[]`；状态读失败返回的保守暂停不算游戏确认。场景名未变化时仍须检查截图，菜单可能在同一场景切换。
5. 商业验收记录准确版本、输入组合、保存边界、来源前后哈希、原持久化目录和原 PlayerPrefs；先证明可见响应，再扩大范围。开场或对话可能锁定角色控制，先通过正常输入推进，确认操作开放后再测位移；不用只延长等待或直接修改场景绕过流程。

```bash
python plugins/unity/build.py --test-core --output "<项目外构建目录>/ControlCoreTests.exe"
python -m unittest discover -s tests -p test_unity_control.py -v
python tests/unity_smoke.py --source "<已核查 Mono 运行库>" --work "<不存在的新目录>"
python tests/unity_smoke.py --source "<新输入 Mono 运行库>" --input-system --loader "<现有 BepInEx 5>" --work "<不存在的新目录>"
python tests/unity_commercial_smoke.py --prepared "<已核查商业副本及 prepared.json>" --steps "<动作 JSON>" --work "<不存在的证据目录>"
```

空白场景探针使用独立产品标识，去掉商业脚本与自动初始化对象。商业探针保留实际场景与游戏代码，从新进度开始，不带入原存档。修改产品标识只能隔离依赖这些标识的 Unity 保存路径，硬编码路径与网络必须另审；当前没有通用商业存档沙箱。

本地逐批日志、失败记录、截图和动作回执位于维护者选择的 `<Unity验证目录>/`（不随源码分发）。最终交付、验证索引及清理记录见 `delivery-extension/`。旧首版证据仍保存在 `evidence/probe-05/`；后续通过不覆盖失败批次。Rewired 复验曾有一次 `stale_command` 安全拒绝；后续已复现并修复命令与租约跨文件读取竞态，回归与新构建见 `stale-command-fix/`。原失败缺少瞬间快照，不能唯一回溯；历史商业验收 DLL 保留，新修正仅旧输入空白场景重新实测，其余构建完成编译验证。角色移动、菜单、剧情和特殊操作分别声明，不以一个游戏或一个输入组合推断全部兼容。

## 实现依据

- [Harmony：原生与 extern 方法的边界](https://harmony.pardeike.net/articles/patching-edgecases.html)
- [Unity：BaseInputModule.inputOverride](https://docs.unity3d.com/ScriptReference/EventSystems.BaseInputModule-inputOverride.html)
- [Unity Input System：设备](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.11/manual/Devices.html)
- [Unity Input System：后台焦点策略](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.11/manual/Settings.html)
- [BepInEx：IL2CPP 插件](https://docs.bepinex.dev/master/articles/dev_guide/plugin_tutorial/2_plugin_start.html)
- [Rewired：Player 动作查询](https://guavaman.com/projects/rewired/docs/api-reference/html/T_Rewired_Player.htm)
