import json
import tempfile
import time
import unittest
from unittest.mock import Mock
from myshadow.member_memory import MemoryService
from myshadow import member_evidence,member_selector


class SemanticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=MemoryService(self.temp.name)
        with self.store.db() as db:db.execute("UPDATE meta SET value='0' WHERE key='started'")
        self.store.observe('g','alice','trip',time.time(),'I cycled to the observatory yesterday.')
        with self.store.db() as db:
            r=dict(db.execute('SELECT * FROM events').fetchone());self.source=r
            member_evidence.write(db,r['scope'],r['member'],[dict(id=r['id'],role='user',kind='event',quote=r['text'],summary='骑车去天文台')],{r['id']:r},self.store._words,self.store.eligible)
            db.execute('UPDATE events SET processed=1')

    def factory(self,callback=None):
        def complete(messages,**kwargs):
            data=json.loads(messages[1]['content']);self.payload=data
            if callback:return callback(data)
            return json.dumps({'ids':[r['id'] for r in data['records'] if 'observatory' in r['quote']]}),{}
        ai=Mock(complete=complete);self.store.reader_factory=lambda:ai

    def test_semantic_catalog_contains_nonlexical_source_and_returns_original(self):
        self.factory()
        result=self.store.recall('g','alice',{'query':'astronomy excursion'})
        self.assertIn('cycled',str(result));self.assertEqual(result['detail'],'semantic')
        self.assertNotIn('骑车去天文台',str(result))

    def test_query_passed_intact_from_context(self):
        self.factory();q='What did I do on my astronomy excursion?'
        self.store.context('g','alice',query=q)
        self.assertEqual(self.payload['question'],q)

    def test_catalog_never_contains_other_member_or_other_scope(self):
        self.factory()
        self.assertEqual(self.store.recall('other','alice',{'query':'astronomy'})['items'],[])
        self.assertEqual(self.store.recall('g','bob',{'query':'astronomy'})['items'],[])

    def test_forged_ids_fall_back_without_fabricating_records(self):
        self.factory(lambda data:('{"ids":[999999]}',{}))
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['detail'],'semantic_failed_lexical_fallback')
        self.assertIn('cycled',str(result));self.assertNotIn('999999',str(result))

    def test_delete_during_model_call_discards_old_material(self):
        def complete(data):
            self.store.manage('g','alice',{'action':'forget','description':'observatory'},'忘记observatory','delete')
            return json.dumps({'ids':[data['records'][0]['id']]}),{}
        self.factory(complete)
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['status'],'changed');self.assertEqual(result['items'],[])

    def test_pause_during_selection_refreshes_automatic_context(self):
        def complete(data):
            self.store.manage('g','alice',{'action':'pause'},'暂停记忆','pause')
            return json.dumps({'ids':[data['records'][0]['id']]}),{}
        self.factory(complete)
        result=json.loads(self.store.context('g','alice',query='observatory'))
        self.assertTrue(result['memory_disabled']);self.assertNotIn('cycled',str(result))

    def test_failure_is_visible_and_does_not_hold_database_lock(self):
        def complete(data):
            with self.store.db() as db:db.execute('UPDATE subjects SET revision=revision+1')
            raise RuntimeError('unavailable')
        self.factory(complete)
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['status'],'changed')

    def test_expiry_during_selection_also_invalidates_candidates(self):
        def complete(data):
            with self.store.db() as db:db.execute('UPDATE events SET received=0')
            self.store.maintain()
            return json.dumps({'ids':[data['records'][0]['id']]}),{}
        self.factory(complete)
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['status'],'changed');self.assertEqual(result['items'],[])

    def test_catalog_and_result_are_bounded(self):
        captured={}
        def complete(messages,**kwargs):
            captured.update(json.loads(messages[1]['content']))
            return json.dumps({'ids':[r['id'] for r in captured['records'][:12]]}),{}
        rows=[('observation '+str(i)+'x'*800,i,{str(i)},{'text':'x'*600,'kind':'source_evidence','status':'quoted','asserted_at':str(i)}) for i in range(700)]
        selected,_=member_selector.choose('observation',rows,lambda:Mock(complete=complete),str)
        self.assertLessEqual(len(captured['records']),240)
        self.assertLessEqual(len(json.dumps(captured['records'],ensure_ascii=False)),160000)
        self.assertLessEqual(len(json.dumps(selected,ensure_ascii=False)),8800)
        self.assertLessEqual(len(selected),16)

    def test_catalog_preserves_qualifying_detail_after_intro(self):
        text='I enjoy crafts. '+('It has been a busy season. '*6)+'I finished the mosaic on Tuesday.'
        captured={}
        def complete(messages,**kwargs):
            captured.update(json.loads(messages[1]['content']))
            return '{"ids":[0]}',{}
        row=(text,1,{'1'},{'text':text,'kind':'source_evidence','status':'quoted'})
        member_selector.choose('crafts', [row],lambda:Mock(complete=complete),str)
        self.assertIn('mosaic on Tuesday',captured['records'][0]['quote'])

    def test_focused_quotes_preserve_host_role_and_date(self):
        def complete(data):
            row=next(r for r in data['records'] if 'observatory' in r['quote'])
            self.assertEqual(row['basis'],'user_statement')
            return json.dumps({'evidence':[{'id':row['id'],'quotes':['I cycled to the observatory yesterday.'],
                'basis':'assistant_statement','asserted_at':'1900-01-01'}]}),{}
        self.factory(complete)
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['detail'],'semantic_verified_context')
        item=result['items'][0];self.assertEqual(item['basis'],'user_statement')
        self.assertNotEqual(item['asserted_at'],'1900-01-01');self.assertTrue(item['selection_verified'])
        self.assertIn('yesterday',item['relative_dates'])

    def test_generated_or_summary_only_quote_is_rejected(self):
        for quote in ('I flew to the observatory.','骑车去天文台'):
            self.factory(lambda data:(json.dumps({'evidence':[{'id':data['records'][0]['id'],'quotes':[quote]}]}),{}))
            result=self.store.recall('g','alice',{'query':'observatory'})
            self.assertEqual(result['detail'],'semantic_failed_lexical_fallback')
            self.assertNotIn(quote,str(result));self.assertIn('cycled',str(result))

    def test_notes_have_local_conflict_and_time_without_cross_scope_time(self):
        with self.store.db() as db:
            c=self.store._ensure(db,'g','alice')
            ident=self.store._fact(db,c['scope'],c['member'],{'slot':'interest','topic':'running',
                'value':'喜欢长跑','kind':'self_report','_visibility':'shared'},0)
            db.execute('UPDATE facts SET conflict=1 WHERE id=?',(ident,))
        self.assertEqual(json.loads(self.store.context('g','alice'))['speaker_notes'],[])
        with self.store.db() as db:db.execute('UPDATE facts SET conflict=0 WHERE id=?',(ident,))
        local=json.loads(self.store.context('g','alice'))['speaker_notes'][0]
        self.assertEqual(local['status'],'active');self.assertIn('asserted_at',local)
        other=json.loads(self.store.context('other','alice'))['speaker_notes']
        self.assertTrue(other);self.assertNotIn('asserted_at',other[0])

    def test_whitespace_difference_returns_original_substring(self):
        source='I visited the museum\n  yesterday.'
        self.assertEqual(member_selector.verified_quote('I visited the museum yesterday.',[source]),source)
        self.assertIsNone(member_selector.verified_quote('I visited the gallery yesterday.',[source]))

    def test_duplicate_record_entries_combine_verified_spans(self):
        def complete(data):
            return json.dumps({'evidence':[{'id':0,'quotes':['I cycled to the observatory']},
                {'id':0,'quotes':['yesterday.']}]}),{}
        self.factory(complete)
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(len(result['items']),1)
        self.assertIn('I cycled to the observatory',result['items'][0]['text'])
        self.assertIn('yesterday.',result['items'][0]['text'])

    def test_verified_selection_keeps_unquoted_qualifiers_and_support(self):
        text='I planned a museum trip but cancelled it after the storm.'
        item={'text':text,'kind':'profile','status':'active','basis':'self_report',
            'source_quotes':['I cancelled the trip.'],'asserted_at':'2026-10-20'}
        response=json.dumps({'evidence':[{'id':0,'quotes':['I planned a museum trip']}]})
        selected,_=member_selector.choose('museum',[ (text,1,{'1'},item) ],lambda:Mock(complete=lambda *a,**k:(response,{})),str)
        self.assertEqual(selected[0]['text'],text)
        self.assertEqual(selected[0]['source_quotes'],item['source_quotes'])
        self.assertNotIn('selection_verified',item)

    def test_valid_empty_selection_retains_lexical_background(self):
        self.factory(lambda data:('{"evidence":[]}',{}))
        result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertIn('cycled',str(result));self.assertEqual(result['detail'],'semantic_verified_context')

    def test_automatic_semantic_budget_is_bounded_and_optional(self):
        self.factory()
        self.store.recall=Mock(side_effect=lambda *a,**k:{'status':'ok','items':[
            {'text':str(i)+'x'*750} for i in range(20)],'partial':False})
        context=self.store.context('g','alice',query='observatory')
        self.assertGreater(len(context),6500);self.assertLessEqual(len(context),9000)
        self.store.config.setdefault('member_memory',{})['semantic_read']=False
        self.assertLessEqual(len(self.store.context('g','alice',query='observatory')),6500)

    def test_lexical_complement_prefers_new_sources_and_is_bounded(self):
        def row(i,source):
            return ('camera '+str(i),i,{source},{'text':'camera '+str(i),'kind':'source_evidence','basis':'user_statement'})
        rows=[row(0,'selected')]+[row(i,'selected' if i>4 else str(i)) for i in range(1,20)]
        def complete(messages,**kwargs):
            catalog=json.loads(messages[1]['content'])['records']
            ident=next(r['id'] for r in catalog if r['quote']=='camera 0')
            return json.dumps({'ids':[ident]}),{}
        selected,_=member_selector.choose('camera',rows,lambda:Mock(complete=complete),str)
        self.assertEqual(len(selected),5)
        self.assertEqual({x['text'] for x in selected},{'camera '+str(i) for i in range(5)})

    def statement(self,text,source='1',date=1,basis='user_statement',kind='source_evidence'):
        item={'text':text,'kind':kind,'basis':basis,'asserted_at':str(date)}
        return ((text,date,{source},item),item)

    def test_subsumed_same_source_statement_keeps_complete_context(self):
        short=self.statement('I planned a museum trip.')
        full=self.statement('I planned a museum trip. However, I cancelled it.',kind='experience')
        for entries in ([short,full],[full,short]):
            chosen=member_selector.distinct_statements(entries)
            self.assertEqual(chosen,[full]);self.assertIn('cancelled',chosen[0][1]['text'])
        self.assertEqual(short[1]['text'],'I planned a museum trip.')

    def test_same_text_different_sources_roles_or_times_is_preserved(self):
        first=self.statement('I finished a model.')
        others=[self.statement('I finished a model.',source='2'),
            self.statement('I finished a model.',date=2),
            self.statement('I finished a model.',basis='assistant_statement')]
        for second in others:self.assertEqual(member_selector.distinct_statements([first,second]),[first,second])

    def test_profile_status_is_never_removed_by_source_deduplication(self):
        fact=self.statement('I own a telescope.',kind='profile');fact[1]['status']='superseded'
        quote=self.statement('I own a telescope.')
        self.assertEqual(member_selector.distinct_statements([fact,quote]),[fact,quote])

    def test_noncontiguous_complementary_windows_are_preserved(self):
        first=self.statement('The first lens was damaged.')
        second=self.statement('The second lens was repaired.')
        self.assertEqual(member_selector.distinct_statements([first,second]),[first,second])

    def test_focus_values_are_exact_original_fragments_with_full_context(self):
        def complete(data):
            row=next(r for r in data['records'] if 'observatory' in r['quote'])
            return json.dumps({'evidence':[{'id':row['id'],'quotes':[row['quote']],
                'focus':['cycled','observatory','yesterday']}]}),{}
        self.factory(complete);result=self.store.recall('g','alice',{'query':'observatory'})
        item=result['items'][0];self.assertEqual(item['focus_spans'],['cycled','observatory','yesterday'])
        self.assertEqual(item['text'],'I cycled to the observatory yesterday.')
        self.assertEqual(item['basis'],'user_statement')

    def test_invalid_focus_is_filtered_without_losing_valid_evidence(self):
        def complete(data):
            row=next(r for r in data['records'] if 'observatory' in r['quote'])
            return json.dumps({'evidence':[{'id':row['id'],'quotes':[row['quote']],
                'focus':['observatory','flew','骑车去天文台',123]}]}),{}
        self.factory(complete);result=self.store.recall('g','alice',{'query':'observatory'})
        self.assertEqual(result['detail'],'semantic_verified_context_focus_filtered')
        self.assertEqual(result['items'][0]['focus_spans'],['observatory'])
        self.assertNotIn('flew',str(result));self.assertIn('cycled',str(result))

    def test_subsumed_statements_merge_complementary_verified_focus(self):
        short=self.statement('I own a telescope.');short[1]['focus_spans']=['telescope']
        full=self.statement('I own a telescope. I also use binoculars.',kind='experience');full[1]['focus_spans']=['binoculars']
        result=member_selector.distinct_statements([short,full])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0][1]['focus_spans'],['binoculars','telescope'])
        self.assertEqual(full[1]['focus_spans'],['binoculars'])

    def test_focus_total_budget_keeps_whole_fragments_and_original_text(self):
        entries=[]
        for i in range(12):
            values=[str(i)+'-'+str(j)+'x'*90 for j in range(3)]
            entry=self.statement(' '.join(values),source=str(i));entry[1]['focus_spans']=values;entries.append(entry)
        result=member_selector.focus_budget(entries)
        self.assertLessEqual(sum(len(v) for _,item in result for v in item.get('focus_spans',[])),600)
        for (_,item),(_,original) in zip(result,entries):
            self.assertEqual(item['text'],original['text'])
            self.assertTrue(all(v in original['focus_spans'] for v in item.get('focus_spans',[])))

    def test_whitespace_focus_merge_resolves_to_retained_original(self):
        base={'text':'I used a red\n  brush.','focus_spans':[]}
        merged=member_selector.merge_focus(base,{'focus_spans':['red brush','blue brush']})
        self.assertEqual(merged['focus_spans'],['red\n  brush'])


if __name__=='__main__':unittest.main()
