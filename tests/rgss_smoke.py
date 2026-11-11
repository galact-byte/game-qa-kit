"""RGSS3 原生 VM 集成探针，不冒充完整游戏兼容测试；只创建隔离测试程序。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json

HARNESS = r'''
begin
  class ProbePlayer
    attr_accessor :x, :y
    def initialize; @x=8; @y=6; @direction=2; end
    def direction; @direction; end
    def moving?; false; end
    def move(direction)
      @direction=direction
      @x+=1 if direction==6; @x-=1 if direction==4
      @y+=1 if direction==2; @y-=1 if direction==8
    end
  end
  class ProbeMap
    def map_id; 1; end
  end
  module SceneManager
    def self.scene; $scene; end
  end
  class Scene_Title
    def update
      if Input.trigger?(:C)
        Graphics.frame_count=0
        $scene=Scene_Map.new
      end
    end
    def label; 'TITLE - normal Input.trigger?(:C)'; end
  end
  class Scene_Map
    def update
      $game_player.move(Input.dir4) if Input.dir4!=0
      $scene=Scene_Menu.new if Input.trigger?(:B)
    end
    def label; "MAP - player #{$game_player.x},#{$game_player.y}"; end
  end
  class Scene_Menu
    def update; $scene=Scene_Map.new if Input.trigger?(:B); end
    def label; 'MENU - normal Input.trigger?(:B)'; end
  end
  $game_player=ProbePlayer.new; $game_map=ProbeMap.new; $scene=Scene_Title.new
  load ENV['GAMEQAKIT_PLUGIN_FILE']
  Graphics.resize_screen(320,240)
  sprite=Sprite.new; sprite.bitmap=Bitmap.new(320,240)
  loop do
    Input.update
    $scene.update
    sprite.bitmap.fill_rect(0,0,320,240,Color.new(20,70,110))
    sprite.bitmap.fill_rect($game_player.x*12,$game_player.y*12,12,12,Color.new(240,200,40))
    sprite.bitmap.draw_text(5,10,310,40,$scene.label)
    Graphics.update
  end
rescue Exception => error
  File.open('harness-error.txt','wb') {|f|f.write("#{error.class}: #{error.message}\n#{error.backtrace.join('\n')}")}
end
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--full-project', action='store_true', help='使用 SDK 空白工程的真实脚本和数据库，需已有 RTP')
    args = parser.parse_args()
    plugin = Path(__file__).resolve().parent.parent / 'plugins/rgss3/GameQaBridge.rb'
    if not plugin.is_file():
        raise RuntimeError('缺少 RGSS3 原生控制插件')
    from rubymarshal.writer import writes  # 仅此开发探针使用已有可选库，不是产品运行依赖。
    if args.work.exists():
        raise ValueError('work 必须是新目录')
    root = args.work.resolve()
    (root / 'Data').mkdir(parents=True)
    (root / 'System').mkdir()
    executable = root / 'GameQA-RGSS-Probe.exe'
    shutil.copy2(args.sdk / 'Game.exe', executable)
    shutil.copy2(args.library, root / 'System/RGSS301.dll')
    (root / 'Game.ini').write_text('[Game]\nRTP=' + ('RPGVXAce' if args.full_project else '') + '\nLibrary=System\\RGSS301.dll\nScripts=Data\\Scripts.rvdata2\nTitle=GameQA RGSS3 native integration probe\n', encoding='ascii')
    shutil.copy2(root / 'Game.ini', root / 'GameQA-RGSS-Probe.ini')
    if args.full_project:
        from io import BytesIO
        from rubymarshal.reader import Reader
        from rubymarshal.classes import RubyString
        class BinaryReader(Reader):
            @staticmethod
            def _get_encoding(_attributes):
                return 'latin1'
        def raw(value):
            if isinstance(value, RubyString):
                value = value.text
            return value.encode('latin1') if isinstance(value, str) else value
        shutil.copytree(args.sdk / 'Data', root / 'Data', dirs_exist_ok=True)
        for folder in ['Graphics', 'Audio', 'Fonts']:
            if (args.sdk / folder).is_dir():
                shutil.copytree(args.sdk / folder, root / folder)
        stream = BytesIO((args.sdk / 'Data/Scripts.rvdata2').read_bytes())
        if stream.read(2) != b'\x04\x08':
            raise ValueError('不支持的 Ruby Marshal 版本')
        scripts = [[row[0], raw(row[1]), raw(row[2])] for row in BinaryReader(stream).read()]
        index = next(i for i, row in enumerate(scripts) if row[1] == b'Main')
        scripts.insert(index, [999999, b'GameQaBridge', zlib.compress(b"load ENV['GAMEQAKIT_PLUGIN_FILE']")])
        (root / 'Data/Scripts.rvdata2').write_bytes(writes(scripts))
    else:
        (root / 'Data/Scripts.rvdata2').write_bytes(writes([[1, b'NativeIntegrationProbe', zlib.compress(HARNESS.encode())]]))
    bridge = root / 'bridge'
    session = uuid.uuid4().hex
    report = {'scope': 'VX Ace SDK blank project' if args.full_project else 'RGSS3 native harness, not full game compatibility', 'ok': False, 'actions': []}
    process = DesktopProcess([str(executable)], cwd=root, env={
        'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session,
        'GAMEQAKIT_PLUGIN_FILE': str(plugin), 'TEMP': str(root), 'TMP': str(root)})
    try:
        with process as child:
            report['pid'] = child.pid
            report['desktop'] = child.desktop_name
            write_connection(bridge, {'protocol': 1, 'session_id': session, 'pid': child.pid,
                'exe_path': str(executable), 'engine': 'rpgmaker_vxace', 'command_format': 'ini'})
            client = BridgeClient(bridge, pid=child.pid, exe_path=executable)
            child.run()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if (root / 'harness-error.txt').exists():
                    raise RuntimeError((root / 'harness-error.txt').read_text(errors='replace'))
                if (bridge / 'plugin-error.json').exists():
                    raise RuntimeError((bridge / 'plugin-error.json').read_text(encoding='utf-8'))
                state = client.status()
                if state.get('connected') and state.get('capabilities'):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('原生插件未就绪：' + str(state))
            if set(state.get('buttons', [])) != {'up', 'down', 'left', 'right', 'ok', 'cancel', 'shift', 'pageup', 'pagedown'}:
                raise AssertionError('RGSS3 按钮声明与既有映射不一致')
            report['buttons'] = state['buttons']
            for identifier, action, options in [('resume', 'resume', {}), ('start', 'confirm', {}),
                    ('right', 'move', {'direction': 'right'}), ('right-again', 'move', {'direction': 'right'}),
                    ('menu', 'cancel', {}), ('back', 'cancel', {})]:
                before = time.perf_counter()
                result = (client.resume(command_id=identifier, timeout=5) if action == 'resume' else
                          client.action(action, command_id=identifier, timeout=5, **options))
                report['actions'].append({**result, 'elapsed_ms': round((time.perf_counter()-before)*1000, 2)})
                if identifier == 'resume':
                    before_request = (bridge / 'request.ini').read_bytes()
                    try:
                        client.action('release', button='square', command_id='unsupported-square', timeout=.2)
                    except RuntimeError as error:
                        if '未声明此按钮' not in str(error):
                            raise
                    else:
                        raise AssertionError('RGSS3 不得接受 PS1 方块键')
                    if (bridge / 'request.ini').read_bytes() != before_request:
                        raise AssertionError('未支持的按钮被写入请求通道')
                    report['optional_square_rejected_before_queue'] = True
                print(identifier, result['scene'], result['frame'], result.get('player'), flush=True)
            by_id = {row['id']: row for row in report['actions']}
            expected = {'start': 'Scene_Map', 'menu': 'Scene_Menu', 'back': 'Scene_Map'}
            for identifier, scene in expected.items():
                if by_id[identifier]['scene'] != scene:
                    raise RuntimeError('原生输入没有经过预期的测试场景逻辑')
            start_x = by_id['start']['player']['x']
            if args.full_project:
                if (by_id['start']['player'].get('passable', {}).get('right') is not False
                        or any(by_id[key]['player']['x'] != start_x or by_id[key]['player']['direction'] != 6
                               for key in ['right', 'right-again'])):
                    raise RuntimeError('SDK 默认水面夹具的阻挡/朝向行为不符，不能当作可移动地图验收')
                report['movement_fixture'] = 'SDK 默认水面阻挡位移；朝向正常响应。位置移动另由原生测试场景验证。'
            elif by_id['right']['player']['x'] != start_x + 1 or by_id['right-again']['player']['x'] != start_x + 2:
                raise RuntimeError('重复移动没有产生两次真实状态变化')
            try:
                client.action('move', direction='right', frames=2)
            except ValueError:
                report['unsupported_hold_rejected'] = True
            else:
                raise RuntimeError('实验插件不得声称支持未验证的长按重复语义')
            # Publish a malformed request directly to verify the plugin boundary too.
            from gameqakit.control import _write_ini
            for identifier, extra in [('v2-time', {'protocol': 2}),
                                      ('mixed-time', {'payload.duration_ms': 100})]:
                before_state = client.status()
                raw = {'protocol': 1, 'session_id': session, 'id': identifier,
                       'payload.action': 'move', 'payload.direction': 'right', 'payload.frames': 1,
                       'pause_id': client._pause_id(), 'after_frame': before_state['frame'], **extra}
                _write_ini(bridge / 'lease.ini', {'session_id': session, 'expires_at': time.time() + 1.5})
                _write_ini(bridge / 'request.ini', raw)
                deadline = time.monotonic() + .5
                while time.monotonic() < deadline:
                    if (bridge / 'results' / (identifier + '.json')).exists():
                        break
                    time.sleep(.02)
                bad = bridge / 'results' / (identifier + '.json')
                if bad.exists() and json.loads(bad.read_text(encoding='utf-8')).get('ok'):
                    raise AssertionError('v1 Ruby bridge accepted incompatible time request')
                after_state = client.status()
                if after_state.get('held') or after_state.get('player', {}).get('x') != before_state.get('player', {}).get('x'):
                    raise AssertionError('incompatible time request produced input')
                report[identifier + '_rejected_without_input'] = True
            client.pause()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                state = client.status()
                if state.get('paused') and not state.get('held'):
                    break
                time.sleep(.02)
            else:
                raise RuntimeError('未确认人工接管释放按键')
            report['pause'] = state
            client.resume(command_id='resume-for-expiry', timeout=5)
            stopped_at = time.monotonic()
            deadline = stopped_at + 4
            while time.monotonic() < deadline:
                expired = client.status()
                if expired.get('paused') and not expired.get('held') and expired.get('error') == 'lease_expired':
                    break
                time.sleep(.02)
            else:
                report['expiry_observation'] = {'host_time': time.time(), 'state': expired}
                raise RuntimeError('主程序停止续约后，原生插件未暂停或未释放按键')
            report['lease_expiry'] = {**expired, 'observed_elapsed_ms': round((time.monotonic()-stopped_at)*1000, 2)}
            client.resume(command_id='resume-for-interrupt', timeout=5)
            interrupted = []
            def long_action():
                try:
                    client.action('wait', frames=120, command_id='interrupt-wait', timeout=5)
                except RuntimeError as error:
                    interrupted.append(str(error))
            worker = threading.Thread(target=long_action)
            worker.start()
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if client.status().get('command_id') == 'interrupt-wait':
                        break
                    time.sleep(.01)
                else:
                    raise RuntimeError('待中断动作未进入原生线程')
                client.pause()
            finally:
                worker.join(6)
            if worker.is_alive() or not interrupted or 'manual_pause' not in interrupted[0]:
                raise RuntimeError('人工接管未中断在途动作')
            report['inflight_pause'] = interrupted
            report['windows'] = child.windows()
            report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        write_json(root / 'result.json', report)
        print('Report:', root / 'result.json', flush=True)


if __name__ == '__main__':
    main()
