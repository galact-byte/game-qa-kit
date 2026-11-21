"""模拟平台入口，阻止 Win32 导入，并通过真实管道运行公共观察链。"""
from pathlib import Path
import subprocess
import sys
import unittest


class MacOSEntryTests(unittest.TestCase):
    def test_arm64_entry_capture_watch_mark_and_readonly_mcp(self):
        script = r'''
import importlib.abc
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading

real_python = sys.executable
platform.machine = lambda: 'arm64'
platform.mac_ver = lambda: ('15.0', ('', '', ''), 'arm64')
sys.platform = 'darwin'
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'gameqakit.win32', 'gameqakit.inputs', 'gameqakit.desktop'}:
            raise AssertionError('禁止在 macOS 导入 Windows 模块：' + fullname)
sys.meta_path.insert(0, Guard())
from gameqakit import platform_support as support
assert support.info()['features']['window_capture']
assert not support.info()['features']['game_control']
from gameqakit import macos_capture as native
source = {'window_id': 42, 'pid': 7, 'started_at': '100.25', 'title': 'Fixture',
          'name': 'Fixture', 'exe_path': '/tmp/Fixture', 'size': [3, 2]}
helper = Path('tests/fixtures/macos_capture_helper.py').resolve()
native.helper_command = lambda: [real_python, '-X', 'utf8', '-B', str(helper), 'normal']
from gameqakit.profile import GameProfile
from gameqakit import watch
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    p = GameProfile('fixture', 'Fixture', data_dir=str(root), target_source=source)
    assert native.find_profile_window(p) == source
    stop = threading.Event()
    def event(row):
        if row.get('kind') == 'frame': stop.set()
    result = watch.watch(root/'watch'/'one', p.proc, p.state_dir, interval=.02,
                         stop_event=stop, on_event=event, target_source=source)
    assert result['ok'] and result['captured'] > 0, result
    index = json.loads((root/'watch'/'one'/'index.jsonl').read_text().splitlines()[0])
    assert index['capture_source']['platform'] == 'darwin' and index['hwnd'] is None
    marked = watch.mark('人工标记', p.proc, p.state_dir, p.watch_dir, target_source=source)
    assert Path(marked['marked']).is_file(), marked
    from gameqakit import mcp_server
    names = set(mcp_server.TOOLS)
    assert {'capture','win','mark','watch_start','watch_stop','processes'} <= names, names
    assert not ({'click','key','load','kill_game','game_start'} & names), names
print('macOS 观察、标记、来源与工具边界通过')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
