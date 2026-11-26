"""真实 RGSS300 调度回归：独立截图预算不能因串联观察图而耗尽心跳。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json
from plugins.rgss3 import prepare as recipe
from tests.rgss_heartbeat_smoke import HARNESS

# 每张图单独低于两秒；固定到同一渲染帧，确定性覆盖累加超限的调度。
BUDGET_FIXTURE = '''
  module GameQaBridge
    class << self
      alias_method :budget_native_observe, :observe
      def observe
        if @current && @current['phase']=='settle' && @current['settle']==1
          @last_observation=0
        end
        budget_native_observe
      end
      [:capture, :capture_png].each do |method|
        original="budget_native_#{method}".to_sym
        alias_method original, method
        define_method(method) do |id|
          started=Time.now.to_f
          result=send(original,id)
          remaining=1.05-(Time.now.to_f-started)
          sleep(remaining) if remaining>0
          @budget_rows ||= []
          @budget_rows << {'method'=>method.to_s,'id'=>id,'seconds'=>Time.now.to_f-started,
                           'frame'=>@frame,'command'=>@current && @current['request']['id']}
          write('capture-times.json',@budget_rows)
          result
        end
      end
    end
  end
'''


def run(runtime_source, root):
    from rubymarshal.writer import writes
    if root.exists():
        raise FileExistsError(root)
    source = runtime_source.resolve()
    dll = source / 'System/RGSS300.dll'
    if hashlib.sha256(dll.read_bytes()).hexdigest() != recipe.COMMERCIAL_PROFILE['library_sha256']:
        raise ValueError('仅接受登记的 RGSS300 运行库')
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source / 'Game.exe', dll)}
    root = root.resolve()
    runtime, bridge = root / 'runtime', root / 'bridge'
    (runtime / 'System').mkdir(parents=True)
    (runtime / 'Data').mkdir()
    executable = runtime / 'Game.exe'
    shutil.copy2(source / 'Game.exe', executable)
    shutil.copy2(dll, runtime / 'System/RGSS300.dll')
    harness = HARNESS.replace('64,48', '640,480').replace('  loop do\n', BUDGET_FIXTURE + '  loop do\n')
    (runtime / 'Data/Scripts.rvdata2').write_bytes(writes([[1, b'ActionBudgetProbe', zlib.compress(harness.encode('utf-8'))]]))
    (runtime / 'Game.ini').write_text('[Game]\nRTP=\nLibrary=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\nTitle=GameQA action budget test\n', encoding='ascii')
    session_id = uuid.uuid4().hex
    host = DesktopProcess([str(executable)], cwd=str(runtime), env={
        'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session_id,
        'GAMEQAKIT_PLUGIN_FILE': str(recipe.BRIDGE), 'GAMEQAKIT_OBSERVATION': '1',
        'HEARTBEAT_SCENARIO': 'action-budget', 'TEMP': str(root), 'TMP': str(root)})
    report = {'ok': False, 'calls': [], 'scope': 'synthetic 640x480; no game data or saves'}
    stop = threading.Event()
    monitor = None
    client = None
    ages = []
    try:
        host.__enter__()
        write_connection(bridge, dict(protocol=1, session_id=session_id, pid=host.pid,
                                     exe_path=str(executable), engine='rpgmaker_vxace', command_format='ini'))
        client = BridgeClient(bridge, pid=host.pid, exe_path=str(executable))
        host.run()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = client.status()
            if state.get('connected') and state.get('observation_seq', 0) > 0:
                break
            time.sleep(.01)
        else:
            raise TimeoutError('synthetic_startup_failed:' + str(state))

        def watch():
            # 与受管生命周期相同的客户端判据，不改变两秒期限。
            while not stop.wait(.01):
                state = client.status()
                if state.get('connected'):
                    ages.append(time.time() - state['updated_at'])
                elif state.get('error') == '插件心跳过期，停止自动操作':
                    report['stale_observed'] = True
                    client.pause()
                    return
        monitor = threading.Thread(target=watch, name='budget-heartbeat')
        monitor.start()
        for action in ('resume', 'wait'):
            started = time.monotonic()
            value = (client.resume(command_id='budget-resume', timeout=5) if action == 'resume' else
                     client.action('wait', frames=60, command_id='budget-wait', timeout=5))
            report['calls'].append(dict(action=action, seconds=time.monotonic()-started, result=value))
            if value.get('status') != 'done' or not value.get('consumed') or value.get('historical'):
                raise AssertionError('动作没有取得本轮有效回执：' + str(value))
        if report.get('stale_observed'):
            raise AssertionError('独立截图预算串联导致心跳过期')
        report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        stop.set()
        if monitor:
            monitor.join()
        try:
            if host.pid and client is not None:
                client.pause()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    state = client.status()
                    if state.get('connected') and state.get('paused') and state.get('held') == []:
                        report['release_confirmed'] = True
                        break
                    time.sleep(.01)
        finally:
            host.close()
        from gameqakit.process_identity import _pid_path
        report['target_exited'] = not host.pid or not _pid_path(host.pid)
        report['source_unchanged'] = all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())
        report['max_connected_heartbeat_age'] = max(ages, default=None)
        report['ok'] = report['ok'] and all(report.get(k) is True for k in ('release_confirmed', 'target_exited', 'source_unchanged'))
        write_json(root / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    return run(args.runtime, args.work)


if __name__ == '__main__':
    raise SystemExit(main())
