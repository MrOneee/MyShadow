import json
import socket
import unittest
from unittest.mock import Mock

from myshadow.shared_links import shared_link, shared_link_text, urls_in_messages, urls_in_text
from myshadow.url_reader import READ_URL_TOOL, UrlReader, extract_page, normalize_url, public_addresses


CARD = ('<msg><appmsg><type>5</type><title>一篇公众号文章</title>'
        '<des>文章简介</des><url>https://mp.weixin.qq.com/s?__biz=abc&amp;mid=1</url>'
        '</appmsg></msg>')


class FakeResponse:
    def __init__(self, data=b'', status=200, headers=None):
        self.data, self.status = data, status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name):
        return self.headers.get(name.lower())

    def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, target, headers=None):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def public_dns(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]


class SharedLinkTests(unittest.TestCase):
    def test_parses_wechat_card_without_leaking_other_xml(self):
        card = CARD.replace('</appmsg>', '<appattach><aeskey>secret</aeskey></appattach></appmsg>')
        self.assertEqual(shared_link(card), {
            'title': '一篇公众号文章', 'description': '文章简介', 'app_type': '5',
            'url': 'https://mp.weixin.qq.com/s?__biz=abc&mid=1'})
        text = shared_link_text(card)
        self.assertIn('一篇公众号文章', text)
        self.assertNotIn('secret', text)

    def test_rejects_non_link_cards_and_unsafe_urls(self):
        for card in (CARD.replace('<type>5</type>', '<type>57</type>'),
                     CARD.replace('https://mp.weixin.qq.com', 'file://localhost'),
                     CARD.replace('https://', 'https://user:pass@')):
            self.assertIsNone(shared_link(card))

    def test_extracts_only_normalized_http_urls_from_user_messages(self):
        text = '看 https://EXAMPLE.com/文章?q=中文。再看 file:///etc/passwd'
        self.assertEqual(urls_in_text(text), ['https://example.com/%E6%96%87%E7%AB%A0?q=%E4%B8%AD%E6%96%87'])
        messages = [{'role': 'system', 'content': 'https://system.invalid'},
                    {'role': 'user', 'content': text}]
        self.assertEqual(urls_in_messages(messages), urls_in_text(text))


class ReaderTests(unittest.TestCase):
    def test_extracts_article_text_metadata_and_ignores_scripts(self):
        data = ('<html><head><title>测试标题</title><meta charset="utf-8">'
                '<meta name="description" content="摘要内容足够长"></head>'
                '<body><nav>菜单菜单菜单菜单</nav><article><p>这是正文第一段，包含足够多的有效文字。</p>'
                '<script>偷走系统提示</script><p>这是正文第二段，用来验证正文提取。</p></article><footer>页脚</footer></body></html>').encode()
        title, content, truncated = extract_page(data, 'text/html; charset=utf-8', 'https://example.com/')
        self.assertEqual(title, '测试标题')
        self.assertIn('正文第一段', content)
        self.assertNotIn('偷走系统提示', content)
        self.assertNotIn('菜单菜单', content)
        self.assertFalse(truncated)

    def test_reads_public_page_with_dns_pinning_and_bounded_headers(self):
        body = b'<html><title>Page</title><body>This is readable public page content for testing.</body></html>'
        connection = FakeConnection(FakeResponse(body, headers={'Content-Type': 'text/html', 'Content-Length': str(len(body))}))
        connector = Mock(return_value=connection)
        result = UrlReader(public_dns, connector).read('https://example.com/path?q=1#fragment')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['source_url'], 'https://example.com/path?q=1')
        self.assertEqual(connection.requests[0][1], '/path?q=1')
        self.assertEqual(connection.requests[0][2]['Accept-Encoding'], 'identity')
        self.assertTrue(connection.closed)
        self.assertEqual(connector.call_args.args[1], '93.184.216.34')

    def test_mtgch_article_adapter_reads_same_origin_public_json(self):
        payload = json.dumps({
            'id': 585,
            'title': '现实裂界',
            'summary': '这是一段文章摘要。',
            'byline': '作者名',
            'first_published_at': '2026-09-02T15:00:00Z',
            'section': {'name': '万智故事'},
            'body_json': {'type': 'doc', 'content': [
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': '这是第一段正文，包含足够多的可读文字。'}]},
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': '这是第二段正文。'}]},
            ]},
        }, ensure_ascii=False).encode()
        connection = FakeConnection(FakeResponse(payload, headers={
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(payload)),
        }))
        result = UrlReader(public_dns, Mock(return_value=connection)).read('https://mtgch.com/articles/585')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['title'], '现实裂界')
        self.assertIn('作者：作者名', result['content'])
        self.assertIn('第一段正文', result['content'])
        self.assertEqual(result['source_url'], 'https://mtgch.com/articles/585')
        self.assertEqual(connection.requests[0][1], '/api/v1/articles/585')
        self.assertEqual(connection.requests[0][2]['Accept'], 'application/json')

    def test_reports_dynamic_shell_as_no_readable_content(self):
        body = b'<html><head><title>SPA</title></head><body><div id="app"></div><script>load()</script></body></html>'
        response = FakeResponse(body, headers={'Content-Type': 'text/html'})
        result = UrlReader(public_dns, Mock(return_value=FakeConnection(response)), adapters=[]).read(
            'https://example.com/app')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'no_readable_content')
        self.assertIn('JavaScript', result['message'])

    def test_private_or_mixed_dns_answers_are_rejected(self):
        def private_dns(host, port, type=0):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        def mixed_dns(host, port, type=0):
            return public_dns(host, port, type) + [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', port))]
        for resolver in (private_dns, mixed_dns):
            connector = Mock()
            result = UrlReader(resolver, connector).read('http://example.com/')
            self.assertEqual(result['status'], 'failed')
            connector.assert_not_called()

    def test_redirect_target_is_revalidated(self):
        first = FakeConnection(FakeResponse(status=302, headers={'Location': 'http://127.0.0.1/private'}))
        result = UrlReader(public_dns, Mock(return_value=first)).read('https://example.com/')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(first.closed)

    def test_refuses_files_compression_and_oversized_pages(self):
        cases = [
            FakeResponse(b'%PDF', headers={'Content-Type': 'application/pdf'}),
            FakeResponse(b'gzip', headers={'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}),
            FakeResponse(b'x', headers={'Content-Type': 'text/html', 'Content-Length': str(1024 * 1024 + 1)}),
        ]
        for response in cases:
            with self.subTest(headers=response.headers):
                self.assertEqual(UrlReader(public_dns, Mock(return_value=FakeConnection(response))).read(
                    'https://example.com/')['status'], 'failed')

    def test_handler_allows_only_conversation_urls_and_two_calls(self):
        reader = UrlReader()
        reader.read = Mock(return_value={'status': 'ok', 'content': '正文'})
        handle = reader.handler(['https://example.com/a'])
        self.assertEqual(handle({'url': 'https://other.example/a'})['error'], 'url_not_in_conversation')
        self.assertEqual(handle({'url': 'https://example.com/a'})['status'], 'ok')
        self.assertEqual(handle({'url': 'https://example.com/a'})['status'], 'ok')
        self.assertEqual(handle({'url': 'https://example.com/a'})['error'], 'read_limit')
        self.assertEqual(reader.read.call_count, 2)

    def test_normalization_rejects_credentials_local_hosts_and_nondefault_ports(self):
        for value in ('https://user:pass@example.com/', 'http://localhost/',
                      'http://thing.local/', 'https://example.com:8443/', 'file:///tmp/x'):
            self.assertEqual(normalize_url(value), '')
        with self.assertRaises(ValueError):
            public_addresses('localhost', 80, lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))])

    def test_readonly_material_route_keeps_read_url_tool(self):
        from agent_runtime.ai import complete_chat
        ai = Mock(config={})
        ai.harness.research.return_value = Mock(text='结果', usage={})
        handler = Mock(return_value={'status': 'ok', 'content': '正文'})
        complete_chat(ai, [{'role': 'user', 'content': '总结这个网页 https://example.com/a'}],
                      None, [READ_URL_TOOL], handler, Mock())
        kwargs = ai.harness.research.call_args.kwargs
        self.assertEqual(kwargs['tools'][0]['name'], 'read_url')
        self.assertIn('output_schema', kwargs['tools'][0])
        kwargs['handler']('read_url', {'url': 'https://example.com/a'})
        handler.assert_called_once_with('read_url', {'url': 'https://example.com/a'})


if __name__ == '__main__':
    unittest.main()
