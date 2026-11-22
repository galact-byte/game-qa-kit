"""在独立 X11 会话验收源码／冻结 MCP 观察链，仅运行自写窗口。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.probe_x11_capture import X11, MARKERS, COVER, reply, command
from tools.verify_macos_product import Client, wait_value


def counter(image):
    image = image.convert('RGB')
    width, height = image.size
    colors = tuple((color >> 16, (color >> 8) & 255, color & 255) for color in MARKERS)
    assert tuple(image.getpixel(p) for p in ((8, 8), (width-8, 8), (8, height-8), (width-8, height-8))) == colors
    value = 0
    for bit in range(16):
        color = image.getpixel((26 + bit * 16, 38))
        assert color in ((0, 0, 0), (255, 255, 255)), color
        value |= int(color == (255, 255, 255)) << bit
    return value


def run(root, executable=None, renderer='cpu'):
    root.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'mode': 'frozen' if executable else 'source', 'renderer': renderer,
              'phases': {}, 'cleanup': []}
    children, logs, client, x11 = [], [], None, None
    def start(kind):
        args = [sys.executable, '-B', str(ROOT / 'tools/probe_x11_capture.py'), '--fixture', kind]
        if kind == 'target' and renderer == 'opengl':
            args = [sys.executable, '-B', str(ROOT / 'tools/probe_x11_gl_fixture.py')]
        log = (root / (kind + '.log')).open('w'); logs.append(log)
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        children.append(process)
        return process, reply(process)
    try:
        target, ready = start('target')
        result['target'] = ready
        name = 'gameqa-x11-opengl-target' if renderer == 'opengl' else 'gameqa-x11-target'
        profile = {'name': 'x11-fixture', 'proc': Path(sys.executable).stem, 'title_substr': name,
                   'exe_path': str(Path('/proc', str(target.pid), 'exe').resolve()),
                   'data_dir': str(root / 'data')}
        (root / 'profile.json').write_text(json.dumps(profile))
        env = dict(os.environ, GAMEQAKIT_PROFILE=str(root / 'profile.json'),
                   GAMEQAKIT_PROFILES=str(root / 'profiles'))
        args = [str(executable), '--mcp'] if executable else [sys.executable, '-B', '-m', 'gameqakit.mcp_server']
        client = Client(args, env, root)
        tools = {row['name'] for row in client.rpc('tools/list', {})['tools']}
        assert {'processes', 'win', 'capture', 'watch_start', 'watch_stop', 'mark'} <= tools
        assert not tools.intersection({'click', 'key', 'game_start', 'background_start'})
        windows = client.call('processes')
        assert any(row['window_id'] == ready['window'] for row in windows['processes']), windows
        result['window'] = client.call('win')
        assert result['window']['window_id'] == ready['window']
        result['initial'] = client.call('capture', out=str(root / 'initial.png'))
        client.call('watch_start', interval=.3, threshold=0, analysis_mode='rules')
        active = wait_value(lambda: client.call('watch_status'), lambda row: row.get('captured', 0) >= 3)
        archive = Path(active['out'])
        cover, result['cover'] = start('cover')
        time.sleep(.15)
        x11 = X11()
        result['focus_before'] = x11.focus()
        for phase in ('covered', 'frozen', 'resized-covered'):
            if phase == 'frozen': command(target, 'freeze')
            if phase == 'resized-covered': command(target, 'resize')
            rows = result['phases'][phase] = []
            for i in range(4):
                path = root / f'{phase}-{i}.png'
                started = time.monotonic()
                response = client.call('capture', out=str(path))
                elapsed = time.monotonic() - started
                assert elapsed < 3, elapsed
                with Image.open(path) as image:
                    value, size = counter(image), list(image.size)
                assert size == ([400, 240] if phase == 'resized-covered' else [320, 180]), size
                pixels = x11.image(x11.root, *size, x=60, y=60)
                fraction = sum(p == COVER for p in pixels) / len(pixels)
                assert fraction == 1, fraction
                from tools.probe_x11_capture import png
                desktop_path = root / f'{phase}-{i}-desktop.png'
                png(desktop_path, pixels, *size)
                focus = x11.focus()
                assert focus == result['focus_before'], '被动采集改变焦点'
                rows.append({'counter': value, 'size': size, 'elapsed_ms': elapsed * 1000,
                             'coverage': fraction, 'focus': focus, 'response': response,
                             'screenshot': path.name, 'screenshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'desktop': desktop_path.name, 'desktop_sha256': hashlib.sha256(desktop_path.read_bytes()).hexdigest()})
                time.sleep(.15)
            values = [row['counter'] for row in rows]
            if phase == 'frozen':
                assert len(set(values)) == 1, values
                result['frozen_control_rejected'] = True
            else:
                assert all(b > a for a, b in zip(values, values[1:])), values
        result['mark'] = client.call('mark', note='X11 自写窗口遮挡观察验收')
        assert Path(result['mark']['marked']).is_file()
        result['status'] = client.call('watch_status')
        rows = [json.loads(line) for line in (archive / 'index.jsonl').read_text().splitlines()]
        assert rows and result['status']['capture_source']['platform'] == 'linux'
        assert all(row['hwnd'] is None and row['capture_source'] == result['status']['capture_source'] for row in rows)
        times = [row['t'] for row in rows]
        assert times == sorted(set(times))
        result['archive_frames'] = len(rows)
        client.call('watch_stop')
        stopped = wait_value(lambda: client.call('watch_status'), lambda row: not row.get('running'))
        assert stopped.get('ok'), stopped
        assert target.poll() is None
        client.call('watch_start', interval=.3, threshold=0, analysis_mode='off')
        wait_value(lambda: client.call('watch_status'), lambda row: row.get('captured', 0) > 0)
        target.stdin.write('quit\n'); target.stdin.flush(); target.wait(timeout=3)
        result['target_exit'] = wait_value(lambda: client.call('watch_status'), lambda row: not row.get('running'))
        assert not result['target_exit']['ok'], result['target_exit']
        result['ok'] = True
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        if client:
            try: client.call('watch_stop')
            except Exception: pass
            (root / 'mcp-calls.json').write_text(json.dumps(client.calls, ensure_ascii=False, indent=2))
            client.close()
        if x11: x11.close()
        for process in reversed(children):
            forced = False
            if process.poll() is None:
                try: process.communicate('quit\n', timeout=3)
                except (subprocess.TimeoutExpired, BrokenPipeError):
                    forced = True; process.kill(); process.communicate(timeout=3)
            else:
                process.stdin.close(); process.stdout.close()
            result['cleanup'].append({'pid': process.pid, 'code': process.returncode, 'forced': forced})
        for log in logs: log.close()
        if any(row['forced'] or row['code'] != 0 for row in result['cleanup']): result['ok'] = False
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['ok'], result
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--renderer', choices=('cpu', 'opengl'), default='cpu')
    args = parser.parse_args()
    print(json.dumps(run(args.root.resolve(), args.executable, args.renderer), ensure_ascii=False))
