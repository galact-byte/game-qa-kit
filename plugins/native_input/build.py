"""使用本机 MSVC 构建原生输入核心或 x86 桥，不下载依赖。"""
import argparse
import hashlib
import re
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(output, *, mode='core', easyhook=None, vcvars=None, owned_image=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    vcvars = Path(vcvars or 'E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
    if os.name != 'nt' or not vcvars.is_file():
        raise RuntimeError('需要已有 MSVC x86；可用 --vcvars 指定路径')
    source = ROOT / ('tests/native_input/CoreTests.cpp' if mode == 'core' else
                     'plugins/native_input/Host.cpp' if mode == 'host' else
                     'tests/native_input/StartupGuardTests.cpp' if mode == 'startup-tests' else
                     'tests/native_input/Target.cpp' if mode == 'target' else 'plugins/native_input/Bridge.cpp')
    args = ['/nologo', '/EHsc', '/MT', '/O2', '/W4', '/WX', '/std:c++17', '/utf-8', '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX',
            '/I' + str(ROOT / 'plugins/native_input')]
    if owned_image is not None:
        digest = hashlib.sha256(Path(owned_image).read_bytes()).hexdigest()
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('自有窗口摘要无效')
        args += ['/DGAMEQA_OWNED_IMAGE=' + digest]
    if mode == 'bridge':
        if easyhook is None:
            raise ValueError('桥构建需要 --easyhook 指向已有的 EasyHook 源码构建目录')
        easyhook = Path(easyhook).resolve()
        easyhook_library = easyhook / 'Build/x86/netfx4-Release/EasyHook32.lib'
        if not (easyhook / 'Public/easyhook.h').is_file() or not easyhook_library.is_file():
            raise ValueError('找不到已有 EasyHook 头文件或 x86 静态库')
        args += ['/LD', '/I' + str(easyhook / 'Public')]
    args += [str(source), '/Fe:' + str(output), '/link']
    if mode != 'core':
        args += ['user32.lib', 'gdi32.lib', 'gdiplus.lib', 'bcrypt.lib', 'advapi32.lib', 'dinput8.lib', 'dxguid.lib']
    if mode == 'bridge':
        args += [str(easyhook_library), 'ole32.lib', 'psapi.lib', 'ntdll.lib']
    if mode == 'startup-tests':
        # The remote-thread fixture checks identical code addresses in its owned child.
        args += ['/DYNAMICBASE:NO', '/FIXED']
    if mode == 'target':
        args += ['/SUBSYSTEM:WINDOWS']
    with tempfile.TemporaryDirectory(prefix='native-build-', dir=output.parent) as tmp:
        script = Path(tmp) / 'compile.cmd'
        script.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\n' +
                          subprocess.list2cmdline(['cl', *args]) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=tmp, check=True)
    if mode in ('core', 'startup-tests'):
        subprocess.run([str(output)], check=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['core', 'bridge', 'host', 'target', 'startup-tests'], default='core')
    parser.add_argument('--easyhook', type=Path)
    parser.add_argument('--vcvars', type=Path)
    parser.add_argument('--owned-image', type=Path, help='仅用于自有窗口测试构建，固定该 EXE 摘要；不可发布')
    options = parser.parse_args()
    build(options.output, mode=options.mode, easyhook=options.easyhook, vcvars=options.vcvars, owned_image=options.owned_image)
