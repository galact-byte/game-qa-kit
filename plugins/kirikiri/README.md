# KiriKiri 固定构建实验控制桥

本桥只验 KiriKiri 2.32.2.426／x86 的固定运行时与本仓库自写 Window 工程。运行时必须匹配 [SOURCES.md](SOURCES.md) 中的 SHA-256；不分发 EXE、商业资源或原存档。没有 GUI、`game_prepare` 或商业启动入口，候选识别仍为 `adapter=null`、`control_verified=false`。

## 商业样本前置审查

[商业 KAG 写入边界审查](../../docs/KIRIKIRI-COMMERCIAL-BOUNDARY.md)记录了正常保存、自由文件选择、配置迁移及原生插件未知项，并列出待批准的副本与还原方案；本轮没有启动商业样本。当前宿主仍只接受自写夹具，不能直接把 `data.xp3` 加入目录运行。

## 输入与组件

`GameQaBridge.tjs` 由夹具显式加载并绑定唯一 Window。它保留原查询和原事件处理器，通过原生 `postInputEvent` 发送 Enter；仅合成 Enter 期间包装单参数 `System.getKeyState(13)`，其他键委托原查询。原处理器返回并实际查询到按下／松开后才记录消费；处理器抛错、卡住或后续被替换均不能报告成功。解绑仅还原仍由桥持有的方法，保留游戏后续替换。

`host.py` 负责固定摘要、新鲜会话、自写夹具、禁止额外 XP3／插件／配置及重解析点检查。共用监督循环位于 `gameqakit/duration_host.py`，共用取图工作进程位于 `gameqakit/capture_worker.py`；插件不依赖 Emuera 插件。Emuera 保留原命令行入口，使用同一监督器，控制协议没有变化。

只声明 v2 `confirm`／`wait`、`ok` 按钮与 1–1000 毫秒整数 `duration_ms`。初始暂停，首图有效后须显式恢复。`pause_scope=input`、`simulation_pause=false`；Timer 序号不是游戏帧。确认成功须同时具备原处理器按下、松开及操作后的有效新图，回执带 PNG 的 SHA-256。重复编号读取既有回执，不重新输入。

## 内部数据与期限

宿主到目标采用固定七行 ASCII 数据：`GQK1`、`seq`、`session`、`pid`、`id`、`kind`、`end`，序号首尾一致；字段位置、长度、身份、正整数、动作和递增序号均校验。目标使用 `Array.load` 读取文本，不求值控制文件。外部仍为现有 JSON 协议，不能把内部记录当作公开控制接口。

目标观察使用该运行时原生 `saveStruct` 的 UTF-16 结构数据，含 `begin_seq`／`end_seq` 完整标记。宿主最多读取 64 KiB，仅解析字符串、整数、字典和数组的受限子集；拒绝表达式、重复字段、过深嵌套和多条记录，不执行 TJS。原生发布不是原子替换，短读仅在原 100 毫秒 I/O 上限内重试；仍不完整时本轮不处理新命令，活性期限照常计时。标准 JSON 仍由宿主原子发布。

目标侧 `Array.load` 没有分块长度参数，行数与字段长度在加载后检查；进程内的文件阻塞由外部宿主期限兜底，不能称为目标文件大小的读取前限制，也不能将其当作恶意任意脚本的沙箱。

保护值保持：15 秒启动、1.5 秒租约、1 秒 UI 活性、1 秒释放确认、3 秒取图、1 秒正常关闭。首次释放期限不因重复暂停或读错刷新。暂停、租约失效和错误先停止新动作并请求松键；目标确认松键才算释放，未确认则回收 Job。截图失败保留已有消费事实，先发布暂停状态再发布失败回执。宿主退出或被终止时，其 Job 回收目标及取图子进程。

首图保持 99.9% 黑白帧判据；失败时零输入。自写夹具关闭内凹客户区装饰，使纯色测试覆盖完整画布，未裁图或放宽判据。故障开关仅由自写夹具读取严格文本枚举，宿主没有跳过验证的选项。

## 本地验证与实验入口

需要 Windows、仓库已有 Python／Pillow 环境和合法持有、摘要匹配的运行时。测试通过显式环境变量提供 EXE，不扫描下载目录。

```powershell
$env:TMP = '<E 盘验证目录>/tmp'
$env:TEMP = $env:TMP
$env:GAMEQAKIT_TEST_KIRIKIRI = '<合法持有的固定运行时 EXE>'
$env:GAMEQAKIT_TEST_EVIDENCE = '<全新证据目录>'
python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_kirikiri_bridge tests.test_kirikiri_data -v
```

测试自行构造 E 盘运行副本，只复制固定 EXE、`tests/kirikiri_fixture/startup.tjs` 与本桥 TJS；没有运行时的真实测试明确跳过。手工构造相同布局后可运行：

```powershell
python plugins/kirikiri/host.py --runtime '<E 盘自写工程>/runtime.exe' --directory '<全新空会话目录>' --session '<新编号>'
```

不复制原 `.cf`／`.cfu`、XP3、MTool、插件或原保存数据。宿主明确指定 `-datapath`，仅为子进程定向 APPDATA／LOCALAPPDATA／TMP／TEMP；实际 `System.dataPath` 在自写工程 `savedata/`，环境配置和日志也在该目录。独立桌面只隔离桌面输入，不能隔离任意文件、注册表和网络。

正常 Enter 处理器在自写第 1 槽保存 `123`，后续改为 `999`，同进程及新会话正常读取恢复 `123`；桥不调用保存、读取或直接改变进度。`storage-ledger.json` 记录原状态不存在、文件、摘要及恢复方法；存档只含游戏值，不含桥会话或按键状态，不使用老师的保留槽。

## 实测边界与回退

真实测试覆盖：原输入／查询一致与画面变化、暂停期间新观察、去重／迟到／错误身份／非法记录拒绝、首图黑白与杂点、动作后黑图、截图写入失败与工作进程卡死、人工暂停、租约、持续读错、原处理器抛错、UI 卡死、未确认释放叠加重复暂停、长按退出、宿主被终止、解绑保留后续替换及两种正常读档。原文件摘要、前台 HWND 和测试进程退出另由本地交付核对保留。

仅证明固定运行时与明确可观测的自写 Window。商业 KAG、多窗口、其他版本、受管入口、长期运行与真人并发未验；原生查询本身仍不随投递事件改变，兼容来自此处的有限覆层。

恢复时先终止本批宿主／Job，保留回执、图片和账本，再重建全新自写副本；不覆盖旧会话。撤回实验桥时删除新增插件、夹具与测试，并整体还原本次共用监督器提取及 Emuera 入口调整。没有原数据迁移。源码不含运行时与反编译产物，来源及许可缺口见 [SOURCES.md](SOURCES.md)。
