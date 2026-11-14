# 连接 AI 客户端

通过 MCP，AI 客户端可以查看游戏画面、管理盯屏记录，并在已接入的游戏中执行后台操作。先从 [README](../README.md)了解用途；后台操作的条件见[游戏接入说明](GAME-ADMISSION.md)。

## 使用 Windows 便携包

在客户端的 MCP 设置中添加以下配置，将路径改成实际解压位置：

```json
{
  "mcpServers": {
    "gameqa": {
      "command": "E:/Apps/GameQA/GameQA-MCP.exe"
    }
  }
}
```

客户端会自动启动 `GameQA-MCP.exe`，无需手动打开。`GameQA.exe` 是图形界面；移动安装位置时保留整个解压目录，并同步修改 MCP 配置中的路径。不同客户端的设置位置和配置结构可能不同，例如 VS Code 使用 `servers` 代替 `mcpServers`。

如需固定使用某个已有游戏配置，可在服务条目下增加 `"env": {"GAMEQAKIT_PROFILE": "your-profile"}`，将 `your-profile` 改为实际配置名。

## 源码方式

已安装项目依赖时，可以执行 `python -m gameqakit.mcp_server`。客户端配置示例：

```json
{
  "mcpServers": {
    "gameqa": {
      "command": "python",
      "args": ["-m", "gameqakit.mcp_server"],
      "env": {"PYTHONPATH": "E:/Projects/game-qa-kit"}
    }
  }
}
```

请替换仓库路径；需要指定游戏配置时，再增加前述 `GAMEQAKIT_PROFILE`。使用虚拟环境时，`command` 应填写其中 Python 的路径。

## 连接后先做什么

先在 GUI 新建或选择游戏配置，再让 AI 调用 `profile`、`processes` 查看目标。选择进程时会保存程序路径，PID 仅用于当前会话；游戏退出后需要重新选择。最小化窗口的尺寸可能无法读取，存档目录也不会自动推断。

可直接描述任务，例如：“开始盯屏，只记录画面和疑点，不操作游戏”，或“检查这个游戏能否后台操作，先告诉我缺少什么”。

更新程序后重新连接 MCP，以取得最新工具列表。工具及参数以运行中的 `tools/list` 为准。

## 游戏接入与受管启动

界面「AI 后台接入」与 MCP 的检查、准备、启动、连接和结束共用会话规则，具体步骤见[游戏接入说明](GAME-ADMISSION.md)。初次就绪保持输入暂停，不自动绑定或操作游戏。

可直接启动的目标和组件要求见[引擎支持范围](ENGINE-COVERAGE.md)。便携包的功能取决于发布时使用的源码提交，主分支的新功能需要重新打包后才会出现在下载包中。

启动游戏的 GUI、CLI 或 MCP 连接需要保持运行。其他客户端可以明确连接、观察、暂停并申请唯一动作权，结束游戏仍需回到创建它的入口。后台绑定后，旧桌面输入、存档恢复等旁路会被阻断；解绑或失联也不会自动回退原配置。人工盯屏会话与后台游戏会话分别管理。

## 推荐流程

1. `profile` 核对目标，`win` 查看窗口。
2. `watch_start` 启动后台观察。不输入、不主动抢焦点；重复启动返回当前 profile 的现有会话。
3. `watch_status` 查看 `running`、采集/留图/分析/疑点/分析丢帧/队列积压、最近错误和降级信息。
4. `watch_events` 分页读取疑点，再按返回的原图路径复核。不要对每页普通对话另行截图。
5. `watch_stop` 请求停止；`watch_status` 返回 `running=false` 才表示采集与分析已停止。
6. `watch_report` 汇总人工标记、自动疑点和普通留图时间线。

原有 `capture`、`mark`、存档工具和输入工具继续可用。

## 盯屏工具参数

| 工具 | 参数与默认值 |
|---|---|
| `watch_start` | `interval=2.0` 秒，必须有限且大于零；`threshold=4`，范围 0–64；可选 `region=[x,y,w,h]` 或 profile 命名区域；`analysis_mode="rules"` |
| `watch_stop` | 无参数；停止请求不等待分析排空 |
| `watch_status` | 无参数；不额外截图 |
| `watch_events` | `limit=20`（1–100），`offset=0`（非负）；可选 `dir` 查询指定会话 |
| `watch_report` | 可选 `dir` 查询指定会话 |

`analysis_mode`：

- `rules`：只对窗口标题做报错/文字规则筛选，不识别自绘正文。
- `ocr`：窗口标题加本地 OCR。需要可选 `rapidocr_onnxruntime` 依赖和对应模型，默认安装不包含这些组件。加载失败时 `analysis_status=degraded`，`analysis_warning` 说明原因，留图和标题筛选继续。
- `off`：仅普通画面留图，不启动分析。

疑点返回原图路径、窗口、时间、区域、OCR/原始文字及分类依据。`verdict="待复核"`；
不能把候选当确认缺陷，也不能用空事件页、dHash 或 OCR 降级时的结果证明没有缺陷。
繁体中文同形字不是日文证据。

## 会话与安全边界

- GUI、CLI 和 MCP 对同一 `state_dir` 互斥采集；共享状态通过 profile 的数据目录保存。
- 关闭 MCP 连接不会自动停止独立采集进程。需要显式停止，或从 GUI 打开同一 profile 后停止。
- GUI 关闭时只请求停止自己新建的会话；接入别处已经运行的会话不会取得所有权。
- 不同 profile 若指向相同游戏但不同 `state_dir`，不在同一把锁内，请勿重复启动。
- 图像分析队列至多四帧；积压时丢弃最旧待分析帧，`dropped` 可见。采集和普通留图不等于逐帧完整录像。
- 工作进程退出时锁自动释放；上次未正常结束的运行状态会显示 `interrupted`，不继续假报运行。
- 新配置的程序路径是严格约束，标题匹配不能跳到其他同名 `Game.exe`。同一程序多实例而没有有效 PID 时明确报错，不猜目标。
- 当前 GUI 会话选定的 PID 可由同 profile 的 MCP 复用；游戏退出后需要重新选进程，或重新加载配置按程序路径定位。重启若存在多个实例仍须重新选择。
- 截图、标记、点击/按键均使用相同目标约束。带程序路径的新配置执行 `kill_game` 时只针对解析出的目标 PID；旧配置的原有行为保留。
- `click` / `key` 仍使用系统前台输入。禁止把后台截图成功当成后台操控已就绪。
- `click`、`key`、`load`、`kill_game` 保持原有 `confirm=true` 要求。确认字段不是替代用户授权的手段。
- 恢复存档前先保留游戏内进度与备份；运行中的游戏通常不会自动重读磁盘存档。

## 引擎检查与统一后台控制（实验）

新控制工具只连接已接入的引擎桥，缺少插件或能力时明确报错，不退回桌面输入。接入与实证边界见 [后台控制实验](BACKGROUND-CONTROL.md)。

| 工具 | 必要参数与作用 |
|---|---|
| `engine_inspect` | `path`：只读目录证据，识别不代表控制可用 |
| `control_status` | 可选 `directory`（默认当前 profile 的 `state/control`）：核对路径/PID、会话、心跳、帧、场景、held 和实际能力 |
| `control_action` | 上述参数、`action`、`confirm=true`；可选 `command_id`、`frames`／`duration_ms`（按协议二选一）、`timeout`、方向/按键/坐标 |
| `control_result` | 上述绑定参数及 `command_id`；只查询，不重发 |
| `control_pause` | 绑定参数；优先暂停请求，不要求危险操作确认 |
| `control_resume` | 绑定参数、`confirm=true`；明确恢复，不复活旧队列 |

未使用 `game_bind` 时，以上工具通过 `GAMEQAKIT_PROFILE` 使用当前配置，不能在每次调用里传另一个 profile；`directory` 是插件会话目录，绑定的程序路径必须与当前配置一致。使用 `game_bind` 后目标来自显式绑定，`directory` 若提供必须与该绑定一致，不再回退 profile。

`control_action` / `control_resume` 可设置 `include_image=true`，回执后附本次截图；`control_result` 同样支持返回图片。有有效新回执时，不再例行追加截图与结果查询。

```json
{"directory":"E:/QA/bridge","action":"move","direction":"right","frames":1,"command_id":"walk-001","confirm":true,"include_image":true}
```

动作统一命名，按 `capabilities` 限制。当前 RGSS3 只接受单帧输入；MV/MZ 和实验 Unity 桥额外提供按帧输入和画布坐标点击。Unity 的 Mono／IL2CPP、旧 Input／新 Input System／Rewired 组合及具体实测范围见[插件接入与限制](../plugins/unity/README.md)，须按目标构建，不能从一个组合推断其他组合可用。暂停验收须同时有 connected=true、paused=true、held=[]，读失败时返回的保守暂停不是游戏确认。桥接目录属于对应目标，不可与另一个进程混用。独立桌面游戏可能不在默认 `win` 清单中，不能据此要求重启或改走前台。

固定构建的[原生输入桥](../plugins/native_input/README.md)已接入相同 MCP 工具。先启动受限宿主，再绑定本次副本路径／PID 和 `bridge/`；`control_status` 显式声明 `protocol=2`、`timing_mode=duration_ms`、`pause_scope=input`、`simulation_pause=false`。使用 `duration_ms=1..1000` 和 `observation_seq`，拒绝 `frames`，不能从回执推算游戏帧。仅支持方向移动、Z 确认／X 取消与等待；暂停不冻结游戏。固定标题菜单路线已逐图核对，自有窗口的实体键鼠并发已验证；正式游戏的实体并发、鼠标和玩法仍未验。

可选的 PS1 方块键使用 `control_action(action="press", button="square", frames=12)`；先核对 `control_status.buttons` 数组包含 `square`，未声明的桥会在排队前拒绝。`ok/cancel/shift` 等既有按钮含义保持各引擎原映射，不能将 `shift` 当成 PS1 菜单键。

超时先按原 `command_id` 查结果。`historical=true` 的旧回执不能指导当前操作，不换编号盲重发。暂停返回请求状态；再次查询，确认 `paused=true` 且 `held=[]` 后才能报告输入暂停完成；这不代表同进程真人手玩接管可用。恢复 AI 必须明确授权。

直接准备和启动仅支持已登记且版本匹配的游戏。其他游戏的兼容性和存档位置需要分别核查，人工盯屏的启停不会恢复 AI 输入。

## 命令行

`--profile` 是全局参数，放在子命令之前：

```bash
python -m gameqakit.cli --profile example watch --analysis rules
python -m gameqakit.cli --profile example mark -m "疑似日文"
python -m gameqakit.cli --profile example report
python -m gameqakit.cli --profile example capture -o shot.png
```

`watch` 可选 `--interval`、`--threshold`、`--region x,y,w,h`、`--downscale`、`--out`。
指定 `--out` 时需使用没有既有会话记录的新目录，避免覆盖证据。
