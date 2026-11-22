"""构建并测量固定 Godot macOS 组件；本步骤不启动场景或发送输入。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVISION = 'f62fdbde15035c5576dad93e586201f4d41ef0cb'


def main():
    root = Path.cwd()
    source = root / 'godot-component-src'
    output = root / 'component'
    evidence = root / 'validation-output'
    output.mkdir()
    evidence.mkdir(exist_ok=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        raise ValueError('上游源码版本不匹配')
    patch = root / 'tools/godot-no-focus.patch'
    subprocess.run(['git', 'apply', '--check', str(patch)], cwd=source, check=True)
    subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
    diff = subprocess.check_output(['git', 'diff', '--numstat'], cwd=source, text=True).strip()
    if diff != '1\t1\tplatform/macos/display_server_macos.mm':
        raise ValueError('源码变更超过单条件补丁')
    command = [sys.executable, '-B', '-m', 'SCons', '-j2', 'platform=macos', 'target=template_debug',
               'arch=arm64', 'vulkan=no', 'metal=no', 'opengl3=yes', 'disable_3d=yes', 'optimize=none',
               'modules_enabled_by_default=no', 'module_gdscript_enabled=yes', 'module_text_server_fb_enabled=yes',
               'module_freetype_enabled=yes', 'accesskit=no', 'sdl=no']
    manifest = {'component_id': 'godot-4.5.1-macos-arm64-no-focus', 'official_asset': False,
                'upstream': 'https://github.com/godotengine/godot', 'revision': revision,
                'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
                'command': command, 'started_at': time.time(), 'build_ok': False}
    env = dict(os.environ, PYTHONPATH=str(Path(os.environ['RUNNER_TEMP']) / 'godot-scons'))
    with (evidence / 'build.log').open('w') as stream:
        result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
    manifest['build_exit'] = result.returncode
    (evidence / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    if result.returncode:
        print('\n'.join((evidence / 'build.log').read_text().splitlines()[-70:]))
        return result.returncode
    runtime = output / 'godot-runtime-macos-arm64'
    shutil.copy2(source / 'bin/godot.macos.template_debug.arm64', runtime)
    manifest.update(runtime=runtime.name, runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
                    runtime_bytes=runtime.stat().st_size, build_ok=True, finished_at=time.time(),
                    architecture=subprocess.check_output(['lipo', '-archs', str(runtime)], text=True).strip(),
                    version=subprocess.check_output([str(runtime), '--version'], text=True).strip())
    if manifest['architecture'] != 'arm64' or not manifest['version'].startswith('4.5.1.stable.gameqa_nofocus.'):
        raise ValueError('构建架构或版本不匹配')
    (output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for name in ('LICENSE.txt', 'COPYRIGHT.txt'):
        shutil.copyfile(source / name, output / ('Godot-' + name))
    shutil.copyfile(patch, output / patch.name)
    from tests.test_godot_bridge import fixture_pack
    authored = output / 'authored'
    authored.mkdir()
    fixture_pack(authored / 'fixture.pck', no_focus=True)
    record = {**manifest, 'pack_sha256': hashlib.sha256((authored / 'fixture.pck').read_bytes()).hexdigest(),
              'support_files': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                for name in ('source-manifest.json', 'Godot-LICENSE.txt', 'Godot-COPYRIGHT.txt', patch.name)}}
    (evidence / 'measured-component.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    shutil.copyfile(output / 'source-manifest.json', evidence / 'source-manifest.json')
    print(json.dumps(record, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
