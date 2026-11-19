# GameMaker 后台控制预检

## 当前结论

2026-11-19，官方 IDE 2026.0.0.16 与运行组件 2026.0.0.23 已在任务目录准备完成，官方空白模板、自写 VM 工程和自写 YYC 工程编译成功。两个自写构建均通过独立桌面后台零输入取图；YYC 生成源码的独立实验副本通过引擎内右键状态映射、普通 `keyboard_check` 消费、到期松键与画面验证。

已获批准并实现固定自写 YYC 受管入口。源码与便携真实 MCP 各 9 项通过，覆盖暂停首图、右移／等待、普通查询消费与释放、租约到期、双会话、截图／通道故障、目标停帧及创建者正常／强制退出回收；独立原生探针确认挂起后首个恢复采样不重放过期持键。使用与组件要求见[插件说明](../plugins/gamemaker/README.md)，架构边界见[接入设计](GAMEMAKER-ADMISSION-DESIGN.md)。

只接登记 x64 自写 YYC 成品及原样场景。VM 输入、商业程序、其他构建与按键、鼠标、菜单、存读档、音频、真人并发及长期稳定性未验；不由当前有限入口扩大支持范围。下文保留正式接入前的实验结果与失败记录。

## 商业候选的只读证据

| 项目 | 本次观测 |
| --- | --- |
| 候选目录 | 盘点中的 `MAGICAL ANGEL FAIRY PRINCESS`，只读 |
| 主程序 | `FairyPrincess_v001.exe`，PE x64，34,159,616 字节 |
| 主程序 SHA-256 | `36ada9219f6e5d9e96b532efe5bfd2bf6e3afdb9257e080cb60fd956970be3f8` |
| 数据文件 | `data.win`，64,538,501 字节；`FORM` 长度与实际大小相等，各分块完整落在文件范围内 |
| 数据 SHA-256 | `7b1a37c68bda4504df0583c05cad539e1f8699ad2d1d189ed39d22d580915020` |
| 编译线索 | 无 `CODE` 块；EXE 匹配 20,202 处 `gml_Object_…`、222 处 `gml_Script_…` 名称，符合 YYC 特征。字符串匹配数不等于独立函数数量 |
| GEN8 原字段 | 字节码标记 17、版本字段 2.0.0.0、窗口字段 1280×800；不作为实际运行时版本认证 |
| 原目录完整性 | 初始清单为 2,384 文件、1,201,526,478 字节；删除前全部摘要一致，证据见本地 `GAME-DELETION-PLAN.json` |

商业程序未启动、未注入、未修改或重打包，不用它运行自写数据；存档未改。此前本机扫描未找到可用 GameMaker 环境是准备之前的历史结论，不能用于否定本轮已建立的环境。

## 官方开发环境与构建

组件来源为官方 IDE 与 `https://gms.yoyogames.com/Zeus-Runtime-LTS2026.rss`，本地保存原始清单、归档长度、SHA-256 和安装记录。用户提供 Core 包，其余匹配主机与 Windows 组件经批准取得，官方安装流程校验签名并解包到任务目录；YYC 模块亦已由官方 Igor 安装。

- 账号运行组件页的完整列表确实没有 Windows 项。本版 feed 将 `base-module-windows-x64`、`windows` 和 `windowsYYC` 分开列出，不能继续要求用户在账号页面查找不存在的项目。
- 官方空白模板 VM 编译退出码 0，自写 VM 编译退出码 0。实测未被账号登录阻断；之前将登录许可一概列为必要前置的判断已纠正，不代表所有发行模式或许可情况均可免登录。
- YYC 生成工程默认使用 v142，而本机可用的是 VS 2022 v143。覆盖 `PlatformToolset=v143` 后编译退出码 0，未新增旧工具链。此覆盖只说明当前自写工程构建通过，不扩大为所有工程兼容。
- 运行组件、缓存、工程、TEMP/TMP 和构建输出主要落在本地任务目录；IDE 启动记录仍列明系统崩溃标记路径，不能宣称零 C 盘写入或完整文件系统隔离。

源码库不提交官方安装包、运行库、编译器、生成的引擎头文件／反汇编或游戏二进制。具体本机路径和恢复操作只保留在本地记录。

## 后台运行与输入实证

所有运行批次使用现有 `DesktopProcess`／Job Object 和生产 `capture_worker.py`。首图先通过原有图像守卫，再进行有限输入；未使用置前台、实体光标或全局键鼠。

| 批次 | 实测结果 |
| --- | --- |
| `runtime-vm-01` | 窗口筛选误写成 640×480，实际为 512×384，未选中窗口；只修正筛选，未降低图像判据 |
| `runtime-vm-02` | 失焦步数 2→42→81，三张图摘要不同，x 均为 80，无指令移动，目标已回收 |
| `runtime-yyc-01` | 失焦步数 2→50→99，三张图摘要不同，x 均为 80，目标已回收 |
| `input-yyc-red-01` | 未加入输入映射的原始自写 YYC 样本未消费有效命令，按预期失败；保留原始结果 |
| `input-yyc-green-01` | 加入映射的独立生成源码副本通过；状态采样 x=80→82→130→188→188，普通查询返回按下／松开，目标已回收 |

输入实验在自写 Step 事件开始处调用自有 C++ 映射，只写当前进程 Runner 的 `_IO_KeyDown[39]`；原事件仍调用生成的 `YYGML_keyboard_check(39)` 决定是否执行原本的移动逻辑。没有直接修改位置、任务或场景。符号依据来自本地固定版本 Runner 库，不作为跨版本公开接口。

探针命令只含固定标记和 `GetTickCount64` 绝对期限，实验持键 900 ms，上限 1,000 ms。缺失、短读或无效数据不置键，到期不再置键。它只是原语探针，没有正式会话身份、消费回执、暂停或续租协议，不能用于真实 MCP 控制。

逐图复核的七张生产截图中，色块左边界为 80、80、104、154、188、188、188，均为 1,024 像素。持键两图继续右移，释放后两图和删除命令后的图均停在 188，界面步数继续增加。窗口标题采样与取图发生在不同时间，图上步数／位置以实际图像为准，不把标题当成同一帧图像。七图 `focus=0`；前台采样未出现目标窗口，不等于真人并发输入已验。

## 正式受管桥与验收

`plugins/gamemaker/` 新增自有静态桥、严格二进制通道、固定构建登记／准备器和受管会话，复用统一协议 2、生产取图、独立桌面及 Job。目标在普通 `keyboard_check(39)` 前映射右键，查询之后才发布消费记录；单动作 1–1,000 ms，租约 1.5 s、停帧／释放 1 s、截图 3 s、启动 15 s 和 99.9% 黑白帧判据保持。

- `native-clock-02`：目标挂起时无新采样，恢复后的 `GetTickCount64` 增量包含 453 ms 挂起区间。
- `managed-native-resume-02`：挂起前仍持键；恢复的首个采样分别以 `action_expired`、`lease_expired` 松键，位置不变。同编号续租不能重新持键，丢失当前输入通道也撤销合成输入，目标已回收。
- `managed-regression-01`：桥、登记、引擎识别、统一准备／worker 和打包定向回归 57 项通过。旧回执退役时的目录枚举竞态另有红→绿回归，忽略较旧记录但不刷新活性；新记录的时钟或确认编号倒退仍拒绝。
- `managed-mcp-03` 与 `portable-managed-mcp-01`：源码、冻结 MCP 各 9 项实际执行通过，无跳过；覆盖正常输入、重复与过期请求、暂停恢复、租约、双会话、截图失败、损坏回执、目标停帧及正常 EOF／强制结束后的进程回收。按下已消费、释放已消费和释放未知分别保留。
- `managed-regression-full`：全仓发现 756 项，执行 456 项通过，按组件／平台条件跳过 300 项，不把跳过计为通过。首轮测试根目录漏建造成的 8 个错误已在测试环境修正后复跑；另一次准备状态读取权限错误保留原日志，后续未复现，未据此声称修复了产品缺陷。
- Windows Git 实际检出曾改变 8 个样本文件的字节并触发摘要拒启；`.gitattributes` 对固定样本保留原始字节后，在 `core.autocrlf=true` 下检出并复核全部 9 个登记文件通过，不放宽摘要。
- `portable-managed-build-01`：便携构建及自检通过，包括冻结 worker、29 个 MCP 工具、Qt 窗口、插件导入和原样样本摘要。主包不含 GameMaker 官方工具／库、固定样本 EXE／data.win 或商业资产；合法持有的登记成品仍须单独提供。本次不创建新 Release。

本地 `MANAGED-FINAL-RUN.json` 保存命令与退出码，`MANAGED-VERIFICATION.json` 汇总保存证据和完整性核对；后者的检查数不作为运行测试数。`MANAGED-VISUAL-CONTACT.png` 对照源码／便携首图、右移和释放后两图，原生产截图与消费回执分别保留。

## 社区方案与输入边界

仍保留 [YYToolkit](https://github.com/AurieFramework/YYToolkit) 作为后续无源码目标的候选路线。核对的上游为 `86c133dc7f4e87991dd2cb7f2eac056dc5f13124`：

- `CallBuiltin`／`CallBuiltinEx` 和 `CreateCallback` 是上游接口存在的证据，不能追认为本机候选兼容。
- `EVENT_FRAME` 对应 `IDXGISwapChain::Present()`，`EVENT_OBJECT_CALL` 对应 `Code_Execute()`；不能把回调数当完整逻辑帧，也不能由 VM 回调推断 YYC 事件边界。
- 许可为 AGPL-3.0；本轮未下载安装、链接或分发 YYToolkit，未新增通用加载器。

[官方 keyboard_key_press 手册](https://manual.gamemaker.io/monthly/en/GameMaker_Language/GML_Reference/Game_Input/Keyboard_Input/keyboard_key_press.htm)警告 Windows 上反复调用可能在游戏外触发按键。[keyboard_key_release](https://manual.gamemaker.io/monthly/en/GameMaker_Language/GML_Reference/Game_Input/Keyboard_Input/keyboard_key_release.htm) 也不能替代输入隔离。当前实验没有调用这两个接口。

`GEN8` 布局参考 [UndertaleModTool 源码](https://github.com/UnderminersTeam/UndertaleModTool/blob/bf485707c9c917fc3f153e78a82ef6f707c5821c/UndertaleModLib/Models/UndertaleGeneralInfo.cs)，仅阅读公开源码，未安装程序或重打包商业数据。

## 后续范围与证据保留

固定自写 YYC 的本次接入已实现。后续按任务分别评估 VM 输入、商业构建及玩法、其他按键和鼠标、存读档、音频、长期稳定性与真人并发；通用加载器或 YYToolkit 集成另列设计和批准范围。商业游戏未启动或注入；本次验证收口后按用户此前授权删除已通关游戏目录及同名归档，共 2,124,572,389 字节。删前原游戏 2,384 文件摘要一致，清单与结果保存在本地 `GAME-DELETION-PLAN.json`、`GAME-DELETION-RESULT.json`，保留既有证据，不触及全局存档。

本地 `<GameMaker预检目录>/preflight-01/` 保存原始结构清单、官方来源、构建日志、实验／正式桥的本地构建记录、失败批次、截图、`INPUT-VERIFICATION.json` 与 `MANAGED-VERIFICATION.json`。源码库只提交自有插件、样本、测试及文档，不包含本机绝对证据路径、官方运行组件或商业资产。

历史 REA／IDA 打开分析库超时、关闭回执 `cleanup_incomplete`，随后会话枚举为空；不能宣称取得商业反编译结果或完整工具清理已确认。固定 Runner 库的本地符号／反汇编取证另记本轮实验材料，不追认那次商业分析成功。

本次产品变化限于固定自写 YYC 的接入分派、插件及打包自检，来源和许可见[声明](../plugins/gamemaker/SOURCES.md)。回退须停止会话并同步撤销插件、登记及分派，无原游戏存档迁移。自写场景不调用保存 API；已知自写产品名的 AppData／注册表候选路径按本地记录核对，不等同全系统写入审计，也不承诺零 C 盘写入。
