"""为固定 KAG 的原生 patch.xp3 通路生成 Unicode 文本覆盖；不改原归档。"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import zlib

MAGIC = b'XP3\r\n \n\x1a\x8bg\x01'
SCRIPT_SUFFIXES = {'.tjs', '.ks'}
MAX_INDEX = 16 * 1024 * 1024
MAX_TEXT = 16 * 1024 * 1024


def _chunk(tag, data):
    return tag + struct.pack('<Q', len(data)) + data


def patch_bytes(files):
    """严格解码 CP932，仅覆盖编码发生变化的文本，保留全部字符和换行。"""
    body, index, records = bytearray(), bytearray(), []
    seen, source_names = set(), set()
    for name, original in sorted(files.items()):
        if (not name or name.startswith(('/', '\\')) or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in name.split('/'))
                or name.casefold() in source_names):
            raise ValueError('覆盖条目名称无效或重复')
        source_names.add(name.casefold())
        patch_name = name.rsplit('/', 1)[-1]
        if patch_name.casefold() in seen:
            raise ValueError('覆盖归档的平面检索名称冲突')
        seen.add(patch_name.casefold())
        if original.startswith(b'\xff\xfe'):
            original[2:].decode('utf-16le', errors='strict')
            continue
        if original.startswith((b'\xfe\xff', b'\xfe\xfe', b'\xef\xbb\xbf')) or b'\0' in original:
            raise ValueError('覆盖输入不是已支持的 CP932 或 UTF-16LE 文本：' + name)
        text = original.decode('cp932', errors='strict')
        if text.isascii():
            continue
        data = b'\xff\xfe' + text.encode('utf-16le')
        # KAG registers the patch archive root; its lookup does not recurse into folders.
        encoded = patch_name.encode('utf-16le')
        entry = _chunk(b'info', struct.pack('<LQQH', 0, len(data), len(data), len(encoded) // 2) + encoded)
        entry += _chunk(b'segm', struct.pack('<LQQQ', 0, 19 + len(body), len(data), len(data)))
        entry += _chunk(b'adlr', struct.pack('<L', zlib.adler32(data)))
        index += _chunk(b'File', entry)
        body += data
        records.append({'name': name, 'patch_name': patch_name, 'source_sha256': hashlib.sha256(original).hexdigest(),
                        'unicode_sha256': hashlib.sha256(data).hexdigest(), 'unicode_bytes': len(data)})
    if not records:
        raise ValueError('归档没有需要转换的 CP932 文本')
    payload = MAGIC + struct.pack('<Q', 19 + len(body)) + body + b'\x00' + struct.pack('<Q', len(index)) + index
    return bytes(payload), records


def write_patch(destination, files):
    payload, records = patch_bytes(files)
    with Path(destination).open('xb') as stream:
        stream.write(payload)
    return {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'entries': records}


def _chunks(data):
    pos = 0
    while pos < len(data):
        if len(data) - pos < 12:
            raise ValueError('XP3 索引块被截断')
        tag, size = data[pos:pos + 4], struct.unpack_from('<Q', data, pos + 4)[0]
        pos += 12
        if size > len(data) - pos:
            raise ValueError('XP3 索引块长度越界')
        yield tag, data[pos:pos + size]
        pos += size


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('XP3 数据被截断')
    return data


def _inflate(data, size):
    decoder = zlib.decompressobj()
    result = decoder.decompress(data, size + 1)
    if len(result) != size or not decoder.eof or decoder.unused_data:
        raise ValueError('XP3 解压长度不符')
    return result


def archive_scripts(path):
    """读取调用者已验证摘要的固定归档；不提取到文件系统，不处理加密。"""
    with Path(path).open('rb') as stream:
        if _read(stream, 11) != MAGIC:
            raise ValueError('不是 XP3 归档')
        offset = struct.unpack('<Q', _read(stream, 8))[0]
        stream.seek(offset)
        kind = _read(stream, 1)[0]
        if kind == 0x80:
            _read(stream, 8)
            stream.seek(struct.unpack('<Q', _read(stream, 8))[0])
            kind = _read(stream, 1)[0]
        if kind not in (0, 1):
            raise ValueError('不支持此 XP3 索引类型')
        packed = struct.unpack('<Q', _read(stream, 8))[0]
        size = struct.unpack('<Q', _read(stream, 8))[0] if kind else packed
        if max(packed, size) > MAX_INDEX:
            raise ValueError('XP3 索引过大')
        index = _read(stream, packed)
        if kind:
            index = _inflate(index, size)
        files = {}
        for tag, entry in _chunks(index):
            if tag != b'File':
                continue
            fields = dict(_chunks(entry))
            info = fields.get(b'info', b'')
            if len(info) < 22:
                raise ValueError('XP3 条目信息缺失')
            flags, raw_size, _, count = struct.unpack_from('<LQQH', info)
            if len(info) != 22 + count * 2:
                raise ValueError('XP3 条目名称长度不符')
            name = info[22:].decode('utf-16le')
            if Path(name).suffix.lower() not in SCRIPT_SUFFIXES:
                continue
            if raw_size > MAX_TEXT or flags & ~0x80000000:
                raise ValueError('XP3 脚本类型或长度不受支持')
            segments, checksum = fields.get(b'segm', b''), fields.get(b'adlr', b'')
            if not segments or len(segments) % 28 or len(checksum) != 4:
                raise ValueError('XP3 脚本段或校验缺失')
            parts, total = [], 0
            for at in range(0, len(segments), 28):
                method, offset, length, packed = struct.unpack_from('<LQQQ', segments, at)
                total += length
                if method not in (0, 1) or total > raw_size or packed > MAX_TEXT:
                    raise ValueError('XP3 脚本段超出支持范围')
                stream.seek(offset)
                data = _read(stream, packed)
                data = _inflate(data, length) if method else data
                if len(data) != length:
                    raise ValueError('XP3 脚本段长度不符')
                parts.append(data)
            data = b''.join(parts)
            if len(data) != raw_size or zlib.adler32(data) != struct.unpack('<L', checksum)[0]:
                raise ValueError('XP3 脚本校验不符，不处理加密归档')
            if name in files:
                raise ValueError('XP3 脚本名称重复')
            files[name] = data
    return files
