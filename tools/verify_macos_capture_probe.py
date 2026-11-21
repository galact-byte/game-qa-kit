"""独立回读 ScreenCaptureKit 探针证据；不依赖采集器的解码结果。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def require_occlusion(windows: list[dict], target_id: int, cover_id: int) -> dict:
    target = next(row for row in windows if row['kCGWindowNumber'] == target_id)
    cover = next(row for row in windows if row['kCGWindowNumber'] == cover_id)
    t, c = target['kCGWindowBounds'], cover['kCGWindowBounds']
    if (windows.index(cover) >= windows.index(target) or cover['kCGWindowAlpha'] != 1
            or not cover['kCGWindowIsOnscreen'] or not target['kCGWindowIsOnscreen']
            or c['X'] > t['X'] or c['Y'] > t['Y']
            or c['X'] + c['Width'] < t['X'] + t['Width']
            or c['Y'] + c['Height'] < t['Y'] + t['Height']):
        raise ValueError('目标没有被不透明窗口完整遮挡')
    return t


def verify(root: Path, *, metadata_only: bool = False) -> dict:
    if not metadata_only:
        from PIL import Image
    data = json.loads((root / 'result.json').read_text(encoding='utf-8'))
    if data.get('architecture') != 'arm64' or not data.get('passed'):
        raise ValueError('原生探测没有成功或不是 arm64')
    decoded = {}
    target_id, cover_id = data['target']['window'], data['cover']['window']
    require_occlusion(data['window_list'], target_id, cover_id)
    resized = require_occlusion(data['resized_window_list'], target_id, cover_id)
    if (resized['Width'], resized['Height']) != (400, 240):
        raise ValueError('目标窗口本身没有改尺寸')
    for phase in ['visible', 'occluded', 'frozen', 'resized']:
        rows = data['phases'][phase]
        values = []
        for row in rows:
            if metadata_only:
                value = row['counter']
            else:
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
        if phase != 'frozen':
            for field in ['serial', 'pts']:
                stamps = [row[field] for row in rows]
                if any(b <= a for a, b in zip(stamps, stamps[1:])):
                    raise ValueError('采集器重复使用旧帧序号或时间戳')
        if phase in ['occluded', 'resized']:
            if any(row['target_state']['occlusion_visible'] for row in rows):
                raise ValueError('目标端没有确认完全遮挡状态')
        decoded[phase] = values
    if not data.get('cleanup') or data['frontmost_before'] != data['frontmost_after']:
        raise ValueError('清理或焦点记录不符')
    if metadata_only:
        return {'passed': True, 'decoded': decoded, 'png_count': 0, 'metadata_only': True}
    for filename in ['desktop-occluded.png', 'desktop-resized.png']:
        with Image.open(root / filename) as image:
            rgb = image.convert('RGB')
            t = resized if filename == 'desktop-resized.png' else require_occlusion(data['window_list'], target_id, cover_id)
            region = rgb.crop((t['X'], t['Y'], t['X'] + t['Width'], t['Y'] + t['Height']))
            if any(max(abs(r-200), abs(g-30), abs(b-180)) > 8 for r,g,b in region.getdata()):
                raise ValueError('整屏目标区域没有被遮挡色完整覆盖')
            count = sum(abs(r-200) < 8 and abs(g-30) < 8 and abs(b-180) < 8 for r,g,b in rgb.getdata())
            if count < 600 * 400 * 0.8:
                raise ValueError('整屏证据缺少足够遮挡色像素')
    return {'passed': True, 'decoded': decoded, 'png_count': 18}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--metadata-only', action='store_true')
    args = parser.parse_args()
    result = verify(args.root, metadata_only=args.metadata_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
