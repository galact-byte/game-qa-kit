"""在真实 DirectInput 模拟键盘设备上验证动作消费与释放。"""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.native_input_safety import owned, connect
from tests.native_input_smoke import read


def run(output, binaries, *, first_alias=False):
    flag = 'emulated-keyboard-1' if first_alias else 'emulated-keyboard'
    with owned(output, binaries, flags=(flag,)) as (host, exe, checks):
        client, meta = connect(output, host, exe)
        client.resume(command_id='emulated-resume')
        result = client.action('move', direction='right', duration_ms=120, command_id='emulated-right')
        target = read(output / 'target-state.json')
        checks['emulated_keyboard_consumes_and_releases'] = (
            result['press_consumed'] and result['release_consumed'] and
            target['position'] == 120 and target['presses'] == target['releases'] == 1)
        checks['emulated_mouse_is_neutral'] = read(output / 'mouse-state.json')['neutral']
        client.pause()
    return read(output / 'result.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    parser.add_argument('--first-alias', action='store_true')
    args = parser.parse_args()
    print(run(args.output.resolve(), args.binaries.resolve(), first_alias=args.first_alias))
