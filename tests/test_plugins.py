"""插件行为用 Node 自带测试运行器验证；真实引擎另作隔离集成检查。"""
from pathlib import Path
import shutil
import subprocess
import unittest


class PluginTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), '插件单元测试需要已有 Node.js')
    def test_rpgmaker_javascript_behavior(self):
        result = subprocess.run(['node', '--test', str(Path(__file__).with_name('rpgmaker_bridge.test.js'))],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout[-14000:] + result.stderr[-3000:])


if __name__ == '__main__':
    unittest.main()
