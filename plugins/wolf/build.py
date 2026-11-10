"""使用已安装 MSVC x86 构建 Wolf 原生控制桥、加载器或安全核心回归。"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(output, *, test=False, injector=False, locale_host=False, vcvars=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    vcvars = Path(vcvars or 'E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
    if os.name != 'nt' or not vcvars.is_file():
        raise RuntimeError('需要已安装 MSVC x86，可用 --vcvars 指定 vcvars32.bat')
    source = ROOT / ('tests/wolf/ControlCoreTests.cpp' if test else
                     'plugins/wolf/LocaleHost.cpp' if locale_host else
                     'plugins/wolf/Injector.cpp' if injector else 'plugins/wolf/Bridge.cpp')
    args = ['/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8', '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX']
    if not test and not injector and not locale_host:
        args += ['/LD']
    args += [str(source), '/Fe:' + str(output)]
    if test:
        args += ['/link', 'bcrypt.lib', 'advapi32.lib']
    else:
        args += ['/link', 'user32.lib', 'gdi32.lib', 'gdiplus.lib', 'bcrypt.lib', 'advapi32.lib']
    with tempfile.TemporaryDirectory(prefix='wolf-build-', dir=output.parent) as tmp:
        script = Path(tmp) / 'compile.cmd'
        script.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(['cl', *args]) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=tmp, check=True)
    if test:
        subprocess.run([str(output)], check=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--test-core', action='store_true')
    mode.add_argument('--injector', action='store_true')
    mode.add_argument('--locale-host', action='store_true')
    parser.add_argument('--vcvars', type=Path)
    args = parser.parse_args()
    build(args.output, test=args.test_core, injector=args.injector, locale_host=args.locale_host, vcvars=args.vcvars)
