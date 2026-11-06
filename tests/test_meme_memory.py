import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from myshadow.meme_memory import MemeMemory, MemeSearch, DEFAULTS


class Memes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.settings(**{})
        self.m=MemeMemory(self.root)

    def settings(self,**opts):
        (self.root/'meme-settings.json').write_text(json.dumps({**DEFAULTS,**opts}),encoding='utf-8')

    def manage(self,scope='a',actor='user',prompt='记住这个梗：摸鱼表示偷闲',**kwargs):
        return self.m.handler(scope,actor,prompt,'source',**kwargs)

    def test_local_isolation_and_user_evidence(self):
        self.assertEqual(self.manage()({'action':'remember','term':'摸鱼','meaning':'偷闲'})['status'],'saved')
        self.assertIn('偷闲',self.m.context('a','摸鱼'))
        self.assertEqual('',self.m.context('b','摸鱼'))
        self.assertIn('error',self.manage()({'action':'remember','term':'摸鱼','meaning':'未经解释的含义'}))

    def test_permissions_and_readonly(self):
        args={'action':'remember','term':'摸鱼','meaning':'偷闲','scope':'public'}
        self.assertIn('error',self.manage()(args))
        self.assertIn('error',self.manage(readonly=True)({**args,'scope':'local'}))
        self.assertIn('error',self.manage(prompt='查看梗摸鱼')({**args,'scope':'local'}))

    def test_dedup_window_and_disable(self):
        now=time.time()
        self.m.observe('a','u','x','摸鱼',now);self.m.observe('a','u','x','摸鱼',now)
        self.m.observe('a','u','old','摸鱼',now-86400)
        with self.m.db() as db:self.assertEqual(1,db.execute('SELECT count(*) FROM messages').fetchone()[0])
        self.settings(enabled=False)
        self.m.observe('a','u','y','摸鱼',now)
        self.assertEqual('',self.m.context('a','摸鱼'))
        with self.m.db() as db:self.assertEqual(1,db.execute('SELECT count(*) FROM messages').fetchone()[0])

    def test_delete_and_idempotent(self):
        args={'action':'remember','term':'摸鱼','meaning':'偷闲'}
        handle=self.manage();self.assertEqual(handle(args),handle(args))
        self.manage(prompt='删除摸鱼这个梗')({'action':'forget','term':'摸鱼'})
        self.assertEqual('',self.m.context('a','摸鱼'))
        with self.m.db() as db:self.assertEqual(1,db.execute('SELECT deleted FROM memes').fetchone()[0])

    def test_settings_validation(self):
        self.settings(min_mentions=5);self.assertEqual(5,self.m.reload()['min_mentions'])
        self.settings(min_mentions=True);self.assertEqual(5,self.m.reload()['min_mentions'])
        self.settings(use_probability=0)
        self.manage()({'action':'remember','term':'摸鱼','meaning':'偷闲'})
        self.assertIn('"allow_use": false',self.m.context('a','摸鱼'))

    def discover(self,answers,search_result=None):
        class Search:
            calls=0
            def query(inner,query):
                inner.calls+=1
                return search_result or {'summary':'摸鱼是网络梗 https://example.org/meme'}
        search=Search()
        with patch.object(self.m,'judge',side_effect=answers):self.m.discover(None,search,'a')
        return search

    def seed(self,n=3):
        for i in range(n):self.m.observe('a','u',str(i),'今天摸鱼了',time.time())

    def test_confirmed_public_and_source(self):
        self.seed()
        self.discover([{'terms':['摸鱼']},{'confirmed':True,'meaning':'偷闲','source_urls':['https://example.org/meme']}])
        self.assertIn('偷闲',self.m.context('b','摸鱼'))

    def test_model_cannot_invent_repetition(self):
        self.seed(2);self.m.observe('a','u','other','不同概念',time.time())
        self.assertEqual(0,self.discover([{'terms':['摸鱼']}]).calls)
        self.assertEqual('',self.m.context('a','摸鱼'))

    def test_failure_and_fabricated_sources(self):
        self.seed()
        self.discover([{'terms':['摸鱼']},{'confirmed':True,'meaning':'偷闲','source_urls':['https://fake.example/meme']}])
        self.assertEqual('',self.m.context('a','摸鱼'))

    def test_delete_during_search_cannot_restore(self):
        self.seed()
        def judge(ai,instruction,data):
            if 'terms' in instruction:return {'terms':['摸鱼']}
            self.manage(prompt='删除摸鱼这个梗')({'action':'forget','term':'摸鱼'})
            return {'confirmed':True,'meaning':'偷闲','source_urls':['https://example.org/meme']}
        class Search:
            def query(self,q):return {'summary':'https://example.org/meme'}
        with patch.object(self.m,'judge',side_effect=judge):self.m.discover(None,Search(),'a')
        self.assertEqual('',self.m.context('b','摸鱼'))

    def test_explicit_unknown_uses_search_and_admin_delete(self):
        class Search:
            def query(self,q):return {'summary':'https://example.org/meme'}
        with patch.object(self.m,'judge',return_value={'confirmed':True,'meaning':'偷闲','source_urls':['https://example.org/meme']}):
            result=self.manage(prompt='记住摸鱼这个梗',ai=object(),search=Search())({'action':'remember','term':'摸鱼'})
        self.assertEqual('public',result['scope'])
        self.assertIn('偷闲',self.m.context('b','摸鱼'))
        self.assertIn('error',self.manage(prompt='删除摸鱼这个梗')({'action':'forget','term':'摸鱼','scope':'public'}))
        self.settings(admin_ids=['owner'])
        self.assertEqual('deleted',self.manage(actor='owner',prompt='删除摸鱼这个梗')({'action':'forget','term':'摸鱼','scope':'public'})['status'])

    def test_cooldown_stale_and_length(self):
        self.settings(use_probability=1)
        self.manage()({'action':'remember','term':'摸鱼','meaning':'偷闲'})
        self.assertIn('"allow_use": true',self.m.context('a','摸鱼'))
        self.m.mark_used('a','今天摸鱼')
        self.assertIn('"allow_use": false',self.m.context('a','摸鱼'))
        self.assertIn('偷闲',self.m.context('a','摸鱼'))
        with self.m.db() as db:
            for i in range(8):self.m.store(db,'a','梗'+str(i),'长'*350,[])
        self.settings(max_context_chars=300)
        context=self.m.context('a','梗0 梗1 梗2 梗3 梗4 梗5 梗6 梗7')
        self.assertLess(len(context),500)

    def test_prepared_search_cannot_switch_term(self):
        from unittest.mock import Mock
        ai=Mock();search=Mock();search.query.return_value={'summary':'公开资料'}
        with patch.object(self.m,'judge',return_value={'term':'摸鱼','needs_search':True}):
            prepared=self.m.prepare(ai,search,'记住摸鱼这个梗')
        self.assertEqual('公开资料',prepared.query('摸鱼 网络梗 含义 出处 用法')['summary'])
        self.assertIn('error',prepared.query('别的概念 网络梗 含义 出处 用法'))
        search.query.assert_called_once()

    def test_harness_text_without_search_is_not_evidence(self):
        from unittest.mock import Mock
        with patch('agent_runtime.engine.DshEngine') as engine:
            engine.return_value.research.return_value=Mock(sources=[],text='看似真实的解释')
            search=MemeSearch(None,self.root)
            self.assertIn('error',search.query('摸鱼'))
            engine.return_value.research.return_value=Mock(sources=[{'name':'web_search','result':{'isError':False,'value':{'url':'https://example.org/meme'}}}],text='真实公开资料')
            result=search.query('摸鱼')
            self.assertIn('https://example.org/meme',result['source_urls'])

    def test_search_quota_and_negation(self):
        self.seed();self.settings(max_searches_per_day=0)
        self.assertEqual(0,self.discover([{'terms':['摸鱼']}]).calls)
        self.assertIn('error',self.manage(prompt='不要记住摸鱼这个梗')({'action':'remember','term':'摸鱼','meaning':'偷闲'}))


if __name__=='__main__':unittest.main()
