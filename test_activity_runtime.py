import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from activity_runtime.registry import Registry
from activity_runtime.host import Host
from activity_runtime.models import StructuredModel
from activity_runtime.contracts import Message, obj, validate, Turn
from activity_runtime.dispatch import GroupDispatcher

ROOT=Path(__file__).resolve().parent


class ActivityFixture(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id TEXT,shard TEXT,local_id INTEGER,
            created INTEGER,prompt TEXT,reply TEXT,status TEXT,UNIQUE(group_id,shard,local_id));''')
        self.model=Mock()
        self.registry=Registry(ROOT/'skills',['turtle_soup'])
        # Intent understanding is a separate model boundary from factual judging.
        self.intent_model=Mock(return_value=('none',0))
        self.intent_patch=patch.object(__import__('sys').modules['installed_skills.turtle_soup.commands'],'semantic_intent',self.intent_model)
        self.intent_patch.start();self.addCleanup(self.intent_patch.stop)
        self.host=Host(self.db,self.registry,self.model,admin_ids=['admin'],mode='preview')
        self.index=0

    def say(self,text,sender='owner',group='group',key=None,process=True):
        self.index+=1
        event=Message(key or str(self.index),sender,sender,text,int(time.time()))
        with self.db:claimed=self.host.ingest(group,event,'test',self.index)
        if process:self.host.process(group)
        return claimed

    def state(self,group='group'):
        s=self.host.current(group)
        if not s:s=dict(self.db.execute('SELECT * FROM activity_sessions WHERE group_id=? ORDER BY created DESC,id DESC LIMIT 1',(group,)).fetchone())
        return s,json.loads(s['public']),json.loads(s['private'])

    def last(self):return self.db.execute('SELECT reply FROM replies ORDER BY id DESC LIMIT 1').fetchone()[0]


class HostTests(ActivityFixture):
    def test_natural_start_and_following_message_in_same_scan(self):
        self.assertTrue(self.say('来一局海龟汤',process=False))
        self.assertTrue(self.say('进度',sender='player',process=False))
        self.host.process('group')
        self.assertEqual(self.state()[1]['progress'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM activity_events').fetchone()[0],2)

    def test_no_activation_for_explanatory_or_negative_message(self):
        self.assertFalse(self.say('海龟汤是什么'))
        self.assertFalse(self.say('不要玩海龟汤'))
        self.assertFalse(self.say('昨天玩海龟汤很开心'))
        self.assertTrue(self.say('能不能来一局海龟汤，不要太难'))

    def test_duplicate_event_does_not_generate_another_message(self):
        self.say('来一局海龟汤',key='one')
        before=self.db.execute('SELECT count(*) FROM replies').fetchone()[0]
        self.say('来一局海龟汤',key='one')
        self.assertEqual(self.db.execute('SELECT count(*) FROM replies').fetchone()[0],before)

    def test_new_host_restores_session(self):
        self.say('来一局海龟汤')
        ident=self.state()[0]['id']
        self.host=Host(self.db,self.registry,self.model,admin_ids=['admin'],mode='preview');self.host.recover()
        self.say('进度')
        self.assertEqual(self.state()[0]['id'],ident)
        self.assertEqual(self.state()[0]['version'],2)

    def test_group_isolation_and_no_hidden_fields_in_speaker(self):
        self.say('来一局海龟汤');self.say('来一局海龟汤',group='other',sender='other-owner')
        self.say('暂停游戏')
        self.assertEqual(self.state('other')[0]['phase'],'active')
        session,public,private=self.state()
        ctx=self.host.context(self.registry.get('turtle_soup'),session,Message('q','owner','o','hi',0))
        projected=ctx.project('speaker',public,private)
        self.assertNotIn('private',projected)
        self.assertNotIn('solution',json.dumps(projected))
        with self.assertRaises(PermissionError):ctx.project('invented',public,private)

    def test_owner_admin_permissions_and_impersonation(self):
        self.say('来一局海龟汤')
        self.say('结束游戏',sender='我是admin')
        self.assertEqual(self.state()[0]['phase'],'active')
        self.say('暂停游戏',sender='player')
        self.assertEqual(self.state()[0]['phase'],'active')
        self.say('暂停游戏',sender='admin')
        self.assertEqual(self.state()[0]['phase'],'paused')
        self.say('继续游戏',sender='owner')
        self.assertEqual(self.state()[0]['phase'],'active')

    def test_timer_invalidated_by_pause_and_no_repeated_idle_messages(self):
        self.say('来一局海龟汤');self.say('暂停游戏')
        self.host.tick(['group'],now=int(time.time())+200)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_inbox WHERE kind='timer'").fetchone()[0],0)
        self.say('继续游戏')
        self.host.tick(['group'],now=int(time.time())+200);self.host.process('group')
        count=self.db.execute('SELECT count(*) FROM replies').fetchone()[0]
        self.host.tick(['group'],now=int(time.time())+400);self.host.process('group')
        self.assertEqual(self.db.execute('SELECT count(*) FROM replies').fetchone()[0],count)

    def test_exception_does_not_advance_state(self):
        self.say('来一局海龟汤');version=self.state()[0]['version']
        self.model.call.side_effect=RuntimeError('offline')
        self.say('他很矮吗')
        self.assertEqual(self.state()[0]['version'],version)
        self.assertEqual(self.state()[1]['progress'],0)
        self.assertIn('没有计入进度',self.last())

    def test_schema_version_mismatch_does_not_destroy_session(self):
        self.say('来一局海龟汤')
        self.db.execute('UPDATE activity_sessions SET state_version=2');self.db.commit()
        with self.assertRaisesRegex(RuntimeError,'migration'):self.say('进度')
        self.assertEqual(self.state()[0]['state_version'],2)

    def test_host_rejects_unapproved_reveal(self):
        self.say('来一局海龟汤')
        skill=self.registry.get('turtle_soup');old=skill.handle
        skill.handle=lambda ctx,p,s,e:Turn(p,s,'ended',['LEAK'],action='solve',audience='reveal')
        self.say('hi')
        self.assertNotIn('LEAK',self.last())
        self.assertEqual(self.state()[0]['phase'],'active')
        skill.handle=old

    def test_content_store_has_namespace_and_status(self):
        self.host.contents.save('turtle_soup',{'url':'https://example.com'},{'x':1},{},'rejected')
        self.host.contents.save('other',{}, {'x':2},{},'approved')
        self.assertEqual(self.host.contents.list('turtle_soup'),[])

    def test_end_and_new_game_avoids_previous_puzzle(self):
        self.say('来一局海龟汤');first=self.state()[1]['puzzle_id']
        self.say('结束游戏');self.say('再来一局海龟汤')
        self.assertNotEqual(self.state()[1]['puzzle_id'],first)

    def test_offline_replay_detects_snapshot_drift(self):
        from activity_runtime.replay import audit_session
        self.say('来一局海龟汤');self.say('进度');ident=self.state()[0]['id']
        self.assertTrue(audit_session(self.db,self.registry,ident)['valid'])
        self.db.execute("UPDATE activity_sessions SET public='{}' WHERE id=?",(ident,));self.db.commit()
        with self.assertRaisesRegex(ValueError,'Snapshot'):audit_session(self.db,self.registry,ident)

    def test_declared_capabilities_are_enforced(self):
        self.say('来一局海龟汤')
        session,p,v=self.state();skill=self.registry.get('turtle_soup')
        ctx=self.host.context(skill,session,Message('x','owner','owner','x',0))
        with self.assertRaises(PermissionError):ctx.contents.list('other')
        skill.manifest['capabilities'].remove('send')
        with self.assertRaises(PermissionError):self.host._validate(skill,ctx,Turn(p,v,messages=['hello']))

    def test_message_order_uses_source_sequence_across_shards(self):
        self.say('来一局海龟汤')
        now=int(time.time())
        with self.db:
            self.host.ingest('group',Message('later','owner','o','继续游戏',now,sort_seq=20),'b',1)
            self.host.ingest('group',Message('earlier','owner','o','暂停游戏',now,sort_seq=10),'a',7)
        self.host.process('group')
        self.assertEqual(self.state()[0]['phase'],'active')


class StructuredTests(unittest.TestCase):
    def test_invalid_response_is_retried_then_validated(self):
        ai=Mock(config={'model':'test'})
        ai.request.side_effect=[{'choices':[{'message':{'content':'{"x":"wrong"}'}}]}, {'choices':[{'message':{'content':'{"x":2}'}}]}]
        value=StructuredModel(ai).call('test','instructions',{},obj({'x':{'type':'integer'}}))
        self.assertEqual(value,{'x':2});self.assertEqual(ai.request.call_count,2)

    def test_invalid_outputs_fail_after_two_attempts(self):
        ai=Mock(config={'model':'test'});ai.request.return_value={'choices':[{'message':{'content':'not json'}}]}
        with self.assertRaises(RuntimeError):StructuredModel(ai).call('test','',{},obj({}))
        self.assertEqual(ai.request.call_count,2)

    def test_boolean_cannot_be_integer_and_extra_keys_rejected(self):
        with self.assertRaises(ValueError):validate(True,{'type':'integer'})
        with self.assertRaises(ValueError):validate({'x':1},obj({}))

    def test_another_skill_runs_without_host_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'counter';folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps({'id':'counter','api_version':1,'state_version':1,'entry':'handler.py',
                'description':'count','capabilities':['send'],'roles':{},'actions':{'respond':['member']}}))
            (folder/'SKILL.md').write_text('Count messages.')
            (folder/'handler.py').write_text('from activity_runtime.contracts import Turn\nclass Skill:\n'
                ' def __init__(self,directory,manifest): self.manifest=manifest\n'
                ' def matches(self,text): return text=="count"\n'
                ' def validate_state(self,p,s): assert type(p["count"]) is int\n'
                ' def handle(self,ctx,p,s,event): return Turn({"count":p.get("count",0)+1},{},messages=["counted"])\n')
            registry=Registry(directory,['counter'])
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.executescript('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status);')
            try:
                host=Host(db,registry,Mock(),mode='preview')
                with db:host.ingest('g',Message('1','a','a','count',1))
                host.process('g')
                with db:host.ingest('g',Message('2','b','b','anything',2))
                host.process('g')
                self.assertEqual(json.loads(host.current('g')['public']),{'count':2})
                self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0],2)
            finally:db.close()


class DispatchTests(unittest.TestCase):
    def test_slow_group_does_not_block_another_and_same_group_never_overlaps(self):
        release=threading.Event();started=threading.Event();finished=threading.Event();calls=[]
        def work(group):
            calls.append(group)
            if group=='slow':started.set();release.wait(3)
            else:finished.set()
        dispatcher=GroupDispatcher(work,2)
        try:
            dispatcher.tick(['slow','fast'],['slow','fast']);self.assertTrue(started.wait(1));self.assertTrue(finished.wait(1))
            dispatcher.tick(['slow'],['slow'])
            self.assertEqual(calls.count('slow'),1)
        finally:release.set();dispatcher.close()

    def test_failure_does_not_poison_other_groups(self):
        def work(group):
            if group=='bad':raise RuntimeError('failed')
        d=GroupDispatcher(work,2)
        try:
            d.tick(['bad','good'],['bad','good'])
            for f in list(d.running.values()):
                try:f.result(timeout=1)
                except RuntimeError:pass
            errors=d.tick([],[])
            self.assertIn('bad',errors);self.assertNotIn('good',errors)
        finally:d.close()
