"""路线记录通过真实文件桥校验动作、证据和停止边界。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image
from gameqakit.game_session import Binding
from gameqakit.sessions import write_json
from gameqakit.route_report import RouteRun, latest_report


class FileBridge:
    def __init__(self, root):
        self.root = root / 'bridge'
        self.meta = dict(protocol=1, session_id='route-session', pid=os.getpid(),
                         exe_path=sys.executable, engine='rpgmaker_mv')
        self.state = dict(self.meta, frame=10, paused=True, held=[], screen=[320, 240],
                          scene='Scene_Title', capabilities=['wait', 'move', 'confirm'])
        self.stop = threading.Event()
        self.skip_action = False
        self.actions = []
        self.thread = threading.Thread(target=self.run)
        write_json(self.root / 'connection.json', self.meta)
        self.publish()
        self.thread.start()

    def publish(self):
        write_json(self.root / 'status.json', dict(self.state, updated_at=time.time()))

    def run(self):
        seen, pause_id = set(), ''
        while not self.stop.wait(.005):
            try:
                pause = self.root / 'pause.json'
                if pause.exists():
                    current = json.loads(pause.read_text('utf-8'))['id']
                    if current != pause_id:
                        self.state.update(paused=True, held=[])
                        pause_id = current
                request = self.root / 'request.json'
                if request.exists():
                    value = json.loads(request.read_text('utf-8'))
                    key = value['id']
                    action = value['payload']['action']
                    if key not in seen and not (self.skip_action and action != 'resume'):
                        seen.add(key)
                        self.actions.append(action)
                        self.state.update(frame=self.state['frame'] + 2, paused=False, held=[], scene='Scene_Map')
                        path = self.root / 'screenshots' / (key + '.png')
                        path.parent.mkdir(exist_ok=True)
                        Image.new('RGB', (320, 240), (30, 90, self.state['frame'] % 255)).save(path)
                        self.publish()
                        write_json(self.root / 'results' / (key + '.json'), dict(
                            self.meta, id=key, ok=True, consumed=True, frame=self.state['frame'],
                            capture_frame=self.state['frame'], screenshot='screenshots/' + key + '.png',
                            scene=self.state['scene']))
                self.publish()
            except (OSError, ValueError, KeyError):
                continue

    def close(self):
        self.stop.set()
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError('文件桥夹具未退出')


class RouteReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugin = FileBridge(self.root)
        self.addCleanup(self.plugin.close)
        self.binding = Binding()
        self.binding.bind(self.plugin.root, 'route-session')
        self.addCleanup(self.binding.close)

    def begin(self, goal='从检查点查看地图'):
        self.route = RouteRun(self.binding, goal)
        return self.route

    def test_step_records_named_action_verified_image_and_pause(self):
        route = self.begin()
        step = route.step('move-1', '向右移动', 'move', direction='right', frames=3)
        self.assertEqual(step['state'], 'done')
        self.assertEqual(step['label'], '向右移动')
        self.assertEqual(step['scene'], 'Scene_Map')
        self.assertTrue(step['pause_confirmed'])
        image = Path(route.status()['report_directory']) / step['image']
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), step['screenshot_sha256'])
        self.assertEqual(self.plugin.actions, ['resume', 'move'])
        self.assertTrue(self.binding.client.status()['paused'])
        self.assertEqual(self.binding.client.status()['held'], [])

    def test_same_step_is_idempotent_but_changed_payload_is_rejected(self):
        route = self.begin()
        first = route.step('one', '等待画面', 'wait', frames=1)
        self.assertEqual(first, route.step('one', '等待画面', 'wait', frames=1))
        self.assertEqual(self.plugin.actions, ['resume', 'wait'])
        with self.assertRaisesRegex(ValueError, '不同|重复'):
            route.step('one', '等待画面', 'wait', frames=2)
        self.assertEqual(self.plugin.actions, ['resume', 'wait'])

    def test_unknown_action_result_is_preserved_and_blocks_later_input(self):
        route = self.begin()
        self.plugin.skip_action = True
        step = route.step('unknown', '等待未返回的动作', 'wait', frames=1, timeout=.05)
        self.assertEqual(step['state'], 'unknown')
        self.assertIn('结果未知', step['error'])
        self.assertTrue(step['command_id'])
        before = (self.plugin.root / 'request.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, '停止|阻断'):
            route.step('later', '不应执行', 'wait', frames=1)
        self.assertEqual((self.plugin.root / 'request.json').read_bytes(), before)
        with self.assertRaisesRegex(ValueError, '通过|未完成'):
            route.finish('passed', '不能把未知动作当成通过')

    def test_changed_binding_is_rejected_without_resuming_new_target(self):
        route = self.begin()
        self.binding.unbind()
        self.plugin.meta['session_id'] = 'replacement'
        self.plugin.state['session_id'] = 'replacement'
        write_json(self.plugin.root / 'connection.json', self.plugin.meta)
        self.plugin.publish()
        self.binding.bind(self.plugin.root, 'replacement')
        with self.assertRaisesRegex(RuntimeError, '目标|会话'):
            route.step('one', '拒绝错发', 'wait', frames=1)
        self.assertEqual(self.plugin.actions, [])

    def test_observer_cannot_begin_a_controlling_route(self):
        self.binding.unbind()
        self.binding.bind(self.plugin.root, 'route-session', observe_only=True)
        with self.assertRaisesRegex(RuntimeError, '控制权|只能查询'):
            self.begin()
        self.assertFalse((self.root / 'route-reports').exists())

    def test_invalid_text_never_sends_input_or_creates_report(self):
        for text in ('', '  ', 123, 'a' * 501):
            with self.subTest(text=str(text)[:30]), self.assertRaises(ValueError):
                self.begin(text)
        self.assertFalse((self.root / 'route-reports').exists())
        self.assertEqual(self.plugin.actions, [])

    def test_finished_report_is_portable_escaped_and_does_not_invent_goal_success(self):
        route = self.begin('检查 <script>alert(1)</script>')
        route.step('one', '等待 <img onerror=alert(1)>', 'wait', frames=1)
        report = route.finish('unverified', '动作完成，尚未核对玩法目标')
        self.assertEqual(report['outcome'], 'unverified')
        self.assertEqual(report['state'], 'finished')
        self.assertEqual(report['cleanup']['state'], 'unverified')
        html = Path(report['report_html']).read_text('utf-8')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img onerror', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('尚未核对玩法目标', html)
        self.assertIn('images/', html)
        self.assertNotIn('src="' + str(self.root), html)
        self.assertIn('未确认回收', html)
        self.assertEqual(latest_report(self.root), Path(report['report_html']))
        self.binding.unbind()
        self.assertTrue(Path(report['report_html']).is_file())
        self.assertTrue((Path(report['report_directory']) / report['steps'][0]['image']).is_file())

    def test_pass_requires_completed_steps_and_explicit_review(self):
        route = self.begin()
        with self.assertRaisesRegex(ValueError, '步骤|通过'):
            route.finish('passed', '尚无动作')
        route.step('one', '确认画面', 'wait', frames=1)
        with self.assertRaises(ValueError):
            route.finish('passed', '')
        result = route.finish('passed', '已看过最后截图，处于地图场景')
        self.assertEqual(result['outcome'], 'passed')
        self.assertIn('AI 复核', Path(result['report_html']).read_text('utf-8'))
        with self.assertRaisesRegex(RuntimeError, '结束'):
            route.step('two', '不能继续', 'wait', frames=1)

    def test_checkpoint_report_distinguishes_import_from_actual_load(self):
        write_json(self.root / 'checkpoint-before.json', {
            'directory': 'checkpoint-source', 'files': {'Save01.rvdata2': 'a' * 64}})
        route = self.begin()
        result = route.status()
        self.assertEqual(result['checkpoint']['files'], {'Save01.rvdata2': 'a' * 64})
        html = Path(result['report_html']).read_text('utf-8')
        self.assertIn('导入不等于已在游戏中读档', html)
        self.assertNotIn('Save09', html)

    def test_image_changed_after_client_validation_is_not_reported_as_done(self):
        route = self.begin()
        execute = self.binding.execute
        def corrupt(method, options, **kwargs):
            result = execute(method, options, **kwargs)
            if method == 'action':
                Path(result['screenshot']).write_bytes(b'changed')
            return result
        with mock.patch.object(self.binding, 'execute', side_effect=corrupt):
            step = route.step('one', '检测证据变化', 'wait', frames=1)
        self.assertEqual(step['state'], 'failed')
        self.assertIn('摘要', step['error'])
        self.assertNotIn('image', step)

    def test_report_write_failure_blocks_all_following_input(self):
        route = self.begin()
        with mock.patch('gameqakit.route_report.write_text', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                route.step('one', '写入失败前不能发动作', 'wait', frames=1)
        self.assertEqual(self.plugin.actions, [])
        self.assertEqual(route.status()['state'], 'blocked')
        with self.assertRaisesRegex(RuntimeError, '停止|阻断'):
            route.step('two', '不应执行', 'wait', frames=1)

    def test_pause_failure_keeps_image_but_does_not_claim_step_success(self):
        route = self.begin()
        with mock.patch.object(route, '_pause', side_effect=RuntimeError('尚未确认暂停松键')):
            step = route.step('one', '观察后未确认暂停', 'wait', frames=1)
        self.assertEqual(step['state'], 'unknown')
        self.assertFalse(step['pause_confirmed'])
        self.assertTrue((route.directory / step['image']).is_file())
        self.assertEqual(route.status()['state'], 'blocked')

    def test_cleanup_uses_actual_managed_session_fields(self):
        route = self.begin()
        result = route.finish(cleanup=dict(state='stopped', target_exited=True,
                             release_confirmed=True, source_unchanged=True,
                             runtime_source_unchanged=True, checkpoint_unchanged=True,
                             worker_alive=False))
        self.assertTrue(result['cleanup']['cleanup_verified'])
        self.assertIn('<dd>已确认回收</dd>', Path(result['report_html']).read_text('utf-8'))

    def test_cleanup_never_trusts_a_flag_over_missing_or_failed_exit_evidence(self):
        for patch in ({'target_exited': False}, {'target_exited': None},
                      {'worker_alive': True}, {'source_unchanged': False},
                      {'release_confirmed': False}, {'state': 'cleanup_failed'}):
            with self.subTest(patch=patch):
                route = self.begin()
                state = dict(state='stopped', target_exited=True, worker_alive=False,
                             release_confirmed=True, cleanup_verified=True)
                state.update(patch)
                result = route.finish(cleanup=state)
                self.assertFalse(result['cleanup']['cleanup_verified'])
                self.assertIn('未确认回收', Path(result['report_html']).read_text('utf-8'))

    def test_reports_do_not_follow_links_outside_session(self):
        self.assertIsNone(latest_report(self.root))
        route = self.begin()
        self.assertEqual(latest_report(self.root), Path(route.status()['report_html']))
        with mock.patch('pathlib.Path.resolve', return_value=self.root.parent / 'outside.html'):
            self.assertIsNone(latest_report(self.root))


if __name__ == '__main__':
    unittest.main()
