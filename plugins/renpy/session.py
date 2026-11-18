"""Run the registered Ren'Py fixture with the existing duration bridge."""
from pathlib import Path
import shutil
import time
import uuid

from gameqakit.admission import no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from . import prepare as recipe


class RenpySession:
    private_dirs = ('bridge', 'appdata', 'localappdata', 'tmp', 'saves', 'multipersistent')

    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.runtime = self.root / 'runtime'
        self.host = self.bridge = self.baseline = None

    def prepare(self):
        revalidate_plan(self.plan)
        self.root.mkdir(exist_ok=False)
        for name in self.private_dirs:
            (self.root / name).mkdir()
        artifacts = Path(self.plan['artifacts'])
        expected = recipe.runtime_files(artifacts)
        files = {name: artifacts / name for name in expected}
        for source, digest in ((Path(self.plan['source']) / recipe.SCRIPT, recipe.SCRIPT_SHA256),
                               (recipe.BRIDGE, self.plan['artifact_digests'][recipe.BRIDGE.name])):
            name = 'game/' + source.name
            files[name] = source
            expected[name] = digest
        for name, source in files.items():
            no_links(source)
            target = self.runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if recipe.tree_digests(self.runtime) != expected:
            raise ValueError('runtime_changed: RenPy copied files do not match the registration')
        self.baseline = expected

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前须准备，且不能重复启动')
        if (recipe.source_digests(self.plan['source']) != self.plan['source_digests']
                or recipe.artifact_digests(self.plan['artifacts']) != self.plan['artifact_digests']
                or recipe.tree_digests(self.runtime) != self.baseline):
            raise ValueError('runtime_changed: RenPy sample or runtime changed')
        for name in self.private_dirs:
            directory = self.root / name
            no_links(directory)
            if any(directory.iterdir()):
                raise ValueError(f'启动前目录须为空：{name}')
        if cancelled():
            raise InterruptedError('接入已取消')
        if {path.name for path in self.root.iterdir()} - {
                *self.private_dirs, 'runtime', 'session.json', 'start.request.json'}:
            raise ValueError('RenPy 会话根目录包含未登记的文件')
        directory = self.root / 'bridge'
        session = uuid.uuid4().hex
        executable = self.runtime / recipe.EXECUTABLE
        env = {'GAMEQAKIT_BRIDGE_DIR': str(directory), 'GAMEQAKIT_SESSION_ID': session,
               'GAMEQA_FIXTURE_ROOT': str(self.root), 'RENPY_PATH_TO_SAVES': str(self.root / 'saves'),
               'RENPY_MULTIPERSISTENT': str(self.root / 'multipersistent'),
               'APPDATA': str(self.root / 'appdata'), 'LOCALAPPDATA': str(self.root / 'localappdata'),
               'TMP': str(self.root / 'tmp'), 'TEMP': str(self.root / 'tmp')}
        self.host = DesktopProcess([str(executable), '-E', '-s', '-B', str(self.runtime / recipe.LAUNCHER),
            str(self.runtime), '--savedir', str(self.root / 'saves/main')], cwd=self.runtime, env=env)
        for key in tuple(self.host.env):
            if key.upper().startswith(('RENPY_', 'SDL_', 'ANDROID_')) and key not in env:
                self.host.env.pop(key)
        self.host.__enter__()
        write_connection(directory, {'protocol': 2, 'session_id': session, 'pid': self.host.pid,
                                     'exe_path': str(executable), 'engine': 'renpy'})
        self.bridge = BridgeClient(directory, pid=self.host.pid, exe_path=str(executable))
        self.host.run()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('target_exited_before_ready')
            state = self.bridge.status()
            if state.get('connected'):
                if state.get('error'):
                    raise RuntimeError(state['error'])
                if state.get('observation_seq', 0) > 0:
                    return {**state, **validate_ready(self.root, state), 'state': 'ready_paused',
                            'bridge_directory': str(directory), 'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout：RenPy 未取得有效首图')

    def stop(self):
        release = release_error = None
        pid = self.host.pid if self.host else None
        try:
            if self.bridge:
                release = False
                state = self.bridge.status()
                if state.get('connected'):
                    self.bridge.pause()
                    pause_id = read_json(self.root / 'bridge/pause.json')['id']
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and self.host.alive():
                        state = self.bridge.status()
                        if (state.get('connected') and state.get('pause_id') == pause_id
                                and state.get('paused') is True and state.get('held') == []):
                            release = True
                            break
                        time.sleep(.02)
                if not release:
                    release_error = state.get('error') or 'pause_not_acknowledged'
        except (OSError, ValueError, RuntimeError) as exc:
            release, release_error = False, str(exc)
        finally:
            if self.host:
                self.host.close()
        exited = not pid or not _pid_path(pid)
        checks = {}
        for key, field, digest_field, verify in (
                ('source_unchanged', 'source', 'source_digests', recipe.source_digests),
                ('runtime_source_unchanged', 'artifacts', 'artifact_digests', recipe.artifact_digests)):
            try:
                checks[key] = verify(self.plan[field]) == self.plan[digest_field]
            except (OSError, ValueError):
                checks[key] = False
        safe = exited and release is not False and all(checks.values())
        result = {'state': 'stopped' if safe else 'cleanup_failed', 'target_exited': exited,
                  'release_confirmed': release, **checks, 'runtime_retained': self.runtime.exists()}
        if release_error:
            result['release_error'] = release_error
        write_json(self.root / 'cleanup.json', result)
        return result
