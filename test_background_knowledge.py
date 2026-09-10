import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from background_knowledge import BackgroundKnowledge,build_index,BACKGROUND_TOOL
from group_personas import load_personas


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.dir=self.root/'knowledge';self.dir.mkdir()
        self.cards=[{'id':'person-gu','title':'顾飞','kind':'person','period':'当前关系',
            'content':'顾飞是蒋丞的男朋友，擅长摄影，也会弹吉他。过去的经历不能说明现在的位置。','entities':['顾飞','蒋丞'],'tags':['男朋友','摄影']},
            {'id':'memory-camera','title':'拍照经历','kind':'memory','period':'过去经历 20',
             'content':'蒋丞曾为顾飞的摄影工作做模特。','entities':['顾飞','蒋丞'],'tags':['摄影','模特'],'sequence':20},
            {'id':'person-pan','title':'潘智','kind':'person','period':'当前关系',
             'content':'潘智是蒋丞的老朋友。','entities':['潘智','蒋丞'],'tags':['朋友']}]
        (self.dir/'cards.json').write_text(json.dumps(self.cards,ensure_ascii=False),encoding='utf-8')
        (self.dir/'aliases.json').write_text(json.dumps({'顾飞':'顾飞','大飞':'顾飞','潘智':'潘智'}),encoding='utf-8')
        (self.dir/'core.md').write_text('顾飞是蒋丞的男朋友。',encoding='utf-8')
        (self.root/'persona.md').write_text('你的名字是蒋丞。',encoding='utf-8')
        self.config={'group_personas':{'123@chatroom':{'background':'knowledge','name':'蒋丞','prompt_file':'persona.md'}}}
        build_index(self.dir);self.k=BackgroundKnowledge(self.root,self.config)

    def test_group_scope_and_aliases(self):
        for text in ['你的顾飞呢','大飞在哪','顾飞摄影']:
            r=self.k.search('123@chatroom',{'query':text})
            self.assertEqual(r['cards'][0]['record_id'],'person-gu')
        self.assertEqual(self.k.search('other',{'query':'顾飞'})['status'],'unavailable')
        self.assertEqual(self.k.automatic('other','顾飞'),'')
        self.assertEqual(self.k.automatic('123@chatroom','今天天气怎么样'),'')
        self.assertIn('顾飞',self.k.automatic('123@chatroom','你的大飞呢'))

    def test_person_overview_survives_dense_candidate_window(self):
        cards=self.cards+[{'id':'dense-'+str(i),'title':'顾飞呢','kind':'memory','period':'过去',
            'content':'顾飞呢','entities':['顾飞'],'tags':[]} for i in range(130)]
        (self.dir/'cards.json').write_text(json.dumps(cards,ensure_ascii=False),encoding='utf-8')
        build_index(self.dir)
        self.assertEqual(self.k.search('123@chatroom',{'query':'你的顾飞呢'})['cards'][0]['record_id'],'person-gu')

    def test_invalid_arguments_sql_and_fts_syntax(self):
        for args in [{'query':'顾飞','group':'other'},{'query':'顾飞','limit':True},{'query':''},{'query':'a'*101}]:
            self.assertIn('error',self.k.search('123@chatroom',args))
        self.k.search('123@chatroom',{'query':'" OR * ; DROP TABLE cards; --'})
        self.assertEqual(len(self.k.search('123@chatroom',{'query':'顾飞'})['cards']),2)

    def test_result_budget_and_per_turn_limit(self):
        r=self.k.search('123@chatroom',{'query':'顾飞','limit':1},max_chars=30)
        self.assertTrue(r['partial'])
        h=self.k.handler('123@chatroom');first=h({'query':'顾飞'})
        self.assertEqual(h({'query':'顾飞'}),first)
        h({'query':'潘智'})
        self.assertIn('error',h({'query':'摄影'}))
        self.assertNotIn('error',self.k.handler('123@chatroom')({'query':'摄影'}))

    def test_core_prompt_loading_and_automatic_context(self):
        from bot import Bot
        b=Bot.__new__(Bot);b.config=self.config;b.personas=load_personas(self.config,self.root);b.background=self.k
        payload=b.message_payload({'group_id':'123@chatroom','group_name':'test'},[],'user','你的顾飞呢','')
        self.assertIn('男朋友',payload[0]['content'])
        self.assertIn('背景记忆',payload[1]['content'])
        self.assertEqual(payload[-1]['content'],'当前提问者：user\n当前提问：你的顾飞呢')
        self.assertNotIn('男朋友',b.system_prompt_for('other'))

    def test_failed_index_is_not_empty_memory(self):
        (self.dir/'index.sqlite').write_bytes(b'bad sqlite')
        self.assertEqual(self.k.search('123@chatroom',{'query':'顾飞'})['status'],'failed')

    def test_directory_escape_rejected(self):
        self.config['group_personas']['123@chatroom']['background']='../escape'
        with self.assertRaises(ValueError):BackgroundKnowledge(self.root,self.config)

    def test_readonly_material_route_keeps_background_tool(self):
        from agent_runtime.ai import complete_chat
        ai=Mock(config={});ai.harness.research.return_value=Mock(text='结果',usage={})
        handler=Mock(return_value={'status':'empty','cards':[]})
        complete_chat(ai,[{'role':'user','content':'整理顾飞的资料'}],None,[BACKGROUND_TOOL],handler,None)
        kwargs=ai.harness.research.call_args.kwargs
        self.assertEqual(kwargs['tools'][0]['name'],'search_background')
        kwargs['handler']('search_background',{'query':'顾飞'})
        handler.assert_called_once_with('search_background',{'query':'顾飞'})

    def test_worker_offers_tool_with_host_bound_scope(self):
        import time
        from bot import Bot
        b=Bot.__new__(Bot);b.config={**self.config,'mode':'preview','max_age_seconds':300}
        b.personas=load_personas(b.config,self.root);b.background=self.k
        b.state=sqlite3.connect(':memory:');b.state.row_factory=sqlite3.Row;self.addCleanup(b.state.close)
        b.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        b.state.execute("INSERT INTO replies VALUES(1,'123@chatroom','test',1,?,'你的顾飞呢',NULL,'pending')",(int(time.time()),))
        b.require_group=Mock();b.participation_current=Mock(return_value=True);b.ai=Mock()
        b.messages_for=Mock(return_value=(b.message_payload({'group_id':'123@chatroom','group_name':'test'},[],'user','你的顾飞呢',''),0))
        with patch('bot.chat_complete',return_value=('怎么，找他有事？',{})) as complete:
            b.process_group('123@chatroom')
        options=complete.call_args.kwargs
        self.assertIn('search_background',[t['function']['name'] for t in options['extra_tools']])
        result=options['tool_handler']('search_background',{'query':'顾飞'})
        self.assertEqual(result['cards'][0]['record_id'],'person-gu')
        self.assertIn('error',options['tool_handler']('search_background',{'query':'顾飞','group':'other'}))

    def test_no_match_is_explicit_and_prompt_budget_is_group_scoped(self):
        self.assertEqual(self.k.search('123@chatroom',{'query':'量子飞艇无人岛'})['status'],'empty')
        from bot import Bot,estimate_tokens
        config={**self.config,'context_token_budget':12800}
        config['group_personas']['123@chatroom']['context_token_budget']=20000
        b=Bot.__new__(Bot);b.config=config;b.personas=load_personas(config,self.root)
        self.assertEqual(b.context_budget_for('123@chatroom'),20000)
        self.assertEqual(b.context_budget_for('other'),12800)


if __name__=='__main__':unittest.main()
