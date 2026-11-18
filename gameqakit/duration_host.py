"""v2 文件桥共用的毫秒动作、释放确认与 Job 监督。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit.control_time import validate_payload
from gameqakit.desktop import DesktopProcess

IDENT = re.compile(r'[A-Za-z0-9_-]{1,64}')


def open_shared(path):
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == W.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'rb')


def read(path):
    deadline = time.monotonic() + .1
    while True:
        try:
            # 允许发布者原子替换被读取的旧版本，避免读者阻塞目标 UI 线程。
            with open_shared(path) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('控制文件超过 64 KiB')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('控制文件必须是对象')
            return value
        except FileNotFoundError:
            return {}
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.005)


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        deadline = time.monotonic() + .1
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.005)
    finally:
        temporary.unlink(missing_ok=True)


class Supervisor:
    engine = ''
    runtime_sha256 = ''
    capture_worker = Path(__file__).with_name('capture_worker.py')
    capture_module = 'gameqakit.capture_worker'
    startup_capture = None

    def __init__(self, target, directory, session):
        self.target, self.directory = target, directory
        self.identity = dict(protocol=2, engine=self.engine, session_id=session,
                             pid=target.pid, exe_path=str(target.argv[0]))
        self.started = self.changed = time.monotonic()
        self.last_publish = self.last_capture = 0
        self.observer = {}
        self.ui_seq = 0
        self.observation_seq = 0
        self.observation = ''
        self.paused = True
        self.pause_id = ''
        self.error = ''
        self.active = None
        self.capture = None
        self.stopping = None
        self.closing = False
        self.workers = []
        self.reason = 'target_exited'

    def state(self):
        return dict(self.identity, updated_at=time.time(), paused=self.paused,
            pause_id=self.pause_id, error=self.error,
            capabilities=['confirm', 'wait'] if self.observation_seq and not self.error else [],
            buttons=['ok'], max_duration_ms=1000, timing_mode='duration_ms', pause_scope='input',
            simulation_pause=False, observation_seq=self.observation_seq,
            observation_screenshot=self.observation,
            held=['ok'] if self.active and self.active['keyed'] and not self.active['release'] else [])

    def send(self, ident, kind):
        publish(self.directory / 'input.json', dict(self.identity, id=ident, kind=kind))

    def release(self):
        active = self.active
        if active and active['keyed'] and not active['release'] and active['release_started'] is None:
            # 首次期限在任何 I/O 之前记录；反复暂停和读错不能延后它。
            active['release_started'] = time.monotonic()
            self.send(active['up_id'], 'up')

    def interrupt(self, error):
        self.paused = True
        if self.active:
            if not self.active['error']:
                self.active['error'] = str(error)
            self.release()

    def fault(self, error):
        if not self.error:
            self.error = str(error)
        self.interrupt(self.error)

    def observe(self):
        state = read(self.directory / 'observer.json')
        if not state:
            return
        if (state.get('session_id') != self.identity['session_id'] or state.get('pid') != self.target.pid
                or type(state.get('seq')) is not int or state['seq'] < self.ui_seq
                or type(state.get('hwnd')) is not int or state['hwnd'] <= 0
                or type(state.get('held')) is not bool or not isinstance(state.get('events'), list)):
            raise ValueError('观察身份或序号无效')
        if state['seq'] > self.ui_seq:
            self.ui_seq, self.changed = state['seq'], time.monotonic()
        self.observer = state
        active = self.active
        if active:
            for event in state['events']:
                if event.get('after_original_handler') is not True:
                    continue
                if event.get('id') == active['down_id'] and event.get('kind') == 'down' and not active['press']:
                    active['press'] = True
                    active['until'] = time.monotonic() + active['request']['payload']['duration_ms'] / 1000
                if event.get('id') == active['up_id'] and event.get('kind') == 'up' and not active['release']:
                    active['release'] = True
                    active['after_ui'] = state['seq']

    def lease_valid(self):
        lease = read(self.directory / 'lease.json')
        expiry = lease.get('expires_at')
        return (lease.get('session_id') == self.identity['session_id']
                and type(expiry) in (int, float) and math.isfinite(expiry) and 0 < expiry - time.time() <= 2)

    def accept(self, request):
        ident = request.get('id')
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError('命令编号无效')
        if (self.directory / 'results' / (ident + '.json')).exists():
            return
        created = request.get('created_at')
        if (request.get('protocol') != 2 or type(request.get('protocol')) is not int
                or request.get('session_id') != self.identity['session_id']
                or request.get('pause_id') != self.pause_id
                or type(request.get('after_observation_seq')) is not int
                or not 0 <= request['after_observation_seq'] <= self.observation_seq
                or type(created) not in (int, float) or not math.isfinite(created)
                or not -1 <= time.time() - created <= 1.5):
            raise ValueError('命令身份、暂停代次、观察序号或有效期无效')
        payload = request.get('payload')
        if not isinstance(payload, dict) or payload.get('action') not in ('resume', 'confirm', 'wait'):
            raise ValueError('未声明的动作')
        validate_payload(payload, self.state())
        if payload['action'] != 'resume' and self.paused:
            raise ValueError('控制已暂停')
        if not self.lease_valid():
            raise ValueError('租约缺失或失效')
        now = time.monotonic()
        self.active = dict(request=request, keyed=payload['action'] == 'confirm', started=now,
            until=now + payload.get('duration_ms', 0) / 1000, after_ui=self.ui_seq,
            press=False, release=False, release_started=None,
            down_id=uuid.uuid4().hex, up_id=uuid.uuid4().hex, error='')
        if self.error:
            self.active['error'] = self.error
            self.finish()
            return
        if payload['action'] == 'resume':
            self.paused = False
        if payload['action'] == 'confirm':
            self.send(self.active['down_id'], 'down')

    def start_capture(self, ident, purpose):
        destination = self.directory / 'screenshots' / (ident + '.png')
        destination.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        command = ([sys.executable, '-m', self.capture_module] if getattr(sys, 'frozen', False)
                   else [sys.executable, '-B', '-X', 'utf8', str(self.capture_worker)])
        worker = DesktopProcess([*command, str(self.directory), token,
            str(self.observer['hwnd']), str(destination), self.target.desktop_name],
            cwd=Path(__file__).resolve().parents[2], isolated_desktop=False)
        worker.__enter__()
        self.capture = dict(worker=worker, started=time.monotonic(), token=token,
                            destination=destination, purpose=purpose)
        self.workers.append(worker.pid)
        worker.run()

    def poll_capture(self):
        capture = self.capture
        if not capture or capture['worker'].alive():
            return
        capture['worker'].close()
        self.capture = None
        result = read(self.directory / 'capture-result.json')
        if result.get('id') != capture['token'] or result.get('ok') is not True:
            raise RuntimeError('取图失败：' + str(result.get('error', '工作进程未返回本次结果')))
        destination = capture['destination']
        if destination.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('截图超过大小上限')
        self.observation = destination.relative_to(self.directory).as_posix()
        self.observation_seq += 1
        self.last_capture = time.monotonic()
        if capture['purpose'] == 'action' and self.active:
            # 取图期间人工暂停或续租失效时，不发布成功回执。
            if not self.lease_valid():
                self.interrupt('取图期间租约失效')
            self.finish(dict(screenshot=self.observation,
                screenshot_sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))

    def finish(self, screenshot=None):
        active = self.active
        request = active['request']
        result = dict(self.identity, **(screenshot or {}), id=request['id'], ok=not active['error'],
            error=active['error'], timing_mode='duration_ms', pause_scope='input', simulation_pause=False,
            pause_id=request['pause_id'], consumed=active['press'], press_consumed=active['press'],
            release_consumed=active['release'], held=self.state()['held'], observation_seq=self.observation_seq,
            started_monotonic_ms=int(active['started'] * 1000), completed_monotonic_ms=int(time.monotonic() * 1000))
        publish(self.directory / 'status.json', self.state())
        self.last_publish = time.monotonic()
        publish(self.directory / 'results' / (request['id'] + '.json'), result)
        self.active = None

    def deadlines(self):
        now = time.monotonic()
        active = self.active
        if self.stopping is not None and now - self.stopping > 1:
            return 'shutdown_timeout'
        if active and active['release_started'] is not None and not active['release'] and now - active['release_started'] > 1:
            return 'release_timeout'
        if self.ui_seq and now - self.changed > 1:
            return 'activity_timeout'
        if not self.observation_seq and now - self.started > 15:
            return 'startup_timeout'
        if self.capture and now - self.capture['started'] > 3:
            return 'capture_timeout'
        return None

    def pump(self):
        self.observe()
        active = self.active
        if active and active['press'] and not active['release'] and time.monotonic() >= active['until']:
            self.release()
        stop = read(self.directory / 'stop.json')
        if stop.get('session_id') == self.identity['session_id'] and self.stopping is None:
            self.stopping = time.monotonic()
            self.interrupt('宿主关闭请求')
        pause = read(self.directory / 'pause.json')
        if pause.get('session_id') == self.identity['session_id'] and pause.get('id') != self.pause_id:
            if not isinstance(pause.get('id'), str) or not IDENT.fullmatch(pause['id']):
                raise ValueError('暂停编号无效')
            self.pause_id = pause['id']
            self.interrupt('人工暂停')
        if not self.paused and not self.lease_valid():
            self.interrupt('租约失效')
        self.poll_capture()
        self.progress()
        if not self.stopping and not self.active and self.observation_seq:
            request = read(self.directory / 'request.json')
            if request:
                self.accept(request)
        if (not self.stopping and not self.active and not self.error and self.observer.get('ready')
                and not self.capture and time.monotonic() - self.last_capture >= .2):
            ident = self.startup_capture if not self.observation_seq and self.startup_capture else 'observation-' + str(self.observation_seq + 1)
            self.start_capture(ident, 'observation')

    def progress(self):
        active = self.active
        if active:
            complete = active['release'] if active['keyed'] else time.monotonic() >= active['until']
            if complete:
                if active['error']:
                    self.finish()
                elif not self.capture and self.ui_seq > active['after_ui']:
                    self.start_capture(active['request']['id'], 'action')
        if self.stopping is not None and not self.active and not self.closing:
            if self.capture:
                self.capture['worker'].close()
                self.capture = None
            self.send(uuid.uuid4().hex, 'close')
            self.closing = True

    def run(self):
        publish(self.directory / 'connection.json', self.identity)
        publish(self.directory / 'supervisor.json', dict(self.identity, host_pid=os.getpid(),
            desktop_name=self.target.desktop_name, runtime_sha256=self.runtime_sha256))
        self.target.run()
        try:
            while self.target.alive():
                reason = self.deadlines()
                if reason:
                    self.reason = reason
                    break
                try:
                    self.pump()
                except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
                    try:
                        self.fault(error)
                    except (OSError, ValueError):
                        pass
                    # 控制文件持续错误时仍先完成释放和失败回执。
                    try:
                        self.progress()
                    except (OSError, ValueError):
                        pass
                if time.monotonic() - self.last_publish >= .04:
                    try:
                        publish(self.directory / 'status.json', self.state())
                        self.last_publish = time.monotonic()
                    except (OSError, ValueError) as error:
                        try:
                            self.fault(error)
                        except (OSError, ValueError):
                            pass
                time.sleep(.01)
        finally:
            if self.capture:
                self.capture['worker'].close()
                self.capture = None
            if self.reason == 'target_exited' and self.stopping is not None:
                self.reason = 'stop_requested'
            self.target.close()
            active = self.active
            publish(self.directory / 'termination.json', dict(self.identity, reason=self.reason,
                elapsed_ms=int((time.monotonic() - self.started) * 1000), target_alive=self.target.alive(),
                capture_worker_pids=self.workers, pending_command=active['request']['id'] if active else None,
                press_consumed=active['press'] if active else False,
                release_consumed=active['release'] if active else False))
        return 0 if self.reason in ('target_exited', 'stop_requested') else 4
