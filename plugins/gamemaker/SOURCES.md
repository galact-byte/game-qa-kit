# 来源、构建与许可

## 本仓库代码

`Bridge.h`、`BridgeState.h`、Python 宿主、准备器、构建器与 `samples` 场景为本项目自有代码，遵循仓库根目录 `LICENSE`。统一 duration 宿主、取图 worker、独立桌面与 Job 回收沿用本仓库实现。没有集成 YYToolkit、EasyHook 或系统键鼠注入库。

`source-manifest.json` 记录自写工程、原始 YYC 生成单元与 `data.win` 摘要；`runtime-manifest.json` 记录本次明确接纳的 EXE 与数据摘要。源码版本以包含这些文件的 Git 提交为准。本地构建记录额外保存两份桥头文件摘要和原生成源码前后核对结果。

## 官方工具与生成物

- GameMaker Windows LTS IDE：`2026.0.0.16`，官方入口 <https://gamemaker.io/en/download/windows/lts/GameMaker.exe>；使用时下载到的安装包经 Authenticode 核验，签发主体为 YOYO GAMES LIMITED。
- 匹配运行组件：`2026.0.0.23`，官方更新清单 <https://gms.yoyogames.com/Zeus-Runtime-LTS2026.rss>。使用 Core、`base-module-windows-x64`、`windows` 与 `windowsYYC`，经官方工具验签和解包。
- 固定 x64 YYC 库 `yyc/Win32/lib/x64/YoYo.lib` 的 SHA-256：`44edf3e5e1ce3a85b1fb8587abac8041a353bf10b7d81c5bbcda0b748d4019ce`。
- 本地已有 Visual Studio 2022 Build Tools 与 Windows SDK。官方生成工程默认选择 v142，本地重建显式覆盖为已安装 v143，没有另装旧工具集。

官方 IDE、SDK、Runner 库及其生成代码属于 GameMaker 许可范围，不能因本仓库的开源许可视为可自由再分发。遵循官方安装时提供的许可条款；本仓库和主便携包不包含官方库、SDK、生成 C++ 单元、IDE 或固定样本 EXE／data.win。维护者应使用合法持有的组件完成本机构建，部署前单独核对适用的再分发条款。

## 维护者构建

1. 使用匹配的官方 IDE 和运行组件编译 `samples` 的原样自写工程，保留 YYC 生成目录和输出 `data.win`。当前生成单元必须匹配 `source-manifest.json`。
2. 在具有现有 VS 2022 x64 工具链的 Windows 上运行：

```powershell
python -X utf8 -B -m plugins.gamemaker.build --generated '<YYC生成目录>' --data '<输出data.win>' --output '<不存在的新构建目录>' --vsdevcmd '<VS安装目录>\Common7\Tools\VsDevCmd.bat'
```

3. 构建器校验登记的生成单元和数据，只在输出副本中插入桥并重建，保留 `build.log`、`build-result.json` 和 `artifacts`。原生成单元摘要必须一致。构建需要原生成工程所引用的本地 SDK、库和资源；不同安装位置或生成器版本需要重新审查生成工程。
4. `artifacts` 仅在实际摘要与 `runtime-manifest.json` 相同时可用于当前受管入口。重编译的时间戳、工具集或资源变化可能得到不同摘要，不能自动覆盖登记以取得资格；必须重新完成目标时钟、原生状态机和 MCP 生命周期／便携验收。

固定成品只用于不写存档的自写样本。商业游戏、通用加载器、VM 输入和其他构建没有由这些来源或构建步骤取得支持资格。
