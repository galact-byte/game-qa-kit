"""显式桥绑定及受管会话入口；旧 profile 不隐式接管受控目标。"""
from __future__ import annotations

from pathlib import Path
import threading
import time

from .control import BridgeClient
from .profile import GameProfile
from .sessions import SessionLease, read_json


class Binding:
    """engaged 保留解绑／失效后的空目标，防止调用方回退旧 profile。"""
    def __init__(self):
        self.engaged = False
        self.client = self.profile = self.session_id = self._lease = None
        self.invalid = self._observe_only = False
        self._action_lock = threading.Lock()

    def bind(self, directory, session_id, *, observe_only=False):
        from .admission import no_links
        if self.client is not None:
            raise RuntimeError('请先显式解绑当前会话')
        directory = Path(directory).absolute()
        no_links(directory)
        meta = read_json(directory / 'connection.json')
        if not isinstance(session_id, str) or not session_id or meta.get('session_id') != session_id:
            raise ValueError('session_id 不匹配')
        client = BridgeClient(directory, pid=meta.get('pid'), exe_path=meta.get('exe_path'))
        state = client.status()
        if state.get('observation_error'):
            raise RuntimeError('绑定失败：取图失效，停止输入。' + str(state['observation_error']))
        if not state.get('connected') or state.get('session_id') != session_id:
            raise RuntimeError('绑定失败：' + state.get('error', '目标身份不匹配'))
        lease = None
        if not observe_only:
            lease = SessionLease(directory / 'binding-control.lock')
            try:
                lease.__enter__()
            except RuntimeError:
                lease = None
        self.client, self.session_id, self._lease = client, session_id, lease
        self.engaged, self.invalid, self._observe_only = True, False, observe_only
        self.profile = GameProfile(name='session-' + session_id, proc=Path(meta['exe_path']).stem,
                                   exe_path=meta['exe_path'], target_pid=meta['pid'],
                                   data_dir=str(directory.parent / 'data'))
        return {**state, 'bound': True, 'can_control': lease is not None,
                'can_stop': False, 'directory': str(directory), 'externally_managed': True}

    def client_for(self, options, *, write=False):
        if self.client is None:
            raise RuntimeError('无活动目标，请显式绑定会话或选择人工 profile')
        if self.invalid:
            raise RuntimeError('绑定已失效，请检查会话后显式解绑')
        directory = options.get('directory')
        if directory and Path(directory).resolve() != self.client.directory.resolve():
            raise ValueError('directory 与当前显式绑定不一致')
        state = self.client.status()
        if state.get('observation_error'):
            self.invalid = True
            raise RuntimeError('绑定已失效：取图失败，停止输入。' + str(state['observation_error']))
        # 纯观察连接失联时清除预览，但不接管输入；恢复后仍重新校验原身份。
        if not state.get('connected') and self._observe_only:
            raise RuntimeError('观察连接暂不可用，请等待原会话或显式解绑')
        if (not state.get('connected') or state.get('session_id') != self.session_id
                or state.get('pid') != self.profile.target_pid):
            self.invalid = True
            raise RuntimeError('绑定已失效：目标退出、身份变化或心跳不可用')
        if write and self._lease is None:
            raise RuntimeError('其他客户端持有控制权；当前连接只能查询和暂停')
        return self.client

    def execute(self, method, options, **kwargs):
        if not self._action_lock.acquire(blocking=False):
            raise RuntimeError('当前客户端仍有动作执行中，请先查询原命令结果')
        try:
            return getattr(self.client_for(options, write=True), method)(**kwargs)
        finally:
            self._action_lock.release()

    def status(self):
        if self.client is None:
            return {'bound': False, 'connected': False, 'can_control': False, 'can_stop': False}
        try:
            state = self.client_for({}).status()
        except RuntimeError as exc:
            state = {'connected': False, 'error': str(exc)}
        return {**state, 'bound': True, 'can_control': self._lease is not None and not self.invalid,
                'can_stop': False, 'directory': str(self.client.directory)}

    def unbind(self, *, timeout=3.0):
        from .win32 import _pid_path
        if not self._action_lock.acquire(blocking=False):
            raise RuntimeError('动作执行中，请先查询原命令结果再解绑')
        try:
            if self.client is None:
                self.engaged = True
                return {'bound': False, 'connected': False}
            state = self.client.status()
            if state.get('connected') and state.get('session_id') == self.session_id:
                if state.get('paused') is not True or state.get('held') != []:
                    self.client.pause()
            deadline = time.monotonic() + timeout
            while True:
                state = self.client.status()
                exited = not _pid_path(self.profile.target_pid)
                released = (state.get('connected') and state.get('session_id') == self.session_id
                            and state.get('paused') is True and state.get('held') == [])
                if exited or released:
                    self.close()
                    self.engaged = True
                    return {'bound': False, 'connected': False, 'release_confirmed': bool(released),
                            'target_exited': exited}
                if time.monotonic() >= deadline:
                    raise RuntimeError('尚未确认暂停松键或目标退出；保留绑定，请检查原会话')
                time.sleep(.01)
        finally:
            self._action_lock.release()

    def close(self):
        # 客户端异常退出只释放操作系统锁，不冒称已确认目标松键。
        if self._lease is not None:
            self._lease.__exit__()
        self.client = self.profile = self.session_id = self._lease = None
        self.invalid = self._observe_only = False


class Manager:
    """停止能力只存在于本实例持有的 Job，不从落盘 PID 恢复。"""
    def __init__(self):
        self.owned = {}
        self._lock = threading.RLock()

    @staticmethod
    def _request(directory, request_id, operation, payload):
        import re
        from .admission import fingerprint, no_links
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
            raise ValueError('request_id 必须为 1–100 位字母、数字、下划线或连字符')
        directory = Path(directory).absolute()
        no_links(directory)
        folder = directory.parent / '.admission-requests'
        no_links(folder)
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / (fingerprint(request_id) + '.json')
        content = {'schema_version': 1, 'operation': operation, 'directory': str(directory),
                   'request_id': request_id, 'payload': payload}
        return record, content

    def prepare(self, plan, *, request_id, confirm=False):
        import os
        import sys
        from . import admission
        from .process_job import OwnedProcess
        from .sessions import write_json
        if confirm is not True:
            raise ValueError('准备需要明确确认这份操作摘要')
        directory = Path(plan['destination']).absolute()
        with self._lock:
            record, content = self._request(directory, request_id, 'prepare', plan)
            with SessionLease(record.with_suffix('.lock')):
                if record.exists():
                    if read_json(record) != content:
                        raise ValueError('request_id 已被不同内容使用，请查询原请求')
                    return self.status(directory)
                admission.revalidate_plan(plan)
                write_json(record, content)
                worker = OwnedProcess([sys.executable, '-m', 'gameqakit.game_worker', str(record), str(os.getpid())],
                                      cwd=str(Path(__file__).resolve().parents[1]),
                                      env={'PYTHONUTF8': '1', 'TEMP': str(directory.parent), 'TMP': str(directory.parent)})
                try:
                    worker.__enter__()
                    self.owned[str(directory.resolve())] = {'worker': worker, 'plan': plan}
                    worker.run()
                except Exception:
                    worker.close()
                    raise
                return self.status(directory)

    def status(self, directory):
        from .admission import no_links
        directory = Path(directory).absolute()
        no_links(directory)
        item = self.owned.get(str(directory.resolve()))
        try:
            state_path = directory / 'session.json'
            if not state_path.exists():
                state_path = directory.parent / '.admission-requests' / (directory.name + '.status.json')
            no_links(state_path)
            state = read_json(state_path)
            if state.get('schema_version') != 1:
                raise ValueError('不支持的会话记录版本')
        except FileNotFoundError:
            state = {'schema_version': 1, 'state': 'preparing' if item else 'interrupted_unverified'}
        alive = item is not None and item['worker'].alive()
        if item is None:
            lease_path = directory.parent / '.admission-requests' / (directory.name + '.lifecycle.lock')
            no_links(lease_path)
            if lease_path.is_file():
                try:
                    with SessionLease(lease_path):
                        pass
                except RuntimeError:
                    alive = True
        if alive and state.get('state') == 'prepared' and (directory / 'start.request.json').exists():
            state = {**state, 'state': 'starting'}
        terminal = state.get('state') in ('stopped', 'cleanup_failed', 'failed', 'cancelled')
        if not alive and not terminal:
            state = {**state, 'recorded_state': state['state'], 'state': 'interrupted_unverified'}
        if alive and state.get('state') == 'ready_paused' and state.get('bridge_directory'):
            control = BridgeClient(state['bridge_directory'], pid=state.get('pid'),
                                   exe_path=state.get('exe_path')).status()
            if control.get('observation_error'):
                control = {**control, 'connected': False}
            state = {**state, 'control': control}
            if control.get('connected') and control.get('paused') is False:
                state['state'] = 'running'
            elif not control.get('connected'):
                state['state'] = 'connection_lost'
        return {**state, 'directory': str(directory), 'can_stop': bool(item is not None and alive and not terminal),
                'owned_by_this_creator': item is not None, 'worker_alive': bool(alive)}

    def _owned(self, directory):
        directory = Path(directory).resolve()
        item = self.owned.get(str(directory))
        if item is None:
            raise RuntimeError('只能由创建者结束或启动会话；本连接没有进程回收权')
        return directory, item

    def start(self, directory, *, request_id, fingerprint, confirm=False):
        from .sessions import write_json
        if confirm is not True:
            raise ValueError('启动需要 confirm=true')
        with self._lock:
            directory, item = self._owned(directory)
            if fingerprint != item['plan']['fingerprint']:
                raise ValueError('摘要变化，请重新检查并确认')
            record, content = self._request(directory, request_id, 'start', {'fingerprint': fingerprint})
            with SessionLease(record.with_suffix('.lock')):
                if record.exists():
                    if read_json(record) != content:
                        raise ValueError('request_id 已被不同内容使用')
                    return self.status(directory)
                if self.status(directory)['state'] != 'prepared':
                    raise RuntimeError('只有准备完成的自有会话可以启动；请先查询原请求状态')
                write_json(record, content)
                write_json(directory / 'start.request.json', content)
                return {**self.status(directory), 'state': 'starting'}

    def stop(self, directory, *, request_id):
        from .sessions import write_json
        with self._lock:
            directory, item = self._owned(directory)
            record, content = self._request(directory, request_id, 'stop', {})
            with SessionLease(record.with_suffix('.lock')):
                if record.exists():
                    if read_json(record) != content:
                        raise ValueError('request_id 已被不同内容使用')
                    return self.status(directory)
                write_json(record, content)
                write_json(record.parent / (Path(item['plan']['destination']).name + '.cancel.json'),
                           {'directory': str(directory)})
                if directory.exists():
                    (directory / 'stop.request').touch()
                return {**self.status(directory), 'state': 'stopping'}

    def close(self):
        for key, item in list(self.owned.items()):
            if item['worker'].alive():
                from .admission import fingerprint
                self.stop(key, request_id='creator-close-' + fingerprint(key)[:16])
                deadline = time.monotonic() + 30
                while item['worker'].alive() and time.monotonic() < deadline:
                    time.sleep(.02)
            item['worker'].close()
