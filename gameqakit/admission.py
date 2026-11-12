"""只读接入检查与版本化操作摘要；不启动、不加载、不发游戏输入。"""
from __future__ import annotations

import hashlib
import json
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
    if plan.get('schema_version') != 1 or plan.get('backend') != 'native_input':
        raise ValueError('unsupported_plan')
    actual = make_plan(plan['source'], plan['destination'], artifacts=plan['artifacts'])
    if actual != plan:
        raise ValueError('plan_changed：原游戏、构建或操作摘要已变化，请重新检查并确认')
    return actual
