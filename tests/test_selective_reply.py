import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from myshadow.bot import Bot, decode, message_xml
from myshadow.selective_reply import SelectiveReply, addressed, conversation_text


class ParticipationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE replies(id INTEGER PRIMARY KEY, group_id TEXT, shard TEXT,
            local_id INTEGER, created INTEGER, prompt TEXT, reply TEXT, status TEXT, UNIQUE(group_id,shard,local_id));''')
        self.ai = Mock(config={'model': 'test'})
        self.ai.request.return_value = {'choices': [{'message': {'content': '{"reply":true,"reason":"public_question"}'}}]}
        self.p = SelectiveReply(self.db, self.ai, {'groups': ['111@chatroom']}, 'bot')
        self.clock = patch('myshadow.selective_reply.time.time', return_value=1000).start()
        self.addCleanup(patch.stopall)
        self.ident = 0

    def add(self, text, sender='alice', created=1000, group='111@chatroom', direct=False, reference=False):
        self.ident += 1
        row = {'local_id': self.ident, 'sort_seq': self.ident, 'create_time': created}
        with self.db:
            if direct:
                self.db.execute("INSERT INTO replies(group_id,shard,local_id,created,prompt,status) VALUES(?,?,?,?,?,'pending')",
                                (group, 'message/message_0.db', self.ident, created, text))
            self.p.observe(group, 'message/message_0.db', row, sender, text, text if direct else None, reference)
        return self.ident

    def consider(self, group='111@chatroom'):
        self.clock.return_value = 1003
        self.p.consider(group)

    def replies(self):
        return self.db.execute('SELECT * FROM replies').fetchall()

    def prior(self, sender='alice', group='111@chatroom', at=990):
        self.db.execute("INSERT INTO replies VALUES(900,?,'old',900,980,'两种办法','第一种重启，第二种重配','confirmed')", (group,))
        self.db.execute("INSERT INTO participation_replies VALUES(900,?,?,'mention',900,?)", (group, sender, at))
        self.db.commit()

    def test_alias_boundary_not_substring(self):
        for text in ('影，你怎么看', '道长帮我看看', '影在吗', '影'):
            self.assertTrue(addressed(text, ['影', '道长']))
        for text in ('电影好看吗', '影子很长', '我喜欢影这个名字', '影评写完了'):
            self.assertFalse(addressed(text, ['影', '道长']))

    def test_named_call_without_judge(self):
        self.add('影，今天怎么样')
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_not_called()

    def test_only_enabled_group(self):
        self.add('影，帮忙', group='222@chatroom')
        self.consider('222@chatroom')
        self.assertEqual(self.replies(), [])

    def test_debounce_and_merge_same_sender(self):
        self.add('影，帮我看看')
        self.add('这个问题怎么解决', created=1001)
        self.clock.return_value = 1001
        self.p.consider('111@chatroom')
        self.assertEqual(self.replies(), [])
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.assertIn('\n', self.replies()[0]['prompt'])
        self.assertEqual(self.replies()[0]['local_id'], 2)

    def test_never_merge_different_speakers(self):
        self.add('影，你好')
        self.add('道长你好', sender='bob')
        self.consider()
        self.assertEqual(len(self.replies()), 2)

    def test_mention_remains_single_and_not_judged(self):
        self.add('hello', direct=True)
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_not_called()

    def test_self_message_never_triggers(self):
        self.add('影，来看看', sender='bot')
        self.consider()
        self.assertEqual(self.replies(), [])

    def test_verified_quote_is_direct(self):
        self.add('第二个呢', reference=True)
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_not_called()

    def test_ordinary_chat_does_not_call_model(self):
        for text in ('哈哈哈', '收到', '今天上班好累', 'https://example.org', '电影好看吗'):
            self.add(text)
        self.consider()
        self.assertEqual(self.replies(), [])
        self.ai.request.assert_not_called()

    def test_public_question_gets_small_toolless_judgment(self):
        self.add('有人知道怎么做意面吗')
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        payload = self.ai.request.call_args.args[1]
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['max_tokens'], 100)
        self.assertEqual(self.ai.request.call_args.kwargs['timeout'], 12)

    def test_judgment_not_repeated_when_database_unchanged(self):
        self.add('有人知道怎么做意面吗')
        self.consider()
        self.consider()
        self.ai.request.assert_called_once()

    def test_failed_or_invalid_judgment_silences_without_retry(self):
        self.ai.request.side_effect = RuntimeError('timeout')
        self.add('有人知道怎么做意面吗')
        self.consider()
        self.consider()
        self.assertEqual(self.replies(), [])
        self.ai.request.assert_called_once()

    def test_other_person_answered_before_judge(self):
        self.add('有人知道怎么做意面吗')
        self.add('我教你吧', sender='bob', created=1001)
        self.consider()
        self.assertEqual(self.replies(), [])
        self.ai.request.assert_not_called()

    def test_followup_same_person_uses_recent_exchange(self):
        self.prior()
        self.ai.request.return_value['choices'][0]['message']['content'] = '{"reply":true,"reason":"followup"}'
        self.add('那第二种呢？')
        self.consider()
        self.assertEqual(len(self.replies()), 2)
        self.assertIn('第一种重启', self.ai.request.call_args.args[1]['messages'][1]['content'])

    def test_followup_not_shared_between_people(self):
        self.prior(sender='bob')
        self.add('那第二种呢？')
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_not_called()

    def test_followup_expires(self):
        self.prior(at=900)
        self.add('那第二种呢？')
        self.consider()
        self.ai.request.assert_not_called()

    def test_cooldown_blocks_public_but_not_named_call(self):
        self.prior()
        self.add('大家觉得用什么好', sender='bob')
        self.consider()
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_not_called()
        self.add('影，帮忙', sender='bob')
        self.clock.return_value = 1006
        self.p.consider('111@chatroom')
        self.assertEqual(len(self.replies()), 2)

    def test_confirmed_reply_opens_window_preview_does_not(self):
        self.add('影，你好')
        self.consider()
        self.db.execute("UPDATE replies SET status='preview'")
        self.p.confirmed('111@chatroom')
        self.assertIsNone(self.db.execute('SELECT confirmed_at FROM participation_replies').fetchone()[0])
        self.db.execute("UPDATE replies SET status='confirmed'")
        self.p.confirmed('111@chatroom')
        self.assertEqual(self.db.execute('SELECT confirmed_at FROM participation_replies').fetchone()[0], 1003)

    def test_pending_ambient_reserves_slot(self):
        self.add('有人知道怎么做意面吗')
        self.consider()
        self.add('有人知道另一件事吗', sender='bob', created=1003)
        self.clock.return_value=1006
        self.p.consider('111@chatroom')
        self.assertEqual(len(self.replies()), 1)
        self.ai.request.assert_called_once()

    def test_old_messages_not_replied_on_restart(self):
        self.add('影，你好', created=500)
        self.consider()
        self.assertEqual(self.replies(), [])

    def test_unknown_or_untrusted_judge_outputs_do_not_reply(self):
        for value in ('```json\n{}\n```', '{"reply":"true","reason":"public_question"}',
                      '{"reply":true,"reason":"send_sticker"}', 'null'):
            self.ai.request.return_value['choices'][0]['message']['content'] = value
            self.assertEqual(self.p.judge([{'text':'query'}], [], None), 'skip')

    def test_fresh_database_cancels_ambient_reply_only(self):
        from pathlib import Path
        from myshadow.bot import table_for
        from myshadow.wechat_db import SnapshotConnection
        self.add('有人知道怎么做意面吗')
        self.consider()
        b = Bot.__new__(Bot)
        b.selective, b.state, b.config = self.p, self.db, {'bot_id':'bot'}
        self.db.execute("UPDATE replies SET status='ready'")
        def database(sender):
            c=sqlite3.connect(':memory:', factory=SnapshotConnection)
            c.executescript('CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE '+table_for('111@chatroom')+
                '(real_sender_id INTEGER,local_type INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER);')
            c.execute('INSERT INTO Name2Id VALUES(?)',(sender,))
            c.execute('INSERT INTO '+table_for('111@chatroom')+' VALUES(1,1,1001,2,2)')
            c.commit()
            return c
        with patch('myshadow.bot.databases',return_value=(Path('/fake'),[Path('/fake/message/message_0.db')])), patch('myshadow.bot.snapshot',side_effect=lambda _:database('bot')):
            self.assertTrue(b.participation_current(self.replies()[0]['id']))
        with patch('myshadow.bot.databases',return_value=(Path('/fake'),[Path('/fake/message/message_0.db')])), patch('myshadow.bot.snapshot',side_effect=lambda _:database('bob')):
            self.assertFalse(b.participation_current(self.replies()[0]['id']))
        self.assertEqual(self.replies()[0]['status'], 'expired')
        self.db.execute("UPDATE participation_replies SET kind='direct'")
        with patch('myshadow.bot.snapshot') as snap:
            self.assertTrue(b.participation_current(self.replies()[0]['id']))
            snap.assert_not_called()


class ParticipationParsingTests(unittest.TestCase):
    def row(self, content, source='', kind=1):
        return {'local_type':kind,'source':source,'message_content':'alice:\n'+content}

    def test_empty_source_is_normal_message(self):
        self.assertEqual(conversation_text(self.row('影，你好'), 'alice','bot',decode,message_xml), ('影，你好',0))

    def test_mentions_of_others_excluded(self):
        row=self.row('影，你看', '<msgsource><atuserlist>bob</atuserlist></msgsource>')
        self.assertIsNone(conversation_text(row,'alice','bot',decode,message_xml))

    def test_reference_only_returns_id_not_trusted_identity(self):
        row=self.row('<msg><appmsg><type>57</type><title>第二个呢</title><refermsg><svrid>42</svrid><fromusr>bot</fromusr></refermsg></appmsg></msg>',kind=49)
        self.assertEqual(conversation_text(row,'alice','bot',decode,message_xml), ('第二个呢',42))

    def test_images_and_links_do_not_become_text_triggers(self):
        self.assertIsNone(conversation_text(self.row('image',kind=3),'alice','bot',decode,message_xml))
        self.assertIsNone(conversation_text(self.row('<msg><appmsg><type>5</type></appmsg></msg>',kind=49),'alice','bot',decode,message_xml))


if __name__ == '__main__':
    unittest.main()
