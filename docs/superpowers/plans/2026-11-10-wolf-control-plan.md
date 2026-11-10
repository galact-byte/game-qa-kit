# Wolf 正式控制桥实现计划

执行状态：Task 1–4 已完成。117 项 Python、16 项 JavaScript 通过；固定哈希官方范例完成安全生命周期、真实 MCP 与地图操作验收，19 个正式批次的成功／失败证据分别保留，测试进程及 runtime 已回收。本地入口：`<Wolf验证目录>/正式桥验收.md`、`formal-delivery-audit.json`；公开范围见 `plugins/wolf/README.md`。本轮实现阶段未提交；现已获用户明确授权提交推送。

> 执行方式：主代理连续执行；已于本轮获批，常规实现步骤不再请求确认。

**目标：** 为已测 Windows x86 官方范例接入既有协议 v1／INI／MCP，按完整循环计帧，证明安全生命周期与真实菜单、地图操作。

**架构：** 独立 `plugins/wolf/` 原生 DLL、安全核心、INI 编解码、x86 加载器与准备／构建入口。固定 EXE 完整 SHA-256；只在本次创建的独立桌面副本中加载。主机与其他插件协议不改。

**依据：** `docs/superpowers/specs/2026-11-10-wolf-control-design.md`；`skills/game-control-adapter/SKILL.md`。

## 全局约束

- 原游戏只读；运行、构建、日志均在 `<Wolf验证目录>/`。准备入口拒绝目录重叠、覆盖和未支持构建；不带原保存目录。
- 复用既有协议语义和已装 MSVC，插件间不得相互引用。帧入口使用已实证的主循环专用调用点，输入使用 DirectInput，重复输入轮询不计帧。
- 不放宽客户端等待、租约、心跳与截图期限；失败先停手、释放输入，不抢前台，不重发未知结果。
- 实现阶段保留先前文档改动，不自动提交；用户现已明确授权提交推送。本地实施记录：`<Wolf验证目录>/implementation-progress.md`。

### Task 1：先验证准备边界与安全核心

**文件：** `tests/test_wolf_control.py`、`tests/wolf/ControlCoreTests.cpp`、`plugins/wolf/{prepare.py,build.py,ControlCore.hpp,Wire.hpp}`。

**接口：** `prepare(source, workspace)` 返回副本路径和版本记录；核心接收与现有桥一致的 Command，完整循环 tick，DirectInput 读取独立报告消费。

1. 写失败测试：未知 EXE 不创建目录，工作区不可重叠／覆盖、不复制保存；核心暂停、恢复、精确长按、重复轮询、未消费拒绝、旧会话／暂停代次、重复命令、租约、停帧与截图失败。
2. `python -m unittest discover -s tests -p test_wolf_control.py -v`，预期：缺失功能导致失败；保留红日志。
3. 最小实现准备入口、安全核心和构建入口，再运行同命令。
   预期：全部通过；不得通过改变期望或时限掩盖失败。

### Task 2：加载正式桥并完成真实输入与截图

**文件：** `plugins/wolf/{Bridge.cpp,Injector.cpp}`、`tests/wolf_smoke.py`。

**接口：** DLL 读既有 connection/lease/pause/request INI，发布 status/results JSON 和 PNG；加载器只接受会话绑定的自有独立桌面进程。

1. 写真实烟测：挂起创建副本进程、绑定会话、加载 DLL、恢复；初始安全暂停、显式恢复、单步与 12 帧长按的实际帧增量、标题切项、新 PNG、前台不变。
2. 先执行到正式 DLL 缺失导致失败并保留日志，然后实现并构建。
3. `python plugins/wolf/build.py --output <Wolf构建目录>/GameQaWolf.dll`。
   预期：x86 DLL 和加载器编译成功；不下载依赖。
4. `python tests/wolf_smoke.py --source <已核查范例目录> --workspace <E盘新工作区> --dll <构建DLL> --injector <构建加载器EXE>`；加载器用 `build.py --injector --output <输出EXE>` 单独构建。
   预期：正式协议动作取得有效新图、真实标题选项变化，暂停期间循环不增长。

### Task 3：验收安全生命周期与 MCP

**文件：** `tests/wolf_smoke.py`、必要的核心／准备入口回归。

1. 补真实安全分支：长按中暂停、租约到期、旧会话／暂停代次、重复命令、截图失败、恢复后再执行、断开与退出回收。
2. 使用既有 MCP stdio 调用，读取真实 PNG／路径与哈希；不修改统一接口。
3. 通过正常输入进入地图，核对位移、松键、菜单开／切／关；每个结论保留画面。
   预期：失败原件独立保留；安全停止后帧稳定、held 为空；恢复不追认失败。

### Task 4：回归、审查与归档交付

**文件：** `plugins/wolf/README.md`、路线／开发／后台控制文档、`CHANGES.md`、本地交接记录。

1. 先测再修改 `tests/test_engines.py` 与 `gameqakit/engines.py`，只声明候选插件，不宣称任意 Wolf 构建支持。
2. 运行 `python -m unittest discover -s tests -v` 与项目既有 JavaScript 桥测试；预期全部通过。
3. 独立一遍自审版本拒绝、ABI、文件发布、暂停竞态、加载失败回收；没有可调用的独立子代理，明确记录自审边界。
4. 核对原始目录哈希、证据哈希、测试进程退出；归档后回收可再生 runtime。同步实际已测范围与未验证项。
5. `git diff --check`，预期无空白错误；给出实际通过数和限制。

## 审查重点

固定调用点补丁须验证原 CALL 目标；主线程挂起期间安装；DirectInput 仅替换键盘目标；心跳线程不能推进游戏轮次。暂停／租约须在每轮输入前复核，截图发布前复核，加载失败禁止继续游戏。原目录与已有测试保存不得覆盖。
