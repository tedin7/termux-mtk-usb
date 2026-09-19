import struct
import tempfile
import unittest
from pathlib import Path
import zlib

from storage_backup import read_gpt, save_backups


def disk_image():
    disk = bytearray(128 * 512)
    table = bytearray(512)
    for index, name in enumerate(('nvram', 'nvdata', 'seccfg')):
        offset = index * 128
        table[offset:offset + 16] = b'T' * 16
        start = 10 + index * 3
        struct.pack_into('<QQ', table, offset + 32, start, start + 1)
        raw = name.encode('utf-16-le')
        table[offset + 56:offset + 56 + len(raw)] = raw
    header = bytearray(512)
    header[:8] = b'EFI PART'
    struct.pack_into('<III', header, 8, 0x10000, 92, 0)
    struct.pack_into('<QQQQ', header, 24, 1, 127, 3, 126)
    struct.pack_into('<QIII', header, 72, 2, 4, 128, zlib.crc32(table))
    struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
    disk[512:1024] = header
    disk[1024:1536] = table
    return disk


class StorageTests(unittest.TestCase):
    def test_valid_gpt_and_verified_backups(self):
        disk = disk_image()
        read = lambda addr, size: bytes(disk[addr:addr + size])
        entries, _, _ = read_gpt(read, len(disk))
        with tempfile.TemporaryDirectory() as tmp:
            manifest = save_backups(read, entries, tmp)
            self.assertEqual(len(manifest), 3)
            self.assertTrue(all(e['verified_reads'] == 2 for e in manifest))
            self.assertEqual((Path(tmp) / 'nvram.bin').stat().st_size, 1024)

    def test_corrupted_header_rejected(self):
        disk = disk_image()
        disk[512 + 40] ^= 1
        with self.assertRaisesRegex(ValueError, 'header CRC'):
            read_gpt(lambda a, n: disk[a:a+n], len(disk))

    def test_corrupted_entries_rejected(self):
        disk = disk_image()
        disk[1024 + 56] ^= 1
        with self.assertRaisesRegex(ValueError, 'table CRC'):
            read_gpt(lambda a, n: disk[a:a+n], len(disk))

    def test_truncated_storage_read_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            read_gpt(lambda a, n: bytes(n - 1), 65536)

    def test_backup_second_read_mismatch_never_finalized(self):
        entries = [{'name': n, 'offset': i * 512, 'length': 512}
                   for i, n in enumerate(('nvram', 'nvdata', 'seccfg'))]
        calls = 0
        def changing_read(a, n):
            nonlocal calls
            calls += 1
            return bytes([calls]) * n
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                save_backups(changing_read, entries, tmp)
            self.assertFalse((Path(tmp) / 'nvram.bin').exists())
            self.assertTrue((Path(tmp) / 'nvram.partial').exists())

    def test_missing_required_partitions_stops_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'Required'):
                save_backups(lambda a, n: self.fail('must not read'), [], tmp)


if __name__ == '__main__':
    unittest.main()
