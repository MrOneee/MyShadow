import json
import sqlite3
import time
import unittest
from unittest.mock import Mock

import test_poll_changes
import test_participation_decision
from member_memory import MemoryService


class MemberMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_poll_changes.PollChangesTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bot=self.fixture.bot;self.bot.memory_v2=True
        self.bot.state.row_factory=sqlite3.Row
        self.bot.state.executescript('''
            CREATE TABLE memory_outbox(id INTEGER PRIMARY KEY,group_id,sender,source,kind,created,text,assistant,UNIQUE(group_id,sender,source,kind));
            CREATE TABLE memory_reply_actors(reply_id INTEGER PRIMARY KEY,sender);
            CREATE TABLE memory_delivery_seen(reply_id INTEGER PRIMARY KEY);
        ''')
        self.store=MemoryService(self.fixture.temp.name,self.bot.config);self.bot.social=self.store
        with self.store.db() as db:db.execute("UPDATE meta SET value='0' WHERE key='started'")

    def test_poll_transaction_and_ack_replay(self):
        self.fixture.add(1);self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM memory_outbox').fetchone()[0],1)
        self.assertFalse(self.bot.state.in_transaction)
        self.bot.flush_member_memory();self.bot.flush_member_memory()
        self.assertEqual(self.store.stats()['events'],1)

    def test_failed_dispatch_keeps_durable_message(self):
        self.fixture.add(1);self.bot.poll()
        original=self.store.observe;self.store.observe=Mock(side_effect=sqlite3.OperationalError('busy'))
        with self.assertRaises(sqlite3.OperationalError):self.bot.flush_member_memory()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM memory_outbox').fetchone()[0],1)
        self.store.observe=original;self.bot.flush_member_memory()
        self.assertEqual(self.store.stats()['events'],1)

    def test_failed_enqueue_never_skips_message_cursor(self):
        self.fixture.add(1)
        original=self.bot.queue_member_memory
        self.bot.queue_member_memory=Mock(side_effect=sqlite3.OperationalError('busy'))
        with self.assertRaises(sqlite3.OperationalError):self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT local_id FROM cursors').fetchone()[0],0)
        self.bot.queue_member_memory=original;self.bot.poll();self.bot.flush_member_memory()
        self.assertEqual(self.store.stats()['events'],1)

    def test_only_confirmed_delivery_builds_experience(self):
        for n,status in enumerate(('confirmed','failed','uncertain','preview'),1):
            self.bot.state.execute('INSERT INTO replies VALUES(?,?,?,?,?,?,?,?)',(n,self.fixture.group,'shard',n,int(time.time()),'一起讨论一个问题','确认回复',status))
            self.bot.state.execute('INSERT INTO memory_reply_actors VALUES(?,?)',(n,'alice'))
        self.bot.state.commit();self.bot.flush_member_memory();self.bot.flush_member_memory()
        with self.store.db() as db:rows=db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'exchange')

    def test_transaction_rollback_does_not_advance_memory_outbox(self):
        with self.assertRaises(RuntimeError):
            with self.bot.state:
                self.bot.queue_member_memory(self.fixture.group,'alice','sample',time.time(),'我喜欢摄影')
                self.bot.state.execute('INSERT INTO cursors VALUES(?,?,?)',(self.fixture.group,'shard',2))
                raise RuntimeError('crash')
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM memory_outbox').fetchone()[0],0)
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM cursors').fetchone()[0],0)

    def test_confirmed_activity_records_actual_exchange_only(self):
        self.bot.activities=object()
        self.bot.state.executescript('''CREATE TABLE activity_inbox(id INTEGER PRIMARY KEY,sender,text,kind);
            CREATE TABLE activity_outbox(reply_id INTEGER PRIMARY KEY,inbox_id);
            INSERT INTO activity_inbox VALUES(1,'alice','门是锁着的吗','message');
            INSERT INTO activity_outbox VALUES(1,1);''')
        self.bot.state.execute('INSERT INTO replies VALUES(1,?,?,?,?,?,?,?)',(self.fixture.group,'activity',1,int(time.time()),'','是的，门锁着。','confirmed'))
        self.bot.state.commit();self.bot.flush_member_memory()
        with self.store.db() as db:event=db.execute('SELECT * FROM events').fetchone()
        self.assertEqual(event['kind'],'activity');self.assertIn('门锁着',event['text']);self.assertNotIn('获胜',event['text'])

    def test_decision_consumes_same_bounded_context(self):
        f=test_participation_decision.DecisionTests();f.setUp();self.addCleanup(f.doCleanups)
        f.p.memory_context=lambda group,sender:self.store.context(group,sender,compact=True)
        f.add('今天工作总算忙完了');f.advance()
        payload=json.loads(f.ai.request.call_args.args[1]['messages'][1]['content'])
        self.assertIn('relationship',payload['member_memory']);self.assertNotIn('affinity',payload['member_memory'])


if __name__=='__main__':unittest.main()
