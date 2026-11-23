"""复核同一 ScreenCaptureKit 缓冲的原始 BGRA 行与转换后 PNG。"""
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
    paths = sorted(root.glob('buffer-pair-*.json'))
    if len(paths) < 4:
        raise ValueError('至少需要四组同缓冲像素证据')
    records = []
    seen = set()
    for path in paths:
        row = json.loads(path.read_text(encoding='utf-8'))
        index, width, height, stride, y = (
            row.get(key) for key in ('sample_index', 'width', 'height', 'row_bytes', 'sample_row'))
        if type(index) is not int or index <= 0 or index in seen:
            raise ValueError('采样序号无效或重复')
        seen.add(index)
        if (any(type(value) is not int for value in (width, height, stride, y))
                or not 16 <= width <= 16384 or not 1 <= height <= 16384
                or not width * 4 <= stride <= 65536 or y != height // 2
                or row.get('pixel_format') != 'BGRA'):
            raise ValueError('原始像素尺寸、行位置或格式无效')
        files = {}
        for key, suffix in [('before', '.bgra'), ('after', '.bgra'), ('converted', '.png')]:
            name = row.get(key)
            if not isinstance(name, str):
                raise ValueError('缺少像素文件路径')
            candidate = (root / name).resolve()
            if candidate.parent != root or candidate.suffix != suffix:
                raise ValueError('像素路径越界或格式错误')
            if not candidate.is_file():
                raise ValueError('缺少像素证据文件')
            files[key] = candidate
        raw = []
        for key in ('before', 'after'):
            if files[key].stat().st_size != stride:
                raise ValueError('BGRA 行长度不符')
            raw.append(files[key].read_bytes()[:width * 4])
        if raw[0] != raw[1]:
            raise ValueError(f'第 {index} 组缓冲在转换期间发生变化，不能归因转换环节')
        image = Image.frombytes('RGBA', (width, 1), raw[0], 'raw', 'BGRA')
        raw_counter = decode_counter(image)
        with Image.open(files['converted']) as converted:
            if converted.size != (width, height):
                raise ValueError('转换后 PNG 尺寸不符')
            converted_counter = decode_counter(converted)
        records.append((index, raw_counter, converted_counter))
    records.sort()
    return {
        'samples': len(records),
        'raw_counters': [raw for _, raw, _ in records],
        'converted_counters': [converted for _, _, converted in records],
        'mismatch_samples': [index for index, raw, converted in records if raw != converted],
        'capture_passed': False,
        'scope': 'sample-buffer conversion boundary only',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), ensure_ascii=False, indent=2))
