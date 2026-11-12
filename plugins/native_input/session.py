"""固定 SRPG Studio 构建的产品生命周期，复用准备器与正式宿主。"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

from PIL import Image
from gameqakit.admission import file_digest, revalidate_plan
from gameqakit.control import BridgeClient
from gameqakit.process_job import OwnedProcess
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from .prepare import prepare, manifest, no_links


def validate_ready(root, state):
    if not (state.get('connected') and state.get('paused') is True and state.get('held') == []
            and state.get('observation_seq', 0) > 0 and state.get('session_id')):
        raise RuntimeError('首图或真实暂停松键尚未确认')
    image = Path(root) / 'bridge/screenshots/startup.png'
    no_links(image)
    with Image.open(image) as source:
        width, height = source.size
        if source.format != 'PNG' or width <= 2 or height <= 2 or width * height > 16777216:
            raise RuntimeError('startup_image_invalid')
        pixels = source.convert('RGB')
        # 与正式桥一致：忽略一像素边缘，拒绝 >=99.9% 的纯黑／纯白采样。
        count, black, white = 0, 0, 0
        total = (width - 2) * (height - 2)
        for index in range(0, total, max(1, total // 20000)):
            value = pixels.getpixel((index % (width - 2) + 1, index // (width - 2) + 1))
            black += value == (0, 0, 0)
            white += value == (255, 255, 255)
            count += 1
        if max(black, white) / count >= .999:
            raise RuntimeError('startup_image_invalid')
    return {'screenshot': str(image), 'screenshot_sha256': file_digest(image)}


def reclaim_runtime(runtime, baseline, *, safe_to_remove):
    runtime = Path(runtime)
    after = manifest(runtime)
    retained = not safe_to_remove or after != baseline
    audit = runtime.parent / 'runtime-after.json'
    write_json(audit, after)
    changed = sorted(name for name in set(after) | set(baseline or {})
                     if after.get(name) != (baseline or {}).get(name))
    result = {'runtime_retained': retained, 'runtime_files': len(after),
              'runtime_manifest': str(audit), 'runtime_changed': changed[:20],
              'reason': '未知变化或退出／原数据核查未通过，保留副本' if retained else '仅未变化的可再生副本已回收'}
    if not retained:
        shutil.rmtree(runtime)
    return result


class NativeSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan['destination'])
        self.host = self.bridge = None
        self.baseline = None

    def prepare(self):
        revalidate_plan(self.plan)
        record = prepare(self.plan['source'], self.root)
        write_json(self.root / 'approved-plan.json', self.plan)
        # 复制摘要针对完整资源；不把复制中变化的来源当已确认内容。
        before = json.loads((self.root / 'source-before.json').read_text(encoding='utf-8'))
        self.baseline = manifest(self.root / 'runtime')
        expected = {name: digest for name, digest in before.items()
                    if name in record['copied'] or any(name.startswith(item + '/') for item in record['copied'])}
        config = (Path(self.plan['source']) / 'game.ini').read_bytes()
        import hashlib
        expected['game.ini'] = hashlib.sha256(
            config.replace(b'NonActiveAnimationPlay=1', b'NonActiveAnimationPlay=0')
                  .replace(b'NonActiveMusicPlay=0', b'NonActiveMusicPlay=1')).hexdigest()
        if self.baseline != expected:
            raise RuntimeError('copy_changed：复制期间源资源发生变化，请保留现场后重新检查')
        if any(before[name] != digest for name, digest in self.plan['source_digests'].items()):
            raise RuntimeError('source_changed_during_prepare')
        write_json(self.root / 'runtime-before.json', self.baseline)
        return {'state': 'prepared', 'runtime': str(self.root / 'runtime')}

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError('启动前需要本创建者完成准备，且不能重复启动')
        artifacts = Path(self.plan['artifacts'])
        for name, digest in self.plan['artifact_digests'].items():
            no_links(artifacts / name)
            if file_digest(artifacts / name) != digest:
                raise RuntimeError('artifact_changed：本机产物已变化，请重新检查')
        if manifest(self.root / 'runtime') != self.baseline:
            raise RuntimeError('runtime_changed_before_start')
        self.host = OwnedProcess([str(artifacts / 'native-host.exe'), str(self.root),
                                  str(artifacts / 'native-input.dll'), self.plan['artifact_digests']['native-input.dll']],
                                 cwd=str(self.root))
        self.host.__enter__()
        self.host.run()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError('接入已取消')
            if not self.host.alive():
                raise RuntimeError('host_exited：加载器已退出，请查看本会话证据')
            if (self.root / 'bridge/fatal.json').exists():
                raise RuntimeError('bridge_failed：' + str(read_json(self.root / 'bridge/fatal.json')))
            if (self.root / 'bridge/connection.json').is_file():
                meta = read_json(self.root / 'bridge/connection.json')
                self.bridge = BridgeClient(self.root / 'bridge', pid=meta['pid'], exe_path=str(self.root / 'runtime/game.exe'))
                state = self.bridge.status()
                if state.get('connected') and state.get('observation_seq', 0) > 0:
                    image = validate_ready(self.root, state)
                    write_json(self.root / 'profile.json', {'name': self.root.name, 'proc': 'game',
                               'exe_path': str(self.root / 'runtime/game.exe'), 'data_dir': str(self.root / 'data')})
                    return {**state, **image, 'state': 'ready_paused', 'bridge_directory': str(self.root / 'bridge'),
                            'host_pid': self.host.pid}
            time.sleep(.02)
        raise TimeoutError('startup_timeout：原生宿主未在既定启动期限内就绪')

    def stop(self):
        release, target_pid, release_error = None, None, None
        try:
            if self.bridge:
                state = self.bridge.status()
                target_pid = state.get('pid')
                if state.get('connected'):
                    self.bridge.pause()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        state = self.bridge.status()
                        if state.get('connected') and state.get('paused') is True and state.get('held') == []:
                            release = True
                            break
                        time.sleep(.01)
                    if release is None:
                        release = False
        except Exception as exc:
            release = False
            release_error = str(exc)
        finally:
            if self.host:
                (self.root / 'stop').touch()
                deadline = time.monotonic() + 3
                while self.host.alive() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.host.close()
        if (self.root / 'process.json').exists():
            target_pid = read_json(self.root / 'process.json').get('pid', target_pid)
        target_exited = target_pid is None or not _pid_path(target_pid)
        source = Path(self.plan['source'])
        source_ok = outside_ok = False
        if (self.root / 'source-before.json').exists():
            current = manifest(source)
            write_json(self.root / 'source-after.json', current)
            source_ok = current == json.loads((self.root / 'source-before.json').read_text(encoding='utf-8'))
            outside = json.loads((self.root / 'external-before.json').read_text(encoding='utf-8'))
            current = manifest(outside['path'])
            write_json(self.root / 'external-after.json', current)
            outside_ok = current == outside['files']
        runtime = self.root / 'runtime'
        result = {'target_exited': target_exited, 'release_confirmed': release,
                  'release_error': release_error,
                  'source_unchanged': source_ok, 'external_unchanged': outside_ok}
        if runtime.exists():
            result.update(reclaim_runtime(runtime, self.baseline,
                          safe_to_remove=target_exited and source_ok and outside_ok and self.baseline is not None))
        result['state'] = 'stopped' if target_exited and source_ok and outside_ok else 'cleanup_failed'
        write_json(self.root / 'cleanup.json', result)
        return result
