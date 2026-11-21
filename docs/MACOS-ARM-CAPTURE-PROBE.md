# macOS arm64 单窗口采集探针结果

## 已验证范围

[云端运行 44940206862](https://github.com/galact-byte/game-qa-kit/actions/runs/44940206862) 基于提交 `b29b1c5a8df5608a5cfd784a34e443ef56d69872`，在 Apple Silicon、macOS 15.8.2（24H232）、runner 镜像 `20261118.0550.1` 上串行运行 AppKit 和 Metal 两组自写窗口。两组均通过 ScreenCaptureKit 的 `SCScreenshotManager` 单窗口截图取得被完全遮挡后的新内容，并完成冻结反例、遮挡中改尺寸和正常退出检查。

这证明该环境中的独立采集路线可行。产品后端、用户授权流程与便携包采集尚未接入；不能将本结果写成 macOS 游戏采集已交付。

## 方法与原始数据

目标窗口、遮挡窗口和采集器各在独立进程运行。目标绘制可从 PNG 独立解码的计数图；遮挡窗口完全不透明。采集过滤器绑定目标窗口，目标截图不能用整屏图替代。另取两张整屏图，只用于核查遮挡覆盖。

| 阶段 | AppKit PNG 计数 | Metal PNG 计数 | 判定 |
| --- | --- | --- | --- |
| 可见 | 10、15、19、24 | 10、14、18、22 | 连续增长 |
| 完全遮挡 | 35、39、44、49 | 23、34、39、42 | 连续增长 |
| 停止目标更新 | 52、52、52、52 | 42、42、42、42 | 增长判据拒绝冻结反例 |
| 遮挡中改为 400×240 | 62、67、72、77 | 45、53、57、60 | 尺寸与连续增长均通过 |

两组各 16 张目标图、2 张桌面图，共 36 张 PNG 均已独立回读。窗口层次、完整矩形覆盖和目标端遮挡状态均符合条件；每组普通遮挡区域检查 57,600 像素，放大后检查 96,000 像素，遮挡色不符像素均为 0。图片总览也已目视检查，目标区域避开 Dock。

两组权限预检与可共享内容查询均成功；采集前后前台进程一致。目标与遮挡进程正常退出，父进程退出码为 0，看门狗未终止残留进程组，也未强杀。Metal 组另核对 GPU 命令完成与无 GPU 错误；绘制提交不能单独代替采集 PNG 的增长证据。

每组记录 17 次截图调用。AppKit 耗时中位数 22.05 ms、范围 12.92–175.92 ms；Metal 中位数 27.21 ms、范围 12.59–124.46 ms。这是当前短探针的调用测量，不能换算为产品帧率或长期性能保证。

## 失败记录与判据修正

早期 `SCStream` 连续流在遮挡后只收到 idle 状态，缓存计数、完整帧序号与时间戳停止增长。AppKit 图层绘制、显式事务提交、Metal 绘制及取证顺序对照均未解除该环境中的冻结；停止连续流后，单窗口截图能取得递增内容。因此本阶段选截图路径继续验证，不推断所有系统或应用的连续流都不可用。

run-11 的放大窗口露出遮挡层 50 像素；run-12 虽通过窗口矩形检查，但桌面图中的目标区域被 Dock 干扰。两批均保留原始结果，不计为完整验收通过。当前实现补齐窗口矩形、目标状态及实际像素覆盖检查，并将窗口整体上移；云端遇到覆盖不足直接失败。本地测试包含覆盖不足必须拒绝的反例，未放宽增长或画面门槛。

## 复核与回归

下载上述运行的 `macos-arm-capture-probe-appkit` 和 `macos-arm-capture-probe-metal` 产物后，分别执行：

```text
python -X utf8 -B tools/verify_macos_capture_probe.py <产物目录>
```

本地聚焦检查 13 项通过。最终全仓命令为：

```text
python -X utf8 -B -W error::ResourceWarning -m unittest discover -s tests -p test_*.py
```

该次运行设置 `PYTHONPATH=tests`，临时目录指向任务专用目录；933 项中 619 项执行通过、314 项按环境条件跳过，零失败，耗时 187.925 秒。实验分支未包含另一工作区的公共采集层新增测试，不能与公共层 937 项的历史结果混用。材料收口 19 项通过；工作区保护检查说明了此前已按用户要求更改的 arm64 优先级段落，公共采集层其余内容保持一致。

原始日志、二进制、36 张 PNG、独立解码结果与 SHA-256 清单保留于本地 `<macOS采集验证目录>/run-13/`、`closure.json` 和 `regression-result.json`；失败现场保留于各历史 `run-*` 目录，不随源码入库。

## 后续与边界

推荐的正式接入形态见[接入设计](MACOS-ARM-CAPTURE-DESIGN.md)：随包 Swift 辅助进程使用单窗口截图，Python 适配现有公共采集会话。该设计待审阅批准，尚未实施。

尚未验证首次授权、拒绝与撤销、普通用户桌面、真实游戏、最小化、长期稳定性、macOS 14、Intel、产品冻结包及人工标记的端到端链路。Linux X11 独立探针结果保留，正式接入与 Wayland 后排。此次只运行自写窗口；游戏、存档、用户输入与系统权限数据库均未改动。

系统 API 依据为 Apple 的 [SCScreenshotManager](https://developer.apple.com/documentation/ScreenCaptureKit/SCScreenshotManager)、[单窗口过滤器](https://developer.apple.com/documentation/ScreenCaptureKit/SCContentFilter/init(desktopIndependentWindow:))、[帧状态](https://developer.apple.com/documentation/ScreenCaptureKit/SCStreamFrameInfo/status)及[窗口遮挡状态](https://developer.apple.com/documentation/AppKit/NSWindow/occlusionState-swift.property)文档。框架来自系统 SDK，未新增下载或第三方运行依赖；API 文档不代替上述实测。
