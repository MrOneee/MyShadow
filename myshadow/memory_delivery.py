"""Durable interaction identity independent of reusable SQLite row IDs."""
import hashlib
import json


def source_for(row, sender, kind='reply'):
    identity=[row[k] for k in ('group_id','shard','local_id','created','prompt','reply')]
    identity.append(sender)
    digest=hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return f'{kind}-v2:{row["id"]}:{digest}'


def record(db, source, row):
    db.execute('INSERT OR IGNORE INTO memory_delivery_receipts VALUES(?,?,?,?,?,?)',
        (source,row['id'],row['group_id'],row['shard'],row['local_id'],row['created']))


UNSEEN = '''NOT EXISTS (SELECT 1 FROM memory_delivery_receipts d
    WHERE d.reply_id=r.id AND d.group_id=r.group_id AND d.shard=r.shard
    AND d.local_id=r.local_id AND d.created=r.created)'''
