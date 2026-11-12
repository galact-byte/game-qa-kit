"""受管入口共用的首图校验；不发送输入、不改变各后端的暂停语义。"""
import hashlib
import io
from pathlib import Path

from PIL import Image

from .admission import no_links


def validate_ready(root, state):
    if not (state.get('connected') and state.get('paused') is True and state.get('held') == []
            and state.get('observation_seq', 0) > 0 and state.get('session_id')):
        raise RuntimeError('首图或真实暂停松键尚未确认')
    image = Path(root) / 'bridge/screenshots/startup.png'
    no_links(image)
    body = image.read_bytes()
    with Image.open(io.BytesIO(body)) as source:
        width, height = source.size
        if source.format != 'PNG' or width <= 2 or height <= 2 or width * height > 16777216:
            raise RuntimeError('startup_image_invalid')
        pixels = source.convert('RGB')
        count, black, white = 0, 0, 0
        total = (width - 2) * (height - 2)
        for index in range(0, total, max(1, total // 20000)):
            value = pixels.getpixel((index % (width - 2) + 1, index // (width - 2) + 1))
            black += value == (0, 0, 0)
            white += value == (255, 255, 255)
            count += 1
        if max(black, white) / count >= .999:
            raise RuntimeError('startup_image_invalid')
    return {'screenshot': str(image), 'screenshot_sha256': hashlib.sha256(body).hexdigest()}
