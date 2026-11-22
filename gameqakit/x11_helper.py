"""X11 隔离辅助入口；只接受有界 JSON 请求并输出长度前缀帧。"""
import datetime as dt
import io
import json
import signal
import struct
import sys

from .macos_capture import MAX_JSON, MAX_PNG
from .capture_session import CaptureEnded


def reply(row, payload=b''):
    data = json.dumps({'version': 1, **row, 'payload_bytes': len(payload)}, ensure_ascii=False).encode('utf-8')
    if len(data) > MAX_JSON or len(payload) > MAX_PNG:
        raise CaptureEnded('X11 响应长度超限')
    sys.stdout.buffer.write(struct.pack('>I', len(data)) + data + payload)
    sys.stdout.buffer.flush()


def main():
    connection = None
    sequence = 0
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        while True:
            data = sys.stdin.buffer.readline(8193)
            if not data: return 0
            try:
                if len(data) > 8192 or not data.endswith(b'\n'):
                    raise CaptureEnded('X11 请求长度超限')
                request = json.loads(data)
                if not isinstance(request, dict) or request.get('version') != 1:
                    raise CaptureEnded('X11 请求版本无效')
                op = request.get('op')
                if op not in ('list', 'capture'): raise CaptureEnded('未知 X11 操作')
                if connection is None:
                    from .x11_native import Connection
                    connection = Connection()
                if op == 'list':
                    windows, unavailable = connection.list_windows()
                    reply({'ok': True, 'windows': windows, 'unavailable': unavailable})
                else:
                    from .x11_capture import source_identity
                    source_identity(request.get('source'))
                    image, source = connection.capture(request['source'])
                    output = io.BytesIO()
                    image.save(output, 'PNG')
                    sequence += 1
                    reply({'ok': True, 'source': source, 'sequence': sequence,
                           'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')}, output.getvalue())
            except Exception as exc:
                reply({'ok': False, 'error': str(exc)[:300]})
                return 1
    finally:
        if connection is not None: connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
