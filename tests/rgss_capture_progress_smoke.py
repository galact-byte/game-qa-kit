"""在独立 RGSS300 程序验证慢像素遍历的活性和中途暂停；不带游戏数据。"""
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

FIXTURE = r'''
  class Bitmap
    alias_method :progress_native_get_pixel, :get_pixel
    def get_pixel(x,y)
      if x==0 && y%4==0 && ENV['CAPTURE_PROGRESS_SCENARIO']!='publish-pause' && File.exist?('fault.trigger')
        current=GameQaBridge.instance_variable_get(:@current)
        if ENV['CAPTURE_PROGRESS_SCENARIO']=='observation' || current
          $capture_progress_delayed=true
          File.open('capture-progress.txt','wb') {|f|f.write(y.to_s)}
          sleep(0.2)
        end
      end
      progress_native_get_pixel(x,y)
    end
  end
  class << File
    alias_method :progress_native_rename, :rename
    def rename(source,target)
      if ENV['CAPTURE_PROGRESS_SCENARIO']=='publish-pause' && target.to_s.end_with?('progress-wait.bmp')
        File.open('capture-progress.txt','wb') {|f|f.write('publishing')}
        sleep(0.3)
      end
      progress_native_rename(source,target)
    end
  end
  module GameQaBridge
    class << self
      alias_method :progress_native_observe, :observe
      def observe
        return if File.exist?('fault.trigger') && ENV['CAPTURE_PROGRESS_SCENARIO']!='observation'
        $capture_progress_delayed=false
        progress_native_observe
        if $capture_progress_delayed
          File.open('slow-capture-completed.txt','wb') {|f|f.write(@observation_seq.to_s)}
        end
      end
    end
  end
'''


def run(source, root, scenario):
    from rubymarshal.writer import writes
    source, root = source.resolve(), root.resolve()
    if root.exists():
        raise FileExistsError(root)
    originals = (source / 'Game.exe', source / 'System/RGSS300.dll')
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    if before[originals[1]] != recipe.COMMERCIAL_PROFILE['library_sha256']:
        raise ValueError('仅接受登记的 RGSS300 运行库')
    runtime, bridge = root / 'runtime', root / 'bridge'
    (runtime / 'System').mkdir(parents=True)
    (runtime / 'Data').mkdir()
    executable = runtime / 'Game.exe'
    shutil.copy2(originals[0], executable)
    shutil.copy2(originals[1], runtime / 'System/RGSS300.dll')
    harness = HARNESS.replace('  loop do\n', FIXTURE + '  loop do\n')
    (runtime / 'Data/Scripts.rvdata2').write_bytes(writes([[1, b'CaptureProgress', zlib.compress(harness.encode('utf-8'))]]))
    (runtime / 'Game.ini').write_text('[Game]\nRTP=\nLibrary=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\nTitle=GameQA capture progress test\n', encoding='ascii')
    report = dict(ok=False, scenario=scenario, scope='synthetic 64x48 RGSS300; no game data or saves')
    session = uuid.uuid4().hex
    host = DesktopProcess([str(executable)], cwd=runtime, env={
        'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session,
        'GAMEQAKIT_PLUGIN_FILE': str(recipe.BRIDGE), 'GAMEQAKIT_OBSERVATION': '1',
        'CAPTURE_PROGRESS_SCENARIO': scenario, 'HEARTBEAT_SCENARIO': 'capture-progress',
        'TEMP': str(root), 'TMP': str(root)})
    stop = threading.Event()
    monitor = None
    client = None
    ages = []
    try:
        host.__enter__()
        write_connection(bridge, dict(protocol=1, session_id=session, pid=host.pid,
                                     exe_path=str(executable), engine='rpgmaker_vxace', command_format='ini'))
        client = BridgeClient(bridge, pid=host.pid, exe_path=executable)
        host.run()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = client.status()
            if state.get('connected') and state.get('observation_seq', 0):
                break
            time.sleep(.01)
        else:
            raise AssertionError('synthetic_startup_failed:' + str(state))
        assert client.resume(command_id='progress-resume', timeout=3).get('status') == 'done'
        start_seq = client.status()['observation_seq']

        def watch():
            sent_pause = False
            while not stop.wait(.01):
                state = client.status()
                if state.get('connected'):
                    ages.append(time.time() - state['updated_at'])
                elif state.get('error') == '插件心跳过期，停止自动操作':
                    report['stale_observed'] = True
                    client.pause()
                    return
                if scenario in ('pause', 'publish-pause') and not sent_pause and (runtime / 'capture-progress.txt').exists():
                    client.pause()
                    report['pause_sent'] = sent_pause = True
        monitor = threading.Thread(target=watch, name='capture-progress-watch')
        monitor.start()
        (runtime / 'fault.trigger').touch()
        if scenario == 'observation':
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = client.status()
                if report.get('stale_observed'):
                    break
                if (runtime / 'slow-capture-completed.txt').exists() and state.get('observation_seq', 0) > start_seq:
                    report['observation_completed'] = True
                    break
                time.sleep(.01)
            assert report.get('observation_completed'), '慢像素遍历导致观察链断开'
        else:
            if scenario in ('pause', 'publish-pause'):
                try:
                    report['action'] = client.action('wait', frames=1, command_id='progress-wait', timeout=5)
                except RuntimeError as exc:
                    report['action_error'] = str(exc)
                else:
                    raise AssertionError('暂停后仍发布了成功动作回执')
            else:
                report['action'] = client.action('wait', frames=1, command_id='progress-wait', timeout=5)
            receipt = json.loads((bridge / 'results/progress-wait.json').read_text(encoding='utf-8'))
            report['receipt'] = receipt
            if scenario in ('pause', 'publish-pause'):
                assert report.get('pause_sent'), '暂停注入未执行'
                assert receipt['ok'] is False and receipt['error'] == 'manual_pause', '暂停后仍发布了成功动作回执'
                assert report['action_error'] == 'manual_pause'
            else:
                assert report['action'].get('status') == 'done' and receipt['ok'] is True, '慢动作截图导致有效回执丢失'
        assert not report.get('stale_observed'), '有进度的像素遍历耗尽两秒心跳'
        report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        stop.set()
        if monitor:
            monitor.join()
        try:
            if client is not None:
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
        report['source_unchanged'] = all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in before.items())
        report['max_connected_heartbeat_age'] = max(ages, default=None)
        report['ok'] = report['ok'] and all(report.get(k) is True for k in ('release_confirmed', 'target_exited', 'source_unchanged'))
        write_json(root / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--scenario', choices=['observation', 'action', 'pause', 'publish-pause'], required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.runtime, args.work, args.scenario))
