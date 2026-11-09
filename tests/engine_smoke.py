"""显式运行已有 MV/MZ 空白工程的隔离副本；不适用于含绝对存档路径的任意游戏。

python tests/engine_smoke.py --source <空白工程> --runtime <已有 nw.exe> --work <新目录>
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit.control import BridgeClient
from gameqakit.desktop import DesktopProcess
from gameqakit.engines import inspect_game
from gameqakit.sessions import write_json
from gameqakit import win32


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if args.work.exists():
        raise ValueError('work 必须是尚不存在的新目录，禁止覆盖既有证据')
    engine = inspect_game(args.source)['engine']
    if engine not in ('rpgmaker_mv', 'rpgmaker_mz'):
        raise ValueError('此集成检查只支持 MV/MZ 空白工程')
    args.work.mkdir(parents=True)
    app = args.work / 'app'
    shutil.copytree(args.source, app, ignore=shutil.ignore_patterns('save', 'Save', '.git', '.trellis'))
    bridge = args.work / 'bridge'
    bridge.mkdir()
    session_id = uuid.uuid4().hex
    package = json.loads((app / 'package.json').read_text(encoding='utf-8-sig'))
    package['name'] = 'gameqa-probe-' + session_id
    package['single-instance'] = False
    package['window']['title'] = 'GameQA isolated ' + engine
    package['window']['show'] = True
    (app / 'package.json').write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
    plugin = Path(__file__).resolve().parent.parent / 'plugins/rpgmaker_js/GameQaBridge.js'
    (app / 'js/plugins').mkdir(exist_ok=True)
    shutil.copy2(plugin, app / 'js/plugins/GameQaBridge.js')
    with (app / 'js/plugins.js').open('a', encoding='utf-8') as stream:
        stream.write('\n$plugins.push({name:"GameQaBridge",status:true,description:"Local QA probe",parameters:{}});\n')
    for folder in ('tmp', 'runtime-data'):
        (args.work / folder).mkdir()
    env = {'GAMEQAKIT_BRIDGE_DIR': str(bridge.resolve()), 'GAMEQAKIT_SESSION_ID': session_id,
           'TEMP': str((args.work / 'tmp').resolve()), 'TMP': str((args.work / 'tmp').resolve())}
    report = {'engine': engine, 'source': str(args.source), 'actions': [], 'foreground_samples': [], 'ok': False}
    command = [str(args.runtime.resolve()), '--user-data-dir=' + str((args.work / 'runtime-data').resolve()), str(app.resolve())]
    stop = threading.Event()
    sampler = None
    try:
        with DesktopProcess(command, cwd=app, env=env) as child:
            report['pid'] = child.pid
            report['desktop'] = child.desktop_name
            write_json(bridge / 'connection.json', {'protocol': 1, 'session_id': session_id, 'pid': child.pid,
                       'exe_path': str(args.runtime.resolve()), 'engine': engine})
            client = BridgeClient(bridge, pid=child.pid, exe_path=args.runtime.resolve())
            def sample():
                while not stop.wait(0.05):
                    report['foreground_samples'].append(int(win32.user32.GetForegroundWindow() or 0))
            sampler = threading.Thread(target=sample)
            sampler.start()
            child.run()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = client.status()
                if status.get('connected') and status.get('capabilities'):
                    break
                if not child.alive():
                    raise RuntimeError('测试进程提前退出')
                time.sleep(0.1)
            else:
                raise RuntimeError('插件未在时限内就绪：' + str(status))
            for identifier, operation, options in [
                ('resume', 'resume', {}), ('title-confirm', 'confirm', {}),
                ('map-wait', 'wait', {'frames': 30}), ('move-right', 'move', {'direction': 'right', 'frames': 24}),
                ('open-menu', 'cancel', {}), ('close-menu', 'cancel', {})]:
                started = time.perf_counter()
                result = (client.resume(command_id=identifier, timeout=15) if operation == 'resume'
                          else client.action(operation, command_id=identifier, timeout=15, **options))
                report['actions'].append({**result, 'operation': operation,
                                          'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)})
                print(engine, identifier, result['scene'], result['frame'], report['actions'][-1]['elapsed_ms'], flush=True)
                if identifier == 'resume':
                    report['initial_scene'] = result['scene']
                    for number in range(10):
                        current = client.status()
                        if current.get('scene') == 'Scene_Title' and current.get('input_ready'):
                            report['title_ready'] = current
                            break
                        warmup = client.action('wait', frames=60, command_id='wait-title-' + str(number), timeout=15)
                        report['actions'].append({**warmup, 'operation': 'wait-for-title'})
                    else:
                        raise RuntimeError('未通过正常更新到达可操作的标题界面')
            pause = client.pause()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = client.status()
                if status.get('paused') and not status.get('held'):
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError('暂停未确认或仍有按键保持')
            report['pause'] = {**pause, 'confirmed': True, 'status': status}
            by_id = {row['id']: row for row in report['actions']}
            before_move = by_id['map-wait']['player']
            after_move = by_id['move-right']['player']
            if not (after_move['x'] > before_move['x'] and after_move['y'] == before_move['y'] and after_move['direction'] == 6):
                raise RuntimeError('空白地图向右输入未产生预期的真实角色移动')
            scenes = {row['id']: row['scene'] for row in report['actions']}
            expected = {'title-confirm': 'Scene_Map',
                        'move-right': 'Scene_Map', 'open-menu': 'Scene_Menu', 'close-menu': 'Scene_Map'}
            report['expected_scenes'] = expected
            for identifier, expected_scene in expected.items():
                if scenes.get(identifier) != expected_scene:
                    raise RuntimeError(f'{identifier} 未通过真实游戏逻辑：{scenes.get(identifier)} != {expected_scene}')
            report['windows'] = child.windows()
            own_windows = {window['hwnd'] for window in report['windows']}
            if own_windows.intersection(report['foreground_samples']):
                raise RuntimeError('测试窗口曾成为用户桌面前台')
            report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(timeout=2)
        write_json(args.work / 'result.json', report)
        print('Report:', args.work / 'result.json', flush=True)
    print('Validated images:', len(report['actions']), 'Source saves were not copied.', flush=True)


if __name__ == '__main__':
    main()
