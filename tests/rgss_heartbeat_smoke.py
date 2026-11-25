"""现有 RGSS300 运行库的确定性故障验证；仅生成独立程序，不带游戏数据或存档。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json
from plugins.rgss3 import prepare as recipe
from plugins.rgss3.session import RgssSession

HARNESS = r'''
begin
  module SceneManager
    def self.scene; @scene; end
    def self.scene=(value); @scene=value; end
  end
  class Scene_Title; end
  SceneManager.scene=Scene_Title.new
  Graphics.resize_screen(64,48)
  sprite=Sprite.new; sprite.bitmap=Bitmap.new(64,48)
  sprite.bitmap.fill_rect(0,0,64,48,Color.new(30,110,180))
  Graphics.brightness=255
  load ENV['GAMEQAKIT_PLUGIN_FILE']
  class << File
    alias_method :heartbeat_native_rename, :rename
    def rename(source,target)
      if target.to_s.end_with?('status.json') && File.exist?('fault.trigger') &&
          ENV['HEARTBEAT_SCENARIO']=='brief-rejections'
        @denied ||= 0
        if @denied<3
          @denied+=1
          raise Errno::EACCES, 'deterministic publication fault'
        end
      end
      heartbeat_native_rename(source,target)
    end
  end
  module GameQaBridge
    class << self
      alias_method :heartbeat_native_observe, :observe
      def observe
        if File.exist?('fault.trigger')
          if ENV['HEARTBEAT_SCENARIO']=='brief-rejections'
            sleep(0.75)
          elsif ENV['HEARTBEAT_SCENARIO']=='slow-capture' && !@delayed_once
            @delayed_once=true
            sleep(2.2)
          end
        end
        heartbeat_native_observe
      end
    end
  end
  loop do
    Input.update
    if Input.press?(:RIGHT)
      consumed=(defined?(consumed) && consumed || 0)+1
      File.open('input-consumption.txt','wb') {|f|f.write(consumed.to_s)}
    end
    Graphics.update
  end
rescue Exception => error
  File.open('harness-error.txt','wb') {|f|f.write("#{error.class}: #{error.message}\n#{error.backtrace.join('\n')}")}
end
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--scenario', choices=['brief-rejections', 'slow-capture', 'locked-status'], required=True)
    parser.add_argument('--active-input', action='store_true')
    args = parser.parse_args()
    if args.active_input and args.scenario != 'brief-rejections':
        parser.error('--active-input 仅用于 brief-rejections')
    if args.work.exists():
        raise FileExistsError(args.work)
    source = args.runtime.resolve()
    expected = recipe.COMMERCIAL_PROFILE['library_sha256']
    if hashlib.sha256((source / 'System/RGSS300.dll').read_bytes()).hexdigest() != expected:
        raise ValueError('仅接受现有登记 RGSS300 运行库')
    from rubymarshal.writer import writes
    root = args.work.resolve()
    runtime = root / 'runtime'
    (runtime / 'System').mkdir(parents=True)
    (runtime / 'Data').mkdir()
    executable = runtime / 'Game.exe'
    shutil.copy2(source / 'Game.exe', executable)
    shutil.copy2(source / 'System/RGSS300.dll', runtime / 'System/RGSS300.dll')
    (runtime / 'Game.ini').write_text('[Game]\nRTP=\nLibrary=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\nTitle=GameQA heartbeat fault test\n', encoding='ascii')
    (runtime / 'Data/Scripts.rvdata2').write_bytes(writes([[1, b'HeartbeatFaultProbe', zlib.compress(HARNESS.encode('utf-8'))]]))
    plan = {'destination': str(root), 'source': str(source), 'artifacts': str(source / 'System'),
            'profile_id': recipe.COMMERCIAL_PROFILE['profile_id']}
    session = RgssSession(plan)
    session.baseline = recipe.manifest(runtime)
    source_before = recipe.manifest(source)
    write_json(root / 'source-before.json', source_before)
    write_json(root / 'runtime-source-before.json', recipe.artifact_digests(source / 'System', commercial=True))
    bridge = root / 'bridge'
    identifier = uuid.uuid4().hex
    report = {'ok': False, 'scenario': args.scenario, 'samples': [], 'scope': 'synthetic RGSS300; no game data or saves'}
    host = DesktopProcess([str(executable)], cwd=str(runtime), env={
        'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': identifier,
        'GAMEQAKIT_PLUGIN_FILE': str(recipe.BRIDGE), 'GAMEQAKIT_OBSERVATION': '1',
        'HEARTBEAT_SCENARIO': args.scenario, 'TEMP': str(root), 'TMP': str(root)})
    locked = None
    try:
        host.__enter__()
        session.host = host
        write_connection(bridge, {'protocol': 1, 'session_id': identifier, 'pid': host.pid,
                         'exe_path': str(executable), 'engine': 'rpgmaker_vxace', 'command_format': 'ini'})
        client = session.bridge = BridgeClient(bridge, pid=host.pid, exe_path=executable)
        host.run()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = client.status()
            if state.get('connected') and state.get('observation_seq', 0) > 0:
                break
            time.sleep(.01)
        else:
            raise RuntimeError('synthetic_startup_failed:' + str(state))
        client.resume(command_id='before-fault', timeout=3)
        if args.scenario == 'locked-status':
            locked = (bridge / 'status.json').open('rb')
        (runtime / 'fault.trigger').touch()
        if args.active_input:
            report['action'] = client.action('move', direction='right', frames=1,
                                            command_id='during-fault', timeout=4)
            report['input_consumptions'] = int((runtime / 'input-consumption.txt').read_text(encoding='ascii'))
            if report['input_consumptions'] != 1 or not report['action'].get('consumed'):
                raise AssertionError('发布重试改变了单帧输入消费')
        started = time.monotonic()
        deadline = started + (3.5 if args.scenario == 'brief-rejections' else 4)
        stale = False
        while time.monotonic() < deadline:
            if locked and time.monotonic() - started >= 2.2:
                locked.close()
                locked = None
            state = client.status()
            report['samples'].append({'elapsed': time.monotonic() - started, 'time': time.time(), 'state': state})
            if state.get('error') == '插件心跳过期，停止自动操作':
                stale = True
                if args.scenario != 'brief-rejections':
                    try:
                        client.action('wait', frames=1, command_id='must-not-consume', timeout=.1)
                    except RuntimeError as exc:
                        report['expired_action_error'] = str(exc)
                    else:
                        raise AssertionError('过期状态接受了新动作')
                    report['expired_action_not_published'] = 'must-not-consume' not in (bridge / 'request.ini').read_text(encoding='utf-8')
                    if locked:
                        # 同一线程随后执行 stop；释放注入锁不替插件发布状态。
                        locked.close()
                        locked = None
                    break
            if (runtime / 'harness-error.txt').exists():
                raise RuntimeError((runtime / 'harness-error.txt').read_text(encoding='utf-8'))
            time.sleep(.01)
        report['stale_observed'] = stale
        cleanup = session.stop()
        report['cleanup'] = cleanup
        session.host = None
        report['ok'] = (cleanup['state'] == 'stopped' and cleanup['release_confirmed'] is True
                        and (not stale if args.scenario == 'brief-rejections' else
                             stale and report.get('expired_action_not_published') is True))
    finally:
        if locked:
            locked.close()
        if session.host:
            report['cleanup'] = session.stop()
        report['source_unchanged'] = source_before == recipe.manifest(source)
        report['ok'] = report['ok'] and report['source_unchanged']
        write_json(root / 'report.json', report)
    print(json.dumps({key: report[key] for key in ('ok', 'scenario', 'stale_observed', 'cleanup', 'source_unchanged')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
