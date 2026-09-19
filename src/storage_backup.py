"""Validate GPT and save two-pass verified backups; no device write API."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zlib

SECTOR = 512
WANTED = {'nvram', 'nvdata', 'persist', 'protect1', 'protect2', 'protect_f',
          'protect_s', 'proinfo', 'seccfg'}


def exact_read(read, address, size):
    data = read(address, size)
    if not isinstance(data, (bytes, bytearray)) or len(data) != size:
        raise RuntimeError(f'Incomplete storage read at {address:#x}: expected {size}')
    return bytes(data)


def read_gpt(read, flash_size):
    header = exact_read(read, SECTOR, SECTOR)
    if header[:8] != b'EFI PART':
        raise ValueError('Missing GPT signature')
    revision, size, crc = struct.unpack_from('<III', header, 8)
    if revision != 0x10000 or not 92 <= size <= SECTOR:
        raise ValueError('Unsupported GPT header')
    checked = bytearray(header[:size])
    checked[16:20] = bytes(4)
    if zlib.crc32(checked) & 0xffffffff != crc:
        raise ValueError('GPT header CRC mismatch')
    current, backup, first, last = struct.unpack_from('<QQQQ', header, 24)
    lba, count, entry_size, entries_crc = struct.unpack_from('<QIII', header, 72)
    disk_sectors = flash_size // SECTOR
    if current != 1 or not 1 < backup < disk_sectors or not 2 <= first <= last < backup:
        raise ValueError('GPT disk bounds invalid')
    length = count * entry_size
    if not 128 <= entry_size <= 4096 or entry_size % 128 or not 1 <= count <= 4096 or length > 16 * 1024 * 1024:
        raise ValueError('GPT table size invalid')
    rounded = (length + SECTOR - 1) // SECTOR * SECTOR
    if lba < 2 or lba * SECTOR + rounded > first * SECTOR:
        raise ValueError('GPT table overlaps usable storage')
    table = exact_read(read, lba * SECTOR, rounded)
    if zlib.crc32(table[:length]) & 0xffffffff != entries_crc:
        raise ValueError('GPT table CRC mismatch')
    entries = []
    names = set()
    for offset in range(0, length, entry_size):
        entry = table[offset:offset + entry_size]
        if entry[:16] == bytes(16):
            continue
        start, end = struct.unpack_from('<QQ', entry, 32)
        name = entry[56:128].decode('utf-16-le').split('\0', 1)[0]
        if name in names or not first <= start <= end <= last:
            raise ValueError('Duplicate name or invalid partition bounds')
        names.add(name)
        entries.append({'name': name, 'offset': start * SECTOR, 'length': (end - start + 1) * SECTOR})
    ordered = sorted(entries, key=lambda p: p['offset'])
    if any(a['offset'] + a['length'] > b['offset'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping GPT partitions')
    return entries, header, table


def save_backups(read, entries, output):
    output = Path(output)
    selected = [e for e in entries if e['name'] in WANTED]
    if not {'nvram', 'nvdata', 'seccfg'} <= {e['name'] for e in selected}:
        raise ValueError('Required radio/security partitions absent; inspect GPT before proceeding')
    if any(e['length'] > 256 * 1024 * 1024 for e in selected):
        raise ValueError('Unexpectedly large backup partition')
    required = sum(e['length'] for e in selected)
    if shutil.disk_usage(output).free < required + 128 * 1024 * 1024:
        raise OSError('Insufficient local space for backups')
    manifest = []
    for entry in selected:
        target = output / (entry['name'] + '.bin')
        partial = target.with_suffix('.partial')
        first = hashlib.sha256()
        with partial.open('xb') as handle:
            for offset in range(0, entry['length'], 1024 * 1024):
                data = exact_read(read, entry['offset'] + offset, min(1024 * 1024, entry['length'] - offset))
                handle.write(data)
                first.update(data)
        second = hashlib.sha256()
        for offset in range(0, entry['length'], 1024 * 1024):
            second.update(exact_read(read, entry['offset'] + offset, min(1024 * 1024, entry['length'] - offset)))
        local = hashlib.sha256()
        with partial.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                local.update(chunk)
        if first.digest() != second.digest() or first.digest() != local.digest():
            raise RuntimeError('Backup verification failed: ' + entry['name'])
        if target.exists():
            raise FileExistsError(target)
        partial.rename(target)
        manifest.append(dict(entry, sha256=first.hexdigest(), verified_reads=2))
        (output / 'backup-manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest
