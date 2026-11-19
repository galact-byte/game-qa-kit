"""Execute the real Lua module in the registered runtime, with no engine input calls."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = os.environ.get('GAMEQAKIT_TEST_ARTEMIS_COMPONENTS')


@unittest.skipUnless(os.name=='nt' and COMPONENTS, 'Requires the registered local Artemis runtime')
class ArtemisLuaTests(unittest.TestCase):
    def test_actual_lua_state_machine(self):
        from gameqakit.desktop import DesktopProcess
        with tempfile.TemporaryDirectory(prefix='artemis-lua-') as temporary:
            root=Path(temporary)
            for name in ('HaitokunoShoudou.exe','d3dcompiler_47.dll'):
                shutil.copyfile(Path(COMPONENTS)/name,root/name)
            module=ROOT/'plugins/artemis/GameQaBridge.lua'
            if module.is_file(): shutil.copyfile(module,root/module.name)
            shutil.copyfile(ROOT/'tests/artemis_state_tests.lua',root/'tests.lua')
            (root/'system.ini').write_text('[WINDOWS]\nWIDTH=640\nHEIGHT=480\nFPS=60\nFORCE_SOFTWARE_RENDER=1\nBOOT=runner.iet\nCHARSET=UTF-8\nSAVEPATH=savedata\n',encoding='utf-8')
            (root/'runner.iet').write_text('''[lua]
local ok, err=pcall(dofile,'tests.lua')
if not ok then
    local f=assert(io.open('lua-results.txt','w')); f:write('FAIL module: '..tostring(err)..'\\n'); f:close()
end
[/lua]
[exit]
''',encoding='utf-8-sig')
            with DesktopProcess([str(root/'HaitokunoShoudou.exe')],cwd=root,detached_stdio=True,
                                env={'TMP':str(root),'TEMP':str(root)}) as target:
                target.run(); deadline=time.monotonic()+15
                while target.alive() and not (root/'lua-results.txt').exists() and time.monotonic()<deadline:
                    time.sleep(.02)
            self.assertTrue((root/'lua-results.txt').is_file(),'Lua runner produced no result')
            lines=(root/'lua-results.txt').read_text(encoding='utf-8').splitlines()
            if evidence:=os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
                path=Path(evidence); path.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(root/'lua-results.txt',path/'lua-results.txt')
            self.assertEqual([line for line in lines if not line.startswith('PASS ')],[], '\n'.join(lines))
            self.assertEqual(len(lines),22)
