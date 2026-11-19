# EasyRPG 自写 RM2K 受管入口

只接 `samples/rm2k` 的原样自写场景，以及 `prepare.py` 登记 SHA-256 的 `GameQaEasyRpg.exe`。首次启动先暂停合成输入并取得有效首图；统一 MCP 的恢复、右方向移动和等待走正常游戏输入。没有商业工程或 RPG Maker 2003 的兼容性承诺。

## 使用

在“AI 后台接入”中选择本目录的 `samples/rm2k`，组件目录选择本次验证的独立运行时目录，输出位置选择一个不存在的新目录。准备、启动后先确认首图，再显式恢复输入。`move` 只接受 `direction=right`，`wait` 不产生方向输入；时间单位为 `duration_ms`，上限沿用共用协议的 1,000 毫秒。

组件目录必须含本次登记的 `GameQaEasyRpg.exe`。工具附带的两份原 Player、其他版本或自行重新构建但未登记的 EXE 均会被拒绝。样本增加存档、改图块或修改地图也会拒绝。便携包含插件和样本，独立运行时作为本地组件单独提供，不捆绑到 GameQA 主 ZIP。

独立桌面及目录重定向不等于文件系统沙箱。运行时明确使用新游戏、无 RTP、无音频、软件渲染，日志、配置、保存和临时文件位于新会话目录。原 Player、原样本和源组件逐项复核摘要。

## 构建来源

| 组件 | 固定来源 | 许可 |
| --- | --- | --- |
| EasyRPG Player | `f8e41f43b619413f95847536412b56f85307d378` | GPL-3.0-or-later；全文见 `licenses/EasyRPG-Player-GPL-3.0.txt` |
| EasyRPG liblcf | `9c09529e3de0a4106470082ef48a00d4c3593c33` | MIT；全文见 `licenses/liblcf-MIT.txt` |
| 官方 Windows 依赖包 | `https://ci.easyrpg.org/downloads/windows/toolchain-windows.zip`，本批 SHA-256 `2c2e621e4406bc2444edecffbf95f17ad377a2fbcbcd916f975db71d470e5dc4` | 各依赖保留原许可及 SPDX 元数据；此下载地址会更新，不能只凭地址复用本批身份 |

下载 URL 和归档摘要见本说明及本地 `download-manifest.json`，固定提交与构建参数见组件目录的 `source-manifest.json`。本批链接的非系统依赖为 SDL2、liblcf、ICU、Expat、libpng、zlib、fmt 和 Pixman；实际链接清单及依赖 SPDX 记录保留在本地组件目录中。本机 VS 2022 和 Windows SDK 属于构建工具，不随运行时分发。

在已有、已核对的源码及依赖上构建，不会自动下载：

```powershell
python -m plugins.easyrpg.build_runtime `
  --player <Player固定提交源码目录> `
  --liblcf <liblcf固定提交源码目录> `
  --dependencies <官方包中vcpkg-export目录> `
  --work <不存在的新构建目录> `
  --output <不存在的新组件目录> `
  --generator-instance <本机VS2022目录>
```

`build_compat.py` 只处理固定 0.8 源码与本批新依赖的 C++17／fmt 兼容；`overlay.py` 加入专用输入源、场景更新后的消费确认并关闭独立运行时的失焦暂停。任何补丁位置不唯一即停止，不猜测其他版本的布局。构建输出是独立命名的 EXE、构建清单、Player／liblcf 合并的对应源码 ZIP，以及依赖许可。

每次重建 EXE 摘要可能改变，不能将新文件直接冒充本批组件。须重新登记摘要并复验源码、便携 worker 和失败路径。修改桥代码或共享数据结构时，宿主与运行时需同时升级，重新准备会话，不复用旧会话。

独立运行时受 Player 的 GPL 条款约束。对外分发时须连同该二进制的对应源码、构建配方、来源及许可提供；只有链接或致谢不构成对应源码交付。本批运行时仅保留在本地用于验证，未上传 GitHub Release。

## 已验与未验范围

源码真实 MCP 覆盖正常移动、目标消费、松键后位置稳定、重复命令不重放、暂停／恢复、双会话隔离与回收、篡改拒启、过期请求拒绝、取图失败释放和停帧故障。停帧后不能确认松键时返回未确认，随后由 Job 回收目标。

暂停仅停止合成输入，游戏继续运行。游戏线程的回执与宿主投递回执分开，不用静止截图推断松键成功。输入计时不依赖截图完成；既有租约、取图和释放期限不放宽，首图为纯黑／纯白时不发动作。

2003、商业内容、RTP、音频、菜单、其他方向、存读档、其他 Player 构建、长时间稳定性和真人键鼠并发未验。完整范围及预检记录见 [接入设计](../../docs/EASYRPG-ADMISSION-DESIGN.md)。
