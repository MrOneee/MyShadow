import json
import sqlite3
import time
import unittest
from unittest.mock import Mock, patch
from myshadow.bot import Bot
from myshadow.native_stickers import NativeStickers, TransientStickerUIError


class NativeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.ai=Mock(config={'model':'test'})
        self.ready=Mock()
        self.lib=NativeStickers(self.ai,self.ready)
        self.shot={'candidates':[{'row':1,'column':i} for i in range(1,6)],'header':'hash','geometry':[0,0,464,472]}
        self.lib.ui=Mock(return_value=self.shot)

    def test_transient_search_failure_reprepares_once(self):
        self.lib.ui.side_effect=[dict(self.shot,candidates=[]),TransientStickerUIError('BadDrawable'),self.shot]
        with patch('myshadow.native_stickers.time.sleep'):
            result=self.lib.search('111@chatroom','喝茶')
        self.assertEqual(result['status'],'found')
        self.assertEqual(self.ready.call_count,3)
        self.assertEqual([c.args[0] for c in self.lib.ui.call_args_list],['prepare']*3)
        self.assertEqual(result['attempted_queries'],['喝茶'])

    def test_transient_retry_is_bounded_and_returns_no_selection(self):
        self.lib.ui.side_effect=TransientStickerUIError('BadWindow')
        with patch('myshadow.native_stickers.time.sleep'), self.assertRaises(TransientStickerUIError):
            self.lib.search('111@chatroom','喝茶')
        self.assertEqual(self.lib.ui.call_count,2)
        self.assertEqual(self.lib.selections,{})

    def test_group_validation_failure_does_not_retry(self):
        self.lib.ui.side_effect=RuntimeError('Group title changed')
        with self.assertRaises(RuntimeError):self.lib.search('111@chatroom','喝茶')
        self.assertEqual(self.lib.ui.call_count,1)

    def test_send_does_not_retry_transient_error(self):
        ident=self.lib.search('111@chatroom','喝茶')['stickers'][0]['id']
        self.lib.ui.reset_mock(side_effect=True)
        self.lib.ui.side_effect=TransientStickerUIError('BadDrawable')
        with self.assertRaises(TransientStickerUIError):self.lib.send(ident,'111@chatroom')
        self.lib.ui.assert_called_once()

    def test_ui_classifies_x11_error_and_handles_empty_stderr(self):
        lib=NativeStickers(self.ai,self.ready)
        with patch('myshadow.native_stickers.subprocess.run',return_value=Mock(returncode=1,stderr='Traceback\nBadDrawable: vanished')):
            with self.assertRaises(TransientStickerUIError):lib.ui('prepare',{})
        with patch('myshadow.native_stickers.subprocess.run',return_value=Mock(returncode=1,stderr='')):
            with self.assertRaisesRegex(RuntimeError,'without an error message'):lib.ui('prepare',{})

    def test_favorites_first_without_any_model_call(self):
        result=self.lib.search('111@chatroom','无奈',source='search')
        self.assertEqual(result['source'],'favorites')
        self.assertEqual(len(result['stickers']),1)
        self.assertEqual(self.lib.ui.call_args.args[1]['source'],'favorites')
        self.ai.request.assert_not_called()
        self.ai.complete.assert_not_called()

    def test_random_choice_uses_only_first_five_cells(self):
        self.shot['candidates'].append({'row':2,'column':1})
        with patch('myshadow.native_stickers.secrets.choice',side_effect=lambda cells:cells[-1]) as choose:
            result=self.lib.search('111@chatroom','开心')
        self.assertEqual(len(choose.call_args.args[0]),5)
        self.assertEqual(self.lib.get(result['stickers'][0]['id'],'111@chatroom')['point'],[408,56])

    def test_empty_favorites_falls_back_to_public(self):
        self.lib.ui.side_effect=[dict(self.shot,candidates=[]),self.shot]
        result=self.lib.search('111@chatroom','无奈')
        self.assertEqual(result['source'],'search')
        self.assertEqual([c.args[1]['source'] for c in self.lib.ui.call_args_list],['favorites','search'])
        self.assertEqual(self.lib.get(result['stickers'][0]['id'],'111@chatroom')['query'],'无奈')
        self.ai.complete.assert_not_called()

    def test_public_empty_retries_short_word_once(self):
        self.lib.ui.side_effect=[dict(self.shot,candidates=[]),dict(self.shot,candidates=[]),self.shot]
        self.ai.request.return_value={'choices':[{'message':{'content':'{"query":"无语"}'}}]}
        result=self.lib.search('111@chatroom','无言以对')
        self.assertEqual(result['attempted_queries'],['无言以对','无语'])
        self.assertEqual(self.ready.call_count,3)
        self.ai.request.assert_called_once()
        self.ai.complete.assert_not_called()

    def test_retry_budget_and_duplicate_query(self):
        self.lib.ui.return_value=dict(self.shot,candidates=[])
        self.ai.request.return_value={'choices':[{'message':{'content':'{"query":"无语"}'}}]}
        self.assertEqual(self.lib.search('111@chatroom','无奈')['stickers'],[])
        self.assertEqual(self.lib.ui.call_count,3)
        self.ai.request.assert_called_once()
        self.lib.ui.reset_mock()
        self.lib.search('111@chatroom','无语')
        self.assertEqual(self.lib.ui.call_count,2)

    def test_overlong_keyword_only_rewritten_when_public_needed(self):
        self.lib.ui.side_effect=[dict(self.shot,candidates=[]),self.shot]
        self.ai.request.return_value={'choices':[{'message':{'content':'{"query":"摸鱼"}'}}]}
        result=self.lib.search('111@chatroom','不想上班又无可奈何的表情包')
        self.assertEqual(result['attempted_queries'],['摸鱼'])

    def test_invalid_shortening_never_reaches_public_search(self):
        self.lib.ui.return_value=dict(self.shot,candidates=[])
        self.ai.request.return_value={'choices':[{'message':{'content':'{"query":"这还是非常长的搜索词"}'}}]}
        self.assertEqual(self.lib.search('111@chatroom','不想上班又无可奈何的表情包')['stickers'],[])
        self.assertEqual(self.lib.ui.call_count,1)

    def test_binding_expiry_and_new_search(self):
        ident=self.lib.search('111@chatroom','开心')['stickers'][0]['id']
        with self.assertRaises(ValueError):self.lib.get(ident,'222@chatroom')
        self.lib.selections[ident]['prepared']-=91
        with self.assertRaises(ValueError):self.lib.send(ident,'111@chatroom')
        self.lib.search('111@chatroom','开心')
        with self.assertRaises(ValueError):self.lib.get(ident,'111@chatroom')

    def test_scaling_and_invalid_cells(self):
        self.shot['scale']=1.25
        self.shot['candidates']=[{'row':1,'column':1}]
        result=self.lib.search('111@chatroom','开心')
        self.assertEqual(self.lib.get(result['stickers'][0]['id'],'111@chatroom')['point'],[70,70])
        self.shot['candidates']=[{'row':5,'column':1}]
        self.ai.request.return_value={'choices':[{'message':{'content':'{"query":""}'}}]}
        self.assertEqual(self.lib.search('111@chatroom','开心')['stickers'],[])


class BoundToolTests(unittest.TestCase):
    def setUp(self):
        self.bot = Bot.__new__(Bot)
        self.bot.groups = {'111@chatroom': {'group_id': '111@chatroom'}}
        self.bot.config = {'mode': 'send'}
        self.bot.state = sqlite3.connect(':memory:')
        self.bot.state.row_factory = sqlite3.Row
        self.bot.state.execute('CREATE TABLE sticker_jobs(reply_id INTEGER PRIMARY KEY,group_id TEXT,sticker_id TEXT,status TEXT,created INTEGER)')
        self.bot.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id TEXT,status TEXT,created INTEGER,prompt TEXT,reply TEXT)')
        self.bot.stickers = Mock()
        self.bot.stickers.search.return_value = {'stickers': [{'id': 'chosen'}]}
        def send(job):
            self.bot.state.execute("UPDATE sticker_jobs SET status='confirmed' WHERE reply_id=?", (job['reply_id'],))
        self.bot.send_sticker = Mock(side_effect=send)
        self.handle = self.bot.sticker_handler({'id': 1, 'group_id': '111@chatroom'})

    def tearDown(self):
        self.bot.state.close()

    def test_search_failure_is_distinct_from_empty_and_logged(self):
        self.bot.stickers.search.side_effect=RuntimeError('BadDrawable diagnostic')
        with patch('builtins.print') as log:
            result=self.handle('search_stickers',{'query':'喝茶'})
        self.assertEqual(result['status'],'failed')
        self.assertIn('不能说没搜到',result['error'])
        events=[json.loads(c.args[0]) for c in log.call_args_list]
        self.assertEqual(events[-1]['event'],'sticker_search_failed')
        self.assertIn('BadDrawable',events[-1]['error'])
        self.bot.send_sticker.assert_not_called()

    def test_search_then_send_only_to_bound_group(self):
        self.handle('search_stickers', {'query': '开心'})
        self.assertEqual(self.handle('send_sticker', {'sticker_id': 'chosen'})['status'], 'confirmed')
        self.assertEqual(self.bot.send_sticker.call_args.args[0]['group_id'], '111@chatroom')

    def test_intent_passes_through_bound_tool(self):
        self.handle('search_stickers', {'query': '摊手', 'intent': '懒得争辩', 'source': 'search'})
        self.bot.stickers.search.assert_called_once_with('111@chatroom', query='摊手', intent='懒得争辩', source='search')

    def test_model_cannot_override_destination(self):
        self.assertIn('error', self.handle('send_sticker', {'sticker_id': 'chosen', 'group_id': '222@chatroom'}))
        self.bot.send_sticker.assert_not_called()

    def test_cannot_send_unoffered_id(self):
        self.assertIn('error', self.handle('send_sticker', {'sticker_id': 'invented'}))
        self.bot.send_sticker.assert_not_called()

    def test_duplicate_tool_call_does_not_duplicate_delivery(self):
        self.handle('search_stickers', {'query': '开心'})
        for _ in range(2):
            self.handle('send_sticker', {'sticker_id': 'chosen'})
        self.bot.send_sticker.assert_called_once()

    def test_failed_delivery_is_not_claimed_confirmed_or_retried(self):
        self.bot.send_sticker.side_effect = RuntimeError('UI failure')
        self.handle('search_stickers', {'query': '开心'})
        self.assertEqual(self.handle('send_sticker', {'sticker_id': 'chosen'})['status'], 'failed_or_uncertain')
        self.handle('send_sticker', {'sticker_id': 'chosen'})
        self.bot.send_sticker.assert_called_once()

    def test_search_is_not_repeated(self):
        self.handle('search_stickers', {'query': '开心'})
        self.assertIn('error', self.handle('search_stickers', {'query': '另一个'}))
        self.bot.stickers.search.assert_called_once()

    def test_sticker_only_finishes_only_after_confirmation(self):
        self.handle('search_stickers', {'query': '开心'})
        self.assertTrue(self.handle('send_sticker', {'sticker_id': 'chosen', 'reply_mode': 'sticker_only'})['finish_without_text'])

    def test_with_text_keeps_followup_enabled(self):
        self.handle('search_stickers', {'query': '开心'})
        self.assertFalse(self.handle('send_sticker', {'sticker_id': 'chosen', 'reply_mode': 'with_text'})['finish_without_text'])

    def previous_sticker(self, group='111@chatroom'):
        self.bot.state.execute('INSERT INTO replies VALUES(0,?,?,?, ?,?)', (group, 'confirmed', int(time.time()), '', ''))
        self.bot.state.execute('INSERT INTO sticker_jobs VALUES(0,?,?,?,?)', (group, 'prior', 'confirmed', int(time.time())))

    def test_consecutive_automatic_sticker_is_blocked(self):
        self.previous_sticker()
        self.assertFalse(self.bot.may_offer_sticker({'id': 1, 'group_id': '111@chatroom', 'prompt': '哈哈'}))

    def test_explicit_request_has_no_cooldown(self):
        self.previous_sticker()
        self.assertTrue(self.bot.may_offer_sticker({'id': 1, 'group_id': '111@chatroom', 'prompt': '再发个表情包'}))

    def test_other_group_does_not_affect_policy(self):
        self.previous_sticker('222@chatroom')
        self.assertTrue(self.bot.may_offer_sticker({'id': 1, 'group_id': '111@chatroom', 'prompt': '哈哈'}))

    def test_text_turn_resets_alternation_without_waiting(self):
        self.previous_sticker()
        self.bot.state.execute("INSERT INTO replies VALUES(1,'111@chatroom','confirmed',?,'','哈哈')", (int(time.time()),))
        self.assertTrue(self.bot.may_offer_sticker({'id': 2, 'group_id': '111@chatroom', 'prompt': '尴尬了😂'}))

    def test_user_can_request_no_sticker(self):
        self.assertFalse(self.bot.may_offer_sticker({'id': 1, 'group_id': '111@chatroom', 'prompt': '别发表情包，认真说'}))

    def test_process_marks_sticker_only_confirmed_without_text_send(self):
        self.bot.config['max_age_seconds'] = 300
        self.bot.state.execute("INSERT INTO replies VALUES(1,'111@chatroom','pending',?,'发个表情包',NULL)", (int(time.time()),))
        self.bot.messages_for = Mock(return_value=([{'role':'system','content':'test'}],0))
        self.bot.ai = Mock()
        self.bot.send = Mock()
        def complete(*args, **kwargs):
            self.bot.state.execute("INSERT INTO sticker_jobs VALUES(1,'111@chatroom','chosen','confirmed',?)", (int(time.time()),))
            return '', {}
        with patch('myshadow.bot.chat_complete', side_effect=complete):
            self.bot.process_group('111@chatroom')
        self.assertEqual(self.bot.state.execute('SELECT status FROM replies WHERE id=1').fetchone()[0], 'confirmed')
        self.bot.send.assert_not_called()

    def test_empty_response_without_delivery_is_rejected(self):
        self.bot.config['max_age_seconds'] = 300
        self.bot.state.execute("INSERT INTO replies VALUES(1,'111@chatroom','pending',?,'发个表情包',NULL)", (int(time.time()),))
        self.bot.messages_for = Mock(return_value=([{'role':'system','content':'test'}],0))
        self.bot.ai = Mock()
        with patch('myshadow.bot.chat_complete', return_value=('',{})), self.assertRaisesRegex(RuntimeError, 'without a confirmed'):
            self.bot.process_group('111@chatroom')
