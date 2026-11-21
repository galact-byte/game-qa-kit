"""Validate Bakin loading progress and submit an explicit screenshot review."""
import hashlib
from pathlib import Path
import time

from PIL import Image
from .admission import no_links
from .control_time import LOADING_MODE
from .sessions import read_json, write_json


def pending(client, request, review=None):
    ident = request['id']
    if request.get('protocol') != 2 or request.get('payload', {}).get('loading_mode') != LOADING_MODE:
        if review is not None:
            raise ValueError('该命令未登记 Bakin 加载意图')
        return dict(id=ident, status='pending', ok=False)
    path = client.directory / 'progress' / (ident + '.json')
    if review is not None:
        if (not isinstance(review, dict) or set(review) != {'accepted', 'observation_seq', 'screenshot_sha256', 'loading_pause_id'}
                or review['accepted'] is not True or type(review['observation_seq']) is not int or review['observation_seq'] < 0):
            raise ValueError('加载审核必须明确接受并绑定本次截图、摘要和暂停令牌')
        path = client.directory / 'progress' / ident / (str(review['observation_seq']) + '.json')
    no_links(path)
    if not path.is_file():
        if review is not None:
            raise ValueError('没有该次加载截图审核记录')
        return dict(id=ident, status='pending', ok=False)
    progress = read_json(path)
    meta = client._connection()
    current = client._state(meta)
    now = time.monotonic() * 1000
    deadline = progress.get('loading_deadline_monotonic_ms')
    if (any(type(progress.get(k)) is not type(meta[k]) or progress[k] != meta[k]
            for k in ('protocol', 'session_id', 'pid', 'exe_path', 'engine'))
            or progress.get('session_id') != request['session_id'] or progress.get('id') != ident
            or progress.get('result_kind') != LOADING_MODE or progress.get('in_progress') is not True
            or progress.get('paused') is not True or progress.get('held') != []
            or progress.get('press_consumed') is not True or type(progress.get('release_consumed')) is not bool
            or progress.get('phase') not in ('loading', 'awaiting_visual_review')
            or type(progress.get('observation_seq')) is not int
            or type(deadline) is not int or not now < deadline <= now + 300000
            or current.get('paused') is not True or current.get('held') != []
            or current.get('loading', {}).get('command_id') != ident
            or current.get('loading', {}).get('pause_id') != progress.get('loading_pause_id')
            or client._pause_id(meta) != progress.get('loading_pause_id')):
        raise RuntimeError('加载进度缺少本次身份、期限或暂停无持键证据')
    if progress.get('observation_pending') is True:
        if (review is not None or meta.get('engine') != 'bakin' or progress['phase'] != 'loading'
                or progress['observation_seq'] != request['after_observation_seq']
                or any(k in progress for k in ('screenshot', 'screenshot_sha256', 'captured_monotonic_ms',
                                               'blank_frame', 'draw_seq'))):
            raise RuntimeError('无画面加载进度不能声称新截图或接受画面审核')
        return dict(progress, status='pending', ok=False, historical=False)
    if (progress['observation_seq'] <= request['after_observation_seq']
            or type(progress.get('captured_monotonic_ms')) is not int
            or progress['captured_monotonic_ms'] > now):
        raise RuntimeError('加载进度缺少本次新截图与采集时间')
    relative = progress.get('screenshot')
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise RuntimeError('加载截图路径无效')
    shot = (client.directory / relative).resolve()
    if shot != (client.directory / 'screenshots' / ('loading-' + str(progress['observation_seq']) + '.png')).resolve():
        raise RuntimeError('加载截图不属于本次观察')
    no_links(shot)
    if shot.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError('加载截图过大')
    if hashlib.sha256(shot.read_bytes()).hexdigest() != progress.get('screenshot_sha256'):
        raise RuntimeError('加载截图摘要不匹配')
    with Image.open(shot) as image:
        if image.format != 'PNG' or min(image.size) <= 2 or image.width * image.height > 16777216:
            raise RuntimeError('加载截图格式或尺寸无效')
        image.load()
    if review is not None:
        if (any(review[k] != progress.get(k) for k in ('observation_seq', 'screenshot_sha256', 'loading_pause_id'))
                or progress.get('phase') != 'awaiting_visual_review' or progress.get('release_consumed') is not True
                or progress.get('blank_frame') is not False or progress.get('scene') != 'MAP'
                or progress.get('map_loading') is not False or progress.get('map_fade') != 'None'
                or now - progress['captured_monotonic_ms'] >= 3000):
            raise ValueError('审核需要本次新地图画面及真实释放，不能接受过场或旧截图')
        write_json(client.directory / 'loading-review.json', dict(meta, id=ident, **review))
    return dict(progress, status='pending', ok=False, screenshot=str(shot), historical=False)
