"""编译真实 HSP C++ 安全核心，验证会话、暂停、租约和消费回执。"""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HspControlTests(unittest.TestCase):
    def test_bytecode_registration_preserves_code_and_existing_plugins(self):
        from plugins.hsp.prepare import add_bridge
        data = bytearray(148)
        data[:4] = b'HSP3'
        struct.pack_into('<I', data, 12, len(data))
        struct.pack_into('<II', data, 24, 112, 20)
        data[112:132] = b'old.dll\0old_init\0xxx'
        struct.pack_into('<IH', data, 80, 132, 16)
        struct.pack_into('<hhIII', data, 132, 0, 0, 0, 8, 0)
        before = bytes(data)
        result = add_bridge(before)
        self.assertEqual(data, before)
        self.assertEqual(result[112:148], before[112:148])
        ds, size = struct.unpack_from('<II', result, 24)
        hp, count = struct.unpack_from('<IH', result, 80)
        self.assertEqual(count, 32)
        self.assertEqual(result[hp:hp+16], before[132:148])
        self.assertEqual(result[ds:ds+20], before[112:132])
        self.assertEqual(result[ds+20:ds+size], b'GameQaHsp.dll\0_hsp3cmdinit@4\0')
        self.assertEqual(struct.unpack_from('<I', result, 12)[0], len(result))
        with self.assertRaises(ValueError):
            add_bridge(result)
        for broken in (b'HSP3', b'bad!' + before[4:], before[:-1]):
            with self.subTest(broken=len(broken)), self.assertRaises(ValueError):
                add_bridge(broken)

    def test_native_safety_core(self):
        vcvars = Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
        if os.name != 'nt' or not vcvars.is_file():
            self.skipTest('需要本地 Windows MSVC x86 工具链')
        parent = Path('E:/DevData/gameqa-hsp/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='core-', dir=parent) as work:
            result = subprocess.run([sys.executable, str(ROOT / 'plugins/hsp/build.py'),
                                     '--test-core', '--output', str(Path(work) / 'core.exe')],
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                                    env={**os.environ, 'TEMP': work, 'TMP': work})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('HSP control assertions passed', result.stdout)
