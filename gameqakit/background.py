"""指定窗口的后台消息与提权代理客户端；不使用系统键鼠或前台 API。"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import time
import uuid
from pathlib import Path


FIELDS = {'kind', 'cx', 'cy', 'end_cx', 'end_cy', 'steps', 'hold_ms', 'interval_ms', 'delta', 'command_id'}


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} 必须为 {low}..{high} 的整数')
    return value


def validate_action(action, width, height):
    if not isinstance(action, dict) or set(action) - FIELDS:
        raise ValueError('输入含未知字段，不允许重定向目标或执行其他操作')
    kind = action.get('kind')
    if kind not in ('click', 'drag', 'wheel'):
        raise ValueError('仅支持 click、drag、wheel')
    result = dict(action)
    for key, maximum in (('cx', width - 1), ('cy', height - 1)):
        result[key] = _integer(action.get(key), 0, min(maximum, 32767), key)
    result['interval_ms'] = _integer(action.get('interval_ms', 600), 600, 1000, 'interval_ms')
    if kind == 'drag':
        for key, maximum in (('end_cx', width - 1), ('end_cy', height - 1)):
            result[key] = _integer(action.get(key), 0, min(maximum, 32767), key)
        result['steps'] = _integer(action.get('steps', 6), 2, 12, 'steps')
    elif kind == 'click':
        result['hold_ms'] = _integer(action.get('hold_ms', 600), 600, 10000, 'hold_ms')
    else:
        result['delta'] = _integer(action.get('delta', -120), -1200, 1200, 'delta')
        if result['delta'] == 0:
            raise ValueError('滚轮 delta 不可为零')
    return result


def perform_action(action, width, height, post, *, sleep=time.sleep):
    action = validate_action(action, width, height)
    x, y = action['cx'], action['cy']
    interval = action['interval_ms'] / 1000
    post(0x200, 0, x, y)
    sleep(interval)
    if action['kind'] == 'wheel':
        post(0x20A, (action['delta'] & 0xffff) << 16, x, y)
    else:
        # 失败与取消后仍尝试向同一个窗口松开，绝不调用系统鼠标释放。
        try:
            post(0x201, 1, x, y)
            if action['kind'] == 'click':
                sleep(action['hold_ms'] / 1000)
            else:
                for index in range(1, action['steps'] + 1):
                    sleep(interval)
                    x = round(action['cx'] + (action['end_cx'] - action['cx']) * index / action['steps'])
                    y = round(action['cy'] + (action['end_cy'] - action['cy']) * index / action['steps'])
                    post(0x200, 1, x, y)
                sleep(interval)
        finally:
            post(0x202, 0, x, y)
    return {'messages_posted': True, 'game_responded': None,
            'delivery_semantics': 'PostMessage 返回成功仅代表入队，游戏响应需对照截图或人工确认',
            'kind': action['kind']}


class Manager:
    def __init__(self):
        self.handle = None
        self.pipe = None
        self.agent_pid = None
        self.target = None
        self.child = None

    def start(self, hwnd, pid, *, elevate=True):
        from . import background_agent as native
        _integer(hwnd, 1, 2**63 - 1, 'hwnd')
        _integer(pid, 1, 2**32 - 1, 'pid')
        if type(elevate) is not bool:
            raise ValueError('elevate 必须为布尔值')
        if self.handle and native.alive(self.handle):
            if self.target == (hwnd, pid):
                return self.status()
            raise RuntimeError('代理已绑定其他目标，请先 background_stop')
        self.close()
        native.check_window(hwnd, pid)
        self.pipe = r'\\.\pipe\gameqa-bg-' + uuid.uuid4().hex
        self.handle, self.agent_pid, self.child = native.launch(self.pipe, hwnd, pid, elevate)
        self.target = (hwnd, pid)
        return {'running': True, 'ready': False, 'hwnd': hwnd, 'pid': pid, 'agent_pid': self.agent_pid,
                'uac': 'requested' if elevate else 'not_requested',
                'next': '调用 background_status 等待 ready=true；拒绝 UAC 时不会启动代理'}

    def _request(self, value):
        from . import background_agent as native
        if not self.handle or not native.alive(self.handle):
            raise RuntimeError('后台代理未运行，请先 background_start')
        result = native.request(self.pipe, self.agent_pid, value)
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    def status(self):
        from . import background_agent as native
        if not self.handle or not native.alive(self.handle):
            return {'running': False, 'ready': False}
        try:
            return self._request({'op': 'status'})
        except (OSError, TimeoutError, RuntimeError) as error:
            return {'running': native.alive(self.handle), 'ready': False, 'error': str(error)}

    def input(self, action):
        ident = action.get('command_id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident):
            raise ValueError('输入必须带唯一 command_id；同编号重试返回原结果')
        return self._request({'op': 'input', 'action': action})

    def capture(self, out):
        from PIL import Image
        out = Path(out)
        if not out.is_absolute() or out.suffix.lower() != '.png' or out.exists():
            raise ValueError('截图必须使用尚不存在的绝对 PNG 路径')
        result = self._request({'op': 'capture'})
        data = base64.b64decode(result.pop('png'), validate=True)
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        out.parent.mkdir(parents=True, exist_ok=True)
        # 特权端不接收落盘路径；避免代理成为任意特权文件写入接口。
        with out.open('xb') as stream:
            stream.write(data)
        return {**result, 'out': str(out), 'sha256': hashlib.sha256(data).hexdigest()}

    def stop(self):
        from . import background_agent as native
        result = {'running': False, 'ready': False}
        try:
            if self.handle and native.alive(self.handle):
                result = self._request({'op': 'stop'})
                native.k.WaitForSingleObject(self.handle, 2000)
                if native.alive(self.handle):
                    raise RuntimeError('代理尚未退出，不报告停止成功')
            return result
        finally:
            if self.handle and not native.alive(self.handle):
                native.k.CloseHandle(self.handle)
                self.handle = None
            if self.child:
                self.child.wait(timeout=2)
                self.child = None

    def close(self):
        self.stop()
