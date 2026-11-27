"""自写后台窗口的进程级 Python/WinDLL 调用审计；不证明原生间接调用。"""
import ctypes
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
WORKER = BASE / 'tests/background_audit_worker.py'


def audit_module():
    path = BASE / 'tests/background_call_audit.py'
    if not path.is_file():
        raise AssertionError('缺少执行前阻断并记录调用者的 WinDLL 测试审计器')
    return importlib.import_module('background_call_audit')


@unittest.skipUnless(os.name == 'nt', '需要 Windows ctypes')
class BackgroundCallAuditTests(unittest.TestCase):
    def test_forbidden_call_is_recorded_before_native_execution(self):
        audit = audit_module()
        events = []

        def must_not_execute(*args):
            self.fail('审计器执行了禁止的真实调用')

        function = audit.AuditedFunction(must_not_execute, 'SetCursorPos', events.append)
        with self.assertRaisesRegex(AssertionError, 'SetCursorPos'):
            function(1, 2)
        self.assertEqual(events[0]['api'], 'SetCursorPos')
        self.assertTrue(events[0]['blocked'])
        self.assertEqual(events[0]['pid'], os.getpid())
        self.assertTrue(events[0]['caller'])

    def test_allowed_call_preserves_signature_result_and_last_error(self):
        audit = audit_module()
        native = ctypes.WinDLL('user32', use_last_error=True).GetDesktopWindow
        events = []
        function = audit.AuditedFunction(native, 'GetDesktopWindow', events.append)
        function.restype = ctypes.c_void_p
        function.argtypes = []
        wanted = native()
        ctypes.set_last_error(123)
        self.assertEqual(function(), wanted)
        self.assertEqual(ctypes.get_last_error(), 123)
        self.assertFalse(events[0]['blocked'])

    def test_install_covers_both_loaders_without_calling_native_forbidden_api(self):
        from unittest import mock
        audit = audit_module()
        fake = mock.Mock()
        with tempfile.TemporaryDirectory(prefix='gameqa-audit-loader-') as temp, \
                mock.patch.object(ctypes, 'WinDLL', return_value=fake), \
                mock.patch.object(ctypes, 'windll', ctypes.LibraryLoader(lambda name: fake)):
            with audit.Audit(temp):
                for dll in (ctypes.WinDLL('user32.dll'), ctypes.windll.user32):
                    for name in sorted(audit.FORBIDDEN):
                        with self.subTest(api=name), self.assertRaisesRegex(AssertionError, name):
                            getattr(dll, name)()
            for name in audit.FORBIDDEN:
                getattr(fake, name).assert_not_called()
            events = [json.loads(line) for line in next(Path(temp).glob('calls-*.jsonl')).read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(events), 2 * len(audit.FORBIDDEN))
            self.assertTrue(all(event['blocked'] for event in events))

    def test_failure_to_record_prevents_native_call(self):
        audit = audit_module()

        def unavailable(event):
            raise OSError('audit-write-unavailable')

        def must_not_execute():
            self.fail('记录失败后仍执行了调用')

        with self.assertRaisesRegex(OSError, 'audit-write-unavailable'):
            audit.AuditedFunction(must_not_execute, 'GetDesktopWindow', unavailable)()


@unittest.skipUnless(os.name == 'nt', '需要 Windows 独立桌面')
class BackgroundCallAuditIntegrationTests(unittest.TestCase):
    def test_real_capture_input_and_cleanup_have_attributed_calls(self):
        from gameqakit.desktop import DesktopProcess, k
        from ctypes import wintypes as W
        audit_module()
        with tempfile.TemporaryDirectory(prefix='gameqa-background-audit-') as temp:
            root = Path(temp)
            with DesktopProcess([sys.executable, '-B', str(WORKER), str(root),
                                 str(Path(__file__).resolve()), '--case', str(root)], cwd=BASE,
                                detached_stdio=True) as child:
                desktop = child.desktop_name
                child.run()
                self.assertEqual(k.WaitForSingleObject(child.process, 30000), 0,
                                 '隔离审计超时；不延长窗口代理原时限')
                code = W.DWORD()
                self.assertTrue(k.GetExitCodeProcess(child.process, ctypes.byref(code)))
                detail = (root / 'case-error.txt').read_text(encoding='utf-8') if (root / 'case-error.txt').exists() else ''
                self.assertEqual(code.value, 0, detail)
            result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            self.assertTrue(result['pixels_changed'])
            self.assertEqual(result['presses'], 2)
            self.assertEqual(result['releases'], 2)
            self.assertEqual(result['wheel_point'], [120, 130])
            self.assertEqual(result['drag_endpoint'], [90, 100])
            self.assertEqual(result['agent_exit'], 0)
            events = [json.loads(line) for path in root.glob('calls-*.jsonl')
                      for line in path.read_text(encoding='utf-8').splitlines()]
            starts = [event for event in events if event['event'] == 'start']
            self.assertEqual({event['role'] for event in starts},
                             {'test_background_call_audit', 'background_fixture', 'background_agent'})
            self.assertEqual({event['desktop'] for event in starts}, {desktop})
            self.assertEqual(len({event['pid'] for event in starts}), 3)
            calls = [event for event in events if event['event'] == 'call']
            self.assertTrue(calls)
            self.assertFalse([event for event in calls if event['blocked']])
            agent = next(event['pid'] for event in starts if event['role'] == 'background_agent')
            self.assertEqual(sum(event['api'] == 'PostMessageW' and event['pid'] == agent for event in calls), 10)
            self.assertEqual(sum(event['api'] == 'PrintWindow' and event['pid'] == agent for event in calls), 2)
            self.assertTrue(result['target_reaped'])


def run_case(root):
    """仅由独立桌面的审计 bootstrap 调用，所有输入只发自写窗口。"""
    import subprocess
    from unittest import mock
    from PIL import Image, ImageChops
    from gameqakit.background import Manager
    from gameqakit.desktop import DesktopProcess
    from gameqakit import background_agent as native
    root = Path(root)

    def until(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError('自写窗口未在原等待窗口内就绪')

    with DesktopProcess([sys.executable, '-B', str(WORKER), str(root),
                         str(BASE / 'tests/background_fixture.py'), str(root / 'target.json')],
                        cwd=BASE, isolated_desktop=False, detached_stdio=True) as fixture:
        fixture.run()
        until(lambda: (root / 'target.json').is_file())
        target = json.loads((root / 'target.json').read_text(encoding='utf-8'))
        manager = Manager()
        try:
            popen = subprocess.Popen

            def audited_agent(argv, **kwargs):
                index = next(i for i, arg in enumerate(argv) if Path(arg).name == 'background_agent.py')
                return popen([argv[0], '-B', '-X', 'utf8', str(WORKER), str(root), *argv[index:]], **kwargs)

            with mock.patch.object(native.subprocess, 'Popen', side_effect=audited_agent):
                manager.start(target['hwnd'], target['pid'], elevate=False)
            until(lambda: manager.status().get('ready'))
            first = manager.capture(root / 'before.png')
            click = {'kind': 'click', 'cx': 50, 'cy': 60, 'command_id': 'one'}
            assert manager.input(click) == manager.input(click)
            until(lambda: (root / 'target.jsonl').exists() and len((root / 'target.jsonl').read_text().splitlines()) >= 3)
            second = manager.capture(root / 'after.png')
            with Image.open(first['out']) as left, Image.open(second['out']) as right:
                changed = ImageChops.difference(left, right).getbbox() is not None
            manager.input({'kind': 'drag', 'cx': 10, 'cy': 20, 'end_cx': 90, 'end_cy': 100,
                           'steps': 2, 'command_id': 'two'})
            manager.input({'kind': 'wheel', 'cx': 120, 'cy': 130, 'delta': -120, 'command_id': 'three'})
            until(lambda: len((root / 'target.jsonl').read_text().splitlines()) >= 10)
            rows = [json.loads(line) for line in (root / 'target.jsonl').read_text().splitlines()]
            manager.stop()
            result = {'pixels_changed': changed,
                      'presses': sum(row['message'] == 0x201 for row in rows),
                      'releases': sum(row['message'] == 0x202 for row in rows),
                      'wheel_point': next(row['point'] for row in rows if row['message'] == 0x20A),
                      'drag_endpoint': [row['point'] for row in rows if row['message'] == 0x202][-1],
                      'agent_exit': manager.status()['exit_code']}
        finally:
            manager.close()
        observed_target = native.process(target['pid'])
        try:
            fixture.close()
            result['target_reaped'] = not native.alive(observed_target)
        finally:
            native.k.CloseHandle(observed_target)
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--case':
        import traceback
        try:
            run_case(sys.argv[2])
        except BaseException:
            (Path(sys.argv[2]) / 'case-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            raise
    else:
        unittest.main()
