"""Managed lifecycle for the registered HSP probe on a private desktop."""
import os
from pathlib import Path
import shutil
import time
import uuid

from gameqakit.admission import revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json
from gameqakit.win32 import _pid_path
from plugins.native_input.prepare import manifest
from . import prepare_authored as recipe


class HspSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.runtime = self.root / 'runtime'
        self.directory = self.root / 'bridge'
        self.host = self.bridge = self.baseline = None

    def prepare(self):
        revalidate_plan(self.plan)
        self.runtime.mkdir(parents=True, exist_ok=False)
        (self.root / 'tmp').mkdir()
        self.directory.mkdir()
        expected = {**self.plan['source_digests'], **self.plan['artifact_digests']}
        for field in ('source', 'artifact'):
            origin = Path(self.plan[field if field == 'source' else 'artifacts'])
            for name in self.plan[field + '_digests']:
                shutil.copyfile(origin / name, self.runtime / name)
        if manifest(self.runtime) != expected:
            raise RuntimeError('copy_changed_during_prepare')
        self.baseline = expected
        write_json(self.root / 'approved-plan.json', self.plan)
        write_json(self.root / 'runtime-before.json', expected)
        return {'state': 'prepared', 'runtime': str(self.runtime)}

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前须由本创建者完成准备，且不能重复启动')
        if cancelled():
            raise InterruptedError('接入已取消')
        if manifest(self.runtime) != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        if recipe.source_digests(self.plan['source']) != self.plan['source_digests']:
            raise RuntimeError('source_changed_before_start')
        if recipe.artifact_digests(self.plan['artifacts']) != self.plan['artifact_digests']:
            raise RuntimeError('artifact_changed_before_start')
        exe = self.runtime / recipe.RUNTIME
        session = uuid.uuid4().hex
        self.host = DesktopProcess([str(exe), str(self.runtime / recipe.SCRIPT)], cwd=self.runtime,
            disable_extension_points=True, env={
                'GAMEQAKIT_BRIDGE_DIR': str(self.directory), 'GAMEQAKIT_SESSION_ID': session,
                'GAMEQAKIT_HSP_MANAGED': '1', 'TEMP': str(self.root / 'tmp'), 'TMP': str(self.root / 'tmp')})
        self.host.__enter__()
        write_connection(self.directory, {'protocol': 1, 'session_id': session, 'pid': self.host.pid,
            'owner_pid': os.getpid(), 'desktop': self.host.desktop_name, 'exe_path': str(exe),
            'command_format': 'ini', 'engine': 'hsp3'})
        self.bridge = BridgeClient(self.directory, pid=self.host.pid, exe_path=exe)
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
            if state.get('input_ready') and state.get('observation_seq', 0) > 0:
                image = validate_ready(self.root, state)
                write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': exe.stem,
                    'exe_path': str(exe), 'data_dir': str(self.root / 'data')})
                return {**state, **image, 'state': 'ready_paused', 'bridge_directory': str(self.directory),
                        'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout')

    def stop(self):
        release, release_error = None, None
        pid = self.host.pid if self.host else None
        try:
            if self.bridge:
                self.bridge.pause()
                generation = self.bridge._pause_id()
                deadline = time.monotonic() + 3
                release = False
                while self.host.alive() and time.monotonic() < deadline:
                    state = self.bridge.status()
                    if (generation and state.get('pause_id') == generation and state.get('connected')
                            and state.get('paused') is True and state.get('held') == []):
                        release = True
                        break
                    time.sleep(.02)
        except (OSError, ValueError, RuntimeError) as exc:
            release, release_error = False, str(exc)
        finally:
            if self.host:
                self.host.close()
        exited = pid is None or not _pid_path(pid)
        checks = {}
        for field, key, verify in (('source', 'source', recipe.source_digests),
                                   ('artifact', 'artifacts', recipe.artifact_digests)):
            try:
                after = verify(self.plan[key])
            except (OSError, ValueError) as exc:
                after = {'error': str(exc)}
            write_json(self.root / (field + '-after.json'), after)
            checks[field + '_unchanged'] = after == self.plan[field + '_digests']
        if self.runtime.exists():
            write_json(self.root / 'runtime-after.json', manifest(self.runtime))
        result = {'target_exited': exited, 'release_confirmed': release, 'release_error': release_error,
                  'runtime_retained': self.runtime.exists(), **checks,
                  'state': 'stopped' if exited and release is not False and all(checks.values()) else 'cleanup_failed'}
        write_json(self.root / 'cleanup.json', result)
        return result
