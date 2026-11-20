# macOS／Linux 通用测试版构建

本阶段交付目标是软件可启动并能使用公共功能：配置读写、存档快照、已有证据复核、公共 MCP 工具。游戏窗口采集、持续盯屏、桌面输入和引擎后台控制在非 Windows 系统上禁用。Windows 的既有引擎验收不能外推到其他系统。

## 构建目标

| 产物 | 原生构建环境 | 架构 |
| --- | --- | --- |
| macOS 测试包 | GitHub `macos-15-intel` | x64／Intel |
| macOS 测试包 | GitHub `macos-15` | arm64／Apple Silicon |
| Linux 测试包 | GitHub `ubuntu-22.04` | x64 |
| Linux 测试包 | GitHub `ubuntu-24.04-arm` | arm64 |

两个架构分别提供包，不提供合并架构包。Linux 目标分别要求 glibc 2.35／2.39；这来自构建环境与固定 Qt wheel 的最低要求，不包含 Alpine／musl 支持。macOS 使用临时签名，不具备开发者签名或公证；较旧 macOS、Wayland 桌面和各发行版的日常使用仍需反馈。

## 本地构建

在目标系统、目标架构上准备 Python 3.12.7 及已有或经授权安装的依赖：

```bash
python -m pip install --only-binary=:all: -r tools/requirements-unix.txt
python -B tools/build_unix.py --version v0.4.1-universal-test --output /path/to/new-build
```

输出目录必须不存在，以免覆盖已有构建。构建脚本不安装依赖；PyInstaller 生成完整目录后，脚本从实际冻结程序执行 `--self-test`，创建界面、保存截图、验证配置与独立测试存档、生成证据报告并连接真实 MCP 子进程，然后打包 `.tar.gz` 并写入 SHA-256 摘要。macOS 说明写入 `.app` 后重新作临时签名并校验签名。

下载后的用户无需 Python。Linux 解压后打开 `GameQA/GameQA`；macOS 解压后打开 `GameQA.app`。详细用户说明随包提供，源码见 [UNIVERSAL-README](../tools/UNIVERSAL-README.md)。

## GitHub Actions

手动工作流 `.github/workflows/universal-desktop.yml` 最多同时运行两个构建，不创建标签或 GitHub Release。它先跑公共入口测试，再构建四种产物，并另外在 Linux X11 虚拟显示／macOS Cocoa 下执行启动自检。包和 JSON／PNG 验证记录分别作为保留 14 天的 artifacts。

```bash
gh workflow run universal-desktop.yml --ref <构建分支> -f version=v0.4.1-universal-test
```

工作流和目标提交必须已在远端，手动工作流文件需先进入默认分支。若某一架构失败，其他架构仍继续构建；只有相应检查全部通过才上传该平台测试包。

## 验证口径

- 本机 Windows 的平台分支检查：禁止导入 Win32 模块后，分别模拟 `darwin`／`linux`，确认 GUI、CLI、MCP 公共入口可导入，平台专用功能禁用，公共功能可用。
- 启动检查：实际创建并截图窗口，保存／读取配置，处理独立测试存档，生成报告，并通过子进程执行 MCP 握手与工具查询。
- 原生构建记录：对应 Actions job、产物摘要、`self-test.json`、`desktop-check.json` 和截图。
- 真实用户电脑：下载安全提示、文件管理器打开目录及日常操作反馈，不能仅凭 CI 建包成功声称全部通过。

本机平台模拟不等于 macOS／Linux 实机验证；原生结果以具体工作流记录为准。
