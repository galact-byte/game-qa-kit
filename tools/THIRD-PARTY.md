# 第三方组件与来源

本项目自有源码采用 MIT，许可证见 `LICENSE`。便携包捆绑组件保留各自许可，不将整个依赖集合声称为 MIT。

| 组件 | 上游 | 许可与说明 |
| --- | --- | --- |
| CPython | https://www.python.org/ | Python Software Foundation License；完整文本随包保留 |
| Pillow | https://python-pillow.org/ | HPND 等上游许可；元数据与许可文件随 Pillow dist-info 保留 |
| PyQt6 | https://www.riverbankcomputing.com/software/pyqt/ | GPLv3／商业双许可；本公开便携构建使用开源发行包，适用 GPLv3，不声称拥有商业许可 |
| Qt 6 | https://www.qt.io/ | LGPLv3／GPLv3 及模块第三方许可，详见 PyQt6-Qt6 元数据与上游许可；保留动态共享库，允许依许可替换兼容库 |
| rubymarshal | https://pypi.org/project/rubymarshal/ | 以捆绑 dist-info 中的上游许可与元数据为准 |
| PyInstaller 引导程序 | https://pyinstaller.org/ | GPL 及引导程序分发例外；许可与例外文本随其 dist-info 保留 |
| Noto Sans CJK（仅 Linux 包） | https://github.com/notofonts/noto-cjk | SIL Open Font License 1.1；随包保留构建系统 `fonts-noto-cjk` 的完整 copyright／许可，位于 `_internal/licenses/NotoCJK/`。字体以应用字体加载，无需用户另行安装。 |
| Ruffle Web 自托管包 0.7.1（仅 Windows 包） | https://github.com/ruffle-rs/ruffle/releases/tag/v0.7.1 | MIT 或 Apache-2.0。上游原始 ZIP 随 `_internal/plugins/flash/` 分发，内含发布元数据及 `LICENSE_MIT`、`LICENSE_APACHE`。SHA-256 为 `ef12edbee135d581a9cd523837d746da30a07a64dbf1cf472d2e472c656f4b5e`。 |

组合发行遵循上游开源许可，包括 PyQt6 的 GPLv3 义务。完整 GPLv3／LGPLv3 文本和 Python 许可随包保留：Windows／Linux 位于 `_internal/licenses/`，macOS 位于 `.app/Contents/Resources/licenses/`。本批 GameQA 主程序发行未修改上述组件；单独的 EasyRPG 验证运行时包含源码输入补丁，来源与构建方法见 `plugins/easyrpg/README.md`，对应源码及许可随本地运行时保存。项目源码和构建配方在 GitHub 公开，固定依赖见 `tools/requirements-windows.txt` 与 `tools/requirements-unix.txt`。正式发行源码以 Release 标签为准；通用测试包不创建 Release，其对应源码以 GitHub Actions 的构建提交为准。

与捆绑二进制对应的上游源码入口（均可免费取得，不需要商业账号）：

- PyQt6 6.10.0 源码发行包：https://pypi.org/project/PyQt6/6.10.0/#files
- PyQt6-sip 13.10.2 源码发行包：https://pypi.org/project/PyQt6-sip/13.10.2/#files
- Qt 6.10.0 完整源码及构建说明：https://download.qt.io/archive/qt/6.10/6.10.0/single/
- CPython 3.12.7：https://www.python.org/downloads/release/python-3127/
- Pillow 11.1.0、rubymarshal 1.2.10、PyInstaller 6.16.0 的源码发行包可在对应 PyPI 版本页面的 Source Distribution 中取得。

PyQt6-sip 的许可和元数据亦随其 dist-info 保留。

`plugins/easyrpg/licenses/` 保留 EasyRPG Player 和 liblcf 的许可，`plugins/easyrpg/source-manifest.json` 记录固定提交及归档摘要。GameQA 主 ZIP 只包含适配器、自写样本及来源说明，另行准备的 `GameQaEasyRpg.exe` 不随主包分发。该独立运行时适用 GPL-3.0-or-later，包含修改后的正常输入源，不能按项目 MIT 许可处理；对外提供其二进制前须同时提供完整对应源码、构建配方及依赖许可。

`plugins/native_input/licenses/` 和相关 `source-manifest.json` 保留 Proto Input、EasyHook、udis86 等插件代码的来源与许可。本便携包不捆绑商业游戏、游戏存档、BepInEx／NW.js／RGSS 等外部运行组件；相应组件仍由用户按已核查流程提供。
