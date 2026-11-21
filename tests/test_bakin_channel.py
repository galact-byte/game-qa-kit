"""Exercise strict messages on the production CLR state without game components."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_bakin_state import ROOT, CSC, state_case

CASES = ('identity', 'schema', 'duplicate', 'gap', 'expiry', 'overflow', 'lease', 'pause', 'resume', 'bounds', 'fault', 'version', 'timing', 'legacy')


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Requires installed Windows .NET Framework compiler')
class BakinChannelTests(unittest.TestCase):
    def test_python_commands_are_consumed_by_production_channel(self):
        import json
        from plugins.bakin.target import TargetChannel
        with tempfile.TemporaryDirectory(prefix='gqk-bakin-wire-') as folder:
            with TargetChannel(folder,'session_1',12,'12345',clock=lambda:1000) as target:
                target.lease()
                (Path(folder)/'target-lease.json').write_text(
                    json.dumps(target.mailbox.read_json('lease')),encoding='utf-8')
                target.command('resume')
                target.status={'command_seq':1}
                target.command('down',action_id='action',key=16,duration=200)
                target.status={'command_seq':2}
                target.command('up')
                run=subprocess.run([str(self.executable),'wire',folder],capture_output=True,text=True,
                                   encoding='utf-8',errors='replace',timeout=10)
                self.assertEqual(0,run.returncode,run.stdout+run.stderr)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='gqk-bakin-channel-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable = Path(cls.temporary.name) / 'ChannelTests.exe'
        build = subprocess.run([str(CSC), '/nologo', '/optimize+', '/target:exe', '/r:System.Web.Extensions.dll',
            '/out:' + str(cls.executable), str(ROOT / 'plugins/bakin/InputState.cs'),
            str(ROOT / 'plugins/bakin/ChannelState.cs'), str(ROOT / 'tests/bakin/ChannelTests.cs')],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)


for case in CASES:
    setattr(BakinChannelTests, 'test_' + case, state_case(case))
