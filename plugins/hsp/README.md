# HSP3 后台控制插件（实验）

本插件面向 Windows x86 HSP3，使用既有协议 v1 / INI 和统一 MCP 动作。当前实测为本机官方 HSP 3.7 运行时的空白工程，以及《八星帝》同一份外置字节码的标题和读取界面。兼容范围限定为下述构建和样本。

## 通用受管入口

场景目录只放仓库自写样本 `samples/gameqa-hsp-fixture.ax`。组件目录放登记的 `hsp3.exe`（SHA-256 `5eeb5ed1bb3f8d7aeac90d35e2f4c1d15f02586a8e8a79484072838b95b7fec8`）和按下文构建的 `GameQaHsp.dll`。选择新目录创建隔离副本后再启动。MCP 实测覆盖暂停首图、持续预览、右移、菜单开关、松键及进程退出、预览故障回收与改动副本拒启。商业字节码沿用下文的独立入口。

字节码可从仓库的 `tests/hsp/Probe.hsp` 重建。在构建目录将脚本命名为 `probe.hsp`，使用 HSP 3.7 的 `hspcmp.exe -ogameqa-hsp-fixture.ax --compath=<HSP安装目录>/common/ probe.hsp`（关闭调试输出）。SHA-256 应为 `6ca089127a166fedbe0253110636ad28f0c53103fb296c68cfd563cd2f78fb7c`。

## 构建与接入

使用已经安装的 MSVC x86 和 HSP 插件 SDK，不自动安装或下载：

```bash
python plugins/hsp/build.py --output <输出目录>/GameQaHsp.dll --sdk <HSP安装目录>/hspsdk/hpi3sample --vcvars <VS安装目录>/VC/Auxiliary/Build/vcvars32.bat
python plugins/hsp/build.py --test-core --output <输出目录>/core.exe --vcvars <VS安装目录>/VC/Auxiliary/Build/vcvars32.bat
python plugins/hsp/prepare.py --source <副本>/start.ax --output <副本>/gameqa.ax
```

`prepare.py` 保留已有代码、数据与插件表，追加桥注册信息，输出必须是新文件。把 DLL 放在测试脚本旁，使用匹配的官方 HSP 运行时执行 `gameqa.ax`。内嵌字节码的原 EXE 不会自动读取这个外置脚本；不能仅修改旁边的 `start.ax` 就认定桥已载入。运行时替换只在已批准的隔离副本内进行，其他运行时版本和游戏须重新验证。

通过 `DesktopProcess` 创建独立桌面子进程，设置 `GAMEQAKIT_BRIDGE_DIR` 和随机 `GAMEQAKIT_SESSION_ID`，调用 `write_connection` 写入协议、当前 PID、完整运行时路径、`command_format=ini`、`engine=hsp3`，随后才调用子进程的 `run()`。桥核对实际线程桌面与 `GAMEQAKIT_DESKTOP_NAME`，不符时拒绝输入。接入示例和会话语义见 [后台控制](../../docs/BACKGROUND-CONTROL.md)。

## 输入与生命周期

- HPI3 接管 `getkey`；当前运行时的 `stick` 直接读取 `GetAsyncKeyState`，由目标运行时的导入表适配，并处理其 `GetActiveWindow` 判断。仅作用于目标进程。
- 鼠标坐标通过目标窗口自身的消息缓存更新；真实子 `Button` 控件使用按下／松开消息，不直接调用业务回调。暂停取消按压，不能生成点击。
- 在 `HSPCTX.msgfunc` 的等待处理边界接入更新并继续调用原处理函数；不能将插件注册阶段当作窗口已创建。更新计数表示桥处理边界，不能理解为渲染帧率或固定时长。
- 支持 `move`、`confirm`、`cancel`、`wait`、`press`、`release`、`click`，动作与截图一起返回；初始暂停。无消费、截图失败、优先暂停和过期租约会停止动作并释放输入。旧会话暂停隔离，未放宽现有时限。
- 独立桌面隔离用户活动桌面的输入，不隔离文件系统、注册表和网络；复制前须查明实际保存位置。没有最小化或任意扩展 DLL 的兼容保证。

## 可重复验证

仅在全新的项目外目录运行，保留日志、回执和 PNG；不传原游戏目录：

```bash
python tests/hsp_smoke.py --runtime <HSP安装目录>/hsp3.exe --compiler <HSP安装目录>/hspcmp.dll --common <HSP安装目录>/common --plugin <构建目录>/GameQaHsp.dll --work <全新证据目录>
```

增加 `--wait-loop` 验证 `wait` 路径，默认使用 `await`；增加 `--desktop-mismatch` 验证桌面绑定不符时拒绝输入。空白工程检查 getkey/stick 位移、菜单、画布点击、原生按钮、按压暂停不误点击、旧会话暂停、运行中暂停、租约过期与前台采样。

商业副本已验证 LOAD 打开读取界面、分页 01–10→11–20、两次关闭返回标题及暂停释放；没有读取原存档，不宣称战斗、剧情、保存／读入进度、全流程或长期稳定。商业界面的“关闭”按钮未被证明是原生控件；原生按钮证据仅来自空白工程。
