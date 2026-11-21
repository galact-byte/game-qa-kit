"""独立核对自写 Metal 夹具 GPU 读回像素。"""
import argparse
import json
from pathlib import Path

from PIL import Image

try:
    from tools.verify_macos_capture_probe import decode_counter
except ModuleNotFoundError:
    from verify_macos_capture_probe import decode_counter


def verify(root: Path) -> dict:
    root = root.resolve()
    records = []
    for path in root.glob('gpu-readback-*.json'):
        row = json.loads(path.read_text(encoding='utf-8'))
        width, row_bytes, expected = row['width'], row['row_bytes'], row['counter']
        if (type(width) is not int or type(row_bytes) is not int
                or not 16 <= width <= 16384 or not width * 4 <= row_bytes <= 65536
                or type(expected) is not int or not 0 <= expected <= 65535):
            raise ValueError('GPU 读回尺寸或计数无效')
        if row.get('gpu_status') != 'completed':
            raise ValueError('GPU 命令未成功完成')
        payload = (root / row['file']).resolve()
        if payload.parent != root or payload.suffix != '.bgra':
            raise ValueError('GPU 读回路径越界或格式错误')
        if payload.stat().st_size != row_bytes:
            raise ValueError('GPU 读回长度不符')
        image = Image.frombytes('RGBA', (width, 1), payload.read_bytes()[:width * 4], 'raw', 'BGRA')
        actual = decode_counter(image)
        if actual != expected:
            raise ValueError(f'GPU 像素计数不符：提交 {expected}，读回 {actual}')
        records.append((expected, actual))
    if not records:
        raise ValueError('缺少 GPU 像素读回记录')
    state_path = root / 'target-state.json'
    if not state_path.is_file():
        raise ValueError('缺少 GPU 目标状态')
    rendering = json.loads(state_path.read_text(encoding='utf-8')).get('rendering', {})
    if (rendering.get('renderer') != 'metal' or rendering.get('readback_enabled') is not True
            or rendering.get('readback_error') != '' or rendering.get('gpu_error') != ''):
        raise ValueError('GPU 读回未启用或渲染诊断失败')
    records.sort()
    return {'decoded': [actual for _, actual in records], 'readbacks': len(records),
            'capture_passed': False, 'scope': 'GPU texture only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), ensure_ascii=False, indent=2))
