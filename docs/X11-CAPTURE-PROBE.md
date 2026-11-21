# Linux X11 遮挡采集探针

此探针验证 XComposite 命名 pixmap 在独立 Xvfb 测试屏幕中的行为，不接入 GameQA 的观察入口，不发送键鼠输入。它只操作自写目标窗口和遮挡窗口。

## 运行与证据

复用环境已有的 Python 3、Xvfb、libX11 与 libXcomposite，无需安装 Python 包。在 Linux 下执行，输出目录必须不存在：

```bash
xvfb-run -a -s "-screen 0 1024x768x24 -nolisten tcp +extension Composite" \
  python3 -B tools/probe_x11_capture.py --output /tmp/gameqa-x11-probe-new
```

也可以运行独立工作流 `x11-capture-probe.yml`；探测分支的相关代码推送会触发单个 Ubuntu 22.04 x64 任务，不调用发布工作流。任务限时 8 分钟，探针限时 60 秒；成功或失败均上传证据，保留 14 天。

目标、遮挡和采集器为三个独立进程。目标每次绘制递增计数、16 位黑白计数条和四角颜色标记。采集器通过 `XCompositeRedirectWindow` 与 `XCompositeNameWindowPixmap` 读取离屏图像，不从根窗口裁切图像充当目标截图。另读根窗口，仅验证目标区域在桌面上确实全部被遮挡。

依次检查：

1. 目标可见时，5 次采集中的计数严格递增。
2. 另一进程完全遮挡目标后，8 次离屏采集中的计数继续严格递增，每次根窗口目标区域均为遮挡色。
3. 目标停止更新后，4 次采集计数保持不变，同一增长判据必须拒绝这组静止帧。
4. 保持遮挡，将目标从 320×180 改为 400×240，重新取得命名 pixmap 后，5 次采集中的计数继续严格递增。
5. 释放采集 pixmap 和重定向，再让两个 fixture 正常退出，记录退出码及是否强杀。

`result.json` 保存环境、Composite 版本、窗口身份、每次计数、采集时间及清理结果；`samples.jsonl` 逐次写入观察值。每段首尾 PNG 和遮挡时的根窗口区域 PNG 用于独立复核。`target.log`、`cover.log` 和工作流的 `probe.log` 保留失败诊断。

## 已验证结果

[云端运行 44930804334](https://github.com/galact-byte/game-qa-kit/actions/runs/44930804334) 基于提交 `6f343256dfef1253b7caebd53a878ed7c3419167`，任务通过。实际镜像为 `20261116.365.1`，Composite 版本为 0.4。

| 阶段 | 观察次数 | 目标图中计数 |
| --- | ---: | --- |
| 可见 | 5 | 6、10、14、18、21 |
| 完全遮挡 | 8 | 29、34、38、42、46、50、55、59 |
| 停止更新对照 | 4 | 60、60、60、60；增长判据拒绝 |
| 遮挡中改变尺寸 | 5 | 63、70、75、80、85 |

22 次观察首尾间隔约 3.86 秒；其中 17 次遮挡观察的根窗口目标区域均为 100% 遮挡色。两个 fixture 均以退出码 0 正常退出，无强杀。本地下载后独立解码 8 张目标 PNG 的计数条，并逐像素核对 3 张根窗口 PNG，11 张图均与记录一致。原始证据、摘要与对照图留存于本地 `<X11探针验证目录>/`。

本地另检查 Python 语法、5 组增长／停滞／倒退输入、PNG 编码回读、Windows 拒绝运行、工作流 YAML、固定 action 提交与空白错误；原有打包相关回归 4 项通过。这些本地检查不代替上述原生采集实测。

## 验收边界

本次证明 Ubuntu x64 云端 Xvfb／XComposite 能采到被遮挡自写 CPU 绘制窗口的新画面。它不证明真实游戏、GPU／OpenGL／Vulkan 画面、窗口管理器重定向冲突、长期运行、Linux arm64、Wayland 或 macOS 已通过；也没有启用产品的 Linux 观察功能。窗口关闭、连接中断与权限撤销的产品生命周期需在正式后端接入时继续验证。macOS ScreenCaptureKit 和 Wayland portal／PipeWire 仍需分别探测。
