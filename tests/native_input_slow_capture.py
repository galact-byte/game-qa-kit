"""取图慢于租约周期时仍须读取续租，不能扩大租约或取图期限。"""
import argparse
import hashlib
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.sessions import write_json
from tests.native_input_mcp import mcp
from tests.native_input_safety import owned, connect
from tests.native_input_smoke import until


def run(output, binaries):
    with owned(output, binaries) as (host, exe, checks):
        client, meta = connect(output, host, exe)
        with mcp(output, exe) as rpc:
            rpc.call('control_resume', confirm=True, command_id='slow-resume')
            (output / 'slow-capture').touch()
            started = time.monotonic()
            result = rpc.call('control_action', confirm=True, action='wait',
                              duration_ms=100, command_id='slow-wait')
            elapsed = time.monotonic() - started
            checks['slow_capture_was_exercised'] = elapsed >= 1.5 and not (output / 'slow-capture').exists()
            checks['renewed_during_capture'] = result['status'] == 'done' and not result['historical']
            checks['image_digest'] = hashlib.sha256(Path(result['screenshot']).read_bytes()).hexdigest() == result['screenshot_sha256']
            rpc.call('control_pause')
            until(lambda: client.status().get('paused') and client.status().get('held') == [], 2)
            checks['pause_after_capture'] = not client.status()['held']
            write_json(output / 'slow-capture-evidence.json', {'elapsed_seconds': elapsed, 'result': result})
    print('slow capture with live MCP renewal passed', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve(), args.binaries.resolve())
