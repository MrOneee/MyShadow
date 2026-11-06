import json
import tempfile
import time
import unittest
from unittest.mock import Mock
from myshadow.member_memory import MemoryService
from myshadow import member_evidence as evidence


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=MemoryService(self.temp.name)
        with self.store.db() as db:db.execute("UPDATE meta SET value='0' WHERE key='started'")

    def add(self,text='I played charades and a scavenger hunt at the picnic.',kind='event',role='user',assistant='',group='g',sender='a',source='1'):
        self.store.observe(group,sender,source,time.time(),text,'exchange' if assistant else 'message',assistant)
        with self.store.db() as db:
            r=dict(db.execute('SELECT * FROM events WHERE source=?',(source,)).fetchone())
            quote=text if role=='user' else assistant
            evidence.write(db,r['scope'],r['member'],[dict(id=r['id'],kind=kind,role=role,summary='picnic',quote=quote)],{r['id']:r},self.store._words,self.store.eligible)
            db.execute('UPDATE events SET processed=1')
        return r

    def test_specific_event_does_not_require_profile_or_confirmed_exchange(self):
        self.add()
        result=self.store.recall('g','a',{'query':'picnic'})
        self.assertIn('charades',str(result))
        self.assertEqual(result['items'][0]['kind'],'source_evidence')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM facts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM episodes').fetchone()[0],0)

    def test_quotes_local_and_same_member_even_in_private_chat(self):
        self.add()
        for group,sender in [('other','a'),('g','b'),('a','a')]:
            self.assertNotIn('charades',str(self.store.recall(group,sender,{'query':'picnic'})))

    def test_sensitive_quote_is_rejected(self):
        self.add('I was diagnosed with cancer.',source='medical')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM memory_evidence').fetchone()[0],0)

    def test_assistant_advice_not_user_event(self):
        r=self.add('What should I pack?',kind='advice',role='assistant',assistant='Pack a compass and two dry bags.')
        result=self.store.recall('g','a',{'query':'compass'})
        self.assertEqual(result['items'][0]['basis'],'assistant_statement')
        with self.store.db() as db:
            evidence.write(db,r['scope'],r['member'],[dict(id=r['id'],kind='event',role='user',summary='compass',quote=r['assistant'])],{r['id']:r},self.store._words,self.store.eligible)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_evidence').fetchone()[0],1)

    def test_source_only_specific_forget_deletes_search_and_event(self):
        self.add()
        result=self.store.manage('g','a',{'action':'forget','description':'charades'},'忘记charades','forget')
        self.assertEqual(result['status'],'forgotten')
        self.assertNotIn('charades',str(self.store.recall('g','a',{'query':'picnic'})))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_evidence_search').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_source_retention_cleans_index_and_pause_blocks_read(self):
        self.add()
        self.store.manage('g','a',{'action':'pause'},'暂停记忆','pause')
        self.assertEqual(self.store.recall('g','a',{'query':'picnic'})['items'],[])
        with self.store.db() as db:db.execute('UPDATE events SET received=0')
        self.store.maintain()
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM memory_evidence_search').fetchone()[0],0)

    def test_upgrade_never_backfills_raw_events(self):
        self.store.observe('g','a','raw',time.time(),'I played charades.')
        with self.store.db() as db:
            db.execute('UPDATE events SET processed=1')
            db.execute("UPDATE meta SET value='2.2.0' WHERE key='memory_version'")
        again=MemoryService(self.temp.name)
        with again.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM memory_evidence').fetchone()[0],0)

    def test_hidden_profile_replacement_cannot_reappear_via_evidence(self):
        r=self.add()
        with self.store.db() as db:
            self.store._fact(db,r['scope'],r['member'],dict(slot='interest',topic='games',value='charades',evidence=[dict(id=r['id'],quote=r['text'])],_asserted_at=1),0)
            c=self.store._ensure(db,'a','a')
            self.store._fact(db,c['scope'],c['member'],dict(slot='interest',topic='games',value='chess',replace=True,_asserted_at=2),0)
        self.assertEqual(self.store.recall('g','a',{'query':'charades'})['items'],[])

    def test_real_extraction_path_saves_source_proposals(self):
        self.store.observe('g','a','m',time.time(),'I cooked paella with saffron yesterday.')
        def complete(messages,**kwargs):
            r=json.loads(messages[1]['content'])['events'][0]
            return json.dumps(dict(members=[dict(member=r['member'],facts=[],memories=[dict(id=r['id'],kind='event',role='user',summary='paella saffron',quote=r['text'])])])),{}
        with self.store.db() as db:db.execute('UPDATE events SET received=0')
        self.assertTrue(self.store.update_once(Mock(complete=complete),['g']))
        self.assertIn('saffron',str(self.store.recall('g','a',{'query':'paella'})))


if __name__=='__main__':unittest.main()
