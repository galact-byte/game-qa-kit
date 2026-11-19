# Artemis 脚本桥来源与许可

## 本项目代码

`GameQaBridge.lua`、Python 通道／宿主／准备器／会话、自写 IEScript 场景、配置和测试由本项目编写，按仓库根目录 [MIT 许可证](../../LICENSE)提供。桥使用固定运行程序已存在的 Lua 输入覆盖和正常查询接口，不包含反编译的引擎实现，也不修改 EXE 或注入 DLL。双向命令与消费文件格式为本项目协议。

## 本地组件

| 组件 | 本次固定身份 | 分发范围 |
| --- | --- | --- |
| `HaitokunoShoudou.exe` | SHA-256 `4480a20da5857ea329036c3031e671241f5efb73e9f75bf07412834d3c0a60b2`，本机持有的《背徳の衝動》ver1.10 目录中的 x64 运行程序 | 不随仓库、测试资产或便携包分发 |
| `d3dcompiler_47.dll` | SHA-256 `efbdbbcd0d954f8fdc53467de5d89ad525e4e4a9cfff8a15d07c6fdb350c407f`，同一目录中的配套组件 | 不随本插件分发；不得将本项目许可扩展到该文件 |

可机读来源与摘要见 [source-manifest.json](source-manifest.json)。该游戏版本用于描述本地材料来源，不代表已确认通用 Artemis 引擎版本。没有可核实的引擎源代码 commit／revision，也没有据此确认这两个固定文件的再分发授权；使用者必须自行提供合法持有的匹配组件。摘要登记是兼容检查，不是许可认证。

准备器仅复制上述两个文件至新自写副本，商业脚本、`root.pfs`、原图像和存档均不复制。内部静态核对、反编译产物及游戏路径只保存在本地 `<Artemis预检目录>/preflight-01/input-static-01/`，不提交源码或随包提供。

## 参考资料与未引入组件

[Artemis 官方功能说明](https://www.ies-net.com/?page_id=24)用于查阅引擎能力；实际参数、时钟与输入语义以固定构建取证和自写实测为准。曾查阅 GARbro、pfs-rs、pfs_upk 的公开说明了解资源格式，未复制其代码或将其作为插件依赖。REA／IDA 与 capstone／pefile 只用于本地分析，不是插件运行依赖，不随本插件附带。

本插件没有新增下载或安装步骤，不新增第三方桥代码。原有 Proto Input／EasyHook 等其他插件的来源与许可证继续由各自目录维护，本插件未复制或调用它们。
