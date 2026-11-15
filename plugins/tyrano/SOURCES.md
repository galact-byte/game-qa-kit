# TyranoScript 实验桥的来源与许可边界

`host.py`、`MainBridge.js`、`Observer.js` 与 `tests/tyrano_fixture/` 是本项目自写实现，按仓库根 [LICENSE](../../LICENSE) 使用。本目录不包含或分发下述第三方组件。

## 验证组件来源

本机既有样本 `Magical_Girl_Mystic_Lune_forDLsite` 的随附运行文件与 `resources/app.asar` 仅作只读组件来源。在独立验证目录复制运行时、公共 TyranoScript 引擎和系统配置，另建自写场景与主进程；没有运行商业剧情、复制其存档或提交商业素材。该交付不是官方 SDK 发行包。

| 对象 | 实际版本／固定内容标识 |
|---|---|
| Electron EXE | 实测 `process.versions.electron=7.1.2`；SHA-256 `07af90854455fcb4c8f16d2eae885373d6960df4f9023717a780ff08b056d8f1` |
| 来源 `resources/app.asar` | SHA-256 `4c06e7e3cd63f65ecb692242c037104376b1ca91c6bff821ec1cf13e21a08e8c` |
| `tyrano/tyrano.js` | SHA-256 `371d77f8f063d24e1afbaab42ccc5cc3421c7bd9766aac6f66285ec82b272398` |
| `tyrano/plugins/kag/kag.js` | 实测 `kag.version=500`（Ver5）；SHA-256 `9126a5115f6b7b1e5544c58b71de7a32698296b921be344c5e1a7079724e32e7` |
| 来源随附 `LICENSE` | SHA-256 `3717d09363ecfedac79057936b95733514f011c4702907a665543a7497a303c1` |
| 来源随附 `LICENSES.chromium.html` | SHA-256 `67885e1586ecf0354e79467340cebe4d977b8ddcb432f7e832008b4ff3c8a1ff` |

这些是随附构建的内容标识。样本未提供可核实的 TyranoScript 上游 commit／revision，不填造上游提交号，也不凭 Ver5 推断任意同名构建一致。完整逐文件来源、摘要、路径和前后核对留在 `<Tyrano验证目录>/engine-probe-01/audit.json` 及 `final-review/source-before.json`。

## 随附声明

来源 `LICENSE` 开头为 `Copyright (c) 2013-2019 GitHub Inc.` 与 MIT 许可全文；同文件另列：

> TyranoScript
>
> Copyright (c) 2012-2019 STRIKEWORKS / Shikemokumk
>
> http://tyrano.jp

该文件还包含 animate.css、html2canvas、jQuery、jQuery UI 等组件的独立声明；Chromium 的声明在 `LICENSES.chromium.html` 中。测试复制公共组件时同时保留这两个原始文件及组件内的声明。

Electron 的 MIT 文本不自动授予 TyranoScript、其附带组件或商业资源的再分发权。本轮未独立核实该随附 TyranoScript 构建的完整上游许可与源码对应关系，因此只交自有代码和外部组件验证方式；不提供第三方下载、运行时打包或商业包改写。未来分发第三方组件前必须另核对对应版本许可并保留完整声明。
