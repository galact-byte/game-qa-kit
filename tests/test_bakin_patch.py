"""Exercise emitted IL behavior and refusal boundaries using self-authored assemblies."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_bakin_state import ROOT, CSC, state_case

CECIL = Path(os.environ.get('GAMEQAKIT_BAKIN_CECIL',''))


@unittest.skipUnless(os.name=='nt' and CSC.is_file() and CECIL.is_file(), 'Requires existing registered Mono.Cecil')
class BakinPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='gqk-bakin-patch-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable=Path(cls.temporary.name)/'PatchTests.exe'
        shutil.copyfile(CECIL,Path(cls.temporary.name)/'Mono.Cecil.dll')
        result=subprocess.run([str(CSC),'/nologo','/target:exe','/main:PatchTests','/r:'+str(CECIL),'/out:'+str(cls.executable),
            str(ROOT/'plugins/bakin/PatchTool.cs'),str(ROOT/'tests/bakin/PatchTests.cs')],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)


for case in ('branch_runtime','launch_count','launch_return','launch_valid','query_signature'):
    setattr(BakinPatchTests,'test_'+case,state_case(case))
