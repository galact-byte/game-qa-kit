"""固定 TyranoScript 自写工程宿主；独立于 Electron 执行 Job 回收期限。"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.control import write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json

RUNTIME_SHA256 = '07af90854455fcb4c8f16d2eae885373d6960df4f9023717a780ff08b056d8f1'


def run(runtime: Path, directory: Path, session: str, data_root: Path, *, fixture_root=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session):
        raise ValueError('会话编号无效')
    for path in (runtime, directory, data_root):
        if not path.is_absolute():
            raise ValueError('运行时、会话及运行数据目录必须使用绝对路径')
    if (directory / 'connection.json').exists():
        raise ValueError('必须使用全新的会话目录，保留原会话证据')
    with runtime.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != RUNTIME_SHA256:
        raise ValueError('运行时摘要未验证，不启动')
    if (runtime.parent / 'resources/app.asar').exists():
        raise ValueError('实验宿主只接受自写 resources/app 工程，不启动打包商业场景')
    if not (runtime.parent / 'resources/app/main.js').is_file():
        raise ValueError('缺少显式自写工程入口')
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('appdata', 'localappdata', 'userdata', 'cache', 'tmp'):
        (data_root / name).mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    main_changed = renderer_changed = started
    main_seq = renderer_seq = None
    release_id = capture_id = None
    release_started = capture_started = None
    ready = False
    stopping = None
    reason = 'target_exited'
    process = DesktopProcess([str(runtime)], cwd=runtime.parent, env={
        'GAMEQAKIT_BRIDGE_DIR': str(directory), 'GAMEQAKIT_SESSION_ID': session,
        'GAMEQAKIT_RUNTIME_ROOT': str(data_root),
        'APPDATA': str(data_root / 'appdata'), 'LOCALAPPDATA': str(data_root / 'localappdata'),
        'TMP': str(data_root / 'tmp'), 'TEMP': str(data_root / 'tmp')})
    if fixture_root is not None:
        for key in tuple(process.env):
            if key.upper().startswith(('ELECTRON_', 'NODE_', 'GAMEQA_FIXTURE_ROOT')):
                process.env.pop(key)
        process.env['GAMEQA_FIXTURE_ROOT'] = str(fixture_root)
    with process as target:
        identity = dict(protocol=2, engine='tyranoscript', session_id=session,
                        pid=target.pid, exe_path=str(runtime))
        write_connection(directory, identity)
        write_json(directory / 'supervisor.json', dict(identity, host_pid=os.getpid(),
            desktop_name=target.desktop_name, runtime_sha256=digest, data_root=str(data_root)))
        target.run()
        while target.alive():
            now = time.monotonic()
            try:
                stop = read_json(directory / 'stop.json')
                if isinstance(stop, dict) and stop.get('session_id') == session and stopping is None:
                    reason = 'stop_requested'
                    stopping = now
            except (OSError, ValueError):
                pass
            try:
                state = read_json(directory / 'watchdog.json')
            except (OSError, ValueError):
                state = {}
            valid = (isinstance(state, dict) and state.get('session_id') == session
                     and state.get('pid') == target.pid
                     and type(state.get('seq')) is int and state['seq'] >= 0
                     and type(state.get('renderer_seq')) is int and state['renderer_seq'] >= 0)
            if valid:
                if main_seq != state['seq']:
                    main_seq, main_changed = state['seq'], now
                if renderer_seq != state['renderer_seq']:
                    renderer_seq, renderer_changed = state['renderer_seq'], now
                ready = ready or state.get('ready') is True
                pending = state.get('release_pending')
                if pending and pending != release_id:
                    release_id, release_started = pending, now
                elif not pending:
                    release_id = release_started = None
                capture = state.get('capture_pending')
                if capture and capture != capture_id:
                    capture_id, capture_started = capture, now
                elif not capture:
                    capture_id = capture_started = None
            if stopping is not None and now - stopping > 1:
                reason = 'shutdown_timeout'
            elif not ready and now - started > 15:
                reason = 'startup_timeout'
            elif ready and (now - main_changed > 1 or now - renderer_changed > 1):
                reason = 'activity_timeout'
            elif release_started is not None and now - release_started > 1:
                reason = 'release_timeout'
            elif capture_started is not None and now - capture_started > 3:
                reason = 'capture_timeout'
            else:
                time.sleep(.01)
                continue
            break
    write_json(directory / 'termination.json', dict(identity, reason=reason,
        elapsed_ms=int((time.monotonic() - started) * 1000), target_alive=target.alive()))
    return 0 if reason in ('target_exited', 'stop_requested') else 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--data-root', type=Path, help='显式隔离运行数据根目录，可供自写工程跨会话读档')
    args = parser.parse_args()
    return run(args.runtime.resolve(), args.directory.resolve(), args.session,
               args.data_root.resolve() if args.data_root else args.directory.resolve() / 'runtime')


if __name__ == '__main__':
    raise SystemExit(main())
