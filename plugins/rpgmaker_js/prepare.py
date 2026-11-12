"""固定 MV 1.6.1 / MZ 1.8.0 SDK 空白工程与已有 NW.js 的准备流程。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

from gameqakit.admission import file_digest, fingerprint, no_links
from gameqakit.sessions import read_json, write_json

SOURCE_FINGERPRINTS = {
    'rpgmaker_mv': '93c5bf24496029614dd61ca43bb673bbc95e3e8f5942422ef267244db89ee84c',
    'rpgmaker_mz': 'e9635e9b4b98f0c84b7df95e6d0a6ac74e5dcb0ef7708861045d0611f4b7a763',
}
RUNTIME_FINGERPRINT = 'beee84d16656882200e913e6ff608720dceef58c1009dfce166b8a9bb617a269'
BRIDGE = Path(__file__).with_name('GameQaBridge.js')
BRIDGE_KEY = '@GameQaBridge.js'


def manifest(root):
    root = Path(root)
    no_links(root)
    if not root.exists():
        return {}
    result = {}
    for item in sorted(root.rglob('*')):
        no_links(item)
        if item.is_file():
            result[item.relative_to(root).as_posix()] = file_digest(item)
    return result


def source_manifest(source):
    return {name: digest for name, digest in manifest(source).items()
            if name.split('/')[0].lower() != 'save'}


def source_digests(source, engine):
    values = source_manifest(source)
    if fingerprint(values) != SOURCE_FINGERPRINTS.get(engine):
        raise ValueError('unsupported_rpgmaker_project：仅已登记 SDK 空白工程已核查保存与插件边界')
    return values


def artifact_digests(artifacts):
    values = manifest(artifacts)
    if 'nw.exe' not in values or fingerprint(values) != RUNTIME_FINGERPRINT:
        raise ValueError('unsupported_nwjs_runtime：请选择已登记的完整 NW.js 运行时目录')
    no_links(BRIDGE)
    return {**values, BRIDGE_KEY: file_digest(BRIDGE)}


def inspect(source, artifacts, engine):
    blockers = []
    try:
        source_digests(source, engine)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择已登记、未经修改的 MV/MZ SDK 空白工程目录。'))
    try:
        if artifacts is None:
            raise ValueError('尚未选择已有 NW.js 运行时。')
        artifact_digests(artifacts)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='选择已登记、包含 nw.exe 的完整 NW.js 目录，无需重新安装。'))
    return blockers


def external_profiles(source):
    package = read_json(Path(source) / 'package.json')
    name = package.get('name') or 'nwjs'
    # 原工程名只用于只读核对；执行副本另用随机标识及显式 user-data-dir。
    if not isinstance(name, str) or name in ('.', '..') or any(c in name for c in '/\\:'):
        raise ValueError('invalid_original_package_name')
    return {str(Path(os.environ[key]) / name): manifest(Path(os.environ[key]) / name)
            for key in ('LOCALAPPDATA', 'APPDATA')}


def prepare(plan):
    source, root, artifacts = (Path(plan[key]) for key in ('source', 'destination', 'artifacts'))
    before, runtime_before = manifest(source), manifest(artifacts)
    external = external_profiles(source)
    root.mkdir()
    write_json(root / 'source-before.json', before)
    write_json(root / 'runtime-source-before.json', runtime_before)
    write_json(root / 'external-before.json', external)
    source_files = source_manifest(source)
    if fingerprint(source_files) != plan['source_digests']:
        raise ValueError('source_changed_during_prepare')
    if fingerprint({**runtime_before, BRIDGE_KEY: file_digest(BRIDGE)}) != plan['artifact_digests']:
        raise ValueError('artifact_changed_during_prepare')
    runtime, app = root / 'runtime', root / 'runtime/app'
    shutil.copytree(artifacts, runtime)
    shutil.copytree(source, app, ignore=lambda folder, names: {
        name for name in names if Path(folder) == source and name.lower() == 'save'})
    if source_manifest(app) != source_files or manifest(runtime) != {
            **runtime_before, **{'app/' + name: digest for name, digest in source_files.items()}}:
        raise ValueError('copy_changed_during_prepare')
    package = read_json(app / 'package.json')
    package.update(name='gameqa-session-' + uuid.uuid4().hex, **{'single-instance': False})
    write_json(app / 'package.json', package)
    (app / 'js/plugins').mkdir(exist_ok=True)
    shutil.copy2(BRIDGE, app / 'js/plugins/GameQaBridge.js')
    with (app / 'js/plugins.js').open('a', encoding='utf-8') as stream:
        stream.write('\n$plugins.push({name:"GameQaBridge",status:true,description:"QA bridge",parameters:{}});\n')
    (root / 'tmp').mkdir()
    baseline = manifest(runtime)
    write_json(root / 'runtime-before.json', baseline)
    return baseline
