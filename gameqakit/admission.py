"""只读接入检查与版本化操作摘要；不启动、不加载、不发游戏输入。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .engines import inspect_game


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def no_links(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('reparse_path_rejected：请选择不经过链接的真实目录')


def validate_destination(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    no_links(source)
    no_links(destination)
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('overlapping_paths：QA 会话与原游戏目录不能重叠')
    if destination.exists():
        raise ValueError('destination_exists：每次接入必须使用全新会话目录')
    return destination


def inspect(path, *, artifacts=None):
    no_links(path)
    result = inspect_game(path)
    result.update(schema_version=1, manual_observation=True, launch_readiness='unknown', blockers=[])

    def block(code, message, field, recovery):
        result['blockers'].append(dict(code=code, message=message, field=field, recovery=recovery))

    if result['engine'] == 'unknown':
        block('unknown_engine', '尚未识别这个游戏的后台接入方式。', 'source', '请正常打开游戏，使用人工盯屏。')
    elif result['engine'] == 'unity_mono':
        from plugins.unity import prepare as unity
        result['blockers'] = unity.inspect(Path(result['root']), artifacts)
        result['launch_readiness'] = 'blocked' if result['blockers'] else 'ready'
        if not result['blockers']:
            result['profile_id'] = 'unity-mono-legacy-taimashi-fixed'
    elif result['engine'] in ('rpgmaker_mv', 'rpgmaker_mz'):
        from plugins.rpgmaker_js import prepare as rpgmaker
        result['blockers'] = rpgmaker.inspect(Path(result['root']), artifacts, result['engine'])
        result['launch_readiness'] = 'blocked' if result['blockers'] else 'ready'
        if not result['blockers']:
            result['profile_id'] = result['engine'] + '-sdk-fixed'
    elif result.get('profile_id') != 'srpgstudio-x86-fixed':
        result['launch_readiness'] = 'not_migrated'
        block('recipe_unavailable', '已识别引擎，但这个构建尚无已核查的产品启动流程。',
              'source', '仍可人工盯屏；后台接入需先补齐该构建的准备和保存边界。')
    else:
        from plugins.native_input.prepare import RESOURCES, validate_image
        source = Path(result['root'])
        try:
            validate_image(source / 'game.exe')
            for name in RESOURCES:
                item = source / name
                no_links(item)
                if not item.exists():
                    raise ValueError('missing_known_resource:' + name)
            config = (source / 'game.ini').read_bytes()
            if config.count(b'NonActiveAnimationPlay=1') != 1 or config.count(b'NonActiveMusicPlay=0') != 1:
                raise ValueError('unsupported_background_configuration')
        except (OSError, ValueError) as exc:
            block('source_not_ready', str(exc), 'source', '请选择完整、未经准备修改的已登记游戏源目录。')
        artifacts = Path(artifacts).absolute() if artifacts else None
        if artifacts:
            for name in ('native-host.exe', 'native-input.dll'):
                no_links(artifacts / name)
                if not (artifacts / name).is_file():
                    block('missing_artifact', '缺少本机产物：' + name, 'artifacts', '选择已有完整构建目录。')
        else:
            block('missing_artifact', '尚未选择已有原生宿主和控制桥。', 'artifacts', '选择已有构建目录，无需重新安装。')
        result['launch_readiness'] = 'blocked' if result['blockers'] else 'ready'
    return result


def make_plan(source, destination, *, artifacts):
    result = inspect(source, artifacts=artifacts)
    if result['launch_readiness'] != 'ready':
        raise ValueError('; '.join(row['message'] for row in result['blockers']))
    destination = validate_destination(result['root'], destination)
    source, artifacts = Path(result['root']), Path(artifacts).absolute()
    if result['engine'] == 'unity_mono':
        from plugins.unity import prepare as unity
        payload = dict(schema_version=1, backend='unity_mono_legacy', source=str(source),
                       destination=str(destination), artifacts=str(artifacts),
                       source_digests=unity.source_digests(source),
                       artifact_digests=unity.artifact_digests(artifacts),
                       data_policy='blank-unity-identity-preserve-progress', desktop='private',
                       changes=['只修改副本：独立产品标识、后台运行和窗口化设置，不带原 SaveData 和插件',
                                '加载所选 Unity 旧 Input 桥、BepInEx 5 和匹配的 Doorstop；不自动输入',
                                '随机产品标识使用目录联接将 LocalLow 游戏数据留在 QA 会话内',
                                '启动后确认输入暂停、松键及有效引擎首图；不宣称停止 Unity 仿真',
                                '结束自有进程，核对原目录、原存档和 PlayerPrefs；移除自有联接，保留新进度'])
        return {**payload, 'fingerprint': fingerprint(payload)}
    if result['engine'] in ('rpgmaker_mv', 'rpgmaker_mz'):
        from plugins.rpgmaker_js import prepare as rpgmaker
        validate_destination(artifacts, destination)
        payload = dict(schema_version=1, backend='rpgmaker_js', engine=result['engine'], source=str(source),
                       destination=str(destination), artifacts=str(artifacts),
                       source_digests=fingerprint(rpgmaker.source_digests(source, result['engine'])),
                       artifact_digests=fingerprint(rpgmaker.artifact_digests(artifacts)),
                       data_policy='blank-sdk-progress-private-nwjs-profile', desktop='private',
                       changes=['只修改副本：完整工程与已登记 NW.js，不带原存档',
                                '加载现有 JavaScript 控制桥；随机产品标识、独立浏览器数据与临时目录',
                                '初始暂停、松键并取得有效引擎首图；不自动输入',
                                '结束自有进程并核对原工程、运行时和原浏览器数据；保留新增进度'])
        return {**payload, 'fingerprint': fingerprint(payload)}
    keys = ('game.exe', 'game.ini', 'data.dts', 'runtime.rts', 'system.dat', 'environment.evs')
    payload = dict(schema_version=1, backend='native_input', source=str(source),
                   destination=str(destination), artifacts=str(artifacts),
                   source_digests={name: file_digest(source / name) for name in keys},
                   artifact_digests={name: file_digest(artifacts / name) for name in ('native-host.exe', 'native-input.dll')},
                   data_policy='blank-menu-only-preserve-runtime', desktop='current',
                   changes=['只读原目录；新建完整资源副本，不带原 Save',
                            '仅副本 game.ini 修改已核查的后台选项',
                            '加载所选本机原生桥，初始暂停；不自动发菜单动作',
                            '结束自有进程并核对基线，保留副本及可能的新进度',
                            '新入口独占动作客户端，不与旧客户端同时控制同一桥'])
    return {**payload, 'fingerprint': fingerprint(payload)}


def revalidate_plan(plan):
    if plan.get('schema_version') != 1 or plan.get('backend') not in ('native_input', 'unity_mono_legacy', 'rpgmaker_js'):
        raise ValueError('unsupported_plan')
    actual = make_plan(plan['source'], plan['destination'], artifacts=plan['artifacts'])
    if actual != plan:
        raise ValueError('plan_changed：原游戏、构建或操作摘要已变化，请重新检查并确认')
    return actual


def preference_locator():
    from .profile import profiles_dir
    return profiles_dir() / '.admission-location.txt'


def load_preferences(qa_root=None):
    from .sessions import read_json, read_text
    if qa_root is None:
        locator = preference_locator()
        no_links(locator)
        if not locator.exists():
            return {}
        qa_root = read_text(locator).strip()
        if not qa_root:
            raise ValueError('invalid_preferences_location')
    root = Path(qa_root).absolute()
    no_links(root)
    path = root / 'admission-preferences.json'
    no_links(path)
    if not path.exists():
        return {'qa_root': str(root)}
    value = read_json(path)
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or set(value) - {'schema_version', 'source', 'artifacts'}
            or any(not isinstance(value.get(key, ''), str) for key in ('source', 'artifacts'))):
        raise ValueError('invalid_preferences：目录偏好格式不受支持，请重新选择目录')
    return {**value, 'qa_root': str(root)}


def save_preferences(qa_root, *, source, artifacts):
    from .sessions import write_json, write_text
    root, source_path = Path(qa_root).absolute(), Path(source).absolute()
    no_links(root)
    no_links(source_path)
    if root == source_path or root in source_path.parents or source_path in root.parents:
        raise ValueError('overlapping_paths：QA 数据目录与游戏源目录不能重叠')
    path, locator = root / 'admission-preferences.json', preference_locator()
    no_links(path)
    no_links(locator)
    root.mkdir(parents=True, exist_ok=True)
    write_json(path, {'schema_version': 1, 'source': str(source_path), 'artifacts': artifacts})
    write_text(locator, str(root))


def list_sessions(qa_root, *, limit=100):
    from .sessions import read_json
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit 必须为 1–100 的整数')
    root = Path(qa_root).absolute()
    no_links(root)
    rows, errors, truncated = [], [], False
    # 只检查父目录的直接子项；总检查数量同样有界，损坏记录不能造成无限扫描。
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 1000:
                truncated = True
                break
            folder = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith('.'):
                continue
            try:
                no_links(folder)
                record = folder / 'session.json'
                no_links(record)
                if not record.is_file():
                    continue
                value = read_json(record)
                if not isinstance(value, dict) or value.get('schema_version') != 1:
                    raise ValueError('unsupported_session_record')
                if len(rows) >= limit:
                    truncated = True
                    break
                rows.append({**value, 'directory': str(folder), 'bridge_directory': str(folder / 'bridge')})
            except (OSError, ValueError) as exc:
                errors.append({'directory': str(folder), 'error': str(exc)})
    return {'sessions': rows, 'errors': errors, 'truncated': truncated}
