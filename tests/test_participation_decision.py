import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from myshadow.selective_reply import SelectiveReply


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status,UNIQUE(group_id,shard,local_id))')
        self.ai=Mock(config={'model':'test'})
        self.now=int(datetime(2026,9,8,12,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        self.clock=patch('myshadow.participation_decision.time.time',return_value=self.now).start()
        self.addCleanup(patch.stopall)
        self.p=SelectiveReply(self.db,self.ai,{'groups':['111@chatroom'],'decision':{'enabled':True}},'bot')
        self.serial=0
        self.response('brief','接一句下班的梗')

    def response(self,action,goal=''):
        self.ai.request.return_value={'choices':[{'message':{'content':json.dumps({'action':action,'goal':goal})}}]}

    def add(self,text,sender='alice',age=0,explicit=False):
        self.serial+=1
        with self.db:
            self.p.observe('111@chatroom','message/message_0.db',{'local_id':self.serial,'sort_seq':self.serial,'create_time':self.clock.return_value-age},sender,text,None,explicit)
        return self.serial

    def advance(self,seconds=3):
        self.clock.return_value+=seconds
        self.p.consider('111@chatroom')

    def replies(self):
        return self.db.execute('SELECT * FROM replies').fetchall()

    def test_statement_and_joke_can_get_a_reply(self):
        self.add('今天上班简直像在渡劫')
        self.advance()
        self.assertEqual(len(self.replies()),1)
        self.assertEqual(self.p.decision.plan(self.replies()[0]['id'])['style'],'brief')

    def test_silent_decision_is_not_repeated_on_empty_polls(self):
        self.response('silent')
        self.add('哈哈哈哈')
        for _ in range(4):self.advance()
        self.ai.request.assert_called_once()
        self.assertEqual(self.replies(),[])

    def test_wait_reconsiders_once_without_new_message(self):
        self.response('wait')
        self.add('这事儿怎么说呢')
        self.advance()
        self.assertEqual(self.replies(),[])
        self.advance(5)
        self.ai.request.assert_called_once()
        self.response('brief','接一句')
        self.advance(6)
        self.assertEqual(len(self.replies()),1)
        self.assertEqual(self.ai.request.call_count,2)

    def test_repeated_wait_stops(self):
        self.response('wait')
        self.add('嗯我想想')
        self.advance();self.advance(11);self.advance(11)
        self.assertEqual(self.ai.request.call_count,2)

    def test_explicit_call_bypasses_decider_and_night_hours(self):
        self.clock.return_value=int(datetime(2026,9,8,23,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        self.add('影，来一下')
        self.advance()
        self.assertEqual(len(self.replies()),1)
        self.ai.request.assert_not_called()

    def test_unsolicited_is_silent_at_night(self):
        self.clock.return_value=int(datetime(2026,9,8,23,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        self.add('今天真累')
        self.advance()
        self.ai.request.assert_not_called()
        self.assertEqual(self.replies(),[])

    def test_idle_initiation_has_context_and_daily_quota(self):
        self.response('silent')
        self.add('周末准备去爬山，不过路线还没定')
        self.advance()
        self.response('initiate','接续爬山话题，聊选路线时可以先考虑距离')
        self.advance(300)
        row=self.replies()[0]
        self.assertEqual(row['shard'],'_proactive')
        plan=self.p.decision.plan(row['id'])
        self.assertIn('爬山',plan['context'])
        self.assertEqual(plan['anchor_local_id'],1)
        with self.db:self.db.execute("UPDATE replies SET status='expired'")
        self.response('silent');self.add('改天还想去露营');self.advance(10)
        self.response('initiate','聊露营');self.advance(301)
        self.assertEqual(len(self.replies()),1)

    def test_no_proactive_without_recent_human_context(self):
        self.response('initiate','随便打招呼')
        self.advance(400)
        self.ai.request.assert_not_called()

    def test_no_proactive_after_unanswered_bot_reply(self):
        self.add('最近想爬山');self.advance()
        with self.db:
            self.db.execute("UPDATE replies SET status='confirmed',reply='可以先找条轻松路线'")
        self.p.confirmed('111@chatroom')
        self.response('initiate','再问一次')
        self.advance(310)
        self.assertEqual(len(self.replies()),1)

    def test_decision_failure_is_fail_closed_and_durable(self):
        self.ai.request.side_effect=TimeoutError()
        self.add('今天好累');self.advance()
        self.p=SelectiveReply(self.db,self.ai,{'groups':['111@chatroom'],'decision':{'enabled':True}},'bot')
        self.advance()
        self.ai.request.assert_called_once()
        self.assertEqual(self.replies(),[])

    def test_hourly_reply_quota_cannot_be_overridden_by_model(self):
        self.p.decision.max_replies_per_hour=1
        self.add('今天累');self.advance()
        with self.db:self.db.execute("UPDATE replies SET status='expired'")
        self.add('真的是渡劫');self.advance(40)
        self.assertEqual(len(self.replies()),1)

    def test_own_message_and_wrong_group_not_judged(self):
        self.add('我自己说的',sender='bot');self.advance()
        self.p.consider('222@chatroom')
        self.ai.request.assert_not_called()

    def test_history_is_group_scoped_and_speakers_anonymized(self):
        self.add('今天累');self.advance()
        payload=json.loads(self.ai.request.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(payload['recent_messages'][0]['speaker'],'成员1')
        self.assertNotIn('alice',json.dumps(payload))

    def test_same_speaker_fragments_merge(self):
        self.add('这也太离谱了');self.add('一天开了八个会');self.advance()
        self.assertIn('这也太离谱了\n一天开了八个会',self.replies()[0]['prompt'])

    def test_recent_engagement_reaches_decider(self):
        self.add('影，来聊两句');self.advance()
        with self.db:self.db.execute("UPDATE replies SET status='confirmed',reply='聊，怎么了？'")
        self.p.confirmed('111@chatroom')
        self.clock.return_value+=10
        self.add('今天经历了一件好笑的事');self.advance()
        payload=json.loads(self.ai.request.call_args.args[1]['messages'][1]['content'])
        self.assertTrue(payload['recently_engaged'])

    def make_proactive(self):
        self.response('silent');self.add('周末想去爬山');self.advance()
        self.response('initiate','聊聊路线选择');self.advance(301)
        return self.replies()[0]

    def test_ambient_generation_receives_goal_without_replacing_current_request(self):
        from myshadow.bot import Bot
        for style,goal in [('brief','只共鸣加班的疲惫，不要给建议'),
                           ('answer','围绕任务优先级给出两条可执行建议')]:
            with self.subTest(style=style):
                self.response(style,goal)
                text='今天又加班到十点，怎么调整任务安排？'
                self.add(text);self.advance(40)
                b=Bot.__new__(Bot);b.state,b.selective=self.db,self.p
                b.config={'mode':'preview','max_age_seconds':300,'system_prompt':'你叫影'}
                b.require_group=Mock();b.participation_current=Mock(return_value=True)
                b.messages_for=Mock(return_value=([{'role':'system','content':'你叫影'},
                    {'role':'user','content':text}],1))
                b.ai=Mock();b.ai.complete.return_value=('测试回复',{})
                b.process_group('111@chatroom')
                messages=b.ai.complete.call_args.args[0]
                reference=json.loads(messages[-2]['content'].split('\n',1)[1])
                self.assertEqual(reference,{'style':style,'direction':goal})
                self.assertEqual(messages[-1],{'role':'user','content':text})
                self.assertNotIn(goal,messages[0]['content'])
                self.assertIn('不是新的用户请求',messages[0]['content'])

    def test_proactive_generation_never_resolves_fake_message_sender(self):
        from myshadow.bot import Bot
        row=self.make_proactive()
        b=Bot.__new__(Bot)
        b.state,b.selective=self.db,self.p
        b.config={'mode':'preview','max_age_seconds':300,'system_prompt':'你叫影'}
        b.require_group=Mock();b.participation_current=Mock(return_value=True)
        b.trigger_sender=Mock(side_effect=AssertionError('synthetic row has no human sender'))
        b.messages_for=Mock(side_effect=AssertionError('synthetic row is not a WeChat message'))
        b.scheduler=Mock();b.stickers=Mock();b.history_search=Mock()
        b.ai=Mock();b.ai.complete.return_value=('选路线可以先看当天的时间余量。',{})
        with patch('myshadow.bot.chat_complete',return_value=('选路线可以先看当天的时间余量。',{})) as complete:
            b.process_group('111@chatroom')
        self.assertEqual(complete.call_args.kwargs['extra_tools'],[])
        b.trigger_sender.assert_not_called();b.messages_for.assert_not_called()
        b.history_search.handler.assert_not_called()
        self.assertEqual(self.replies()[0]['status'],'preview')

    def test_proactive_freshness_uses_original_anchor_not_queue_time(self):
        from pathlib import Path
        from myshadow.bot import Bot,table_for
        from myshadow.wechat_db import SnapshotConnection
        row=self.make_proactive()
        b=Bot.__new__(Bot);b.state,b.selective=self.db,self.p;b.config={'bot_id':'bot'}
        def snapshot(_):
            c=sqlite3.connect(':memory:',factory=SnapshotConnection)
            c.executescript('CREATE TABLE Name2Id(user_name);INSERT INTO Name2Id VALUES("bob");'
                'CREATE TABLE '+table_for('111@chatroom')+'(real_sender_id,local_type,create_time,sort_seq,local_id);')
            c.execute('INSERT INTO '+table_for('111@chatroom')+' VALUES(1,1,?,2,2)',(self.now+20,))
            c.commit();return c
        with patch('myshadow.bot.databases',return_value=(Path('/fake'),[Path('/fake/message/message_0.db')])),patch('myshadow.bot.snapshot',side_effect=snapshot):
            self.assertFalse(b.participation_current(row['id']))
        self.assertEqual(self.replies()[0]['status'],'expired')


if __name__=='__main__':unittest.main()
