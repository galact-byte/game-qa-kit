# Unity 后台控制插件（实验）

Windows 下的 Unity 控制桥，沿用协议 v1、INI 命令和现有 MCP 的 `control_status/action/result/pause/resume`。必须绑定独立 Win32 Desktop；不发送桌面键鼠，不直接改角色坐标、剧情标志或调用按钮事件。

## 输入后端与边界

| 运行时／输入 | 实现 | 验证范围 |
|---|---|---|
| Mono／旧 Input | BepInEx 5、Harmony 改写托管调用点和已绑定的标准输入委托 | Unity 2020 空白场景的真实位移、菜单、UI、暂停和租约释放；《Taimashi》Unity 2020.3.7f1 完整副本的标题点击、进剧情和推进对话 |
| Mono／新 Input System | 独立虚拟键盘和鼠标、标准状态事件、外部设备隔离 | Unity 6000.4.3f1 空白场景的 InputAction 位移、菜单、标准 UI 和额外设备隔离 |
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

主程序“AI 后台接入”、CLI 与 MCP 的 `game_*` 工具已迁入已登记哈希的《Taimashi》Unity 2020.3.7f1 Mono／旧 Input 构建，操作顺序见[游戏接入说明](../../docs/GAME-ADMISSION.md)。其他 Unity 输入后端尚未迁入这一启动入口，继续使用各自已验接入流程。

已有组件目录须采用以下结构，桥用本项目当前源码和该游戏的程序集构建，加载器与 BepInEx 核心须使用原已核查材料，不自动下载：

```text
<组件目录>/
  GameQaBridge.dll
  winhttp.dll
  BepInEx/core/*.dll
```

入口只读核对程序、全部托管程序集与已选组件摘要；仅修改 QA 副本的产品标识、窗口与后台设置，排除原 `SaveData` 和旧插件。随机公司标识的 LocalLow 目录通过私有目录联接指向本次会话的 `persistent/`；结束先确认松键再回收自有目标，核对原目录、原存档与 PlayerPrefs 并移除本次联接。运行副本产生缓存或其他变化时保留现场，不自动删除新进度。

桥在输入初始暂停时也于帧绘制结束后导出首图及观察图，更新 `observation_seq`；此操作不恢复输入，不暂停整个 Unity 仿真。取图失败会使绑定失效，worker 记录具体原因并收尾；无法取得松键确认时记为未确认，目标已退出不能代替确认。

受管真实 MCP 批次已逐图验证：初始暂停首图、正常标题“开始”点击、进入剧情，Qt 观察预览随当前图更新且不占动作权，观察者关闭后目标仍存活，最终由创建者停止并通过原数据核对。该批只覆盖此固定构建的菜单／剧情入口，不能扩展为其他 Unity 构建或完整玩法验收。

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
