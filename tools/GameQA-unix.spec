# macOS／Linux 通用测试包；仅冻结公共入口及其实际依赖。
from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent
licenses = [(str(ROOT / 'LICENSE'), 'licenses/game-qa-kit'),
            (str(ROOT / 'tools/licenses/Python-3.12.7.txt'), 'licenses/Python'),
            (str(ROOT / 'tools/licenses'), 'licenses/open-source'),
            (str(ROOT / 'plugins/native_input/samples/srpgstudio-x86-fixed.json'),
             'plugins/native_input/samples')]
metadata = []
for package in ('Pillow', 'PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'rubymarshal', 'pyinstaller'):
    metadata += copy_metadata(package)
a = Analysis([str(ROOT / 'tools/portable_entry.py')], pathex=[str(ROOT)], binaries=[],
             datas=licenses + metadata,
             hiddenimports=['gameqakit.gui', 'gameqakit.cli', 'gameqakit.mcp_server',
                            'gameqakit.sessions', 'gameqakit.startup_check'],
             excludes=['gameqakit.win32', 'gameqakit.inputs', 'gameqakit.desktop',
                       'gameqakit.game_worker', 'gameqakit.background_agent',
                       'torch', 'torchvision', 'ultralytics', 'easyocr', 'paddleocr',
                       'paddle', 'cv2', 'onnxruntime', 'numpy', 'matplotlib', 'scipy',
                       'tensorflow', 'IPython', 'tkinter', 'pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GameQA',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=sys.platform != 'darwin')
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='GameQA')
if sys.platform == 'darwin':
    app = BUNDLE(collection, name='GameQA.app', bundle_identifier='io.github.game-qa-kit',
                 info_plist={'CFBundleName': 'Game QA Kit', 'NSHighResolutionCapable': True})
