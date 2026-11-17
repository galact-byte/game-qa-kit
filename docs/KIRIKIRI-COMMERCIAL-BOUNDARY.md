# KiriKiri 商业 KAG 样本写入边界审查

本轮仅完成只读审查与后续副本方案；未启动商业脚本、部署控制桥、修改原存档或验证商业玩法。KiriKiri 的产品识别仍为 `adapter=null`、`control_verified=false`。现有实验桥只接受自写 Window，不能直接用于本样本。

## 样本与证据范围

复用既有静态提取与反编译，不重跑路径／输入探针。原包含 1,138 个文件，共 1,802,169,925 字节；`data.xp3` 有 6,122 个归档条目，其中 `.ks` 55 个、`.tjs` 23 个。提取覆盖 275 个文本文件、9,847,748 字节，转码错误为零；命中表只是检索辅助，不代表调用可达性证明。

| 对象 | SHA-256 | 只读所见 |
| --- | --- | --- |
| 原游戏 EXE | `8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11` | 固定 KiriKiri 2.32.2.426／x86，与自写工程所用构建一致；不等于商业兼容已验 |
| `data.xp3` | `b3c3d86d6e6a5c34100e1e2d8a6cde59ca7576fe1c6d8f0f0ff7c100a7ab045e` | 商业 KAG 脚本与资源；不分发提取正文 |
| `wuvorbis.dll` | `302b1eb97ed0f6110904e84c11bf7436e4cdddd414f4d24d105aba687f32b47f` | 商业脚本显式链接的原生音频插件；另有读入的 TJS／KS 插件 |

原包还带 MTool、转区组件、翻译数据和已有保存目录。不能整包复制后认定为干净测试环境；`MTool_Game.exe` 与原游戏 EXE 在本基线中摘要相同，也不据此为其他附带组件背书。

## 脚本写入与触发条件

下列行号均指 `<KiriKiri验证目录>/commercial-boundary-01/text/data.xp3/` 中的 UTF-8 提取正文；原始商业代码留在本地。

| 写入或路径来源 | 直接证据 | 边界判断 |
| --- | --- | --- |
| 保存目录及日志 | `system/MainWindow.tjs:38,286-292`：`saveDataLocation` 初始为 `System.dataPath`；无冒号则拼接 `System.exePath`；主窗口设置 `Debug.logLocation` | 常规路径可以随固定运行时的 `-datapath` 转向，但单靠字符串拼接不构成系统写入隔离 |
| 系统配置、全局变量 | `system/MainWindow.tjs:1146-1184`：`saveSystemVariables()` 使用 `saveDataLocation + dataName` 生成 `sc.ksd`／`su.ksd`，再 `Dictionary.saveStruct` | 受 `readOnlyMode` 限制；读取书签信息也可能触发系统变量保存，不能只监测“保存”按钮 |
| 普通书签及缩略图 | `system/MainWindow.tjs:1758-1763,1820-1900`：槽号生成 `.bmp`／`.kdt`，特殊编号生成 `.ksd`；`saveLayerImage` 与 `saveStruct` 写同一文件路径 | 本样本 `system/Config.tjs:79,88,112,306` 配置为 `freeSaveDataMode=false`、`saveThumbnail=true`、`dataName="data"`、48 个书签。原包 `data0.bmp` 对应内部编号 0，不能拿文件编号代替面向用户的槽号；本轮未改任何槽 |
| 已读位置 | `system/MainWindow.tjs:2232-2266` 的 `setRecordLabel`／`incRecordLabel` 更新 `sflags`；`1182-1184` 将它保存到 `su.ksd` | 与系统变量共享保存路径；没有独立的已读位置文件写入证明 |
| 自由保存 | `system/MainWindow.tjs:2031-2093`：`Storages.selectFile` 返回的 `selectdata.name` 直接传给 `saveBookMarkToFile`；初始目录还可能复用上次保存名 | 当前配置关闭此模式，未证明发生越界写入；若启用或由脚本调用该函数，`-datapath` 不会约束文件选择器结果，必须在真实写入入口验证路径 |
| 配置迁移窗口 | `system/Initialize.tjs:153-158` 加载 `UpdateConfig.tjs`；`system/UpdateConfig.tjs:8-10,79-90` 创建 `Pad`，文件名来自 `Storages.getPlacedPath("Config.tjs")`，提示用户右键保存 | 是有条件的交互写入路径；不能写成启动必然写配置。`Pad` 保存及另存路径不由常规存档变量限制；初批应拒绝迁移／编辑窗口，而不是允许它默认保存 |
| 异常日志与环境配置 | `system/Initialize.tjs:25` 的 `Debug.logAsError()`；固定 EXE 既有 `-datapath`／`.cf`／`.cfu` 静态与自写探针，见[先前核查](KIRIKIRI-STATIC-REVIEW.md) | KAG 正常日志位置不证明初始化早期、异常或原生组件全部受控；环境配置曾在自写工程保存目录中实证，不外推为商业完整审计 |
| 游戏后置重写 | `system/AfterInit.tjs:47-54,57-77,103` 包装书签保存、确认与系统变量保存 | 包装继续调用原保存实现，没有消除自由文件名风险；后续桥不得跳过这些原处理器 |
| `SaveAnywhere.ks` | `others/SaveAnywhere.ks` 的插件回调及书签数据记录 | 名称表示可保存的剧情位置扩展，不能仅凭名称认定为任意文件路径保存；需与上述文件选择器分开判断 |

对提取文本检索了脚本加载、`Plugins.link`、`Storages`、`saveStruct`／`saveLayerImage`、文件流、注册表、`shellExecute` 和绝对／父目录路径等模式。检索没有命中不等于运行期不存在动态构造或原生调用。

## 原生插件：已知与未证实

商业脚本 `scenario/first.ks:48` 显式加载 `wuvorbis.dll`；`system/MainWindow.tjs:5051` 的标签处理器执行 `Plugins.link(elm.module)`。该 DLL 的 PE 导入与导出落在 `native-pe-review.json`；原生链接函数的既有 REA 反编译证据落在 `native-v2link.json`，证据 ID 为 `ev_701fa1b4ee85e89448b1be22bf0f88008d8ccb5ffb2a9fc27ad252919efc21ec`，分析入口 `0x110107b0`。

- DLL 导入 `WriteFile`、`FlushFileBuffers`、`SetFilePointer`、`CloseHandle`，并带 `GetProcAddress`／`LoadLibraryA`。没有静态导入 `CreateFile*` 不能证明不能写文件；它还可能使用宿主提供的句柄、导出接口或动态解析。
- 导入表不能区分可达音频逻辑与 CRT 残留，也不能证明这些写调用发生过。本轮不将它记为已证实越界写入，亦不将它列入已证明只读的白名单。
- 已有 V2Link 分析只覆盖链接入口，不能替代音频解码回调与所有异常分支的完整调用图。补查按名称交叉引用时，当前 REA／IDA provider 返回 `capability_unavailable`（`list_names` 未声明）；保留此限制，不以空结果当作无调用。
- EXE、插件和归档的上游完整 revision／再分发许可仍未证实；只提交本审查结论，不提交游戏文件、提取正文、反编译产物或原存档。

## 建议的副本范围与还原方式（待批准，未实施）

建议先补齐写入入口约束与原生插件调用边界，再申请一次独立桌面的商业启动验证。当前结论不足以直接启动商业样本。复制目录、重定向环境变量或独立 Desktop／Job 都不等同文件系统沙箱；若要采用操作系统隔离方案，需另行确定架构与授权，不在本轮变更安全配置。

1. **副本输入**：只从原目录读取原游戏 EXE（3,768,320 字节）、`data.xp3`（1,074,749,078 字节）与 `wuvorbis.dll`（200,704 字节），合计 1,078,718,102 字节。拟建于 `<KiriKiri验证目录>/commercial-run-01/game/`，逐文件核对摘要。不复制 MTool、Locale Emulator、翻译文件、`.cf`／`.cfu` 或既有 `savedata`；不修改 EXE 或重打 XP3。是否缺少附加资源只能由后续获批批次判断，不能称这三个文件已通过完整启动验收。
2. **运行数据**：新建本批 `savedata`、会话证据、`appdata`、`localappdata` 与 `tmp`；固定命令行 `-datapath` 指向本批绝对保存目录，仅给本进程设置相关环境变量。启动前检查可用空间、路径规范化、无重解析点、无到原目录的搜索路径。环境变量不是写入强制边界。
3. **副本脚本变更**：现有宿主只认仓库自写 `startup.tjs` 并拒绝额外文件。商业 KAG 准备器、加载钩子及保存路径守卫均尚未实现；须先审阅设计。拟只在副本部署加载与守卫脚本，保留原剧情、原输入处理器和保存调用；在最末写入入口检查规范化后的目的路径属于本批目录，拒绝自由保存越界及配置编辑／迁移窗口。具体加载位置与优先级必须由真实加载链证据确定，不能猜测任意命名的覆写脚本会生效。
4. **原生边界与失败路径**：补齐音频插件回调的写入可达性，或设计经批准的有效写入隔离；TJS 守卫不覆盖原生 `WriteFile`。用自写输入先验证路径穿越、绝对路径、保存对话框、配置迁移、早期错误和写入失败；未通过不启动商业批次。不调整原启动、租约、截图、释放或黑帧判据。
5. **获批后的首批**：仅一次新会话，在独立诊断桌面／Job 中先取有效新图；首图不通过即停手，零动作。随后才按获批动作范围验证正常输入经过原处理器及画面推进；不直接调用剧情跳转、存档函数或任务完成函数。启动与控制验证是后续验收，当前没有其运行证据；批次失败不自动重跑。
6. **存档与还原**：首批不带原存档、不写原槽，不自动同步任何测试文件回原目录；如后续需存读档，另列测试槽、改前备份、改后文件及回退命令。新增测试目录本身是改动清单，证据保留在副本外。结束确认 Job 树退出后复核原目录全文件摘要；回收副本前列完整路径和占用，按批准范围处理，不删除原存档或失败证据。异常时保留现场，不能宣称已还原。

方案尚需老师批准的范围：商业 KAG 加载／守卫与宿主接入设计、三文件副本与新增脚本、一次商业启动及其动作清单。原目录保持只读，安全软件与系统采集不在本方案内。

## 本地复核与交接

证据统一位于 `<KiriKiri验证目录>/commercial-boundary-01/`：

- `original-before.json`：本轮读取前原目录逐文件摘要与大小。
- `scan-summary.json`、`text-manifest.json`、`archive-index.json`、`boundary-hits.json` 与 `text/`：既有提取／扫描记录及正文。
- `native-pe-review.json`、`native-v2link.json`、`native/wuvorbis.dll`：原生导入／导出及链接分析。
- `verify_review.py`、`verification.json`：本轮全原目录摘要复核与文档引用／样本摘要核验。该检查只读原目录，不启动游戏；结果不等于系统范围写入审计。

本轮仅修改审查与交接文档，候选识别、控制协议、桥实现和发布流程均未改。Release 构建仍由老师查看，不查询、不重复触发。下一步先审阅上述副本范围与待实现边界，不重跑已完成探针与调查。
