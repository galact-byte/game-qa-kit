"""验证 Metal 诊断读回的原始像素，不以 GPU 完成记录代替图像。"""
import json
from pathlib import Path
import tempfile
import unittest

from tools.verify_metal_readback import verify


class MetalReadbackTests(unittest.TestCase):
    def record(self, root, expected=421, pixels=421):
        width = 320
        row_bytes = 1280
        payload = bytearray(row_bytes)
        for x in range(width):
            level = 240 if pixels & (1 << (x * 16 // width)) else 20
            payload[x * 4:x * 4 + 4] = bytes([level, level, level, 255])
        name = f'gpu-readback-{expected}'
        (root / f'{name}.bgra').write_bytes(payload)
        row = {'counter': expected, 'width': width, 'row_bytes': row_bytes,
               'file': f'{name}.bgra', 'gpu_status': 'completed'}
        (root / f'{name}.json').write_text(json.dumps(row), encoding='utf-8')
        state = {'rendering': {'renderer': 'metal', 'readback_enabled': True,
                               'readback_error': '', 'gpu_error': ''}}
        (root / 'target-state.json').write_text(json.dumps(state), encoding='utf-8')
        return row

    def test_decodes_raw_pixels_and_compares_expected_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.record(root)
            self.assertEqual(verify(root)['decoded'], [421])

    def test_rejects_gpu_completion_with_stale_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.record(root, expected=422, pixels=421)
            with self.assertRaisesRegex(ValueError, '计数不符'):
                verify(root)

    def test_rejects_invalid_pixels_truncation_and_missing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, '缺少'):
                verify(root)
            row = self.record(root)
            path = root / row['file']
            original = path.read_bytes()
            path.write_bytes(original[:-1])
            with self.assertRaisesRegex(ValueError, '长度'):
                verify(root)
            path.write_bytes(bytes([200, 30, 180, 255]) * 320)
            with self.assertRaisesRegex(ValueError, '像素无效'):
                verify(root)

    def test_rejects_missing_or_failed_renderer_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.record(root)
            state_path = root / 'target-state.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['rendering']['readback_error'] = 'write failed'
            state_path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'GPU'):
                verify(root)
            state_path.unlink()
            with self.assertRaisesRegex(ValueError, '状态'):
                verify(root)

    def test_rejects_external_payload_paths_and_failed_gpu_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.record(root)
            metadata = root / 'gpu-readback-421.json'
            row['file'] = '../outside.bgra'
            metadata.write_text(json.dumps(row), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '路径'):
                verify(root)
            row['file'] = 'gpu-readback-421.bgra'
            row['gpu_status'] = 'error'
            metadata.write_text(json.dumps(row), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'GPU'):
                verify(root)
