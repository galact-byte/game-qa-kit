# KiriKiri 固定样本核查与自写工程探针

候选识别保持只读；后续已用固定 EXE 副本与纯自写 TJS 完成路径、事件探针，并按批准设计实现有限状态覆层与独立宿主实验桥。原生查询本身仍不随合成事件改变；桥使原处理器内的合成 Enter 查询与按下／松开一致，且验证真实画面变化。商业脚本、原存档与原运行目录未启动或修改，通用控制标记不启用。

## 识别范围

`inspect_game` 同时要求目录中的 `.xp3` 文件具有 19 字节完整头（11 字节 XP3 签名与 8 字节索引偏移字段），以及某个 `.exe` 文件前 64 KiB 中同时具有 MZ 头和 `@TVP(kirikiri) for win32` 标记；扩展名大小写不敏感。

返回 `kirikiri_candidate`、`adapter=null`、`control_verified=false`。只读取文件，不解析或解包 XP3 索引，不加载运行时，不把头字段或版本字符串当成有效归档、完整 PE 或控制认证。没有外置 XP3 或采用其他标记布局的构建不在此次判定范围内。

## 固定样本所见

- 本机盘点中的一个候选带 `data.xp3`，EXE 版本资源为 KiriKiri 2.32.2.426／x86，运行时 SHA-256 为 `8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11`。
- 同目录带 MTool、转区组件与既有 `savedata`；不能把整包当作干净引擎，也不能将同版本号视为未修改上游。原 EXE、商业资源和存档不随源码分发。
- 只读提取的 KAG 初始化脚本标注 `3.32 stable rev. 2`。`MainWindow.tjs` 接收原键盘事件，并使用 `System.getKeyState` 再查询状态。后续必须验证输入确实经过原处理器且状态查询一致，不能仅凭投递消息认定消费。
- `saveDataLocation` 初始取 `System.dataPath`，没有冒号时另拼 `System.exePath`；日志也定向到该保存目录。正常存档、缩略图及系统／已读状态均使用这个保存位置。

## 保存路径证据与探针

REA 对固定 EXE 的静态反编译显示：

- `0x60FA38` 查询 UTF-16 `-datapath`（`0x6F6EA6`），将取值交给 `0x67AA34` 展开，再发布为引擎数据路径；初始化还读取程序对应的 `.cf` 和保存位置的 `.cfu`。
- `0x67AA34` 对空值使用 `$(exepath)\savedata`，支持 `$(exepath)`、`$(personalpath)`、`$(appdatapath)` 与 `$(vistapath)` 展开。仅看到相对的 `savedata` 或复制了游戏目录，不能证明所有写入已隔离。
- `0x67AEF8` 复用同一展开函数生成 `.cfu` 路径。
- EXE 文件偏移 `0x396E00` 的 `XOPT_EMBED_AREA_` 后方虽有 `datapath` 字符串，但实际内嵌配置从区域 `+20` 开始读取，该处首字节为零；未见参数解析按后方列表过滤。此前仅凭该列表怀疑参数覆盖受阻，证据不足，现以以下运行观察修正。

三组自写 TJS 路径探针正常退出，输入数均为零：

| 条件 | 观测到的 `System.dataPath` | 后续参数查询／配置文件 |
|---|---|---|
| 无 `.cf` 与命令行覆盖 | 本批副本 `savedata/` | 环境配置 `krenvprf.kep` 在该目录 |
| `.cf` 指向 `cf-data`，该目录 `.cfu` 再指向 `cfu-data` | 仍为 `cf-data/` | 参数查询返回 `.cfu` 的 `cfu-data`；环境配置仍写 `cf-data` |
| 命令行指向 `cli-data`，同时有 `.cf` 与 `.cfu` | `cli-data/` | 命令行覆盖 `.cf`；`.cfu` 自定义标记被读取，数据目录仍为 `cli-data` |

这些观察证明固定构建本批参数的实际效果，不是对所有启动参数优先级或所有商业写入的证明。早期探针报告失败还涉及旧版对象调用约定：`Dictionary.saveStruct` 存在，须按原脚本用 `incontextof` 绑定对象；不能将那次失败写成接口不存在。

## 后台事件、查询与画面

`<KiriKiri验证目录>/input-probe-05/` 的自写 Window 在独立桌面接收一组原生 `postInputEvent` Enter：按下处理器于 tick 13 返回记录，松开处理器于 tick 19 返回记录。tick 是 50 毫秒 Timer 回调序号，不是游戏帧。首图通过现有黑白帧拒绝规则后才创建动作门控。

- 首图为紫色等待画面，操作后图为绿色 `ENTER UP HANDLER`；两图均为 644×364 客户区 PNG，摘要不同，已读回核对。
- 原生单参数 `System.getKeyState(13)` 在按下和松开处理器中均返回零。故事件消费、画面变化已观察到，但完整键状态语义未成立，不能直接用于会复查状态的 KAG。
- 官方接口文档只记录单参数 `getKeyState(code)`。早期第四批额外布尔参数和 `cached`／`physical` 标签没有依据；第五批删除额外参数与错误标签后重测，原生查询仍为零。
- 第一批按客户区精确尺寸找窗口失败，零输入；第二／三批无处理器报告，第三批也无定时器心跳。临时 Timer 未按构造约定绑定回调，改为与原脚本一致的 `new Timer(callback, "")` 后才观测到正常事件。失败批次保留，不当作输入接口不支持的证据。
- 原 EXE 与六份原存档在各批前后摘要一致，前台 HWND 前后相同；这些观测不等于系统范围的写入审计或真人并发验收。

临时探针阶段尚未形成协议、暂停释放、租约、故障回收或存读档验收。后续已按[批准实验桥设计](superpowers/specs/2026-11-16-kirikiri-bridge-design.md)实施下述桥，探针原生查询为零的结论保持。

## 实验桥实施与边界

`plugins/kirikiri/` 只接受上述固定 EXE 与仓库自写 Window 工程：原生 `postInputEvent` 触发正常事件，仅合成 Enter 的 `System.getKeyState(13)` 包装为按下 1／松开 0，其他键仍委托原查询。原处理器返回后记录消费；实际画面与 PNG 摘要变化、两次 Enter、处理器内部状态一致均已实证。原处理器抛错不记录成功，解绑保留游戏后续替换的方法。

宿主与 Emuera 共用 `gameqakit/duration_host.py` 和 `gameqakit/capture_worker.py`，插件独立且现有 v2 JSON 协议保持。内部目标控制为严格七行 ASCII 数据；观察仅解析受限原生结构文本，不执行表达式。原 15 秒启动、1.5 秒租约、1 秒 UI／释放／关闭、3 秒截图、100 毫秒 I/O 和首图 99.9% 判据均保持。观察快照短读不更新活性、不接受新命令，也不延长释放期限。

`bridge-review/suite-15/` 的 28 项真实桥测试及 3 项数据解析测试通过，涵盖初始暂停和新观察、输入与图像、去重／身份／过期／恶意记录拒绝、黑白杂点首图零输入、动作后黑图、截图写入故障／卡死、租约／人工暂停／持续读错、UI 卡死、未确认释放叠加重复暂停、长按退出、宿主终止和解绑。正常 Enter 在自写第 1 槽保存 `123`、改为 `999` 后同进程与新会话均恢复 `123`；桥不调用存读档或直接改变进度，旧会话及按键状态不进存档。账本、前后原文件摘要与测试失败批次另存本地。

完整回归与交付复核见 `bridge-review/release-regression/` 和 `bridge-review/delivery-verification.json`；使用、来源／许可缺口、恢复方法见[桥说明](../plugins/kirikiri/README.md)及[来源记录](../plugins/kirikiri/SOURCES.md)。运行时不分发，固定私有构建 upstream revision 及再分发许可未证实。

上述仅是固定构建、自写 Window 的实验结果；商业 KAG、多窗口、其他版本、通用受管入口、长期稳定性和真人并发未验。产品候选仍为 `adapter=null`、`control_verified=false`，不能直接将桥放入任意游戏宣称兼容。

## 证据与恢复

原始提取清单、转码脚本、REA evidence bundle 及地址对应反编译片段保存在 `<KiriKiri验证目录>/static-review/`；路径与输入探针及范围／还原账本分别位于 `path-probe-01/` 至 `path-probe-03/`、`input-probe-01/` 至 `input-probe-05/`。最新正确接口测量为 `input-probe-05/`，交付复核入口为 `probe-delivery/verification.json`。

静态函数证据包括 `ev_8258b016c6de54bdb0ff6e4c5c7c623f848d45899cd88d409a341cabc9ee7be5`（`0x60FA38`）、`ev_a0b69deb91887162275c3aa427d65a42b9340af935ee0aae8bd5fb906e0c7850`（`0x67AA34`）、`ev_29989332850eee8b3d373cf0713c5c9d8460b35633e7a9435e657c525eea48e3`（`0x67AEF8`）。这些是当前 IDA 数据库的静态推导，不是运行观测；数据库修订与完整调用覆盖仍未知。

撤回本轮实验实现时，整体还原新增桥、夹具／测试、共用监督器提取与 Emuera 入口调整；撤回早期候选识别另还原对应识别代码和测试。均不涉及原游戏数据迁移，测试副本按本地账本关闭 Job 后重建，不覆盖原数据。
