"""后台接入界面的确认、阻碍反馈与关闭生命周期。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox
from gameqakit.admission_gui import AdmissionDialog


class AdmissionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = mock.Mock()
        self.manager.owned = {}
        self.patch = mock.patch('gameqakit.admission_gui.Manager', return_value=self.manager)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.dialog = AdmissionDialog()
        self.dialog.source.setText('source')
        self.dialog.qa_root.setText('qa')
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
