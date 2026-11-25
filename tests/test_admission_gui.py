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

    def checkpoint_fixture(self):
        from tests.test_rgss_admission import RgssAdmissionTests
        RgssAdmissionTests.setUp(self)
        # 非规范写法用于覆盖同一目录规范化后的计划与确认展示。
        self.seed = self.root / 'source' / '..' / 'checkpoint'
        self.seed.mkdir()
        (self.seed / 'Save01.rvdata2').write_bytes(b'branch point')
        self.dialog.source.setText(str(self.source))
        self.dialog.artifacts.setText(str(self.artifacts))
        self.dialog.checkpoint.setText(str(self.seed))
        return self.recipe.manifest(self.source), self.recipe.manifest(self.seed)

    def test_selected_checkpoint_reaches_real_plan_and_review_without_copy(self):
        source_before, seed_before = self.checkpoint_fixture()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.assertTrue(self.dialog.start.isEnabled())
        plan = self.dialog.plan
        self.assertEqual(plan['checkpoint']['directory'], str(self.seed.resolve()))
        self.assertEqual(set(plan['checkpoint']['files']), {'Save01.rvdata2'})
        self.assertIn(str(self.seed.resolve()), self.dialog.detail.toPlainText())
        self.assertIn('Save01.rvdata2', self.dialog.detail.toPlainText())
        self.assertFalse(Path(plan['destination']).exists())
        self.assertEqual(self.recipe.manifest(self.source), source_before)
        self.assertEqual(self.recipe.manifest(self.seed), seed_before)
        self.manager.prepare.assert_not_called()

    def test_confirmation_identifies_checkpoint_and_imported_slots(self):
        self.checkpoint_fixture()
        (self.seed / 'Save07.rvdata2').write_bytes(b'last test slot')
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as question:
            self.dialog.start.click()
        summary = question.call_args.args[2]
        self.assertIn(str(self.seed.resolve()), summary)
        self.assertIn('Save01.rvdata2', summary)
        self.assertIn('Save07.rvdata2', summary)
        self.manager.prepare.assert_not_called()

    def test_confirmed_checkpoint_plan_is_forwarded_to_managed_preparation(self):
        self.checkpoint_fixture()
        self.dialog.timer.stop()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        plan = self.dialog.plan
        self.manager.prepare.return_value = {'state': 'preparing'}
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.dialog.start.click()
            self.drain(lambda: not self.dialog.busy)
        self.manager.prepare.assert_called_once_with(plan, request_id=self.dialog.request + '-prepare', confirm=True)
        self.assertIn('checkpoint', self.manager.prepare.call_args.args[0])
        self.manager.start.assert_not_called()

    def test_checkpoint_changed_after_check_is_rejected_before_copy(self):
        from gameqakit import admission
        self.checkpoint_fixture()
        self.dialog.timer.stop()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        destination = Path(self.dialog.plan['destination'])
        self.manager.prepare.side_effect = lambda plan, **options: admission.revalidate_plan(plan)
        (self.seed / 'Save01.rvdata2').write_bytes(b'changed since inspection')
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.dialog.start.click()
            self.drain(lambda: not self.dialog.busy)
        self.assertIn('操作失败', self.dialog.state.text())
        self.assertFalse(destination.exists())
        self.manager.start.assert_not_called()

    def test_clearing_checkpoint_invalidates_plan_and_rechecks_without_import(self):
        self.checkpoint_fixture()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.assertIn('checkpoint', self.dialog.plan)
        self.dialog.checkpoint.clear()
        self.assertFalse(self.dialog.start.isEnabled())
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.assertTrue(self.dialog.start.isEnabled())
        self.assertNotIn('checkpoint', self.dialog.plan)
        self.assertNotIn(str(self.seed.resolve()), self.dialog.detail.toPlainText())

    def test_changed_checkpoint_path_requires_new_check(self):
        self.checkpoint_fixture()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.dialog.checkpoint.setText(str(self.root / 'different-checkpoint'))
        self.assertIsNone(self.dialog.plan)
        self.assertFalse(self.dialog.start.isEnabled())
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.assertIsNone(self.dialog.plan)
        self.assertFalse(self.dialog.start.isEnabled())
        self.assertIn('专用测试存档目录', self.dialog.detail.toPlainText())
        self.manager.prepare.assert_not_called()

    def test_manual_slot_in_checkpoint_is_blocked_by_real_validator(self):
        self.checkpoint_fixture()
        (self.seed / 'Save09.rvdata2').write_bytes(b'manual slot fixture')
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        self.assertIsNone(self.dialog.plan)
        self.assertFalse(self.dialog.start.isEnabled())
        self.assertIn('Save07.rvdata2', self.dialog.detail.toPlainText())
        self.manager.prepare.assert_not_called()

    def test_checkpoint_folder_picker_preserves_plan_on_cancel(self):
        self.checkpoint_fixture()
        self.dialog.check.click()
        self.drain(lambda: not self.dialog.busy)
        plan = self.dialog.plan
        picker = next(button for button in self.dialog.path_buttons
                      if button.accessibleName() == '选择测试检查点')
        with mock.patch('gameqakit.admission_gui.QFileDialog.getExistingDirectory', return_value=''):
            picker.click()
        self.assertEqual(self.dialog.checkpoint.text(), str(self.seed))
        self.assertIs(self.dialog.plan, plan)
        with mock.patch('gameqakit.admission_gui.QFileDialog.getExistingDirectory', return_value=str(self.root / 'other')):
            picker.click()
        self.assertIsNone(self.dialog.plan)
        self.assertFalse(self.dialog.start.isEnabled())

    def test_switching_game_discards_previous_checkpoint(self):
        self.checkpoint_fixture()
        self.dialog.source.setText(str(self.root / 'different-game'))
        self.assertEqual(self.dialog.checkpoint.text(), '')
        self.assertFalse(self.dialog.start.isEnabled())

    def test_checkpoint_is_not_remembered_and_is_locked_during_operations(self):
        import threading
        self.checkpoint_fixture()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        from gameqakit import admission
        inspect = admission.inspect
        def slow_inspect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return inspect(*args, **kwargs)
        with mock.patch('gameqakit.admission.inspect', side_effect=slow_inspect):
            self.dialog.check.click()
            self.drain(entered.is_set)
            self.assertFalse(self.dialog.checkpoint.isEnabled())
            self.assertTrue(all(not button.isEnabled() for button in self.dialog.path_buttons))
            release.set()
            self.drain(lambda: not self.dialog.busy)
        self.assertTrue(self.dialog.checkpoint.isEnabled())
        dialog = AdmissionDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.source.text(), str(self.source))
        self.assertEqual(dialog.checkpoint.text(), '')
        self.dialog._display_external({'bound': True, 'connected': True, 'paused': True, 'held': []})
        self.dialog.connected_external = True
        self.dialog._controls()
        self.assertFalse(self.dialog.checkpoint.isEnabled())

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

    def test_v1_advertised_observation_refreshes_creator_and_external_preview(self):
        from PIL import Image
        bridge = Path(self.tmp.name) / 'v1-bridge'
        (bridge / 'screenshots').mkdir(parents=True)
        startup = bridge / 'screenshots/startup.png'
        image = bridge / 'screenshots/observation.png'
        Image.new('RGB', (24, 24), 'red').save(startup)
        Image.new('RGB', (24, 24), 'blue').save(image)
        state = {'connected': True, 'protocol': 1, 'observation_seq': 3,
                 'observation_screenshot': 'screenshots/observation.png'}
        self.dialog._display({'state': 'ready_paused', 'screenshot': str(startup),
                              'bridge_directory': str(bridge), 'control': state})
        self.assertEqual(self.dialog.preview.pixmap().toImage().pixelColor(0, 0).name(), '#0000ff')
        Image.new('RGB', (24, 24), 'green').save(image)
        self.dialog._display_external({**state, 'bound': True, 'directory': str(bridge),
                                       'observation_seq': 4, 'paused': True, 'held': []})
        self.assertEqual(self.dialog.preview.pixmap().toImage().pixelColor(0, 0).name(), '#008000')
        self.assertEqual(self.dialog.last_image, (str(image), 4))
        self.manager.start.assert_not_called()

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
