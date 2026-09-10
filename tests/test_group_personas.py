import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from myshadow.bot import Bot, prompt_for
from myshadow.group_personas import load_personas
from myshadow.selective_reply import SelectiveReply
from activity_runtime.host import Host

A='10000000001@chatroom'
B='10000000002@chatroom'
PROFILES={A:{'name':'牢影','aliases':['牢影','影','道长'],'prompt_file':'personas/laoying.md'},
          B:{'name':'蒋丞','aliases':['蒋丞','丞哥','丞丞','猫丞丞'],'prompt_file':'personas/jiangcheng.md'}}


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.bot=Bot.__new__(Bot)
        self.bot.config={'bot_id':'bot','bot_name':'影','system_prompt':'default persona',
                         'group_personas':PROFILES,'max_age_seconds':300}
        self.bot.personas=load_personas(self.bot.config,Path(__file__).resolve().parents[1])
        self.bot.activity_hint='活动功能仍然可用'

    def test_parallel_group_prompts_and_legacy_fallback(self):
        def payload(group):
            return self.bot.message_payload({'group_id':group,'group_name':'same display name'},[],
                'user','你叫什么？','过去我叫影')[0]['content']
        with ThreadPoolExecutor(max_workers=4) as pool:
            prompts=list(pool.map(payload,[A,B]*20))
        for i,p in enumerate(prompts):
            self.assertIn('牢影' if i%2==0 else '蒋丞',p)
            self.assertNotIn('蒋丞' if i%2==0 else '牢影',p)
            self.assertNotIn('王也',p)
            self.assertIn('search_history',p)
            self.assertIn('search_stickers',p)
            self.assertIn('活动功能仍然可用',p)
        self.assertIn('default persona',payload('other'))
        self.assertEqual(self.bot.config['system_prompt'],'default persona')
        with self.assertRaises(TypeError):self.bot.personas[A]['name']='changed'

    def test_actual_mentions_keep_account_identity(self):
        row={'local_type':1,'create_time':1000,'source':'<msgsource><atuserlist>bot</atuserlist></msgsource>'}
        for group,name in [(A,'牢影'),(B,'蒋丞'),(B,'影')]:
            row['message_content']='user:\n@'+name+'\u2005你好'
            self.assertEqual(prompt_for(row,'user',self.bot.mention_config(group),1001),'你好')
        row['source']='<msgsource/>'
        self.assertIsNone(prompt_for(row,'user',self.bot.mention_config(B),1001))

    def test_aliases_and_decision_style_are_group_scoped(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
        db.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,prompt,reply)')
        ai=Mock(config={'model':'test'})
        ai.request.return_value={'choices':[{'message':{'content':'{"action":"silent","goal":""}'}}]}
        p=SelectiveReply(db,ai,{'groups':[A,B],'decision':{'enabled':True}},'bot',self.bot.persona_for)
        now=int(time.time())
        for i,(g,word,expected) in enumerate([(A,'道长',1),(B,'道长',0),(B,'丞哥',1),(A,'丞哥',0)]):
            p.observe(g,'shard',{'local_id':i,'create_time':now},'user',word+'，你好',None)
            self.assertEqual(db.execute('SELECT addressed FROM participation_messages WHERE local_id=?',(i,)).fetchone()[0],expected)
        for group,name,other in [(A,'牢影','蒋丞'),(B,'蒋丞','牢影')]:
            context,_,_=p.decision.context(group,now,'user')
            p.decision.judge(context,'message')
            system=ai.request.call_args.args[1]['messages'][0]['content']
            self.assertIn(name,system);self.assertNotIn(other,system);self.assertNotIn('王也',system)

    def test_scheduled_ai_uses_task_group_persona(self):
        b=self.bot;b.config['mode']='send';b.weather=None;b.search=None
        b.require_group=Mock();b.scheduler=Mock();b.ai=Mock();b.send=Mock()
        b.state=sqlite3.connect(':memory:');b.state.row_factory=sqlite3.Row;self.addCleanup(b.state.close)
        b.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        b.state.execute('CREATE TABLE scheduled_runs(id,reply_id)')
        b.scheduler.claim.return_value={'group_id':B,'action':'ask_ai','run_id':'one','text':'你叫什么',
            'task_id':'task','due':int(time.time())}
        # Any downstream scheduler storage details are exercised by existing schedule tests.
        with patch('myshadow.bot.chat_complete',return_value=('蒋丞。',{})) as complete:
            b.run_scheduled([B])
        self.assertIn('蒋丞',complete.call_args.args[1][0]['content'])
        self.assertNotIn('牢影',complete.call_args.args[1][0]['content'])

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg={'group_personas':{A:{'name':'a','prompt_file':'../outside.md'}}}
            with self.assertRaises(ValueError):load_personas(cfg,Path(d))

    def test_activity_host_and_commands_use_current_group_identity(self):
        import sys
        from activity_runtime.registry import Registry
        from activity_runtime.contracts import Message
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
        db.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        registry=Registry(Path(__file__).resolve().parents[1]/'skills',['turtle_soup'])
        host=Host(db,registry,Mock(),mode='preview',persona=self.bot.persona_for)
        for group,name in [(A,'牢影'),(B,'蒋丞')]:
            host.ingest(group,Message('start','owner','owner','来一局海龟汤',int(time.time())))
            host.process(group)
            self.assertIn('我是'+name,db.execute('SELECT reply FROM replies WHERE group_id=?',(group,)).fetchone()[0])
            session=host.current(group)
            ctx=host.context(registry.get('turtle_soup'),session,Message('hint','owner','owner','提示',int(time.time())))
            commands=sys.modules['installed_skills.turtle_soup.commands']
            with patch.object(commands,'semantic_intent',side_effect=AssertionError('Local alias should take fast path')):
                self.assertEqual(commands.recognize(ctx,json.loads(session['public']),name+'，给个提示')[0],'hint')


if __name__=='__main__':unittest.main()
