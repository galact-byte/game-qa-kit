# Game QA Kit Windows 便携版

1. 将整个 ZIP 解压到可写目录，建议非系统盘。
2. 双击 `GameQA.exe` 打开盯屏巡检控制台，无需安装 Python 或运行 BAT。
3. 新建游戏配置，选择已运行的目标，再点击「开始盯屏」。人工盯屏不会自动控制游戏。

必须保留同目录的 `_internal`，不要只复制单个 EXE。配置位于 `profiles/`，未指定其他目录的新配置默认把记录写入 `data/<配置名>/`；显式设置的数据路径仍优先。升级请先解压到新目录，再复制自己的 `profiles/` 与 `data/`，不要覆盖旧进度。

## AI 客户端配置

在支持 MCP 的客户端设置中添加以下配置，将路径改成实际解压位置：

```json
{
  "mcpServers": {
    "gameqa": {
      "command": "E:/Apps/GameQA/GameQA-MCP.exe"
    }
  }
}
```

客户端会自动启动 `GameQA-MCP.exe`，无需手动打开，也无需安装 Python。`GameQA.exe` 用来打开图形界面。移动程序后，需要同步修改客户端中的路径。

可通过 `GAMEQAKIT_PROFILE` 指定游戏配置。AI 后台操作还需要匹配的游戏适配组件，支持情况见 [引擎支持范围](https://github.com/galact-byte/game-qa-kit/blob/main/docs/ENGINE-COVERAGE.md)。

## 使用条件

- 系统：Windows 10／11 x64。便携包不带游戏、存档、第三方游戏运行时或个人配置。
- AI 后台接入只对已登记构建开放，仍需匹配的原生组件／Unity 桥／NW.js／RGSS301 等既有产物；本包不会下载或安装这些组件。
- OCR／YOLO 模型和可选机器学习依赖未随包提供；普通留图、人工标记与规则候选仍可用。
- 后台取图能力由游戏决定；黑图、停更或失联时应停止输入。自动疑点不是已确认缺陷。
- 程序尚未代码签名，Windows 可能提示发布者未知。请核对 GitHub 下载来源与 SHA-256，不需要关闭安全软件。

源码、构建脚本和具体已验范围见对应 GitHub Release 的标签源码。项目源码采用 MIT；捆绑的 Python、Qt、PyQt6 等另有许可证，见 `第三方组件说明.md` 和 `_internal` 内的上游许可文件。
