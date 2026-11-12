"""固定 MV/MZ SDK 工程的独立桌面受管生命周期。"""
from __future__ import annotations

from pathlib import Path
import shutil
import time
import uuid

from gameqakit.admission import fingerprint, no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from . import prepare as recipe


class RpgMakerSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.host = self.bridge = self.baseline = None

    def prepare(self):
        revalidate_plan(self.plan)
        self.baseline = recipe.prepare(self.plan)
        return {'state': 'prepared', 'runtime': str(self.root / 'runtime')}

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前需要本创建者完成准备，且不能重复启动')
        if fingerprint(recipe.artifact_digests(self.plan['artifacts'])) != self.plan['artifact_digests']:
            raise RuntimeError('artifact_changed')
        if recipe.manifest(self.root / 'runtime') != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        if cancelled():
            raise InterruptedError('接入已取消')
        executable, app = self.root / 'runtime/nw.exe', self.root / 'runtime/app'
        directory = self.root / 'bridge'
        session = uuid.uuid4().hex
        self.host = DesktopProcess([str(executable), '--user-data-dir=' + str(self.root / 'runtime-data'), str(app)],
            cwd=str(app), env={'GAMEQAKIT_BRIDGE_DIR': str(directory), 'GAMEQAKIT_SESSION_ID': session,
                              'GAMEQAKIT_OBSERVATION': '1', 'TEMP': str(self.root / 'tmp'),
                              'TMP': str(self.root / 'tmp')})
        self.host.__enter__()
        write_connection(directory, {'protocol': 1, 'session_id': session, 'pid': self.host.pid,
                                     'exe_path': str(executable), 'engine': self.plan['engine']})
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
                for key, expected in (('runtime_data_path', self.root / 'runtime-data/Default'),
                                      ('save_directory', app / 'save')):
                    if Path(state.get(key) or '').resolve() != expected.resolve():
                        raise RuntimeError('unexpected_' + key)
                write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': 'nw',
                           'exe_path': str(executable), 'data_dir': str(self.root / 'data')})
                return {**state, **image, 'state': 'ready_paused', 'bridge_directory': str(directory),
                        'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout：MV/MZ 未在既定启动期限内取得有效首图')

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
        source_ok = runtime_source_ok = external_ok = False
        if (self.root / 'source-before.json').exists():
            after = recipe.manifest(self.plan['source'])
            write_json(self.root / 'source-after.json', after)
            source_ok = after == read_json(self.root / 'source-before.json')
            after = recipe.manifest(self.plan['artifacts'])
            write_json(self.root / 'runtime-source-after.json', after)
            runtime_source_ok = after == read_json(self.root / 'runtime-source-before.json')
            before = read_json(self.root / 'external-before.json')
            after = {path: recipe.manifest(path) for path in before}
            write_json(self.root / 'external-after.json', after)
            external_ok = after == before
        safe = exited and release is not False and source_ok and runtime_source_ok and external_ok
        runtime = self.root / 'runtime'
        retained = runtime.exists()
        changed = []
        if retained:
            after = recipe.manifest(runtime)
            write_json(self.root / 'runtime-after.json', after)
            changed = sorted(name for name in set(after) | set(self.baseline or {})
                             if after.get(name) != (self.baseline or {}).get(name))
            if safe and self.baseline is not None and after == self.baseline:
                no_links(runtime)
                shutil.rmtree(runtime)
                retained = False
        result = {'state': 'stopped' if safe else 'cleanup_failed', 'target_exited': exited,
                  'release_confirmed': release, 'release_error': release_error, 'source_unchanged': source_ok,
                  'runtime_source_unchanged': runtime_source_ok, 'external_unchanged': external_ok,
                  'runtime_retained': retained, 'runtime_changed': changed[:20]}
        write_json(self.root / 'cleanup.json', result)
        return result
