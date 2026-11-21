"""独立回读 ScreenCaptureKit 探针证据；不依赖采集器的解码结果。"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from PIL import Image


def decode_counter(image: Image.Image) -> int:
    image = image.convert('RGB')
    value = 0
    for bit in range(16):
        pixel = image.getpixel((int((bit + 0.5) * image.width / 16), image.height // 2))
        if max(pixel) - min(pixel) > 10 or not (max(pixel) < 40 or min(pixel) > 220):
            raise ValueError(f'目标计数像素无效：{pixel}')
        if min(pixel) > 220:
            value |= 1 << bit
    return value


def require_growth(values: list[int]) -> None:
    if len(values) < 4 or any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError(f'缺少连续增长证据：{values}')


def verify(root: Path) -> dict:
    data = json.loads((root / 'result.json').read_text(encoding='utf-8'))
    if data.get('architecture') != 'arm64' or not data.get('passed'):
        raise ValueError('原生探测没有成功或不是 arm64')
    decoded = {}
    for phase in ['visible', 'occluded', 'frozen', 'resized']:
        rows = data['phases'][phase]
        values = []
        for row in rows:
            with Image.open(root / row['file']) as image:
                value = decode_counter(image)
                if value != row['counter']:
                    raise ValueError('PNG 像素计数与原生记录不符')
                if phase == 'resized' and image.size != (400, 240):
                    raise ValueError(f'改尺寸后的图像错误：{image.size}')
                values.append(value)
        if phase == 'frozen':
            if len(values) < 4 or len(set(values)) != 1:
                raise ValueError('冻结反例没有稳定停住')
            try:
                require_growth(values)
            except ValueError:
                pass
            else:
                raise ValueError('增长判据错误接受冻结反例')
        else:
            require_growth(values)
        decoded[phase] = values
    if not data.get('cleanup') or data['frontmost_before'] != data['frontmost_after']:
        raise ValueError('清理或焦点记录不符')
    for filename in ['desktop-occluded.png', 'desktop-resized.png']:
        with Image.open(root / filename) as image:
            rgb = image.convert('RGB')
            count = sum(abs(r-200) < 8 and abs(g-30) < 8 and abs(b-180) < 8 for r,g,b in rgb.getdata())
            if count < 600 * 400 * 0.8:
                raise ValueError('整屏证据缺少足够遮挡色像素')
    return {'passed': True, 'decoded': decoded, 'png_count': 18}


if __name__ == '__main__':
    result = verify(Path(sys.argv[1]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
