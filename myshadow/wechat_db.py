"""Read-only snapshots of this project's WeChat SQLCipher databases.

SQLCipher format reference: linux-wechat-agent/tools/wechat-decrypt.
Original databases are never modified. Keys and snapshots remain in the project.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import struct
import subprocess
from contextlib import ExitStack
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parents[1]
PAGE = 4096


class SnapshotConnection(sqlite3.Connection):
    """A read-only snapshot owns its memory until its scope exits or close()."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def private_json(path, value):
    os.umask(0o077)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        owner = path.stat() if path.exists() else path.parent.stat()
        os.chown(temp, owner.st_uid, owner.st_gid)
    temp.chmod(0o600)
    temp.replace(path)


def databases():
    accounts = list((ROOT / 'data/wechat/xwechat_files').glob('*/db_storage'))
    if len(accounts) != 1:
        raise RuntimeError('Expected exactly one logged-in account')
    base = accounts[0]
    paths = [base / 'contact/contact.db', base / 'session/session.db']
    paths += sorted((base / 'message').glob('message_[0-9]*.db'))
    return base, paths


def mac_key(key, salt):
    return hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in salt), 2, 32)


def valid_page(page, number, mac):
    offset = 16 if number == 1 else 0
    expected = hmac.new(mac, page[offset:4032] + struct.pack('<I', number), hashlib.sha512).digest()
    return hmac.compare_digest(expected, page[4032:])


def extract_keys():
    base, paths = databases()
    pages = {str(p.relative_to(base)): p.open('rb').read(PAGE) for p in paths}
    remaining = set(pages)
    found = {}
    if Path('/.dockerenv').exists():
        pids = []
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    if (entry / 'comm').read_text().strip() == 'wechat':
                        pids.append(int(entry.name))
                except (OSError, ProcessLookupError):
                    continue
    else:
        container = subprocess.check_output(
            ['bash', str(ROOT / 'compose.sh'), 'ps', '-q', 'desktop'], text=True).strip()
        if not container:
            raise RuntimeError('Project desktop container is not running')
        rows = subprocess.check_output(['docker', 'top', container, '-eo', 'pid,comm'], text=True).splitlines()[1:]
        pids = [int(row.split()[0]) for row in rows if row.split()[1] == 'wechat']
    if not pids:
        raise RuntimeError('Project WeChat process not found')
    pattern = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    tried = set()
    for pid in pids:
        if Path(f'/proc/{pid}/comm').read_text().strip() != 'wechat':
            continue
        with open(f'/proc/{pid}/mem', 'rb', buffering=0) as mem:
            for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
                fields = line.split()
                if not fields[1].startswith('rw'):
                    continue
                if len(fields) > 5 and fields[5] != '[heap]':
                    continue
                start, end = (int(s, 16) for s in fields[0].split('-'))
                tail = b''
                for offset in range(start, end, 1024 * 1024):
                    try:
                        mem.seek(offset)
                        chunk = tail + mem.read(min(1024 * 1024, end - offset))
                    except OSError:
                        break
                    for match in pattern.finditer(chunk):
                        key = bytes.fromhex(match[1][:64].decode())
                        if key in tried:
                            continue
                        tried.add(key)
                        for rel in list(remaining):
                            page = pages[rel]
                            if valid_page(page, 1, mac_key(key, page[:16])):
                                found[rel] = key.hex()
                                remaining.remove(rel)
                    tail = chunk[-200:]
                    if not remaining:
                        break
                if not remaining:
                    break
    if remaining:
        raise RuntimeError('Missing database keys for: ' + ', '.join(sorted(remaining)))
    private_json(ROOT / 'database_keys.json', {'account': base.parent.name, 'keys': found})
    print(json.dumps({'database_keys_ready': len(found)}))


def checksum(data, state=(0, 0), endian='<'):
    s0, s1 = state
    words = struct.unpack(endian + str(len(data) // 4) + 'I', data)
    for a, b in zip(words[::2], words[1::2]):
        s0 = (s0 + a + s1) & 0xffffffff
        s1 = (s1 + b + s0) & 0xffffffff
    return s0, s1


def committed_frames(wal):
    if not wal:
        return [], None
    if len(wal) < 32:
        raise RuntimeError('Incomplete WAL header; retry snapshot')
    magic, version, size = struct.unpack('>III', wal[:12])
    if magic not in (0x377f0682, 0x377f0683) or size != PAGE:
        raise RuntimeError('Unsupported WAL format')
    endian = '<' if magic == 0x377f0682 else '>'
    state = checksum(wal[:24], endian=endian)
    if state != struct.unpack('>II', wal[24:32]):
        raise RuntimeError('Invalid WAL header checksum')
    frames, committed, final_size = [], 0, None
    for offset in range(32, len(wal) - (24 + PAGE) + 1, 24 + PAGE):
        header = wal[offset:offset + 24]
        page = wal[offset + 24:offset + 24 + PAGE]
        if header[8:16] != wal[16:24]:
            break
        state = checksum(header[:8] + page, state, endian)
        if state != struct.unpack('>II', header[16:24]):
            break
        number, db_size = struct.unpack('>II', header[:8])
        if not number:
            break
        frames.append((number, page))
        if db_size:
            committed, final_size = len(frames), db_size
    return frames[:committed], final_size


def stamp(path):
    try:
        s = path.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    except FileNotFoundError:
        return None


def database_revision(rel):
    """Cheap invalidation token; includes account, WAL, replacements and keys."""
    base, allowed = databases()
    source = base / rel
    if source not in allowed:
        raise RuntimeError('Database is outside the message/contact allowlist')
    return (str(source), stamp(source), stamp(Path(str(source) + '-wal')),
            stamp(ROOT / 'database_keys.json'))


def wal_page_offsets(stream):
    """Index committed WAL pages without retaining page contents in Python."""
    header = stream.read(32)
    if not header:
        return {}, None
    if len(header) != 32:
        raise RuntimeError('Incomplete WAL header; retry snapshot')
    magic, version, size = struct.unpack('>III', header[:12])
    if magic not in (0x377f0682, 0x377f0683) or size != PAGE:
        raise RuntimeError('Unsupported WAL format')
    endian = '<' if magic == 0x377f0682 else '>'
    state = checksum(header[:24], endian=endian)
    if state != struct.unpack('>II', header[24:]):
        raise RuntimeError('Invalid WAL header checksum')
    committed, pending, final_size = {}, {}, None
    while True:
        frame = stream.read(24)
        offset = stream.tell()
        page = stream.read(PAGE) if len(frame) == 24 else b''
        if len(page) != PAGE or frame[8:16] != header[16:24]:
            break
        state = checksum(frame[:8] + page, state, endian)
        if state != struct.unpack('>II', frame[16:]):
            break
        number, db_size = struct.unpack('>II', frame[:8])
        if not number:
            break
        pending[number] = offset
        if db_size:
            committed.update(pending)
            pending.clear()
            final_size = db_size
    return committed, final_size


def snapshot(rel):
    before = database_revision(rel)
    source = Path(before[0])
    base, _ = databases()
    key_config = json.loads((ROOT / 'database_keys.json').read_text())
    if key_config['account'] != base.parent.name:
        raise RuntimeError('Account changed; reconfigure explicitly')
    key = bytes.fromhex(key_config['keys'][rel])
    wal_path = Path(str(source) + '-wal')
    with ExitStack() as stack:
        db = stack.enter_context(source.open('rb'))
        size = os.fstat(db.fileno()).st_size
        if not size or size % PAGE:
            raise RuntimeError('Invalid encrypted database length')
        mac = mac_key(key, db.read(16))
        try:
            wal = stack.enter_context(wal_path.open('rb'))
        except FileNotFoundError:
            wal = None
        offsets, final_size = wal_page_offsets(wal) if wal else ({}, None)
        count = final_size if final_size is not None else size // PAGE
        if count > size // PAGE + len(offsets):
            raise RuntimeError('Invalid WAL database size')
        result = bytearray(count * PAGE)
        empty = bytes(PAGE)
        for number in range(1, count + 1):
            if number in offsets:
                wal.seek(offsets[number])
                page = wal.read(PAGE)
            else:
                db.seek((number - 1) * PAGE)
                page = db.read(PAGE)
            if len(page) != PAGE:
                raise RuntimeError('Incomplete database page; retry snapshot')
            if page == empty:
                continue
            if not valid_page(page, number, mac):
                raise RuntimeError('Database page authentication failed')
            offset = 16 if number == 1 else 0
            decryptor = Cipher(algorithms.AES(key), modes.CBC(page[4016:4032])).decryptor()
            decoded = decryptor.update(page[offset:4016]) + decryptor.finalize()
            start = (number - 1) * PAGE
            if number == 1:
                result[:16] = b'SQLite format 3\0'
            result[start + offset:start + 4016] = decoded
        if before != database_revision(rel):
            raise RuntimeError('Database changed during snapshot; retry')
    # Snapshot has its committed WAL merged; use rollback mode in this memory copy.
    result[18:20] = bytes([1, 1])
    connection = sqlite3.connect(':memory:', factory=SnapshotConnection)
    try:
        # deserialize copies into SQLite; pass the buffer directly, not bytes(result).
        connection.deserialize(result)
        connection.execute('PRAGMA query_only=ON')
        connection.row_factory = sqlite3.Row
        connection.revision = before
    except BaseException:
        connection.close()
        raise
    return connection


if __name__ == '__main__':
    extract_keys()
