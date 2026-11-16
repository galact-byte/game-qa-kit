# Emuera 固定构建实验控制桥

本目录为固定 Emuera 1.824／.NET Framework 4.5 的自写工程提供现有 v2 毫秒协议桥。只接受[来源记录](SOURCES.md)中的原 EXE SHA-256；其他私家版、Skia 或同名运行时不自动兼容。没有通用准备器、GUI 或 `game_prepare` 启动入口。

## 组成与输入

- `build.py`：使用本机已有的 .NET Framework 4 C# 编译器构建 x86 启动器；不下载或安装工具。`--testing` 仅供故障夹具使用，生产构建不包含遮图或卡死开关。
- `Launcher.cs`：STA 启动器通过原程序集入口运行引擎，在原入口设置 WinForms 默认值之后绑定目标输入框。只投递正常 Enter 消息；在原 `richTextBox1_KeyDown` 返回后的观察器中确认按下，在目标 UI 线程的 KeyUp 事件中确认松开。每次发送前重新核对处理器顺序；待处理输入期间不通过嵌套消息循环发布活性。
- `host.py`：独立 Python 宿主复用 `DesktopProcess`，持有 Win32 Desktop／Job，管理 BridgeClient 会话、租约、暂停、真实输入回执和保护期限。
- `capture_worker.py`：独立取图进程，连接本批桌面并沿用现有客户区取图和无效图像判据；阻塞时由宿主回收。

只开放 `confirm`、`wait` 与显式恢复，动作 `duration_ms` 为 1–1000 的整数，按钮仅声明 `ok`。初始输入暂停；`pause_scope=input`、`simulation_pause=false`。UI 观察序号不代表游戏帧，暂停时游戏仍可运行并持续生成新预览。只有实际按下、实际松开和操作后的有效新图齐备，才发布确认动作成功回执。

数字／文本菜单没有协议动作。早期探针输入数字 `7` 的结果不属于本桥能力，不能据此声称常见 Emuera 数字菜单可用。桥不调用场景推进、保存／读取函数，不修改输入框文本或游戏进度。

## 生命周期

沿用 15 秒启动、1.5 秒客户端租约、1 秒 UI 活性停滞、3 秒截图、1 秒释放确认与关闭期限。控制文件短时共享冲突最多重试 100 毫秒；不提高首图 99.9% 判据。纯黑、近乎全黑或白色首图后不发送输入。

人工暂停、租约失效和持续控制文件读取错误先停止新输入并请求释放，目标确认后才结束动作；反复暂停和读取错误不能刷新首次释放时间。目标未确认松开或 UI 卡死时，由独立宿主回收本批 Job。截图失败回执保留已发生的按下和松开事实，暂停状态先于回执发布，避免客户端读到旧状态。

正常关闭先确认松键，再请求原窗口退出，最多一秒后强制回收；不为配置写盘延长期限。宿主被终止时 Job 关闭回收目标；取图进程同样受 Job 约束。`termination.json` 保留退出原因、未完成动作的消费事实和取图进程 PID。

## 构建与验证

需要 Windows、本机已有的 .NET Framework 4 编译器及已核对的原 `Emuera.exe` 复制组件。仓库不提供原运行时。

```powershell
python plugins/emuera/build.py --output '<E 盘自写工程目录>/GameQaEmuera.exe'
$env:TMP = '<E 盘测试临时目录>'
$env:TEMP = $env:TMP
$env:GAMEQAKIT_TEST_EMUERA = '<包含已核对 Emuera.exe 的组件目录>'
$env:GAMEQAKIT_TEST_EVIDENCE = '<全新证据目录>'
python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_emuera_bridge -v
```

测试构造自写 CSV／ERB，不运行商业剧情、不携带原存档。缺少 Windows 或显式组件目录时真实测试明确跳过，不计为通过；组件摘要错误时拒绝启动。正常存读档夹具通过 Enter 触发原 ERB 逻辑，在第 1 槽保存 `123`、改为 `999`、同进程和新会话读取为 `123`。`storage-ledger.json` 记录新建文件、摘要与还原方式，自写存档证据单独保留。

宿主只支持这一实验布局：原 `Emuera.exe` 与 `GameQaEmuera.exe` 同目录，自写工程含 `csv/GAMEBASE.csv`、`erb/bridge.erb`，会话目录必须全新。文件存在检查不认证脚本来源，固定 EXE 摘要也不认证任意工程。

```powershell
python plugins/emuera/host.py --runtime '<自写工程>/Emuera.exe' --launcher '<自写工程>/GameQaEmuera.exe' --directory '<全新会话绝对路径>' --session '<新会话编号>'
```

原入口中的 `Application.ExecutablePath` 与 `ExeName` 会指向启动器；脚本、配置和保存仍基于同一自写工程目录。默认配置采用目录保存模式，本夹具实际生成程序旁的 `save01.sav`；未把这个文件名推断为所有版本或配置的通用规则。子进程 APPDATA／LOCALAPPDATA／TMP／TEMP 指向运行副本内专用目录，生成的配置、存档与运行副本只用于本批验证。纯自写工程恢复方法是先关闭本批 Job、再重新构造新批次，不覆盖旧证据。

## 使用边界与回退

仅验证固定构建和自写场景。商业游戏、数字／文本菜单、其他版本、通用受管入口、长期稳定性与真人并发未验；产品识别仍为 `emuera_candidate`、`adapter=null`、`control_verified=false`。

独立桌面只隔离桌面输入，不隔离任意文件、注册表或网络。路径定向仅针对已核查的实验工程，不能把本桥直接放入原游戏当作安全启动器。

仓库仅包含自有桥、构建脚本和夹具，不分发反编译输出、原运行时、商业资源或原存档。来源及许可限制见 [SOURCES.md](SOURCES.md)。移除本目录和对应测试即可撤回实验桥；共用协议、其他引擎桥和现有受管入口无接口变化。
