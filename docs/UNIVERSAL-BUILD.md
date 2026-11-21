# macOS／Linux 通用测试版构建

已发布通用测试包的交付目标是软件可启动并能使用公共功能：配置读写、存档快照、已有证据复核、公共 MCP 工具。该批包在非 Windows 系统上禁用游戏窗口采集、持续盯屏、桌面输入和引擎后台控制。Windows 的既有引擎验收不能外推到其他系统。

开发中的 macOS arm64 采集分支已另行原生构建 Swift 组件及冻结应用：cloud-22 的源码／冻结 AppKit 产品链分别通过遮挡新图、冻结反例、尺寸变化、并发观察／人工标记、归档与退出，各独立核对 33 张 PNG；真实组件故障与恢复各通过 11 项、各回收 12 个辅助进程且无残留。Metal 仍有重复计数，完整产品验收未通过，首次授权／撤权、普通用户桌面与真实游戏未验。这些结果不改变下文旧测试包的功能范围；实施及限制见[接入设计](MACOS-ARM-CAPTURE-DESIGN.md)。

## 构建目标

| 产物 | 原生构建环境 | 架构 |
| --- | --- | --- |
| macOS 测试包 | GitHub `macos-15-intel` | x64／Intel |
| macOS 测试包 | GitHub `macos-15` | arm64／Apple Silicon |
| Linux 测试包 | GitHub `ubuntu-22.04` | x64 |
| Linux 测试包 | GitHub `ubuntu-24.04-arm` | arm64 |

两个架构分别提供包，不提供合并架构包。Linux 目标分别要求 glibc 2.35／2.39；这来自构建环境与固定 Qt wheel 的最低要求，不包含 Alpine／musl 支持。macOS 使用临时签名，不具备开发者签名或公证；较旧 macOS、Wayland 桌面和各发行版的日常使用仍需反馈。

## 已验证测试包

版本 `v0.4.2-universal-test`，源码 `3532d01e61f7dc5261d24424769dd1e4e04e6b2b`。[运行 44746750109](https://github.com/galact-byte/game-qa-kit/actions/runs/44746750109) 的四个 job 全部通过，各平台 12 项公共入口回归、冻结程序自检及 Cocoa／X11 桌面启动检查通过；8 份结果 JSON 与四张桌面截图已回读，中文正常。

| 平台 | 下载 artifact |
| --- | --- |
| macOS Apple Silicon | [universal-macos-arm64](https://github.com/galact-byte/game-qa-kit/actions/runs/44746750109/artifacts/14632958547) |
| macOS Intel | [universal-macos-x64](https://github.com/galact-byte/game-qa-kit/actions/runs/44746750109/artifacts/14633417025) |
| Linux x64 | [universal-linux-x64](https://github.com/galact-byte/game-qa-kit/actions/runs/44746750109/artifacts/14633152854) |
| Linux arm64 | [universal-linux-arm64](https://github.com/galact-byte/game-qa-kit/actions/runs/44746750109/artifacts/14634081145) |

Actions 下载需登录 GitHub；artifact 保留 14 天。每份下载包含 `.tar.gz` 程序包和 SHA-256 文件，解压外层下载文件后再解压程序包。用户真实桌面的日常交互与 Wayland 仍待反馈。

## 本地构建

在目标系统、目标架构上准备 Python 3.12.7 及已有或经授权安装的依赖：

```bash
python -m pip install --only-binary=:all: -r tools/requirements-unix.txt
python -B tools/build_unix.py --version v0.4.1-universal-test --output /path/to/new-build
```

Linux 构建环境还须具备 `fonts-noto-cjk` 和 Qt X11 依赖（含 `libxcb-shape0`）；工作流安装这些构建依赖。Noto 字体与完整许可一起装入 Linux 包并由应用加载，不要求试用者自行安装中文字体。

输出目录必须不存在，以免覆盖已有构建。构建脚本不安装依赖；PyInstaller 生成完整目录后，脚本从实际冻结程序执行 `--self-test`，创建界面、保存截图、验证配置与独立测试存档、生成证据报告并连接真实 MCP 子进程，然后打包 `.tar.gz` 并写入 SHA-256 摘要。macOS 说明写入 `.app` 后重新作临时签名并校验签名。

下载后的用户无需 Python。Linux 解压后打开 `GameQA/GameQA`；macOS 解压后打开 `GameQA.app`。详细用户说明随包提供，源码见 [UNIVERSAL-README](../tools/UNIVERSAL-README.md)。

## GitHub Actions

手动工作流 `.github/workflows/universal-desktop.yml` 的四个 Unix 构建最多同时运行两个，Windows x64 候选单独构建；不创建标签或 GitHub Release。Unix 任务先跑公共入口测试，再构建四种产物，并在 Linux X11 虚拟显示／macOS Cocoa 下执行启动自检。Windows 任务使用既有固定依赖，运行可用回归、构建便携包并执行实际 EXE 自检，上传 ZIP、摘要和自检 JSON。五目标候选均来自同一提交；仅正式发布的复用调用跳过此 Windows 候选任务，继续使用发布入口自身的 Windows 构建。包和 JSON／PNG 验证记录分别作为保留 14 天的 artifacts。

```bash
gh workflow run universal-desktop.yml --ref <构建分支> -f version=v0.4.1-universal-test
```

工作流和目标提交必须已在远端，手动工作流文件需先进入默认分支。若某一架构失败，其他架构仍继续构建；只有相应检查全部通过才上传该平台测试包。

## 正式 Release

`.github/workflows/windows-release.yml` 保留原调用入口，发布时复用本文件的四平台原生构建任务，再汇总 Windows x64 ZIP、macOS x64／arm64 与 Linux x64／arm64 四份 `.tar.gz`。只有五个目标全部通过构建及启动自检，并且每份原始 SHA-256 验证通过，才创建标签与 Release。`tools/assemble_release.py` 拒绝缺少平台、摘要不符、重名或越界资产，输出一份统一的 `SHA256SUMS.txt`。

工作流接入与本地汇总测试不等于已经发布。下次正式版本仍须有对应 `docs/releases/<版本>.md`，并在远端完成构建后回读标签、说明和五份资产；macOS 签名与 Linux 兼容范围沿用上面的限制。

## 验证口径

- 本机 Windows 的平台分支检查：禁止导入 Win32 模块后，分别模拟 `darwin`／`linux`，确认 GUI、CLI、MCP 公共入口可导入，平台专用功能禁用，公共功能可用。
- 启动检查：实际创建并截图窗口，检查中文字符覆盖，保存／读取配置，处理独立测试存档，生成报告，并通过子进程执行 MCP 握手与工具查询。
- 原生构建记录：对应 Actions job、产物摘要、`self-test.json`、`desktop-check.json` 和截图。
- 真实用户电脑：下载安全提示、文件管理器打开目录及日常操作反馈，不能仅凭 CI 建包成功声称全部通过。

本机平台模拟不等于 macOS／Linux 实机验证；原生结果以具体工作流记录为准。
