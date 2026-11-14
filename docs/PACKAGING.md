# Windows 便携版与 GitHub Release

## 用户资产

- `game-qa-kit-v<版本>-windows-x64.zip`：完整解压后双击 `GameQA.exe`。
- `GameQA-MCP.exe`：控制台型 MCP stdio 入口，与 GUI 共用 `_internal`，不需要外部 Python。
- `SHA256SUMS.txt`：ZIP 的 SHA-256，发布前在远端再次校验。

采用 PyInstaller onedir，而不是每次启动都解压到临时目录的单文件包。必须保留 `_internal`，程序无需管理员权限、不会自动安装依赖。可选 OCR／YOLO 及游戏／外部引擎组件不打入包中。

## 存储与兼容

只有冻结便携版将默认 `profiles/` 和 `data/<配置名>/` 放在 EXE 旁；源码运行的默认目录不变，配置里的显式数据路径与 `GAMEQAKIT_DATA` 仍优先。选择可写目录，升级采用新目录，再复制自己的数据。

`sys.executable` 在冻结环境下指向 EXE，不再是 Python。`gameqakit.application` 对既有 `-m gameqakit.sessions`、`-m gameqakit.game_worker` 等登记模块显式分派，保留后台 worker 和 MCP 的原有实现，不把子进程再次打开为 GUI。

插件的 Ruby／JavaScript／JSON、源文件与许可作为资源保留。未捆绑商业游戏、存档、用户 profiles、原生编译产物、Unity／NW.js／RGSS 组件或机器学习模型。具体支持范围仍以引擎覆盖表为准。

## 本地构建

构建器不会自动安装依赖；Windows x64 Python 3.12.7 与依赖准备好后：

```powershell
python -m pip install -r tools/requirements-windows.txt
python tools/build_windows.py --version v0.2.1 --output E:/DevData/gameqa-release/new-build
```

输出目录必须不存在，防止覆盖旧发布和证据。脚本为本次构建单独设置 PyInstaller 缓存，不移动全局缓存。构建后在自有 Win32 Desktop＋Job 中启动真实 EXE，检查插件资源、冻结子进程、MCP 握手／工具列表、Qt 窗口和截图；自检失败不打 ZIP。此自检不启动游戏或发送输入，不代替各引擎的真实游戏验收。

## 远端发布

工作流：`.github/workflows/windows-release.yml`，仅 `workflow_dispatch` 手动触发，不在普通 push 上自动发版。

```powershell
gh workflow run windows-release.yml --ref main -f version=v0.2.1
```

先人工编写并提交 `docs/releases/<版本>.md`，再触发对应版本。CI 在 Windows 2022 上按固定版本安装依赖，运行聚焦产品回归、构建两个 EXE 并执行真实冻结产物自检。上游 Actions 使用已核对的完整提交 SHA。

构建任务只有仓库读取权限。成功后 Linux 发布任务取得 `contents: write`，回读并验证校验和；若版本／Release 已存在，不覆盖原资产或说明。新标签指向本次构建的准确提交；已有标签必须匹配同一提交。正文完全来自人工说明文件，不使用自动提交列表。失败时保留 14 天的构建自检证据，成功资产同样先落 Actions artifact，再上传 GitHub Release。

触发工作流只代表请求被接受，不代表构建或发布成功。可直接打开对应 Actions 运行查看结果；无需本地代理持续跟踪。正式发布后应核对标签、正文和 ZIP／校验文件，以远端实际结果为准。

## 许可与当前限制

项目源码 MIT；PyQt6 开源发行包适用 GPLv3，Qt 与其他组件也须遵守上游许可。便携包提供完整许可、固定依赖版本、上游对应源码地址与本项目标签源码／构建配方，详见 `tools/THIRD-PARTY.md`。不声称整个依赖集合是 MIT，不包含商业 Qt 许可。

当前不进行代码签名，不提供安装器或自动更新。Windows 可能提示未知发布者，应核对下载来源和 SHA-256，不需要修改安全软件设置。未验收的 Wolf 受管接入分支不纳入本次便携版；已提交的显式首图兼容选项仍保持原有实验边界。
