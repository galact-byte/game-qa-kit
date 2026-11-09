# DuckStation 隔离探针（开发验证）

此目录的 `Probe.cpp/prepare.py/run.py` 保存 SDL3 进程内虚拟手柄实验，探针本身不提供统一协议；正式桥见 [plugins/duckstation](../../plugins/duckstation/README.md)，共用的协议验收辅助脚本也放在本目录。
使用本机已有 DuckStation、BIOS、MSVC 和 Pillow；不提供镜像或 BIOS，不自动下载依赖。

## 范围与数据

`prepare.py` 从指定模拟器复制程序、依赖、资源和 BIOS，创建便携运行副本；不复制原记忆卡、即时存档或游戏设置。副本使用单独记忆卡、禁用音频输出与退出即时保存，输入只绑定虚拟设备 `SDL-15`。源程序和游戏目录的配置、BIOS、保存数据及指定镜像基线写入 `source-baseline.json`。这不是文件系统沙箱。

`run.py` 只向自己用 `DesktopProcess` 创建的独立桌面进程加载实验 DLL，不发送系统键鼠输入。必须先确认原保存路径和副本写入位置；输出目录必须不存在。退出时由 Job Object 回收进程树。探针 DLL 不支持在运行中卸载，回收以进程退出为界。

```powershell
python tests/duckstation/prepare.py --source <现有模拟器目录> --workspace <新的E盘工作目录> --game-dir <镜像目录>
cmd /c tests\duckstation\build.cmd "<vcvars64.bat完整路径>" "<同一工作目录>"
python tests/duckstation/run.py --workspace <同一工作目录> --game <cue完整路径> --out <新的证据目录> --inject --boot-wait 50 --steps tests/duckstation/menu.json --safety
```

脚本的 `completed` 只表示所选分支执行结束；必须同时核对 `injected=true`、菜单截图、释放掩码、暂停截图以及 `process_reaped`。省略 `--inject` 只走被动截图分支，不能算输入验收。

`menu.json` 针对本次《妖精战士3》样本：Start、圆圈、下、上。启动等待以秒计，机器负载可能改变启动时间，固定等待不能证明标题就绪；必须读回 `initial.png` 和操作后截图，开场动画或黑场批次不计为菜单通过。截图无效会中止本批，不能改用前台按键继续。

## 已测边界

本机 DuckStation 随带 SDL 3.4.20；独立桌面副本已观察到圆圈进入 NEW GAME 子菜单、下移 CONVERT、上移 GAME START；暂停和运行期间，250 毫秒租约到期后实际 SDL 按钮状态归零。具体批次、截图和原数据核对见 `<PS1验证目录>/验收索引.md`，该路径是本机测试证据，不是运行依赖。

SDL 更新回调在模拟器暂停时仍会运行；`callbacks` 与 `consumed_seq` 只能证明 SDL 侧更新，不能当作 PS1 帧号或游戏消费回执。探针本身没有游戏帧同步、统一 v1 会话校验、暂停代次或 MCP 接入；这些能力现由独立的正式桥及 `tests/duckstation_smoke.py` 验证，见 `<PS1验证目录>/正式桥验收索引.md`。音频、实机玩法、保存进度与换盘未验。

保留原始失败批次、源码和已测 DLL、基线与必要副本配置后，才可回收可再生成的运行副本；不得将测试记忆卡复制回原目录。
