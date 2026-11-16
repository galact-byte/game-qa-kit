# KiriKiri 实验桥来源与许可范围

## 自有代码

`host.py`、`GameQaBridge.tjs`、`struct_data.py`、`tests/kirikiri_fixture/` 及桥测试由本项目编写，按根目录 [MIT 许可证](../../LICENSE)提供。共用监督器和取图工作进程由本项目既有 Emuera 宿主提取，未复制上游引擎实现。桥只调用公开 TJS API，不提交反编译代码、不重编译或修改原 EXE。

## 验证组件

| 项目 | 已核对值 |
|---|---|
| 运行时 | KiriKiri 2.32.2.426，Windows x86 |
| 固定 EXE SHA-256 | `8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11` |
| 来源 | 本机合法持有的候选游戏包中的固定运行时；只复制 EXE 与自写工程验证 |
| 对应上游 commit／revision | 未核实，不能以版本资源推定 |
| 此固定构建的完整许可证与修改记录 | 未核实；不分发该 EXE、嵌入库或商业内容 |

机器可读记录见 [source-manifest.json](source-manifest.json)。本地源路径、七个原文件前后摘要与运行证据仅留验证目录，不写进源码。固定摘要是兼容约束，不是再分发许可证明；本仓库 MIT 不扩展到原运行时或反编译材料。

## 接口参考

以下为公开接口语义参考，不代表该固定构建的完整源码或许可来源：

- [Window.postInputEvent](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_Window_postInputEvent.html)：通过原事件队列投递输入。
- [System.getKeyState](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_System_getKeyState.html)：单参数按键查询。
- [Window.innerSunken](https://krkrz.github.io/krkr2doc/kr2doc/contents/f_Window_innerSunken.html)：自写夹具关闭客户区内凹装饰。
- [Array.load／save 与 saveStruct](https://krkrz.github.io/krkr2doc/tjs2doc/contents/array.html)：文本行读取与结构数据输出，不能将 load 当成执行脚本。
- [Dictionary.saveStruct](https://krkrz.github.io/krkr2doc/tjs2doc/contents/dictionary.html)：目标观察输出；本固定构建通过 `incontextof` 绑定对象调用。

实机接口可用性由自写测试确认，不从文档推断输入、松键或画面成功。未来若更换运行时、引入第三方插件或打包 EXE，须重新核实固定来源、revision、许可及测试范围。
