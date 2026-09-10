import json
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from tests import test_member_memory
from myshadow.member_memory import MemoryService


class UnifiedProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_member_memory.MemberMemoryTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.m=self.fixture.store;self.a='a@chatroom';self.b='b@chatroom';self.user='wxid_alice';self.seq=0

    def manage(self,group,action,value='',description='',slot='interest',topic='摄影',request=None,**kw):
        self.seq+=1
        request=request or {'remember':'记住我'+value,'correct':'把'+description+'改成'+value,
            'forget':'忘记'+description if description else '忘记我','share':'允许这条摄影资料在其他群使用',
            'unshare':'撤回摄影的共享','view':'查看我的画像','pause':'暂停记忆','resume':'恢复记忆'}[action]
        return self.m.manage(group,self.user,dict(action=action,value=value,description=description,slot=slot,topic=topic,**kw),request,str(self.seq))

    def recall(self,group,query='摄影'):return self.m.recall(group,self.user,{'query':query})

    def test_group_base_fact_available_to_owner_private_not_other_group(self):
        self.manage(self.a,'remember','喜欢摄影')
        self.assertIn('喜欢摄影',self.m.context(self.user,self.user))
        self.assertNotIn('喜欢摄影',self.m.context(self.b,self.user))
        self.assertNotIn('喜欢摄影',self.m.context(self.user,'wxid_bob'))
        with self.m.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM subjects WHERE member=?',(self.m.member(self.user),)).fetchone()[0],1)

    def test_private_correction_invalidates_all_old_versions_without_disclosure(self):
        self.manage(self.a,'remember','喜欢摄影')
        self.manage(self.a,'share',description='摄影')
        self.assertIn('喜欢摄影',self.m.context(self.b,self.user))
        result=self.manage(self.user,'correct','不喜欢摄影','喜欢摄影')
        self.assertEqual(result['status'],'corrected')
        self.assertIn('不喜欢摄影',self.m.context(self.user,self.user))
        for group in (self.a,self.b):
            self.assertNotIn('摄影',self.m.context(group,self.user))
            self.assertEqual(self.recall(group)['status'],'empty')
        # An older version cannot expose an intermediate visible replacement either.
        self.manage(self.user,'correct','改拍人像摄影','不喜欢摄影')
        self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_private_fact_requires_explicit_share_and_revoke(self):
        self.manage(self.user,'remember','喜欢摄影')
        self.assertEqual(self.recall(self.a)['status'],'empty')
        self.assertEqual(self.manage(self.user,'share',description='摄影')['status'],'shared')
        self.assertIn('喜欢摄影',self.m.context(self.a,self.user))
        self.assertEqual(self.manage(self.user,'unshare',description='摄影')['status'],'unshared')
        self.assertEqual(self.recall(self.a)['status'],'empty')
        self.assertIn('喜欢摄影',self.m.context(self.user,self.user))

    def test_no_implicit_or_negated_share(self):
        self.manage(self.user,'remember','喜欢摄影')
        for request in ('记住我喜欢摄影','不要把这条资料共享到其他群','不允许其他群使用这条资料'):
            with self.subTest(request=request):self.assertIn('error',self.manage(self.user,'share',description='摄影',request=request))
        self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_same_name_cannot_access_another_identity_or_account(self):
        self.manage(self.user,'remember','喜欢摄影');self.manage(self.user,'share',description='摄影')
        self.assertNotIn('摄影',self.m.context(self.a,'wxid_bob'))
        other=MemoryService(self.fixture.tmp.name,{'bot_id':'other-bot'})
        self.assertNotIn('摄影',other.context(self.a,self.user))

    def test_contextual_alias_and_boundaries_stay_local(self):
        self.manage(self.a,'remember','小林',slot='nickname',topic='',request='以后叫我小林')
        self.manage(self.a,'remember','不要开外貌玩笑',slot='boundary',topic='')
        for group in (self.b,self.user):
            ctx=self.m.context(group,self.user);self.assertNotIn('小林',ctx);self.assertNotIn('外貌',ctx)
        self.assertIn('error',self.manage(self.a,'share',description='小林'))

    def test_repeated_base_assertions_deduplicate_in_owner_view(self):
        self.manage(self.a,'remember','喜欢摄影');self.manage(self.b,'remember','我喜欢摄影')
        items=self.recall(self.user)['items']
        self.assertEqual(len(items),1)
        with self.m.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM facts').fetchone()[0],2)

    def test_uncertain_conflict_needs_confirmation_then_resolves(self):
        self.manage(self.a,'remember','喜欢摄影')
        result=self.manage(self.b,'remember','不喜欢摄影')
        self.assertEqual(result['status'],'needs_confirmation')
        self.assertNotIn('喜欢摄影',self.m.context(self.user,self.user))
        self.assertTrue(all(r['status']=='needs_confirmation' for r in self.recall(self.user)['items']))
        self.manage(self.user,'correct','不喜欢摄影','喜欢摄影')
        self.assertIn('不喜欢摄影',self.m.context(self.user,self.user))
        self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_explicit_cross_group_correction_uses_known_description_only(self):
        self.manage(self.a,'remember','喜欢摄影')
        self.assertEqual(self.manage(self.b,'correct','不喜欢摄影','喜欢摄影')['status'],'corrected')
        self.assertEqual(self.recall(self.a)['status'],'empty')
        self.assertIn('不喜欢摄影',self.m.context(self.b,self.user))

    def test_core_delete_clears_sources_and_versions_globally(self):
        self.manage(self.a,'remember','喜欢摄影');self.manage(self.b,'remember','喜欢摄影')
        self.manage(self.user,'forget',description='摄影')
        for group in (self.a,self.b,self.user):self.assertEqual(self.recall(group)['status'],'empty')
        with self.m.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM fact_search').fetchone()[0],0)

    def test_inflight_other_group_extraction_cannot_undo_correction(self):
        self.manage(self.a,'remember','喜欢摄影')
        self.m.observe(self.a,self.user,'late',time.time()+0.1,'我喜欢摄影')
        with self.m.db() as db:db.execute('UPDATE events SET received=?',(time.time()-1000,))
        def response(messages,**kw):
            e=json.loads(messages[1]['content'])['events'][0]
            self.manage(self.user,'correct','不喜欢摄影','喜欢摄影')
            return json.dumps({'members':[{'member':e['member'],'facts':[{'slot':'interest','topic':'摄影','value':'喜欢摄影','kind':'self_report','replace':True,'evidence':[{'id':e['id'],'quote':e['text']}]}]}]}),{}
        self.m.update_once(Mock(complete=Mock(side_effect=response)),[self.a])
        self.assertIn('不喜欢摄影',self.m.context(self.user,self.user));self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_group_extractor_never_receives_private_facts(self):
        self.manage(self.user,'remember','喜欢摄影')
        self.m.observe(self.a,self.user,'new',time.time()+1,'今天天气真不错啊')
        with self.m.db() as db:db.execute('UPDATE events SET received=?',(time.time()-1000,))
        def response(messages,**kw):
            self.assertNotIn('喜欢摄影',messages[1]['content']);return '{"members":[]}',{}
        self.m.update_once(Mock(complete=Mock(side_effect=response)),[self.a])

    def test_extractor_cannot_forge_visibility(self):
        self.m.observe(self.user,self.user,'new',time.time()+1,'我喜欢摄影')
        with self.m.db() as db:db.execute('UPDATE events SET received=?',(time.time()-1000,))
        def response(messages,**kw):
            e=json.loads(messages[1]['content'])['events'][0]
            return json.dumps({'members':[{'member':e['member'],'facts':[{'slot':'interest','topic':'摄影','value':'喜欢摄影','kind':'self_report','visibility':'shared','_visibility':'shared','evidence':[{'id':e['id'],'quote':e['text']}]}]}]}),{}
        self.m.update_once(Mock(complete=Mock(side_effect=response)),[self.user])
        self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_old_assertion_arriving_late_cannot_replace_newer_private_fact(self):
        self.manage(self.user,'remember','不喜欢摄影')
        with self.m.db() as db:
            c=self.m._ensure(db,self.a,self.user)
            self.m._fact(db,c['scope'],c['member'],{'slot':'interest','topic':'摄影','value':'喜欢摄影','replace':True,'_asserted_at':time.time()-100},0)
        self.assertIn('不喜欢摄影',self.m.context(self.user,self.user));self.assertEqual(self.recall(self.a)['status'],'empty')

    def test_global_revision_invalidates_other_turn_tool_cache(self):
        self.manage(self.a,'remember','喜欢摄影');self.manage(self.a,'share',description='摄影')
        h=self.m.handler(self.b,self.user,'看看喜好','read')
        self.assertEqual(h('recall_memory',{})['status'],'ok')
        self.manage(self.user,'correct','不喜欢摄影','喜欢摄影')
        self.assertEqual(h('recall_memory',{})['status'],'empty')

    def test_relationships_not_merged_with_unified_profile(self):
        for group in (self.a,self.b,self.user):self.m.context(group,self.user)
        with self.m.db() as db:
            db.execute('INSERT INTO relationships VALUES(?,?,?,?,?,?,?,?)',(self.m.scope(self.a),self.m.member(self.user),90,70,30,3,0,time.time()))
        self.assertIn('老朋友',self.m.context(self.a,self.user))
        self.assertIn('初识',self.m.context(self.b,self.user));self.assertIn('初识',self.m.context(self.user,self.user))

    def test_existing_database_migration_preserves_scope_and_can_be_reopened(self):
        root=Path(self.fixture.tmp.name)/'old';(root/'member-memory').mkdir(parents=True)
        with sqlite3.connect(root/'member-memory/memory.sqlite3') as db:
            db.execute('CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,member TEXT,slot TEXT,value TEXT,kind TEXT,confidence TEXT,recorded_at REAL,valid_from TEXT,valid_to REAL,status TEXT,evidence TEXT,supersedes INTEGER,version INTEGER)')
            db.execute('INSERT INTO facts VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.m.scope(self.a),self.m.member(self.user),'interest','喜欢摄影','self_report','high',time.time(),None,None,'active','[]',None,0))
        upgraded=MemoryService(root,self.m.config)
        self.assertIn('喜欢摄影',upgraded.context(self.a,self.user))
        self.assertIn('喜欢摄影',upgraded.context(self.user,self.user))
        self.assertNotIn('喜欢摄影',upgraded.context(self.b,self.user))
        reopened=MemoryService(root,self.m.config)
        with reopened.db() as db:
            row=db.execute('SELECT * FROM facts').fetchone()
            self.assertEqual(row['id'],1);self.assertEqual(row['visibility'],'local');self.assertEqual(row['topic'],'摄影')


if __name__=='__main__':unittest.main()
