"""测试管道夹具；系统 X11 由另外的原生产品验收覆盖。"""
import io
import json
import struct
import sys
import time
from PIL import Image

scenario = sys.argv[1]
sequence = 0
for line in sys.stdin.buffer:
    request = json.loads(line)
    if scenario == 'hang': time.sleep(10)
    if scenario == 'oversize':
        sys.stdout.buffer.write(struct.pack('>I', 65537)); sys.stdout.buffer.flush(); break
    sequence += 1
    source = dict(request['source'])
    if scenario == 'changed': source['connection_id'] = 'other'
    image = io.BytesIO()
    Image.new('RGB', (3, 2), (19, 51, 81)).save(image, 'PNG')
    payload = b'bad' if scenario == 'bad_png' else image.getvalue()
    row = {'version': 1, 'ok': True, 'source': source, 'sequence': sequence,
           'captured_at': '2026-01-02T03:04:05.123+00:00', 'payload_bytes': len(payload)}
    data = json.dumps(row).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(data)) + data + payload)
    sys.stdout.buffer.flush()
