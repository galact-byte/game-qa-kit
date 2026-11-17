# KiriKiri 实验桥来源与许可范围

## 自有代码

`host.py`、`capture_worker.py`、`GameQaBridge.tjs`、`struct_data.py`、`prepare.py`、`build.py`、`Guard.hpp`、`StartupGuard.hpp`、`GuardBridge.cpp`、`GuardHost.cpp`、`guarded.py`、`kag_host.py`、`AfterInit2.tjs`、`KagBridge.tjs` 及自写夹具／测试由本项目编写，按根目录 [MIT 许可证](../../LICENSE)提供。监督器和独立桌面取图复用项目既有代码；原生守卫复用 `plugins/native_input/{WriteGuard,Native,JsonWire}.hpp`，构建显式链接 Windows 的 `bcrypt`／`advapi32`／`user32` 系统库。本 KiriKiri 构建未链接 EasyHook，不应将原生输入后端的 EasyHook 依赖误记为本插件的链接依赖。

## 验证组件

| 项目 | 已核对值 |
|---|---|
| 运行时 | KiriKiri 2.32.2.426，Windows x86 |
| 固定 EXE SHA-256 | `8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11` |
| 来源 | 本机合法持有的候选游戏包中的固定运行时；只复制 EXE 与自写工程验证 |
| 对应上游 commit／revision | 未核实，不能以版本资源推定 |
| 此固定构建的完整许可证与修改记录 | 未核实；不分发该 EXE、嵌入库或商业内容 |

机器可读记录见 [source-manifest.json](source-manifest.json)。本地源路径、七个原文件前后摘要与运行证据仅留验证目录，不写进源码。固定摘要是兼容约束，不是再分发许可证明；本仓库 MIT 不扩展到原运行时或反编译材料。

## 原生输入参考与许可核对

本项目原生输入调查参考了 [Proto Input 固定提交](https://github.com/SplitScreen-Me/splitscreenme-protoInput/tree/e18e06bb345b86540e7464f7355c649bfdd17f38) `e18e06bb345b86540e7464f7355c649bfdd17f38`；本地源码清单、上游 `LICENSE`、`lib/EasyHook/LICENSE` 与 `lib/EasyHook/DriverShared/Disassembler/udis86-LICENSE.txt` 已逐份核对，其完整原文保留在 [THIRD-PARTY-NOTICES.md](../native_input/THIRD-PARTY-NOTICES.md)。该参考快照不能推定 KiriKiri 固定运行时的上游 revision 或许可。原始材料与本轮核验结果只留本地验证目录。

## 接口参考

以下为公开接口语义参考，不代表该固定构建的完整源码或许可来源：

- [krkr2 绘制源码](https://github.com/krkrz/krkr2)中的 `WindowFormUnit.cpp`：绘制请求与延后重画的语义参考；只读核对，没有将引擎代码复制或编译进本插件。
- [Window.postInputEvent](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_Window_postInputEvent.html)：通过原事件队列投递输入。
- [System.getKeyState](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_System_getKeyState.html)：单参数按键查询。
- [Window.innerSunken](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_Window_innerSunken.html)：自写夹具关闭客户区内凹装饰。
- [Array.load／save 与 saveStruct](https://krkrz.github.io/krkr2doc/tjs2doc/contents/array.html)：文本行读取与结构数据输出，不能将 load 当成执行脚本。
- [Dictionary.saveStruct](https://krkrz.github.io/krkr2doc/tjs2doc/contents/dictionary.html)：目标观察输出；本固定构建通过 `incontextof` 绑定对象调用。

实机接口可用性由自写测试确认，不从文档推断输入、松键或画面成功。未来若更换运行时、引入第三方插件或打包 EXE，须重新核实固定来源、revision、许可及测试范围。
