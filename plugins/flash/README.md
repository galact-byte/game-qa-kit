# Flash 自写样本后台测试

通用游戏接入支持仓库 `samples/` 中登记的 AVM1 SWF。需要 Windows x64 和已安装的 Microsoft Edge `157.0.4341.60`，准备和启动时校验 `msedge.exe`、`msedge.dll` 和 Ruffle ZIP 的 SHA-256。浏览器更新后的构建需要重新验收。官方 Ruffle Web `0.7.1` 自托管资源与许可证随便携包提供。

## 使用方式

1. 在游戏接入界面选择 `plugins/flash/samples/`（便携包位于 `_internal/plugins/flash/samples/`），指定一个新的测试副本目录。程序会查找已登记的 Edge 安装。
2. 确认准备与启动。首图合格后，会话保持输入暂停，可以继续查看截图。
3. 交给 MCP 操作时显式恢复输入。用 `control_action` 的 `move` 和 `right` 验证色块按下时右移 88 像素、松开时回到原位。也可以用 `wait` 等待后取图。
4. 用 `game_stop` 结束会话，检查进程退出、按键释放和原样本摘要回执。故障时读取 `bridge/termination.json` 区分按下已确认、松开已确认和进程退出。

后台进程使用独立 Windows 桌面、Job、私有浏览器配置与 CDP 管道。截图无效或停更后禁止新输入。渲染器失去响应时，宿主按期限回收进程，并保留当时能确认的输入回执。

商业 SWF、投影器、鼠标、存读档、其他浏览器构建和真人键鼠同时操作待验。[接入设计](../../docs/FLASH-ADMISSION-DESIGN.md)
