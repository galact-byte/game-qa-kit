"""验证不同客户端串行创建/回收原生截图组件的行为与开销。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit import macos_capture as native
from tools.diagnose_macos_helper import wait_json
from tools.verify_macos_capture_probe import decode_counter, require_growth


def client(root, helper, number):
    import fcntl
    native.helper_command = lambda: [str(helper)]
    source = json.loads((root/'source.json').read_text())
    samples = []
    for index in range(4):
        started = time.monotonic()
        with (root/'native.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            frame = native._capture_once(source)
        samples.append({'counter': decode_counter(frame.image),
                        'elapsed_ms': (time.monotonic()-started)*1000})
        frame.image.save(root/f'client-{number}-{index}.png')
        time.sleep(.35)
    require_growth([s['counter'] for s in samples])
    (root/f'client-{number}.json').write_text(json.dumps(samples, indent=2))


def run(root, fixture, helper):
    root.mkdir(parents=True, exist_ok=False)
    native.helper_command = lambda: [str(helper)]
    children = []
    clients = []
    result = {'ok': False}
    try:
        children.append(subprocess.Popen([str(fixture), 'target', str(root)]))
        ready = wait_json(root/'target-ready.json')
        source = next(w for w in native.list_windows() if w['window_id'] == ready['window'])
        (root/'source.json').write_text(json.dumps(source))
        children.append(subprocess.Popen([str(fixture), 'cover', str(root)]))
        wait_json(root/'cover-ready.json')
        time.sleep(.6)
        for number in (1, 2):
            clients.append(subprocess.Popen([sys.executable, '-X', 'utf8', '-B', __file__,
                '--root', str(root), '--helper', str(helper), '--client', str(number)]))
        for process in clients:
            if process.wait(timeout=20) != 0:
                raise RuntimeError(f'Client failed: {process.returncode}')
        result.update(ok=True, clients=[json.loads((root/f'client-{n}.json').read_text()) for n in (1, 2)])
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        (root/'stop').touch()
        cleanup = []
        for process in clients + children:
            forced = False
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
                forced = True
            cleanup.append({'pid': process.pid, 'code': process.returncode, 'forced': forced})
        result['cleanup'] = cleanup
        (root/'result.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--client', type=int)
    args = parser.parse_args()
    if args.client:
        client(args.root.resolve(), args.helper.resolve(), args.client)
    else:
        run(args.root.resolve(), args.fixture.resolve(), args.helper.resolve())
