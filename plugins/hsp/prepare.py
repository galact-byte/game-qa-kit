"""为独立测试副本的 HSP3 字节码追加桥注册项，原文件保持只读。"""
from __future__ import annotations

import argparse
from pathlib import Path
import struct


def add_bridge(source: bytes) -> bytes:
    if len(source) < 112 or len(source) > 64 * 1024 * 1024 or source[:4] != b'HSP3':
        raise ValueError('需要有效的 HSP3 字节码文件')
    if struct.unpack_from('<I', source, 12)[0] != len(source):
        raise ValueError('HSP3 文件大小与头部不符')
    ds, ds_size = struct.unpack_from('<II', source, 24)
    hpi, hpi_size = struct.unpack_from('<IH', source, 80)
    for offset, size in ((ds, ds_size), (hpi, hpi_size)):
        if offset < 112 or offset + size > len(source):
            raise ValueError('HSP3 数据段越界')
    if hpi_size % 16 or hpi_size + 16 > 32767:
        raise ValueError('HSP3 插件表大小无效')
    data = source[ds:ds + ds_size]
    if b'GameQaHsp.dll\0' in data:
        raise ValueError('该字节码已含桥插件名称，不重复注册')
    name = b'GameQaHsp.dll\0'
    entry = b'_hsp3cmdinit@4\0'
    plugins = source[hpi:hpi + hpi_size] + struct.pack('<hhIII', 0, 0, ds_size, ds_size + len(name), 0)
    result = bytearray(source)
    new_ds = len(result)
    result.extend(data + name + entry)
    new_hpi = len(result)
    result.extend(plugins)
    struct.pack_into('<I', result, 12, len(result))
    struct.pack_into('<II', result, 24, new_ds, ds_size + len(name) + len(entry))
    struct.pack_into('<IH', result, 80, new_hpi, len(plugins))
    return bytes(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.source.resolve() == options.output.resolve():
        parser.error('输出必须是新的测试脚本文件，不能覆盖原字节码')
    if options.source.stat().st_size > 64 * 1024 * 1024:
        parser.error('字节码超过 64 MiB 上限')
    content = add_bridge(options.source.read_bytes())
    with options.output.open('xb') as stream:
        stream.write(content)
