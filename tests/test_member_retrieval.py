import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from myshadow.member_memory import MemoryService
from myshadow import member_retrieval as retrieval


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryService(self.tmp.name,{'bot_id':'test'})
        self.group='group';self.sender='alice'
        with self.store.db() as db:db.execute("UPDATE meta SET value='0' WHERE key='started'")

    def fact(self,value,quote,scope=None,visibility='local',topic='hobby'):
        group=scope or self.group
        with self.store.db() as db:
            c=self.store._ensure(db,group,self.sender)
            return self.store._fact(db,c['scope'],c['member'],{'slot':'interest','topic':topic,'value':value,
                '_visibility':visibility,'evidence':[{'quote':quote}],'kind':'self_report'},c['version'])

    def exchange(self,text,assistant='acknowledged'):
        self.store.observe(self.group,self.sender,'exchange',time.time(),text,'exchange',assistant)
        with self.store.db() as db:db.execute('UPDATE events SET received=0')
        self.store.update_once(Mock(complete=Mock(return_value=(' {"members":[]}',{}))),[self.group])

    def test_query_context_finds_older_profile_via_original_language(self):
        self.fact('喜欢攀岩','I enjoy bouldering in the evenings.')
        for n in range(12):self.fact('爱好'+str(n),'unrelated hobby '+str(n),topic=str(n))
        context=json.loads(self.store.context(self.group,self.sender,query='What about bouldering?'))
        self.assertIn('喜欢攀岩',str(context['relevant_memories']))
        self.assertNotIn('relevant_memories',json.loads(self.store.context(self.group,self.sender,compact=True,query='bouldering')))

    def test_source_quotes_cannot_be_searched_or_read_across_shared_scopes(self):
        self.fact('喜欢攀岩','my secretproject involves bouldering',visibility='shared')
        other=self.store.recall('elsewhere',self.sender,{'query':'secretproject'})
        self.assertEqual(other['items'],[])
        shared=self.store.recall('elsewhere',self.sender,{'query':'攀岩'})
        self.assertTrue(shared['items'])
        self.assertNotIn('secretproject',str(shared))
        self.assertNotIn('source_quotes',shared['items'][0])
        self.assertEqual(self.store.recall(self.group,'bob',{'query':'secretproject'})['items'],[])

    def test_confirmed_exchange_retains_tail_and_returns_query_window(self):
        text=('Background material. '*35)+'The saffron recipe needs exactly 17 grams.'
        self.exchange(text,'Recipe recorded. '*45)
        result=self.store.recall(self.group,self.sender,{'query':'saffron recipe grams'})
        self.assertIn('17 grams',str(result))
        self.assertTrue(result['items'][0]['truncated'])
        self.assertLessEqual(len(result['items'][0]['text']),902)

    def test_delete_clears_quote_index_and_does_not_resurrect_on_migration(self):
        self.fact('喜欢攀岩','bouldering private clue')
        self.store.manage(self.group,self.sender,{'action':'forget','description':'攀岩'},'忘记攀岩','forget-one')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM fact_evidence_search').fetchone()[0],0)
            db.execute("DELETE FROM meta WHERE key='memory_version'")
        again=MemoryService(self.tmp.name,{'bot_id':'test'})
        self.assertEqual(again.recall(self.group,self.sender,{'query':'bouldering'})['items'],[])

    def test_cleanup_of_shared_evidence_reindexes_remaining_fact(self):
        # Two fact rows cite the same source; erasing one removes the old quote
        # from the surviving fact's evidence and from the auxiliary FTS index.
        with self.store.db() as db:
            c=self.store._ensure(db,self.group,self.sender)
            ev=[{'id':999,'quote':'bouldering and secretcycling'}]
            for value,topic in [('攀岩','攀岩'),('骑车','骑车')]:
                self.store._fact(db,c['scope'],c['member'],{'slot':'interest','value':value,'topic':topic,'evidence':ev},0)
        self.store.manage(self.group,self.sender,{'action':'forget','description':'攀岩'},'忘记攀岩','forget')
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':'secretcycling'})['items'],[])
        self.assertTrue(self.store.recall(self.group,self.sender,{'query':'骑车'})['items'])

    def test_legacy_migration_expands_only_existing_episodes_and_is_idempotent(self):
        text='intro '*65+'The telescope aperture is 203 millimeters.'
        self.exchange(text)
        with self.store.db() as db:
            db.execute("UPDATE episodes SET text='legacy truncated text'")
            db.execute("DELETE FROM meta WHERE key='memory_version'")
        again=MemoryService(self.tmp.name,{'bot_id':'test'})
        self.assertIn('203 millimeters',str(again.recall(self.group,self.sender,{'query':'telescope aperture'})))
        with again.db() as db:
            first=db.execute('SELECT text FROM episodes').fetchone()[0]
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='memory_version'").fetchone()[0],retrieval.VERSION)
        again=MemoryService(self.tmp.name,{'bot_id':'test'})
        with again.db() as db:
            self.assertEqual(db.execute('SELECT text FROM episodes').fetchone()[0],first)
            db.execute('DELETE FROM episodes');db.execute('DELETE FROM episode_search')
            db.execute("DELETE FROM meta WHERE key='memory_version'")
        again=MemoryService(self.tmp.name,{'bot_id':'test'})
        with again.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM episodes').fetchone()[0],0)

    def test_partial_forget_cleans_quote_in_different_slot(self):
        with self.store.db() as db:
            c=self.store._ensure(db,self.group,self.sender)
            evidence=[{'id':999,'quote':'bouldering and short answers'}]
            for slot,value in [('interest','喜欢攀岩'),('communication','简短回答')]:
                self.store._fact(db,c['scope'],c['member'],{'slot':slot,'value':value,'evidence':evidence},0)
        self.store.manage(self.group,self.sender,{'action':'forget','description':'攀岩'},'忘记攀岩','partial')
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':'bouldering'})['items'],[])
        kept=self.store.recall(self.group,self.sender,{'query':'简短'})
        self.assertTrue(kept['items']);self.assertNotIn('bouldering',str(kept))

    def test_pause_and_context_budget(self):
        for n in range(12):self.fact('攀岩'+str(n),'bouldering '+'x'*180,topic=str(n))
        self.assertLessEqual(len(self.store.context(self.group,self.sender,query='bouldering')),6500)
        self.store.manage(self.group,self.sender,{'action':'pause'},'暂停记忆','pause')
        self.assertNotIn('bouldering',self.store.context(self.group,self.sender,query='bouldering'))

    def test_explicit_update_keeps_versions_but_does_not_replace_unrelated_topic(self):
        with self.store.db() as db:
            c=self.store._ensure(db,self.group,self.sender)
            for value,topic,replace,when in [('完成四次练习','练习次数',False,1),('购买两本书','购书',False,2),('现在累计完成七次练习','练习次数',True,3)]:
                self.store._fact(db,c['scope'],c['member'],{'slot':'background','value':value,'topic':topic,'replace':replace,'_asserted_at':when},0)
            active=db.execute("SELECT value FROM facts WHERE status='active' AND conflict=0").fetchall()
            self.assertEqual({r[0] for r in active},{'购买两本书','现在累计完成七次练习'})


if __name__=='__main__':unittest.main()
