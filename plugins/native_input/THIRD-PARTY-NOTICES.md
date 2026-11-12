# 原生输入后端第三方来源与许可

本目录的输入核心、协议、宿主、守卫、取图和测试由本项目实现；进程内 API hook 通过已有 EasyHook x86 静态库提供。未把 Nucleus 的 GPL 代码复制进入本目录，也不分发上游游戏处理脚本、驱动、服务或游戏文件。

核查与试验参考的 Proto Input 固定快照为 [SplitScreen-Me/splitscreenme-protoInput](https://github.com/SplitScreen-Me/splitscreenme-protoInput/tree/e18e06bb345b86540e7464f7355c649bfdd17f38)，提交 `e18e06bb345b86540e7464f7355c649bfdd17f38`。本机已有的 EasyHook 源码来自该快照的 `lib/EasyHook/`；外部构建使用 `EasyHookDll/NativeOnly.vcxproj` 的 `netfx4-Release`／Win32 配置，并使用原型阶段已有的项目配置，不包含远程 .NET 注入服务。

本仓库不内置 EasyHook 源码、静态库或编译后二进制。构建时通过 `--easyhook` 引用已有目录；输出物静态链接该库时，应一并保留下列 EasyHook MIT 与其反汇编依赖 udis86 BSD 声明。Proto Input 的 MIT 声明也保留于下方，区分参考来源与实际链接依赖。

本地原始源码／归档摘要清单留在 `<Proto原型目录>/source-manifest.json`，EasyHook 的许可原文件为 `lib/EasyHook/LICENSE` 和 `lib/EasyHook/DriverShared/Disassembler/udis86-LICENSE.txt`。原始许可证内容如下。

## Proto Input — MIT

MIT License

Copyright (c) 2021 Ilyaki

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## EasyHook — MIT

Copyright (c) 2009 Christoph Husse & Copyright (c) 2012 Justin Stenning

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## udis86 — BSD 2-Clause

Copyright (c) 2002-2012, Vivek Thampi <vivek.mt@gmail.com>
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
