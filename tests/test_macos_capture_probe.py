"""独立验证 macOS 探针 PNG 像素与增长判据。"""
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

    def test_cover_cannot_decode_as_target_counter(self):
        with self.assertRaises(ValueError):
            decode_counter(Image.new('RGB', (320, 180), (200, 30, 180)))
