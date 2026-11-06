import ast
import json
import sqlite3
import time
import unittest
from pathlib import Path
from types import MethodType,SimpleNamespace
from unittest.mock import Mock
from tests import test_member_memory as fixtures
from tests.test_memory_delivery_wiring import queue_method
from myshadow.memory_delivery import source_for,record,UNSEEN
from myshadow.reply_style import readable_reply


def positive(messages,**kwargs):
    events=json.loads(messages[1]['content'])['events'];e=events[0]
    return json.dumps({'members':[{'member':e['member'],'relationship':{
        'type':'positive','confidence':'high','evidence':[{'id':e['id'],'quote':e['text']}]}}]}),{}


class RelationshipWindows(unittest.TestCase):
    setUp=fixtures.MemberMemoryTests.setUp
    add=fixtures.MemberMemoryTests.add
    run_batch=fixtures.MemberMemoryTests.run_batch
    rows=fixtures.MemberMemoryTests.rows

    def test_message_then_confirmed_exchange_across_batches(self):
        self.clock=int(self.clock//1800)*1800+900
        self.add('这几次和你一起排查问题很开心咱俩配合很顺手')
        self.run_batch(positive)
        self.assertFalse(self.rows('relationships'))
        self.add('今天的问题我已经完整描述清楚了',kind='exchange')
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertEqual(self.rows('relationships')[0]['affinity'],51)
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertEqual(self.rows('relationships')[0]['affinity'],51)

    def test_exchange_then_message_upgrades_only_existing_window(self):
        self.clock=int(self.clock//1800)*1800+900
        self.add('这次我们先一起讨论方案细节',kind='exchange')
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.add('这几次和你一起排查问题很开心咱俩配合很顺手')
        self.run_batch(positive)
        self.assertEqual(self.rows('relationships')[0]['affinity'],51)
        self.assertEqual(self.rows('relationships')[0]['familiarity'],2)

    def test_other_window_or_member_does_not_consume_evidence(self):
        self.clock=int(self.clock//1800)*1800+900
        self.add('这几次和你一起排查问题很开心咱俩配合很顺手');self.run_batch(positive)
        self.add('今天我们一起讨论一下具体方案',kind='exchange',sender='bob')
        self.add('今天我们一起讨论一下具体方案',kind='exchange',created=self.clock+1800)
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertTrue(all(r['affinity']==50 for r in self.rows('relationships')))

    def test_deleted_evidence_cannot_be_applied_later(self):
        self.clock=int(self.clock//1800)*1800+900
        self.add('这几次和你一起排查问题很开心咱俩配合很顺手');self.run_batch(positive)
        with self.store.db() as db:db.execute('DELETE FROM events')
        self.add('今天我们一起讨论一下具体方案',kind='exchange')
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertEqual(self.rows('relationships')[0]['affinity'],50)

    def test_exact_legacy_duplicate_skipped_but_reused_id_is_new(self):
        text='今天我们一起讨论一下具体方案'
        self.store.observe(self.group,self.sender,'reply:131',self.clock,text,'exchange','收到')
        self.store.observe(self.group,self.sender,'reply-v2:131:one',self.clock,text,'exchange','收到')
        self.store.observe(self.group,self.sender,'reply-v2:131:two',self.clock+86400,text,'exchange','收到')
        self.assertEqual(len(self.rows('events')),2)

    def test_pending_evidence_has_no_private_quote_and_respects_version(self):
        self.clock=int(self.clock//1800)*1800+900
        text='这几次和你一起排查问题很开心咱俩配合很顺手'
        self.add(text);self.run_batch(positive)
        with self.store.db() as db:
            saved=db.execute('SELECT proposal FROM relationship_candidates').fetchone()[0]
            self.assertNotIn(text,saved)
            db.execute('UPDATE controls SET version=version+1')
        self.add('今天我们一起讨论一下具体方案',kind='exchange')
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertEqual(self.rows('relationships')[0]['affinity'],50)


class DeliveryReceipts(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status);
            CREATE TABLE memory_reply_actors(reply_id INTEGER PRIMARY KEY,sender);
            CREATE TABLE memory_delivery_seen(reply_id INTEGER PRIMARY KEY);
            CREATE TABLE memory_outbox(id INTEGER PRIMARY KEY,group_id,sender,source,kind,created,text,assistant,
                UNIQUE(group_id,sender,source,kind));''')
        tree=ast.parse((Path(__file__).resolve().parents[1]/'myshadow/bot.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Bot')
        node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='flush_member_memory')
        ns={'delivery_source':source_for,'record_delivery':record,'DELIVERY_UNSEEN':UNSEEN,'readable_reply':readable_reply}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'flush','exec'),ns)
        self.social=Mock();self.social.eligible.return_value=True
        self.bot=SimpleNamespace(state=self.db,social=self.social,memory_v2=True)
        self.bot.queue_member_memory=MethodType(queue_method(),self.bot)
        self.flush=MethodType(ns['flush_member_memory'],self.bot)

    def insert(self,local,created,prompt):
        self.db.execute("INSERT INTO replies VALUES(131,'g','message',?,?,?,'收到','confirmed')",(local,created,prompt))
        self.db.execute("INSERT OR REPLACE INTO memory_reply_actors VALUES(131,'alice')")

    def test_id_reuse_and_legacy_seen_do_not_hide_new_delivery(self):
        self.insert(1,100,'我们先讨论今天的事情')
        self.db.execute('INSERT INTO memory_delivery_seen VALUES(131)')
        self.flush();self.flush()
        self.assertEqual(self.social.observe.call_count,1)
        first=self.social.observe.call_args.args[2]
        self.db.execute('DELETE FROM replies');self.insert(2,200,'我们再讨论明天的事情')
        self.flush();self.flush()
        self.assertEqual(self.social.observe.call_count,2)
        self.assertNotEqual(first,self.social.observe.call_args.args[2])

    def test_identity_changes_for_activity_and_other_conversation(self):
        r=dict(id=1,group_id='g',shard='_activity',local_id=3,created=100,prompt='题目',reply='提示')
        self.assertNotEqual(source_for(r,'a','activity'),source_for({**r,'group_id':'other'},'a','activity'))
        self.assertNotEqual(source_for(r,'a','activity'),source_for({**r,'local_id':4},'a','activity'))

    def test_activity_id_reuse_and_repeated_flush(self):
        self.bot.activities=True
        self.db.executescript('CREATE TABLE activity_outbox(reply_id,inbox_id); CREATE TABLE activity_inbox(id,sender,text,kind);')
        for i in (1,2):
            self.db.execute('DELETE FROM replies');self.db.execute('DELETE FROM activity_outbox');self.db.execute('DELETE FROM activity_inbox')
            self.db.execute("INSERT INTO replies VALUES(1,'g','_activity',?,?,?,'提示','confirmed')",(i,100*i,'活动'))
            self.db.execute("INSERT INTO activity_inbox VALUES(?,'alice','这是一次活动中的提问','message')",(i,))
            self.db.execute('INSERT INTO activity_outbox VALUES(1,?)',(i,))
            self.flush();self.flush()
        self.assertEqual(self.social.observe.call_count,2)
        self.assertTrue(all(c.args[2].startswith('activity-v2:') for c in self.social.observe.call_args_list))

    def test_processed_prefix_cannot_starve_later_deliveries(self):
        for i in range(1,46):
            self.db.execute("INSERT INTO replies VALUES(?,'g','message',?,100,'讨论具体事情','收到','confirmed')",(i,i))
            self.db.execute("INSERT INTO memory_reply_actors VALUES(?,'alice')",(i,))
        self.flush();self.flush();self.flush()
        self.assertEqual(self.social.observe.call_count,45)


if __name__=='__main__':unittest.main()
