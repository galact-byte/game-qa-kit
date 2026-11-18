"""Check Chromium DevTools IPC on the registered private-desktop runtime."""
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt' and os.environ.get('GAMEQAKIT_TEST_FLASH_EDGE'),
                     'requires the registered Edge build')
class FlashPipeTests(unittest.TestCase):
    def test_private_pipe_reaches_only_the_owned_browser(self):
        from importlib.util import find_spec

        self.assertIsNotNone(find_spec('plugins.flash'), 'Private Edge DevTools transport is unavailable')
        from plugins.flash.cdp import Browser

        edge = Path(os.environ['GAMEQAKIT_TEST_FLASH_EDGE']).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix='gameqa-flash-pipe-') as tmp:
            profile = Path(tmp) / 'profile'
            with Browser(edge, Path(tmp)) as browser:
                version = browser.call('Browser.getVersion')
                self.assertIn('157.0.4341.60', version['product'])
                self.assertTrue(browser.host.desktop_name.startswith('gameqakit-'))
                self.assertTrue(browser.host.alive())
                self.assertFalse((profile / 'DevToolsActivePort').exists())
            self.assertFalse(browser.host.alive())
