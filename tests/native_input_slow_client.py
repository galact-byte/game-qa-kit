"""客户端校验截图慢于租约周期时仍须续租；使用真实原生桥与独立自有窗口。"""
import argparse
from pathlib import Path
import sys
import time
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.sessions import write_json
from tests.native_input_safety import owned, connect
from tests.native_input_smoke import until


def run(output, binaries):
    with owned(output, binaries) as (host, exe, checks):
        client, _ = connect(output, host, exe)
        original = Image.open
        delayed = False

        def slow_open(*args, **kwargs):
            nonlocal delayed
            if not delayed:
                delayed = True
                time.sleep(1.8)
            return original(*args, **kwargs)

        started = time.monotonic()
        with patch('gameqakit.control.Image.open', side_effect=slow_open):
            result = client.resume(command_id='slow-client-resume', timeout=4)
        state = client.status()
        elapsed = time.monotonic() - started
        write_json(output / 'slow-client-evidence.json', {
            'elapsed_seconds': elapsed, 'result': result, 'state_after_validation': state})
        checks['slow_validation_exercised'] = delayed and elapsed >= 1.8
        checks['lease_survived_validation'] = state.get('paused') is False and not state.get('error')
        assert checks['lease_survived_validation'], state
        result = client.action('move', direction='right', duration_ms=100, command_id='after-slow-client')
        checks['next_action_consumed'] = result['press_consumed'] and result['release_consumed']
        client.pause()
        until(lambda: client.status().get('paused') and client.status().get('held') == [], 2)
        checks['pause_confirmed'] = not client.status()['held']
    print('slow client validation with renewal passed', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve(), args.binaries.resolve())
