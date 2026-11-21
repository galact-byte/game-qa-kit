# Metal GPU 像素读回诊断

## 目的与范围

默认采集产品链仍存在重复计数。本诊断只为区分 GPU 实际绘制内容与系统采集输出，不修改产品默认采集逻辑，不替代原验收组。

在自写夹具的环境变量中显式设置 `GAMEQA_PROBE_GPU_READBACK=1` 后，Metal 视图允许纹理读取，在同一命令缓冲区中将 drawable 中间行复制到共享缓冲区。命令成功完成后保存原始 BGRA 字节、对应计数、行宽及命令状态；最多记录前 256 个计数。没有 CPU 等待 GPU 完成。

诊断增加纹理复制、CPU 文件写入，并关闭夹具的 `framebufferOnly`。这些开销和 drawable 配置差异可能影响时序，因此诊断组无论通过与否，都不能覆盖默认组的结果。

## 独立核验

```text
python -X utf8 -B tools/verify_metal_readback.py <诊断组证据目录>
```

核验器直接解码原始字节，与每条提交计数比较，同时拒绝旧像素、截断数据、无效像素、越界路径、失败命令及缺失／失败的目标状态。输出的 `capture_passed` 固定为 false，表示此脚本只核对 GPU 纹理，不判断产品截图验收。

macOS arm64 产品工作流保留源码／冻结两组默认 Metal 验收，分别追加显式读回诊断与独立解码；不再重复已被否定的 async stdin 对照。所有原增长、截图期限、黑白帧及退出判据保持。

## 当前验证

本地先红后绿日志保留于实施目录。受影响聚焦回归 81 项通过，工作流 YAML 解析及默认组未开启诊断的检查通过。随后云端运行 `44959967994`（源码 `d72a69a`）已完成 Swift 编译和源码／冻结包诊断：原始 BGRA 分别独立解码 32／33 条，均与提交计数相符。默认源码遮挡截图为 `16、17、17、19`，默认冻结为 `18、19、19、20`；诊断组分别为 `17、19、22、22` 和 `18、20、20、31`。四组均未满足原连续增长判据，不能以 GPU 读回通过替代产品验收。

新证据证明诊断组纹理中有对应的新内容；仍不能在 drawable 呈现与系统采集输出之间确定唯一根因。所有呈现回调的 `presented_time` 仍为 0。证据为 `<macOS产品实施目录>/cloud-24/independent-metal-readback-review.json`，默认组与诊断组分别保留，不重复已否定的 stdin 对照。

Apple 对 [`MTLDrawable.presentedTime`](https://developer.apple.com/documentation/metal/mtldrawable/presentedtime) 的定义明确指出，0 既可能表示尚未呈现，也可能表示该帧被丢弃；单独这个值不能确定帧被丢弃或唯一故障原因。夹具当前的事务分支已按 [`presentsWithTransaction`](https://developer.apple.com/documentation/quartzcore/cametallayer/presentswithtransaction) 要求先提交、等待排程，再直接调用 drawable 的 `present()`，默认分支仍采用命令缓冲区呈现。该实现无需再次修改；cloud-19 的事务对照遮挡计数 `14、14、23、23` 已失败，flush 对照也已在 cloud-18／19 失败，不重复运行。

目标原生记录报告的 GPU 为 `Apple Paravirtual device`。这是当前云端环境的实际属性，不能据此认定虚拟 GPU 是根因；也不能以本地单元测试代替物理 GPU／普通授权桌面证据。下一层需要能区分呈现与系统采集的新增原生观测，或具备对照条件的真实 arm64 桌面，不能仅重跑已有开关。

同批 AppKit 源码／冻结产品链独立核对 33／37 张 PNG，原生故障链各 11 项、各回收 12 个辅助进程。当前源码完整回归 966 项，652 通过、314 环境跳过、零失败，运行 198.089 秒；跳过项与未通过的 Metal 原生链不计入支持结论。

## 回退

取消诊断组环境变量即可禁用读回；默认验收组未启用该开关。诊断只操作自写窗口与新建证据目录，没有游戏、输入、存档或权限数据库写入。
