"""Generate a local AVM1 smoke-test movie from original geometric shapes."""
from pathlib import Path
from struct import pack
import sys


def bits(*fields):
    stream = ''.join(f'{value & ((1 << width) - 1):0{width}b}' for width, value in fields if width)
    return int(stream.ljust((len(stream) + 7) // 8 * 8, '0'), 2).to_bytes((len(stream) + 7) // 8, 'big')


def rect(w, h):
    width = max(w, h).bit_length() + 1
    return bits((5, width), (width, 0), (width, w), (width, 0), (width, h))


def matrix(x, y):
    width = max(x, y).bit_length() + 1
    return bits((1, 0), (1, 0), (5, width), (width, x), (width, y))


def tag(code, data=b''):
    return pack('<H', code << 6 | min(len(data), 63)) + (pack('<I', len(data)) if len(data) >= 63 else b'') + data


def shape(ident, w, h, rgb):
    # Four clockwise axis-aligned edges with a single solid fill on their right.
    records = [(1, 0), (5, 4), (1, 1)]
    for vertical, delta in [(0, w), (1, h), (0, -w), (1, -h)]:
        width = abs(delta).bit_length() + 1
        records.extend([(2, 3), (4, width - 2), (1, 0), (1, vertical), (width, delta)])
    records.append((6, 0))
    return tag(2, pack('<H', ident) + rect(w, h) + b'\x01\x00' + bytes(rgb) + b'\x00\x10' + bits(*records))


def place(depth, x, y, ident=None, name=None):
    flags = 4 | (2 if ident else 1) | (0x20 if name else 0)
    return tag(26, bytes([flags]) + pack('<H', depth) + (pack('<H', ident) if ident else b'')
               + matrix(x * 20, y * 20) + (name.encode('ascii') + b'\0' if name else b''))


def action(op, data):
    return bytes([op]) + pack('<H', len(data)) + data


def push(*values):
    return action(0x96, b''.join(b'\x00' + v.encode('ascii') + b'\0' if isinstance(v, str)
                                 else b'\x07' + pack('<i', v) for v in values))


def set_x(x):
    return push('marker', 0, x) + b'\x23'


def frame_actions(frame):
    # Key.isDown(39) is evaluated on every frame. SetProperty updates the marker clip.
    down = set_x(128)
    jump = action(0x99, pack('<h', len(down)))
    up = set_x(40)
    code = push(39, 1, 'Key') + b'\x1c' + push('isDown') + b'\x52'
    trace = push(f'gameqa-flash-frame-{frame}') + b'\x26'
    return tag(12, trace + code + action(0x9d, pack('<h', len(up) + len(jump))) + up + jump + down + b'\0')


body = rect(320 * 20, 240 * 20) + pack('<HH', 6 * 256, 2)
body += tag(9, bytes((35, 62, 95)))
body += shape(1, 32 * 20, 32 * 20, (80, 240, 100))
body += shape(2, 24 * 20, 18 * 20, (242, 106, 42))
body += tag(39, pack('<HH', 3, 1) + place(1, 0, 0, 1) + tag(1) + tag(0))
body += place(1, 40, 100, 3, 'marker') + place(2, 32, 24, 2) + frame_actions(1) + tag(1)
body += place(2, 95, 24) + frame_actions(2) + tag(1) + tag(0)
Path(sys.argv[1]).write_bytes(b'FWS\x07' + pack('<I', 8 + len(body)) + body)
