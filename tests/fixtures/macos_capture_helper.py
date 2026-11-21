"""真实标准管道中的原生辅助进程替身；只用于协议故障注入。"""
import io
import json
import struct
import sys
import time

from PIL import Image

scenario = sys.argv[1]
source = {'window_id': 42, 'pid': 7, 'started_at': '100.25',
          'title': 'Fixture', 'name': 'Fixture', 'exe_path': '/tmp/Fixture', 'size': [3, 2]}
sequence = 0
for line in sys.stdin.buffer:
    request = json.loads(line)
    sequence += 1
    if scenario == 'hang':
        time.sleep(60)
    if scenario == 'eof':
        sys.stdout.buffer.write(struct.pack('>I', 100) + b'{')
        sys.stdout.buffer.flush()
        break
    if scenario == 'oversize':
        sys.stdout.buffer.write(struct.pack('>I', 1000000))
        sys.stdout.buffer.flush()
        break
    image = Image.new('RGB', (3, 2), (19, 51, 81))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    payload = stream.getvalue()
    row = {'version': 1, 'ok': True, 'source': dict(source), 'sequence': sequence,
           'captured_at': '2026-01-02T03:04:05.123+00:00', 'payload_bytes': len(payload)}
    if request['op'] == 'list':
        payload = b''
        row = {'version': 1, 'ok': True, 'windows': [source], 'payload_bytes': 0}
    if scenario == 'permission':
        payload = b''
        row = {'version': 1, 'ok': False, 'error': 'permission_denied', 'payload_bytes': 0}
    if scenario == 'changed':
        row['source']['started_at'] = '101.25'
    if scenario == 'changed_path':
        row['source']['exe_path'] = '/tmp/Replacement'
    if scenario == 'bad_png':
        payload = b'bad png'
        row['payload_bytes'] = len(payload)
    if scenario == 'bad_version':
        row['version'] = 9
    if scenario == 'bad_length':
        row['payload_bytes'] = 1000000000
    if scenario == 'wrong_dimensions':
        row['source']['size'] = [99, 99]
    if scenario == 'duplicate':
        row['sequence'] = 1
    header = json.dumps(row).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('>I', len(header)) + header + payload)
    sys.stdout.buffer.flush()
