"""固定 Unity Mono 旧 Input 构建的受管生命周期。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import time
import uuid

from gameqakit.admission import no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
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
        if recipe.artifact_digests(self.plan['artifacts']) != self.plan['artifact_digests']:
            raise RuntimeError('artifact_changed')
        if recipe.manifest(self.root / 'runtime') != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        if cancelled():
            raise InterruptedError('接入已取消')
        self.connect_persistent()
        executable = self.root / 'runtime/Taimashi.exe'
        directory = self.root / 'bridge'
        session = uuid.uuid4().hex
        self.host = DesktopProcess([str(executable), '-screen-fullscreen', '0', '-screen-width', '1024',
                                    '-screen-height', '768', '-logFile', str(self.root / 'player.log')],
                                   cwd=str(executable.parent), env={'GAMEQAKIT_BRIDGE_DIR': str(directory),
                                   'GAMEQAKIT_SESSION_ID': session, 'GAMEQAKIT_INPUT_BACKEND': 'legacy'})
        self.host.__enter__()
        connection = {'protocol': 1, 'session_id': session, 'pid': self.host.pid,
                      'exe_path': str(executable), 'engine': 'unity_mono', 'command_format': 'ini'}
        write_json(directory / 'connection.json', connection)
        _write_ini(directory / 'connection.ini', connection)
        self.bridge = BridgeClient(directory, pid=self.host.pid, exe_path=str(executable))
        self.host.run()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('target_exited_before_ready')
            state = self.bridge.status()
            if state.get('observation_error'):
                raise RuntimeError('observation_failed:' + state['observation_error'])
            if state.get('connected') and state.get('input_ready') and state.get('observation_seq', 0) > 0:
                image = validate_ready(self.root, state)
                expected = str(self.root / 'persistent/CompatibilityProbe')
                if not Path(state.get('persistent_data_path', '')).resolve() == Path(expected).resolve():
                    raise RuntimeError('unexpected_persistent_data_path')
                write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': 'Taimashi',
                           'exe_path': str(executable), 'data_dir': str(self.root / 'data')})
                return {**state, **image, 'state': 'ready_paused', 'bridge_directory': str(directory),
                        'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout：Unity 未在既定启动期限内取得有效首图')

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
            source_ok = after == read_json(self.root / 'source-before.json')
            before = read_json(self.root / 'external-before.json')
            after = recipe.manifest(before['path'])
            write_json(self.root / 'external-after.json', after)
            external_ok = after == before['files']
            registry = recipe.registry_snapshot('DefaultCompany', 'Taimashi')
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
