"""在 macOS arm64 验证既有 Godot 桥，仅运行仓库自写 PCK，不开放产品控制入口。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import uuid

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_godot_bridge import fixture_pack
from gameqakit.sessions import read_json, write_json


def run(root, runtime, probe):
    assert sys.platform == 'darwin' and platform.machine() == 'arm64'
    root.mkdir(parents=True, exist_ok=False)
    directory = root / 'bridge'; directory.mkdir()
    for name in ('home', 'tmp', 'data'): (root / name).mkdir()
    runtime = runtime.resolve()
    result = {'ok': False, 'experimental_only': True, 'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
              'architecture': platform.machine(), 'actions': [], 'proofs': [], 'cleanup': {}}
    process = cover = None
    log = (root / 'runtime.log').open('w')
    cover_log = (root / 'cover.log').open('w')
    session = uuid.uuid4().hex
    pause_id = ''
    try:
        version = subprocess.run([str(runtime), '--version'], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        assert version.startswith('4.5.1.stable'), version
        result['version'] = version
        help_text = subprocess.run([str(runtime), '--help'], capture_output=True, text=True, check=True, timeout=15).stdout
        (root / 'runtime-help.txt').write_text(help_text)
        assert '--no-focus' in help_text, '运行时未提供不请求窗口焦点的启动参数'
        fixture_pack(root / 'fixture.pck')
        result['pack_sha256'] = hashlib.sha256((root / 'fixture.pck').read_bytes()).hexdigest()
        args = [str(runtime), '--main-pack', str(root / 'fixture.pck'), '--script', str(ROOT / 'plugins/godot/GameQaBridge.gd'),
                '--audio-driver', 'Dummy', '--max-fps', '60', '--no-focus', '--position', '100,200', '--log-file', str(root / 'godot.log')]
        env = dict(os.environ, HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), XDG_DATA_HOME=str(root / 'data'),
                   GAMEQAKIT_BRIDGE_DIR=str(directory), GAMEQAKIT_SESSION_ID=session,
                   GAMEQA_FIXTURE_STATE=str(root / 'fixture-state.json'))
        # exec 保持子进程 PID；连接元数据写完后才进入运行时，避免启动竞态。
        gate = "import os,sys,time; from pathlib import Path\np=Path(sys.argv[1]); end=time.monotonic()+10\nwhile not p.is_file():\n if time.monotonic()>end: sys.exit(3)\n time.sleep(.01)\nos.execv(sys.argv[2],sys.argv[2:])"
        process = subprocess.Popen([sys.executable, '-B', '-c', gate, str(directory / 'connection.json'), *args],
                                   cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        meta = {'protocol': 1, 'session_id': session, 'pid': process.pid, 'exe_path': str(runtime), 'engine': 'godot'}
        write_json(directory / 'connection.json', meta)

        def until(reader, predicate, timeout=3, renew=False):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if process.poll() is not None: raise RuntimeError(f'Godot 提前退出：{process.returncode}')
                if renew: lease()
                try:
                    value = reader()
                    if predicate(value): return value
                except (FileNotFoundError, json.JSONDecodeError): pass
                time.sleep(.02)
            raise TimeoutError('Godot 原生条件未在既定期限达到')

        def status():
            row = read_json(directory / 'status.json')
            assert all(row.get(key) == meta[key] for key in ('protocol', 'session_id', 'pid', 'exe_path'))
            assert time.time() - row['updated_at'] < 2
            return row

        def lease():
            write_json(directory / 'lease.json', {'session_id': session, 'expires_at': time.time() + 1.5})

        def fixture(): return read_json(root / 'fixture-state.json')

        def send(identifier, action, frames=1, **options):
            state = status()
            request = {'protocol': 1, 'session_id': session, 'id': identifier,
                       'payload': {'action': action, 'frames': frames, **options},
                       'after_frame': state['frame'], 'pause_id': pause_id, 'created_at': time.time()}
            lease(); write_json(directory / 'request.json', request)
            return request

        def action(identifier, kind, frames=1, **options):
            request = send(identifier, kind, frames, **options)
            begin = time.monotonic()
            receipt = until(lambda: read_json(directory / 'results' / (identifier + '.json')), lambda row: True, renew=True)
            assert receipt.get('ok') and receipt.get('id') == identifier and receipt.get('session_id') == session, receipt
            assert status()['held'] == [] and not fixture()['right_pressed'] and not fixture()['left_pressed']
            shot = directory / receipt['screenshot'] if not Path(receipt['screenshot']).is_absolute() else Path(receipt['screenshot'])
            # 桥回执截图实际使用相对于 bridge 的路径；所有路径必须仍在本次会话内。
            assert shot.resolve().is_relative_to(directory.resolve()), shot
            digest = hashlib.sha256(shot.read_bytes()).hexdigest()
            with Image.open(shot) as image:
                assert image.size == (320, 240)
                image.verify()
            row = {'request': request, 'receipt': receipt, 'screenshot_sha256': digest,
                   'fixture': fixture(), 'elapsed_ms': (time.monotonic()-begin)*1000}
            result['actions'].append(row)
            return shot

        initial = until(status, lambda row: row.get('observation_seq', 0) > 0 or row.get('error'), timeout=15)
        assert not initial.get('error') and initial['paused'] and initial['held'] == [] and initial['input_ready'], initial
        assert fixture()['x'] == 60 and Path(fixture()['user_data_dir']).resolve().is_relative_to(root)
        with Image.open(directory / initial['observation_screenshot']) as image:
            assert image.size == (320, 240) and image.convert('RGB').getpixel((65, 105)) == (255, 128, 32)
        shutil.copyfile(directory / initial['observation_screenshot'], root / 'initial.png')
        result['initial'] = initial
        cover = subprocess.Popen([str(probe), 'cover', str(root), str(process.pid)], stdout=cover_log, stderr=subprocess.STDOUT)
        until(lambda: read_json(root / 'cover-ready.json'), lambda row: 'window' in row)

        def proof(name, renew=False):
            with (root / (name + '-probe.log')).open('w') as stream:
                worker = subprocess.Popen([str(probe), 'proof', str(root), str(process.pid)],
                                          stdout=stream, stderr=subprocess.STDOUT)
                try:
                    end = time.monotonic() + 10
                    while worker.poll() is None:
                        if time.monotonic() >= end: raise TimeoutError('桌面遮挡证据探针未退出')
                        if renew: lease()
                        time.sleep(.05)
                    assert worker.returncode == 0, worker.returncode
                finally:
                    if worker.poll() is None:
                        worker.kill(); worker.wait(timeout=3)
            row = read_json(root / 'proof.json'); assert row['ok'], row
            for file in ('desktop-covered.png', 'target-covered.png', 'proof.json'):
                (root / file).rename(root / (name + '-' + file))
            result['proofs'].append({'phase': name, **row})

        proof('before-input')
        result['paused_observation'] = until(status, lambda row: row['frame'] > initial['frame'] and row['observation_seq'] > initial['observation_seq'])
        before = action('resume', 'resume')
        after = action('right10', 'move', 10, direction='right')
        assert fixture()['x'] == 100, fixture()
        with Image.open(before) as first, Image.open(after) as last:
            assert ImageChops.difference(first.convert('RGB'), last.convert('RGB')).getbbox()
            assert last.convert('RGB').getpixel((105, 105)) == (255, 128, 32)
        action('left10', 'move', 10, direction='left')
        assert fixture()['x'] == 60
        action('wait10', 'wait', 10)
        assert fixture()['x'] == 60
        proof('after-actions', renew=True)
        send('manual-hold', 'move', 120, direction='right')
        until(fixture, lambda row: row.get('right_pressed'), renew=True)
        pause_id = uuid.uuid4().hex
        write_json(directory / 'pause.json', {'session_id': session, 'id': pause_id})
        paused = until(status, lambda row: row.get('pause_id') == pause_id and row['paused'] and row['held'] == [])
        until(fixture, lambda row: not row['right_pressed'])
        result['manual_pause'] = {'status': paused, 'fixture': fixture(), 'receipt': read_json(directory / 'results/manual-hold.json')}
        assert result['manual_pause']['receipt']['error'] == 'manual_pause'
        action('resume-after-pause', 'resume')
        send('lease-hold', 'move', 120, direction='right')
        until(fixture, lambda row: row.get('right_pressed'))
        expired = until(status, lambda row: row['paused'] and row.get('error') == 'lease_expired' and row['held'] == [])
        until(fixture, lambda row: not row['right_pressed'])
        result['lease_expiry'] = {'status': expired, 'fixture': fixture(), 'receipt': read_json(directory / 'results/lease-hold.json')}
        assert result['lease_expiry']['receipt']['error'] == 'lease_expired'
        proof('after-release')
        result['ok'] = True
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        if cover:
            (root / 'cover-stop').touch()
            try: cover.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cover.kill(); cover.wait(timeout=3); result['ok'] = False
            result['cleanup']['cover_exit'] = cover.returncode
        if process:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3); result['ok'] = False
            result['cleanup']['target_exit'] = process.returncode
            result['cleanup']['target_exited'] = process.poll() is not None
            result['cleanup']['status_after_exit'] = read_json(directory / 'status.json') if (directory / 'status.json').is_file() else {}
        log.close(); cover_log.close()
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['ok'], result
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--probe', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root.resolve(), args.runtime.resolve(), args.probe.resolve()), ensure_ascii=False))
