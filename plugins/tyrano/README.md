# TyranoScript 自写样本接入

本目录为固定 TyranoScript Ver5（引擎数值版本 `500`）／Electron 7.1.2 的自写工程提供现有 v2 毫秒协议桥和受管入口。商业工程仍只识别，不由工具启动。

## 受管接入

在[通用接入入口](../../docs/GAME-ADMISSION.md)中，游戏目录选择仅含 `gameqa-tyrano-fixture.ks` 的 `plugins/tyrano/samples/`，组件目录选择本机已登记的复制件根目录。准备时只复制固定运行组件和自写样本，不复制原剧情、存档和浏览器数据。启动前复核全部已准备文件，在独立桌面暂停取首图；必须显式恢复才接收输入。黑屏保留已有消费回执、拒绝后续输入并回收本批进程。

受管样本只开放确认和等待；受管存读档未验，后文的存读档结果来自独立桥用例。用户数据和缓存写入会话的 `data/`，证据留在 `bridge/`，结束后不自动删除运行副本。

## 组成与输入

- `host.py`：复用 `DesktopProcess`，持有独立 Win32 Desktop／Job，写入会话连接及监督记录，并用自己的单调时钟回收停滞进程树。宿主异常退出也由 Job 关闭回收。
- `MainBridge.js`：主进程的真实输入、取图、控制文件与回执。使用 Electron `webContents.sendInputEvent`，只开放 Enter 确认、等待与恢复；动作 `duration_ms` 为 1–1000 的整数。
- `Observer.js`：页内只读观察真实键盘事件、引擎键保持状态和绘制活性。DOM 分发结束后才确认按下／松开；不调用场景推进接口或修改游戏进度。

初始输入暂停，显式恢复后才接受动作；`pause_scope=input`、`simulation_pause=false`。网页逻辑与动画继续运行，观察序号不代表游戏帧数。预览约每 200 毫秒更新，动作图独立保存并附 SHA-256。只有按下、松开确认和操作后的有效图像齐备才成功；截图失败的回执保留已消费输入。

引擎须已经加载场景，窗口显示后再经历两次绘制观察才取首图。首图全黑、近乎全黑或全白时停止输入，不重试动作或降低 99.9% 判据。短时控制文件共享冲突最多重试 100 毫秒，超过期限按失败处理。

## 生命周期

独立宿主保持 15 秒启动、1 秒主／渲染线程停滞、3 秒截图和 1 秒未确认输入期限；客户端租约仍为 1.5 秒。暂停、租约失效和控制文件错误先释放按键，目标确认松键后才回执；缺少确认时回收本次 Job。持续读取错误不能顺延释放期限。

显式关闭时，先停止接单并确认松键，再请求 Electron 正常退出，使 localStorage 能刷盘。宿主给正常退出最多 1 秒；窗口阻止退出或进程卡住仍强制回收，不延长原故障期限。异常终止不保证尚未刷盘的游戏数据持久化。

`termination.json` 记录退出原因及回收结果。会话连接、租约、输入与请求留在桥对象中，不放入 `kag.stat` 或游戏存档；正常读取旧游戏进度不会恢复旧控制会话。

## 验证方式

需要本机已有、来源与摘要已核对的复制组件目录，包括 `engine-runtime.exe`、Electron 运行文件、`locales/`、`resources/app/tyrano/`、`index.html` 与 `data/system/Config.tjs`。两套回归使用同一份仓库自写场景及窗口入口，不运行商业剧情或携带原存档。

```powershell
$env:TMP = '<E 盘测试临时目录>'
$env:TEMP = $env:TMP
$env:GAMEQAKIT_TEST_TYRANO = '<已有复制组件目录>'
$env:GAMEQAKIT_TEST_EVIDENCE = '<全新测试证据目录>'
python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_tyrano_managed tests.test_tyrano_bridge -v
```

缺少 Windows 或组件目录时，真实运行测试明确跳过，不计为通过。文件存档和 localStorage 都通过正常 Enter 键映射触发引擎 quicksave／quickload，验证同进程和跨进程读取。程序旁 `.sav`、用户数据、缓存及临时文件均在测试目录，原组件摘要另外核对。所有运行副本由测试创建并回收，失败现场及回执图像另存证据目录。

自写主进程必须先调用 `configure(app)` 设置路径，再创建窗口并调用 `start(window)`，随后加载自写场景。验证所用的 CLI 为：

```powershell
python plugins/tyrano/host.py --runtime '<自写工程运行时绝对路径>' --directory '<全新会话绝对路径>' --session '<新会话编号>' --data-root '<隔离运行数据绝对路径>'
```

CLI 拒绝已存在连接的会话和 `resources/app.asar` 商业封装。这些检查与固定 EXE 摘要只约束当前实验布局，不认证网页脚本，也不构成任意工程的安全启动器。

## 来源与使用边界

组件来源、固定摘要及许可范围见 [来源声明](SOURCES.md)。仓库只包含自有桥、宿主和夹具，不分发运行时、解出的引擎、商业资源或存档。

仅验证这个运行时与自写工程。鼠标虽有独立探针，未加入本桥；取消键、方向键、帧动作、跨命令保持按键、仿真暂停、其他 Electron／NW.js 版本、商业游戏、长期稳定性和真人并发均未验。产品识别仍为 `tyranoscript_candidate`、`adapter=null`、`control_verified=false`。

独立桌面只隔离桌面输入，路径重定向只约束已核对的自写工程；它们不隔离任意文件、注册表或网络写入。不要把此目录直接放进原游戏作为通用安装方式。撤回本目录即可移除实验桥，共用协议和现有适配器无接口变化，无游戏数据迁移。
