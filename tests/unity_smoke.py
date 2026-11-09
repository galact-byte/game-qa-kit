"""从本地 Unity 2020 Mono 运行库创建空白场景验证；不运行原商业游戏脚本。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit.control import BridgeClient, _write_ini
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json
from gameqakit import win32

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('unity_build', ROOT / 'plugins/unity/build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source: Path, work: Path, identity: str, *, input_system=False, loader=None):
    import UnityPy
    managed = list(source.glob('*_Data/Managed'))
    if len(managed) != 1:
        raise ValueError('需要唯一 Unity Mono 数据目录')
    original_data = managed[0].parent
    original_exe = source / (original_data.name[:-5] + '.exe')
    if not original_exe.is_file():
        raise FileNotFoundError(original_exe)
    app = work / 'app'
    app.mkdir()
    data = app / 'GameQaUnityProbe_Data'
    data.mkdir()
    hashes = {}

    def copy_file(src, dst):
        hashes[str(src)] = digest(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    executable = app / 'GameQaUnityProbe.exe'
    copy_file(original_exe, executable)
    loader = loader or source
    for name in ['UnityPlayer.dll', 'dstorage.dll', 'dstoragecore.dll']:
        if name != 'UnityPlayer.dll' and not (source / name).is_file():
            continue
        copy_file(source / name, app / name)
    copy_file(loader / 'winhttp.dll', app / 'winhttp.dll')
    for folder in ['MonoBleedingEdge', 'BepInEx/core']:
        origin = source if folder == 'MonoBleedingEdge' else loader
        for file in (origin / folder).rglob('*'):
            if file.is_file():
                copy_file(file, app / folder / file.relative_to(origin / folder))
    (app / 'doorstop_config.ini').write_text(
        '[General]\nenabled=true\ntarget_assembly=BepInEx\\core\\BepInEx.Preloader.dll\n'
        'redirect_output_log=false\n[UnityMono]\ndebug_enabled=false\n', encoding='utf-8')
    for file in managed[0].glob('*.dll'):
        if file.name.startswith(('Unity', 'System', 'Mono.', 'I18N', 'netstandard', 'mscorlib')):
            copy_file(file, data / 'Managed' / file.name)
    for name in ['globalgamemanagers.assets', 'globalgamemanagers.assets.resS']:
        if (original_data / name).exists():
            copy_file(original_data / name, data / name)
    for file in (original_data / 'Resources').glob('*'):
        if file.is_file():
            copy_file(file, data / 'Resources' / file.name)
    globals_path = original_data / 'globalgamemanagers'
    hashes[str(globals_path)] = digest(globals_path)
    env = UnityPy.load(str(globals_path))
    for obj in list(env.objects):
        if obj.type.name not in ('PlayerSettings', 'GraphicsSettings', 'QualitySettings', 'UnityConnectSettings', 'MonoManager', 'BuildSettings'):
            continue
        if input_system and str(env.file.unity_version).startswith('6000.') and obj.type.name in ('PlayerSettings', 'GraphicsSettings', 'QualitySettings', 'UnityConnectSettings'):
            if obj.type.name == 'PlayerSettings':
                import struct
                raw = obj.get_raw_data()
                original_identity = (original_data / 'app.info').read_text(encoding='utf-8').splitlines()
                replacements = []
                for index, value in enumerate(original_identity):
                    old = value.encode('utf-8')
                    new = ('QA' + identity.rsplit('_', 1)[-1])[:len(old)].encode('ascii')
                    marker = struct.pack('<I', len(old)) + old
                    if len(new) != len(old) or raw.count(marker) != 1 or len(old) < 6:
                        raise ValueError('无法可靠隔离 Unity 6 产品标识')
                    raw = raw.replace(marker, struct.pack('<I', len(new)) + new)
                    replacements.append(new.decode('ascii'))
                obj.set_raw_data(raw)
                (work / 'fixture-identity.json').write_text(json.dumps(replacements), encoding='utf-8')
            continue
        tree = obj.read_typetree()
        if obj.type.name == 'PlayerSettings':
            tree['companyName'] = identity
            tree['productName'] = 'UnityControlProbe'
            tree['defaultScreenWidth'] = 640
            tree['defaultScreenHeight'] = 480
            tree['runInBackground'] = True
            tree['fullscreen'] = False
            tree['fullscreenMode'] = 3
            tree['resizableWindow'] = False
            tree['m_ShowUnitySplashScreen'] = False
            tree['m_ShowUnitySplashLogo'] = False
        elif obj.type.name == 'BuildSettings':
            tree['scenes'] = ['Assets/Scenes/GameQaProbe.unity']
        elif obj.type.name == 'MonoManager':
            tree['m_ScriptHashes'] = []
            tree['m_RuntimeClassHashes'] = []
            tree['m_Scripts'] = []
        elif obj.type.name == 'GraphicsSettings':
            tree['m_CustomRenderPipeline'] = {'m_FileID': 0, 'm_PathID': 0}
        elif obj.type.name == 'QualitySettings':
            for quality in tree['m_QualitySettings']:
                quality['customRenderPipeline'] = {'m_FileID': 0, 'm_PathID': 0}
        elif obj.type.name == 'UnityConnectSettings':
            tree['m_Enabled'] = False
            for key in ['UnityAnalyticsSettings', 'UnityAdsSettings', 'CrashReportingSettings', 'PerformanceReportingSettings', 'UnityPurchasingSettings']:
                for flag in list(tree.get(key, {})):
                    if isinstance(tree[key][flag], bool):
                        tree[key][flag] = False
        obj.save_typetree(tree)
    (data / 'globalgamemanagers').write_bytes(env.file.save())
    assets = UnityPy.load(str(data / 'globalgamemanagers.assets'))
    for key, obj in list(assets.file.objects.items()):
        if obj.type.name in ('MonoBehaviour', 'MonoScript'):
            del assets.file.objects[key]
    (data / 'globalgamemanagers.assets').write_bytes(assets.file.save())
    level = original_data / 'level0'
    hashes[str(level)] = digest(level)
    scene = UnityPy.load(str(level))
    scene.file.objects.clear()
    (data / 'level0').write_bytes(scene.file.save())
    manifest = json.loads((original_data / 'ScriptingAssemblies.json').read_text(encoding='utf-8'))
    entries = [(name, kind) for name, kind in zip(manifest['names'], manifest['types'])
               if (data / 'Managed' / name).exists() and (not name.startswith('Unity.') or input_system and name == 'Unity.InputSystem.dll')]
    entries.append(('Assembly-CSharp.dll', 16))
    (data / 'ScriptingAssemblies.json').write_text(json.dumps({'names': [name for name, _ in entries],
                                                           'types': [kind for _, kind in entries]}), encoding='utf-8')
    (data / 'RuntimeInitializeOnLoads.json').write_text('{"root":[]}', encoding='utf-8')
    (data / 'app.info').write_text(identity + '\nUnityControlProbe\n', encoding='utf-8')
    (data / 'boot.config').write_text('wait-for-native-debugger=0\nhdr-display-enabled=0\n', encoding='utf-8')
    fixture = data / 'Managed/Assembly-CSharp.dll'
    refs = build.references(app)
    fixture_source = ROOT / 'tests/unity/ProbePlayer.cs'
    if input_system:
        fixture_source = work / 'InputSystemProbePlayer.cs'
        fixture_source.write_text('#define INPUT_SYSTEM\n' + (ROOT / 'tests/unity/ProbePlayer.cs').read_text(encoding='utf-8'), encoding='utf-8')
        refs += [data / 'Managed/Unity.InputSystem.dll', data / 'Managed/netstandard.dll']
    build.compile_sources(fixture, [fixture_source], refs)
    plugins = app / 'BepInEx/plugins'
    plugins.mkdir()
    build.compile_sources(plugins / 'ProbeHarness.dll', [ROOT / 'tests/unity/ProbeHarness.cs'], refs + [fixture])
    build.compile_sources(plugins / 'GameQaBridge.dll', sorted((ROOT / 'plugins/unity').glob('*.cs')), refs)
    if input_system:
        build.compile_sources(plugins / 'GameQaInputSystem.dll', [ROOT / 'plugins/unity/backends/InputSystemBackend.cs'], refs + [plugins / 'GameQaBridge.dll'])
    return app, executable, hashes


def actor(result):
    return next(item['position'] for item in result['state']['root_transforms'] if item['name'] == 'InputControlledPlayer')


def run(source: Path, work: Path, *, input_system=False, loader=None):
    if work.exists():
        raise ValueError('work 必须不存在，禁止覆盖证据')
    work.mkdir(parents=True)
    session = uuid.uuid4().hex
    identity = 'GameQaKitProbe_' + session
    report = {'ok': False, 'identity': identity, 'source': str(source), 'actions': [], 'foreground_samples': []}
    stop = threading.Event()
    sampler = None
    original_hashes = {}
    try:
        app, executable, original_hashes = prepare(source, work, identity, input_system=input_system, loader=loader)
        bridge = work / 'bridge'
        bridge.mkdir()
        temporary = work / 'tmp'
        temporary.mkdir()
        argv = [str(executable), '-force-d3d11', '-screen-fullscreen', '0', '-screen-width', '640', '-screen-height', '480',
                '-logFile', str(work / 'player.log')]
        env = {'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session,
               'GAMEQAKIT_INPUT_BACKEND': 'input_system' if input_system else 'legacy',
               'TEMP': str(temporary), 'TMP': str(temporary)}
        with DesktopProcess(argv, cwd=app, env=env) as child:
            report.update(pid=child.pid, desktop=child.desktop_name)
            meta = {'protocol': 1, 'session_id': session, 'pid': child.pid, 'exe_path': str(executable),
                    'engine': 'unity_mono', 'command_format': 'ini'}
            write_json(bridge / 'connection.json', meta)
            _write_ini(bridge / 'connection.ini', meta)
            client = BridgeClient(bridge, pid=child.pid, exe_path=executable)

            def sample():
                while not stop.wait(.05):
                    report['foreground_samples'].append(int(win32.user32.GetForegroundWindow() or 0))
            sampler = threading.Thread(target=sample)
            sampler.start()
            child.run()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                status = client.status()
                if status.get('connected') and status.get('input_ready'):
                    break
                if not child.alive():
                    raise RuntimeError('Unity 进程提前退出')
                time.sleep(.1)
            else:
                raise RuntimeError('Unity 输入后端未就绪: ' + str(status))
            report['initial_status'] = status
            for identifier, action, options in [
                ('resume', 'resume', {}), ('right', 'move', {'direction': 'right', 'frames': 6}),
                ('up', 'move', {'direction': 'up', 'frames': 4}), ('menu', 'confirm', {}),
                ('cancel', 'cancel', {}), ('click', 'click', {'x': 100, 'y': 424}),
            ]:
                start = time.perf_counter()
                result = client.resume(command_id=identifier) if action == 'resume' else client.action(action, command_id=identifier, **options)
                report['actions'].append({**result, 'elapsed_ms': round((time.perf_counter()-start)*1000, 1)})
                current = client.status()
                assert current.get('frame', -1) >= result['frame'] and not current.get('paused'), '回执发布后心跳仍是旧暂停状态'
                print(identifier, result['frame'], actor(result), flush=True)
            rows = {row['id']: row for row in report['actions']}
            if input_system:
                assert 'FOREIGN DEVICE ISOLATED' in '\n'.join(rows['click']['state']['ui_text']), '额外输入设备未被隔离'
                fixture_identity = json.loads((work / 'fixture-identity.json').read_text(encoding='utf-8'))
                assert [status.get('company'), status.get('product')] == fixture_identity, '运行时产品标识与隔离记录不一致'
            assert actor(rows['right'])[0] > actor(rows['resume'])[0] + .5, '向右正常输入未产生位移'
            assert actor(rows['up'])[1] > actor(rows['right'])[1] + .3, '向上正常输入未产生位移'
            assert any('MENU OPEN' in text for text in rows['menu']['state']['ui_text']), '确认未打开菜单'
            assert all('MENU OPEN' not in text for text in rows['cancel']['state']['ui_text']), '取消未关闭菜单'
            assert rows['click']['screenshot_sha256'] != rows['cancel']['screenshot_sha256'], 'UI 点击未改变画面'
            assert 'CLICKS=2' in '\n'.join(rows['click']['state']['ui_text']), '点击未触发标准 Button.onClick'
            client.pause()
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                status = client.status()
                if status.get('connected') and status.get('paused') and status.get('held') == []:
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('暂停未释放按键')
            report['pause_status'] = status
            report['resumed_after_pause'] = client.resume(command_id='resume-after-pause')
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(client.action, 'move', direction='right', frames=120, command_id='preempted')
                deadline = time.monotonic()+2
                while time.monotonic()<deadline and not client.status().get('held'):
                    time.sleep(.02)
                assert client.status().get('held'), '长动作未开始，无法证明运行中抢占'
                client.pause()
                try:
                    pending.result(timeout=3)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('运行中暂停未中断长动作')
            failure = json.loads((bridge / 'results/preempted.json').read_text(encoding='utf-8'))
            assert not failure['ok'] and failure['error']=='manual_pause'
            report['priority_pause'] = failure
            client.resume(command_id='resume-for-lease')
            _write_ini(bridge / 'lease.ini', {'session_id': session, 'expires_at': 0})
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                status = client.status()
                if status.get('connected') and status.get('paused') and status.get('held') == [] and status.get('error')=='lease_expired':
                    break
                time.sleep(.02)
            else:
                raise AssertionError('租约到期未暂停释放')
            report['lease_expired_status'] = status
            report['windows'] = child.windows()
            own = {item['hwnd'] for item in report['windows']}
            assert not own.intersection(report['foreground_samples']), '测试窗口抢占了前台'
            report['ok'] = True
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(2)
        report['changed_original_files'] = [path for path, before in original_hashes.items() if digest(Path(path)) != before]
        report['original_file_count'] = len(original_hashes)
        write_json(work / 'result.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--input-system', action='store_true')
    parser.add_argument('--loader', type=Path, help='现有 BepInEx 5 运行库目录，仅用于空白测试壳')
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.work.resolve(), input_system=args.input_system, loader=args.loader)
