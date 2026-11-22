# macOS／Linux 通用测试包；仅冻结公共入口及其实际依赖。
from pathlib import Path
import os
import platform
import sys
from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent
licenses = [(str(ROOT / 'LICENSE'), 'licenses/game-qa-kit'),
            (str(ROOT / 'tools/licenses/Python-3.12.7.txt'), 'licenses/Python'),
            (str(ROOT / 'tools/licenses'), 'licenses/open-source'),
            (str(ROOT / 'plugins/native_input/samples/srpgstudio-x86-fixed.json'),
             'plugins/native_input/samples')]
licenses += [(str(ROOT / 'plugins/godot/GameQaBridge.gd'), 'plugins/godot'),
             (str(ROOT / 'plugins/godot/macos-arm64-component.json'), 'plugins/godot')]
metadata = []
native_binaries = []
if sys.platform == 'darwin' and platform.machine() == 'arm64':
    helper = Path(os.environ.get('GAMEQA_MAC_CAPTURE_HELPER', ''))
    if not helper.is_file():
        raise RuntimeError('macOS arm64 构建必须随包提供已编译的采集组件')
    native_binaries = [(str(helper), 'plugins/macos_capture')]
if sys.platform == 'linux':
    font = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    font_license = Path('/usr/share/doc/fonts-noto-cjk/copyright')
    if not font.is_file() or not font_license.is_file():
        raise RuntimeError('Linux 构建需要 fonts-noto-cjk，字体和许可必须同时存在')
    licenses += [(str(font), 'fonts'), (str(font_license), 'licenses/NotoCJK')]
for package in ('Pillow', 'PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'rubymarshal', 'pyinstaller'):
    metadata += copy_metadata(package)
a = Analysis([str(ROOT / 'tools/portable_entry.py')], pathex=[str(ROOT)], binaries=native_binaries,
             datas=licenses + metadata,
             hiddenimports=['gameqakit.gui', 'gameqakit.cli', 'gameqakit.mcp_server',
                            'gameqakit.sessions', 'gameqakit.startup_check',
                            'gameqakit.macos_capture', 'gameqakit.x11_capture',
                            'gameqakit.x11_helper', 'gameqakit.x11_native',
                            'gameqakit.game_worker', 'gameqakit.macos_process',
                            'plugins.godot.prepare', 'plugins.godot.session'],
             excludes=['gameqakit.win32', 'gameqakit.inputs', 'gameqakit.desktop',
                       'gameqakit.background_agent',
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
