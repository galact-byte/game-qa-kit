"""固定 Unity Mono 旧 Input 与新 Input System 的只读登记和副本准备。"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
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
INPUT_SYSTEM_DIGESTS = {
    'OmaNeko-Seikatsu.exe': '7012cb287d675a41e5461903b5591cbb5b3f866c64d246cd771e9f4547df20b5',
    'UnityPlayer.dll': '887a53283f4fb0614aa88e6421e9b188c73e43a7cf44247fa445a8a08111c73d',
    'OmaNeko-Seikatsu_Data/globalgamemanagers': '69792dc36ee581d3f21024f0ef1718b51a1e6193984886c5bb67b2280071a96d',
    'OmaNeko-Seikatsu_Data/Managed/Assembly-CSharp.dll': '1e076881b019a4a8b65b58fe0119c2488d1afd14571656621c53a585c8966795',
}
INPUT_SYSTEM_MANAGED_FINGERPRINT = '5e20afa59934b3b27746b55e37f666f3b6638c086553de90f813e4b9a56c51fc'
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


def build_profile(source):
    if (Path(source) / 'OmaNeko-Seikatsu.exe').is_file():
        return dict(profile_id='unity-mono-input-system-omaneko-fixed', backend='unity_mono_input_system',
                    executable='OmaNeko-Seikatsu.exe', data='OmaNeko-Seikatsu_Data',
                    company='omaneko', product='OmaNeko-Seikatsu', input_backend='input_system',
                    digests=INPUT_SYSTEM_DIGESTS, managed_fingerprint=INPUT_SYSTEM_MANAGED_FINGERPRINT)
    return dict(profile_id='unity-mono-legacy-taimashi-fixed', backend='unity_mono_legacy',
                executable='Taimashi.exe', data='Taimashi_Data', company='DefaultCompany',
                product='Taimashi', input_backend='legacy', digests=SOURCE_DIGESTS,
                managed_fingerprint=MANAGED_FINGERPRINT)


def managed_manifest(source):
    data = build_profile(source)['data']
    folder = Path(source) / data / 'Managed'
    return {data + '/Managed/' + name: value for name, value in manifest(folder).items()}


def source_digests(source):
    source = Path(source)
    profile = build_profile(source)
    result = {}
    for name, expected in profile['digests'].items():
        no_links(source / name)
        result[name] = file_digest(source / name)
        if result[name] != expected:
            raise ValueError('unsupported_unity_build:' + name)
    managed = managed_manifest(source)
    result.update(managed)
    if fingerprint(managed) != profile['managed_fingerprint']:
        raise ValueError('unsupported_unity_managed_build')
    return result


def artifact_digests(artifacts, *, input_system=False):
    artifacts = Path(artifacts)
    keys = ('winhttp.dll', 'GameQaBridge.dll', *('BepInEx/core/' + name for name in CORE_DLLS))
    if input_system:
        keys += ('GameQaInputSystem.dll',)
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
                             recovery='请选择已登记的完整 Unity Mono 固定构建源目录。'))
    if importlib.util.find_spec('UnityPy') is None:
        blockers.append(dict(code='missing_dependency', message='准备副本需要已有 UnityPy。',
                             field='source', recovery='使用已配置 UnityPy 的环境；本入口不会自动安装。'))
    try:
        if artifacts is None:
            raise ValueError('请选择匹配的 Unity 桥、Doorstop 和 BepInEx 5 核心目录。')
        artifact_digests(artifacts, input_system=build_profile(source)['input_backend'] == 'input_system')
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='missing_artifact', message=str(exc), field='artifacts',
                             recovery='目录需包含对应输入后端的桥、winhttp.dll 和 BepInEx/core。'))
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


def patch_input_settings(data, company, product):
    import UnityPy
    if (data / 'app.info').read_text(encoding='utf-8').splitlines() != ['omaneko', 'OmaNeko-Seikatsu']:
        raise ValueError('unexpected_unity_save_identity')
    env = UnityPy.load(str(data / 'globalgamemanagers'))
    settings = next(obj for obj in env.objects if obj.type.name == 'PlayerSettings')
    # 本固定 Unity 6 类型树无法正常解析；只替换已核查的定长标识，保留其他序列化字段。
    raw = settings.get_raw_data()
    for original, replacement in (('omaneko', company), ('OmaNeko-Seikatsu', product)):
        old, new = original.encode('utf-8'), replacement.encode('ascii')
        marker = struct.pack('<I', len(old)) + old
        if len(old) != len(new) or raw.count(marker) != 1:
            raise ValueError('private_identity_marker_mismatch')
        raw = raw.replace(marker, struct.pack('<I', len(new)) + new)
    settings.set_raw_data(raw)
    (data / 'globalgamemanagers').write_bytes(env.file.save())
    (data / 'app.info').write_text(company + '\n' + product + '\n', encoding='utf-8')


def prepare(plan):
    source, root, artifacts = (Path(plan[key]) for key in ('source', 'destination', 'artifacts'))
    profile = build_profile(source)
    before = manifest(source)
    original = local_low() / profile['company'] / profile['product']
    no_links(original)
    external = {'path': str(original), 'files': manifest(original),
                'company': profile['company'], 'product': profile['product'],
                'registry': registry_snapshot(profile['company'], profile['product'])}
    root.mkdir()
    write_json(root / 'source-before.json', before)
    write_json(root / 'external-before.json', external)
    if any(before[name] != digest for name, digest in plan['source_digests'].items()):
        raise ValueError('source_changed_during_prepare')
    token = uuid.uuid4().hex
    company, product = 'GameQaKitSession_' + token, 'CompatibilityProbe'
    if profile['input_backend'] == 'input_system':
        company, product = 'QA' + token[:len(profile['company']) - 2], 'QA' + token[:len(profile['product']) - 2]
    record = {'company': company, 'product': product,
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
        relative = 'BepInEx/plugins/GameQaKit/' + name if name in ('GameQaBridge.dll', 'GameQaInputSystem.dll') else name
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifacts / name, target)
        expected[relative] = digest
    (runtime / 'doorstop_config.ini').write_text(
        '[General]\nenabled=true\ntarget_assembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirect_output_log=false\n'
        '[UnityMono]\ndebug_enabled=false\n[UnityDoorstop]\nenabled=true\n'
        'targetAssembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirectOutputLog=false\n', encoding='utf-8')
    data = profile['data']
    if profile['input_backend'] == 'input_system':
        patch_input_settings(runtime / data, company, product)
    else:
        patch_settings(runtime / data, company)
    for name in ('doorstop_config.ini', data + '/globalgamemanagers', data + '/app.info'):
        expected[name] = file_digest(runtime / name)
    actual = manifest(runtime)
    if actual != expected:
        raise RuntimeError('copy_changed：副本内容与确认的来源不一致')
    write_json(root / 'runtime-before.json', actual)
    write_json(root / 'approved-plan.json', plan)
    return record, actual
