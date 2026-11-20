"""公共功能启动自检：只读写独立测试目录，不启动或操作游戏。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def check(report: Path, *, require_frozen=False):
    report = Path(report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    result = {'frozen': bool(getattr(sys, 'frozen', False)), 'platform': sys.platform}
    previous = {name: os.environ.get(name) for name in
                ('GAMEQAKIT_PROFILES', 'GAMEQAKIT_DATA', 'GAMEQAKIT_PROFILE')}
    window = None
    try:
        if require_frozen and not result['frozen']:
            raise RuntimeError('产物自检必须由打包后的程序执行')
        from . import profile, saves, watch
        from .platform_support import info
        from .engines import inspect_game
        from PyQt6.QtWidgets import QApplication, QLabel
        from PyQt6.QtGui import QTextLayout
        from .gui import App
        result.update(info())
        with tempfile.TemporaryDirectory(prefix='startup-', dir=report.parent) as directory:
            root = Path(directory)
            os.environ.update(GAMEQAKIT_PROFILES=str(root / 'profiles'),
                              GAMEQAKIT_DATA=str(root / 'data'), GAMEQAKIT_PROFILE='startup-check')
            sample = profile.GameProfile(name='startup-check', proc='Game')
            profile.save_profile(sample)
            if profile.load_profile(sample.name).name != sample.name:
                raise RuntimeError('配置保存／读取失败')
            result['profile_roundtrip'] = True
            savedata = root / 'sample-save'
            savedata.mkdir()
            (savedata / 'slot1.sav').write_text('startup-check', encoding='utf-8')
            snapshot = saves.snapshot_save('check', savedata, ['*.sav'], sample.saves_dir)
            if (snapshot / 'slot1.sav').read_text(encoding='utf-8') != 'startup-check':
                raise RuntimeError('测试存档快照内容不一致')
            result['save_snapshot'] = True
            evidence = sample.watch_dir / 'sample'
            evidence.mkdir(parents=True)
            (evidence / 'index.jsonl').write_text(
                json.dumps({'t': 'self-test', 'file': 'sample.png'}) + '\n', encoding='utf-8')
            review = watch.watch_report(sample.state_dir, sample.watch_dir, evidence)
            if review['frames'] != 1 or not Path(review['report']).is_file():
                raise RuntimeError('测试证据报告生成失败')
            result['evidence_report'] = True
            result['engine_inspect'] = inspect_game(savedata)['engine']
            command = [sys.executable]
            if not result['frozen']:
                command += ['-B', '-m', 'gameqakit.application']
            requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                         'params': {'name': 'platform_info', 'arguments': {}}}]
            child = subprocess.run(
                [*command, '--mcp'],
                input=''.join(json.dumps(request) + '\n' for request in requests),
                text=True, encoding='utf-8', capture_output=True, timeout=20, check=True)
            responses = [json.loads(line) for line in child.stdout.splitlines() if line.strip()]
            if [row.get('id') for row in responses] != [1, 2, 3] or any('error' in row for row in responses):
                raise RuntimeError(f'MCP 握手／工具查询失败：{responses}')
            result['mcp_tool_count'] = len(responses[1]['result']['tools'])
            capabilities = json.loads(responses[2]['result']['content'][0]['text'])
            if capabilities['platform'] != sys.platform:
                raise RuntimeError('MCP 平台信息不一致')
            app = QApplication.instance() or QApplication([])
            window = App()
            window.show()
            app.processEvents()
            layout = QTextLayout('盯屏巡检配置存档', window.findChild(QLabel, 'h1').font())
            layout.beginLayout()
            layout.createLine()
            layout.endLayout()
            runs = layout.glyphRuns()
            result['chinese_glyphs'] = bool(runs) and all(
                0 not in run.glyphIndexes() for run in runs)
            if not result['chinese_glyphs']:
                raise RuntimeError('界面字体缺少中文字符，无法交付可读界面')
            image = window.grab()
            if image.isNull() or not image.save(str(report.with_suffix('.png'))):
                raise RuntimeError('界面创建或截图失败')
            result['qt_window_size'] = [image.width(), image.height()]
            window.close()
            window = None
            app.processEvents()
        result['ok'] = True
        return result
    except BaseException as exc:
        result.update(ok=False, error=repr(exc))
        raise
    finally:
        if window is not None:
            window.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
