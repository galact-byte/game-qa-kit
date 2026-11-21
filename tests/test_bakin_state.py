"""Execute the production input state against independent CLR behavior cases."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
CASES = ('edges', 'repeat', 'consume', 'pause', 'lease', 'renewal', 'suspend',
         'axis', 'trigger-consumed', 'duplicate', 'fault', 'illegal', 'repeat-consume', 'trigger-repeat-release', 'consume-start', 'delayed-sample', 'start-expiry', 'pending-cancel', 'timing-bounds')


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Requires installed Windows .NET Framework compiler')
class BakinStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='gqk-bakin-state-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable = Path(cls.temporary.name) / 'StateTests.exe'
        build = subprocess.run([str(CSC), '/nologo', '/optimize+', '/target:exe',
            '/out:' + str(cls.executable), str(ROOT / 'plugins/bakin/InputState.cs'),
            str(ROOT / 'tests/bakin/StateTests.cs')], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)


def state_case(name):
    def check(self):
        result = subprocess.run([str(self.executable), name], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('PASS ' + name, result.stdout)
    return check


for case in CASES:
    setattr(BakinStateTests, 'test_' + case.replace('-', '_'), state_case(case))
