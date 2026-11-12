"""固定 VX Ace SDK 与既有 RGSS301 的准备流程，仅修改新建副本。"""
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


def source_manifest(source):
    return {name: digest for name, digest in manifest(source).items() if not is_save(name)}


def source_digests(source):
    values = source_manifest(source)
    if fingerprint(values) != SOURCE_FINGERPRINT:
        raise ValueError('unsupported_rgss_project：仅已登记 VX Ace SDK 空白工程已核查脚本与保存边界')
    return values


def artifact_digests(artifacts):
    path = Path(artifacts) / 'RGSS301.dll'
    no_links(path)
    if file_digest(path) != LIBRARY_SHA256:
        raise ValueError('unsupported_rgss_library：仅已登记 RGSS301.dll 已验证')
    no_links(BRIDGE)
    return {'RGSS301.dll': file_digest(path), '@GameQaBridge.rb': file_digest(BRIDGE)}


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
    blockers = []
    try:
        source_digests(source)
        import rubymarshal.writer  # 已声明依赖；检查不自动安装。
    except (OSError, ValueError, ImportError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择已登记 VX Ace SDK；准备依赖见 requirements.txt。'))
    try:
        if artifacts is None:
            raise ValueError('尚未选择已有 RGSS301.dll。')
        artifact_digests(artifacts)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='选择包含已登记 RGSS301.dll 的现有目录，无需安装运行时。'))
    return blockers


def prepare(plan):
    from rubymarshal.writer import writes
    source, root, artifacts = (Path(plan[key]) for key in ('source', 'destination', 'artifacts'))
    before, runtime_before = manifest(source), artifact_digests(artifacts)
    source_files = source_manifest(source)
    if fingerprint(source_files) != plan['source_digests']:
        raise ValueError('source_changed_during_prepare')
    if fingerprint(runtime_before) != plan['artifact_digests']:
        raise ValueError('artifact_changed_during_prepare')
    root.mkdir()
    write_json(root / 'source-before.json', before)
    write_json(root / 'runtime-source-before.json', runtime_before)
    runtime = root / 'runtime'
    shutil.copytree(source, runtime, ignore=lambda folder, names: {
        name for name in names if Path(folder) == source and is_save(name)})
    if manifest(runtime) != source_files:
        raise ValueError('copy_changed_during_prepare')
    scripts = read_scripts(runtime / 'Data/Scripts.rvdata2')
    mains = [index for index, row in enumerate(scripts) if row[1] == b'Main' and
             b'rgss_main { SceneManager.run }' in zlib.decompress(row[2]).splitlines()]
    if len(mains) != 1:
        raise ValueError('unsupported_rgss_main_entry')
    scripts.insert(mains[0], [999999, b'GameQaBridge', zlib.compress(b"load 'GameQaBridge.rb'\n")])
    (runtime / 'Data/Scripts.rvdata2').write_bytes(writes(scripts))
    ini = (runtime / 'Game.ini').read_bytes()
    if ini.count(b'System\\RGSS300.dll') != 1:
        raise ValueError('unsupported_rgss_library_configuration')
    (runtime / 'Game.ini').write_bytes(ini.replace(b'System\\RGSS300.dll', b'System\\RGSS301.dll'))
    shutil.copy2(artifacts / 'RGSS301.dll', runtime / 'System/RGSS301.dll')
    shutil.copy2(BRIDGE, runtime / 'GameQaBridge.rb')
    (root / 'tmp').mkdir()
    baseline = manifest(runtime)
    write_json(root / 'runtime-before.json', baseline)
    return baseline
