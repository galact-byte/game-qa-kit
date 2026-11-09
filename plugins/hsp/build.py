"""使用已安装的 MSVC x86 工具链构建 HSP 插件或运行独立安全核心测试。"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(output, *, sdk=None, test=False, vcvars=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    vcvars = Path(vcvars or 'E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
    if os.name != 'nt' or not vcvars.is_file():
        raise RuntimeError('需要已安装的 MSVC x86 工具链，可用 --vcvars 指定 vcvars32.bat')
    source = ROOT / ('tests/hsp/ControlCoreTests.cpp' if test else 'plugins/hsp/Bridge.cpp')
    args = ['/nologo', '/EHsc', '/W4', '/WX', '/std:c++14', '/source-charset:.932', '/execution-charset:utf-8']
    if not test:
        sdk = Path(sdk or 'E:/hsp37/hspsdk/hpi3sample').resolve()
        if not (sdk / 'hsp3plugin.h').is_file():
            raise RuntimeError('需要本地 HSP 插件 SDK，可用 --sdk 指定头文件目录')
        args += ['/LD', '/I' + str(sdk)]
    args += [str(source), '/Fe:' + str(output)]
    if not test:
        args += ['user32.lib', 'gdi32.lib', 'gdiplus.lib']
    with tempfile.TemporaryDirectory(prefix='hsp-build-', dir=output.parent) as temporary:
        script = Path(temporary) / 'compile.cmd'
        script.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(['cl', *args]) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=temporary, check=True)
    if test:
        subprocess.run([str(output)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-core', action='store_true')
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--vcvars', type=Path)
    options = parser.parse_args()
    build(options.output, sdk=options.sdk, test=options.test_core, vcvars=options.vcvars)
