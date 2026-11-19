# GameMaker 后台控制预检

## 当前结论

2026-11-19 完成盘中一处 GameMaker 候选的只读结构核查与社区工具资料核对。尚未建立可复用的空白工程运行组件，也没有输入、取图或受管生命周期实测。没有新增产品控制入口。

当前阻碍是缺少允许测试的空白工程运行包或可用开发环境；不能把商业 YYC 程序当作可加载任意自写数据的通用 runner。先取得自写样本，再评估引擎内部输入映射，正式桥架构须另行设计、批准。

## 本机文件证据

| 项目 | 本次观测 |
| --- | --- |
| 候选目录 | 盘点中的 `MAGICAL ANGEL FAIRY PRINCESS`，只读 |
| 主程序 | `FairyPrincess_v001.exe`，PE x64，34,159,616 字节 |
| 主程序 SHA-256 | `36ada9219f6e5d9e96b532efe5bfd2bf6e3afdb9257e080cb60fd956970be3f8` |
| 数据文件 | `data.win`，64,538,501 字节；`FORM` 长度与实际大小相等，各分块完整落在文件范围内 |
| 数据 SHA-256 | `7b1a37c68bda4504df0583c05cad539e1f8699ad2d1d189ed39d22d580915020` |
| 编译线索 | 没有 `CODE` 块；EXE 中匹配 20,202 处 `gml_Object_…`、222 处 `gml_Script_…` 名称，符合 YYC 原生编译特征。这些是字符串匹配数，不等于独立函数数量 |
| GEN8 原字段 | 字节码标记 17、版本字段 2.0.0.0、窗口字段 1280×800；不将这些字段当作实际运行时版本认证 |
| 原目录完整性 | 2,384 文件、1,201,526,478 字节，核查前后逐文件摘要一致 |

只读扫描本机工具、开发数据与源码工作目录（跳过常见依赖／构建目录）及所检查的安装目录顶层，没有找到 GameMaker、Igor、GMAssetCompiler、UndertaleModTool、Dogscepter 或 YYToolkit 可用工具。命中的三个编辑器图标不是运行组件；该结果不等于全机不存在任何开发环境。扫描范围的本机路径保留在本地恢复记录。

## 可复用方案与输入边界

优先评估 [YYToolkit](https://github.com/AurieFramework/YYToolkit) 的现有引擎接口，避免重新自建通用加载器。本次核对上游 `86c133dc7f4e87991dd2cb7f2eac056dc5f13124` 的 README、共享接口和事件定义：

- `CallBuiltin`／`CallBuiltinEx` 可调用引擎函数；`CreateCallback` 提供事件回调。它们只证明上游存在接口，不证明本机候选兼容或后台输入已验。
- `EVENT_FRAME` 定义为 `IDXGISwapChain::Present()`；`EVENT_OBJECT_CALL` 表示 `Code_Execute()`。不能将任意回调计数直接作为完整游戏逻辑帧。
- README 将现代 x64 列为支持，但不同编译模式与版本的支持程度分别列示，不能由此追认该固定商业构建。
- 项目许可为 AGPL-3.0；若采用其代码、链接或分发组件，须先独立核对适用义务与来源记录，再确定交付方式。本次未下载、安装或集成软件。

[官方 keyboard_key_press 手册](https://manual.gamemaker.io/monthly/en/GameMaker_Language/GML_Reference/Game_Input/Keyboard_Input/keyboard_key_press.htm)明确说明：Windows 上在 Step／Alarm 等事件反复调用时，可能在游戏之外触发按键。因此不得直接把此函数当作隔离的后台输入原语。对应的 [keyboard_key_release](https://manual.gamemaker.io/monthly/en/GameMaker_Language/GML_Reference/Game_Input/Keyboard_Input/keyboard_key_release.htm) 也不能替代输入隔离与正常查询消费的实测。

推荐后续在自写场景验证仅引擎内生效的输入状态映射／查询覆层，让正常 `keyboard_check` 等查询驱动场景，并单独观察消费与松键；不能直接改角色位置、调用任务完成或切换场景冒充正常输入。

`GEN8` 字段布局参考 [UndertaleModTool 源码](https://github.com/UnderminersTeam/UndertaleModTool/blob/bf485707c9c917fc3f153e78a82ef6f707c5821c/UndertaleModLib/Models/UndertaleGeneralInfo.cs)。本次只阅读公开源码，未安装其程序或重打包商业数据。

## 下一批进入条件与顺序

1. 取得允许使用的 GameMaker 开发环境，或含源码与匹配运行组件的最小空白工程运行包；新增下载／安装先取得用户批准。首批仅右移／等待，样本不含商业资源或存档。
2. 固定运行组件与样本摘要，核对并明确程序旁、AppData、注册表等写入位置；新建任务副本，保留还原记录。未核清写入边界不启动。
3. 在独立桌面先零输入验证持续新画面与失焦更新；首图无效立即停手，不降低现有图像门槛。
4. 实证仅目标引擎内输入、普通查询消费、按住／释放后的画面、用户桌面输入隔离。不得使用全局键鼠或置前台回退。
5. 原语成立后提交正式接入设计；复用统一 MCP 与既有监督生命周期，按真实计时证据决定协议，不把 Present 当作逻辑帧。随后才实现和验收租约、停帧、取图故障、双会话与创建者退出。

## 证据与未完成项

本地 `<GameMaker预检目录>/preflight-01/` 保存 `inspect_candidate.py`、`source-manifest.json`、`result.json`、上游源码参考、`sources.json` 与恢复记录。原始商业文件、反汇编产物、截图和本机绝对路径不随源码提交。

REA 选定固定 EXE 后，IDA 在打开分析库阶段超时；关闭返回 `cleanup_incomplete`，不能宣称取得反编译结果或工具清理已确认。随后 IDA 会话枚举为空。只读结构结论来自本地文件解析；REA 诊断失败及其任务临时目录另记本地恢复文件，不擅自更改全局工具配置。

本次没有运行游戏、发送输入、改动存档、制作桥或改变产品识别／控制声明。回退本次文档不涉及数据迁移。
