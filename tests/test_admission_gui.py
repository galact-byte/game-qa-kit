"""后台接入界面的确认、阻碍反馈与关闭生命周期。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox
from gameqakit.admission_gui import AdmissionDialog


class AdmissionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pref_patch = mock.patch('gameqakit.admission.preference_locator', return_value=Path(self.tmp.name) / 'location.txt')
        self.pref_patch.start()
        self.addCleanup(self.pref_patch.stop)
        self.manager = mock.Mock()
        self.manager.owned = {}
        self.patch = mock.patch('gameqakit.admission_gui.Manager', return_value=self.manager)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.dialog = AdmissionDialog()
        self.dialog.source.setText('source')
        self.dialog.qa_root.setText(str(Path(self.tmp.name) / 'qa'))
        self.dialog.artifacts.setText('build')
        self.addCleanup(self.dialog.deleteLater)

    def drain(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), '界面操作没有结束')

    def check_ready(self):
        plan = {'destination': 'qa/session', 'fingerprint': 'digest', 'changes': ['仅副本修改']}
        with mock.patch('gameqakit.admission.inspect', return_value={'launch_readiness': 'ready'}), mock.patch('gameqakit.admission.make_plan', return_value=plan):
            self.dialog.check.click()
            self.drain(lambda: not self.dialog.busy)
        return plan

    def test_blockers_show_recovery_without_start(self):
        result = {'launch_readiness': 'blocked', 'blockers': [{'message': '缺少组件', 'recovery': '请选择已有构建目录'}]}
        with mock.patch('gameqakit.admission.inspect', return_value=result):
            self.dialog.check.click()
            self.drain(lambda: not self.dialog.busy)
        self.assertIn('请选择已有构建目录', self.dialog.detail.toPlainText())
        self.assertFalse(self.dialog.start.isEnabled())
        self.manager.prepare.assert_not_called()

    def test_editing_source_invalidates_checked_plan(self):
        self.check_ready()
        self.assertTrue(self.dialog.start.isEnabled())
        self.dialog.source.setText('different-source')
        self.assertFalse(self.dialog.start.isEnabled())

    def test_declining_summary_never_prepares(self):
        self.check_ready()
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            self.dialog.start.click()
        self.manager.prepare.assert_not_called()

    def test_confirm_then_prepare_start_and_close_owned_session(self):
        plan = self.check_ready()
        self.manager.prepare.return_value = {'state': 'preparing'}
        self.manager.start.return_value = {'state': 'starting'}
        self.manager.status.return_value = {'state': 'prepared', 'can_stop': True}
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.dialog.start.click()
            self.drain(lambda: not self.dialog.busy)
        self.manager.prepare.assert_called_once()
        self.assertEqual(self.manager.prepare.call_args.args[0], plan)
        self.dialog._poll()
        self.drain(lambda: not self.dialog.busy)
        self.manager.start.assert_called_once()
        self.assertFalse(self.dialog.source.isEnabled())
        self.dialog.close()
        self.drain(lambda: self.manager.close.call_count == 1 and not self.dialog.busy)

    def test_selected_qa_root_restores_previous_paths(self):
        from gameqakit import admission
        qa = Path(self.tmp.name) / 'saved-qa'
        admission.save_preferences(qa, source=str(Path(self.tmp.name) / 'saved-game'), artifacts='saved-build')
        dialog = AdmissionDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.qa_root.text(), str(qa))
        self.assertEqual(dialog.source.text(), str(Path(self.tmp.name) / 'saved-game'))
        self.assertEqual(dialog.artifacts.text(), 'saved-build')

    def test_connect_existing_is_observation_only_and_never_starts_or_stops_it(self):
        row = {'directory': 'external', 'bridge_directory': 'external/bridge', 'session_id': 'external-session',
               'state': 'ready_paused'}
        with mock.patch('gameqakit.admission.list_sessions', return_value={'sessions': [row], 'errors': [], 'truncated': False}):
            self.dialog.refresh.click()
            self.drain(lambda: not self.dialog.busy)
        with mock.patch.object(self.dialog.binding, 'bind', return_value={'bound': True, 'connected': True, 'paused': True}) as bind:
            self.dialog.connect_existing.click()
            self.drain(lambda: not self.dialog.busy)
        bind.assert_called_once_with('external/bridge', 'external-session', observe_only=True)
        self.assertFalse(self.dialog.stop.isEnabled())
        self.assertIn('外部管理', self.dialog.state.text())
        self.manager.start.assert_not_called()
        self.manager.prepare.assert_not_called()
        with mock.patch.object(self.dialog.binding, 'unbind', return_value={'bound': False}) as unbind:
            self.dialog.disconnect.click()
            self.drain(lambda: not self.dialog.busy)
        unbind.assert_called_once()
        self.manager.stop.assert_not_called()
        self.assertTrue(self.dialog.check.isEnabled())

    def test_failed_disconnect_keeps_binding_and_blocks_new_launch(self):
        self.dialog.connected_external = True
        with mock.patch.object(self.dialog.binding, 'unbind', side_effect=RuntimeError('松键未确认')):
            self.dialog._disconnect()
            self.drain(lambda: not self.dialog.busy)
        self.assertTrue(self.dialog.connected_external)
        self.assertFalse(self.dialog.check.isEnabled())
        self.assertIn('松键未确认', self.dialog.state.text())

    def test_closing_external_connection_waits_for_release_without_stopping_target(self):
        self.dialog.connected_external = True
        with mock.patch.object(self.dialog.binding, 'unbind', side_effect=RuntimeError('尚未确认松键')):
            self.dialog.close()
            self.drain(lambda: not self.dialog.busy)
        self.assertFalse(self.dialog.closed)
        self.assertFalse(self.dialog.closing)
        self.manager.close.assert_not_called()
        self.manager.stop.assert_not_called()
        with mock.patch.object(self.dialog.binding, 'unbind', return_value={'bound': False}) as unbind:
            self.dialog.close()
            self.drain(lambda: self.dialog.closed)
        unbind.assert_called_once()
        self.manager.close.assert_called_once()

    def test_changing_qa_root_invalidates_discovered_sessions(self):
        self.dialog.existing.addItem('old', {'directory': 'old', 'session_id': 'old'})
        self.dialog.qa_root.setText(str(Path(self.tmp.name) / 'other'))
        self.assertEqual(self.dialog.existing.count(), 0)
        self.assertFalse(self.dialog.connect_existing.isEnabled())

    def test_external_observation_displays_current_bridge_image_without_action(self):
        from PIL import Image
        bridge = Path(self.tmp.name) / 'bridge'
        (bridge / 'screenshots').mkdir(parents=True)
        image = bridge / 'screenshots/observation.png'
        Image.new('RGB', (24, 24), 'red').save(image)
        self.dialog._display_external({'bound': True, 'connected': True, 'paused': True, 'held': [],
                                      'protocol': 2, 'observation_seq': 5, 'directory': str(bridge)})
        self.assertFalse(self.dialog.preview.pixmap().isNull())
        self.assertEqual(self.dialog.last_image, (str(image), 5))
        self.assertIn('暂停且已松键', self.dialog.state.text())
        self.manager.start.assert_not_called()

    def test_creator_preview_follows_observation_sequence(self):
        from PIL import Image
        bridge = Path(self.tmp.name) / 'bridge'
        (bridge / 'screenshots').mkdir(parents=True)
        startup = bridge / 'screenshots/startup.png'
        observation = bridge / 'screenshots/observation.png'
        Image.new('RGB', (24, 24), 'red').save(startup)
        Image.new('RGB', (24, 24), 'blue').save(observation)
        state = {'state': 'ready_paused', 'screenshot': str(startup), 'bridge_directory': str(bridge),
                 'control': {'connected': True, 'protocol': 2, 'observation_seq': 8}}
        self.dialog._display(state)
        self.assertEqual(self.dialog.preview.pixmap().toImage().pixelColor(0, 0).name(), '#0000ff')
        self.assertEqual(self.dialog.last_image, (str(observation), 8))

    def test_observation_refresh_does_not_reuse_qt_filename_cache(self):
        from PIL import Image
        image = Path(self.tmp.name) / 'refresh.png'
        Image.new('RGB', (24, 24), 'red').save(image)
        timestamp = image.stat().st_mtime_ns
        self.dialog._show_image(str(image), token=('frame', 1))
        self.assertEqual(self.dialog.preview.pixmap().toImage().pixelColor(0, 0).name(), '#ff0000')
        Image.new('RGB', (24, 24), 'blue').save(image)
        os.utime(image, ns=(timestamp, timestamp))
        self.dialog._show_image(str(image), token=('frame', 2))
        self.assertEqual(self.dialog.preview.pixmap().toImage().pixelColor(0, 0).name(), '#0000ff')
        self.assertEqual(self.dialog.last_image, ('frame', 2))

    def test_lost_external_connection_clears_old_preview(self):
        from PIL import Image
        image = Path(self.tmp.name) / 'image.png'
        Image.new('RGB', (24, 24), 'red').save(image)
        self.dialog._show_image(str(image))
        self.dialog._display_external({'connected': False, 'error': '目标退出'})
        pixmap = self.dialog.preview.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())
        self.assertIsNone(self.dialog.last_image)
        self.assertIn('失效', self.dialog.preview.text())

    def test_creator_status_failure_clears_old_preview(self):
        from PIL import Image
        image = Path(self.tmp.name) / 'image.png'
        Image.new('RGB', (24, 24), 'red').save(image)
        self.dialog._show_image(str(image))
        self.dialog.directory = 'owned-session'
        self.manager.status.side_effect = RuntimeError('目标身份查询失败')
        self.dialog._poll()
        pixmap = self.dialog.preview.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())
        self.assertIsNone(self.dialog.last_image)
        self.assertIn('目标身份查询失败', self.dialog.state.text())

    def test_closing_while_checking_waits_for_worker(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def inspect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'launch_readiness': 'blocked', 'blockers': []}
        with mock.patch('gameqakit.admission.inspect', side_effect=inspect):
            self.dialog.check.click()
            self.drain(entered.is_set)
            self.dialog.close()
            self.manager.close.assert_not_called()
            release.set()
            self.drain(lambda: self.manager.close.call_count == 1 and not self.dialog.busy)


if __name__ == '__main__':
    unittest.main()
