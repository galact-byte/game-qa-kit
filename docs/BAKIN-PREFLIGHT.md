# Bakin 输入适配预检

本轮完成只读识别和两个本机构建的静态核查，尚未运行自写 Bakin 工程。没有后台输入、截图或商业玩法验收结果，也没有新增控制适配器。

## 已确认的构建差异

| 项目 | 构建 A | 构建 B |
|---|---|---|
| Player／Engine 文件版本 | 1.13.1.1 | 2.5.1.0 |
| `bakinengine.dll` SHA-256 | `0bbdd30990ff79752fbbece4eccc27e3c0d70499579a248066a36f65ba52a827` | `e79836201d17e5c40754011df9f96e99c92be61b95d674195581bc36e80e5a19` |
| `Yukar.Engine.Input.ForceSetState` | 未发现该公开方法 | 公开静态方法，参数为 `KeyStates`、`GameState` |
| `InputCore.ChangeKeyStates` | 内部方法 | 内部方法，公开入口调用它 |
| 实机输入结论 | 未验证 | 未验证 |

证据来自本地程序集的元数据与反编译类型，未执行商业 Player，也未改写程序集或资源包。反编译文本和完整本地目录清单留在证据目录，不随源码提交。

2.5.1.0 的 `ForceSetState` 将状态写入指定 `GameState` 对应的直接、触发、释放触发和重复状态；`InputCore.Update` 又会重算这些状态。因此它只是可研究的输入入口：调用时机、松键语义、输入隔离、失焦后循环、目标侧消费及安全生命周期都需要自写工程实测。不能把一次 API 调用等同于游戏已执行动作，也不能把新版文档套用于 1.13.1.1。

## 只读识别范围

`inspect_game` 需要同目录的三项证据：

- `bakinplayer.exe` 的 `MZ` 文件头；
- `bakinengine.dll` 的 `MZ` 文件头；
- `data.rbpack` 的 `BKNPAK\x00\x01` 签名。

符合时只返回 `engine=bakin_candidate`、`adapter=null`、`control_verified=false`。这不是完整 PE／资源包有效性校验，也不授权加载程序、解包资源或启动受管会话。文件名匹配但签名不符、缺失任一证据时不认定为 Bakin。两个实际目录均已通过只读识别核对。

## 自写样本前置缺口

本轮在本机 Steam 配置登记的 F、D、G、E 四个库中未找到名称含 Bakin 的安装目录；这是有界查找结果，不代表全盘不存在其他便携安装。当前未取得可修改、自有且能正常运行的空白工程与对应导出包。

[官方试用版说明](https://rpgbakin.com/en/products/rpg-developer-bakin-demo)明确列出：

- 试用版不能将创建的游戏导出为独立可执行程序；
- 试用版不能使用插件；
- 使用期为首次启动后 30 天；
- 官方列出的可用磁盘空间要求为 8 GB；该数字不是实际下载量。

因此不将下载试用版作为解决本轮前置缺口的方案。本轮没有下载、安装或购买 Bakin，也没有尝试规避试用版限制。

推荐先取得合法正式版生成的、允许修改的最小自写工程及其 Windows 导出包，保留项目源文件、版本信息和来源许可。也可使用老师已有的正式版安装；取得后先核对版本和样本保存边界，再进行隔离预检。当前缺口不影响其他引擎的只读调查。

## 后续验证顺序

1. 核对自写工程、运行时、来源与保存路径，创建隔离副本并登记摘要与还原方式。
2. 只运行自写工程，先验证无输入时持续取新画面、失焦继续更新及独立桌面隔离；无效首图立即停手。
3. 经引擎输入接口验证正常逻辑消费、可见位移和松键，不直接移动角色或调用场景切换。
4. 验证目标更新时机、租约到期、暂停与恢复、宿主退出、截图失败和进程回收。
5. 临时预检通过后另写正式接入设计，按既有规则取得批准再实现受管桥。

## 本地证据索引

本地证据目录统一记作 `<bakin-preflight>`，实际路径保存在本机交接记录，不进入仓库。

- `candidates.json`：两个原始候选目录；
- `source-manifest.json`：8 个相关组件的版本、大小与 SHA-256；
- `candidate-*-Input.cs`、`candidate-*-InputCore.cs`、`candidate-*-GameMain.cs`、`candidate-*-Program.cs`：静态取证输出；
- `rea-static-evidence.json`：REA 静态检查记录；
- `steam-editor-search.json`：四个已登记 Steam 库的有界查找；
- `official-sources.md`：官方输入接口与试用版限制；
- `recognition-red.log`、`recognition-green.log`：识别行为的 Red／Green 记录；
- `real-recognition.json`：两个真实目录的识别结果；
- `full-regression.log`：全仓首轮 758 项，448 通过、309 条件跳过、1 项 Unity 创建目录联接时的 `WinError 32`；原失败保留。
- `affected-regression.log`：识别、接入、CLI、MCP 与 Unity 模块 78 项重跑通过；共享冲突根因未确认，不宣称已修复。
- `VERIFICATION.json`、`RESTART-HANDOFF.md`：最后复核与待办。

## 官方参考

- [Input 类参考](https://rpgbakin.com/csreference/doc/en/class_yukar_1_1_engine_1_1_input.html)：公开 `ForceSetState`、`GetState` 等签名；文档没有替代本机构建验证。
- [试用版功能对照](https://rpgbakin.com/en/products/rpg-developer-bakin-demo)：导出、插件、期限与磁盘要求。
