import contextlib
import io
import json
import sqlite3
import time
import unittest
from unittest.mock import Mock, patch

from myshadow.ai_client import AIClient
from myshadow.bot import Bot
from myshadow.realtime import chat_complete
from tests.test_realtime import call, response
from myshadow.web_search import WebSearch


def search_response(text='资料摘要 https://docs.python.org/3/whatsnew/3.13.html'):
    return {'status': 'completed', 'usage': {'total_tokens': 30}, 'output': [
        {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'search'}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.ai = Mock(config={'model': 'deepseek-v4-flash'})
        self.ai.request.return_value = search_response()
        self.search = WebSearch(self.ai)

    def test_builtin_search_enabled_and_only_query_sent(self):
        result = self.search.query('Python 3.13 新功能')
        self.assertNotIn('error', result)
        endpoint, payload = self.ai.request.call_args.args
        self.assertEqual(endpoint, '/responses')
        self.assertEqual(payload['input'], 'Python 3.13 新功能')
        self.assertEqual(payload['tools'], [{'type': 'web_search'}])
        self.assertEqual(payload['tool_choice'], 'auto')
        self.assertEqual(self.ai.request.call_args.kwargs, {'timeout': 45, 'max_bytes': 1048576})
        self.assertEqual(result['total_tokens'], 30)
        self.assertEqual(result['search_calls'], 1)
        self.assertIn('https://', result['summary'])

    def test_invalid_query_never_requests_api(self):
        for query in ('', ' ', None, 42, 'a' * 201, 'x\ny'):
            with self.subTest(query=query):
                self.assertIn('error', self.search.query(query))
        self.ai.request.assert_not_called()

    def test_does_not_treat_model_text_without_search_as_evidence(self):
        self.ai.request.return_value['output'].pop(0)
        self.assertIn('error', self.search.query('public query'))

    def test_incomplete_response_retains_usage_but_not_summary(self):
        self.ai.request.return_value['status'] = 'incomplete'
        result = self.search.query('public query')
        self.assertIn('error', result)
        self.assertNotIn('summary', result)
        self.assertEqual(result['total_tokens'], 30)

    def test_failed_search_call_not_accepted(self):
        self.ai.request.return_value['output'][0]['status'] = 'failed'
        self.assertIn('error', self.search.query('query'))

    def test_bad_responses_return_structured_error(self):
        for data in (None, [], {}, {'status': 'completed', 'output': [None]}, search_response('')):
            with self.subTest(data=data):
                self.ai.request.return_value = data
                self.assertIn('error', self.search.query('public query'))

    def test_network_error_does_not_log_credentials_or_query(self):
        self.ai.request.side_effect = RuntimeError('secret-token')
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = self.search.query('private-query')
        self.assertIn('error', result)
        self.assertNotIn('secret-token', json.dumps(result) + log.getvalue())
        self.assertNotIn('private-query', log.getvalue())

    def test_summary_is_bounded(self):
        self.ai.request.return_value = search_response('x' * 10000)
        result = self.search.query('query')
        self.assertEqual(len(result['summary']), 6000)
        self.assertTrue(result['truncated'])

    def test_progress_messages_are_not_search_results(self):
        self.ai.request.return_value['output'].insert(0, {'type': 'message', 'content': [
            {'type': 'output_text', 'text': '让我查一下'}]})
        self.assertNotIn('让我查一下', self.search.query('query')['summary'])

    def test_missing_final_answer_rejects_progress_only(self):
        items = self.ai.request.return_value['output']
        items.reverse()
        self.assertIn('error', self.search.query('query'))


class SearchToolTests(unittest.TestCase):
    def setUp(self):
        self.ai = Mock(config={'model': 'deepseek-v4-flash'})
        self.search = Mock()
        self.search.query.return_value = {'summary': '资料 https://example.org', 'total_tokens': 30}
        self.messages = [{'role': 'user', 'content': '搜索公开资料'}]

    def search_call(self, args='{"query":"公开资料"}', ident='s1'):
        return call('web_search', args, ident)

    def test_search_round_trip_and_usage(self):
        self.ai.request.side_effect = [response(calls=[self.search_call()]), response('有来源的回答')]
        text, usage = chat_complete(self.ai, self.messages, None, search=self.search)
        self.search.query.assert_called_once_with(query='公开资料')
        self.assertEqual(usage['total_tokens'], 50)
        self.assertEqual(text, '有来源的回答')
        self.assertEqual(len(self.messages), 1)
        self.assertIn('https://example.org', self.ai.request.call_args.args[1]['messages'][-1]['content'])

    def test_ordinary_chat_does_not_search(self):
        self.ai.request.return_value = response('你好')
        chat_complete(self.ai, self.messages, None, search=self.search)
        self.search.query.assert_not_called()
        self.assertEqual(self.ai.request.call_count, 1)

    def test_disabled_search_not_advertised_or_executed(self):
        self.ai.request.side_effect = [response(calls=[self.search_call()]), response('未启用')]
        chat_complete(self.ai, self.messages, None)
        self.search.query.assert_not_called()
        self.assertEqual(self.ai.request.call_args.args[1]['tools'], [])
        self.assertIn('not_configured', self.ai.request.call_args.args[1]['messages'][-1]['content'])

    def test_malformed_and_extra_arguments_never_execute(self):
        for args in ('{', '[]', '{}', '{"query":"q","url":"https://example.org"}', 'x' * 2049):
            self.ai.request.side_effect = [response(calls=[self.search_call(args)]), response('参数无效')]
            chat_complete(self.ai, self.messages, None, search=self.search)
        self.search.query.assert_not_called()

    def test_two_search_requests_per_reply_even_if_model_repeats(self):
        self.ai.request.side_effect = [response(calls=[self.search_call(ident='s' + str(n))]) for n in range(4)]
        self.ai.complete.return_value = ('资料不足', {'total_tokens': 5})
        _, usage = chat_complete(self.ai, self.messages, None, search=self.search)
        self.assertEqual(self.search.query.call_count, 2)
        self.ai.complete.assert_called_once()
        self.assertEqual(usage['total_tokens'], 105)

    def test_search_and_weather_have_independent_limits(self):
        weather = Mock()
        weather.query.return_value = {'city': '北京'}
        self.ai.request.side_effect = [response(calls=[self.search_call(), call()]), response('综合结果')]
        chat_complete(self.ai, self.messages, weather, search=self.search)
        weather.query.assert_called_once()
        self.search.query.assert_called_once()

    def test_search_failure_is_returned_to_model(self):
        self.search.query.return_value = {'error': 'search_unavailable'}
        self.ai.request.side_effect = [response(calls=[self.search_call()]), response('这次没查到')]
        text, _ = chat_complete(self.ai, self.messages, None, search=self.search)
        self.assertEqual(text, '这次没查到')

    def test_context_budget_applies_after_search(self):
        self.search.query.return_value = {'summary': '中' * 20000}
        self.ai.request.return_value = response(calls=[self.search_call()])
        text, _ = chat_complete(self.ai, self.messages, None, search=self.search)
        self.assertIn('太长', text)
        self.assertEqual(self.ai.request.call_count, 1)

    def test_bot_uses_search_without_weather_or_stickers(self):
        bot = Bot.__new__(Bot)
        bot.ai, bot.search = self.ai, self.search
        bot.config = {'mode': 'preview', 'max_age_seconds': 300}
        bot.require_group = Mock()
        bot.messages_for = Mock(return_value=(self.messages, 1))
        bot.state = sqlite3.connect(':memory:')
        self.addCleanup(bot.state.close)
        bot.state.row_factory = sqlite3.Row
        bot.state.execute('CREATE TABLE replies(id INTEGER, group_id TEXT, created INTEGER, prompt TEXT, reply TEXT, status TEXT)')
        bot.state.execute('INSERT INTO replies VALUES(1,?,?,?,?,?)', ('g', int(time.time()), '搜索', '', 'pending'))
        with patch('myshadow.bot.chat_complete', return_value=('已搜索', {'total_tokens': 50})) as complete:
            bot.process_group('g')
        self.assertIs(complete.call_args.kwargs['search'], self.search)
        self.ai.complete.assert_not_called()
        self.assertEqual(bot.state.execute('SELECT status FROM replies').fetchone()[0], 'preview')

    def test_bot_offers_bound_read_url_for_conversation_link(self):
        bot = Bot.__new__(Bot)
        bot.ai, bot.search = self.ai, self.search
        bot.link_reader = Mock()
        link_handler = Mock(return_value={'status': 'ok'})
        bot.link_reader.handler.return_value = link_handler
        bot.config = {'mode': 'preview', 'max_age_seconds': 300}
        bot.require_group = Mock()
        messages = [{'role': 'system', 'content': '规则'},
                    {'role': 'user', 'content': '看看 https://example.com/a'}]
        bot.messages_for = Mock(return_value=(messages, 1))
        bot.state = sqlite3.connect(':memory:')
        self.addCleanup(bot.state.close)
        bot.state.row_factory = sqlite3.Row
        bot.state.execute('CREATE TABLE replies(id INTEGER, group_id TEXT, created INTEGER, prompt TEXT, reply TEXT, status TEXT)')
        bot.state.execute('INSERT INTO replies VALUES(1,?,?,?,?,?)', ('g', int(time.time()), '看看链接', '', 'pending'))
        def complete(*args, **kwargs):
            self.assertIn('read_url', [tool['function']['name'] for tool in kwargs['extra_tools']])
            self.assertEqual(kwargs['tool_handler']('read_url', {'url': 'https://example.com/a'}), {'status': 'ok'})
            return '读完了', {'total_tokens': 20}
        with patch('myshadow.bot.chat_complete', side_effect=complete):
            bot.process_group('g')
        bot.link_reader.handler.assert_called_once_with(['https://example.com/a'])
        link_handler.assert_called_once_with({'url': 'https://example.com/a'})


class BoundedRequestTests(unittest.TestCase):
    def test_response_read_is_bounded_and_closed(self):
        ai = AIClient.__new__(AIClient)
        ai.base_url, ai.config = 'https://api.deepseek.com', {'api_key': 'test-key'}
        ai.opener = Mock()
        response = Mock()
        response.read.return_value = b'x' * 11
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        ai.opener.open.return_value = manager
        with self.assertRaisesRegex(RuntimeError, 'too large'):
            ai.request('/responses', {}, timeout=45, max_bytes=10)
        response.read.assert_called_once_with(11)
        manager.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
