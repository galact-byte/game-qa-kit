"""在原生 Mac 上对照持有／重新枚举过滤器的截图行为，保留失败现场。"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess


def run(fixture, output):
    output.mkdir(parents=True, exist_ok=False)
    summaries = []
    for name, extra in [('reference', {}), ('default-config', {'GAMEQA_PROBE_DEFAULT_CONFIG': '1'}),
                        ('exclude-desktop', {'GAMEQA_PROBE_EXCLUDE_DESKTOP': '1'})]:
        root = output/name
        root.mkdir()
        env = {**os.environ, 'GAMEQA_CAPTURE_MODE': 'snapshot', **extra}
        row = {'name': name, 'options': extra, 'timed_out': False}
        with (root/'run.log').open('wb') as log:
            process = subprocess.Popen([str(fixture), 'capture', str(root)], env=env,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                row['timed_out'] = True
            finally:
                (root/'stop').touch()
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    row['forced_kill'] = True
            row['exit_code'] = process.returncode
        summaries.append(row)
        (output/'comparison.json').write_text(json.dumps(summaries, indent=2), encoding='utf-8')
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    run(options.fixture.resolve(), options.output.resolve())
