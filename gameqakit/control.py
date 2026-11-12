"""统一的本地控制协议：精确目标、限帧动作、操作后证据和优先暂停。"""
from __future__ import annotations

import configparser
import hashlib
import io
import json
import math
from pathlib import Path
import re
import threading
from contextlib import contextmanager
import time
import uuid

from PIL import Image
from .sessions import SessionLease, write_json, write_text, read_json, read_text

_BUTTONS = {'up', 'down', 'left', 'right', 'ok', 'cancel', 'shift', 'pageup', 'pagedown'}
_OPTIONAL_BUTTONS = {'square'}
_ACTION_FIELDS = {'move': {'direction', 'frames'}, 'confirm': {'frames'}, 'cancel': {'frames'},
                  'wait': {'frames'}, 'press': {'button', 'frames'}, 'release': {'button', 'frames'},
                  'click': {'x', 'y', 'frames'}}


def _read(path):
    path = Path(path)
    value = read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError(f'控制文件必须是对象：{path.name}')
    return value


def _command_id(value=None):
    value = uuid.uuid4().hex if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('命令编号仅允许 1..64 个字母、数字、下划线或连字符')
    return value


def _same_path(left, right):
    return str(Path(left).resolve()).casefold() == str(Path(right).resolve()).casefold()


def _ini_text(value):
    flat = {}
    def flatten(prefix, item):
        if isinstance(item, dict):
            for key, child in item.items():
                flatten(prefix + '.' + key if prefix else key, child)
        else:
            flat[prefix] = '' if item is None else str(item)
    flatten('', value)
    parser = configparser.ConfigParser(interpolation=None)
    parser['bridge'] = flat
    output = io.StringIO()
    parser.write(output)
    return output.getvalue()


def _write_ini(path, value):
    write_text(path, _ini_text(value))


def write_connection(directory, meta):
    directory = Path(directory)
    write_json(directory / 'connection.json', meta)
    if meta.get('command_format') == 'ini':
        _write_ini(directory / 'connection.ini', meta)


class BridgeClient:
    def __init__(self, directory, *, pid=None, exe_path=None):
        self.directory = Path(directory)
        self.pid = pid
        self.exe_path = exe_path
        self._ipc = None
        self._ipc_meta = None
        self._requests = {}
        self._inflight = None

    def _mailbox(self, meta):
        if self._ipc is None:
            from .wolf_ipc import WolfMailbox
            self._ipc = WolfMailbox(meta['pid'], meta['session_id'])
        return self._ipc

    def _live_text(self, name, meta):
        deadline = time.monotonic() + .1
        while True:
            value = self._mailbox(meta).read(name)
            if value or time.monotonic() >= deadline:
                return value
            time.sleep(.002)

    def _connection(self):
        from .win32 import _pid_path
        meta = self._ipc_meta or _read(self.directory / 'connection.json')
        if (type(meta.get('protocol')) is not int or meta['protocol'] not in (1, 2) or not isinstance(meta.get('session_id'), str)
                or not meta['session_id'] or type(meta.get('pid')) is not int or meta['pid'] <= 0
                or not isinstance(meta.get('exe_path'), str) or not meta['exe_path']):
            raise RuntimeError('控制连接信息无效或协议不兼容')
        if self.pid is not None and self.pid != meta['pid']:
            raise RuntimeError('控制插件不属于所选 PID')
        if self.exe_path is None or not _same_path(self.exe_path, meta['exe_path']):
            raise RuntimeError('请先绑定精确程序路径；插件路径与 profile 必须一致')
        if meta.get('command_format', 'json') not in ('json', 'ini'):
            raise RuntimeError('不支持的控制传输格式')
        transport = meta.get('control_transport', 'files-v1')
        if transport not in ('files-v1', 'wolf-shm-v1'):
            raise RuntimeError('不支持的控制通道；不会静默回退')
        if meta['protocol'] == 2 and (transport != 'files-v1' or meta.get('command_format', 'json') != 'json'):
            raise RuntimeError('时间协议首版仅使用 JSON 文件通道')
        if transport == 'wolf-shm-v1' and meta.get('command_format') != 'ini':
            raise RuntimeError('Wolf 共享内存要求 INI 命令格式')
        actual = _pid_path(meta['pid'])
        if not actual or not _same_path(actual, meta['exe_path']):
            raise RuntimeError('目标进程已退出或路径不匹配')
        if transport == 'wolf-shm-v1':
            self._mailbox(meta)
            self._ipc_meta = meta
        return meta

    def _state(self, meta):
        state = (json.loads(self._live_text('status', meta) or '{}') if self._ipc_meta
                 else _read(self.directory / 'status.json'))
        if not isinstance(state, dict):
            raise RuntimeError('插件状态必须是对象')
        if (type(state.get('protocol')) is not int or state['protocol'] != meta['protocol'] or state.get('session_id') != meta['session_id']
                or state.get('pid') != meta['pid']
                or not isinstance(state.get('exe_path'), str)
                or not _same_path(state['exe_path'], meta['exe_path'])):
            raise RuntimeError('插件心跳属于其他进程、会话或协议')
        updated = state.get('updated_at')
        if (type(updated) not in (int, float) or not math.isfinite(updated)
                or not -1 <= time.time() - updated <= 2):
            raise RuntimeError('插件心跳过期，停止自动操作')
        if (type(state.get('paused')) is not bool
                or not isinstance(state.get('capabilities'), list)
                or not all(isinstance(item, str) for item in state['capabilities'])):
            raise RuntimeError('插件状态缺少有效模式或能力声明')
        if meta['protocol'] == 2:
            from .control_time import validate_state
            validate_state(state)
        elif type(state.get('frame')) is not int or state['frame'] < 0:
            raise RuntimeError('插件状态缺少有效逻辑帧')
        return state

    def status(self):
        try:
            meta = self._connection()
            return {'connected': True, **self._state(meta)}
        except (OSError, ValueError, RuntimeError) as exc:
            return {'connected': False, 'paused': True, 'error': str(exc)}

    def _wire_write(self, name, value, meta=None):
        meta = meta if meta is not None else self._connection()
        if meta.get('control_transport') == 'wolf-shm-v1':
            deadline = time.monotonic() + .1
            while True:
                try:
                    self._mailbox(meta).write(name, _ini_text(value))
                    return
                except RuntimeError as exc:
                    if str(exc) != 'ipc_writer_busy' or time.monotonic() >= deadline:
                        raise
                    time.sleep(.002)
        if meta.get('command_format') == 'ini':
            _write_ini(self.directory / (name + '.ini'), value)
        else:
            write_json(self.directory / (name + '.json'), value)

    def _pause_id(self, meta=None):
        meta = meta if meta is not None else self._connection()
        if meta.get('control_transport') == 'wolf-shm-v1':
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(self._live_text('pause', meta))
            value = dict(parser['bridge']) if parser.has_section('bridge') else {}
        elif meta.get('command_format') == 'ini':
            parser = configparser.ConfigParser(interpolation=None)
            path = self.directory / 'pause.ini'
            if path.exists():
                parser.read_string(read_text(path))
            value = dict(parser['bridge']) if parser.has_section('bridge') else {}
        else:
            path = self.directory / 'pause.json'
            value = _read(path) if path.exists() else {}
        return value.get('id', '') if value.get('session_id') == meta['session_id'] else ''

    def _request_pause(self, meta):
        self._wire_write('pause', {'id': uuid.uuid4().hex, 'session_id': meta['session_id'],
                                   'at': time.time()}, meta)
        return {'state': 'pause_requested', 'confirmed': False}

    def pause(self):
        # 不能排在普通动作的锁后面，否则“人工接管”会等长按执行完。
        return self._request_pause(self._connection())

    def action(self, action, *, command_id=None, timeout=5.0, **options):
        if action not in _ACTION_FIELDS or set(options) - (_ACTION_FIELDS[action] | {'duration_ms'}):
            raise ValueError('未知动作或不适用于此动作的参数')
        meta = self._connection()
        if meta['protocol'] == 2:
            return self._submit({'action': action, **options}, command_id, timeout)
        if 'duration_ms' in options:
            raise ValueError('帧协议不接受 duration_ms，不自动换算')
        frames = options.pop('frames', 1)
        if type(frames) is not int or not 1 <= frames <= 120:
            raise ValueError('frames 必须是 1..120 的整数')
        if action == 'move' and options.get('direction') not in {'up', 'down', 'left', 'right'}:
            raise ValueError('移动方向必须为 up/down/left/right')
        if action == 'press' and options.get('button') not in _BUTTONS | _OPTIONAL_BUTTONS:
            raise ValueError('不支持的引擎按钮')
        if action == 'release' and options.get('button') is not None and options['button'] not in _BUTTONS | _OPTIONAL_BUTTONS:
            raise ValueError('不支持的引擎按钮')
        if action == 'click' and any(type(options.get(key)) is not int or options[key] < 0 for key in ('x', 'y')):
            raise ValueError('点击坐标必须是非负整数')
        return self._submit({'action': action, **options, 'frames': frames}, command_id, timeout)

    def resume(self, *, command_id=None, timeout=5.0):
        payload = {'action': 'resume'}
        if self._connection()['protocol'] == 1:
            payload['frames'] = 1
        return self._submit(payload, command_id, timeout)

    @contextmanager
    def _renewing(self, meta, deadline):
        stop = threading.Event()
        failures = []
        def renew():
            try:
                while not stop.wait(.1) and time.monotonic() < deadline:
                    self._wire_write('lease', {'session_id': meta['session_id'],
                                             'expires_at': time.time() + 1.5}, meta)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=renew, name='control-lease', daemon=True)
        worker.start()
        try:
            yield failures
        finally:
            stop.set()
            worker.join()

    def _submit(self, payload, command_id, timeout):
        command_id = _command_id(command_id)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.05 <= timeout <= 15:
            raise ValueError('timeout 必须在 0.05..15 秒之间')
        with SessionLease(self.directory / 'host.lock'):
            meta = self._connection()
            stored = self.directory / 'requests' / (command_id + '.json')
            if stored.exists():
                request = _read(stored)
                if request.get('session_id') != meta['session_id'] or request.get('payload') != payload:
                    raise ValueError('同一命令编号不能用于其他会话或不同动作')
                result = self.result(command_id)
                if result.get('status') == 'pending':
                    raise RuntimeError(f'命令 {command_id} 尚未确认，请查询结果，不重复发送')
                return result
            state = self._state(meta)
            if meta['protocol'] == 2:
                from .control_time import validate_payload
                validate_payload(payload, state)
            elif 'duration_ms' in payload or 'frames' not in payload:
                raise ValueError('帧协议需要 frames，不接受时间动作')
            if payload['action'] != 'resume':
                if state['paused']:
                    raise RuntimeError('控制已暂停，请明确恢复后再发动作')
                if payload['action'] not in state['capabilities']:
                    raise RuntimeError('当前插件未声明此动作能力；不会降级为桌面输入')
                button = payload.get('button')
                if button in _OPTIONAL_BUTTONS:
                    buttons = state.get('buttons')
                    if not isinstance(buttons, list) or button not in buttons:
                        raise RuntimeError('当前插件未声明此按钮能力')
                if payload['action'] not in ('wait', 'release') and state.get('input_ready') is False:
                    raise RuntimeError('当前界面正在转场或尚未就绪，请先等待，不盲目发输入')
            if meta['protocol'] == 1:
                maximum = state.get('max_input_frames', 120)
                if type(maximum) is not int or not 1 <= maximum <= 120:
                    raise RuntimeError('插件的按键限帧声明无效')
                if payload['action'] not in ('wait', 'resume', 'release') and payload['frames'] > maximum:
                    raise ValueError(f'当前插件输入最多持续 {maximum} 帧；不会擅自缩短或模拟桌面输入')
            if payload['action'] == 'click':
                screen = state.get('screen', [])
                if (not isinstance(screen, list) or len(screen) != 2
                        or any(type(v) is not int or v <= 0 for v in screen)
                        or payload['x'] >= screen[0] or payload['y'] >= screen[1]):
                    raise ValueError('点击坐标超出插件声明的游戏画面')
            observation = ({'after_observation_seq': state['observation_seq']} if meta['protocol'] == 2
                           else {'after_frame': state['frame']})
            request = {'protocol': meta['protocol'], 'session_id': meta['session_id'], 'id': command_id,
                       'payload': payload, **observation,
                       'pause_id': self._pause_id(meta), 'created_at': time.time()}
            write_json(stored, request)
            if self._ipc_meta:
                self._requests[command_id] = request
            deadline = time.monotonic() + timeout
            self._inflight = command_id
            try:
                self._wire_write('lease', {'session_id': meta['session_id'], 'expires_at': time.time() + 1.5}, meta)
                with self._renewing(meta, deadline) as failures:
                    self._wire_write('request', request, meta)
                    while time.monotonic() < deadline:
                        if failures:
                            raise RuntimeError('控制续租失败') from failures[0]
                        result = self.result(command_id)
                        if result.get('status') != 'pending':
                            if not result.get('ok'):
                                raise RuntimeError(result.get('error', '插件拒绝或未完成动作'))
                            if failures or time.monotonic() >= deadline:
                                break
                            return result
                        time.sleep(0.02)
                raise TimeoutError(f'命令 {command_id} 结果未知；已请求暂停，请查询结果，禁止盲目重发')
            except Exception:
                self._request_pause(meta)
                raise
            finally:
                self._inflight = None
                self._requests.pop(command_id, None)

    def result(self, command_id):
        command_id = _command_id(command_id)
        request = self._requests.get(command_id) if self._ipc_meta else None
        if request is None:
            request_path = self.directory / 'requests' / (command_id + '.json')
            if not request_path.exists():
                return {'id': command_id, 'status': 'unknown', 'ok': False}
            request = _read(request_path)
        result = None
        if self._ipc_meta:
            live = json.loads(self._mailbox(self._ipc_meta).read('result') or '{}')
            if not isinstance(live, dict):
                raise RuntimeError('回执必须是对象')
            if live.get('id') == command_id:
                result = live
            elif self._inflight == command_id:
                return {'id': command_id, 'status': 'pending', 'ok': False}
        if result is None:
            result_path = self.directory / 'results' / (command_id + '.json')
            if not result_path.exists():
                return {'id': command_id, 'status': 'pending', 'ok': False}
            result = _read(result_path)
        if (type(result.get('protocol')) is not int or result['protocol'] != request.get('protocol') or result.get('session_id') != request.get('session_id')
                or result.get('id') != command_id):
            raise RuntimeError('回执不属于本次命令和会话')
        if result.get('ok') is not True:
            return {**result, 'status': 'failed'}
        if request['protocol'] == 2:
            from .control_time import validate_result
            validate_result(result, request)
        elif (result.get('consumed') is not True or type(result.get('frame')) is not int
                or type(result.get('capture_frame')) is not int
                or result['frame'] <= request['after_frame']
                or result['capture_frame'] < result['frame']):
            raise RuntimeError('缺少操作后新帧，停止自动操作')
        relative = result.get('screenshot')
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise RuntimeError('截图路径必须位于本次控制证据目录')
        shot = (self.directory / relative).resolve()
        if not shot.is_relative_to(self.directory.resolve()):
            raise RuntimeError('截图路径越界')
        if shot.parent != (self.directory / 'screenshots').resolve() or shot.name not in (command_id + '.png', command_id + '.bmp'):
            raise RuntimeError('截图不属于本次命令')
        try:
            if shot.stat().st_size > 16 * 1024 * 1024:
                raise RuntimeError('控制截图过大')
            with Image.open(shot) as image:
                if image.format not in ('PNG', 'BMP') or image.width * image.height > 16777216:
                    raise RuntimeError('控制截图格式或尺寸无效')
                image.load()
                extrema = image.convert('RGB').getextrema()
                if extrema in (((0, 0),) * 3, ((255, 255),) * 3):
                    raise RuntimeError('操作后截图是黑帧或白帧')
                if image.format == 'BMP':
                    import os
                    converted = shot.with_suffix('.png')
                    temporary = converted.with_suffix('.png.part')
                    try:
                        image.save(temporary, format='PNG')
                        os.replace(temporary, converted)
                    finally:
                        temporary.unlink(missing_ok=True)
                    shot = converted
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'操作后截图不可读：{exc}') from exc
        current = self.status()
        historical = (not current.get('connected') or current.get('session_id') != request['session_id']
                      or self._pause_id() != request.get('pause_id', ''))
        digest = hashlib.sha256(shot.read_bytes()).hexdigest()
        if request['protocol'] == 2 and result.get('screenshot_sha256') != digest:
            raise RuntimeError('时间动作截图摘要不匹配')
        return {**result, 'status': 'done', 'screenshot': str(shot), 'historical': historical,
                'screenshot_sha256': digest}
