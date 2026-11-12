"""固定 Taimashi / Unity 2020.3 Mono 旧 Input 的只读登记与副本准备。"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import uuid

from gameqakit.admission import file_digest, fingerprint, no_links
from gameqakit.sessions import write_json

SOURCE_DIGESTS = {
    'Taimashi.exe': '71bdf62c7f2d4db7f1ce48f6bc77039db2159de42ff3bc91267f59cb42b1b384',
    'UnityPlayer.dll': '58564b8e7fcfc5215dcfce8b9c87405ae41502df89567a7c04dc1006497d0773',
    'Taimashi_Data/globalgamemanagers': 'ebfd0c938bbdfa1ff215a0ac5fa13248c77917c61b4914ecf07b45386fe270d3',
    'Taimashi_Data/Managed/Assembly-CSharp.dll': 'edc47c5ec36ed96a525abb16749c25a26398a98512e5eed4a7c3fd54aeb73845',
}
MANAGED_FINGERPRINT = '7c11c1f3363037f742d929ccae43eced33f7a9fd13d5bfcbd3bc7d389dac04ff'
LOADER_DIGEST = '8c6cdbc38836dee87e3368f5de1994d7c0ccebf29e4ce7aba3c0981f9375412c'
CORE_DLLS = ('0Harmony.dll', '0Harmony20.dll', 'BepInEx.dll', 'BepInEx.Harmony.dll',
             'BepInEx.Preloader.dll', 'HarmonyXInterop.dll', 'Mono.Cecil.dll',
             'Mono.Cecil.Mdb.dll', 'Mono.Cecil.Pdb.dll', 'Mono.Cecil.Rocks.dll',
             'MonoMod.RuntimeDetour.dll', 'MonoMod.Utils.dll')
EXCLUDED = ('BepInEx', 'winhttp.dll', 'doorstop_config.ini', 'SaveData')


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


def managed_manifest(source):
    folder = Path(source) / 'Taimashi_Data/Managed'
    return {'Taimashi_Data/Managed/' + name: value for name, value in manifest(folder).items()}


def source_digests(source):
    source = Path(source)
    result = {}
    for name, expected in SOURCE_DIGESTS.items():
        no_links(source / name)
        result[name] = file_digest(source / name)
        if result[name] != expected:
            raise ValueError('unsupported_unity_build:' + name)
    managed = managed_manifest(source)
    result.update(managed)
    if fingerprint(managed) != MANAGED_FINGERPRINT:
        raise ValueError('unsupported_unity_managed_build')
    return result


def artifact_digests(artifacts):
    artifacts = Path(artifacts)
    keys = ('winhttp.dll', 'GameQaBridge.dll', *('BepInEx/core/' + name for name in CORE_DLLS))
    result = {}
    for name in keys:
        no_links(artifacts / name)
        result[name] = file_digest(artifacts / name)
    if result['winhttp.dll'] != LOADER_DIGEST:
        raise ValueError('unsupported_unity_loader_build')
    return result


def inspect(source, artifacts):
    blockers = []
    try:
        source_digests(source)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择已登记的完整 Unity 2020.3 Mono 旧 Input 源目录。'))
    if importlib.util.find_spec('UnityPy') is None:
        blockers.append(dict(code='missing_dependency', message='准备副本需要已有 UnityPy。',
                             field='source', recovery='使用已配置 UnityPy 的环境；本入口不会自动安装。'))
    try:
        if artifacts is None:
            raise ValueError('请选择已有 Unity 旧 Input 桥、匹配的 Doorstop 和 BepInEx 5 核心目录。')
        artifact_digests(artifacts)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='目录需包含 GameQaBridge.dll、winhttp.dll 和 BepInEx/core。'))
    return blockers


def local_low():
    return Path(os.environ['LOCALAPPDATA']).parent / 'LocalLow'


def registry_snapshot(company, product):
    import winreg
    result = {}
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        def visit(name):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | view) as key:
                    children, values, _ = winreg.QueryInfoKey(key)
                    for index in range(values):
                        label, value, kind = winreg.EnumValue(key, index)
                        result[str(view) + '/' + name + '/' + label] = hashlib.sha256(
                            repr((kind, value)).encode('utf-8')).hexdigest()
                    for index in range(children):
                        visit(name + '\\' + winreg.EnumKey(key, index))
            except FileNotFoundError:
                pass
        visit('Software\\' + company + '\\' + product)
    return result


def patch_settings(data, company):
    import UnityPy
    env = UnityPy.load(str(data / 'globalgamemanagers'))
    settings = next(obj for obj in env.objects if obj.type.name == 'PlayerSettings')
    values = settings.read_typetree()
    if (values['companyName'], values['productName']) != ('DefaultCompany', 'Taimashi'):
        raise ValueError('unexpected_unity_save_identity')
    values.update(companyName=company, productName='CompatibilityProbe', runInBackground=True,
                  defaultScreenWidth=1024, defaultScreenHeight=768, defaultIsFullScreen=False,
                  fullscreen=False, fullscreenMode=3)
    if 'displayResolutionDialog' in values:
        values['displayResolutionDialog'] = 0
    settings.save_typetree(values)
    (data / 'globalgamemanagers').write_bytes(env.file.save())
    (data / 'app.info').write_text(company + '\nCompatibilityProbe\n', encoding='utf-8')


def prepare(plan):
    source, root, artifacts = (Path(plan[key]) for key in ('source', 'destination', 'artifacts'))
    before = manifest(source)
    original = local_low() / 'DefaultCompany/Taimashi'
    no_links(original)
    external = {'path': str(original), 'files': manifest(original),
                'registry': registry_snapshot('DefaultCompany', 'Taimashi')}
    root.mkdir()
    write_json(root / 'source-before.json', before)
    write_json(root / 'external-before.json', external)
    if any(before[name] != digest for name, digest in plan['source_digests'].items()):
        raise ValueError('source_changed_during_prepare')
    company = 'GameQaKitSession_' + uuid.uuid4().hex
    record = {'company': company, 'product': 'CompatibilityProbe',
              'junction': str(local_low() / company), 'persistent': str(root / 'persistent')}
    write_json(root / 'unity-preparation.json', record)
    runtime = root / 'runtime'
    shutil.copytree(source, runtime, ignore=lambda directory, names:
                    [name for name in names if name in EXCLUDED] if Path(directory) == source else [])
    expected = {name: value for name, value in before.items() if name.split('/')[0] not in EXCLUDED}
    for name, digest in plan['artifact_digests'].items():
        no_links(artifacts / name)
        if file_digest(artifacts / name) != digest:
            raise ValueError('artifact_changed_during_prepare:' + name)
        relative = 'BepInEx/plugins/GameQaKit/GameQaBridge.dll' if name == 'GameQaBridge.dll' else name
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifacts / name, target)
        expected[relative] = digest
    (runtime / 'doorstop_config.ini').write_text(
        '[General]\nenabled=true\ntarget_assembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirect_output_log=false\n'
        '[UnityMono]\ndebug_enabled=false\n[UnityDoorstop]\nenabled=true\n'
        'targetAssembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirectOutputLog=false\n', encoding='utf-8')
    patch_settings(runtime / 'Taimashi_Data', company)
    for name in ('doorstop_config.ini', 'Taimashi_Data/globalgamemanagers', 'Taimashi_Data/app.info'):
        expected[name] = file_digest(runtime / name)
    actual = manifest(runtime)
    if actual != expected:
        raise RuntimeError('copy_changed：副本内容与确认的来源不一致')
    write_json(root / 'runtime-before.json', actual)
    write_json(root / 'approved-plan.json', plan)
    return record, actual
