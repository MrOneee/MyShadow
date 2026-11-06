"""Host integration: bound mutation and budgeted recall, without desktop sends."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from myshadow.bot import Bot, estimate_tokens
from myshadow.meme_memory import MemeMemory, DEFAULTS


class MemeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        (root/'meme-settings.json').write_text(json.dumps(DEFAULTS))
        b=self.bot=Bot.__new__(Bot)
        b.memes=MemeMemory(root)
        b.config={'mode':'send','max_age_seconds':300,'context_token_budget':12800,'system_prompt':'你是影','bot_name':'影','bot_id':'wxid_bot'}
        b.ai=Mock(config={'model':'test','max_tokens':1024})
        b.require_group=Mock(return_value={'group_id':'111@chatroom','group_name':'测试群'})
        b.trigger_sender=Mock(return_value='wxid_user')
        b.messages_for=Mock(return_value=([{'role':'system','content':'你是影'},{'role':'user','content':'记住摸鱼这个梗：偷闲'}],0))
        b.send=Mock();b.participation_current=Mock(return_value=True)
        b.state=sqlite3.connect(':memory:');b.state.row_factory=sqlite3.Row;self.addCleanup(b.state.close)
        b.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        b.state.execute("INSERT INTO replies VALUES(1,'111@chatroom','test',1,?,'记住摸鱼这个梗：偷闲',NULL,'pending')",(int(time.time()),))

    def test_tool_roundtrip_and_readonly_preview(self):
        b=self.bot
        def complete(ai,messages,weather,**kwargs):
            names=[t['function']['name'] for t in kwargs['extra_tools']]
            self.assertIn('manage_meme',names)
            result=kwargs['tool_handler']('manage_meme',{'action':'remember','term':'摸鱼','meaning':'偷闲'})
            self.assertEqual('saved',result['status'])
            return '记住了',{}
        with patch('myshadow.bot.chat_complete',side_effect=complete),patch('myshadow.bot.decide_expression',return_value={'mode':'text','text_bubbles':1,'intent':''},create=True):
            b.process_group('111@chatroom')
        b.send.assert_called_once_with(1)
        self.assertIn('偷闲',b.memes.context('111@chatroom','摸鱼'))
        self.assertEqual('',b.memes.context('222@chatroom','摸鱼'))

    def test_tool_schema_reserved_and_context_counted(self):
        b=self.bot
        base=b.input_budget_for({'group_id':'111@chatroom','prompt':'你好'})
        budget=b.input_budget_for({'group_id':'111@chatroom','prompt':'记住摸鱼这个梗'})
        self.assertLess(budget,base)
        b.memes.handler('111@chatroom','wxid_user','记住摸鱼这个梗：偷闲','x')({'action':'remember','term':'摸鱼','meaning':'偷闲'})
        b.context_for=Mock(return_value=([],'用户'));b.memory_for=Mock(return_value='');b.image_context_for=Mock(return_value='')
        trigger={'id':1,'group_id':'111@chatroom','prompt':'摸鱼'}
        # Exercise the real builder instead of the model stub used by the first test.
        messages,_=Bot.messages_for(b,trigger)
        self.assertIn('偷闲',json.dumps(messages,ensure_ascii=False))
        self.assertLess(estimate_tokens(messages),b.input_budget_for(trigger))


if __name__=='__main__':unittest.main()
