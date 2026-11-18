# Godot 4.5.1 自写样本接入

本目录提供使用 v1 帧协议的 `GameQaBridge.gd`。通用「AI 后台接入」和 MCP `game_*` 入口仅接受本仓库的自写样本和已登记的 Godot 4.5.1 运行时，不支持商业 PCK 或其他版本。

## 启动自写样本

1. 在源码根目录运行 `python -c "from pathlib import Path; from tests.test_godot_bridge import fixture_pack; p = Path('godot-sample'); p.mkdir(); fixture_pack(p / 'fixture.pck')"`。选择新目录，避免覆盖已有样本。生成的目录只应包含 `fixture.pck`。
2. 在后台组件目录准备本机已有的 `godot-runtime.exe`，SHA-256 必须为 `2a2ffce588f342dc40d4d913da7682bfda24fb986c543b1bf88afe69fe1e7aab`。只识别同一构建，不下载或分发运行时。
3. 在「AI 后台接入」选择上述游戏目录、组件目录和 QA 数据目录，检查并确认准备后启动。启动时不会自动输入；须先取得有效首图，随后由 MCP 显式连接和恢复。动作后取图失败时停止输入，并回收本次启动的游戏进程。

## 已验证范围

真实 Godot 进程在独立 Win32 Desktop／Job 中运行。输入经 `Input.parse_input_event` 交给引擎，画面从视口读取，不使用系统键鼠。桥按 SceneTree 的实际处理帧计数；暂停只停止合成输入，场景逻辑和预览继续运行。

`tests/test_godot_managed.py` 的真实 MCP 用例覆盖暂停首图、右移 40 像素并读到松键、退出及回收、黑屏保留已消费回执并拒绝后续操作，以及准备后篡改副本时拒绝启动。`tests/test_godot_bridge.py` 的 15 项真实运行测试覆盖：

- 初始暂停、场景首图与测试用户目录；正常方向输入后的场景位移与释放。
- 引擎确认键、正常 GUI 按钮点击及鼠标释放；输入暂停时预览仍更新。
- 进行中动作被暂停或租约失效中断，场景实际读到松键。
- 错误会话、无效参数及过期暂停代次拒绝；目标退出后拒绝继续输入。
- 黑图、带单个亮点的黑图拒绝；PNG 写入失败停止动作并撤销控制能力；同帧已消费输入的失败回执保留 `consumed=true`。

这些结果不证明商业游戏的保存隔离、自定义主循环兼容、存读档、玩法或长期稳定性，也不替代真人键鼠并发验收。桥不提供进程外写入守卫；仅重定向 AppData 或复制目录不构成通用数据沙箱。

## 验证方式

准备本机已有的匹配运行时，设置以下环境变量后运行：

```powershell
$env:GAMEQAKIT_TEST_GODOT = '<Godot 4.5.1 运行时 EXE 完整路径>'
$env:GAMEQAKIT_TEST_EVIDENCE = '<全新测试证据目录>'
python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_godot_managed tests.test_godot_bridge -v
```

测试使用自写 PCK 和场景，不加载商业游戏资源。未指定运行时时，实机用例明确跳过，不计为通过。设置 `TMP`／`TEMP` 可指定测试临时目录。证据目录保留状态、回执、PNG 与日志；运行时副本不随证据复制。

连接校验要求协议、会话、PID、EXE 路径和引擎标识一致。脚本通过显式 `--script` 入口运行，项目必须使用默认 `SceneTree`；正常启动项目声明的主场景，不通过脚本直接修改任务结果。截图失败后保持暂停，不回退到桌面输入。

## 当前阻碍

本机另外两个运行时分别报告 4.6.3、4.7.2，启动自写工程时都拒绝 `--main-pack`，报错包含 `disable_path_overrides`。这说明这两个固定构建不能沿用当前测试入口，不能据此断言所有 4.6／4.7 运行时都不兼容，也不能因 PCK 头中的版本号就宣称运行时兼容。

这两个构建的外部脚本接入、资源补丁方案及商业资源保存边界尚待确定。当前不修改原游戏、不更换运行时、不绕过其编译限制；变更接入架构后应重新设计并验收。
