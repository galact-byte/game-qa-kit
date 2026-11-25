"""RGSSAD v3 只读索引与解密；写入仅允许无链接的新文件。"""
from array import array
from pathlib import Path
import re
import struct
import sys

from gameqakit.admission import no_links


def table(path):
    path = Path(path)
    no_links(path)
    size = path.stat().st_size
    rows, names = [], set()
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:8] != b'RGSSAD\0\x03':
            raise ValueError('invalid_rgss_archive_header')
        key = (struct.unpack('<I', header[8:])[0] * 9 + 3) & 0xffffffff
        key_bytes = struct.pack('<I', key)
        while True:
            data = stream.read(16)
            if len(data) != 16 or stream.tell() > 16 * 1024 * 1024:
                raise ValueError('invalid_rgss_archive_table')
            offset, length, content_key, name_length = (value ^ key for value in struct.unpack('<IIII', data))
            if offset == 0:
                break
            if len(rows) >= 10000 or not 0 < name_length < 4096 or offset + length > size:
                raise ValueError('invalid_rgss_archive_entry')
            encoded = stream.read(name_length)
            if len(encoded) != name_length:
                raise ValueError('truncated_rgss_archive_name')
            try:
                name = bytes(value ^ key_bytes[i % 4] for i, value in enumerate(encoded)).decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('invalid_rgss_archive_name') from exc
            name = name.replace('\\', '/')
            parts = name.split('/')
            if any(not part or part in ('.', '..') or part.endswith((' ', '.'))
                   or re.search(r'[\x00-\x1f<>:"|?*]', part)
                   or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
                   for part in parts):
                raise ValueError('unsafe_rgss_archive_path')
            folded = name.casefold()
            if folded in names:
                raise ValueError('duplicate_rgss_archive_entry')
            names.add(folded)
            rows.append({'name': name, 'offset': offset, 'size': length, 'key': content_key})
        table_end = stream.tell()
    end = table_end
    for row in sorted(rows, key=lambda item: item['offset']):
        if row['offset'] < end:
            raise ValueError('overlapping_rgss_archive_content')
        end = row['offset'] + row['size']
        parts = row['name'].casefold().split('/')
        if any('/'.join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError('rgss_archive_file_directory_collision')
    return rows


def decrypt(path, row):
    no_links(path)
    with Path(path).open('rb') as stream:
        stream.seek(row['offset'])
        body = stream.read(row['size'])
    if len(body) != row['size']:
        raise ValueError('truncated_rgss_archive_content')
    complete = len(body) // 4 * 4
    words = array('I')
    words.frombytes(body[:complete])
    if sys.byteorder != 'little':
        words.byteswap()
    key = row['key']
    for i, word in enumerate(words):
        words[i] = word ^ key
        key = (key * 7 + 3) & 0xffffffff
    if sys.byteorder != 'little':
        words.byteswap()
    tail = bytes(value ^ struct.pack('<I', key)[i] for i, value in enumerate(body[complete:]))
    return words.tobytes() + tail


def extract(path, destination):
    destination = Path(destination)
    no_links(destination)
    rows = table(path)
    # 完整预检后才写入，防止归档覆盖松散资源或文件目录别名。
    for row in rows:
        target = destination / row['name']
        no_links(target)
        if target.exists() or any(parent.exists() and not parent.is_dir()
                                  for parent in target.parents if parent != destination):
            raise ValueError('rgss_archive_destination_collision')
    for row in rows:
        target = destination / row['name']
        no_links(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(decrypt(path, row))
    return rows
