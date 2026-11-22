"""Godot 桥真实运行回归；显式提供已有引擎程序后才运行。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest

from PIL import Image, ImageChops
from gameqakit.control import BridgeClient, write_connection
from gameqakit.sessions import read_json, write_json

BRIDGE = Path(__file__).resolve().parents[1] / 'plugins/godot/GameQaBridge.gd'
RUNTIME = os.environ.get('GAMEQAKIT_TEST_GODOT', '')


def fixture_pack(path, *, black=False, bright_speck=False):
    """自写场景以真实 Input 查询移动方块，不替桥生成动作或回执。"""
    files = {
        '.godot/global_script_class_cache.cfg': b'list=[]\n',
        'project.godot': b'''config_version=5
[application]
config/name="GameQA Godot Fixture"
run/main_scene="res://main.tscn"
config/use_custom_user_dir=true
config/custom_user_dir_name="gameqa-godot-fixture"
[display]
window/size/viewport_width=320
window/size/viewport_height=240
[rendering]
renderer/rendering_method="gl_compatibility"
''',
        'main.tscn': b'''[gd_scene load_steps=2 format=3]
[ext_resource type="Script" path="res://main.gd" id="1"]
[node name="Fixture" type="Node2D"]
script = ExtResource("1")
''',
        'main.gd': ('''extends Node2D
var marker: ColorRect
var clicks = 0
var confirms = 0
func _ready():
    var background = ColorRect.new()
    background.color = Color.BLACK if %s else Color(0.12, 0.16, 0.22)
    background.size = Vector2(320, 240)
    add_child(background)
    marker = ColorRect.new()
    marker.color = Color.BLACK if %s else Color8(255, 128, 32)
    marker.size = Vector2(32, 32)
    marker.position = Vector2(60, 100)
    add_child(marker)
    if not %s:
        var button = Button.new()
        button.text = "Test"
        button.position = Vector2(210, 150)
        button.size = Vector2(80, 40)
        button.pressed.connect(func():
            clicks += 1
            marker.color = Color.RED)
        add_child(button)
    if %s:
        var speck = ColorRect.new()
        speck.color = Color.WHITE
        speck.size = Vector2.ONE
        speck.position = Vector2(2, 2)
        add_child(speck)
func _process(_delta):
    if Input.is_action_just_pressed("ui_accept"):
        confirms += 1
        if FileAccess.file_exists(OS.get_environment("GAMEQA_FIXTURE_STATE") + ".blank"):
            var cover = ColorRect.new()
            cover.color = Color.BLACK
            cover.size = Vector2(320, 240)
            add_child(cover)
            OS.delay_msec(220)
    if Input.is_action_pressed("ui_right"):
        marker.position.x += 4
    if Input.is_action_pressed("ui_left"):
        marker.position.x -= 4
    var fixture_path = OS.get_environment("GAMEQA_FIXTURE_STATE")
    var output = FileAccess.open(fixture_path + ".part", FileAccess.WRITE)
    output.store_string(JSON.stringify({"x": marker.position.x,
        "right_pressed": Input.is_action_pressed("ui_right"),
        "left_pressed": Input.is_action_pressed("ui_left"),
        "mouse_pressed": Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT),
        "clicks": clicks, "confirms": confirms,
        "user_data_dir": OS.get_user_data_dir()}))
    output.close()
    DirAccess.rename_absolute(fixture_path + ".part", fixture_path)
''' % ('true' if black else 'false', 'true' if black else 'false',
       'true' if black else 'false', 'true' if bright_speck else 'false')).encode(),
    }
    index, contents = struct.pack('<I', len(files)), b''
    for name, data in files.items():
        encoded = ('res://' + name).encode() + b'\0'
        encoded += bytes(-len(encoded) % 4)
        index += (struct.pack('<I', len(encoded)) + encoded + struct.pack('<QQ', len(contents), len(data))
                  + hashlib.md5(data).digest() + struct.pack('<I', 0))
        contents += data + bytes(-len(data) % 16)
    base = (96 + len(index) + 15) // 16 * 16
    header = struct.pack('<4s5IQ16I', b'GDPC', 2, 4, 5, 1, 2, base, *([0] * 16))
    path.write_bytes(header + index + bytes(base - 96 - len(index)) + contents)


@unittest.skipUnless(os.name == 'nt' and RUNTIME, '需 Windows 与 GAMEQAKIT_TEST_GODOT 已有引擎程序')
class GodotBridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BRIDGE.is_file(), 'Godot 控制桥尚未实现')
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-godot-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = os.environ.get('GAMEQAKIT_TEST_EVIDENCE')
        if evidence:
            self.addCleanup(lambda: shutil.copytree(self.root, Path(evidence) / self.id().split('.')[-1],
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', '*.pck', 'appdata', 'localappdata', 'tmp')))
        self.exe = self.root / 'fixture.exe'
        shutil.copy2(RUNTIME, self.exe)
        self.directory = self.root / 'bridge'
        self.directory.mkdir()
        for name in ('tmp', 'appdata', 'localappdata'):
            (self.root / name).mkdir()

    def start(self, *, black=False, bright_speck=False, wrong_session=False):
        from gameqakit.desktop import DesktopProcess
        fixture_pack(self.root / 'fixture.pck', black=black, bright_speck=bright_speck)
        self.host = DesktopProcess([str(self.exe), '--main-pack', str(self.root / 'fixture.pck'),
            '--script', str(BRIDGE), '--audio-driver', 'Dummy', '--max-fps', '60',
            '--log-file', str(self.root / 'godot.log')], cwd=self.root, env={
                'GAMEQAKIT_BRIDGE_DIR': str(self.directory), 'GAMEQAKIT_SESSION_ID': 'fixture-session',
                'GAMEQA_FIXTURE_STATE': str(self.root / 'fixture-state.json'),
                'APPDATA': str(self.root / 'appdata'), 'LOCALAPPDATA': str(self.root / 'localappdata'),
                'TEMP': str(self.root / 'tmp'), 'TMP': str(self.root / 'tmp')})
        self.host.__enter__()
        self.addCleanup(self.host.close)
        write_connection(self.directory, {'protocol': 1, 'session_id': 'other-session' if wrong_session else 'fixture-session',
            'pid': self.host.pid, 'exe_path': str(self.exe), 'engine': 'godot'})
        self.client = BridgeClient(self.directory, pid=self.host.pid, exe_path=str(self.exe))
        self.host.run()
        return self.until(lambda: ((self.directory / 'status.json').exists()
            and (self.client.status().get('observation_seq', 0) > 0 or self.client.status().get('error')))
            or not self.host.alive())

    def until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(.02)
        log = (self.root / 'godot.log').read_text(encoding='utf-8', errors='replace') if (self.root / 'godot.log').exists() else ''
        self.fail('Godot 条件未达成：' + log[-3000:])

    def fixture(self):
        # 测试夹具状态不参与桥判定，仅独立核对真实场景读取到的输入。
        try:
            return read_json(self.root / 'fixture-state.json')
        except (OSError, ValueError):
            return {}

    def test_starts_paused_with_real_scene_preview_and_isolated_user_data(self):
        self.start()
        state = self.client.status()
        self.assertTrue(state['connected'])
        self.assertTrue(state['paused'])
        self.assertEqual(state['held'], [])
        self.assertEqual(state['screen'], [320, 240])
        self.assertEqual(self.fixture()['x'], 60)
        self.assertTrue(Path(self.fixture()['user_data_dir']).is_relative_to(self.root))
        with Image.open(self.directory / state['observation_screenshot']) as shot:
            self.assertEqual(shot.getpixel((65, 105))[:3], (255, 128, 32))

    def test_input_moves_actual_scene_and_releases_before_receipt(self):
        self.start()
        self.client.resume(command_id='resume')
        before = Image.open(self.directory / 'screenshots/resume.png').convert('RGB')
        result = self.client.action('move', direction='right', frames=10, command_id='right')
        self.assertEqual(self.fixture()['x'], 100)
        self.assertFalse(self.fixture()['right_pressed'])
        self.assertTrue(result['consumed'])
        after = Image.open(result['screenshot']).convert('RGB')
        self.assertIsNotNone(ImageChops.difference(before, after).getbbox())
        self.assertEqual(after.getpixel((105, 105)), (255, 128, 32))
        self.assertEqual(self.client.status()['held'], [])
        again = self.client.action('move', direction='right', frames=10, command_id='right')
        self.assertEqual(again['screenshot_sha256'], result['screenshot_sha256'])
        self.assertEqual(self.fixture()['x'], 100)

    def test_click_reaches_normal_gui_and_mouse_is_released(self):
        self.start()
        self.client.resume(command_id='resume')
        result = self.client.action('click', x=230, y=170, frames=2, command_id='click')
        self.assertEqual(self.fixture()['clicks'], 1)
        self.assertFalse(self.fixture()['mouse_pressed'])
        self.assertEqual(self.client.status()['held'], [])
        with Image.open(result['screenshot']) as shot:
            self.assertEqual(shot.getpixel((65, 105))[:3], (255, 0, 0))

    def test_confirm_reaches_normal_input_action(self):
        self.start()
        self.client.resume(command_id='resume')
        self.client.action('confirm', frames=2, command_id='confirm')
        self.assertEqual(self.fixture()['confirms'], 1)

    def test_observation_keeps_updating_while_input_is_paused(self):
        self.start()
        before = self.client.status()
        def refreshed():
            state = self.client.status()
            if state['observation_seq'] > before['observation_seq'] and state['frame'] > before['frame']:
                return state
        state = self.until(refreshed)
        self.assertGreater(state['frame'], before['frame'])
        self.assertTrue(state['paused'])
        self.assertEqual(self.fixture()['x'], 60)

    def test_capture_write_failure_stops_active_input(self):
        self.start()
        self.client.resume(command_id='resume')
        self.raw_request('long', {'action': 'move', 'direction': 'right', 'frames': 120})
        self.until(lambda: self.fixture().get('right_pressed'))
        # 只阻断夹具的下一次 PNG 原子写入，复现真实发布失败路径。
        (self.directory / 'screenshots/observation.png.part').mkdir()
        self.until(lambda: self.client.status().get('error') == 'capture_write_failed')
        self.until(lambda: not self.fixture().get('right_pressed'))
        self.assertTrue(self.client.status()['paused'])
        self.assertEqual(self.client.status()['capabilities'], [])
        self.assertEqual(self.client.result('long')['status'], 'failed')

    def test_first_frame_capture_failure_does_not_hide_consumed_input(self):
        self.start()
        self.client.resume(command_id='resume')
        (self.root / 'fixture-state.json.blank').touch()
        self.raw_request('black-after-confirm', {'action': 'confirm', 'frames': 10})
        self.until(lambda: self.client.status().get('error') == 'blank_capture')
        self.assertEqual(self.fixture()['confirms'], 1)
        result = read_json(self.directory / 'results/black-after-confirm.json')
        self.assertFalse(result['ok'])
        self.assertTrue(result['consumed'])
        self.assertEqual(self.client.status()['held'], [])

    def test_target_exit_is_reported_and_does_not_accept_new_input(self):
        self.start()
        self.host.close()
        self.assertFalse(self.client.status()['connected'])
        with self.assertRaisesRegex(RuntimeError, '目标进程已退出'):
            self.client.resume(command_id='after-exit')

    def test_pause_interrupts_active_action_and_target_reads_release(self):
        self.start()
        self.client.resume(command_id='resume')
        self.raw_request('long', {'action': 'move', 'direction': 'right', 'frames': 120})
        self.until(lambda: self.fixture().get('right_pressed'))
        self.client.pause()
        self.until(lambda: self.client.status().get('paused') and not self.fixture().get('right_pressed'))
        self.assertEqual(self.client.result('long')['status'], 'failed')
        self.assertEqual(self.client.status()['held'], [])
        self.assertLess(self.fixture()['x'], 540)

    def raw_request(self, command_id, payload, *, session='fixture-session', pause_id=''):
        state = self.client.status()
        request = {'protocol': 1, 'session_id': session, 'id': command_id, 'payload': payload,
                   'after_frame': state['frame'], 'pause_id': pause_id, 'created_at': time.time()}
        (self.directory / 'requests').mkdir(exist_ok=True)
        write_json(self.directory / 'requests' / (command_id + '.json'), request)
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() + 1.5})
        write_json(self.directory / 'request.json', request)

    def test_atomic_lease_renewal_does_not_pause_live_bridge(self):
        self.start()
        self.client.resume(command_id='resume')
        stop = threading.Event()

        def renew():
            count = 0
            while not stop.is_set():
                write_json(self.directory / 'lease.json', {
                    'session_id': 'fixture-session', 'expires_at': time.time() + 1.5})
                count += 1
            return count

        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(renew)
            try:
                deadline = time.monotonic() + 4
                while True:
                    state = self.client.status()
                    if ((state['connected'] and state['paused']) or writer.done()
                            or time.monotonic() >= deadline):
                        break
                    time.sleep(.015)
            finally:
                stop.set()
            count = writer.result()
        self.assertTrue(state['connected'], state)
        self.assertFalse(state['paused'], f'{count} renewals: {state}')
        self.assertGreater(count, 10)
        result = self.client.action('move', direction='right', frames=10, command_id='right')
        self.assertTrue(result['consumed'])
        self.assertEqual(self.fixture()['x'], 100)

    def test_lease_expiry_releases_active_input_without_replaying_command(self):
        self.start()
        self.client.resume(command_id='resume')
        self.raw_request('long', {'action': 'move', 'direction': 'right', 'frames': 120})
        self.until(lambda: self.fixture().get('right_pressed'))
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() - 1})
        self.until(lambda: self.client.status().get('paused') and not self.fixture().get('right_pressed'))
        self.assertEqual(self.client.result('long')['status'], 'failed')
        self.assertEqual(self.client.status()['error'], 'lease_expired')

    def test_missing_lease_releases_active_input_without_reusing_previous_expiry(self):
        self.start()
        self.client.resume(command_id='resume')
        self.raw_request('long', {'action': 'move', 'direction': 'right', 'frames': 120})
        self.until(lambda: self.fixture().get('right_pressed'))
        lease_path = self.directory / 'lease.json'
        expires_at = read_json(lease_path)['expires_at']

        def remove_lease():
            try:
                lease_path.unlink()
            except PermissionError:
                return False
            return True

        self.until(remove_lease)
        self.until(lambda: self.client.status().get('error') == 'lease_expired')
        state = self.client.status()
        self.assertTrue(state['paused'])
        self.assertLess(state['updated_at'], expires_at)
        self.assertEqual(self.client.result('long')['error'], 'lease_expired')
        self.until(lambda: self.fixture().get('right_pressed') is False)

    def test_stale_pause_generation_cannot_resume(self):
        self.start()
        self.client.pause()
        self.raw_request('stale', {'action': 'resume', 'frames': 1}, pause_id='')
        self.until(lambda: self.client.result('stale')['status'] == 'failed')
        self.assertEqual(self.fixture()['x'], 60)
        self.assertTrue(self.client.status()['paused'])

    def test_invalid_payload_is_rejected_without_input(self):
        self.start()
        self.raw_request('invalid', {'action': 'move', 'direction': 'right', 'frames': 1, 'duration_ms': 10})
        self.until(lambda: self.client.result('invalid')['status'] == 'failed')
        self.assertEqual(self.fixture()['x'], 60)
        self.assertTrue(self.client.status()['paused'])

    def test_black_capture_fails_closed_and_does_not_advertise_control(self):
        self.start(black=True)
        state = self.client.status()
        self.assertTrue(state['paused'])
        self.assertEqual(state['capabilities'], [])
        self.assertTrue(state['error'])
        self.assertEqual(self.fixture()['x'], 60)

    def test_black_capture_with_one_bright_pixel_is_still_rejected(self):
        self.start(black=True, bright_speck=True)
        state = self.client.status()
        self.assertEqual(state['capabilities'], [])
        self.assertEqual(state['error'], 'blank_capture')
        self.assertEqual(self.fixture()['x'], 60)

    def test_wrong_session_never_starts_scene_or_publishes_ready(self):
        self.start(wrong_session=True)
        self.assertFalse(self.host.alive())
        self.assertFalse((self.root / 'fixture-state.json').exists())
        self.assertFalse((self.directory / 'status.json').exists())


if __name__ == '__main__':
    unittest.main()
