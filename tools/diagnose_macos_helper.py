"""串行对比原生组件的执行器、标准输入等待与遮挡首次取图。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit import macos_capture as native
from tools.verify_macos_capture_probe import decode_counter, require_growth


def wait_json(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(.05)
    raise TimeoutError(str(path))


def run_case(root, fixture, helper, asynchronous, hidden_start, concurrent=False):
    root.mkdir(parents=True, exist_ok=False)
    os.environ['GAMEQAKIT_NATIVE_ASYNC_STDIN'] = '1' if asynchronous else '0'
    native.helper_command = lambda: [str(helper)]
    row = {'helper': helper.name, 'async_stdin': asynchronous, 'hidden_start': hidden_start,
           'ok': False, 'concurrent': concurrent, 'phase': 'start', 'samples': {}}
    children = []
    session = None
    peer = None
    log = (root/'fixture.log').open('w')
    try:
        children.append(subprocess.Popen([str(fixture), 'target', str(root)], stdout=log, stderr=log))
        ready = wait_json(root/'target-ready.json')
        source = next(w for w in native.list_windows() if w['window_id'] == ready['window'])

        def cover():
            children.append(subprocess.Popen([str(fixture), 'cover', str(root)], stdout=log, stderr=log))
            wait_json(root/'cover-ready.json')
            time.sleep(.6)

        def collect(name):
            row['phase'] = name
            samples = row['samples'][name] = []
            deadline = time.monotonic() + 5
            while len(samples) < 4 and time.monotonic() < deadline:
                frame = session.read(timeout=.05, stop_event=threading.Event())
                if frame is not None:
                    frame.image.save(root/f'{name}-{len(samples)}.png')
                    samples.append(decode_counter(frame.image))
            require_growth(samples)

        if hidden_start:
            cover()
        session = native.MacOSCaptureSession(source, interval=.35)
        if not hidden_start:
            collect('visible')
            cover()
        if concurrent:
            peer = session
            session = native.MacOSCaptureSession(source, interval=.35)
            collect('occluded-second')
            session.close()
            session, peer = peer, None
            collect('occluded-first')
        else:
            collect('occluded')
        row['ok'] = True
    except Exception as exc:
        row['error'] = repr(exc)
    finally:
        (root/'stop').touch()
        if session:
            session.close()
        if peer:
            peer.close()
        cleanup = []
        for child in children:
            forced = False
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                forced = True
                child.kill()
                child.wait(timeout=2)
            cleanup.append({'pid': child.pid, 'exit_code': child.returncode, 'forced': forced})
        row['cleanup'] = cleanup
        log.close()
        (root/'result.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--helper', type=Path, action='append', required=True)
    args = parser.parse_args()
    rows = []
    for helper in args.helper:
        for asynchronous in (False, True):
            for concurrent in (False, True):
                name = f'{helper.name}-async{int(asynchronous)}-concurrent{int(concurrent)}'
                rows.append(run_case(args.root/name, args.fixture.resolve(), helper.resolve(),
                                     asynchronous, hidden_start=False, concurrent=concurrent))
    (args.root/'comparison.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
