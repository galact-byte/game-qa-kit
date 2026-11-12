# RPG Maker MV／MZ JavaScript 桥

共用 v1 帧协议与统一 MCP，保留按引擎版本适配的输入／渲染钩子。旧插件接入方法见[后台控制说明](../../docs/BACKGROUND-CONTROL.md)；引擎支持范围以[覆盖表](../../docs/ENGINE-COVERAGE.md)为准。

## 受管启动

主程序“AI 后台接入”、CLI 与 MCP 共用 `prepare.py`／`session.py`。当前仅接受登记的 MV 1.6.1、MZ 1.8.0 SDK 空白工程和固定完整 NW.js 运行时；修改过的工程、额外插件、商业构建与其他运行时返回阻碍。选择原工程目录及包含 `nw.exe` 的组件目录，使用全新 QA 会话目录；具体步骤见[游戏接入说明](../../docs/GAME-ADMISSION.md)。

准备时核对完整源与组件清单，在新目录复制工程及运行时，不带原 `save/`；只给副本安装本仓库桥、随机包标识并关闭单实例限制。浏览器数据通过 `--user-data-dir` 定位到会话 `runtime-data/`，NW.js 实报路径为其中的 `Default/`；存档位于副本 `runtime/app/save/`，临时目录也在会话内。会话目录不得与原工程或运行时重叠。

准备摘要存完整清单的 SHA-256，而逐文件清单留在会话内，避免大工程的准备请求超过控制文件上限；摘要变化仍拒绝。初始状态保持暂停与松键，仅允许引擎自然初始化到标题并导出有效首图，不自动开始游戏。

## 截图与观察

受管启动显式启用 `GAMEQAKIT_OBSERVATION=1`，桥在屏幕呈现后导出 `screenshots/startup.png` 与持续更新的 `screenshots/observation.png`，报告观察序号、存档和浏览器数据路径。未设置此环境变量的旧接入保留原行为。

动作截图等待场景资源就绪及画面呈现；离屏 `renderTexture` 绘制不计作屏幕呈现，新场景的第一次呈现用于初始化，后续呈现才发布。Qt 创建者和外部观察者按持续观察图刷新，不能只按 v1／v2 协议号决定显示首图。图片从已关闭文件的字节解码，避免阻塞 Windows 发布新图。

## 生命周期与边界

启动由独立桌面和创建者持有的 Job 管理。连接不恢复输入；外部观察不占动作锁，也没有终止创建者进程的权限。取图失败、黑图或断连时停止本批，不退回前台输入。结束须确认暂停松键、进程退出，并核对原工程、原运行时与原浏览器数据；仅无变化副本可回收，新增存档或其他变化保留。

本次验证范围为这两个固定 SDK 工程的标题开始、地图右移、菜单、当前观察预览与最终收尾。商业游戏、额外插件、其他版本、存读档、长期稳定性及真人实体输入并发均不据此宣称通过。独立桌面和目录副本不是完整文件系统沙箱。

## 回归

```bash
python -X utf8 -W error::ResourceWarning -m unittest tests.test_rpgmaker_admission tests.test_admission_gui tests.test_game_worker tests.test_game_session -v
node --test tests/rpgmaker_bridge.test.js
```

回退前先结束自有受管会话；旧桥入口继续保留，新增进度及失败证据无需删除。原始实机批次记录仅在本地验证目录留存。
