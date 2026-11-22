"""自写 X11 目标的身份、辅助进程和服务器故障验收。"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit import x11_capture
from gameqakit.capture_session import CaptureEnded
from tools.probe_x11_capture import reply, command


def next_frame(session, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = session.read(timeout=.05, stop_event=threading.Event())
        if frame is not None: return frame
    raise AssertionError('采集会话没有给出图像或终止原因')


def run(root, executable=None):
    root.mkdir(parents=True, exist_ok=False)
    original = x11_capture.helper_command
    if executable: x11_capture.helper_command = lambda: [str(executable), '-m', 'gameqakit.x11_helper']
    result = {'ok': False, 'mode': 'frozen' if executable else 'source', 'cases': []}
    try:
        for scenario in ('changed-pid', 'unmap', 'helper-exit', 'server-hang', 'server-exit'):
            case = {'scenario': scenario, 'ok': False}
            result['cases'].append(case)
            directory = root / scenario; directory.mkdir()
            server = target = session = None
            display_before = os.environ.get('DISPLAY')
            logs = []
            try:
                # 每个故障拥有自己的服务器，绝不停止继承的 DISPLAY。
                with (directory / 'server.log').open('w') as server_log:
                    server = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1024x768x24', '+extension', 'Composite', '-nolisten', 'tcp'],
                                              stdout=subprocess.PIPE, stderr=server_log, text=True)
                    import selectors
                    selector = selectors.DefaultSelector(); selector.register(server.stdout, selectors.EVENT_READ)
                    try:
                        assert selector.select(5), 'Xvfb 未发布 DISPLAY'
                        number = server.stdout.readline().strip()
                    finally: selector.close()
                    assert number.isdigit(), number
                    os.environ['DISPLAY'] = ':' + number
                    log = (directory / 'target.log').open('w'); logs.append(log)
                    target = subprocess.Popen([sys.executable, '-B', str(ROOT / 'tools/probe_x11_capture.py'), '--fixture', 'target'],
                                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
                    ready = reply(target)
                    source = next(row for row in x11_capture.list_windows() if row['window_id'] == ready['window'])
                    expected = {**source, 'pid': source['pid'] + 1} if scenario == 'changed-pid' else source
                    session = x11_capture.X11CaptureSession(expected, interval=.03)
                    if scenario != 'changed-pid': next_frame(session)
                    if scenario == 'unmap': command(target, 'unmap')
                    elif scenario == 'helper-exit': session.process.terminate(); session.process.wait(timeout=3)
                    elif scenario == 'server-hang': os.kill(server.pid, signal.SIGSTOP)
                    elif scenario == 'server-exit': server.terminate(); server.wait(timeout=3)
                    begin = time.monotonic()
                    try:
                        while True: next_frame(session)
                    except CaptureEnded as exc:
                        case['error'] = str(exc)
                    case['ended_seconds'] = time.monotonic() - begin
                    assert case['ended_seconds'] < 3.5, case
                    stop = threading.Event(); stop.set()
                    begin = time.monotonic()
                    assert session.read(timeout=3, stop_event=stop) is None
                    case['cancel_seconds'] = time.monotonic() - begin
                    assert case['cancel_seconds'] < .1
                    session.close(); session.close()
                    assert session.process.poll() is not None and not session.worker.is_alive()
                    case['helper_exit'] = session.process.returncode
                    case['ok'] = True
            finally:
                if server and server.poll() is None:
                    os.kill(server.pid, signal.SIGCONT)
                if session: session.close()
                if target:
                    try: target.communicate('quit\n', timeout=3)
                    except (subprocess.TimeoutExpired, BrokenPipeError):
                        target.kill(); target.communicate(timeout=3)
                    case['target_exit'] = target.returncode
                if server:
                    if server.poll() is None: server.terminate()
                    server.wait(timeout=3); server.stdout.close()
                    case['server_exit'] = server.returncode
                for log in logs: log.close()
                if display_before is None: os.environ.pop('DISPLAY', None)
                else: os.environ['DISPLAY'] = display_before
                (directory / 'result.json').write_text(json.dumps(case, ensure_ascii=False, indent=2))
        result['ok'] = all(row['ok'] for row in result['cases'])
    finally:
        x11_capture.helper_command = original
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['ok'], result
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root.resolve(), args.executable), ensure_ascii=False))
