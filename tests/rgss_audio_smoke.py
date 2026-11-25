"""在已有 RGSS 原生 VM 中验证后台四类音频静音；不加载游戏或存档。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import time
import uuid
import wave
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from rubymarshal.writer import writes

HARNESS = r'''
begin
  $audio_calls=[]
  class << Audio
    [:bgm_play,:bgs_play,:me_play,:se_play].each do |name|
      original="probe_native_#{name}".to_sym
      alias_method original,name
      define_method(name) do |*args|
        $audio_calls << [name.to_s,args]
        send(original,*args) if args[1]==0
      end
    end
  end
  directory=ENV.delete('GAMEQAKIT_BRIDGE_DIR')
  load ENV['GAMEQAKIT_PLUGIN_FILE']
  Audio.se_play('tone.wav',83,113)
  manual=$audio_calls.dup
  $audio_calls=[]
  ENV['GAMEQAKIT_BRIDGE_DIR']=directory
  load ENV['GAMEQAKIT_PLUGIN_FILE']
  Audio.bgm_play('tone.wav',81,111,0)
  Audio.bgs_play('tone.wav',82,112,0)
  Audio.me_play('tone.wav',83,113)
  Audio.se_play('tone.wav')
  File.open('audio-result.json','wb') {|f|f.write(GameQaBridge.json({'manual'=>manual,'background'=>$audio_calls}))}
  module SceneManager
    def self.scene; @scene ||= Object.new; end
  end
  sprite=Sprite.new; sprite.bitmap=Bitmap.new(Graphics.width,Graphics.height)
  sprite.bitmap.fill_rect(0,0,Graphics.width,Graphics.height,Color.new(20,70,110))
  loop do
    Input.update
    Graphics.update
  end
rescue Exception => error
  File.open('audio-error.txt','wb') {|f|f.write("#{error.class}: #{error.message}\n#{error.backtrace.join('\n')}")}
end
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args()
    root=args.work.resolve()
    if root.exists():
        raise FileExistsError('work 必须是新目录')
    plugin=Path(__file__).resolve().parent.parent/'plugins/rgss3/GameQaBridge.rb'
    inputs=[args.executable,args.library,plugin]
    digest=lambda:{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    before=digest()
    (root/'Data').mkdir(parents=True)
    (root/'System').mkdir()
    executable=root/'GameQA-Audio-Probe.exe'
    shutil.copy2(args.executable,executable)
    shutil.copy2(args.library,root/'System/RGSS.dll')
    (root/'GameQA-Audio-Probe.ini').write_text('[Game]\nRTP=\nLibrary=System\\RGSS.dll\nScripts=Data\\Scripts.rvdata2\nTitle=GameQA background audio probe\n',encoding='ascii')
    (root/'Data/Scripts.rvdata2').write_bytes(writes([[1,b'AudioProbe',zlib.compress(HARNESS.encode('utf-8'))]]))
    with wave.open(str(root/'tone.wav'),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(22050)
        stream.writeframes(b''.join(struct.pack('<h',8000 if i%50<25 else -8000) for i in range(2205)))
    bridge=root/'bridge'
    session=uuid.uuid4().hex
    host=DesktopProcess([str(executable)],cwd=str(root),env={'GAMEQAKIT_BRIDGE_DIR':str(bridge),'GAMEQAKIT_SESSION_ID':session,'GAMEQAKIT_PLUGIN_FILE':str(plugin),'TEMP':str(root),'TMP':str(root)})
    report={'ok':False,'checks':{},'input_before':before}
    try:
        with host:
            report['pid']=host.pid
            write_connection(bridge,{'protocol':1,'session_id':session,'pid':host.pid,'exe_path':str(executable),'engine':'rpgmaker_vxace','command_format':'ini'})
            host.run()
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if (root/'audio-error.txt').exists():
                    raise RuntimeError((root/'audio-error.txt').read_text(encoding='utf-8'))
                if (root/'audio-result.json').exists():
                    break
                time.sleep(.02)
            else:
                raise TimeoutError('原生音频探针未返回结果')
            result=json.loads((root/'audio-result.json').read_text(encoding='utf-8'))
            report['result']=result
            report['checks']['without_background_bridge_preserves_manual_audio']=result['manual']==[['se_play',['tone.wav',83,113]]]
            report['checks']['background_bgm_bgs_me_se_muted_preserving_pitch_and_position']=result['background']==[
                ['bgm_play',['tone.wav',0,111,0]],['bgs_play',['tone.wav',0,112,0]],
                ['me_play',['tone.wav',0,113]],['se_play',['tone.wav',0]]]
            client=BridgeClient(bridge,pid=host.pid,exe_path=str(executable))
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                state=client.status()
                if state.get('connected') and state.get('capabilities'):
                    break
                time.sleep(.02)
            report['status']=state
            report['checks']['status_confirms_muted']=state.get('audio_muted') is True
            report['checks']['paused_and_released']=state.get('connected') is True and state.get('paused') is True and state.get('held')==[]
    finally:
        import psutil
        report['checks']['target_exited']=not psutil.pid_exists(host.pid)
        report['checks']['inputs_unchanged']=digest()==before
        report['ok']=len(report['checks'])==6 and all(report['checks'].values())
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    assert report['ok'], '后台音频未静音或生命周期核对失败，见 report.json'


if __name__=='__main__':
    main()
