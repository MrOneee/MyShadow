import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from member_memory import MemoryService
from member_memory_policy import command, manage_intent
from social_memory import SocialMemory, digest, member_key


class MemberMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryService(self.tmp.name,{'bot_id':'bot','member_memory':{'quiet_seconds':10}})
        self.group='g@chatroom';self.sender='alice';self.clock=time.time();self.seq=0
        with self.store.db() as db:db.execute("UPDATE meta SET value='0' WHERE key='started'")

    def add(self,text='我喜欢摄影',kind='message',sender=None,created=None,source=None,group=None):
        self.seq+=1
        self.store.observe(group or self.group,sender or self.sender,source or str(self.seq),self.clock if created is None else created,text,kind,'一起试试这个办法。' if kind=='exchange' else '')

    def extract(self,messages,**kwargs):
        payload=json.loads(messages[1]['content']);out=[]
        for e in payload['events']:
            out.append({'member':e['member'],'facts':[{'slot':'interest','value':e['text'][:100],'kind':'self_report',
                'evidence':[{'id':e['id'],'quote':e['text'][:100]}]}]})
        return json.dumps({'members':out}),{}

    def run_batch(self,fn=None):
        with self.store.db() as db:db.execute('UPDATE events SET received=?',(time.time()-1000,))
        return self.store.update_once(Mock(complete=Mock(side_effect=fn or self.extract)),[self.group])

    def manage(self,action,request,**args):
        self.seq+=1
        return self.store.manage(self.group,self.sender,{'action':action,**args},request,str(self.seq))

    def rows(self,table):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM '+table)]

    def test_restart_and_scope_and_account_isolation(self):
        self.add();self.run_batch()
        other=MemoryService(self.tmp.name,self.store.config)
        self.assertIn('摄影',other.context(self.group,self.sender))
        self.assertNotIn('摄影',other.context('direct',self.sender))
        self.assertNotIn('摄影',other.context(self.group,'bob'))
        different=MemoryService(self.tmp.name,{'bot_id':'different'})
        self.assertNotIn('摄影',different.context(self.group,self.sender))
        different=MemoryService(self.tmp.name,{'bot_id':'bot','group_personas':{self.group:{'persona_id':'different'}}})
        self.assertNotIn('摄影',different.context(self.group,self.sender))

    def test_no_idle_or_not_due_model_calls(self):
        ai=Mock()
        self.assertFalse(self.store.update_once(ai,[self.group]));self.add()
        self.assertFalse(self.store.update_once(ai,[self.group]));ai.complete.assert_not_called()

    def test_event_replay_and_extraction_idempotent(self):
        self.add(kind='exchange',source='same');self.add(kind='exchange',source='same')
        self.run_batch();self.run_batch()
        self.assertEqual(self.store.stats(),{'events':1,'facts':1,'episodes':1,'relationship_events':1,'relationships':1})

    def test_unknown_member_and_fabricated_quote_rejected(self):
        self.add()
        def bad(msg,**kw):
            e=json.loads(msg[1]['content'])['events'][0]
            return json.dumps({'members':[{'member':e['member'],'facts':[{'slot':'interest','value':'滑雪','kind':'self_report','evidence':[{'id':e['id'],'quote':'我喜欢滑雪'}]}]},{'member':'stranger','facts':[]}]}),{}
        self.run_batch(bad);self.assertEqual(self.rows('facts'),[])

    def test_third_party_evidence_rejected(self):
        self.add();self.add('我喜欢画画',sender='bob')
        def bad(msg,**kw):
            a,b=json.loads(msg[1]['content'])['events']
            return json.dumps({'members':[{'member':a['member'],'facts':[{'slot':'interest','value':b['text'],'kind':'self_report','evidence':[{'id':b['id'],'quote':b['text']}]}]}]}),{}
        self.run_batch(bad);self.assertFalse(self.rows('facts'))

    def test_sensitive_and_prestart_excluded(self):
        self.add('我的密码是1234');self.add('我的手机号13800138000')
        with self.store.db() as db:db.execute("UPDATE meta SET value=? WHERE key='started'",(str(self.clock+1),))
        self.add();self.assertFalse(self.rows('events'))

    def test_multi_value_correction_and_history(self):
        self.manage('remember','记住我喜欢摄影',value='摄影',slot='interest')
        self.manage('remember','记住我喜欢画画',value='画画',slot='interest')
        self.manage('correct','把摄影改成徒步',description='摄影',value='徒步')
        active=[r['value'] for r in self.rows('facts') if r['status']=='active']
        self.assertEqual(active,['画画','徒步'])
        self.assertIsNotNone(self.rows('facts')[-1]['supersedes'])
        found=self.store.recall(self.group,self.sender,{'query':'摄影'})
        self.assertEqual(found['items'][0]['status'],'superseded')

    def test_partial_forget_erases_versions_preserves_unrelated(self):
        self.manage('remember','记住摄影',value='摄影',slot='interest')
        self.manage('remember','记住画画',value='画画',slot='interest')
        self.manage('correct','摄影改成徒步',description='摄影',value='徒步')
        self.manage('forget','忘记徒步',description='徒步')
        self.assertEqual([r['value'] for r in self.rows('facts')],['画画'])
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':'摄影'})['status'],'empty')

    def test_forget_all_and_resume_cutoff(self):
        self.add(kind='exchange');self.run_batch()
        self.assertEqual(self.manage('forget','忘记我')['status'],'forgotten')
        for t in ('facts','episodes','events','relationships','relationship_events'):self.assertEqual(self.rows(t),[])
        self.add();self.assertEqual(self.rows('events'),[])
        self.manage('resume','恢复记忆');self.add();self.assertEqual(self.rows('events'),[])
        self.add(created=time.time()+1);self.assertEqual(len(self.rows('events')),1)

    def test_inflight_extraction_cannot_restore_forgotten(self):
        self.add(kind='exchange')
        def late(msg,**kw):
            self.manage('forget','忘记我');return self.extract(msg,**kw)
        self.run_batch(late)
        self.assertFalse(self.rows('facts'));self.assertFalse(self.rows('episodes'));self.assertFalse(self.rows('relationships'))

    def test_partial_forget_no_blanket_erase_and_negation(self):
        self.assertIn('error',self.manage('forget','忘记我喜欢摄影'))
        self.assertIn('error',self.manage('forget','不要忘记我的记忆'))
        self.assertIn('error',self.manage('remember','天气不错',value='天气不错'))
        self.assertIn('error',self.manage('remember','记住摄影',value='不存在的内容'))

    def test_pause_keeps_data_but_disables_context(self):
        self.manage('remember','记住摄影',value='摄影')
        self.manage('pause','暂停记忆');self.assertNotIn('摄影',self.store.context(self.group,self.sender))
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':''})['status'],'disabled')
        self.manage('resume','恢复记忆');self.assertIn('摄影',self.store.context(self.group,self.sender))

    def test_tool_binding_budget_and_receipt(self):
        h=self.store.handler(self.group,self.sender,'记住摄影','turn')
        a={'action':'remember','value':'摄影'}
        self.assertEqual(h('manage_memory',a),h('manage_memory',a));self.assertEqual(len(self.rows('facts')),1)
        self.assertIn('error',h('recall_memory',{'query':'','member':'bob'}))
        h('recall_memory',{'query':'摄影'});self.assertIn('error',h('recall_memory',{'query':'历史'}))
        read=self.store.handler(self.group,self.sender,'记住画画','other',readonly=True)
        self.assertIn('error',read('manage_memory',{'action':'remember','value':'画画'}))

    def test_failure_retries_persisted_queue(self):
        self.add()
        with self.assertRaises(ValueError):self.run_batch(lambda *a,**k:('broken',{}))
        self.assertEqual(self.rows('events')[0]['processed'],0)
        other=MemoryService(self.tmp.name,self.store.config)
        self.assertFalse(other.update_once(Mock(),[self.group]))
        with self.store.db() as db:db.execute('UPDATE jobs SET lease_until=0')
        self.assertTrue(self.run_batch())

    def test_forget_invalidates_in_turn_recall_cache(self):
        self.manage('remember','记住摄影',value='摄影')
        h=self.store.handler(self.group,self.sender,'忘记我','turn')
        self.assertEqual(h('recall_memory',{'query':''})['status'],'ok')
        h('manage_memory',{'action':'forget'})
        self.assertEqual(h('recall_memory',{'query':''})['status'],'disabled')

    def test_inflight_lease_prevents_second_extractor(self):
        self.add()
        def work(msg,**kw):
            other=MemoryService(self.tmp.name,self.store.config)
            ai=Mock();self.assertFalse(other.update_once(ai,[self.group]));ai.complete.assert_not_called()
            return self.extract(msg,**kw)
        self.run_batch(work)

    def test_payload_bound_and_members_bound(self):
        for n in range(40):self.add('我喜欢'+str(n)+'摄影'*400,sender='member'+str(n))
        def bounded(msg,**kw):
            p=json.loads(msg[1]['content']);self.assertLess(len(msg[1]['content']),16500)
            self.assertLessEqual(len({e['member'] for e in p['events']}),8)
            return '{"members":[]}',{}
        self.run_batch(bounded);self.assertTrue(any(not r['processed'] for r in self.rows('events')))

    def test_observation_needs_three_distinct_sources(self):
        for n in range(3):self.add('我喜欢摄影',source=str(n))
        def observation(msg,**kw):
            events=json.loads(msg[1]['content'])['events']
            return json.dumps({'members':[{'member':events[0]['member'],'facts':[{'slot':'communication','kind':'observation','value':'喜欢简短','evidence':[{'id':e['id'],'quote':e['text']} for e in events]}]}]}),{}
        self.run_batch(observation);self.assertFalse(self.rows('facts'))

    def test_no_raw_numbers_and_boundary_priority(self):
        self.manage('remember','记住不要开外貌玩笑',value='不要开外貌玩笑',slot='boundary')
        for n in range(10):self.manage('remember','记住爱好'+str(n),value='爱好'+str(n),slot='interest')
        data=json.loads(self.store.context(self.group,self.sender,compact=True))
        self.assertIn('不要开外貌玩笑',str(data));self.assertNotIn('affinity',str(data));self.assertNotIn('familiarity": 0',str(data))

    def test_legacy_migration_and_delete(self):
        legacy=SocialMemory(self.tmp.name)
        path=legacy.path(digest(self.group),member_key(self.group,self.sender));path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('```json\n'+json.dumps({'items':[{'slot':'interest','text':'摄影'}]})+'\n```',encoding='utf-8')
        self.assertIn('摄影',self.store.context(self.group,self.sender));self.assertFalse(self.rows('relationships'))
        changed=MemoryService(self.tmp.name,{'bot_id':'bot','group_personas':{self.group:{'persona_id':'new'}}})
        self.assertNotIn('摄影',changed.context(self.group,self.sender))
        self.manage('forget','忘记我');self.assertFalse(path.exists())
        self.assertEqual(legacy.read(digest(self.group),member_key(self.group,self.sender)),{})

    def test_commands_and_shadow(self):
        self.assertEqual(command('请看看我的画像'),'view');self.assertIsNone(command('不要忘记我'))
        self.assertTrue(manage_intent('把称呼改成小李'))
        self.store.shadow=True;self.assertEqual(self.store.context(self.group,self.sender),'')

    def test_recall_marks_expired_observation_as_tentative(self):
        with self.store.db() as db:
            c=self.store._ensure(db,self.group,self.sender)
            self.store._fact(db,c['scope'],c['member'],{'slot':'communication','value':'通常简短回答','kind':'observation'},0,now=time.time()-40*86400)
        self.assertNotIn('通常简短回答',self.store.context(self.group,self.sender))
        item=self.store.recall(self.group,self.sender,{'query':''})['items'][0]
        self.assertEqual(item['basis'],'observation');self.assertEqual(item['status'],'stale_observation')

    def test_recall_schema_allows_overview_and_host_validation_feedback(self):
        from activity_runtime.contracts import validate
        from member_memory_policy import RECALL_TOOL,OUTPUT
        validate({},RECALL_TOOL['function']['parameters'])
        validate({'error':'validation_failed','detail':'invalid arguments','retryable':True},OUTPUT)
        self.assertEqual(self.store.recall(self.group,self.sender,{})['status'],'empty')

    def test_retention_and_fts_cleanup(self):
        self.add('一起讨论如何把照片拍好',kind='exchange',created=time.time()-200*86400);self.run_batch()
        with self.store.db() as db:db.execute('UPDATE events SET received=?',(time.time()-200*86400,))
        self.store.maintain();self.assertFalse(self.rows('events'));self.assertFalse(self.rows('episodes'))
        self.assertFalse(self.rows('episode_search'));self.assertTrue(self.rows('facts'))

    def test_one_hundred_multiday_trajectories(self):
        # Distinct sessions, replayed deliveries, neutral feedback and burst caps.
        for trajectory in range(100):
            sender='trajectory'+str(trajectory)
            for day in range(5):
                for burst in range(3):
                    stamp=self.clock-10*86400+day*86400+burst*1801
                    text='第'+str(day)+'天我们讨论第'+str(burst)+'个摄影技巧'
                    self.add(text,kind='exchange',sender=sender,created=stamp,source=f'{trajectory}:{day}:{burst}')
            while self.run_batch(lambda *a,**k:('{"members":[]}',{})):pass
            with self.store.db() as db:
                r=db.execute('SELECT * FROM relationships WHERE member=?',(self.store.member(sender),)).fetchone()
                self.assertEqual(r['familiarity'],20);self.assertEqual(r['affinity'],50);self.assertEqual(r['stage'],1)
        self.assertEqual(len(self.rows('relationships')),100)

    def test_repeated_text_does_not_build_relationship(self):
        for day in range(3):self.add('你好你好我是来刷好感的你好',kind='exchange',created=self.clock-day*86400)
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        self.assertEqual(self.rows('relationships')[0]['familiarity'],2)

    def test_positive_applies_only_to_evidenced_window(self):
        self.add('今天一起解决问题很开心',kind='exchange',created=self.clock-3600)
        self.add('今天别的问题也来讨论一下',kind='exchange')
        def positive(msg,**kw):
            e=json.loads(msg[1]['content'])['events'][0]
            return json.dumps({'members':[{'member':e['member'],'relationship':{'type':'positive','confidence':'high','evidence':[{'id':e['id'],'quote':e['text']}]}}]}),{}
        self.run_batch(positive);self.assertEqual(self.rows('relationships')[0]['affinity'],51)

    def test_single_hostile_classification_does_not_lower_affinity(self):
        self.add('你刚才说的是错的，我不同意',kind='exchange')
        def hostile(msg,**kw):
            e=json.loads(msg[1]['content'])['events'][0]
            return json.dumps({'members':[{'member':e['member'],'relationship':{'type':'friction','confidence':'high','evidence':[{'id':e['id'],'quote':e['text']}]}}]}),{}
        self.run_batch(hostile);self.assertEqual(self.rows('relationships')[0]['affinity'],50)

    def test_later_positive_same_window_once_and_rebuild(self):
        self.add('我们先讨论今天的问题',kind='exchange')
        self.run_batch(lambda *a,**k:('{"members":[]}',{}))
        def positive(msg,**kw):
            e=json.loads(msg[1]['content'])['events'][0]
            return json.dumps({'members':[{'member':e['member'],'relationship':{'type':'positive','confidence':'high','evidence':[{'id':e['id'],'quote':e['text']}]}}]}),{}
        self.add('咱俩配合排查问题真的很愉快',kind='exchange');self.run_batch(positive)
        self.add('和你今天一起查问题开心极了',kind='exchange');self.run_batch(positive)
        with self.store.db() as db:self.store._rebuild(db,self.store.scope(self.group),self.store.member(self.sender),time.time())
        self.assertEqual(self.rows('relationships')[0]['affinity'],51)
        self.assertEqual(self.rows('relationships')[0]['familiarity'],2)


if __name__=='__main__':unittest.main()
