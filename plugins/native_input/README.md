# 受限原生后台输入桥（实验）

本插件使用同一套 MCP 控制工具，在目标进程内消费软件键盘输入，不创建独立 Win32 Desktop。当前仅登记一个 SRPG Studio x86、`data.dts` 模式的构建，不是通用游戏加载器。

## 已验范围与限制

- 固定 EXE SHA-256：`8e123a4c07cfbc9143c266faf87bb66ef3600ab1254ee6d294fbaf685bb502a7`，样本档案为 [srpgstudio-x86-fixed.json](samples/srpgstudio-x86-fixed.json)。只读识别返回候选档案，始终保持 `control_verified=false`；不会启动或注入。
- 正式宿主／DLL 经真实 MCP 完成后台「标题 → 环境设置 → 返回标题」，逐图核对光标移动、设置界面与返回标题，正常 Z 确认／X 取消。`game-menu-03/` 的 7 张动作观察、输入消费和松键、前台采样、原目录／外部数据摘要与回收检查通过。
- 自有窗口完成安全故障注入与真实 MCP 检查，包括按住时暂停、控制端退出／租约到期、宿主被杀后的 Job 回收、未读松开、窗口线程阻塞、黑白帧和证据发布失败。
- 自有窗口实体键鼠并发已通过：`physical-20261112-222347-200801/` 完成 12 轮前台左键与后台右移重叠，参与者确认实际按住左键并移动、点击鼠标；独立确认记录绑定原结果摘要。**正式游戏实体并发仍未验**，自动冒烟和自有窗口结果均不能替代。
- 不支持鼠标动作、坐标点击、手柄、x64、其他哈希、非 `data.dts` 模式。存读档、玩法、全流程和长期运行尚未验证。最小化取图直接拒绝。
- 暂停只停止 AI 输入并清键，游戏逻辑和动画继续；不宣称模拟暂停或帧精确操作。原型旧批次与本插件验收分别保留。

## 准备副本与构建

要求已有 Windows、Python 3.12+、MSVC x86 和 EasyHook x86 静态库；脚本不下载、安装依赖，不安装服务／驱动。所有输出放项目外的任务目录。

```bash
python plugins/native_input/prepare.py <只读原游戏目录> <全新会话目录>
python plugins/native_input/build.py --mode core --output <构建目录>/core-tests.exe
python plugins/native_input/build.py --mode bridge --easyhook <已有EasyHook目录> --output <构建目录>/native-input.dll
python plugins/native_input/build.py --mode host --output <构建目录>/native-host.exe
```

`--easyhook` 目录须包含 `Public/easyhook.h` 与 `Build/x86/netfx4-Release/EasyHook32.lib`。MSVC 默认查找本机安装，可用 `--vcvars` 显式指定现有 `vcvars32.bat`。来源与许可证见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

准备入口在复制前拒绝未知哈希、非 x86、缺少数据标志／资源、未核查的后台配置、路径重叠、既有目标和符号链接／junction。只复制已核查资源，不带原 `Save`；副本新建空保存目录，仅修改副本 `game.ini` 的后台动画／音乐选项。生成准备记录、测试数据账本及原目录／外部文档保存目录摘要。

宿主调用形式为：

```text
native-host.exe <全新会话目录> <native-input.dll绝对路径> <该DLL的SHA-256>
```

宿主再次核验固定 EXE、x86、DLL 摘要和会话边界，挂起创建副本进程，加入关闭即回收的 Job，完成引导期写入保护、DLL 和输入隔离后才恢复主线程。启动失败不发布可用连接。不要使用 `--owned-image` 构建的测试宿主启动游戏；它只用于自有窗口回归。

## 统一 MCP

沿用 `control_status`、`control_resume`、`control_action`、`control_result`、`control_pause`，不增加另一套原生专用动作工具。profile 的精确路径／当前 PID 必须绑定本次副本；`directory` 指向会话的 `bridge/`。

状态声明 `protocol=2`、`timing_mode=duration_ms`、`pause_scope=input`、`simulation_pause=false`、`observation_seq` 和实际能力。当前动作是 `move`、`confirm`、`cancel`、`wait`，每次 1–1000ms；方向键／Z／X 对应普通游戏输入，不直接切场景或修改进度。先明确恢复，再发送：

```json
{"action":"move","direction":"down","duration_ms":120,"command_id":"menu-001","confirm":true,"include_image":true}
```

v2 拒绝 `frames`，v1 继续使用原 `frames` 并拒绝时间字段；不自动换算。回执包含单调起止时间、输入消费、释放消费、新观察序号、截图路径及 SHA-256。图像合格且状态发布后才发布成功回执。

`control_pause` 返回请求后，还须查到已连接、`paused=true` 且 `held=[]`。如果目标不再读取松开事件，兜底期限到达后失败回收，不能只靠软件保持键为空声称目标松开。结果不明时先用原编号查询，不能盲换编号重试。

租约仍为 1.5 秒、输入轮询停滞 1 秒、截图／宿主心跳 3 秒、启动 15 秒。控制接收线程按请求→最新租约→暂停代次读取，与取图／证据发布线程分开；独立看门狗只处理内存状态。取图期间仍能读取续租和暂停，失败后停止接收新命令，不延长期限。引导期在同一启动期限内等待有效首帧；黑白画面判据保持 0.999，排除取图最外圈一像素边缘。证据文件读写仅对 Windows 短暂共享冲突作 100ms 有界重试，持续失败仍松键并回收。运行中截图失败不回退前台。

## 数据与退出

引导期阻断文件／注册表写入，DLL 就绪后限制副本外文件创建／写入并阻断注册表修改，保留已核查系统加密设备例外。每批检查原目录、外部保存目录和副本外探针，正常停止或故障后核对目标／宿主退出，归档副本摘要后只回收运行副本。

这不是完整安全沙箱；不能抵御任意程序绕过 hook、硬链接等对抗行为，也不隔离网络。不因有 Job 或复制了目录就宣称任意游戏数据安全。更换构建／保存模式须重新核查与批准，不添加宽泛哈希放行。

## 可重复验证

```bash
python -m unittest tests.test_engines tests.test_native_prepare tests.test_native_concurrent tests.test_native_game_concurrent -v
python tests/native_input_safety.py --output <全新安全批次目录> --binaries <自有窗口构建目录>
python tests/native_input_mcp.py --output <全新MCP批次目录> --binaries <自有窗口构建目录>
python tests/native_input_slow_capture.py --output <全新慢取图批次目录> --binaries <自有窗口构建目录>
python tests/native_input_game.py --source <获准原目录> --output <全新游戏批次目录> --binaries <游戏宿主构建目录>
python tests/native_input_concurrent.py --output <全新并发批次目录> --binaries <自有窗口构建目录>
python tests/native_input_game_concurrent.py --source <获准原目录> --output <全新游戏并发批次目录> --binaries <游戏宿主构建目录>
```

自有窗口需要 `target.exe`、固定该窗口摘要的 `owned-host.exe` 和同版本 `native-input.dll`，通过 `build.py --mode target`、`--mode host --owned-image <target.exe>` 构建。游戏批次使用 `native-host.exe` 和 `native-input.dll`，不使用测试宿主。

自有窗口并发入口打开人工见证窗口，点击“准备好了”后按住左方向键、在窗口内移动鼠标并至少点击两次，后台同时执行 12 次软件右移动。

正式游戏入口 `native_input_game_concurrent.py` 复用同一菜单脚本、宿主与 DLL，在新副本经真实 MCP 进入环境设置，保持约 8 秒观察后返回标题，共 16 条动作与 17 张观察图；不重复发送标题下移，不更改设置或操作存档。见证窗口记录人工左键、鼠标与意外串入的按键，使用与回执相同的系统单调时钟核对真实保持区间；至少三条按键动作重叠、设置页人工左键采样跨度至少五秒，并检查鼠标参与、前台与退出隔离。未开始或中途关闭见证入口均停止并回收副本。

两个入口的 `--smoke` 均不要求人工参与，明确不作实体结论；原始结果保留 `physical_verified=false`。游戏批次还需逐图核对菜单及 BGM 选项未受前台左键／鼠标影响，并由参与者确认操作无干扰，再另存绑定原结果摘要的复核记录，不改写原始证据。

本地证据统一留在 `<原生输入验证目录>/`，不入源码仓库：`owned-safety-resumed/`、`owned-mcp-resumed/`、`game-menu-03/`、`concurrent-smoke-02/`，以及 `logs/` 的红／绿阶段记录。失败批次保留，后续通过不追认历史偶发首帧空白、启动失败或恢复拒绝均已定位。
