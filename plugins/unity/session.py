"""固定 Unity Mono 与 IL2CPP 输入构建的受管生命周期。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid

from gameqakit.admission import no_links, revalidate_plan
from gameqakit.admission_runtime import StartupImagePending, validate_ready
from gameqakit.control import BridgeClient, _write_ini
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from . import prepare as recipe


class UnitySession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.host = self.bridge = self.baseline = self.link = None
        self.link_created = False
        self.observed_loads = set()

    def prepare(self):
        revalidate_plan(self.plan)
        record, self.baseline = recipe.prepare(self.plan)
        self.link = Path(record['junction'])
        return {'state': 'prepared', 'runtime': str(self.root / 'runtime')}

    def connect_persistent(self):
        import _winapi
        no_links(self.link)
        if self.link.exists():
            raise RuntimeError('private_identity_exists')
        target = self.root / 'persistent'
        no_links(target)
        target.mkdir(exist_ok=True)
        self.link.parent.mkdir(parents=True, exist_ok=True)
        # Unity 使用系统 LocalLow；只有本次随机产品标识联接到 QA 根，游戏数据留在会话内。
        _winapi.CreateJunction(str(target), str(self.link))
        self.link_created = True
        if not self.link.is_junction() or self.link.resolve() != target.resolve():
            raise RuntimeError('private_persistent_redirect_failed')

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前需要本创建者完成准备，且不能重复启动')
        profile = recipe.build_profile(self.plan['source'])
        if recipe.artifact_digests(self.plan['artifacts'], input_system=profile['input_backend'] == 'input_system',
                                   rewired=profile['input_backend'] == 'rewired',
                                   il2cpp=profile['backend'] == 'unity_il2cpp_input_system') != self.plan['artifact_digests']:
            raise RuntimeError('artifact_changed')
        if recipe.manifest(self.root / 'runtime') != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        if cancelled():
            raise InterruptedError('接入已取消')
        self.connect_persistent()
        executable = self.root / 'runtime' / profile['executable']
        record = read_json(self.root / 'unity-preparation.json')
        directory = self.root / 'bridge'
        session = uuid.uuid4().hex
        graphics = ['-force-d3d11'] if profile['input_backend'] == 'input_system' else []
        self.host = DesktopProcess([str(executable), *graphics, '-screen-fullscreen', '0', '-screen-width', '1024',
                                    '-screen-height', '768', '-logFile', str(self.root / 'player.log')],
                                   cwd=str(executable.parent), env={'GAMEQAKIT_BRIDGE_DIR': str(directory),
                                   'GAMEQAKIT_SESSION_ID': session, 'GAMEQAKIT_INPUT_BACKEND': profile['input_backend']})
        self.host.__enter__()
        connection = {'protocol': 1, 'session_id': session, 'pid': self.host.pid,
                      'exe_path': str(executable),
                      'engine': 'unity_il2cpp' if profile['backend'] == 'unity_il2cpp_input_system' else 'unity_mono',
                      'command_format': 'ini'}
        write_json(directory / 'connection.json', connection)
        _write_ini(directory / 'connection.ini', connection)
        self.bridge = BridgeClient(directory, pid=self.host.pid, exe_path=str(executable))
        self.host.run()
        ready = self.wait_ready(self.root / 'persistent' / record['product'], cancelled)
        write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': Path(profile['executable']).stem,
                   'exe_path': str(executable), 'data_dir': str(self.root / 'data')})
        return ready

    def wait_ready(self, expected_persistent, cancelled=lambda: False):
        deadline = time.monotonic() + 15
        last_sequence = 0
        backend_was_ready = False
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('target_exited_before_ready')
            state = self.bridge.status()
            if state.get('observation_error'):
                raise RuntimeError('observation_failed:' + state['observation_error'])
            if backend_was_ready and not state.get('connected'):
                raise RuntimeError('bridge_disconnected_before_ready')
            if state.get('connected'):
                # 第 0 帧初始化占位心跳可能先于后端加载；就绪后断线才代表控制通路丢失。
                backend_was_ready = backend_was_ready or state.get('input_ready') is True
                if Path(state.get('persistent_data_path', '')).resolve() != Path(expected_persistent).resolve():
                    raise RuntimeError('unexpected_persistent_data_path')
                if not (state.get('paused') is True and state.get('held') == [] and state.get('session_id')):
                    raise RuntimeError('首图或真实暂停松键尚未确认')
            if state.get('connected') and state.get('input_ready') and state.get('observation_seq', 0) > 0:
                sequence = state['observation_seq']
                if sequence > last_sequence:
                    last_sequence = sequence
                    try:
                        image = validate_ready(self.root, state)
                    except StartupImagePending as exc:
                        # 只等待启动空白图；保存同一份已判读字节，不重读正在发布的截图。
                        folder = self.root / 'startup-candidates'
                        folder.mkdir(exist_ok=True)
                        (folder / f'{sequence}.png').write_bytes(exc.body)
                        write_json(folder / f'{sequence}.json', {'state': state, 'error': str(exc)})
                    else:
                        return {**state, **image, 'state': 'ready_paused',
                                'bridge_directory': str(self.root / 'bridge'), 'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout：Unity 未在既定启动期限内取得有效首图')

    def observe_loading(self, cancelled=lambda: False):
        """仅固定 Rewired 构建可凭加载前证明延期回收；不续租或恢复输入。"""
        if self.plan.get('backend') != 'unity_mono_rewired':
            return False
        path = self.root / 'bridge/loading.json'
        no_links(path)
        if not path.is_file():
            return False
        signal = read_json(path)
        meta = self.bridge._connection()
        started = signal.get('started_at')
        requested, pending = signal.get('requested'), signal.get('pending')
        identifier = signal.get('id')
        if not (signal.get('schema_version') == 1
                and all(signal.get(key) == meta[key] for key in ('protocol', 'session_id', 'pid', 'exe_path'))
                and isinstance(identifier, str) and 1 <= len(identifier) <= 64 and identifier not in self.observed_loads
                and signal.get('eligible') is True and signal.get('release_observed') is True
                and type(started) in (int, float) and math.isfinite(started) and 0 <= time.time()-started < 10
                and type(signal.get('start_frame')) is int and type(signal.get('release_frame')) is int
                and 0 < signal['release_frame'] <= signal['start_frame']
                and type(signal.get('start_observation_seq')) is int and signal['start_observation_seq'] >= 0
                and isinstance(requested, list) and 0 < len(requested) <= 64
                and all(isinstance(item, str) and 0 < len(item) <= 256 for item in requested)
                and len(set(requested)) == len(requested)
                and isinstance(pending, list) and all(isinstance(item, str) and item in requested for item in pending)
                and signal.get('phase') in ('loading', 'ready_paused')):
            return False
        self.observed_loads.add(identifier)
        deadline = time.monotonic() + max(0, 10-(time.time()-started))
        self.bridge.pause()
        record = dict(id=identifier, started_at=started, limit_seconds=10, state='observing',
                      input_resumed=False, lease_renewed=False)
        write_json(self.root / 'loading-observation.json', record)
        while time.monotonic() < deadline:
            if cancelled():
                raise RuntimeError('loading_cancelled')
            if not self.host.alive():
                raise RuntimeError('loading_target_exited')
            if (self.root / 'bridge/fatal.json').exists():
                raise RuntimeError('loading_bridge_failed')
            latest = read_json(path)
            if latest.get('id') != identifier or any(latest.get(key) != meta[key] for key in ('session_id', 'pid', 'exe_path')):
                raise RuntimeError('loading_identity_changed')
            state = self.bridge.status()
            if state.get('observation_error'):
                raise RuntimeError('loading_observation_failed:' + str(state['observation_error']))
            completed = latest.get('completion_frame')
            if (latest.get('phase') == 'ready_paused' and latest.get('pending') == []
                    and latest.get('requested') == requested and latest.get('eligible') is True
                    and type(completed) is int and completed >= signal['start_frame']
                    and state.get('connected') and state.get('paused') is True and state.get('held') == []
                    and state.get('release_observed') is True and state.get('input_ready') is True
                    and state.get('loading_id') == identifier and state.get('loading_phase') == 'ready_paused'
                    and type(state.get('frame')) is int and state['frame'] > completed
                    and type(state.get('observation_frame')) is int and state['observation_frame'] > completed
                    and type(state.get('observation_seq')) is int
                    and state['observation_seq'] > signal['start_observation_seq']):
                image = validate_ready(self.root, state)
                if cancelled():
                    raise RuntimeError('loading_cancelled')
                if not self.host.alive():
                    raise RuntimeError('loading_target_exited')
                if time.monotonic() >= deadline:
                    break
                write_json(self.root / 'loading-observation.json', {**record, **image, 'state': 'recovered_paused',
                           'completion_frame': completed, 'observation_frame': state['observation_frame'],
                           'observation_seq': state['observation_seq']})
                return True
            time.sleep(.02)
        write_json(self.root / 'loading-observation.json', {**record, 'state': 'timeout'})
        raise RuntimeError('loading_timeout')

    def stop(self):
        release = release_error = None
        pid = self.host.pid if self.host else None
        try:
            if self.bridge:
                state = self.bridge.status()
                if state.get('connected'):
                    self.bridge.pause()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        state = self.bridge.status()
                        if state.get('connected') and state.get('paused') is True and state.get('held') == []:
                            release = True
                            break
                        time.sleep(.01)
                    if release is None:
                        release = False
                else:
                    release, release_error = False, state.get('error') or 'bridge_disconnected_before_release'
        except Exception as exc:
            release, release_error = False, str(exc)
        finally:
            if self.host:
                self.host.close()
        exited = pid is None or not _pid_path(pid)
        source_ok = external_ok = registry_ok = False
        if (self.root / 'source-before.json').exists():
            after = recipe.manifest(self.plan['source'])
            write_json(self.root / 'source-after.json', after)
            source_ok = after == json.loads((self.root / 'source-before.json').read_text(encoding='utf-8'))
            before = json.loads((self.root / 'external-before.json').read_text(encoding='utf-8'))
            after = recipe.manifest(before['path'])
            write_json(self.root / 'external-after.json', after)
            external_ok = after == before['files']
            registry = recipe.registry_snapshot(before.get('company', 'DefaultCompany'), before.get('product', 'Taimashi'))
            write_json(self.root / 'registry-after.json', registry)
            registry_ok = registry == before['registry']
        link_removed = not self.link_created
        if self.link_created and exited:
            if self.link.is_junction() and self.link.resolve() == (self.root / 'persistent').resolve():
                os.rmdir(self.link)
                link_removed = True
        safe = exited and release is not False and source_ok and external_ok and registry_ok and link_removed
        runtime = self.root / 'runtime'
        retained = runtime.exists()
        changed = []
        if retained:
            after = recipe.manifest(runtime)
            write_json(self.root / 'runtime-after.json', after)
            changed = sorted(name for name in set(after) | set(self.baseline or {})
                             if after.get(name) != (self.baseline or {}).get(name))
            if safe and self.baseline is not None and after == self.baseline:
                shutil.rmtree(runtime)
                retained = False
        result = {'state': 'stopped' if safe else 'cleanup_failed', 'target_exited': exited,
                  'release_confirmed': release, 'release_error': release_error, 'source_unchanged': source_ok,
                  'external_unchanged': external_ok, 'registry_unchanged': registry_ok,
                  'private_link_removed': link_removed, 'runtime_retained': retained, 'runtime_changed': changed[:20]}
        write_json(self.root / 'cleanup.json', result)
        return result
