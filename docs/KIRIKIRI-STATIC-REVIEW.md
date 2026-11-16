# KiriKiri 固定样本静态核查

本轮只交付候选识别与静态核查；没有启动样本，没有输入、截图或控制兼容结论。

## 识别范围

`inspect_game` 同时要求目录中的 `.xp3` 文件具有 19 字节完整头（11 字节 XP3 签名与 8 字节索引偏移字段），以及某个 `.exe` 文件前 64 KiB 中同时具有 MZ 头和 `@TVP(kirikiri) for win32` 标记；扩展名大小写不敏感。

返回 `kirikiri_candidate`、`adapter=null`、`control_verified=false`。只读取文件，不解析或解包 XP3 索引，不加载运行时，不把头字段或版本字符串当成有效归档、完整 PE 或控制认证。没有外置 XP3 或采用其他标记布局的构建不在此次判定范围内。

## 固定样本所见

- 本机盘点中的一个候选带 `data.xp3`，EXE 版本资源为 KiriKiri 2.32.2.426／x86，运行时 SHA-256 为 `8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11`。
- 同目录带 MTool、转区组件与既有 `savedata`；不能把整包当作干净引擎，也不能将同版本号视为未修改上游。原 EXE、商业资源和存档不随源码分发。
- 只读提取的 KAG 初始化脚本标注 `3.32 stable rev. 2`。`MainWindow.tjs` 接收原键盘事件，并使用 `System.getKeyState` 再查询状态。后续必须验证输入确实经过原处理器且状态查询一致，不能仅凭投递消息认定消费。
- `saveDataLocation` 初始取 `System.dataPath`，没有冒号时另拼 `System.exePath`；日志也定向到该保存目录。正常存档、缩略图及系统／已读状态均使用这个保存位置。

## 保存路径证据与阻碍

REA 对固定 EXE 的静态反编译显示：

- `0x60FA38` 查询 UTF-16 `-datapath`（`0x6F6EA6`），将取值交给 `0x67AA34` 展开，再发布为引擎数据路径；初始化还读取程序对应的 `.cf` 和保存位置的 `.cfu`。
- `0x67AA34` 对空值使用 `$(exepath)\savedata`，支持 `$(exepath)`、`$(personalpath)`、`$(appdatapath)` 与 `$(vistapath)` 展开。仅看到相对的 `savedata` 或复制了游戏目录，不能证明所有写入已隔离。
- `0x67AEF8` 复用同一展开函数生成 `.cfu` 路径。
- EXE 文件偏移 `0x396E00` 的 `XOPT_EMBED_AREA_` 中列有 `datapath` 排除项。该项对启动参数和已有配置的最终优先级尚未证实；未把字符串存在本身当作“命令行覆盖已成功”或“覆盖必然不可能”的证据。

下一步先查清排除项／参数优先级，核对只复制运行时与自写 TJS 工程时的配置、日志、保存和插件写入边界，再决定最小独立桌面探针。探针范围和恢复方式须在执行前列清，不运行原商业脚本或原存档。当前没有探针、正式桥或待实施的加载器设计。

## 证据与恢复

原始提取清单、转码脚本、REA evidence bundle 及地址对应反编译片段保存在 `<KiriKiri验证目录>/static-review/`；红阶段及入口回归日志分别为 `engines-red.log` 和 `identification-regression.log`，最终受影响回归为 `precommit-regression.log`（73 项通过）。交付核对覆盖本地链接与固定样本只读摘要；本地路径及商业产物不提交源码。

静态函数证据包括 `ev_8258b016c6de54bdb0ff6e4c5c7c623f848d45899cd88d409a341cabc9ee7be5`（`0x60FA38`）、`ev_a0b69deb91887162275c3aa427d65a42b9340af935ee0aae8bd5fb906e0c7850`（`0x67AA34`）、`ev_29989332850eee8b3d373cf0713c5c9d8460b35633e7a9435e657c525eea48e3`（`0x67AEF8`）。这些是当前 IDA 数据库的静态推导，不是运行观测；数据库修订与完整调用覆盖仍未知。

撤回本次识别代码、测试和文档即可回退，不涉及游戏数据迁移或存档还原。
