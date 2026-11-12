# 后台控制实验与插件接入

这里记录开发接口与实证边界。人工盯屏照常使用原来的 GUI；尚未提供把任意游戏一键转为后台控制的入口。

## 范围与保护记录

- 当前 VX Ace 复核为后台非最小化、客户区 640×480；旧读数差异未确定原因，不归为用户最小化。
- 原游戏和存档先只读备份并核对 SHA-256。9 号档及关联 PNG 禁止改动，前 7 档仅用于已批准测试；本机完整路径与还原方法只记仓库外清单和游戏自己的账本。
- 以下实测结果对应已归档的实现与提交，后续按已验证范围增量推进；被测游戏的 Trellis 契约仅约束其数据和同步记录，不为工具开发另起游戏巡检任务。
- 人工盯屏继续独立工作，控制失败不回退系统键鼠。最新覆盖摘要见[引擎覆盖表](ENGINE-COVERAGE.md)。

## 当前支持范围

| 引擎 | 实现 | 已实测 | 尚未证明 |
|---|---|---|---|
| RPG Maker MV / MZ | `plugins/rpgmaker_js/GameQaBridge.js`，同一实现适配两个版本 | 独立 Win32 Desktop 中的 SDK 空白工程：新游戏、移动、开关菜单、动作后截图、暂停；前台采样句柄不变 | 带第三方插件的商业游戏、最小化、全流程与存档隔离 |
| RPG Maker VX Ace / RGSS3 | `plugins/rgss3/GameQaBridge.rb`，独立 Ruby 实现 | SDK + RGSS301：新游戏、转向、菜单、截图、暂停和租约过期释放；原生场景位移；当前定制游戏完整副本经 MCP 读入 7 号档并移动 (29,8)→(29,9)；一轮连续测试 33 条命令（含恢复）、15 次菜单往返全部成功，耗时中位数 984 ms、最大 2547 ms，最后暂停释放 | 其他定制游戏、全剧情及长时稳定性；此前一次未消费输入的安全拒绝原因未复现，后续成功不代表已修复 |
| Wolf RPG | `plugins/wolf/` Windows x86 原生 DLL／加载器，统一 v1／INI／MCP；实时控制走 `wolf-shm-v1` | 两个固定哈希构建；官方范例标题、对话、移动、菜单、安全生命周期与战斗闭环；商业构建新游戏、教室位移／菜单、第 1 槽保存及同进程／重启读取、30 轮短时位移／菜单重复；分批证据见 [Wolf 说明](../plugins/wolf/README.md) | 其他构建、官方存读档、商业战斗、全流程、长期稳定性、音频与设备；菜单动画需等待，松键后走完当前格才停。只读识别不证明控制可用 |
| Unity Mono / 旧输入 | `plugins/unity/`，BepInEx 5、Harmony 调用点和输入委托适配，使用同一 v1 / INI / MCP | Unity 2020 空白场景位移、菜单、UI、暂停与租约释放；《Taimashi》2020.3.7f1 完整副本标题→剧情→对话推进 | 商业玩法移动、全剧情、动态新增程序集和其他游戏 |
| Unity Mono / 新 Input System | 虚拟键盘／鼠标、标准状态事件、既有及新增外部设备隔离 | Unity 6000.4.3f1 空白场景的 InputAction 位移、菜单、UI、额外设备隔离 | 手柄、自定义设备和其他版本／游戏组合 |
| Unity IL2CPP / 新 Input System | BepInEx 6 IL2CPP 入口、Il2CppInterop，复用同一控制核心 | 《Novice Succubus Lillia》2022.3.62f2 完整副本的标题、难度选择、开始剧情和对话推进 | 角色移动、其他游戏、IL2CPP 旧输入／Rewired |
| Unity Mono / Rewired | 真实动作映射、正负按键与边沿、鼠标、标准 UI 输入覆盖 | Unity 5.6.7f1《Night of Revenge》完整副本：标题、难度、开场／地图对话、左右实际位移与操作后截图、暂停释放 | 未覆盖的 Rewired 接口、其他版本／组合及完整游戏流程；历史安全拒绝缺少瞬间快照，后续已修复可复现的命令读取竞态 |
| HSP3 / Windows x86 | `plugins/hsp/`，HPI3 与目标进程输入读取适配，协议 v1 / INI | 官方 HSP 3.7 空白工程的 getkey/stick 位移、菜单、画布及原生按钮点击、暂停／租约释放；《八星帝》相同外置字节码经官方运行时执行，LOAD→分页 01–10→11–20→两次关闭返回标题，最终 10 条命令成功并消费 | 原内嵌 EXE 自动加载、其他版本／扩展 DLL、玩法、进度读写、全流程与长期稳定性；商业“关闭”按钮未证明为原生控件 |
| DuckStation / PS1 | `plugins/duckstation/`，指定 Windows x64 构建、SDL 虚拟手柄、原生逐帧执行，协议 v1 / INI / MCP | 《妖精战士3》标题子菜单 GAME START→CONVERT→GAME START；实际执行帧、精确长按与操作后截图，暂停／租约释放、旧会话与暂停代次拒绝、重复命令及截图失败停止；《妖精战士3》开局屋内移动、记录册保存／读取、重启后读测试卡、方块菜单打开／选项切换／返回已逐图核对，见 `<PS1验证目录>/玩法验收索引.md`；桥梁首战的走格、命令、双方回合、伤害／升级及战后返回世界地图亦已实测，见 `<PS1验证目录>/battle-retest/战斗验证.md`；长转场仍可能超时，需暂停查询并显式恢复 | 其他模拟器／构建、实体手柄干扰（当前跳过，保留接口）、完整玩法、换盘、音频、自动启动就绪检测与长期稳定性 |
| SRPG Studio / x86 DirectInput | `plugins/native_input/` 固定 EXE 档案、受限原生宿主／DLL，统一 MCP／v2 显式毫秒 | 同一正式 DLL 在副本完成标题 → 环境设置 → 返回，正常 Z／X 和逐图观察；自有窗口安全回归与真实 MCP 入口通过 | 正式后端实体键鼠并发、游戏鼠标、存读档、玩法、其他构建及长期运行；暂停仅停止 AI 输入，见 [插件说明](../plugins/native_input/README.md) |
| XP / VX、Tyrano / KiriKiri 等 | 只读识别/样本盘点 | 输出引擎线索与后续验证条件 | 不声明输入插件可用；不能套用其他引擎实现 |

`gameqakit/engines.py` 只识别，`gameqakit/control.py` 统一校验与调度，各插件只面向主程序。`gameqakit/desktop.py` 负责实验性独立桌面和 Job Object 进程树回收。插件没有互相导入。

独立 Win32 Desktop 不是虚拟机或 Sandbox，也不隔离文件系统、注册表、网络和外部存档。它不切换用户的活动桌面；本轮使用 SDK 副本及已核对写入位置的当前 VX Ace 定制游戏副本，不代表任意游戏都兼容。

## 协议 v1

一个桥接目录绑定一个目标。`connection.json` 提供 `protocol=1`、随机 `session_id`、目标 `pid`、精确 `exe_path`、`engine` 和可选 `command_format=ini`。主程序会核对目标和插件会话；不能拿不同进程的目录凑用。

文件职责：

- `status.json`：插件心跳、帧号、暂停状态、`held`、实际能力、画面尺寸和只读场景/玩家信息。
- `request.json`，RGSS3、Unity、HSP 和 DuckStation 为 `request.ini`：唯一 `id`、会话、动作、参数、发出时的帧号和暂停代次。
- `results/<id>.json`、`screenshots/<id>.png`：命令结果与操作后证据。主程序检查帧号、图片路径边界、PNG 内容和历史状态。
- `pause.json` / `pause.ini`：独立于动作锁的优先暂停请求，包含当前会话标识；旧会话暂停不得影响新会话。
- `lease.json` / `lease.ini`：主程序有限租约；失联后插件暂停并释放输入。动作超时也请求暂停，不能静默改走前台。

动作共用 `move`、`confirm`、`cancel`、`wait`、`release` 等名称；以插件 `capabilities` 为准。MV/MZ、Unity 和 HSP 还提供 `press` 和画布坐标 `click`。RGSS3 当前只允许单帧输入，不承诺长按或点击；DuckStation 支持 1–120 个原生执行帧及 `press`，不支持坐标点击，普通动作释放后另推进两帧再截图。首图黑场时在原有 3 秒时限内保持松键、每 30 帧复查；仍黑则失败暂停，显式恢复不能追认原命令成功。可选 `square` 仅在桥的 `buttons` 数组声明后接受，其他按钮映射不变。收到不支持的参数明确拒绝。输入走引擎正常输入链，不直接完成任务、设置剧情标志或切换场景。

相同 `command_id` 用于查重和查询，不能绑定另一份参数。`historical=true` 表示旧会话、旧暂停代次等历史结果，不能指导当前动作。`control_pause` 返回的是请求状态，需再查 `paused=true` 且 `held=[]`；恢复须显式调用 `control_resume`。不要把恢复当作查询。

## 在副本里接入

只对明确批准的测试副本操作。先查游戏自己的存档与测试契约，列出所有可能写入的位置；复制游戏目录不能隔离 AppData、注册表、云存档或绝对路径。原档、测试档和空白工程必须分别记录。

MV/MZ：把桥脚本加入副本的 `js/plugins/`，在副本 `js/plugins.js` 的插件列表中启用；游戏进程环境设置 `GAMEQAKIT_BRIDGE_DIR` 和 `GAMEQAKIT_SESSION_ID`。当前脚本需要 NW.js 的文件访问能力。`connection.json` 指向本次实际启动的目标和会话。本轮使用 `DesktopProcess` 的独立桌面路线，不能把在默认桌面加载成功当成双向输入隔离实证。

VX Ace：将 Ruby 桥插在副本真正执行的 `rgss_main` 入口前；定制游戏入口未必叫 Main。环境同上，协议文件采用 INI。桥使用自己的更新计数，不在初始化时依赖游戏可能重写的 `Graphics.frame_count`。当前插件还会核对 `DesktopProcess` 设置的 `GAMEQAKIT_DESKTOP_NAME` 与线程实际桌面名，核对失败不声明输入能力。不能覆盖原 `Scripts.rvdata2`。`tests/rgss_smoke.py` 展示通过 Marshal 复制、追加桥接和运行 SDK 副本的完整过程，需要已有 `rubymarshal`；不自动安装。

Unity：见 [Unity 插件构建与接入](../plugins/unity/README.md)。按 Mono／IL2CPP、旧 Input／新 Input System／Rewired 的实际组合构建，主桥和对应后端 DLL 必须来自同次构建；不能互换运行时。核对独立桌面与 JSON/INI 绑定，等后端就绪后才恢复。商业副本保留原场景与代码，使用独立产品标识，并核对原文件、原持久化目录及 PlayerPrefs 前后状态；这不是通用存档沙箱。暂停须有 `connected=true`、`paused=true`、`held=[]` 的真实状态，读失败的保守暂停不算验收。

HSP：见 [HSP 插件构建与接入](../plugins/hsp/README.md)。只在副本中为外置 HSP3 字节码追加注册，使用匹配的官方运行时；原 EXE 内嵌脚本不会自动改走旁边的 `start.ax`。核对独立桌面、精确运行时路径及 INI 绑定。HPI3 回调、stick 的直接读取和鼠标消息缓存分别验证；桥更新计数不等于渲染帧率。先查保存位置，不带入原存档。本次商业验收到标题／读取菜单，不代表玩法或进度读写。

DuckStation：见 [PS1 插件构建与接入](../plugins/duckstation/README.md)。准备入口与 DLL 都核对已测 EXE 的完整 SHA-256，原生偏移不能跨构建使用。副本新建便携配置与记忆卡，不带原进度；通过正常 SDL 虚拟手柄绑定输入，用原生单步计时与数字手柄回读证明消费，SDL 回调数不是游戏帧数。只复用现有统一 MCP，音频关闭；固定开场等待仍需读图核对。

接入后先核对心跳与能力，再明确恢复，执行最小正常输入并查看操作后 PNG。不要只凭“ok”或内部坐标认为交互正确。暂停/失联释放验证通过后，才扩大路径测试。

原生输入：见 [受限原生桥](../plugins/native_input/README.md)。固定样本只在全新副本挂起加载，守卫就绪后恢复，不使用独立 Desktop；当前仅普通方向键、Z 确认／X 取消。v2 使用 1–1000ms 和 `observation_seq`，不冒充执行帧；`pause_scope=input`、`simulation_pause=false`，暂停不冻结游戏。只读识别命中不启动。实体并发入口自动冒烟不算人工验收；游戏实体并发仍未验。

## 可重复的实验命令

命令只适用于空白 SDK 工程；`--work` 必须是全新、项目外专用目录，不使用真实游戏或原存档目录。路径按本机填写。

```bash
python tests/engine_smoke.py --source <MV或MZ空白工程> --runtime <已有nw.exe> --work <全新测试目录>
python tests/rgss_smoke.py --full-project --sdk <VX-Ace空白工程> --library <已有RGSS301.dll> --work <全新测试目录>
python tests/rgss_smoke.py --sdk <VX-Ace空白工程> --library <已有RGSS301.dll> --work <另一个全新目录>
python tests/hsp_smoke.py --runtime <已有hsp3.exe> --compiler <已有hspcmp.dll> --common <HSP的common目录> --plugin <构建的GameQaHsp.dll> --work <全新HSP证据目录>
```

前三条为 RPG Maker 实验：前两条验证完整 SDK 工程，第三条使用原生最小场景验证位移与释放。HSP 命令编译空白工程；`--wait-loop` 验证 wait 等待路径，`--desktop-mismatch` 验证桌面不符时拒绝输入。均不等于商业游戏验证。结果写入 `result.json`，截图在桥接目录。成功后回收测试进程；保留结果、必要 PNG 与日志，游戏副本及临时运行目录在核对后回收。

自动回归：

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
node --test tests/rpgmaker_bridge.test.js
python -m compileall -q gameqakit tests
git diff --check
```

## 后续验证入口

当前 VX Ace 定制游戏已定位相对 `Save/` 的存档/全局数据和副本 `Data/` 缓存写入。正常读入 7 号档后验证移动、菜单和暂停，另完成 33 条命令、15 次菜单往返的连续测试。原目录 17 个存档相关文件及脚本等共 20 项哈希不变；9 号档及关联截图没有复制进副本，原游戏未注入。此前一次安全拒绝的细分诊断已补入插件，具体触发条件未复现，保留失败回执，不放宽租约或暂停校验。33 条命令不等于长时间稳定性保证。

Unity 已从空白场景继续到商业副本：旧输入、新 Input System、IL2CPP 和 Rewired 分开构建与验收，具体组合见上表。原进度不带入副本，独立产品标识隔离依赖标识的 PlayerPrefs／persistentDataPath；仍须排查硬编码路径和网络。先前两个 `helper` 不是外部控制桥，本项目自建插件；不再索取不存在的用户插件。失败批次、旧结论与最新验证索引分开保存，不能用菜单响应推断角色移动或所有组合兼容。

Wolf SDK 的普通窗口消息未观察到效果；正式原生桥已接入游戏原 DirectInput 键盘与完整循环边界，统一协议、安全生命周期、MCP 和地图操作均有独立正式批次证据。当前仅支持官方 Windows x86 范例与已登记商业构建的两个完整 EXE 哈希，准备入口拒绝未知哈希；具体哈希、日文启动入口及逐批验收见 [Wolf 插件说明](../plugins/wolf/README.md)。官方战斗、商业存读档及短时重复结果分别归档，不互相套用。准备入口不复制原保存；副本外文件／注册表写入守卫只按实测范围说明，不能等同完整沙箱或独立桌面的固有隔离。XP/VX 等仍待各自运行时验证，没有技术上无法适配的结论。HSP 首版已在上表范围实测；焦点修复的两份后续红日志实际停于截图失败，不能作为焦点缺陷稳定复现的证明，失败原件与有效通过证据分别保留。

本机全局 Skill 入口已在备份原正文后切换为仓库源的轻量引导，维护源分别为 `skills/localization-playtest/` 和 `skills/game-control-adapter/`，通过 Pi 解析器发现检查。异地使用者仍须自行注册自己的路径，不复制本机入口。两个 Skill 共六个行为场景已在新上下文评估，不能代替真实游戏回归；额外独立审查曾因连接错误失败，未冒称通过。

YOLO/OCR 的模型与依赖、真正虚拟机/Sandbox 没有自动安装。完整测试副本在归档并核对证据哈希后回收，保留截图、回执、失败日志及恢复备份；本机路径、归档映射和槽位账本已同步到被测游戏目录。
