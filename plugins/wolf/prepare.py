"""为已验证 Wolf 构建建立全新隔离副本；源目录始终只读。"""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import shutil
import struct

SUPPORTED_SHA256 = '90800e5dff9fa992a1fa78be5b2d5158baf2e9cc9603a45d964358e919f6868a'
SUPPORTED_IMAGES = {SUPPORTED_SHA256,
                    '2b811e3db991f45113087f979e3f375da7e46769a01f6e554584ed7c100b2f27'}
EXCLUDED = ['Save', 'SaveData', 'Save_MTool', 'Tool', 'MTool_*']
SOURCE_FINGERPRINT = '167fc818c3a2912a144e064c42e38551f086e9a83e8bba6bc282f44003a28eb0'


def validate_image(path):
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest not in SUPPORTED_IMAGES:
        raise ValueError('不支持的 Wolf EXE：必须重新验证完整构建，不能关闭哈希校验')
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('无效 PE 文件')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('无效 PE 文件')
    if struct.unpack_from('<H', data, offset + 4)[0] != 0x14c:
        raise ValueError('仅支持 Windows x86 Wolf 构建')
    return digest


def prepare(source, workspace):
    source, workspace = Path(source).resolve(), Path(workspace).resolve()
    if source == workspace or source in workspace.parents or workspace in source.parents:
        raise ValueError('源目录与工作区不得重叠')
    if workspace.exists():
        raise FileExistsError('工作区必须不存在，禁止覆盖已有测试或原始数据')
    if not source.is_dir():
        raise ValueError('源游戏目录不存在')
    for path in (source, *source.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('源目录含链接，无法保证副本的数据边界')
    digest = validate_image(source / 'Game.exe')
    workspace.mkdir(parents=True, exist_ok=False)
    runtime = workspace / 'runtime'
    shutil.copytree(source, runtime, ignore=shutil.ignore_patterns(*EXCLUDED))
    record = {'source': str(source), 'runtime': str(runtime),
              'exe_sha256': digest, 'copied_user_saves': False,
              'excluded': EXCLUDED, 'source_modified': False}
    (workspace / 'preparation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    (workspace / '测试数据记录.md').write_text(
        '# Wolf 测试副本记录\n\n'
        f'- 原目录（只读）：`{source}`\n- 本次副本：`{runtime}`\n'
        '- 改动：仅复制游戏资源；不带原 Save／SaveData／Save_MTool、Tool 与 MTool_*，不改原档。\n'
        '- 存档／槽：测试副本自建，不使用原目录任何槽。\n'
        '- 改前备份：原目录即只读基线；无需覆盖原文件。\n'
        '- 还原：退出本次 Job 后回收 runtime；原目录不需还原。\n', encoding='utf-8')
    return record


def manifest(root):
    from gameqakit.admission import file_digest, no_links
    root = Path(root)
    no_links(root)
    values = {}
    if root.exists():
        for item in sorted(root.rglob('*')):
            no_links(item)
            if item.is_file():
                values[item.relative_to(root).as_posix()] = file_digest(item)
    return values


def source_manifest(source):
    return {name: digest for name, digest in manifest(source).items()
            if not any(fnmatch.fnmatchcase(part, pattern) for part in Path(name).parts for pattern in EXCLUDED)}


def source_digests(source):
    from gameqakit.admission import fingerprint
    if validate_image(Path(source) / 'Game.exe') != SUPPORTED_SHA256:
        raise ValueError('unsupported_wolf_managed_build：受管入口仅迁入已核查官方范例')
    values = source_manifest(source)
    if fingerprint(values) != SOURCE_FINGERPRINT:
        raise ValueError('unsupported_wolf_project：资源与已核查官方范例不一致')
    return values


def artifact_digests(artifacts):
    from gameqakit.admission import file_digest, no_links
    values = {}
    for name in ('GameQaWolf.dll', 'WolfInjector.exe'):
        path = Path(artifacts) / name
        no_links(path)
        values[name] = file_digest(path)
    return values


def inspect(source, artifacts):
    blockers = []
    try:
        source_digests(source)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择已登记的完整官方 Wolf 范例；商业构建暂用原入口。'))
    try:
        if artifacts is None:
            raise ValueError('尚未选择已有 Wolf 控制桥和注入器。')
        artifact_digests(artifacts)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='选择包含 GameQaWolf.dll 和 WolfInjector.exe 的现有构建目录。'))
    return blockers


def prepare_managed(plan):
    from gameqakit.admission import fingerprint
    from gameqakit.sessions import write_json
    source, root = Path(plan['source']), Path(plan['destination'])
    before, artifacts = manifest(source), artifact_digests(plan['artifacts'])
    if fingerprint(source_digests(source)) != plan['source_digests']:
        raise ValueError('source_changed_during_prepare')
    if artifacts != plan['artifact_digests']:
        raise ValueError('artifact_changed_during_prepare')
    expected = source_manifest(source)
    prepare(source, root)
    write_json(root / 'approved-plan.json', plan)
    write_json(root / 'source-before.json', before)
    write_json(root / 'artifact-before.json', artifacts)
    baseline = manifest(root / 'runtime')
    if baseline != expected:
        raise RuntimeError('copy_changed_during_prepare')
    write_json(root / 'runtime-before.json', baseline)
    (root / 'tmp').mkdir()
    return baseline


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('workspace', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.workspace), ensure_ascii=False, indent=2))
