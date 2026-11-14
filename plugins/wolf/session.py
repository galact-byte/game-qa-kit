"""固定官方 Wolf 范例的受管生命周期；不执行测试脚本或预定菜单动作。"""
from pathlib import Path
import os
import shutil
import subprocess
import time
import uuid

from gameqakit.admission import revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json, read_text
from gameqakit.win32 import _pid_path
from . import prepare as recipe
from .startup import initialize


class WolfSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.host = self.bridge = self.baseline = None

    def prepare(self):
        revalidate_plan(self.plan)
        self.baseline = recipe.prepare_managed(self.plan)
        return {'state': 'prepared', 'runtime': str(self.root / 'runtime')}

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前须由本创建者完成准备，且不能重复启动')
        if cancelled():
            raise InterruptedError('接入已取消')
        if recipe.artifact_digests(self.plan['artifacts']) != self.plan['artifact_digests']:
            raise RuntimeError('artifact_changed')
        if recipe.manifest(self.root / 'runtime') != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        runtime, directory = self.root / 'runtime', self.root / 'bridge'
        directory.mkdir()
        executable, artifacts, session = runtime / 'Game.exe', Path(self.plan['artifacts']), uuid.uuid4().hex
        deadline = time.monotonic() + 15
        self.host = DesktopProcess([str(executable)], cwd=runtime, disable_extension_points=True, env={
            'GAMEQAKIT_BRIDGE_DIR': str(directory), 'GAMEQAKIT_SESSION_ID': session,
            'TEMP': str(self.root / 'tmp'), 'TMP': str(self.root / 'tmp')})
        self.host.__enter__()
        write_connection(directory, {'protocol': 1, 'session_id': session, 'pid': self.host.pid,
            'owner_pid': os.getpid(), 'desktop': self.host.desktop_name, 'exe_path': str(executable),
            'command_format': 'ini', 'control_transport': 'wolf-shm-v1', 'wolf_initialize': '1'})
        loaded = subprocess.run([str(artifacts / 'WolfInjector.exe'), str(self.host.pid),
            str(artifacts / 'GameQaWolf.dll'), str(directory)], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=8)
        write_json(self.root / 'injector.json', {'returncode': loaded.returncode,
                                               'stdout': loaded.stdout, 'stderr': loaded.stderr})
        if loaded.returncode:
            raise RuntimeError('wolf_injection_failed：请查看本会话 injector.json')
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('target_exited_before_ready')
            try:
                stage = read_text(directory / 'plugin-stage.txt')
            except FileNotFoundError:
                stage = ''
            if stage:
                if stage != 'ready':
                    raise RuntimeError('wolf_bridge_failed:' + stage)
                break
            time.sleep(.02)
        else:
            raise TimeoutError('startup_timeout')
        self.bridge = BridgeClient(directory, pid=self.host.pid, exe_path=str(executable))
        self.host.run()
        paused_since = None
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('target_exited_before_ready')
            state = self.bridge.status()
            if state.get('connected') and state.get('input_ready') and state.get('at_frame_boundary'):
                if state.get('frame') != 0 or state.get('paused') is not True or state.get('held') != [] or state.get('error'):
                    raise RuntimeError('wolf_initial_pause_invalid')
                if paused_since is None:
                    paused_since = time.monotonic()
                # 沿用已实证入口的两秒零帧一致性观察，包含在原十五秒启动期限内。
                if time.monotonic() - paused_since >= 2:
                    break
            else:
                paused_since = None
            time.sleep(.02)
        else:
            raise TimeoutError('startup_timeout')
        if deadline - time.monotonic() < 3:
            raise TimeoutError('startup_timeout')
        initialized = initialize(self.host, self.bridge)
        write_json(self.root / 'startup-receipt.json', initialized)
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            state = self.bridge.status()
            if state.get('observation_error'):
                raise RuntimeError('observation_failed:' + state['observation_error'])
            if not state.get('connected'):
                raise RuntimeError('bridge_disconnected_after_startup')
            if state.get('observation_seq', 0) > 0:
                if not state.get('at_frame_boundary'):
                    raise RuntimeError('wolf_startup_not_at_boundary')
                image = validate_ready(self.root, state)
                write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': 'Game',
                    'exe_path': str(executable), 'data_dir': str(self.root / 'data')})
                return {**state, **image, 'state': 'ready_paused', 'bridge_directory': str(directory),
                        'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout')

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
                        if (state.get('connected') and state.get('paused') is True and state.get('held') == []
                                and state.get('at_frame_boundary')):
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
        source_ok = artifact_ok = False
        if (self.root / 'source-before.json').exists():
            after = recipe.manifest(self.plan['source'])
            write_json(self.root / 'source-after.json', after)
            source_ok = after == read_json(self.root / 'source-before.json')
            try:
                after = recipe.artifact_digests(self.plan['artifacts'])
            except (OSError, ValueError) as exc:
                after = {'error': str(exc)}
            write_json(self.root / 'artifact-after.json', after)
            artifact_ok = after == read_json(self.root / 'artifact-before.json')
        safe = exited and release is not False and source_ok and artifact_ok
        runtime = self.root / 'runtime'
        retained, changed = runtime.exists(), []
        if retained:
            after = recipe.manifest(runtime)
            write_json(self.root / 'runtime-after.json', after)
            changed = sorted(name for name in set(after) | set(self.baseline or {})
                             if after.get(name) != (self.baseline or {}).get(name))
            if safe and self.baseline is not None and after == self.baseline:
                shutil.rmtree(runtime)
                retained = False
        result = {'state': 'stopped' if safe else 'cleanup_failed', 'release_confirmed': release,
                  'release_error': release_error, 'target_exited': exited, 'source_unchanged': source_ok,
                  'artifact_unchanged': artifact_ok, 'runtime_retained': retained, 'runtime_changed': changed[:20]}
        write_json(self.root / 'cleanup.json', result)
        return result
