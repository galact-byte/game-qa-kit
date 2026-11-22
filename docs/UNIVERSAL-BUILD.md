# macOS 与 Linux 程序包

v0.4.3 起，正式 Release 提供 macOS Intel、macOS Apple Silicon、Linux x64 和 Linux arm64 四种独立程序包，与 Windows 包共用 `SHA256SUMS.txt`。

## 下载与运行

从 [Releases](https://github.com/galact-byte/game-qa-kit/releases/latest) 按系统和芯片选择 `.tar.gz`，完整解压后运行：

| 平台 | 程序入口 | 兼容范围 |
| --- | --- | --- |
| macOS Intel | `GameQA.app` | x64；临时签名，尚未公证 |
| macOS Apple Silicon | `GameQA.app` | arm64；临时签名，尚未公证 |
| Linux x64 | `GameQA/GameQA` | glibc 2.35 或更新版本 |
| Linux arm64 | `GameQA/GameQA` | glibc 2.39 或更新版本 |

无需另装 Python。Linux 包内附中文字体；Alpine／musl 不在支持范围内。较旧 macOS、Wayland 与各发行版的日常桌面使用尚未验证。

## 可以使用的功能

macOS／Linux 可以配置项目、处理存档快照、复核已有证据并使用公共 MCP 工具。游戏窗口采集、持续盯屏、桌面输入和引擎后台控制目前仍限 Windows。

开发分支中的采集实验不代表正式程序包已支持该功能。Windows 上验证过的游戏和控制桥也不能直接视为支持其他系统。

## 升级与数据

升级前结束旧会话，并保留自己的配置与数据。macOS 使用用户的 Application Support 目录，Linux 使用 XDG 目录。详细位置和使用方法见随包说明，源码在 [UNIVERSAL-README](../tools/UNIVERSAL-README.md)。

## 构建环境

四个平台分别使用原生环境构建，不提供合并架构包。

| 目标 | GitHub runner |
| --- | --- |
| macOS x64 | `macos-15-intel` |
| macOS arm64 | `macos-15` |
| Linux x64 | `ubuntu-22.04` |
| Linux arm64 | `ubuntu-24.04-arm` |

## 本地构建

在目标系统、目标架构上准备 Python 3.12.7 及已有或经授权安装的依赖：

```bash
python -m pip install --only-binary=:all: -r tools/requirements-unix.txt
python -B tools/build_unix.py --version <构建版本> --output /path/to/new-build
```

Linux 构建环境还须具备 `fonts-noto-cjk` 和 Qt X11 依赖（含 `libxcb-shape0`）；工作流安装这些构建依赖。Noto 字体与完整许可一起装入 Linux 包并由应用加载，不要求试用者自行安装中文字体。

输出目录必须不存在，以免覆盖已有构建。构建脚本不安装依赖；PyInstaller 生成完整目录后，脚本从实际冻结程序执行 `--self-test`，创建界面、保存截图、验证配置与独立测试存档、生成证据报告并连接真实 MCP 子进程，然后打包 `.tar.gz` 并写入 SHA-256 摘要。macOS 说明写入 `.app` 后重新作临时签名并校验签名。

下载后的用户无需 Python。Linux 解压后打开 `GameQA/GameQA`；macOS 解压后打开 `GameQA.app`。详细用户说明随包提供，源码见 [UNIVERSAL-README](../tools/UNIVERSAL-README.md)。

## GitHub Actions

手动工作流 `.github/workflows/universal-desktop.yml` 的四个 Unix 构建最多同时运行两个，Windows x64 候选单独构建；不创建标签或 GitHub Release。Unix 任务先跑公共入口测试，再构建四种产物，并在 Linux X11 虚拟显示／macOS Cocoa 下执行启动自检。Windows 任务使用既有固定依赖，运行可用回归、构建便携包并执行实际 EXE 自检，上传 ZIP、摘要和自检 JSON。五目标候选均来自同一提交；仅正式发布的复用调用跳过此 Windows 候选任务，继续使用发布入口自身的 Windows 构建。包和 JSON／PNG 验证记录分别作为保留 14 天的 artifacts。

```bash
gh workflow run universal-desktop.yml --ref <构建分支> -f version=<构建版本>
```

工作流和目标提交必须已在远端，手动工作流文件需先进入默认分支。若某一架构失败，其他架构仍继续构建；只有相应检查全部通过才上传该平台测试包。

## 正式 Release

`.github/workflows/windows-release.yml` 保留原调用入口，发布时复用本文件的四平台原生构建任务，再汇总 Windows x64 ZIP、macOS x64／arm64 与 Linux x64／arm64 四份 `.tar.gz`。只有五个目标全部通过构建及启动自检，并且每份原始 SHA-256 验证通过，才创建标签与 Release。`tools/assemble_release.py` 拒绝缺少平台、摘要不符、重名或越界资产，输出一份统一的 `SHA256SUMS.txt`。

每次正式发布须准备对应的 `docs/releases/<版本>.md`，完成远端构建后核对标签、说明、五个平台的程序包和统一校验文件。macOS 签名与 Linux 兼容范围沿用上面的限制。

## 验证口径

- 本机 Windows 的平台分支检查：禁止导入 Win32 模块后，分别模拟 `darwin`／`linux`，确认 GUI、CLI、MCP 公共入口可导入，平台专用功能禁用，公共功能可用。
- 启动检查：实际创建并截图窗口，检查中文字符覆盖，保存／读取配置，处理独立测试存档，生成报告，并通过子进程执行 MCP 握手与工具查询。
- 原生构建记录：对应 Actions job、产物摘要、`self-test.json`、`desktop-check.json` 和截图。
- 真实用户电脑：下载安全提示、文件管理器打开目录及日常操作反馈，不能仅凭 CI 建包成功声称全部通过。

本机平台模拟不等于 macOS／Linux 实机验证；原生结果以具体工作流记录为准。
