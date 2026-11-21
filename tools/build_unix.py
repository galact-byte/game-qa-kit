"""在 macOS／Linux 的当前架构构建通用测试包，不发布、不下载依赖。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(version, output):
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('版本必须为 v主版本.次版本.修订号，可带预发布后缀')
    if sys.platform not in ('darwin', 'linux'):
        raise RuntimeError('通用测试包必须在对应的 macOS／Linux 系统上构建')
    arch = {'x86_64': 'x64', 'AMD64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine())
    if not arch:
        raise RuntimeError('仅提供 x64 与 arm64 构建')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('构建目录必须不存在，禁止覆盖已有产物')
    output.mkdir(parents=True)
    build_env = {**os.environ, 'PYINSTALLER_CONFIG_DIR': str(output / 'cache')}
    if sys.platform == 'darwin' and arch == 'arm64':
        helper = output / 'native/GameQACapture'
        helper.parent.mkdir(parents=True)
        subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-swift-version', '5', '-O',
                        '-target', 'arm64-apple-macosx14.0',
                        str(ROOT / 'plugins/macos_capture/CaptureHelper.swift'), '-o', str(helper),
                        '-framework', 'AppKit', '-framework', 'ScreenCaptureKit'], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(helper)], check=True)
        build_env['GAMEQA_MAC_CAPTURE_HELPER'] = str(helper)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
                    str(ROOT / 'tools/GameQA-unix.spec')], cwd=ROOT, check=True,
                   env=build_env)
    mac = sys.platform == 'darwin'
    app = output / ('dist/GameQA.app' if mac else 'dist/GameQA')
    resources = app / 'Contents/Resources' if mac else app
    shutil.copy2(ROOT / 'tools/UNIVERSAL-README.md', resources / '使用说明.md')
    shutil.copy2(ROOT / 'tools/THIRD-PARTY.md', resources / '第三方组件说明.md')
    binary = app / 'Contents/MacOS/GameQA' if mac else app / 'GameQA'
    report = output / 'self-test.json'
    subprocess.run([str(binary), '--self-test', str(report)], check=True, timeout=90,
                   env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    if not json.loads(report.read_text(encoding='utf-8')).get('ok'):
        raise RuntimeError('冻结产物启动自检失败')
    if mac:
        # 复制说明后重新封存 ad-hoc 签名；这不等于开发者签名或公证。
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    target = 'macos' if mac else 'linux'
    archive = output / f'game-qa-kit-{version}-{target}-{arch}.tar.gz'
    with tarfile.open(archive, 'x:gz') as stream:
        stream.add(app, arcname=app.name)
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.version, args.output))
