"""在独立桌面运行完整 Unity Mono 商业副本，记录来源校验与动作证据。"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.control import BridgeClient, _write_ini
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import write_json
from gameqakit import win32


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def tree_snapshot(root):
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}


def registry_snapshot(company, product):
    import winreg
    result = {}
    key_name = 'Software\\' + company + '\\' + product
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        def visit(name):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | view) as key:
                    count = winreg.QueryInfoKey(key)
                    for i in range(count[1]):
                        label, value, kind = winreg.EnumValue(key, i)
                        result[str(view) + '/' + name + '/' + label] = hashlib.sha256(repr((kind, value)).encode('utf-8')).hexdigest()
                    for i in range(count[0]):
                        visit(name + '\\' + winreg.EnumKey(key, i))
            except FileNotFoundError:
                pass
        visit(key_name)
    return result


def prepare(source, work, loader, backend):
    import UnityPy
    if work.exists() or work.is_relative_to(source):
        raise ValueError('工作目录必须不存在，且不得位于原游戏内')
    managed = list(source.glob('*_Data/Managed'))
    if len(managed) != 1:
        raise ValueError('需要唯一的 Mono 数据目录')
    data = managed[0].parent
    executable = source / (data.name[:-5] + '.exe')
    if not executable.is_file():
        raise FileNotFoundError(executable)
    if any(p.is_symlink() or p.is_junction() for p in source.rglob('*')):
        raise ValueError('原目录包含链接，需要单独核对外部写入边界')
    env = UnityPy.load(str(data / 'globalgamemanagers'))
    settings = next(obj for obj in env.objects if obj.type.name == 'PlayerSettings')
    values = settings.read_typetree()
    original_company, original_product = values['companyName'], values['productName']
    persistent = Path(os.environ['LOCALAPPDATA']).parent / 'LocalLow' / original_company / original_product
    identity = 'GameQaKitCommercial_' + uuid.uuid4().hex
    work.mkdir(parents=True)
    record = {'source': str(source), 'backend': backend, 'original_company': original_company,
              'original_product': original_product, 'original_persistent': str(persistent),
              'original_registry': registry_snapshot(original_company, original_product),
              'original_persistent_files': tree_snapshot(persistent), 'original_files': tree_snapshot(source),
              'identity': identity, 'product': 'CompatibilityProbe', 'unity_version': str(env.file.unity_version)}
    write_json(work / 'before.json', record)
    if (source / 'SaveData').is_dir():
        shutil.copytree(source / 'SaveData', work / 'original-save-backup')
    app = work / 'app'
    def ignore(directory, names):
        if Path(directory) == source:
            return [name for name in names if name in ('BepInEx', 'doorstop_config.ini', 'winhttp.dll')]
        return []
    shutil.copytree(source, app, ignore=ignore)
    shutil.copytree(loader / 'BepInEx/core', app / 'BepInEx/core')
    native_loader = loader / 'winhttp.dll'
    import struct
    def machine(path):
        with path.open('rb') as stream:
            stream.seek(0x3c)
            offset = struct.unpack('<I', stream.read(4))[0]
            stream.seek(offset + 4)
            return struct.unpack('<H', stream.read(2))[0]
    if machine(native_loader) != machine(executable):
        native_loader = source / 'winhttp.dll'
        if not native_loader.is_file() or machine(native_loader) != machine(executable):
            raise ValueError('缺少与游戏位数一致的现有 Doorstop 加载器')
    shutil.copy2(native_loader, app / 'winhttp.dll')
    record['native_loader'] = str(native_loader)
    (app / 'doorstop_config.ini').write_text('[General]\nenabled=true\ntarget_assembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirect_output_log=false\n[UnityMono]\ndebug_enabled=false\n[UnityDoorstop]\nenabled=true\ntargetAssembly=BepInEx\\core\\BepInEx.Preloader.dll\nredirectOutputLog=false\n', encoding='utf-8')
    values.update(companyName=identity, productName='CompatibilityProbe', runInBackground=True,
                  defaultScreenWidth=1024, defaultScreenHeight=768, defaultIsFullScreen=False,
                  fullscreen=False, fullscreenMode=3)
    if 'displayResolutionDialog' in values:
        values['displayResolutionDialog'] = 0
    settings.save_typetree(values)
    (app / data.name / 'globalgamemanagers').write_bytes(env.file.save())
    (app / data.name / 'app.info').write_text(identity + '\nCompatibilityProbe\n', encoding='utf-8')
    command = [sys.executable, str(ROOT / 'plugins/unity/build.py'), '--game-root', str(app),
               '--output', str(app / 'BepInEx/plugins/GameQaKit/GameQaBridge.dll')]
    if backend == 'rewired':
        command.append('--rewired')
    elif backend == 'input_system':
        command.append('--input-system')
    with (work / 'build.log').open('wb') as output:
        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
    record['executable'] = str(app / executable.name)
    write_json(work / 'prepared.json', record)
    return record


def run(prepared, work, steps):
    if work.exists():
        raise ValueError('证据目录必须不存在')
    work.mkdir(parents=True)
    meta = json.loads((prepared / 'prepared.json').read_text(encoding='utf-8'))
    executable = Path(meta['executable'])
    bridge = work / 'bridge'
    bridge.mkdir()
    temporary = work / 'tmp'
    temporary.mkdir()
    session = uuid.uuid4().hex
    stop = threading.Event()
    sampler = None
    report = {'ok': False, 'prepared': str(prepared), 'actions': [], 'foreground_samples': []}
    environment = {'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session,
                   'GAMEQAKIT_INPUT_BACKEND': meta['backend'], 'TEMP': str(temporary), 'TMP': str(temporary)}
    try:
        with DesktopProcess([str(executable), '-screen-fullscreen', '0', '-screen-width', '1024', '-screen-height', '768',
                             '-logFile', str(work / 'player.log')], cwd=executable.parent, env=environment) as child:
            connection = {'protocol': 1, 'session_id': session, 'pid': child.pid, 'exe_path': str(executable),
                          'engine': meta.get('engine', 'unity_mono'), 'command_format': 'ini'}
            write_json(bridge / 'connection.json', connection)
            _write_ini(bridge / 'connection.ini', connection)
            client = BridgeClient(bridge, pid=child.pid, exe_path=executable)
            def sample():
                while not stop.wait(.05):
                    report['foreground_samples'].append(int(win32.user32.GetForegroundWindow() or 0))
            sampler = threading.Thread(target=sample)
            sampler.start()
            child.run()
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                status = client.status()
                if status.get('connected') and status.get('input_ready'):
                    break
                if not child.alive():
                    raise RuntimeError('游戏启动后提前退出')
                time.sleep(.1)
            else:
                raise RuntimeError('输入后端未就绪: ' + str(status))
            assert (status.get('company'), status.get('product')) == (meta['identity'], meta['product']), '产品标识隔离失败'
            expected = Path(os.environ['LOCALAPPDATA']).parent / 'LocalLow' / meta['identity'] / meta['product']
            assert Path(status['persistent_data_path']).resolve() == expected.resolve(), '持久化目录隔离失败'
            report.update(initial_status=status, persistent_data_path=status['persistent_data_path'], pid=child.pid)
            for index, step in enumerate([{'action': 'resume'}, *steps]):
                action = step['action']
                options = {key: value for key, value in step.items() if key != 'action'}
                start = time.perf_counter()
                identifier = 'step-' + str(index)
                if action == 'observe':
                    seconds = float(options['seconds'])
                    if not 0 < seconds <= 30:
                        raise ValueError('被动观察时长必须在 0 到 30 秒之间')
                    client.pause()
                    pause_deadline = time.monotonic() + 2
                    while time.monotonic() < pause_deadline:
                        observation_status = client.status()
                        if observation_status.get('connected') and observation_status.get('paused') and observation_status.get('held') == []:
                            break
                        time.sleep(.05)
                    else:
                        raise AssertionError('被动观察前未释放输入')
                    time.sleep(seconds)
                    if not child.alive():
                        raise RuntimeError('被动观察时游戏退出')
                    result = client.resume(command_id=identifier)
                else:
                    result = client.resume(command_id=identifier) if action == 'resume' else client.action(action, command_id=identifier, **options)
                report['actions'].append({**result, 'requested_step': step, 'elapsed_ms': round((time.perf_counter() - start) * 1000, 1)})
                write_json(work / 'actions.json', report['actions'])
                assert result.get('ok') and result.get('consumed'), '动作未成功消费: ' + str(result)
                print(identifier, result.get('state', {}).get('scene'), result.get('state', {}).get('ui_text'), flush=True)
            client.pause()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = client.status()
                if status.get('connected') and status.get('paused') and status.get('held') == []:
                    break
                time.sleep(.05)
            else:
                raise AssertionError('暂停未释放按键')
            report['pause_status'] = status
            report['windows'] = child.windows()
            assert not {item['hwnd'] for item in report['windows']}.intersection(report['foreground_samples']), '测试窗口进入前台'
            report['ok'] = True
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(2)
        original_files = tree_snapshot(Path(meta['source']))
        report['changed_original_files'] = [name for name, value in meta['original_files'].items()
                                            if original_files.get(name) != value]
        report['new_original_files'] = sorted(set(original_files) - set(meta['original_files']))
        report['original_persistent_unchanged'] = tree_snapshot(Path(meta['original_persistent'])) == meta['original_persistent_files']
        report['original_registry_unchanged'] = registry_snapshot(meta['original_company'], meta['original_product']) == meta['original_registry']
        write_json(work / 'result.json', report)
        if report['changed_original_files'] or report['new_original_files'] or not report['original_persistent_unchanged'] or not report['original_registry_unchanged']:
            raise AssertionError('原数据前后校验不一致，详见 result.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--loader', type=Path)
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--backend', choices=['legacy', 'rewired', 'input_system'], default='legacy')
    parser.add_argument('--steps', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if args.prepare:
        if not args.loader:
            parser.error('--prepare 需要 --loader')
        prepare(args.prepare.resolve(), args.work.resolve(), args.loader.resolve(), args.backend)
    else:
        if not args.prepared or not args.steps:
            parser.error('运行需要 --prepared 与 --steps')
        run(args.prepared.resolve(), args.work.resolve(), json.loads(args.steps.read_text(encoding='utf-8')))
