# KiriKiri 固定构建实验控制桥

本桥只验 KiriKiri 2.32.2.426／x86 的固定运行时、自写 Window 与自写 KAG 工程，包含原生启动／持续守卫和 KAG 监督器的同批验证。运行时必须匹配 [SOURCES.md](SOURCES.md) 中的 SHA-256；不分发 EXE、商业资源或原存档。通用 GUI／`game_prepare` 入口尚未接入，候选识别仍为 `adapter=null`、`control_verified=false`。

## 商业样本前置审查

[商业 KAG 写入边界审查](../../docs/KIRIKIRI-COMMERCIAL-BOUNDARY.md)记录了正常保存、自由文件选择、配置迁移及原生插件边界；插件 CRT 写入引用仍不能证明完整不可达。[已批准实现的商业接入设计](../../docs/superpowers/specs/2026-11-17-kirikiri-commercial-design.md)对应的专用准备器、主线程恢复前守卫、原 `AfterInit.tjs` 后的加载点与 KAG 接线已实现，并通过自写联合验收。商业首批已执行：守卫就绪，但宿主将目录作为项目传入，未加载 `data.xp3` 中的 `startup.tjs`，在首图前失败，零输入并已回收。项目选择现已修正为有 `data.xp3` 时显式选择归档，否则选择目录；自写归档夹具验证通过，商业修正版尚未重跑。这不增加商业适配覆盖。

`prepare.py` 限定三份原始输入的摘要与输出清单，拒绝已有输出、重解析点及篡改；`GuardHost.cpp` 负责挂起启动、初始化期守卫、目标与守卫身份、持续完整性与 Job 回收；`guarded.py` 将该目标接到 KAG 监督器。默认守卫对越界请求返回访问拒绝并终止批次，商业宿主拒绝实验守卫。

自写实验单独启用精确共享节打开／创建与 CNG 设备调用的非致命拒绝：仍返回访问拒绝、不创建对象、不返回句柄，只不因此终止该自写批次。相邻对象／会话／掩码／控制码及文件后备映射保持致命拒绝。DirectInput 的临时非致命分支已撤销；自写目标改用 `-joypad=no -wheel=message -dbstyle=gdi -usedb=yes`。这些设置和三条精确非致命拒绝已获单独批准，迁入独立的 `KiriKiriCommercialHost.exe`／`KiriKiriCommercialGuard.dll` 构建；回执以 `denial_policy=2` 标识商业兼容策略，并记录 `runtime_sha256`，三条请求仍返回拒绝，其余危险请求仍终止批次。默认严格策略保持不变，严格／自写／商业兼容三种守卫不可混配。商业单批授权已消耗，失败后不得自行重启。

## 输入与组件

`GameQaBridge.tjs` 由夹具显式加载并绑定唯一 Window。它保留原查询和原事件处理器，通过原生 `postInputEvent` 发送 Enter；仅合成 Enter 期间包装单参数 `System.getKeyState(13)`，其他键委托原查询。原处理器返回并实际查询到按下／松开后才记录消费；处理器抛错、卡住或后续被替换均不能报告成功。解绑仅还原仍由桥持有的方法，保留游戏后续替换。

`host.py` 负责固定摘要、新鲜会话、自写夹具、禁止额外 XP3／插件／配置及重解析点检查。共用监督循环位于 `gameqakit/duration_host.py`；KiriKiri 使用独立的 `plugins/kirikiri/capture_worker.py`，在目标所属隔离桌面直接读窗口 GDI 画面。固定运行时会延后重画，`PrintWindow` 可能返回整张灰底，因此不再用于本插件。共用取图入口仅提取不带重画的窗口读取分支，Emuera 等既有默认 `PrintWindow` 路径保持原样，控制协议没有变化。

`AfterInit2.tjs` 保留原初始化逻辑，再加载 `KagBridge.tjs`。KAG 回执不依赖夹具 `window.value`／`handlerQuery`；只记录原处理器真实返回和处理器内确实发生的键查询，未查询时不伪造查询证据。监督器按固定窗口类和观察身份拒绝额外游戏窗口，不将 Delphi／输入法辅助窗口算作第二个游戏窗口；不支持同时控制多个游戏窗口。

只声明 v2 `confirm`／`wait`、`ok` 按钮与 1–1000 毫秒整数 `duration_ms`。初始暂停，首图有效后须显式恢复。`pause_scope=input`、`simulation_pause=false`；Timer 序号不是游戏帧。确认成功须同时具备原处理器按下、松开及操作后的有效新图，回执带 PNG 的 SHA-256。重复编号读取既有回执，不重新输入。

## 内部数据与期限

宿主到目标采用固定七行 ASCII 数据：`GQK1`、`seq`、`session`、`pid`、`id`、`kind`、`end`，序号首尾一致；字段位置、长度、身份、正整数、动作和递增序号均校验。目标使用 `Array.load` 读取文本，不求值控制文件。外部仍为现有 JSON 协议，不能把内部记录当作公开控制接口。

目标观察使用该运行时原生 `saveStruct` 的 UTF-16 结构数据，含 `begin_seq`／`end_seq` 完整标记。宿主最多读取 64 KiB，仅解析字符串、整数、字典和数组的受限子集；拒绝表达式、重复字段、过深嵌套和多条记录，不执行 TJS。原生发布不是原子替换，短读仅在原 100 毫秒 I/O 上限内重试；仍不完整时本轮不处理新命令，活性期限照常计时。标准 JSON 仍由宿主原子发布。

目标侧 `Array.load` 没有分块长度参数，行数与字段长度在加载后检查。控制文件遇到短暂共享读取冲突时，本轮不接收命令，下一轮重试；已读出的格式／身份／序号错误仍立即拒绝。持续读取失败仍由原宿主租约／活性／释放期限兜底，没有新增宽限期。不能称为目标文件大小的读取前限制，也不能将其当作恶意任意脚本的沙箱。

保护值保持：15 秒启动、1.5 秒租约、1 秒 UI 活性、1 秒释放确认、3 秒取图、1 秒正常关闭。首次释放期限不因重复暂停或读错刷新。暂停、租约失效和错误先停止新动作并请求松键；目标确认松键才算释放，未确认则回收 Job。截图失败保留已有消费事实，先发布暂停状态再发布失败回执。宿主退出或被终止时，其 Job 回收目标及取图子进程。

首图保持 99.9% 黑白帧判据；失败时零输入。自写夹具关闭内凹客户区装饰，使纯色测试覆盖完整画布，未裁图或放宽判据。故障开关仅由自写夹具读取严格文本枚举，宿主没有跳过验证的选项。

## 本地验证与实验入口

需要 Windows、仓库已有 Python／Pillow 环境和合法持有、摘要匹配的运行时。测试通过显式环境变量提供 EXE，不扫描下载目录。

```powershell
$env:TMP = '<E 盘验证目录>/tmp'
$env:TEMP = $env:TMP
$env:GAMEQAKIT_TEST_KIRIKIRI = '<合法持有的固定运行时 EXE>'
$env:GAMEQAKIT_TEST_EVIDENCE = '<全新证据目录>'
python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_kirikiri_bridge.KiriKiriBridgeTests tests.test_kirikiri_data tests.test_kirikiri_guard tests.test_kirikiri_startup tests.test_kirikiri_prepare tests.test_kirikiri_kag.KagBridgeTests tests.test_kirikiri_guarded.GuardedKagTests tests.test_kirikiri_guarded.CommercialCompatKagTests -v
```

普通桥测试只复制固定 EXE、`tests/kirikiri_fixture/startup.tjs` 与本桥 TJS；KAG 联合测试改用自写 KAG 夹具，并在 E 盘构建守卫／宿主。所有测试不含商业 XP3、原存档和原插件；归档用例在本地将自写启动脚本打包为 XP3，并移除散装脚本，验证加载、原处理器按下／松开、画布变化及去重。没有运行时的真实测试明确跳过。原生构建还要求本机已有的 x86 MSVC 工具链，不自动安装。手工构造普通自写布局后可运行：

```powershell
python plugins/kirikiri/host.py --runtime '<E 盘自写工程>/runtime.exe' --directory '<全新空会话目录>' --session '<新编号>'
```

不复制原 `.cf`／`.cfu`、XP3、MTool、插件或原保存数据。宿主明确指定 `-datapath`，仅为子进程定向 APPDATA／LOCALAPPDATA／TMP／TEMP；实际 `System.dataPath` 在自写工程 `savedata/`，环境配置和日志也在该目录。独立桌面只隔离桌面输入，不能隔离任意文件、注册表和网络。

正常 Enter 处理器在自写第 1 槽保存 `123`，后续改为 `999`，同进程及新会话正常读取恢复 `123`；桥不调用保存、读取或直接改变进度。`storage-ledger.json` 记录原状态不存在、文件、摘要及恢复方法；存档只含游戏值，不含桥会话或按键状态，不使用老师的保留槽。

## 实测边界与回退

真实测试覆盖：原输入／查询一致与画面变化、暂停期间新观察、去重／迟到／错误身份／非法记录拒绝、首图黑白与杂点、动作后黑图、截图写入失败与工作进程卡死、人工暂停、租约、持续读错、原处理器抛错、UI 卡死、未确认释放叠加重复暂停、长按退出、宿主被终止、解绑保留后续替换及两种正常读档。原文件摘要、前台 HWND 和测试进程退出另由本地交付核对保留。

已验证固定运行时与明确可观测的自写 Window／受守卫 KAG，包括加载身份、额外游戏窗口拒绝、控制文件读取冲突及有效画布的持续取图。商业 KAG、多个游戏窗口的控制、其他版本、通用受管入口、长期运行与真人并发未验；原生查询本身仍不随投递事件改变，兼容来自此处的有限覆层。独立桌面与本守卫不是任意程序的完整系统沙箱。

恢复时先终止本批宿主／Job，保留回执、图片和账本，再重建全新自写副本；不覆盖旧会话。撤回实验桥时删除新增插件、夹具与测试，并整体还原本次共用监督器提取及 Emuera 入口调整。没有原数据迁移。源码不含运行时与反编译产物，来源及许可缺口见 [SOURCES.md](SOURCES.md)。
