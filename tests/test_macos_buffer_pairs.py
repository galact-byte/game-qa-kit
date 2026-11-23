"""核对同一采集缓冲的 CPU 行与 Core Image 输出，不把一致视为新帧。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image


class MacOSBufferPairTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.verify_macos_buffer_pairs'),
                             '缺少同缓冲像素证据复核器')
        from tools.verify_macos_buffer_pairs import verify
        self.verify = verify
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def pair(self, index, raw=421, converted=421, after=None):
        width, height, stride = 320, 180, 1312
        def pixels(counter):
            payload = bytearray(stride)
            for x in range(width):
                level = 240 if counter & (1 << (x * 16 // width)) else 20
                payload[x * 4:x * 4 + 4] = bytes([level, level, level, 255])
            return payload
        stem = f'buffer-pair-{index:04d}'
        row = {'sample_index': index, 'width': width, 'height': height,
               'row_bytes': stride, 'sample_row': height // 2, 'pixel_format': 'BGRA',
               'before': f'{stem}-before.bgra', 'after': f'{stem}-after.bgra',
               'converted': f'{stem}.png'}
        (self.root / row['before']).write_bytes(pixels(raw))
        (self.root / row['after']).write_bytes(pixels(raw if after is None else after))
        image = Image.new('RGB', (width, height), (20, 50, 80))
        for bit in range(16):
            level = 240 if converted & (1 << bit) else 20
            image.paste((level, level, level), (bit * 20, height // 3,
                                              (bit + 1) * 20, height * 2 // 3))
        image.save(self.root / row['converted'])
        path = self.root / f'{stem}.json'
        path.write_text(json.dumps(row), encoding='utf-8')
        return path, row

    def test_stale_but_matching_pixels_are_not_reported_as_capture_success(self):
        for index in range(1, 5):
            self.pair(index)
        result = self.verify(self.root)
        self.assertEqual(result['raw_counters'], [421, 421, 421, 421])
        self.assertEqual(result['converted_counters'], [421, 421, 421, 421])
        self.assertEqual(result['mismatch_samples'], [])
        self.assertFalse(result['capture_passed'])
        self.assertEqual(result['scope'], 'sample-buffer conversion boundary only')

    def test_reports_conversion_disagreement_from_pixels_not_metadata_claims(self):
        for index in range(1, 5):
            path, row = self.pair(index, raw=422, converted=421)
            row['raw_counter'] = row['converted_counter'] = 999
            path.write_text(json.dumps(row), encoding='utf-8')
        result = self.verify(self.root)
        self.assertEqual(result['raw_counters'], [422] * 4)
        self.assertEqual(result['converted_counters'], [421] * 4)
        self.assertEqual(result['mismatch_samples'], [1, 2, 3, 4])
        self.assertFalse(result['capture_passed'])

    def test_rejects_buffer_mutation_while_converting(self):
        for index in range(1, 5):
            self.pair(index, after=422 if index == 2 else 421)
        with self.assertRaisesRegex(ValueError, '转换期间'):
            self.verify(self.root)

    def test_rejects_missing_truncated_wrong_format_and_outside_evidence(self):
        for index in range(1, 5):
            path, original = self.pair(index)
        for field, value in [('before', '../outside.bgra'), ('after', '/outside.bgra'),
                             ('pixel_format', 'NV12'), ('row_bytes', 1),
                             ('sample_row', 0), ('width', True), ('height', 0)]:
            with self.subTest(field=field):
                row = dict(original, **{field: value})
                path.write_text(json.dumps(row), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.verify(self.root)
        path.write_text(json.dumps(original), encoding='utf-8')
        raw = self.root / original['before']
        raw.write_bytes(raw.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, '长度'):
            self.verify(self.root)
        raw.unlink()
        with self.assertRaisesRegex(ValueError, '缺少'):
            self.verify(self.root)

    def test_rejects_invalid_counter_pixels_and_insufficient_evidence(self):
        with self.assertRaisesRegex(ValueError, '至少'):
            self.verify(self.root)
        for index in range(1, 5):
            _, row = self.pair(index)
        payload = bytes([200, 30, 180, 255]) * 328
        for field in ['before', 'after']:
            (self.root / row[field]).write_bytes(payload)
        with self.assertRaisesRegex(ValueError, '像素'):
            self.verify(self.root)

    def test_rejects_png_size_mismatch_and_duplicate_sample_indices(self):
        for index in range(1, 5):
            path, row = self.pair(index)
        Image.new('RGB', (16, 1)).save(self.root / row['converted'])
        with self.assertRaisesRegex(ValueError, '尺寸'):
            self.verify(self.root)
        self.pair(4)
        row['sample_index'] = 3
        path.write_text(json.dumps(row), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '序号'):
            self.verify(self.root)
