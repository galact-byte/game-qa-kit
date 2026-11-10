# Wolf 实时控制隔离实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 消除 Wolf 高频控制文件 I/O 对游戏线程、安全检查和续租的直接阻塞，完成验证、提交推送与交接。

**Architecture:** Wolf 声明可选 `wolf-shm-v1` 传输，使用 Windows 分页文件支持的命名共享内存；每个槽位以零等待命名互斥和提交序号发布有界消息，游戏线程读取不等待。主机与桥保留协议 v1 的会话、暂停代次、唯一编号与期限。证据写盘独立于控制检查，只有截图和回执成功发布后才返回成功。

**Tech Stack:** Windows x86 C++17、Python ctypes、现有 unittest／MSVC 与 JavaScript 回归；无新增依赖。

**Spec:** 本计划中的设计约束；本机会话修复建议作为问题背景。用户明确要求在当前会话修复、提交、推送后再交接，直接执行，不为普通实施重复确认。

## Global Constraints

- 1.5 秒租约、1 秒停帧、3 秒截图期限和 15 秒启动门槛不变。
- 原游戏、原存档只读；运行副本与证据在项目外验证目录；不改安全软件设置。
- 所有控制消息仍校验会话／目标，旧会话暂停无效；超时查询原编号，不自动重发。
- 实时传输显式协商，不静默降级。其他引擎文件传输行为保持，相关回归验证。
- 名称绑定 PID 与随机会话；共享区只由桥创建，主机只能打开；拒绝已有同名对象。消息长度有界、并发发布不能读到半写数据。
- 当前工作区已含前期 Wolf 日文启动及截图修复；保留并审查既有改动，提交只包含该交付范围，不覆盖用户文件。

## Review Focus

- 发布者在写消息中途退出：读取必须有界、旧租约到期停止。
- 旧会话／旧暂停代次与未知传输：拒绝，不能切回文件继续输入。
- 回执／截图写盘阻塞：控制检查继续，截图失败和原期限仍有效。
- 主机截图读取阻塞：续租与读取分离，但受调用存活和有限截止期约束。
- 客户端／游戏退出与重复命令：资源回收，未知结果不重发，历史结果可查询。

### Task 1：固定阻塞回归及共享区传输边界

**Files:** tests/wolf/BridgeThreadTests.cpp、tests/test_wolf_control.py、tests/test_wolf_ipc.py、plugins/wolf/Realtime.hpp、gameqakit/wolf_ipc.py。

**Interfaces:** Realtime 提供 slots(request, lease, pause, status, result) 的有界快照读写；Python WolfMailbox 只打开已存在映射，read/write 对相同槽使用一致布局。

- [x] 加入真实生产输入路径的文件读取阻塞测试，运行观察红。
- [x] 加入跨 Python／C++ 通信、半写／超大／会话隔离测试，运行观察红。
- [x] 实现最小共享区通道，跑通上述测试。

### Task 2：隔离桥安全检查、证据发布和主机续租

**Files:** plugins/wolf/Bridge.cpp、gameqakit/control.py、tests/test_control.py、tests/wolf/BridgeThreadTests.cpp。

- [x] 回归覆盖回执写盘卡住期间输入与到期检查、主机证据读取卡住期间续租。
- [x] guard 只消费实时快照；独立控制循环检查期限并发布状态；文件工作在锁外。
- [x] 客户端显式协商 Wolf 通道；命令历史证据保留，续租线程仅存活于有限动作调用期。
- [x] 验证暂停／旧会话／断连／失败／未知结果及其他引擎兼容。

### Task 3：构建、真实验证与交付

**Files:** plugins/wolf/README.md、CHANGES.md、docs/ROADMAP.md、本地交接。

- [x] 构建正式 DLL，运行完整 Python 和 JavaScript 回归，审查 diff。
- [x] 在已授权隔离副本验证启动、正常输入、安全与商业玩法，逐图记录边界；回收并核原目录。
- [x] 更新真实验证记录，核 .gitignore 和远端。
- 提交／推送结果与新会话开场说明记录在项目外 `<Wolf验证目录>/session-review/新会话交接.md`，避免提交自引用哈希。

验证结果：134 项 Python、16 项 JavaScript 全绿，无跳过；商业批次 13 完成 147 条正常动作及 147 次恢复、教室位移／菜单往返、10 次 MCP 和安全生命周期。原目录 852 文件零差异，运行副本与三个记录进程退出。证据入口 `<Wolf验证目录>/session-review/修复与验收.md`。

## 执行记录

- 初始工作区 main 已含前期 Wolf 未提交变更。按照用户“在这里修”的明确要求继续本工作区，不另起会话或复制丢失改动的工作树。
- 共享内存替代命名管道：避开写入守卫中的文件路径拦截，使用 Windows 原生匿名分页支持；不引入网络端口或新依赖。
