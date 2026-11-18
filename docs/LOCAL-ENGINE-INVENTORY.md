# 本机游戏引擎盘点与覆盖缺口

本记录来自两个下载目录的只读盘点，不代表扫描过所有磁盘、压缩包内容或用户的全部游戏。引擎实际可用范围以[支持表](ENGINE-COVERAGE.md)为准。

## 盘点范围与计数

2026-11-15，完整遍历 D／G 两个 `BaiduNetdiskDownload` 目录：

以下为 11 月 15 日扫描的历史计数。11 月 18 日对原清单的 404 个未分类目录逐项补审，结果见[补审记录](#11-月-18-日文件证据补审)。

| 项目 | 数量 |
|---|---:|
| D 盘遍历目录 | 27,011 |
| G 盘遍历目录 | 22,939 |
| 合计遍历目录 | 49,950 |
| EXE 文件 | 2,046 |
| 含 EXE 的目录 | 520 |
| 读取错误 | 0 |
| 跳过的重解析点 | 4 |
| 尚未分类的含 EXE 目录 | 404 |

分类单位是“含 EXE 的目录”，不是独立游戏。同一游戏可能带运行时、卸载器、启动器和工具子目录；以下数量不能相加后解释为游戏总数。未进入压缩包，不跟随重解析点，也不把零读取错误解释为所有引擎均已识别。

## 已找到的目录证据

| 分类 | 目录数 | 控制覆盖缺口 |
|---|---:|---|
| RPG Maker MV | 28 | 仅已登记工程／运行时的受管通路，不自动覆盖这些商业样本 |
| RPG Maker MZ | 20 | 同上 |
| RPG Maker VX Ace | 3 | 有独立桥与固定受管工程，其他样本逐款核查 |
| Unity Mono | 28 | 按旧 Input、新 Input System、Rewired 及构建分别核查 |
| Unity IL2CPP | 8 | 固定新 Input System 构建已有实证，其他构建不继承 |
| Godot 4 | 4 | 固定 4.5.1 自写工程已接入通用启动；另两个本机运行时拒绝当前入口 |
| Ren'Py | 1 | 固定 8.3.2 自写工程已接入通用启动；商业工程仍只读识别 |
| TyranoScript | 5 | 固定 Ver5／Electron 7.1.2 自写工程已接入通用启动；商业工程仍只读识别 |
| Emuera | 6 | 固定 1.824 自写工程已接入通用启动；数字／文本菜单、其他版本及商业启动未验 |
| RPG Developer Bakin 运行目录 | 2 | 无已验控制通路 |
| Bakin 启动器目录 | 2 | 与运行目录分别计数，不算额外两款游戏 |
| Wolf RPG | 4 | 含补审发现的两个不同构建，均超出既有桥的 EXE 摘要登记 |
| HSP | 1 | 固定 HSP 3.7 x86 自写样本已迁入通用入口，商业候选沿用独立桥 |
| SRPG Studio | 1 | 固定构建的受限原生后端有标题／设置实证 |
| Artemis | 1 | 无已验控制通路 |
| GameMaker | 1 | 无已验控制通路 |
| Unreal | 1 | 无已验控制通路 |
| KiriKiri | 1 | 商业候选为只读识别。固定 2.32.2.426／x86 自写 Window 已迁入受管入口，独立实验桥另验正常存读档 |
| Flash | 1 | 自写 AVM1 样本已接入通用入口，表中的商业候选尚待验证 |
| 工具与独立运行时 | 164 | 补审确认，详见下表 |
| 未分类 | 238 | 仍可能包含工具、数据子目录或其他游戏 |

未出现在表中的引擎不代表本机没有，也不代表已经覆盖。XP／VX、2000／2003 目前只有识别能力；DuckStation 已有固定构建桥，但本次盘点未补齐原受管入口样本。

## 11 月 18 日文件证据补审

| 原未分类目录复核结果 | 目录数 |
|---|---:|
| MTool 客户端、链接工具、加载器、翻译辅助模块 | 108（32＋32＋32＋12） |
| 独立 Wolf、Emuera 运行时与 Flash 播放器工具 | 56（50＋3＋3） |
| Wolf 游戏候选 | 2 |
| 仍未分类 | 238 |
| 合计 | 404 |

两处 Wolf 候选均同时具备 `Game.exe` 的 `WOLF RPG Editor` 版本资源、`Data/BasicData.wolf` 和 `Data/MapData.wolf`。版本资源分别为 `ver2.22` 与 `3.343.2024.617`。两个 `Game.exe` 的 SHA-256 均不匹配现有桥登记，仍待各自构建验证。

从已归类目录中另外复核了八处候选，沿用原有分类。下列证据只用于确认候选归属和挑选后续试验样本。

| 分类 | 目录数 | 复核依据 |
|---|---:|---|
| Bakin | 4 | 两组启动目录与其 `data` 子目录。播放器版本资源分别为 `1.13.1.1` 与 `2.5.1.0`，均带 `bakinengine.dll`、`data.rbpack` |
| GameMaker | 1 | `data.win` 的 `FORM`、`GEN8` 标记与 `Created with GameMaker` 版本资源 |
| Artemis | 1 | `root.pfs` 的 `pf6` 头、分卷、`scenario` 和 `system.ini` |
| Unreal | 1 | `BootstrapPackagedGame` 版本资源、`Engine` 目录、Shipping EXE 与 PAK |
| Flash | 1 | `Shockwave Flash` 播放器版本资源、附带 SWF 头，以及 EXE 末尾的投影器签名与内嵌 SWF 头 |

逐目录证据见 `<引擎盘点目录>/review-20261118/review.json` 和 `review.csv`。补审以原清单为输入，读取文件标记与版本资源，产品识别和控制范围以[支持表](ENGINE-COVERAGE.md)为准。

## KiriKiri 本轮核查

一个候选命中完整头长度的 XP3 文件和 EXE 前 64 KiB 的 MZ／`@TVP(kirikiri) for win32` 标记，返回 `kirikiri_candidate`，保持 `adapter=null`、`control_verified=false`。测试覆盖大小写扩展名、文件路径输入、源文件不变、截断头／伪扩展名／无运行时标记拒绝及有界 EXE 读取；不解析归档索引，不把候选识别当作版本或控制认证。

固定样本版本资源为 2.32.2.426／x86，随包带 MTool 与转区组件；只读提取的 KAG 脚本同时接收事件与查询 `System.getKeyState`。固定 EXE 副本配纯自写 TJS 的三组路径探针已证实：命令行 `-datapath` 覆盖 `.cf`，`.cfu` 不重定位已经初始化的 `System.dataPath`。内嵌配置实际读取起点为空，不能从后方 `datapath` 字符串推断参数被过滤。

自写 Window 后台 Enter 按下／松开处理器已被原生事件触发，前后图从紫变绿；原生单参数键状态查询仍不随合成事件改变。后续已按批准设计加入仅合成 Enter 的 TJS 状态覆层与独立宿主：原处理器实际读到按下 1、松开 0，其他键仍委托原查询；确认／等待、暂停释放、故障回收及正常存读档已在固定构建自写工程实证。商业 KAG、多窗口、其他版本及工具入口未验，候选控制标记保持关闭。

原 EXE 与六份原存档摘要前后相同，未运行商业脚本、插件或原存档；独立桌面不隔离任意文件写入。详细探针、失败批次及恢复见[核查记录](KIRIKIRI-STATIC-REVIEW.md)，实验用法与许可缺口见[桥说明](../plugins/kirikiri/README.md)和[来源记录](../plugins/kirikiri/SOURCES.md)。原始证据仅留 `<KiriKiri验证目录>/bridge-review/`，不提交商业文件及反编译产物。

## Emuera 本轮核查

六个候选目录均通过 `CSV/FLAG.csv`、`ERB/` 和 `MZ` 开头的 `Emuera*.exe` 联合识别；产品返回 `emuera_candidate`，`adapter=null`、`control_verified=false`。只读检测不加载程序集或运行脚本，不把文件名前缀和 MZ 头当作完整 PE／版本认证；普通目录、缺标记和非 MZ 文件不命中。

其中一个 Emuera 1.824／.NET Framework 4.5 构建已完成反编译核查：原入口运行 WinForms 消息循环，原输入框处理 Enter；配置、脚本和保存默认基于程序目录。其他候选包含 Skia 等不同封装，不能继承该版本的加载方案。

只复制固定运行时、使用自写 CSV／ERB 的独立桌面探针已验证：输入框收到数字 `7`，Enter 后出现绿色 `ACCEPTED 7`，再次 Enter 出现橙色完成提示。三张图已读回，前台窗口前后相同，原 EXE 摘要一致；原商业脚本与存档未运行。早期启动未识别到窗口的失败现场独立保留。

早期探针的松键只确认投递；后续已按恢复工作时批准的设计新增 `plugins/emuera/` 实验桥。C# STA 启动器运行固定程序集原入口，目标 UI 线程在原按键处理返回后确认 Enter 按下／松开，独立 Python／Job 宿主管理 v2 确认／等待、暂停、租约、取图和故障回收。21 项真实检查通过：原入口路径与处理器重挂、重复输入去重、黑白图零输入、UI／取图卡死、未确认释放和关闭长按，以及正常第 1 槽存档的同进程与新会话读取。详情见[实验说明](../plugins/emuera/README.md)。

登记的自写场景已通过通用入口验证确认／等待、黑屏停手与回收。数字／文本菜单、商业游戏、其他版本、受管存读档、长期稳定性和真人并发未验；六个候选的产品控制标记不变。固定构建的完整再分发许可与上游 revision 尚未证实，原运行时不随仓库或便携包分发，见[来源限制](../plugins/emuera/SOURCES.md)。

原始反编译输出、探针、截图和核对日志保存在 `<Emuera验证目录>/source-review/`、`engine-probe-03/`、`identification-*.json` 与 `bridge-release-verified/`，此前失败现场另存于 `bridge-release-full/`，不随源码提交。运行副本位于验证目录，不构成文件系统／注册表沙箱。

## TyranoScript 本轮核查

五个候选均在 ASAR 索引中找到同一网页根下的 `tyrano/tyrano.js` 和 `tyrano/plugins/kag/kag.js`。产品识别现支持这对松散文件及 `resources/app.asar` 的根层同名成员；仅返回 `tyranoscript_candidate`，`adapter=null`、`control_verified=false`。

ASAR 识别只读取头与索引，JSON 索引上限为 16 MiB，不解包、不执行脚本、不跟随链接或外置成员；拒绝损坏的头、截断文件和超出归档范围的成员。未知封装不通过文件名或字符串猜测。

一个候选提供的 Electron 7.1.2／TyranoScript Ver5 引擎组件已在独立桌面的自写场景中验证：Enter 进入引擎原有键盘处理并推进场景；松键后 `is_keydown=false`；正常鼠标点击再次推进场景。前后截图确认方块右移及变色，所用原文件摘要一致。原商业剧情没有运行。

后续按已批准设计新增 `plugins/tyrano/` 实验桥，复用 v2 毫秒协议，确认键和等待经 BridgeClient 验证；独立 Python／Job 宿主处理主进程、渲染页、截图和释放确认停滞。文件保存和 localStorage 都验证正常保存、同进程读取和新会话读取，桥状态不进入游戏存档。暂停只停止输入，保护期限不变。详情见[实验说明](../plugins/tyrano/README.md)。

登记的自写场景已通过通用入口验证确认／等待、黑屏停手与回收。商业游戏、其他版本、鼠标协议和真人并发尚未验证；五个候选识别的控制标记不变。

本机原始清单、脚本、日志与截图分别保留在 `<引擎盘点目录>/audit-20261115-150828/`、`<Tyrano验证目录>/engine-probe-01/` 和 `final-review/`，不随源码提交。接入取舍见[已批准设计](superpowers/specs/2026-11-15-tyranoscript-bridge-design.md)。
