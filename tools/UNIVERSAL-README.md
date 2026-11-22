# macOS／Linux 通用测试版

本包可编辑游戏配置、查看存档快照和复核已有证据，无需安装 Python。macOS 14 及更新版本的 Apple Silicon 另提供窗口截图、持续观察、人工标记与证据归档；首次使用请按系统提示授予屏幕录制权限。

该采集功能目前只完成云端自写 AppKit 窗口的源码及冻结验证。Metal 画面仍有重复帧，普通用户桌面、首次授权／拒绝／撤权和真实游戏尚未验收，请核对实际截图是否更新。macOS Intel 与默认 Linux 入口仍只提供公共功能；桌面键鼠输入仅在 Windows 开放，实验 Godot 后台控制默认关闭。

## 打开程序

- macOS：解压后打开 `GameQA.app`，也可以先拖入「应用程序」。下载时请选择与机器一致的 `arm64`（Apple Silicon）或 `x64`（Intel）包。本测试包仅作临时签名，未作 Apple 开发者签名及公证；系统可能要求在「隐私与安全性」中确认打开，受组织策略管理的电脑可能无法打开。
- Linux：解压后双击目录中的 `GameQA`；需要桌面环境。请完整保留旁边的 `_internal` 目录。若文件管理器不允许直接执行，可在该目录终端运行 `./GameQA`。

Linux x64 构建基于 Ubuntu 22.04，要求 glibc 2.35 或更新版本；arm64 基于 Ubuntu 24.04，要求 glibc 2.39 或更新版本。Alpine／musl 不在本次范围内。macOS 构建和启动检查使用 macOS 15，较早系统尚未验证。X11 的启动检查使用虚拟显示，Wayland 桌面尚未实机验证。

## 配置与记录

打开软件后点击「新建」，手动填写配置。可以指定存档目录和 QA 数据目录、查看已有快照并生成证据报告；没有记录时会显示空状态。灰色按钮对应暂未开放的平台功能。

配置和记录保存在用户目录，升级程序无需搬动它们：

- macOS：`~/Library/Application Support/game-qa-kit/`。
- Linux：配置默认在 `~/.config/game-qa-kit/profiles/`，记录默认在 `~/.local/share/game-qa-kit/`；支持 XDG 目录设置。

侧栏的「打开数据目录」会在系统文件管理器中打开当前配置的记录目录。

## MCP

MCP 使用同一个可执行文件，增加 `--mcp` 参数：macOS 的命令为 `GameQA.app/Contents/MacOS/GameQA`，Linux 为 `GameQA/GameQA`。各平台均提供配置、快照、报告和引擎识别等公共工具；`platform_info` 可查询当前能力。符合上述系统要求的 Apple Silicon 包另开放观察、人工标记和采集相关工具。固定 Godot 自写实验组件需单独准备并显式启用，其他平台或引擎不会据此开放后台控制。

## 反馈

请记录使用的系统版本、处理器类型、出问题时的操作及截图。构建产物的自检覆盖窗口创建、配置读写、独立测试存档快照、报告生成及真实 MCP 子进程握手；它不能替代日常桌面使用反馈。

项目源码、依赖版本与构建配方：https://github.com/galact-byte/game-qa-kit 。第三方许可见包内「第三方组件说明.md」及捆绑的许可证文件。
