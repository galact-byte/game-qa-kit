"""仅供取证的限量失联快照，不参与控制授权或重试。"""
import json
import math
import os
from pathlib import Path
import time

from gameqakit.admission import no_links
from gameqakit.sessions import write_json


def capture_disconnect(root, bridge_status):
    if os.environ.get('GAMEQAKIT_UNITY_DIAGNOSTICS') != '1':
        return
    try:
        root = Path(root)
        target = root / 'disconnect.json'
        no_links(target)
        if target.exists():
            return
        record = {'schema_version': 1, 'monotonic_ns': time.monotonic_ns(),
                  'wall_time': time.time(), 'bridge_status': bridge_status,
                  'heartbeat_age_seconds': None, 'files': {}}
        # Preserve the bytes read at failure, including malformed/truncated input. Never reread to retry.
        for name in ('status.json', 'connection.json', 'request.ini', 'lease.ini', 'pause.ini', 'diagnostics.json'):
            try:
                path = root / 'bridge' / name
                no_links(path)
                with path.open('rb') as stream:
                    body = stream.read(8193)
                record['files'][name] = {'text': body[:8192].decode('utf-8', errors='replace'),
                                         'truncated': len(body) > 8192}
            except (OSError, ValueError) as error:
                record['files'][name] = {'error': str(error)[:512]}
        raw = record['files']['status.json']
        if 'text' in raw and not raw['truncated']:
            try:
                state = json.loads(raw['text'])
                updated = state.get('updated_at') if isinstance(state, dict) else None
                if type(updated) in (int, float) and math.isfinite(updated):
                    record['heartbeat_age_seconds'] = record['wall_time'] - updated
            except (ValueError, OverflowError):
                pass
        write_json(target, record)
    except Exception:
        # Diagnostic I/O failure must not replace the original exception or bypass finally/stop.
        return
