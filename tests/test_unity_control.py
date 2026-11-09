"""在常规回归中编译并执行不依赖 Unity 的真实 C# 控制核心测试。"""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UnityControlTests(unittest.TestCase):
    def test_compiled_control_core_safety(self):
        build = ROOT / 'plugins/unity/build.py'
        tools = runpy.run_path(str(build))
        try:
            tools['compiler']()
        except RuntimeError as error:
            self.skipTest(str(error))
        if os.name != 'nt':
            self.skipTest('本地回归需要 Windows .NET Framework 运行编译产物')
        parent = Path('E:/DevData/gameqa-unity/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='core-', dir=parent) as directory:
            result = subprocess.run([sys.executable, str(build), '--test-core', '--output',
                                     str(Path(directory) / 'ControlCoreTests.exe')],
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                                    env={**os.environ, 'TEMP': directory, 'TMP': directory})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Unity control assertions', result.stdout)
        self.assertIn('mapped action assertions', result.stdout)

    def test_frame_intake_handles_command_publication_and_priority_pause(self):
        if os.name != 'nt':
            self.skipTest('本地回归需要 Windows .NET Framework 运行编译产物')
        tools = runpy.run_path(str(ROOT / 'plugins/unity/build.py'))
        try:
            tools['compiler']()
        except RuntimeError as error:
            self.skipTest(str(error))
        source = (ROOT / 'plugins/unity/GameQaBridge.cs').read_text(encoding='utf-8')
        # Compile the actual intake methods, so changing production ordering is
        # tested without loading Unity or duplicating the polling implementation.
        lease = source[source.index('        private double Lease()'):source.index('        private bool Ready')]
        tick = source[source.index('        private void Tick()'):source.index('        private IEnumerator AfterRender()')]
        parent = Path('E:/DevData/gameqa-unity/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='intake-', dir=parent) as directory:
            generated = Path(directory) / 'TickUnderTest.cs'
            generated.write_text('using System;\nusing UnityEngine;\nusing GameQaKit.Unity;\ninternal sealed partial class BridgeTickHarness\n{\n'
                                 + lease + tick + '\n}\n', encoding='utf-8')
            output = Path(directory) / 'BridgeTickTests.exe'
            tools['compile_sources'](output, [ROOT / 'plugins/unity/ControlCore.cs',
                                              ROOT / 'tests/unity/BridgeTickTests.cs', generated])
            result = subprocess.run([str(output), directory], capture_output=True, encoding='utf-8',
                                    errors='replace', timeout=10,
                                    env={**os.environ, 'TEMP': directory, 'TMP': directory})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Unity frame intake assertions', result.stdout)
