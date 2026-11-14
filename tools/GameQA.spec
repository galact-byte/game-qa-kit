# PyInstaller onedir：两个入口共用同一份依赖，便于便携使用与故障定位。
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
analysis = Analysis(
    [str(ROOT / 'tools/portable_entry.py')], pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'plugins'), 'plugins'), (str(ROOT / 'LICENSE'), 'licenses/game-qa-kit'),
           (str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses/Python'),
           (str(ROOT / 'tools/licenses'), 'licenses/open-source')]
          + copy_metadata('Pillow') + copy_metadata('PyQt6') + copy_metadata('PyQt6-Qt6')
          + copy_metadata('PyQt6-sip') + copy_metadata('rubymarshal') + copy_metadata('pyinstaller'),
    hiddenimports=collect_submodules('gameqakit') + collect_submodules('plugins'),
    excludes=['torch', 'torchvision', 'ultralytics', 'easyocr', 'paddleocr', 'paddle',
              'cv2', 'onnxruntime', 'numpy', 'matplotlib', 'scipy', 'tensorflow',
              'IPython', 'tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
gui = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='GameQA',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
mcp = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='GameQA-MCP',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collection = COLLECT(gui, mcp, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='GameQA')
