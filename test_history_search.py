import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from bot import table_for
from history_search import HistorySearch, boundary
from wechat_db import SnapshotConnection


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.group='111@chatroom';self.table=table_for(self.group)
        self.now=int(datetime(2026,9,8,12,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        self.state=sqlite3.connect(':memory:');self.state.row_factory=sqlite3.Row
        self.addCleanup(self.state.close)
        self.state.execute('CREATE TABLE history_exclusions(group_id,server_id,reason)')
        self.bot=SimpleNamespace(state=self.state,config={'bot_id':'robot','bot_name':'影'},require_group=Mock(),
            trigger_anchor=Mock(return_value={'create_time':self.now,'sort_seq':100,'local_id':100,'server_id':999,'sender':'alice'}))
        self.h=HistorySearch(self.bot)
        self.trigger={'group_id':self.group,'shard':'message/message_0.db','local_id':100}
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE Name2Id(user_name);INSERT INTO Name2Id VALUES("alice"),("bob");'
            'CREATE TABLE '+self.table+'(local_id,server_id,local_type,create_time,sort_seq,real_sender_id,message_content);'
            'CREATE TABLE '+table_for('222@chatroom')+'(local_id,server_id,local_type,create_time,sort_seq,real_sender_id,message_content);')
        self.contact=sqlite3.connect(':memory:');self.addCleanup(self.contact.close)
        self.contact.executescript('CREATE TABLE contact(username,nick_name);INSERT INTO contact VALUES("alice","小李"),("bob","小王");')
        self.closed=[]
        def snap(shard):
            c=sqlite3.connect(':memory:',factory=SnapshotConnection)
            c.deserialize((self.contact if shard=='contact/contact.db' else self.db).serialize())
            c.row_factory=sqlite3.Row;self.closed.append(c)
            return c
        for patcher in [patch('history_search.snapshot',side_effect=snap),
                        patch('history_search.databases',return_value=(Path('/db'),[Path('/db/message/message_0.db')]))]:
            patcher.start();self.addCleanup(patcher.stop)

    def add(self,text,ident=1,age=60,kind=1,sender=1,group=None,server=None,seq=None):
        self.db.execute('INSERT INTO '+table_for(group or self.group)+' VALUES(?,?,?,?,?,?,?)',
            (ident,server or ident,kind,self.now-age,seq or ident,sender,('alice' if sender==1 else 'bob')+':\n'+text))
        self.db.commit()

    def query(self,**args):
        return self.h.query(self.trigger,dict(query='',**args))

    def test_current_group_only_and_no_identifiers_in_output(self):
        self.add('本群开会')
        self.add('其他群秘密开会',group='222@chatroom')
        result=self.h.query(self.trigger,{'query':'开会'})
        self.assertEqual([m['text'] for m in result['messages']],['本群开会'])
        self.assertEqual(result['messages'][0]['sender'],'小李')
        self.assertNotIn('alice',json.dumps(result));self.assertNotIn('message_0',json.dumps(result))
        self.bot.require_group.assert_called_once_with(self.group)

    def test_current_and_future_messages_excluded(self):
        self.add('之前',ident=1)
        self.add('当前提问',ident=100,age=0,server=999,seq=100)
        self.add('随后发言',ident=101,age=0,seq=101)
        self.add('未来发言',ident=102,age=-1)
        self.assertEqual([m['text'] for m in self.query()['messages']],['之前'])

    def test_time_range_date_end_includes_whole_day(self):
        self.add('昨天开会',age=86400)
        self.add('今天开会')
        result=self.query(start='2026-09-07',end='2026-09-07')
        self.assertEqual([m['text'] for m in result['messages']],['昨天开会'])
        self.assertEqual(boundary('2026-09-07',True)-boundary('2026-09-07'),86400)

    def test_older_date_can_be_requested(self):
        self.add('上个月露营',age=35*86400)
        self.assertEqual(self.query()['messages'],[])
        self.assertEqual(len(self.query(start='2026-08-01',end='2026-08-07')['messages']),1)

    def test_bad_parameters_and_cross_group_args_rejected(self):
        for args in ({'query':'x','group_id':'222@chatroom'},{'query':'x','sql':'DROP TABLE x'},
                     {'query':'x','limit':True},{'query':'x','limit':11},
                     {'query':'x','start':'2026-01-01','end':'2026-03-01'}):
            self.assertIn('error',self.h.query(self.trigger,args))

    def test_sender_is_resolved_only_in_current_group(self):
        self.add('开会',sender=1);self.add('开会',ident=2,sender=2)
        with patch('history_search.member_names',return_value={'alice':['小李'],'bob':['小王']}) as names:
            result=self.query(sender='小李')
        self.assertEqual(len(result['messages']),1)
        self.assertEqual(result['messages'][0]['sender'],'小李')
        self.assertEqual(names.call_args.args[1],self.group)

    def test_ambiguous_sender_is_not_guessed(self):
        with patch('history_search.member_names',return_value={'alice':['同名'],'bob':['同名']}):
            self.assertIn('error',self.query(sender='同名'))

    def test_quote_does_not_impersonate_original_author(self):
        self.add('<msg><appmsg><type>57</type><title>我不同意这个方案</title><refermsg><fromusr>bob</fromusr><content>我支持</content></refermsg></appmsg></msg>',kind=49)
        result=self.query()['messages'][0]
        self.assertEqual(result['sender'],'小李')
        self.assertEqual(result['text'],'我不同意这个方案')

    def test_literal_keyword_and_excluded_records(self):
        self.add('SQL %_ 和中文',ident=1)
        self.add('其他SQL信息',ident=2)
        self.assertEqual(len(self.h.query(self.trigger,{'query':'%_'})['messages']),1)
        self.state.execute('INSERT INTO history_exclusions VALUES(?,?,?)',(self.group,2,'misroute'));self.state.commit()
        self.assertEqual(len(self.h.query(self.trigger,{'query':'sql'})['messages']),1)

    def test_limit_order_and_text_budget(self):
        for i in range(1,11):self.add('长文本'*200,ident=i,age=i)
        result=self.query(limit=10)
        self.assertTrue(result['more_matches'])
        self.assertLessEqual(sum(len(m['text']) for m in result['messages']),1600)
        self.assertTrue(all(m['truncated'] for m in result['messages']))

    def test_scan_budget_reports_partial(self):
        self.add('较新',ident=2,age=20);self.add('较旧',ident=1,age=40)
        with patch('history_search.SCAN_LIMIT',1):result=self.query()
        self.assertTrue(result['partial']);self.assertEqual(len(result['messages']),1)

    def test_snapshot_closed_and_failure_not_reported_as_no_history(self):
        self.add('你好');self.query()
        for c in self.closed:
            with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')
        with patch('history_search.snapshot',side_effect=RuntimeError('unreadable')):
            result=self.query()
        self.assertTrue(result['partial'])

    def test_per_turn_cache_and_two_call_limit(self):
        self.h.query=Mock(return_value={'messages':[]})
        handle=self.h.handler(self.trigger)
        handle({'query':'a'});handle({'query':'a'});handle({'query':'b'})
        self.assertIn('error',handle({'query':'c'}))
        self.assertEqual(self.h.query.call_count,2)

    def test_cross_shard_duplicates_removed(self):
        self.add('同一条消息')
        with patch('history_search.databases',return_value=(Path('/db'),[Path('/db/message/message_0.db'),Path('/db/message/message_1.db')])):
            result=self.query()
        self.assertEqual(len(result['messages']),1)

    def test_bot_offers_history_tool_without_other_tools(self):
        from bot import Bot
        self.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        self.state.execute('INSERT INTO replies VALUES(1,?,?,?,?,?,?,?)',(self.group,'message/message_0.db',100,self.now,'查上周开会的历史','','pending'))
        self.state.commit()
        b=Bot.__new__(Bot);b.state=self.state
        b.config={'mode':'preview','max_age_seconds':300}
        b.require_group=Mock();b.participation_current=Mock(return_value=True)
        b.messages_for=Mock(return_value=([{'role':'system','content':'影'},{'role':'user','content':'查上周开会的历史'}],0))
        b.history_search=Mock();handler=Mock(return_value={'messages':[]});b.history_search.handler.return_value=handler
        b.ai=Mock()
        def complete(*args,**kwargs):
            self.assertEqual([t['function']['name'] for t in kwargs['extra_tools']],['search_history'])
            kwargs['tool_handler']('search_history',{'query':'开会'})
            return '这次没查到。',{}
        with patch('bot.chat_complete',side_effect=complete),patch('bot.time.time',return_value=self.now):
            b.process_group(self.group)
        handler.assert_called_once_with({'query':'开会'})
        self.assertEqual(b.history_search.handler.call_args.args[0]['group_id'],self.group)


if __name__=='__main__':unittest.main()
