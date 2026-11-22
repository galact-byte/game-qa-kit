# 开发与验证

## 仓库职责

本页面向维护者，介绍模块职责、数据文件和验证方法。软件使用见 [README](../README.md)，AI 客户端配置见 [MCP 使用说明](MCP.md)，构建与发布见 [便携包文档](PACKAGING.md)。开发方向放在 [路线图](ROADMAP.md)，历史改动放在根目录 `CHANGES.md`。

巡检方法维护在 `skills/localization-playtest/SKILL.md`，插件开发步骤维护在 `skills/game-control-adapter/SKILL.md`。使用 Pi 时可用 `--skill <项目中的SKILL.md路径>` 显式加载，或将全局入口指向仓库源。避免同时加载两份同名正文；不能假定项目版本会自动覆盖全局版本。

控制插件和独立桌面的实现与验证见 [后台控制实验](BACKGROUND-CONTROL.md)。观察链继续独立工作，不隐式开启 AI 输入。

## 模块边界

| 模块 | 职责 |
|---|---|
| `profile.py` | 游戏配置及数据目录；旧配置无需迁移 |
| `win32.py` | 找窗口、现有 PrintWindow 截图、窗口标题读取 |
| `watch.py` | 采集、dHash 普通留图、人工标记、疑点分页和复核报告 |
| `analysis.py` | 容量四帧的异步队列、规则/可选 OCR、事件证据 |
| `sessions.py` | 文件锁、原子 JSON、独立采集进程的共享生命周期 |
| `gui.py` | GUI 与分页疑点窗口；通过公共会话入口控制采集 |
| `mcp_server.py` | stdio JSON-RPC 接口；stdout 仅协议、错误日志走 stderr |
| `engines.py` / `control.py` | 只读引擎线索、统一命令与目标/会话/回执验证 |
| `admission.py` / `admission_runtime.py` | 只读检查、准备请求校验及受管接入的公共入口 |
| `game_session.py` / `game_worker.py` | 受管游戏会话、观察连接及创建者生命周期 |
| `admission_gui.py` | 游戏检查、后台启动和已有会话连接界面 |
| `application.py` | 源码／便携包路径及冻结子进程入口分派 |
| `desktop.py` / `plugins/` | 独立桌面与进程树回收、各引擎独立输入桥 |
| `inputs.py` / `saves.py` | 保留既有输入机制/护栏和存档逻辑；支持显式窗口与按 PID 结束选定进程 |

Profile 新增可选 `exe_path`（持久化）和 `target_pid`（仅当前对象/会话，不写配置）。GUI 从有窗口的
进程自动填充名称、路径和可读尺寸，不推断存档目录；最小化时尺寸为未知。`profile_target` 让同 profile 的
MCP 复用正在运行的 GUI 会话 PID。路径约束不能被标题兜底绕过；同路径多 PID 而未选择时拒绝猜测。

普通帧去重与疑点分析分别决策：dHash 接近不能阻止发生变化的像素/标题进入分析。
Pillow 的保存会修改图像对象内部状态，跨线程必须使用独立副本。

## 数据与生命周期

会话仍位于 `<data_dir>/watch/<微秒时间戳>/`，原有 `index.jsonl`、`marks.jsonl` 与旧报告兼容。
新增：

- `status.json`：统计、运行状态、降级及最近错误。
- `events/<序号_类别>/raw.png`、`metadata.json`：原始疑点与依据。疑点原图不受普通帧缩小影响。
- `events.jsonl`：首次归档索引；事件详情与分页查询以各事件的 `metadata.json` 为准。
- `worker.json` / `worker.log`：独立会话配置与错误日志。
- `stop.request`：跨入口停止请求。

Windows 下短期读句柄可能阻止 `os.replace`：原子 JSON 写入仅对 `PermissionError` 最多重试 0.25 秒，
持续失败仍向上传递，保留旧状态并删除临时文件。测试清理跨 MCP 创建的工作进程时等待实际退出，不能把
`running=false` 当成 stderr 日志句柄已经关闭。

`state/watch.lock` 是采集生命周期锁；`watch_control.lock` 串行化启停操作。
进程退出后操作系统自动释放锁，不删除锁文件来判断存活。
停止先终止采集，再排空队列，最后发布停止状态；OCR 很慢时应保持“停止中”，不伪报结束。

## 验证

Windows 环境，运行完整离线/子进程/Qt 测试：

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
```

自动回归只使用临时数据、合成图片和测试子进程，不操作真实键鼠或原游戏存档。
Qt 交互测试使用离屏平台；原生中文渲染另做 Windows 平台检查，不能把离屏字体占位框当作实机效果。

```bash
node --test tests/rpgmaker_bridge.test.js
python -m compileall -q gameqakit tests
```

控制测试包括精确目标/会话、命令去重、截图校验、旧暂停/历史回执，并以 README 的文件路径启动 MCP 检查新工具；包导入通过不能代替注册入口验证。`test_unity_control.py` 会使用本地 C# 编译器编译并执行 Unity 控制核心的安全测试，并直接编译生产 `Tick`／`Lease` 方法验证真实文件交错：恢复请求发布期间的租约更新、新暂停优先以及过期／其他会话／旧代次拒绝；缺编译器时明确跳过，不安装依赖。图形实证单独运行 `tests/unity_smoke.py`，其空白场景创建、正常输入与写入隔离说明见 [Unity 插件](../plugins/unity/README.md)。`test_hsp_control.py` 使用已安装 MSVC x86 编译执行 HSP 安全核心，并验证字节码追加注册保持原代码及已有插件表、不重复注册和拒绝损坏头部；缺工具链明确跳过。真实 HSP 图形验收使用 `tests/hsp_smoke.py`，分别覆盖 await、`--wait-loop` 和 `--desktop-mismatch`，构建、运行时与已测边界见 [HSP 插件](../plugins/hsp/README.md)。红日志只有因目标缺陷失败才可计入复现，截图失败等环境步骤须独立保留。JavaScript 测试包括场景初始化、过渡、渲染失败和失联释放。原生桌面测试覆盖空桌面、真实枚举错误、进程树回收和客户区完整截图：用精确上下色块检查标题栏与底部裁切，避开 DPI 缩放边缘插值。PrintWindow 使用 `PW_CLIENTONLY`，不把整窗画进客户区画布。真实 SDK 实验另按后台控制文档显式运行。

显式运行原生遮挡截图检查：

```bash
python tests/native_smoke.py --out <新的项目外证据目录>
```

会短暂显示两个不激活的测试窗口：绿色目标被红色窗口遮挡。检查实际 PrintWindow 原图仍为绿色、
标题错误被归档、重复启动复用会话、停止与报告可用。记录前后台窗口句柄，结果写入 `result.json`。
不把该合成窗口通过推广成所有游戏都兼容。

## DuckStation／PS1

正式桥入口见 [插件说明](../plugins/duckstation/README.md)，原始 SDL [隔离探针](../tests/duckstation/README.md) 继续保留。正式桥通过原生执行帧计时、模拟器数字手柄回读和原生截图接入既有 v1／INI／MCP；只允许完整 EXE 哈希匹配的 Windows x64 构建。其他构建必须重新取证验证，不能仅改偏移或关闭校验。

`tests/test_duckstation_control.py` 在常规 unittest 中编译执行 C++ 安全核心，并验证版本拒绝、防覆盖和新配置不带原保存。`tests/duckstation_smoke.py --safety --mcp` 覆盖长按中优先暂停、250 毫秒租约过期、旧会话／暂停代次、重复命令、截图保存失败及真实 MCP 文件路径启动。MCP 超过 1 MiB 的图片按路径交付，验收核对文件和哈希，不强求内联或提高上限。

SDL 更新回调在模拟器暂停时仍运行，需用原生单步和帧号确认执行；固定等待不能代替就绪检测。真实测试使用独立空白记忆卡，原配置和镜像只读。截图失败时按现有限时停止，不能放宽阈值后追认通过。

已验证动作与已知限制以[插件说明](../plugins/duckstation/README.md)为准；音频、全流程、换盘、实体手柄干扰和其他构建尚未验证。

## Wolf RPG

构建、组件与已测范围见 [Wolf 插件说明](../plugins/wolf/README.md)。官方范例与商业构建使用各自登记的完整 EXE 哈希；未知构建拒绝。官方范例已接入通用受管入口，商业构建仍使用独立日文启动流程，不能混用准备与启动方式。

开发验证使用 `tests/test_wolf_control.py`、`tests/test_wolf_startup.py` 与 `tests/test_wolf_admission.py`，分别检查控制核心和副本边界、有界初始化及受管生命周期。真实游戏验证使用 `tests/wolf_smoke.py`，参数组合与组件名称以插件文档为准；`--mcp-steps <JSON>` 通过真实 MCP 执行正常输入。

回执按完整游戏循环计帧，不能用输入轮询次数代替。普通动作释放后另有两轮观察；暂停需连接有效、保持键为空且 `at_frame_boundary=true`，再核对帧稳定。移动要等当前格走完，菜单退出要等动画完成，最终以操作后画面确认行为。

官方范例的既有桥已验证移动、菜单及战斗；其受管入口只验证首图、标题切项、当前预览和生命周期。固定商业构建另有移动、菜单、第 1 槽保存与跨进程读档记录，尚未迁入受管入口。全流程、商业战斗和长期稳定性未验。失败记录、守卫和原有期限保留；独立桌面与写入守卫不等于完整数据沙箱。

## 迭代原则

先写目标行为的失败测试，再实现与回归。新引擎先做最小样本实证，不先堆插件框架。
安装、下载、修改游戏/存档、替换全局入口和提交推送分别按用户授权处理，不因路线文件存在就自动执行。

临时分支持续收口：有用且验证通过的改动合入 `main`；确认原分支提交已可从主线追溯、必要证据已留存后，及时删除不再使用的本地及远端分支，包括后续新增的分支。分支被工作区使用时，保留原提交和未提交修改，以 detached HEAD 保留工作区后再删除分支；不得为清理分支重置用户工作树。
