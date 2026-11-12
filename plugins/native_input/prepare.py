"""为固定 x86 SRPG Studio data.dts 构建建立全新测试副本，原数据只读。"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import struct

SAMPLE = json.loads((Path(__file__).with_name('samples') / 'srpgstudio-x86-fixed.json').read_text(encoding='utf-8'))
SUPPORTED_SHA256 = SAMPLE['exe_sha256']
RESOURCES = ('game.exe', 'game.ini', 'data.dts', 'runtime.rts', 'system.dat', 'environment.evs', 'Audio')


def no_links(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('reparse_path_rejected')
    if path.is_dir():
        for item in path.rglob('*'):
            if item.is_symlink() or item.is_junction():
                raise ValueError('reparse_path_rejected')


def manifest(path):
    path = Path(path)
    no_links(path)
    if not path.exists():
        return {}
    result = {}
    for item in sorted(path.rglob('*')):
        if item.is_file():
            with item.open('rb') as stream:
                result[item.relative_to(path).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def documents():
    value = ctypes.create_unicode_buffer(32768)
    if ctypes.WinDLL('shell32').SHGetFolderPathW(None, 5, None, 0, value) != 0:
        raise RuntimeError('documents_lookup_failed')
    return Path(value.value) / 'SRPG Studio'


def validate_image(path):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SUPPORTED_SHA256:
        raise ValueError('unsupported_native_image')
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('invalid_pe_image')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset < 64 or offset + 24 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('invalid_pe_image')
    if struct.unpack_from('<H', data, offset + 4)[0] != 0x14c:
        raise ValueError('x86_image_required')
    return digest


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(source, workspace, *, external=None):
    source, workspace = Path(source).absolute(), Path(workspace).absolute()
    no_links(source)
    no_links(workspace)
    source, workspace = source.resolve(), workspace.resolve()
    if source == workspace or source in workspace.parents or workspace in source.parents:
        raise ValueError('overlapping_paths')
    if workspace.exists():
        raise FileExistsError('workspace_must_be_new')
    if not source.is_dir():
        raise ValueError('source_directory_required')
    if not (source / 'data.dts').is_file():
        raise ValueError('data_mode_required')
    digest = validate_image(source / 'game.exe')
    if any(not (source / name).exists() for name in RESOURCES):
        raise ValueError('missing_known_resource')
    config = (source / 'game.ini').read_bytes()
    if config.count(b'NonActiveAnimationPlay=1') != 1 or config.count(b'NonActiveMusicPlay=0') != 1:
        raise ValueError('unsupported_background_configuration')
    external = Path(external) if external is not None else documents()
    no_links(external)
    before, outside = manifest(source), manifest(external)
    workspace.mkdir(parents=True, exist_ok=False)
    save(workspace / 'source-before.json', before)
    save(workspace / 'external-before.json', {'path': str(external), 'files': outside})
    runtime = workspace / 'runtime'
    runtime.mkdir()
    for name in ('bridge', 'temp'):
        (workspace / name).mkdir()
    for name in RESOURCES:
        origin, target = source / name, runtime / name
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)
    (runtime / 'Save').mkdir()
    (runtime / 'game.ini').write_bytes(config.replace(b'NonActiveAnimationPlay=1', b'NonActiveAnimationPlay=0')
                                    .replace(b'NonActiveMusicPlay=0', b'NonActiveMusicPlay=1'))
    validate_image(runtime / 'game.exe')
    record = {'source': str(source), 'runtime': str(runtime), 'exe_sha256': digest,
              'copied': list(RESOURCES), 'copied_user_saves': False,
              'changes': {'game.ini': ['NonActiveAnimationPlay 1 -> 0', 'NonActiveMusicPlay 0 -> 1']},
              'source_modified': False, 'external_path': str(external),
              'restore': 'Stop and verify all owned processes exited; archive runtime manifest, then remove runtime only.'}
    save(workspace / 'preparation.json', record)
    (workspace / '测试数据记录.md').write_text(
        '# 原生后台输入测试副本记录\n\n'
        f'- 原目录（只读）：`{source}`\n- 副本：`{runtime}`\n'
        '- 改动：只复制已核查资源；不复制原 Save，副本创建空 Save。仅副本 game.ini 修改后台动画／音乐选项。\n'
        '- 槽位：不读取或写入原存档；本轮只验标题、环境设置和返回，不进行保存操作。\n'
        '- 改前备份：原目录保留不动，source-before.json／external-before.json 记录摘要。\n'
        '- 加载：仅挂起创建副本进程，由 Job 回收；加载前后均限制副本外文件与注册表写入。\n'
        '- 还原：先停宿主并核对目标退出，保存 runtime-after.json，再回收 runtime；原目录无需还原。\n',
        encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('workspace', type=Path)
    options = parser.parse_args()
    print(json.dumps(prepare(options.source, options.workspace), ensure_ascii=False, indent=2))
