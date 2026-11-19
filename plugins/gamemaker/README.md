# GameMaker 固定自写 YYC 受管入口

仅接受 `samples` 原样自写工程和 `runtime-manifest.json` 登记的 Windows x64 YYC 成品。准备器复制已登记的 `gameqa-gamemaker.exe`、`data.win` 与样本配置；普通使用不运行编译器，不修改商业 EXE。组件须合法持有，不随主便携包分发。

## 使用

在「AI 后台接入」选择本目录 `samples`，后台组件目录选择包含登记 EXE 与 `data.win` 的本机构建目录，输出选择不存在的新目录。未知组件、修改后的工程、额外资源及链接路径会被拒绝，改文件名不能绕过校验。固定组件身份见 [runtime-manifest.json](runtime-manifest.json)。

统一 MCP 按 `game_inspect` → `game_prepare` → `game_start` 启动；确认暂停且有效的首图，再以 `game_bind` 绑定桥目录并 `control_resume`。`control_action` 只开放向右的 `move` 和 `wait`，单位 `duration_ms`，单次 1–1,000 ms；等待不投递按键。人工暂停停止合成输入，游戏逻辑继续。结束由创建者调用 `game_stop` 并核对进程与摘要结果。

用户无需安装 IDE 或编译器。维护者的本地构建步骤与组件许可见 [SOURCES.md](SOURCES.md)；编译出不同摘要的成品不会自动取得接入资格。

## 输入与生命周期

自有 C++ 桥静态链接到固定自写目标，在 Step 普通 `keyboard_check(39)` 前设置当前进程的 `_IO_KeyDown[39]`；原事件据查询结果移动，桥在查询之后发布消费记录。不发送系统键鼠，不置前台，不直接改变角色位置。

目标 `GetTickCount64` 构造绝对期限。目标挂起期间没有新采样，恢复时读数包含挂起时段；首个恢复采样拒绝过期持键。心跳只更新 1.5 秒租约，不延长单动作；租约过期后同编号续租不能自行恢复，须新编号显式恢复。沿用 1 秒停帧／释放、3 秒截图、15 秒启动与 99.9% 黑白帧门槛。

双向记录逐代只发布一次，绑定会话、PID 与序号。忽略目录枚举竞态产生的较旧消费记录，且不刷新活性；新采样中的时钟或确认编号倒退仍拒绝。当前输入通道丢失、无效记录、租约到期均撤销合成按键；目标无法采样、回执通道损坏或截图失败则按现有宿主边界停止并回收 Job。已消费按下、已消费释放和释放未知分别记录，进程退出不冒充正常松键消费。

## 验证与范围

源码和冻结 MCP 共用宿主、worker、生产截图与 Job。用例覆盖暂停首图、右移／等待、普通查询消费与释放、重复请求、暂停／恢复、过期请求、租约、通道损坏、目标停帧、截图失败、双会话隔离、创建者正常 EOF 与强制终止回收。独立原生探针另验动作到期、挂起恢复、同编号续租不恢复以及输入通道丢失释放。原始失败批次保留，详见 [预检记录](../../docs/GAMEMAKER-PREFLIGHT.md)。

```powershell
$env:GAMEQAKIT_TEST_GAMEMAKER_COMPONENTS = '<登记成品目录>'
python -X utf8 -B -m unittest -v tests.test_gamemaker_bridge tests.test_gamemaker_managed
```

设置 `GAMEQAKIT_TEST_PORTABLE_MCP` 为便携 `GameQA-MCP.exe` 可验证冻结入口；没有组件时运行用例明确跳过，跳过不算通过。原生状态机测试源在 `tests/gamemaker/BridgeStateTests.cpp`，需现有 MSVC x64 环境编译运行。

只声明登记自写 YYC 构建的受管右移／等待。VM 输入、商业程序、其他构建和按键、鼠标、菜单、存读档、音频、真人并发及长期稳定性未验。独立桌面与目录复制不构成文件系统／注册表／网络沙箱，不承诺零 C 盘写入。

## 升级与回退

宿主、原生桥、登记摘要和固定成品需同步更新；先结束旧会话，再准备新目录，不复用旧记录。撤销插件及接入登记可回退，无原游戏存档迁移。自写源和源成品结束后校验摘要；有证据或新数据的会话目录单独保留。
