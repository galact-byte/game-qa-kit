# Metal 采集缓冲与图像转换边界诊断

## 要回答的问题

已有 [GPU 读回与呈现诊断](METAL-GPU-READBACK.md) 确认：GPU 纹理能包含新计数，系统采集输出仍会重复；冻结后重新露出能取得最终内容。这些结果尚不能唯一确定呈现、合成或采集责任。

现有 sample-buffer 探针把 `CVPixelBuffer` 经 `CIImage`、`CIContext.createCGImage` 转为图像，未保留同一缓冲的 CPU 原始像素。本诊断增加这个边界的直接对照，不更换过滤参数，也不重复事务、flush、stdin、尺寸、autorelease 或显示同步对照。

## 官方资料与可确定的边界

- Apple 的 [`CVPixelBufferLockBaseAddress`](https://developer.apple.com/documentation/corevideo/cvpixelbufferlockbaseaddress(_:_:)) 要求 CPU 访问像素前锁定缓冲，访问后解锁；只读锁和解锁必须使用相同的 `readOnly` 标记。该文档同时说明，GPU 访问不需要锁定，锁定可能影响性能。因此读回组可能改变同步和时序，不能把读回后的改善当成产品修复。
- Apple 对 [`MTLDrawable.presentedTime`](https://developer.apple.com/documentation/metal/mtldrawable/presentedtime) 的定义是 drawable 实际显示的主机时间；值为 0 可以表示尚未呈现或帧被丢弃。已有证据中的 0 无法提供唯一归因。
- 本仓库的 `CaptureHelper.swift` 默认使用 `SCScreenshotManager.captureImage`；新增对照使用 `captureSampleBuffer`。本诊断的结论限于后者，不能直接外推默认产品路径。

没有证据证明增加日志字段、使用 Instruments 或更换云端机器就必然能确定根因；本轮不把这些可能性写成已验证能力。

## 实施与运行

`tools/probe_macos_capture.swift` 新增显式环境变量 `GAMEQA_PROBE_RAW_PIXEL_PAIRS=1`，仅允许与 `GAMEQA_CAPTURE_MODE=snapshot`、`GAMEQA_PROBE_SAMPLE_BUFFER=1` 一起使用。默认不启用。

每次采集持有同一个缓冲，在 Core Image 转换前复制中央 BGRA 行；保存转换后的 PNG，再次锁定并复制同一行，保存前后原始字节、PNG、实际尺寸、stride、采样位置与 mach 时间。仅接受有界、非平面的 BGRA 缓冲，最多 64 组；不把 JSON 自报计数作为像素证据。PNG 写入之后才读第二行，覆盖转换及编码期间的缓冲变化。

手动入口沿用主线已有的 `.github/workflows/macos-arm-product.yml`，新增默认关闭的 `buffer_boundary_only` 输入；开启时仅调用独立的 `.github/workflows/macos-metal-buffer-diagnostic.yml`，产品与旧帧时间任务均跳过。获批后可对实验分支运行：

```text
gh workflow run macos-arm-product.yml --ref experiment/metal-buffer-boundary -f buffer_boundary_only=true
```

独立诊断只运行四个顺序用例：AppKit 无读回、AppKit 同缓冲读回、Metal 无读回、Metal 同缓冲读回。无读回组是新增观测的配套控制；不运行旧参数矩阵，不构建产品包。

沿用原连续增长、冻结反例与 3 秒期限。原重复帧及冻结延迟症状记录为诊断发现；其他错误、证据不足、焦点记录缺失或改变、超时、残留进程、强杀会停止批次。读回组固定 `diagnostic_only=true`、`passed=false`；工作流成功只表示规定的诊断运行结束，不表示 Metal 采集通过。

取回原始证据后，在已具备项目 Pillow 依赖的本地环境分别运行：

```text
python -X utf8 -B tools/verify_macos_buffer_pairs.py <证据目录>/appkit-pairs
python -X utf8 -B tools/verify_macos_buffer_pairs.py <证据目录>/metal-pairs
```

云端不新增依赖。复核器独立解码两份 BGRA 行及 PNG，拒绝缺失、截断、非法路径、尺寸／格式错误、无效计数像素、重复序号及转换期间原始像素变化。输出 `capture_passed` 固定为 false。还需结合原 phase PNG、完整遮挡证据、目标状态及无读回控制组复核；该脚本本身不验收遮挡、增长或产品支持。

## 结果如何解释

| 实际观测 | 能支持的判断 | 仍不能支持的判断 |
| --- | --- | --- |
| 同缓冲前后原始行相同，原始计数与 PNG 不同 | sample-buffer 转换／编码边界出现不一致，值得进一步缩小 | 默认 `captureImage` 路径已有同样根因 |
| 同缓冲原始行与 PNG 一致且共同重复 | 此样本的旧计数已存在于可供 CPU 读取的采集缓冲 | 目标未呈现、WindowServer 或系统采集谁负责 |
| 无读回仍失败，加入 CPU 读回后改善 | 读回／同步／额外写盘改变了实验行为 | 锁定缓冲可作为产品修复 |
| 转换前后原始行不同 | 不能进行稳定的同缓冲归因，复核失败 | PNG 不同即可归咎 Core Image |
| 四组都无法重现旧症状 | 本批没有获得需要定位的失败样本 | 历史失败已修复或云端 GPU 已排除 |

若原始缓冲已经重复且没有其他有效呈现观测，下一步仍需可交互的真实 arm64／物理 GPU 对照。首次授权、撤权和普通桌面体验继续单独验收。

## 原生结果：缓冲区本身已包含重复计数

云端批次 [`45118415633`](https://github.com/galact-byte/game-qa-kit/actions/runs/45118415633) 基于 `c1081663cc187158c8d3d1d060fc022381ba11a6` 完成 Swift 编译与四组诊断；产品及旧帧时间矩阵均跳过。独立复核原始 BGRA、PNG、阶段样本、遮挡桌面像素及回收记录，结果如下：

| 用例 | 完全遮挡下的 PNG 计数 | 同缓冲复核 | 原增长判据 |
| --- | --- | --- | --- |
| AppKit 无读回 | `28、31、35、38` | 不读取原始行 | 通过，冻结反例及改尺寸亦通过 |
| AppKit 有读回 | `27、31、33、36` | 17 组，原始行前后相同且计数与 PNG 一致 | 原阶段通过，诊断结果仍固定不可判产品通过 |
| Metal 无读回 | `18、19、19、19` | 不读取原始行 | 失败，停于遮挡阶段 |
| Metal 有读回 | `21、22、22、22` | 9 组，原始行前后相同且计数与 PNG 一致 | 失败，停于遮挡阶段 |

共独立解码 26 组缓冲的 52 份原始行及配对 PNG，并核对阶段／失败后的采集 PNG；原始工件包含 88 张 PNG。四组桌面目标区域共 422,400 像素均为遮挡色，目标阶段记录确认完全遮挡。最大截图耗时约 385.808 毫秒，小于原 3 秒期限；四组退出正常、无需看门狗终止或强杀，子进程清理完成。焦点只核对已有端点记录，不宣称持续监测。

Metal 读回组遮挡样本的目标计数为 `30、32、36、38`，采集原始行与 PNG 为 `21、22、22、22`。因此本批重复已存在于 CPU 可读取的采集缓冲，未发现 Core Image／PNG 转换造成计数分歧；CPU 读回没有消除症状。结论限于本批 sample-buffer 通路，不能证明默认 `captureImage` 的唯一根因，更不能判定 WindowServer 或虚拟 GPU 有错。Metal 两组均未进入冻结与改尺寸阶段，这些阶段不算通过。

独立结果为 `<路线图续推目录>/metal-buffer/cloud-01/independent-buffer-review.json`，原文件摘要清单为同目录 `evidence-manifest.json`；本地原始证据及对照图不随源码提交。当前云端 GPU 为 `Apple Paravirtual device`。若没有进一步能判别呈现／合成责任的实际观测，下一项需要物理 GPU／可交互 arm64 Mac 对照；不重跑本批或旧参数矩阵。

## 验证状态与回退

本地五模块聚焦检查 33 项通过、零跳过；本批 Swift 编译及原生诊断已完成，Metal 产品采集仍未修复。本次独立实验分支推送与受限云端诊断已获授权，不涉及正式发布或主线合并。

取消新环境变量即可禁用读回；撤回本次探针差异、工作流入口、复核器、测试及本文即可回退。没有游戏、存档、权限数据库或产品协议迁移。
