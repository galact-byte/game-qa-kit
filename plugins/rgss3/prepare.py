"""固定 VX Ace 工程与登记运行库的准备流程，仅修改新建副本。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
import shutil
import zlib

from gameqakit.admission import file_digest, fingerprint, no_links
from gameqakit.sessions import write_json

SOURCE_FINGERPRINT = 'ef327a77290e4f0864a8e160950569afda2087d101c2d0ffa47652e51c5571a6'
LIBRARY_SHA256 = '27647690ed16218cd988dd71069fdca67207515b2a2df775be361f0198ab6876'
BRIDGE = Path(__file__).with_name('GameQaBridge.rb')
COMMERCIAL_PROFILE = {
    'profile_id': 'rpgmaker-vxace-commercial-rgss300-fixed',
    'source_fingerprint': '4a242ccabb63894337d77ec5ae3413158372b694b7da516fc049265a659d4730',
    'library_sha256': 'b9be93afb4d6be8059f1db7b04a7dea275ce865c517974c2f3d942f108d941bd',
    'scripts_sha256': '205fd1dcac83449f1088b337290bda891069a69ef623831819e41d284ab249bd',
    'main_index': 173, 'script_count': 175, 'archive_entries': 373,
    'loose_overrides': ['Graphics/System/window.png'],
}


def manifest(root):
    root = Path(root)
    no_links(root)
    if not root.exists():
        return {}
    values = {}
    for item in sorted(root.rglob('*')):
        no_links(item)
        if item.is_file():
            values[item.relative_to(root).as_posix()] = file_digest(item)
    return values


def is_save(name):
    return re.fullmatch(r'Save\d+\.rvdata2', name, re.IGNORECASE) is not None


def checkpoint_snapshot(directory, *, source=None, artifacts=None):
    directory = Path(directory).absolute()
    no_links(directory)
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError('invalid_checkpoint：请选择专用测试存档目录')
    for other in (source, artifacts):
        if other is not None:
            other = Path(other).resolve()
            if directory == other or directory in other.parents or other in directory.parents:
                raise ValueError('overlapping_checkpoint：检查点不能与原工程或组件目录重叠')
    files = {}
    for item in sorted(directory.iterdir()):
        no_links(item)
        if not item.is_file() or re.fullmatch(r'Save0[1-7]\.rvdata2', item.name, re.IGNORECASE) is None:
            raise ValueError('invalid_checkpoint_file：仅接受 Save01.rvdata2 至 Save07.rvdata2')
        files[item.name] = file_digest(item)
    if not files:
        raise ValueError('empty_checkpoint：专用检查点目录中没有测试存档')
    return {'directory': str(directory), 'files': files}


def source_manifest(source):
    return {name: digest for name, digest in manifest(source).items() if not is_save(name)}


def project_profile(source, *, values=None):
    digest = fingerprint(source_manifest(source) if values is None else values)
    if digest == SOURCE_FINGERPRINT:
        return {'profile_id': 'rpgmaker-vxace-sdk-rgss301-fixed'}
    if digest == COMMERCIAL_PROFILE['source_fingerprint']:
        return dict(COMMERCIAL_PROFILE)
    raise ValueError('unsupported_rgss_project：仅接受已登记固定 VX Ace 工程及其完整资源摘要')


def source_digests(source):
    values = source_manifest(source)
    project_profile(source, values=values)
    return values


def artifact_digests(artifacts, *, commercial=False):
    name = 'RGSS300.dll' if commercial else 'RGSS301.dll'
    expected = COMMERCIAL_PROFILE['library_sha256'] if commercial else LIBRARY_SHA256
    path = Path(artifacts) / name
    no_links(path)
    if file_digest(path) != expected:
        raise ValueError('unsupported_rgss_library：所选运行库与固定工程登记不符')
    no_links(BRIDGE)
    return {name: file_digest(path), '@GameQaBridge.rb': file_digest(BRIDGE)}


def read_scripts(path):
    from rubymarshal.reader import Reader
    from rubymarshal.classes import RubyString

    class BinaryReader(Reader):
        @staticmethod
        def _get_encoding(_attributes):
            return 'latin1'

    def raw(value):
        if isinstance(value, RubyString):
            value = value.text
        return value.encode('latin1') if isinstance(value, str) else value

    stream = BytesIO(Path(path).read_bytes())
    if stream.read(2) != b'\x04\x08':
        raise ValueError('unsupported_marshal_version')
    return [[row[0], raw(row[1]), raw(row[2])] for row in BinaryReader(stream).read()]


def inspect(source, artifacts):
    blockers, commercial = [], False
    try:
        profile = project_profile(source)
        commercial = profile['profile_id'] == COMMERCIAL_PROFILE['profile_id']
        import rubymarshal.writer  # 已声明依赖；检查不自动安装。
    except (OSError, ValueError, ImportError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择已登记固定 VX Ace 工程；准备依赖见 requirements.txt。'))
    try:
        if artifacts is None:
            raise ValueError('尚未选择对应固定工程的运行库目录。')
        artifact_digests(artifacts, commercial=commercial)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='选择包含对应登记 DLL 的现有目录；商业配置保留原配 RGSS300。'))
    return blockers


def prepare(plan):
    commercial = plan.get('profile_id') == COMMERCIAL_PROFILE['profile_id']
    owned = []
    try:
        return _prepare(plan, commercial=commercial, owned=owned)
    except Exception:
        # 商业归档失败只回收本次新副本，保留源摘要与失败记录。
        runtime = Path(plan['destination']) / 'runtime'
        if commercial and owned and runtime.exists():
            manifest(runtime)
            shutil.rmtree(runtime)
        raise


def _prepare(plan, *, commercial, owned):
    from rubymarshal.writer import writes
    source, root, artifacts = (Path(plan[key]) for key in ('source', 'destination', 'artifacts'))
    before, runtime_before = manifest(source), artifact_digests(artifacts, commercial=commercial)
    source_files = {name: digest for name, digest in before.items() if not is_save(name)}
    if fingerprint(source_files) != plan['source_digests']:
        raise ValueError('source_changed_during_prepare')
    if fingerprint(runtime_before) != plan['artifact_digests']:
        raise ValueError('artifact_changed_during_prepare')
    checkpoint = plan.get('checkpoint')
    if checkpoint is not None:
        current = checkpoint_snapshot(checkpoint['directory'], source=source, artifacts=artifacts)
        if current != checkpoint:
            raise ValueError('checkpoint_changed_during_prepare')
    root.mkdir()
    owned.append(root)
    if checkpoint is not None:
        write_json(root / 'checkpoint-before.json', checkpoint)
    write_json(root / 'source-before.json', before)
    write_json(root / 'runtime-source-before.json', runtime_before)
    runtime = root / 'runtime'
    overrides = set(COMMERCIAL_PROFILE.get('loose_overrides', [])) if commercial else set()
    excluded = {'Game.rgss3a', 'system_data.rvdata2'} | overrides if commercial else set()
    excluded = {name.casefold() for name in excluded}
    shutil.copytree(source, runtime, ignore=lambda folder, names: {
        name for name in names if (Path(folder).relative_to(source) / name).as_posix().casefold() in excluded
        or (Path(folder) == source and is_save(name))})
    if manifest(runtime) != {name: digest for name, digest in source_files.items() if name.casefold() not in excluded}:
        raise ValueError('copy_changed_during_prepare')
    if commercial:
        from .archive import extract, table
        rows = table(source / 'Game.rgss3a')
        if len(rows) != COMMERCIAL_PROFILE['archive_entries']:
            raise ValueError('unsupported_rgss_archive_entries')
        if any(is_save(Path(row['name']).name) for row in rows):
            raise ValueError('unexpected_rgss_archive_save')
        extract(source / 'Game.rgss3a', runtime)
        archive_names = {row['name'] for row in rows}
        for name in overrides:
            if name not in archive_names or not name.startswith('Graphics/'):
                raise ValueError('unsupported_rgss_loose_override')
            shutil.copy2(source / name, runtime / name)
        if file_digest(runtime / 'Data/Scripts.rvdata2') != COMMERCIAL_PROFILE['scripts_sha256']:
            raise ValueError('unsupported_rgss_scripts')
    scripts = read_scripts(runtime / 'Data/Scripts.rvdata2')
    mains = [index for index, row in enumerate(scripts) if (commercial or row[1] == b'Main') and
             b'rgss_main { SceneManager.run }' in zlib.decompress(row[2]).splitlines()]
    if len(mains) != 1 or (commercial and (mains != [COMMERCIAL_PROFILE['main_index']]
                                         or len(scripts) != COMMERCIAL_PROFILE['script_count'])):
        raise ValueError('unsupported_rgss_main_entry')
    scripts.insert(mains[0], [999999, b'GameQaBridge', zlib.compress(b"load 'GameQaBridge.rb'\n")])
    (runtime / 'Data/Scripts.rvdata2').write_bytes(writes(scripts))
    if not commercial:
        ini = (runtime / 'Game.ini').read_bytes()
        if ini.count(b'System\\RGSS300.dll') != 1:
            raise ValueError('unsupported_rgss_library_configuration')
        (runtime / 'Game.ini').write_bytes(ini.replace(b'System\\RGSS300.dll', b'System\\RGSS301.dll'))
        shutil.copy2(artifacts / 'RGSS301.dll', runtime / 'System/RGSS301.dll')
    shutil.copy2(BRIDGE, runtime / 'GameQaBridge.rb')
    if checkpoint is not None:
        for name in checkpoint['files']:
            shutil.copy2(Path(checkpoint['directory']) / name, runtime / name)
        current = checkpoint_snapshot(checkpoint['directory'], source=source, artifacts=artifacts)
        write_json(root / 'checkpoint-after-copy.json', current)
        if current != checkpoint:
            raise ValueError('checkpoint_changed_during_prepare')
    (root / 'tmp').mkdir()
    baseline = manifest(runtime)
    if checkpoint is not None:
        copied = {name: baseline.get(name) for name in checkpoint['files']}
        if copied != checkpoint['files']:
            raise ValueError('checkpoint_copy_changed_during_prepare')
    if manifest(source) != before:
        raise ValueError('source_changed_during_prepare')
    if artifact_digests(artifacts, commercial=commercial) != runtime_before:
        raise ValueError('artifact_changed_during_prepare')
    write_json(root / 'runtime-before.json', baseline)
    return baseline
