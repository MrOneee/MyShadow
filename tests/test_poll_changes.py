"""Skipping unchanged files must never skip queued replies or unread batches."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import myshadow.bot as bot
from myshadow.wechat_db import SnapshotConnection


class PollChangesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.contact = 'contact/contact.db'
        self.shard = 'message/message_0.db'
        self.group = '111@chatroom'
        self.fixtures = {self.contact: sqlite3.connect(':memory:'), self.shard: sqlite3.connect(':memory:')}
        for connection in self.fixtures.values():
            self.addCleanup(connection.close)
        self.fixtures[self.contact].executescript("""
            CREATE TABLE contact(username TEXT, nick_name TEXT, is_in_chat_room INTEGER);
            INSERT INTO contact VALUES('111@chatroom', 'One', 1), ('222@chatroom', 'Two', 0);
        """)
        self.fixtures[self.shard].executescript("""
            CREATE TABLE Name2Id(user_name TEXT);
            INSERT INTO Name2Id VALUES('wxid_a'),('wxid_bot');
        """)
        for group in (self.group, '222@chatroom'):
            self.fixtures[self.shard].execute('CREATE TABLE ' + bot.table_for(group) + '''(
                local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER,
                real_sender_id INTEGER, create_time INTEGER, source TEXT, message_content TEXT)''')
        self.fixtures[self.shard].commit()
        self.revisions = {self.contact: ('contact', 1), self.shard: ('message', 1)}
        self.opened = []
        self.bot = bot.Bot.__new__(bot.Bot)
        self.bot.groups = {}
        self.bot._contact_scan = None
        self.bot._message_scans = {}
        self.bot.config = {'mode': 'preview', 'max_age_seconds': 300, 'bot_id': 'wxid_bot', 'bot_name': 'Bot'}
        self.bot.state = sqlite3.connect(':memory:')
        self.addCleanup(self.bot.state.close)
        self.bot.state.executescript('''
            CREATE TABLE cursors(group_id TEXT, shard TEXT, local_id INTEGER, PRIMARY KEY(group_id,shard));
            CREATE TABLE replies(id INTEGER PRIMARY KEY, group_id TEXT, shard TEXT, local_id INTEGER,
                created INTEGER, prompt TEXT, reply TEXT, status TEXT, UNIQUE(group_id,shard,local_id));
        ''')
        self.bot.process_group = Mock()
        base = Path('/fake/db_storage')
        for patcher in (
            patch.object(bot, 'ROOT', Path(self.temp.name)),
            patch.object(bot, 'databases', return_value=(base, [base / self.shard])),
            patch.object(bot, 'database_revision', side_effect=lambda rel: self.revisions[rel]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(bot, 'snapshot', side_effect=self.open_snapshot)
        self.snapshot = patcher.start()
        self.addCleanup(patcher.stop)

    def open_snapshot(self, rel):
        connection = sqlite3.connect(':memory:', factory=SnapshotConnection)
        connection.deserialize(self.fixtures[rel].serialize())
        connection.row_factory = sqlite3.Row
        connection.revision = self.revisions[rel]
        self.opened.append(connection)
        return connection

    def add(self, count, group=None, sender=1):
        c = self.fixtures[self.shard]
        table = bot.table_for(group or self.group)
        start = c.execute('SELECT coalesce(max(local_id),0) FROM ' + table).fetchone()[0]
        for identifier in range(start + 1, start + count + 1):
            c.execute('INSERT INTO ' + table + ' VALUES(?,?,?,?,?,?,?)',
                      (identifier, identifier, 1, sender, int(time.time()),
                       '<msgsource><atuserlist>wxid_bot</atuserlist></msgsource>', '@Bot hello'))
        c.commit()

    def count_snapshots(self, rel):
        return sum(call.args == (rel,) for call in self.snapshot.call_args_list)

    def test_unchanged_files_skip_snapshots_but_process_queue(self):
        self.add(1)
        self.bot.poll()
        self.bot.poll()
        self.assertEqual(self.count_snapshots(self.contact), 1)
        self.assertEqual(self.count_snapshots(self.shard), 1)
        self.assertEqual(self.bot.process_group.call_count, 2)
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 1)
        for connection in self.opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_unchanged_files_still_drive_scheduler(self):
        self.bot.config['mode'] = 'send'
        self.bot.ui = Mock()
        self.bot.scheduler = Mock()
        self.bot.run_scheduled = Mock()
        self.bot.poll()
        self.bot.poll()
        self.assertEqual(self.bot.run_scheduled.call_count, 2)
        self.bot.run_scheduled.assert_called_with((self.group,))

    def test_changed_wal_scans_again(self):
        self.bot.poll()
        self.add(1)
        self.revisions[self.shard] = ('message', 2)
        self.bot.poll()
        self.assertEqual(self.count_snapshots(self.shard), 2)
        self.assertEqual(self.count_snapshots(self.contact), 1)
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 1)

    def test_backlog_drains_even_when_file_stops_changing(self):
        self.add(205)
        for expected in (100, 200, 205, 205):
            self.bot.poll()
            self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], expected)
        self.assertEqual(self.count_snapshots(self.shard), 3)

    def test_new_group_is_scanned_even_with_unchanged_messages(self):
        self.add(1, group='222@chatroom')
        self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 0)
        self.fixtures[self.contact].execute("UPDATE contact SET is_in_chat_room=1 WHERE username='222@chatroom'")
        self.fixtures[self.contact].commit()
        self.revisions[self.contact] = ('contact', 2)
        self.bot.poll()
        self.assertEqual(self.count_snapshots(self.shard), 2)
        self.assertEqual(self.bot.state.execute('SELECT group_id FROM replies').fetchone()[0], '222@chatroom')

    def test_failed_scan_is_retried_without_file_change(self):
        self.add(1)
        with patch.object(bot, 'prompt_for', side_effect=RuntimeError('read failed')):
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                self.bot.poll()
        self.bot.poll()
        self.assertEqual(self.count_snapshots(self.shard), 2)
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 1)

    def test_failed_reply_is_retried_without_new_message(self):
        self.add(1)
        self.bot.process_group.side_effect = [RuntimeError('API failed'), None]
        self.assertIn(self.group, self.bot.poll())
        self.assertEqual(self.bot.poll(), {})
        self.assertEqual(self.bot.process_group.call_count, 2)
        self.assertEqual(self.count_snapshots(self.shard), 1)

    def test_delivery_confirmation_always_gets_new_snapshot(self):
        self.bot.poll()
        self.add(1, sender=2)
        self.assertEqual(self.bot.delivery_matches(self.group, '@Bot hello', 0), [1])
        self.assertEqual(self.count_snapshots(self.shard), 2)

    def test_selective_call_is_processed_after_debounce_without_db_change(self):
        from myshadow.selective_reply import SelectiveReply
        self.bot.state.row_factory = sqlite3.Row
        self.bot.ai = Mock(config={'model': 'test'})
        self.bot.selective = SelectiveReply(self.bot.state, self.bot.ai, {'groups': [self.group]}, 'wxid_bot')
        self.add(1)
        self.fixtures[self.shard].execute('UPDATE ' + bot.table_for(self.group) +
            " SET message_content='wxid_a:\n影，你好',source='' WHERE local_id=1")
        self.fixtures[self.shard].commit()
        now=time.time()
        with patch('myshadow.selective_reply.time.time', return_value=now):
            self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 0)
        with patch('myshadow.selective_reply.time.time', return_value=now+3):
            self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0], 1)
        self.assertEqual(self.count_snapshots(self.shard), 1)
        self.bot.ai.request.assert_not_called()
