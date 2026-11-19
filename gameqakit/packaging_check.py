"""便携产物自检：不启动游戏，不发送输入，不依赖外部 Python。"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def check(report):
    from . import profile
    report.parent.mkdir(parents=True, exist_ok=True)
    result = {'frozen': bool(getattr(sys, 'frozen', False))}
    try:
        if not result['frozen']:
            raise RuntimeError('产物自检必须由打包后的 EXE 执行')
        base = Path(sys.executable).resolve().parent
        result['profiles'] = str(profile.profiles_dir())
        assert profile.profiles_dir() == base / 'profiles'
        result['default_data'] = str(profile.GameProfile(name='portable-check', proc='Game').data_path)
        assert Path(result['default_data']) == base / 'data/portable-check'
        modules = ('gameqakit.engines', 'gameqakit.game_worker', 'plugins.unity.prepare',
                   'plugins.rgss3.prepare', 'plugins.rpgmaker_js.prepare', 'plugins.native_input.session',
                   'plugins.godot.session', 'plugins.renpy.session', 'plugins.tyrano.session',
                   'plugins.emuera.session', 'plugins.kirikiri.session', 'plugins.kirikiri.capture_worker',
                   'plugins.hsp.session', 'plugins.flash.session', 'plugins.easyrpg.session',
                   'plugins.gamemaker.session')
        for name in modules:
            importlib.import_module(name)
        result['module_imports'] = list(modules)
        root = Path(__file__).resolve().parents[1]
        for name in ('plugins/native_input/samples/srpgstudio-x86-fixed.json',
                     'plugins/rgss3/GameQaBridge.rb', 'plugins/rpgmaker_js/GameQaBridge.js',
                     'plugins/godot/GameQaBridge.gd', 'plugins/renpy/GameQaBridge.rpy',
                     'plugins/renpy/samples/gameqa-renpy-fixture.rpy', 'plugins/tyrano/Fixture.js',
                     'plugins/tyrano/MainBridge.js', 'plugins/tyrano/Observer.js',
                     'plugins/tyrano/samples/gameqa-tyrano-fixture.ks', 'plugins/emuera/Launcher.cs',
                     'plugins/emuera/samples/gameqa-emuera-fixture.erb', 'plugins/kirikiri/GameQaBridge.tjs',
                     'plugins/kirikiri/samples/gameqa-kirikiri-fixture.tjs', 'plugins/hsp/samples/gameqa-hsp-fixture.ax',
                     'plugins/flash/samples/gameqa-flash-fixture.swf',
                     'plugins/easyrpg/samples/rm2k-sha256.json', 'plugins/easyrpg/gameqa_input.cpp',
                     'plugins/gamemaker/Bridge.h', 'plugins/gamemaker/BridgeState.h',
                     'plugins/gamemaker/source-manifest.json', 'plugins/gamemaker/runtime-manifest.json'):
            assert (root / name).is_file(), name
        from .admission import file_digest
        from plugins.flash.prepare import ARCHIVE, ARCHIVE_SHA, SCRIPT, SCRIPT_SHA
        for path, digest in ((ARCHIVE, ARCHIVE_SHA), (ARCHIVE.parent / 'samples' / SCRIPT, SCRIPT_SHA)):
            assert file_digest(path) == digest, path
        from plugins.easyrpg.prepare import source_digests
        source_digests(root / 'plugins/easyrpg/samples/rm2k')
        from plugins.gamemaker.prepare import source_digests as gamemaker_source_digests
        gamemaker_source_digests(root / 'plugins/gamemaker/samples')
        marker = report.with_name('worker-self-test.json')
        subprocess.run([sys.executable, '--worker-self-test', str(marker)], check=True, timeout=20,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert json.loads(marker.read_text(encoding='utf-8'))['frozen']
        result['frozen_subprocess'] = True
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
        mcp = subprocess.run([str(base / 'GameQA-MCP.exe')],
            input=''.join(json.dumps(request) + '\n' for request in requests),
            capture_output=True, text=True, encoding='utf-8', timeout=20, check=True)
        responses = [json.loads(line) for line in mcp.stdout.splitlines() if line.strip()]
        assert len(responses) == 2 and all('result' in entry for entry in responses), responses
        result['mcp_tool_count'] = len(responses[1]['result']['tools'])
        os.environ['QT_QPA_PLATFORM'] = 'windows'
        from PyQt6.QtWidgets import QApplication
        from .gui import App
        app = QApplication.instance() or QApplication([])
        window = App()
        window.show()
        app.processEvents()
        image = window.grab()
        assert not image.isNull()
        assert image.save(str(report.with_name('portable-gui.png')))
        result['qt_window_size'] = [image.width(), image.height()]
        window.close()
        app.processEvents()
        result['ok'] = True
    except BaseException as exc:
        result.update(ok=False, error=repr(exc))
        raise
    finally:
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
