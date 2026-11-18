"""Manage the registered Emuera fixture through the duration supervisor."""
from pathlib import Path
import shutil
from threading import Thread
import time
import uuid

from gameqakit.admission import file_digest, no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from . import host, prepare as recipe
from .build import build


class ManagedSupervisor(host.Supervisor):
    startup_capture = 'startup'
    observation_error = ''

    def state(self):
        return {**super().state(), 'observation_error': self.observation_error}

    def poll_capture(self):
        try:
            super().poll_capture()
        except (OSError, ValueError, RuntimeError) as exc:
            self.observation_error = str(exc)
            raise


class _Watchdog(Thread):
    def alive(self):
        return self.is_alive()


class EmueraSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.runtime = self.root / 'runtime'
        self.directory = self.root / 'bridge'
        self.executable = self.runtime / recipe.LAUNCHER
        self.session = uuid.uuid4().hex
        self.baseline = None
        self.host = self.bridge = None
        self.pid = None
        self.host_error = None

    def _runtime_digests(self):
        def scan(directory):
            no_links(directory)
            for path in directory.iterdir():
                no_links(path)
                if path.is_dir():
                    yield from scan(path)
                else:
                    yield path.relative_to(self.runtime).as_posix(), file_digest(path)
        return dict(scan(self.runtime))

    def prepare(self):
        revalidate_plan(self.plan)
        self.root.mkdir(exist_ok=False)
        self.directory.mkdir()
        for name in ('csv', 'erb', 'sav'):
            (self.runtime / name).mkdir(parents=True)
        for source, destination, digest in (
                (Path(self.plan['artifacts']) / recipe.RUNTIME, self.runtime / recipe.RUNTIME, recipe.RUNTIME_SHA256),
                (Path(self.plan['source']) / recipe.SCRIPT, self.runtime / 'erb/bridge.erb', recipe.SCRIPT_SHA256),
                (recipe.LAUNCHER_SOURCE, self.runtime / 'Launcher.cs', self.plan['artifact_digests']['Launcher.cs'])):
            no_links(source)
            shutil.copyfile(source, destination)
            if file_digest(destination) != digest:
                raise ValueError('runtime_changed: Emuera input changed during preparation')
        (self.runtime / 'csv/GAMEBASE.csv').write_bytes(recipe.GAMEBASE)
        build(self.executable, source=self.runtime / 'Launcher.cs')
        self.baseline = self._runtime_digests()

    def _watch(self):
        try:
            host.run(self.runtime / recipe.RUNTIME, self.executable, self.directory, self.session,
                     supervisor=ManagedSupervisor)
        except Exception as exc:
            self.host_error = str(exc)

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前须准备，且不能重复启动')
        if (recipe.source_digests(self.plan['source']) != self.plan['source_digests']
                or recipe.artifact_digests(self.plan['artifacts']) != self.plan['artifact_digests']
                or self._runtime_digests() != self.baseline):
            raise ValueError('runtime_changed: Emuera runtime or scene changed')
        no_links(self.directory)
        if any(self.directory.iterdir()) or {p.name for p in self.root.iterdir()} - {
                'runtime', 'bridge', 'session.json', 'start.request.json', 'stop.request'}:
            raise ValueError('Emuera 会话包含未登记的文件')
        if cancelled():
            raise InterruptedError('接入已取消')
        self.host = _Watchdog(target=self._watch, daemon=True)
        self.host.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError(self.host_error or 'target_exited_before_ready')
            if self.bridge is None and (self.directory / 'connection.json').is_file():
                meta = read_json(self.directory / 'connection.json')
                self.pid = meta['pid']
                self.bridge = BridgeClient(self.directory, pid=self.pid, exe_path=str(self.executable))
            if self.bridge:
                state = self.bridge.status()
                if state.get('connected'):
                    if state.get('error'):
                        raise RuntimeError(state['error'])
                    if state.get('observation_seq', 0) > 0:
                        return {**state, **validate_ready(self.root, state), 'state': 'ready_paused',
                                'bridge_directory': str(self.directory)}
            time.sleep(.02)
        raise TimeoutError('startup_timeout: Emuera 未取得有效首图')

    def stop(self):
        release = release_error = None
        if self.host:
            release = False
            try:
                if self.bridge and self.host.alive():
                    state = self.bridge.status()
                    # A capture fault latches the supervisor closed to new input. An empty
                    # held list here already requires the target's release acknowledgement.
                    release = (state.get('connected') is True and bool(state.get('observation_error'))
                               and bool(state.get('error')) and state.get('paused') is True
                               and state.get('held') == [])
                    if not release:
                        self.bridge.pause()
                        pause_id = read_json(self.directory / 'pause.json')['id']
                        deadline = time.monotonic() + 1
                        while self.host.alive() and time.monotonic() < deadline:
                            state = self.bridge.status()
                            if (state.get('connected') and state.get('pause_id') == pause_id
                                    and state.get('paused') is True and state.get('held') == []):
                                release = True
                                break
                            time.sleep(.02)
                if not release:
                    release_error = 'pause_not_acknowledged'
            except (OSError, ValueError, RuntimeError) as exc:
                release_error = str(exc)
            finally:
                write_json(self.directory / 'stop.json', {'session_id': self.session})
                self.host.join(3)
        termination = self.directory / 'termination.json'
        ended = read_json(termination) if termination.is_file() else {}
        exited = not (self.host and self.host.alive()) and not _pid_path(ended.get('pid') or self.pid or 0)
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
        if ended:
            result['termination_reason'] = ended['reason']
        write_json(self.root / 'cleanup.json', result)
        return result
