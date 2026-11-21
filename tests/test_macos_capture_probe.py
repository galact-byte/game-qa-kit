"""独立验证 macOS 探针 PNG 像素与增长判据。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from PIL import Image
from tools.verify_macos_capture_probe import decode_counter, require_growth, require_occlusion


class MacCaptureEvidenceTests(unittest.TestCase):
    def test_counter_survives_window_size_changes(self):
        for size in [(320, 180), (400, 240), (640, 360)]:
            image = Image.new('RGB', size, (20, 50, 80))
            for bit in range(16):
                value = 240 if 421 & (1 << bit) else 20
                for x in range(bit * size[0] // 16, (bit + 1) * size[0] // 16):
                    for y in range(size[1] // 3, 2 * size[1] // 3):
                        image.putpixel((x, y), (value, value, value))
            self.assertEqual(decode_counter(image), 421)

    def test_growth_rejects_freeze_regression_and_short_evidence(self):
        require_growth([12, 15, 18, 21])
        for values in [[12] * 4, [12, 16, 15, 21], [12, 15], []]:
            with self.assertRaises(ValueError):
                require_growth(values)

    def test_requires_opaque_cover_above_entire_target(self):
        target = {'kCGWindowNumber': 1, 'kCGWindowAlpha': 1, 'kCGWindowIsOnscreen': True,
                  'kCGWindowBounds': {'X': 100, 'Y': 100, 'Width': 320, 'Height': 180}}
        cover = {'kCGWindowNumber': 2, 'kCGWindowAlpha': 1, 'kCGWindowIsOnscreen': True,
                 'kCGWindowBounds': {'X': 90, 'Y': 90, 'Width': 600, 'Height': 400}}
        require_occlusion([cover, target], 1, 2)
        for windows in [[target, cover], [dict(cover, kCGWindowAlpha=0.5), target],
                        [dict(cover, kCGWindowBounds={'X': 90, 'Y': 90, 'Width': 200, 'Height': 400}), target]]:
            with self.assertRaises(ValueError):
                require_occlusion(windows, 1, 2)

    @staticmethod
    def metadata():
        target = {'kCGWindowNumber': 1, 'kCGWindowAlpha': 1, 'kCGWindowIsOnscreen': True,
                  'kCGWindowBounds': {'X': 100, 'Y': 488, 'Width': 320, 'Height': 180}}
        cover = {'kCGWindowNumber': 2, 'kCGWindowAlpha': 1, 'kCGWindowIsOnscreen': True,
                 'kCGWindowBounds': {'X': 90, 'Y': 348, 'Width': 600, 'Height': 400}}
        resized = copy.deepcopy(target)
        resized['kCGWindowBounds'].update(Width=400, Height=240)
        data = {'architecture': 'arm64', 'passed': True, 'cleanup': True,
                'target': {'window': 1}, 'cover': {'window': 2},
                'window_list': [cover, target], 'resized_window_list': [cover, resized],
                'frontmost_before': 123, 'frontmost_after': 123, 'phases': {}}
        data['desktop_coverage'] = {
            'occluded': {'checked_pixels': 57600, 'mismatched_pixels': 0},
            'resized': {'checked_pixels': 96000, 'mismatched_pixels': 0}}
        for index, (phase, counters) in enumerate([
                ('visible', [1, 2, 3, 4]), ('occluded', [6, 7, 8, 9]),
                ('frozen', [10, 10, 10, 10]), ('resized', [12, 13, 14, 15])]):
            data['phases'][phase] = [
                {'counter': count, 'serial': index * 4 + sample, 'pts': index * 4 + sample,
                 'target_state': {'occlusion_visible': phase == 'visible'}}
                for sample, count in enumerate(counters)]
        return data

    def metadata_gate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'result.json').write_text(json.dumps(data), encoding='utf-8')
            return subprocess.run(
                [sys.executable, '-X', 'utf8', '-S', '-B', str(Path(__file__).resolve().parents[1] /
                 'tools' / 'verify_macos_capture_probe.py'), str(root), '--metadata-only'],
                capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_metadata_gate_works_without_pillow(self):
        result = self.metadata_gate(self.metadata())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['passed'])

    def test_metadata_gate_rejects_resize_exposing_fifty_pixels(self):
        data = self.metadata()
        for field in ['window_list', 'resized_window_list']:
            data[field][0]['kCGWindowBounds']['Y'] = 278
        result = self.metadata_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('目标没有被不透明窗口完整遮挡', result.stderr)

    def test_metadata_gate_rejects_target_visible_under_reported_cover(self):
        data = self.metadata()
        data['phases']['resized'][0]['target_state']['occlusion_visible'] = True
        result = self.metadata_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('目标端没有确认完全遮挡状态', result.stderr)

    def test_metadata_gate_rejects_dock_over_target_pixels(self):
        data = self.metadata()
        data['desktop_coverage']['resized']['mismatched_pixels'] = 15200
        result = self.metadata_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('整屏目标区域没有被遮挡色完整覆盖', result.stderr)

    def test_metadata_gate_rejects_missing_pixel_coverage(self):
        data = self.metadata()
        del data['desktop_coverage']
        self.assertNotEqual(self.metadata_gate(data).returncode, 0)

    def test_cover_cannot_decode_as_target_counter(self):
        with self.assertRaises(ValueError):
            decode_counter(Image.new('RGB', (320, 180), (200, 30, 180)))
