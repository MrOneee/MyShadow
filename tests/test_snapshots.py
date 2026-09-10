"""Encrypted page/WAL fixtures exercise snapshot lifetime and consistency."""
import hashlib
import hmac
import io
import json
import os
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from myshadow import wechat_db as db


def plain_database(value='base'):
    """Two valid SQLite pages with SQLCipher's 80 reserved bytes per page."""
    first = bytearray(db.PAGE)
    first[:16] = b'SQLite format 3\0'
    struct.pack_into('>H', first, 16, db.PAGE)
    first[18:24] = bytes([1, 1, 80, 64, 32, 32])
    for offset, number in ((24, 1), (28, 2), (40, 1), (44, 4), (56, 1), (92, 1), (96, 3040000)):
        struct.pack_into('>I', first, offset, number)
    sql = b'CREATE TABLE t(x)'
    record = bytes([6, 23, 15, 15, 1, 13 + 2 * len(sql)]) + b'tablett\x02' + sql
    leaf_cell(first, 100, record)
    second = bytearray(db.PAGE)
    value = value.encode()
    leaf_cell(second, 0, bytes([2, 13 + 2 * len(value)]) + value)
    return bytes(first), bytes(second)


def leaf_cell(page, header, record):
    cell = bytes([len(record), 1]) + record
    position = 4016 - len(cell)
    page[header] = 13
    struct.pack_into('>HHH', page, header + 1, 0, 1, position)
    struct.pack_into('>H', page, header + 8, position)
    page[position:4016] = cell


KEY, SALT = b'k' * 32, b's' * 16


def encrypted(page, number):
    offset = 16 if number == 1 else 0
    iv = bytes([number]) * 16
    cipher = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    prefix = SALT if number == 1 else b''
    data = prefix + cipher.update(page[offset:4016]) + cipher.finalize() + iv
    tag = hmac.new(db.mac_key(KEY, SALT), data[offset:] + struct.pack('<I', number), hashlib.sha512).digest()
    return data + tag


def make_wal(frames):
    header = struct.pack('>IIIIII', 0x377f0682, 3007000, db.PAGE, 0, 13, 17)
    state = db.checksum(header)
    result = bytearray(header + struct.pack('>II', *state))
    for number, size, page in frames:
        frame = struct.pack('>IIII', number, size, 13, 17)
        state = db.checksum(frame[:8] + page, state)
        result.extend(frame + struct.pack('>II', *state) + page)
    return bytes(result)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rel = 'message/message_0.db'
        self.base = self.root / 'data/wechat/xwechat_files/wxid_test_1234/db_storage'
        self.source = self.base / self.rel
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b''.join(encrypted(page, i) for i, page in enumerate(plain_database(), 1)))
        self.wal = Path(str(self.source) + '-wal')
        (self.root / 'database_keys.json').write_text(json.dumps({'account': self.base.parent.name, 'keys': {self.rel: KEY.hex()}}))
        patcher = patch.object(db, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_snapshot_reads_and_closes(self):
        with db.snapshot(self.rel) as connection:
            self.assertEqual(connection.execute('SELECT x FROM t').fetchone()[0], 'base')
            self.assertEqual(connection.revision, db.database_revision(self.rel))
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO t VALUES('forbidden')")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_exception_closes_connection(self):
        with self.assertRaisesRegex(ValueError, 'caller failed'):
            with db.snapshot(self.rel) as connection:
                raise ValueError('caller failed')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_only_last_committed_version_of_each_page_is_used(self):
        frames = [(2, 2, encrypted(plain_database(value)[1], 2)) for value in ('first', 'committed')]
        frames.append((2, 0, encrypted(plain_database('uncommitted')[1], 2)))
        self.wal.write_bytes(make_wal(frames))
        with db.snapshot(self.rel) as connection:
            self.assertEqual(connection.execute('SELECT x FROM t').fetchone()[0], 'committed')

    def test_wal_can_grow_database(self):
        first, second = plain_database('grown')
        self.source.write_bytes(encrypted(first, 1))
        self.wal.write_bytes(make_wal([(2, 2, encrypted(second, 2))]))
        with db.snapshot(self.rel) as connection:
            self.assertEqual(connection.execute('SELECT x FROM t').fetchone()[0], 'grown')

    def test_incomplete_tail_is_not_applied(self):
        valid = make_wal([(2, 2, encrypted(plain_database('committed')[1], 2))])
        self.wal.write_bytes(valid + bytes(24) + bytes(200))
        with db.snapshot(self.rel) as connection:
            self.assertEqual(connection.execute('SELECT x FROM t').fetchone()[0], 'committed')

    def test_bad_page_authentication_fails(self):
        data = bytearray(self.source.read_bytes())
        data[100] ^= 1
        self.source.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, 'authentication'):
            db.snapshot(self.rel)

    def test_change_during_read_fails_without_publishing_snapshot(self):
        revision = db.database_revision(self.rel)
        with patch.object(db, 'database_revision', side_effect=[revision, ('changed',)]):
            with self.assertRaisesRegex(RuntimeError, 'changed during snapshot'):
                db.snapshot(self.rel)

    def test_streaming_path_does_not_read_entire_files(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file copy')):
            with db.snapshot(self.rel) as connection:
                self.assertEqual(connection.execute('SELECT x FROM t').fetchone()[0], 'base')

    def test_wal_change_and_removal_change_revision(self):
        before = db.database_revision(self.rel)
        self.wal.write_bytes(make_wal([]))
        after = db.database_revision(self.rel)
        self.assertNotEqual(before, after)
        self.wal.unlink()
        self.assertNotEqual(after, db.database_revision(self.rel))

    def test_file_replacement_with_same_size_and_mtime_invalidates_revision(self):
        before = db.database_revision(self.rel)
        times = self.source.stat()
        replacement = self.source.with_suffix('.replacement')
        replacement.write_bytes(self.source.read_bytes())
        os.utime(replacement, ns=(times.st_atime_ns, times.st_mtime_ns))
        replacement.replace(self.source)
        self.assertNotEqual(before, db.database_revision(self.rel))

    def test_wal_commit_can_shrink_database(self):
        first = bytearray(plain_database()[0])
        struct.pack_into('>I', first, 28, 1)
        first[100:4016] = bytes(3916)
        first[100] = 13
        struct.pack_into('>H', first, 105, 4016)
        self.wal.write_bytes(make_wal([(1, 1, encrypted(first, 1))]))
        with db.snapshot(self.rel) as connection:
            self.assertEqual(connection.execute('PRAGMA page_count').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)

    def test_key_file_changes_invalidate_revision(self):
        before = db.database_revision(self.rel)
        (self.root / 'database_keys.json').write_text('{}')
        self.assertNotEqual(before, db.database_revision(self.rel))

    def test_other_account_is_rejected(self):
        (self.root / 'database_keys.json').write_text(json.dumps({'account': 'other', 'keys': {}}))
        with self.assertRaisesRegex(RuntimeError, 'Account changed'):
            db.snapshot(self.rel)

    def test_invalid_wal_header_rejected(self):
        self.wal.write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError, 'Incomplete WAL header'):
            db.snapshot(self.rel)

    def test_uncommitted_corrupt_tail_matches_existing_parser(self):
        wal = bytearray(make_wal([(2, 2, encrypted(plain_database()[1], 2))]))
        wal[60] ^= 1
        offsets, size = db.wal_page_offsets(io.BytesIO(wal))
        self.assertEqual((offsets, size), ({}, None))
        self.assertEqual(db.committed_frames(wal), ([], None))
