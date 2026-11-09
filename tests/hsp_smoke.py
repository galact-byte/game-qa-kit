"""在独立桌面运行 HSP 3.7 空白工程，验收正常输入、取证及安全停止。"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit import win32
from gameqakit.control import BridgeClient, _write_ini, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json


def run(runtime, compiler, plugin, work, *, common, desktop_mismatch=False, wait_loop=False):
    work.mkdir(parents=True, exist_ok=False)
    app = work / 'app'
    app.mkdir()
    bridge = work / 'bridge'
    bridge.mkdir()
    executable = app / 'HspProbe.exe'
    shutil.copy2(runtime, executable)
    shutil.copy2(plugin, app / 'GameQaHsp.dll')
    source = Path(__file__).parent / 'hsp/Probe.hsp'
    text = source.read_text(encoding='utf-8')
    if wait_loop:
        text = text.replace('await 30', 'wait 1')
    (app / 'probe.hsp').write_text(text, encoding='utf-8')
    import subprocess
    result = subprocess.run([str(compiler), '-d', '-oprobe.ax', '--compath=' + str(common) + '/', 'probe.hsp'],
                            cwd=app, capture_output=True)
    (work / 'compile.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('HSP 空白工程编译失败')
    report = {'ok': False, 'actions': [], 'foreground_samples': []}
    session = uuid.uuid4().hex
    stop = threading.Event()
    sampler = None
    try:
        process = DesktopProcess([str(executable), str(app / 'probe.ax')], cwd=app,
                                 env={'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session})
        if desktop_mismatch:
            process.env['GAMEQAKIT_DESKTOP_NAME'] = 'gameqakit-wrong-binding'
        with process as child:
            report.update(pid=child.pid, desktop=child.desktop_name)
            write_connection(bridge, {'protocol': 1, 'session_id': session, 'pid': child.pid,
                                     'exe_path': str(executable), 'command_format': 'ini', 'engine': 'hsp3'})
            client = BridgeClient(bridge, pid=child.pid, exe_path=executable)
            def sample():
                while not stop.wait(.05):
                    report['foreground_samples'].append(int(win32.user32.GetForegroundWindow() or 0))
            sampler = threading.Thread(target=sample)
            sampler.start()
            child.run()
            if desktop_mismatch:
                stage = bridge / 'plugin-stage.txt'
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if stage.is_file() and stage.read_text() == 'isolated_desktop_required':
                        break
                    time.sleep(.02)
                else:
                    raise AssertionError('错误桌面绑定没有拒绝加载输入桥')
                assert not (bridge / 'status.json').exists(), '错误桌面仍发布了控制能力'
                report.update(ok=True, rejected='isolated_desktop_required')
                return
            def wait_state(predicate, timeout=3):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    state = client.status()
                    if predicate(state):
                        return state
                    if not child.alive():
                        import ctypes
                        from ctypes import wintypes
                        from gameqakit.desktop import k
                        code = wintypes.DWORD()
                        k.GetExitCodeProcess(child.process, ctypes.byref(code))
                        raise AssertionError('HSP 提前退出: ' + hex(code.value) + ' status=' + str(state))
                    time.sleep(.02)
                raise AssertionError('状态未达到预期: ' + str(state))
            report['initial'] = wait_state(lambda s: s.get('connected') and s.get('input_ready'))
            def action(identifier, name, **options):
                receipt = client.resume(command_id=identifier) if name == 'resume' else client.action(name, command_id=identifier, **options)
                report['actions'].append(receipt)
                # The fixture writes observed game values, independently of the bridge.
                state = json.loads((app / 'observed.json').read_text(encoding='ascii'))
                receipt['observed'] = state
                print(identifier, receipt['frame'], state, flush=True)
                return state
            start = action('resume', 'resume')
            right = action('right', 'move', direction='right', frames=6)
            up = action('up', 'move', direction='up', frames=4)
            assert right['x'] == start['x'] + 12, 'getkey 未产生准确位移'
            assert up['y'] == right['y'] - 4, 'stick 未产生准确位移'
            menu = action('menu', 'confirm')
            closed = action('cancel', 'cancel')
            assert menu['menu'] == 1 and closed['menu'] == 0, '确认或取消未被游戏消费'
            click = action('click', 'click', x=400, y=300)
            assert click['clicks'] == 1 and click['mx'] == 400 and click['my'] == 300, '鼠标命中未被游戏消费'
            native = action('native-button', 'click', x=70, y=405)
            assert native['native'] == 1, '原生按钮没有通过正常鼠标输入触发'
            client.pause()
            report['pause'] = wait_state(lambda s: s.get('connected') and s.get('paused') and s.get('held') == [])
            action('resume-after-pause', 'resume')
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(client.action, 'click', x=70, y=405, frames=120, command_id='button-preempted')
                wait_state(lambda s: s.get('held') == ['mouse0'])
                client.pause()
                try:
                    pending.result(timeout=3)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('暂停没有中断原生按钮按下状态')
            wait_state(lambda s: s.get('connected') and s.get('paused') and s.get('held') == [])
            recovered = action('resume-after-button-pause', 'resume')
            assert recovered['native'] == 1, '暂停取消按压时误触发了按钮'
            report['button_preempted'] = json.loads((bridge / 'results/button-preempted.json').read_text(encoding='utf-8'))
            assert report['button_preempted']['error'] == 'manual_pause'
            pause_file = bridge / 'pause.ini'
            saved_pause = pause_file.read_bytes()
            _write_ini(pause_file, {'session_id': 'other-session', 'id': 'foreign'})
            time.sleep(.15)
            foreign_state = client.status()
            assert foreign_state.get('connected') and not foreign_state['paused'] and foreign_state['held'] == [], '旧会话暂停影响了当前会话'
            report['foreign_pause'] = foreign_state
            # Restore the current generation before the host submits another request.
            pause_file.write_bytes(saved_pause)
            action('after-foreign-pause', 'wait', frames=2)
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(client.action, 'move', direction='right', frames=120, command_id='preempted')
                wait_state(lambda s: s.get('held') == ['right'])
                client.pause()
                try:
                    pending.result(timeout=3)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('暂停没有中断动作')
            report['preempted'] = json.loads((bridge / 'results/preempted.json').read_text(encoding='utf-8'))
            assert report['preempted']['error'] == 'manual_pause'
            wait_state(lambda s: s.get('connected') and s.get('paused') and s.get('held') == [])
            action('resume-for-lease', 'resume')
            _write_ini(bridge / 'lease.ini', {'session_id': session, 'expires_at': 0})
            report['expiry'] = wait_state(lambda s: s.get('connected') and s.get('paused') and s.get('held') == [] and s.get('error') == 'lease_expired')
            report['windows'] = child.windows()
            assert not {w['hwnd'] for w in report['windows']}.intersection(report['foreground_samples'])
            report['ok'] = True
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(2)
        write_json(work / 'result.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--common', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--desktop-mismatch', action='store_true')
    parser.add_argument('--wait-loop', action='store_true')
    args = parser.parse_args()
    run(args.runtime.resolve(), args.compiler.resolve(), args.plugin.resolve(), args.work.resolve(),
        common=args.common.resolve(), desktop_mismatch=args.desktop_mismatch, wait_loop=args.wait_loop)
