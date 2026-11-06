import tempfile
import unittest

from myshadow.member_memory import MemoryService


class TemporalProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryService(self.tmp.name,{'bot_id':'temporal-test'})
        self.group='group';self.sender='alice'

    def fact(self,value,when,replace=False,topic='reading total',group=None,**extra):
        with self.store.db() as db:
            control=self.store._ensure(db,group or self.group,self.sender)
            item={'slot':'background','topic':topic,'value':value,'replace':replace,
                '_asserted_at':when,'evidence':[{'quote':value}],**extra}
            return self.store._fact(db,control['scope'],control['member'],item,control['version'],now=1000+when)

    def rows(self):
        with self.store.db() as db:return {r['id']:dict(r) for r in db.execute('SELECT * FROM facts')}

    def test_ordinary_conflict_is_not_resolved_by_arrival_order(self):
        for first,second in ((20,10),(10,20)):
            with self.subTest(first=first,second=second):
                topic='topic '+str(first)
                a=self.fact('finished four novels',first,topic=topic)
                b=self.fact('finished six novels',second,topic=topic)
                rows=self.rows()
                self.assertEqual([(rows[i]['status'],rows[i]['conflict']) for i in (a,b)],
                    [('active',1),('active',1)])
                self.assertTrue(all(rows[i]['replaced_by'] is None for i in (a,b)))

    def test_explicit_repeated_value_resolves_all_conflicting_versions(self):
        a=self.fact('finished four novels',10)
        b=self.fact('finished six novels',20)
        c=self.fact('finished six novels',30,replace=True)
        rows=self.rows()
        self.assertEqual((rows[c]['status'],rows[c]['conflict']),('active',0))
        self.assertEqual(rows[c]['asserted_at'],30)
        for ident in (a,b):
            self.assertEqual((rows[ident]['status'],rows[ident]['conflict']),('superseded',0))
            self.assertEqual(rows[ident]['replaced_by'],c)
            self.assertEqual(rows[ident]['valid_to'],30)
        self.assertEqual(rows[c]['supersedes'],b)

    def test_explicit_same_value_retains_new_date_and_prevents_stale_replacement(self):
        a=self.fact('finished four novels',10)
        b=self.fact('finished four novels',30,replace=True)
        c=self.fact('finished six novels',20,replace=True)
        rows=self.rows()
        self.assertNotEqual(a,b)
        self.assertEqual(rows[b]['status'],'active')
        self.assertEqual(rows[c]['status'],'superseded')
        self.assertEqual(rows[c]['replaced_by'],b)
        self.assertEqual(rows[a]['valid_to'],20)
        self.assertEqual(rows[c]['valid_to'],30)
        self.assertEqual(rows[b]['recorded_at'],1030)

    def test_late_explicit_update_fills_history_without_replacing_current(self):
        a=self.fact('finished four novels',10)
        unrelated=self.fact('weekly climbing',15,topic='exercise')
        current=self.fact('finished eight novels',40,replace=True)
        middle=self.fact('finished six novels',30,replace=True)
        early=self.fact('finished five novels',20,replace=True)
        rows=self.rows()
        self.assertEqual({i for i,r in rows.items() if r['status']=='active'},{current,unrelated})
        self.assertEqual([rows[i]['valid_to'] for i in (a,early,middle)],[20,30,40])
        self.assertTrue(all(rows[i]['replaced_by']==current for i in (a,early,middle)))
        self.assertTrue(all(rows[i]['conflict']==0 for i in rows))

    def test_simultaneous_differing_replacements_require_confirmation(self):
        a=self.fact('finished four novels',20,replace=True)
        b=self.fact('finished six novels',20,replace=True)
        rows=self.rows()
        self.assertTrue(all(rows[i]['status']=='active' and rows[i]['conflict']==1 for i in (a,b)))
        self.assertNotIn('finished four novels',self.store.context(self.group,self.sender))

    def test_ordinary_repetition_does_not_confirm_a_conflict(self):
        self.fact('finished four novels',10)
        self.fact('finished six novels',20)
        repeated=self.fact('finished six novels',30)
        rows=self.rows()
        self.assertEqual(rows[repeated]['conflict'],1)
        self.assertTrue(all(r['status']=='active' for r in rows.values()))

    def test_management_correction_inherits_target_topic(self):
        original=self.fact('完成四本小说',10,topic='小说累计阅读量')
        result=self.store.manage(self.group,self.sender,
            {'action':'correct','description':'完成四本小说','value':'完成六本小说'},
            '把完成四本小说改成完成六本小说','correction')
        self.assertEqual(result['status'],'corrected')
        rows=self.rows();replacement=rows[original]['replaced_by']
        self.assertEqual(rows[replacement]['topic'],'小说累计阅读量')
        self.assertEqual(rows[replacement]['supersedes'],original)

    def test_private_confirmation_suppresses_all_public_old_versions(self):
        a=self.fact('finished four novels',10,_visibility='shared')
        b=self.fact('finished six novels',20,_visibility='shared')
        latest=self.fact('finished six novels',30,replace=True,group=self.sender)
        self.assertEqual(self.store.recall('elsewhere',self.sender,{'query':'novels'})['items'],[])
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':'novels'})['items'],[])
        self.assertIn('finished six novels',self.store.context(self.sender,self.sender))
        rows=self.rows()
        self.assertTrue(all(rows[i]['replaced_by']==latest for i in (a,b)))
        self.assertEqual(rows[latest]['visibility'],'local')

    def test_forgetting_confirmed_value_removes_entire_version_chain(self):
        self.fact('完成四本小说',10,topic='小说累计阅读量')
        self.fact('完成六本小说',20,topic='小说累计阅读量')
        self.fact('完成六本小说',30,replace=True,topic='小说累计阅读量')
        kept=self.fact('喜欢攀岩',25,topic='运动')
        result=self.store.manage(self.group,self.sender,{'action':'forget','description':'完成六本小说'},
            '忘记完成六本小说','forget')
        self.assertEqual(result['status'],'forgotten')
        self.assertEqual(set(self.rows()),{kept})
        self.assertEqual(self.store.recall(self.group,self.sender,{'query':'小说'})['items'],[])


if __name__=='__main__':unittest.main()
