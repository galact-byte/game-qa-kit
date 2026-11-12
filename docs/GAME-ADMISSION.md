# 游戏接入与受管会话

主程序保留人工盯屏，并增加“AI 后台接入”。这两条路径共用现有观察和证据工具。引擎识别、已验证控制通路、当前构建能否由新入口启动是三件不同的事；以检查结果和[引擎覆盖表](ENGINE-COVERAGE.md)为准。

## 人工盯屏

1. 正常打开游戏，在主程序新建配置并选择运行中的窗口。
2. 确认程序路径和 QA 数据目录；新配置自动避开已有名称。
3. 开始盯屏，自己玩游戏并标记疑点。

选择窗口不会加载插件、恢复窗口或向游戏发送输入。保存路径未知时保持未设置，不自动猜测。

## AI 后台接入

1. 在主程序打开“AI 后台接入”，选择原游戏目录、QA 数据目录及已有后台组件目录。
2. 点“只读检查”，阅读引擎证据、阻碍和本次操作摘要。检查不会启动游戏。
3. 条件成立后点“确认并启动”。程序在 QA 目录下创建新副本，原目录只读、不带原存档；初次就绪时暂停输入，并显示首图。
4. AI 通过 MCP 显式连接该会话，明确恢复后才执行动作。界面显示的“运行”指输入控制状态，不代表整个游戏仿真被暂停或恢复。
5. 从创建该会话的窗口点“结束会话”，或关闭创建者窗口。程序请求暂停松键，回收自有进程树并核对数据。

当前完整启动流程只覆盖已登记哈希的 SRPG Studio x86 构建，需已有 `native-host.exe` 与 `native-input.dll`。其余引擎会明确返回尚未迁入及恢复建议；原有适配器与控制工具仍保留，不能据识别成功宣称可启动任意游戏。

只回收数据核对通过且没有任何运行变化的可再生副本。出现存档、缓存或无法解释的变化时保留整个运行副本及清单，供复核。退出、取图或桥失败均不回退到前台键鼠。独立桌面或复制目录都不是文件系统沙箱。

## MCP 创建和连接

`game_inspect(path, artifacts, destination)` 只读检查；条件成立且指定全新 `destination` 时返回 `plan` 和摘要指纹。`artifacts` 是已有组件目录，不是安装指令。

| 工具 | 参数与行为 |
|---|---|
| `game_prepare` | `plan`、`request_id`、`confirm=true`；异步准备，创建者须保持连接 |
| `game_start` | `directory`、`request_id`、`fingerprint`、`confirm=true`；只启动本 MCP 创建且已准备的会话，初始暂停 |
| `game_status` | 可选 `directory`；查受管会话或本连接的绑定状态，包含是否可停止 |
| `game_bind` | 桥 `directory`、当前 `session_id`、`confirm=true`；校验精确 PID、程序路径、心跳与会话，不恢复输入 |
| `game_unbind` | 无参数；确认暂停松键或目标退出后解绑，不回退旧 profile |
| `game_stop` | 会话 `directory`、`request_id`、`confirm=true`；仅创建者可停止，异步等待最终状态 |

创建流程为检查 → 审阅摘要 → prepare → 查询 prepared → start → 查询 ready_paused。准备与启动使用不同请求编号；请求编号在同一 QA 父目录内须唯一。相同编号与内容只查询原结果，不能借重复请求重启或重新取得所有权。超时先查询原请求／命令，不能盲目重发。

已有 GUI 或另一 MCP 创建的会话可显式 bind；不需要伪造受管记录。连接者可查询与暂停，只有取得动作锁的客户端可以恢复或发动作。连接不授予进程停止权；应回创建者窗口结束。创建者退出会回收其会话，不能靠另一客户端连接延长进程生命周期。

绑定后使用原 `control_status`、`control_resume`、`control_action`、`control_result`、`control_pause`。原生 v2 使用 `duration_ms`，v1 桥使用 `frames`，不能换算或混用。后台绑定、绑定失效和解绑后的空目标均阻止旧桌面输入、存档恢复和按名终止工具绕过控制桥；重新使用人工 profile 需另建明确的人工工作流连接。

## CLI

```bash
python -m gameqakit.cli game inspect "<原游戏目录>" --artifacts "<后台组件目录>" --destination "<QA目录>/<新会话名>"
python -m gameqakit.cli game start "<原游戏目录>" --artifacts "<后台组件目录>" --destination "<QA目录>/<新会话名>" --fingerprint "<检查返回的指纹>" --confirm
python -m gameqakit.cli game status "<QA目录>/<会话名>"
```

`start` 持续运行以保持创建者生命周期，Ctrl+C 请求收尾；它不会打印 PID 后脱离运行。`status` 不收养落盘 PID，也不因查询获得停止权。

## 记录与恢复

每次新建独立会话目录。`approved-plan.json` 保存已确认摘要，`session.json` 保存生命周期状态，`profile.json` 供现有观察工具加载，`bridge/` 保存控制与图片证据，`cleanup.json` 及前后清单保存退出核查。QA 父目录的 `.admission-requests/` 保存请求去重和进程生命周期锁。

重新启动程序只可查询历史记录或显式连接仍有效的桥；历史记录不是可以重新启动或停止任意 PID 的凭证。创建者／worker 意外退出会通过 Job Object 回收自有后代；未经正常核对的记录显示 `interrupted_unverified`，不追认已正常清理。

初版尚未提供重启后的目录偏好记忆、图形化历史会话浏览和其他后端的产品启动流程。真人并发输入体验集中留到最终验收，自动测试和菜单画面不能代替真人验收。
