import json
import tempfile
import unittest
import time
from unittest.mock import Mock

from myshadow.member_memory import MemoryService
from myshadow import member_retrieval as retrieval


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=MemoryService(tmp.name,{'bot_id':'test'})

    def fact(self,group,value,quote,when,topic='books',shared=False,replace=False):
        with self.store.db() as db:
            c=self.store._ensure(db,group,'alice')
            return self.store._fact(db,c['scope'],c['member'],dict(slot='background',value=value,topic=topic,
                kind='self_report',_asserted_at=when,replace=replace,_visibility='shared' if shared else 'local',
                evidence=[dict(id=int(when),quote=quote)]),c['version'])

    def test_select_covers_distinct_sources_before_repeated_source(self):
        candidates=[('canoe cycling',3,{'1'},{'text':'canoe cycling detail A'}),
                    ('canoe cycling',2,{'1'},{'text':'canoe cycling detail B'}),
                    ('cycling',1,{'2'},{'text':'separate cycling trip'})]
        selected=retrieval.select(candidates,['canoe','cycling'],str,limit=2)
        self.assertEqual(selected[1]['text'],'separate cycling trip')

    def test_selection_skips_oversized_item_without_losing_later_evidence(self):
        candidates=[('canoe',3,{'1'},{'text':'x'*1000}),('canoe',2,{'2'},{'text':'canoe trip'})]
        self.assertEqual(retrieval.select(candidates,['canoe'],str,budget=100),[{'text':'canoe trip'}])

    def test_identical_statements_on_distinct_dates_are_separate_evidence(self):
        rows=[('canoe',t,{str(t)},{'text':'I won a canoe race.'}) for t in (1,2)]
        self.assertEqual(len(retrieval.select(rows,['canoe'],str)),2)

    def test_topic_extension_keeps_new_value_without_repeating_query_word(self):
        self.fact('group','完成三次比赛','I completed three marathons.',1,topic='马拉松')
        self.fact('group','目前累计五次','Now five in total.',2,topic='马拉松',replace=True)
        result=self.store.recall('group','alice',{'query':'marathons'})
        self.assertTrue(any(x['text']=='目前累计五次' and x['status']=='active' for x in result['items']))

    def test_local_quote_wins_deduplication_against_same_shared_value(self):
        self.fact('group','读过The Odyssey','I read The Odyssey in April.',1)
        self.fact('elsewhere','读过The Odyssey','a private cross-scope detail',2,shared=True)
        result=self.store.recall('group','alice',{'query':'Odyssey'})
        self.assertIn('April',str(result));self.assertNotIn('private cross-scope',str(result))

    def test_source_time_is_distinct_from_storage_time(self):
        self.fact('group','这个月参加了皮划艇比赛','I raced my canoe this month.',1688169600)
        row=self.store.recall('group','alice',{'query':'canoe'})['items'][0]
        self.assertTrue(row['asserted_at'].startswith('2023-07-01'))
        self.assertNotEqual(row['asserted_at'],row['recorded_at'])

    def test_extractor_previous_does_not_expose_shared_source_timestamp(self):
        self.fact('elsewhere','读过Odyssey','private source quotation',1234567890,shared=True)
        self.store.observe('group','alice','new',time.time(),'I also enjoy Odyssey.')
        with self.store.db() as db:db.execute('UPDATE events SET received=0')
        ai=Mock();ai.complete.return_value=('{"members":[]}',{})
        self.store.update_once(ai,['group'])
        payload=json.loads(ai.complete.call_args.args[0][1]['content'])
        previous=next(iter(payload['previous'].values()))
        self.assertTrue(previous)
        self.assertNotIn('asserted_at',previous[0])
        self.assertNotIn('private source quotation',str(payload))

    def test_hidden_topic_successor_not_exposed_by_expansion(self):
        self.fact('group','已读Odyssey','I read Odyssey.',1)
        self.fact('alice','已读Iliad','I now read Iliad.',2,replace=True)
        result=self.store.recall('group','alice',{'query':'Odyssey'})
        self.assertNotIn('Iliad',str(result));self.assertNotIn('Odyssey',str(result))

    def test_upgrade_from_21_never_refills_deleted_text(self):
        self.fact('group','读过Odyssey','redacted quotation',1)
        with self.store.db() as db:
            db.execute("UPDATE meta SET value='2.1.0' WHERE key='memory_version'")
            db.execute("UPDATE facts SET evidence='[]'")
            db.execute('DELETE FROM fact_evidence_search')
        again=MemoryService(self.store.base,{'bot_id':'test'})
        self.assertEqual(again.recall('group','alice',{'query':'redacted'})['items'],[])
        with again.db() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='memory_version'").fetchone()[0],retrieval.VERSION)

    def test_small_evidence_can_fill_more_than_six_slots_within_budget(self):
        for i in range(10):self.fact('group',f'读过book {i}',f'I read book number {i}.',i+1,topic=str(i))
        result=self.store.recall('group','alice',{'query':'book'})
        self.assertEqual(len(result['items']),10)
        self.assertLess(len(json.dumps(result,ensure_ascii=False)),6500)


if __name__=='__main__':unittest.main()
