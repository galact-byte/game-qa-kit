"""只使用已有的 MSVC x86 构建 KiriKiri 专用组件。"""
from pathlib import Path
import argparse
import subprocess
import hashlib
import tempfile
import shutil
import time

BASE = Path(__file__).resolve().parents[2]


def build(output, *, mode, vcvars=Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat'), fixture_target=None,
          shell_counter_experiment=False):
    source = {'guard-tests': 'tests/kirikiri_native/GuardTests.cpp',
              'guard': 'plugins/kirikiri/GuardBridge.cpp',
              'startup-target': 'tests/kirikiri_native/StartupTarget.cpp',
              'startup-dll': 'tests/kirikiri_native/StartupDll.cpp',
              'host': 'plugins/kirikiri/GuardHost.cpp'}[mode]
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not Path(vcvars).is_file():
        raise RuntimeError('需要已有的 MSVC x86，可用 --vcvars 指定')
    args = ['cl', '/nologo', '/EHsc', '/MT', '/O2', '/W4', '/WX', '/std:c++17', '/utf-8',
            '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/I' + str(BASE / 'plugins/kirikiri')]
    if shell_counter_experiment:
        if mode not in ('guard', 'guard-tests'):
            raise ValueError('共享节拒绝实验仅适用于自写验证守卫')
        args.append('/DGAMEQA_SHELL_DENIAL_EXPERIMENT')
    if fixture_target is not None:
        if mode != 'host':
            raise ValueError('自写目标定义仅适用于测试宿主构建')
        args.append('/DGAMEQA_OWNED_IMAGE=' + hashlib.sha256(Path(fixture_target).read_bytes()).hexdigest())
    for name, symbol in [('AfterInit2.tjs', 'GAMEQA_AFTERINIT_HASH'), ('KagBridge.tjs', 'GAMEQA_KAG_HASH')]:
        args.append('/D' + symbol + '=' + hashlib.sha256((BASE / 'plugins/kirikiri' / name).read_bytes()).hexdigest())
    if mode in ('guard', 'startup-dll'):
        args.append('/LD')
    args += [str(BASE / source), '/Fe:' + str(output), '/link', 'bcrypt.lib', 'advapi32.lib', 'user32.lib']
    folder = Path(tempfile.mkdtemp(prefix='kirikiri-build-', dir=output.parent))
    try:
        script = folder / 'compile.cmd'
        script.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\n' +
                          subprocess.list2cmdline(args) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=folder, check=True)
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(folder)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.1)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['guard-tests', 'guard', 'host'], required=True)
    parser.add_argument('--vcvars', type=Path, default=Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat'))
    args = parser.parse_args()
    build(args.output, mode=args.mode, vcvars=args.vcvars)
