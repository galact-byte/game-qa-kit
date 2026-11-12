"""真实 Windows Job 回收测试；所有目标均为本仓库自有 Python 程序。"""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from gameqakit import process_job
from gameqakit.win32 import _pid_path


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('自有测试进程未在期限内达到预期状态')


class ProcessJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_current_desktop_process_is_suspended_until_run_and_handles_are_not_inherited(self):
        marker = self.root / 'ran'
        with process_job.OwnedProcess([sys.executable, '-c',
                f'from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(30)'],
                cwd=str(self.root)) as child:
            self.assertFalse(marker.exists())
            flags = ctypes.c_ulong()
            kernel = process_job.k
            kernel.GetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            self.assertTrue(kernel.GetHandleInformation(child.job, ctypes.byref(flags)))
            self.assertFalse(flags.value & 1)
            child.run()
            wait_for(marker.exists)
            pid = child.pid
        wait_for(lambda: not _pid_path(pid))

    def test_job_assignment_failure_never_resumes_target(self):
        child = process_job.OwnedProcess([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=str(self.root))
        with mock.patch.object(process_job.k, 'AssignProcessToJobObject', return_value=0), \
             mock.patch.object(process_job.k, 'ResumeThread') as resume:
            with self.assertRaises(OSError):
                child.__enter__()
            resume.assert_not_called()
        self.assertFalse(child.alive())

    def test_creator_and_worker_crash_each_reap_the_owned_tree_only(self):
        helper = Path(__file__).with_name('process_tree_helper.py')
        for victim in ('creator', 'worker'):
            with self.subTest(victim=victim):
                batch = self.root / victim
                batch.mkdir()
                with subprocess.Popen([sys.executable, str(helper), str(batch), 'creator']) as creator, \
                     subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as bystander:
                    try:
                        wait_for(lambda: (batch / 'ready').exists())
                        ids = json.loads((batch / 'pids.json').read_text(encoding='utf-8'))
                        if victim == 'creator':
                            creator.terminate()
                            creator.wait(5)
                        else:
                            handle = process_job.k.OpenProcess(1, False, ids['worker'])
                            self.assertTrue(handle)
                            try:
                                self.assertTrue(process_job.k.TerminateProcess(handle, 1))
                            finally:
                                process_job.k.CloseHandle(handle)
                        wait_for(lambda: all(not _pid_path(ids[key]) for key in ('worker', 'host', 'target')))
                        self.assertIsNone(bystander.poll())
                    finally:
                        if creator.poll() is None:
                            creator.terminate()
                        creator.wait(5)
                        bystander.terminate()
                        bystander.wait(5)


if __name__ == '__main__':
    unittest.main()
