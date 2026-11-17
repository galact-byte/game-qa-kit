"""KAG 原处理器没有夹具字段时也必须返回真实消费证据。"""
import os
from pathlib import Path
import shutil
import unittest
from tests.test_kirikiri_bridge import KiriKiriBridgeTests, BASE, PLUGIN


@unittest.skipUnless(os.name == 'nt' and os.environ.get('GAMEQAKIT_TEST_KIRIKIRI'), '需固定本地运行时')
class KagBridgeTests(KiriKiriBridgeTests):
    def setUp(self):
        super().setUp()
        shutil.copyfile(BASE / 'tests/kirikiri_kag_fixture/startup.tjs', self.runtime / 'startup.tjs')
        for name in ('AfterInit2.tjs', 'KagBridge.tjs'):
            shutil.copyfile(PLUGIN / name, self.runtime / name)
        self.host_script = self.root / 'kag-host-fixture.py'
        self.host_script.write_text(
            'import sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(BASE)!r})\n'
            'from plugins.kirikiri.kag_host import Supervisor\n'
            'from gameqakit.desktop import DesktopProcess\n'
            'runtime, directory, session = Path(sys.argv[2]),Path(sys.argv[4]),sys.argv[6]\n'
            'for folder in ("savedata","appdata","localappdata","tmp"): (runtime.parent/folder).mkdir(exist_ok=True)\n'
            'environment={k:str(runtime.parent/v) for k,v in (("APPDATA","appdata"),("LOCALAPPDATA","localappdata"),("TEMP","tmp"),("TMP","tmp"))}\n'
            'with DesktopProcess([str(runtime),str(runtime.parent),"-dbstyle=gdi","-usedb=yes","-datapath="+(runtime.parent/"savedata").as_posix(),"-gameqadir="+directory.as_posix()],cwd=runtime.parent,env=environment) as target:\n'
            ' Supervisor(target,directory,session).run()\n', encoding='utf-8')

    def test_kag_handlers_without_fixture_fields_dispatch_and_release(self):
        self.start()
        self.client.resume()
        result = self.client.action('confirm', duration_ms=70, command_id='kag-input')
        self.assertTrue(result['press_consumed'])
        self.assertTrue(result['release_consumed'])
        events = self.observer()['events']
        self.assertEqual([e['query_count'] for e in events], [1, 0])
        self.assertEqual([e['handler_return'] for e in events], [17, 'released'])
        self.assertEqual([e['handler_query'] for e in events], [1, -1])
        self.assertNotIn('value', self.observer())
        from plugins.kirikiri.host import read_struct
        self.assertEqual(read_struct(Path(self.observer()['data_path']) / 'original-init.tjs')['preserved'], 1)

# The inherited fixture suite assumes storage and fault switches specific to FixtureWindow.
# KAG lifecycle cases are selected explicitly; normal bridge regressions run separately.
for name in dir(KiriKiriBridgeTests):
    if name.startswith('test_'):
        setattr(KagBridgeTests, name, None)
KagBridgeTests.test_control_read_contention_preserves_target_and_input = (
    KiriKiriBridgeTests.test_control_read_contention_preserves_target_and_input)
