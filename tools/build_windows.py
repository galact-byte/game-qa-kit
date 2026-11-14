"""在指定目录构建并自检 Windows 便携版，不下载依赖、不发布。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import struct
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def check_bundle(app, output):
    sys.path.insert(0, str(ROOT))
    from gameqakit.desktop import DesktopProcess
    report = output / 'self-test.json'
    with DesktopProcess([str(app / 'GameQA.exe'), '--self-test', str(report)], cwd=output) as child:
        child.run()
        deadline = time.monotonic() + 90
        while child.alive():
            if time.monotonic() >= deadline:
                raise TimeoutError('便携版自检超时，回收本次 Job')
            time.sleep(.05)
    if not report.is_file() or not json.loads(report.read_text(encoding='utf-8')).get('ok'):
        raise RuntimeError('便携版自检未通过，请查看 self-test.json')


def build(version, output):
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('版本必须为 v主版本.次版本.修订号，可带预发布后缀')
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('Windows 便携版必须在 Windows x64 Python 上构建')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('构建目录必须不存在，禁止覆盖旧发布资产')
    output.mkdir(parents=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
                    str(ROOT / 'tools/GameQA.spec')], cwd=ROOT, check=True,
                   env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(output / 'cache')})
    app = output / 'dist/GameQA'
    (app / 'profiles').mkdir()
    shutil.copy2(ROOT / 'profiles/example.json', app / 'profiles/example.json')
    shutil.copy2(ROOT / 'LICENSE', app / 'LICENSE')
    shutil.copy2(ROOT / 'tools/PORTABLE-README.md', app / '使用说明.md')
    shutil.copy2(ROOT / 'tools/THIRD-PARTY.md', app / '第三方组件说明.md')
    check_bundle(app, output)
    archive = output / f'game-qa-kit-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(app.rglob('*')):
            if item.is_file():
                bundle.write(item, Path('GameQA') / item.relative_to(app))
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS.txt').write_text(checksum + '  ' + archive.name + '\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.version, args.output))
