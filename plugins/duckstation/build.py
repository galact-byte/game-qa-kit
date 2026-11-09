"""使用本机 MSVC x64 构建指定版本 DuckStation 控制桥与安全核心测试。"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(output, *, test=False, vcvars=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    vcvars = Path(vcvars or 'E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars64.bat')
    if os.name != 'nt' or not vcvars.is_file():
        raise RuntimeError('需要已安装的 MSVC x64；可用 --vcvars 指定 vcvars64.bat')
    source = ROOT / ('tests/duckstation/ControlCoreTests.cpp' if test else 'plugins/duckstation/Bridge.cpp')
    args = ['/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8']
    if not test:
        args += ['/LD']
    args += [str(source), '/Fe:' + str(output)]
    with tempfile.TemporaryDirectory(prefix='duckstation-build-', dir=output.parent) as tmp:
        script = Path(tmp) / 'compile.cmd'
        script.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(['cl', *args]) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=tmp, check=True)
    if test:
        subprocess.run([str(output)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-core', action='store_true')
    parser.add_argument('--vcvars', type=Path)
    args = parser.parse_args()
    build(args.output, test=args.test_core, vcvars=args.vcvars)
