# Bakin 固定构建桥的来源与许可

本目录的 `InputState.cs`、`ChannelState.cs`、`RuntimeBridge.cs`、`PreparedLaunch.cs`、`PatchTool.cs` 以及 Python 宿主、准备器、通道与会话代码为本仓库代码，沿用仓库根目录许可。商业游戏程序、解包运行时、资源和存档不随源码或主包分发。

补丁工具在本机验证时使用既有的 Mono.Cecil 二进制，不自动下载或安装依赖：

| 项目 | 核验值 |
| --- | --- |
| 上游 | <https://github.com/jbevain/cecil> |
| 上游版本 | `0.10.4` |
| 标签对象 | `0d02a1cbf4df463ac2e1e5d2a21b5a5ebf0aeb52` |
| 上游提交 | `98ec890d44643ad88d573e97be0e120435eda732` |
| 实际程序集 | `Mono.Cecil, Version=0.10.4.0, Culture=neutral, PublicKeyToken=50cebf1cceb9d05e` |
| 实际文件 SHA-256 | `7ae470288fff4a402899c254d0a76cefef55877f5c54f96e83c797cc5bb6e2f6` |
| 原始许可 | MIT，见 [third-party/Mono.Cecil.LICENSE.txt](third-party/Mono.Cecil.LICENSE.txt) |

版本与公钥来自实际程序集元数据；标签及提交来自上游 GitHub API。上述版本匹配不等于已证明该二进制由此提交可重现构建，当前记录不作该保证。该 DLL 是沿用先前预检工具的本地文件，不纳入本次源码变更；发行时若携带它，应同时携带原始版权与许可，不得宣称其打包来源已经完成可重现构建核验。

许可原文取自 <https://raw.githubusercontent.com/jbevain/cecil/0.10.4/LICENSE.txt>。上游版本查询：<https://api.github.com/repos/jbevain/cecil/git/ref/tags/0.10.4>，标签对应提交查询：<https://api.github.com/repos/jbevain/cecil/git/tags/0d02a1cbf4df463ac2e1e5d2a21b5a5ebf0aeb52>。
