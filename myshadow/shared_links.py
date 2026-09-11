"""Translate WeChat app-message link cards into small domain values."""
import html
import re
import xml.etree.ElementTree as ET

from .url_reader import normalize_url


LINK_APP_TYPES = {'4', '5'}
URL_PATTERN = re.compile(r'https?://[^\s<>"\'\u3000，。；：！？、（）【】《》「」]+', re.I)


def _clean(value, limit):
    value = html.unescape(value or '')
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def shared_link(message, sender=''):
    """Return a verified-looking public web link from a WeChat share card."""
    if not isinstance(message, str) or not message or len(message) > 65536:
        return None
    if sender and message.startswith(sender + ':\n'):
        message = message[len(sender) + 2:]
    if '<!DOCTYPE' in message.upper() or '<!ENTITY' in message.upper():
        return None
    start = message.find('<msg')
    if start < 0:
        return None
    try:
        root = ET.fromstring(message[start:])
        app = root.find('./appmsg')
        if app is None or (app.findtext('type') or '').strip() not in LINK_APP_TYPES:
            return None
        url = normalize_url(app.findtext('url') or app.findtext('lowurl') or '')
        if not url:
            return None
        title = _clean(app.findtext('title') or '', 200) or '未命名链接'
        description = _clean(app.findtext('des') or app.findtext('description') or '', 300)
        return {'title': title, 'url': url, 'description': description,
                'app_type': (app.findtext('type') or '').strip()}
    except (ET.ParseError, ValueError, UnicodeError):
        return None


def shared_link_text(message, sender=''):
    link = shared_link(message, sender)
    if not link:
        return ''
    text = '[链接分享] ' + link['title']
    if link['description']:
        text += '\n简介：' + link['description']
    return text + '\n' + link['url']


def urls_in_text(text, limit=12):
    if not isinstance(text, str):
        return []
    output = []
    for match in URL_PATTERN.finditer(html.unescape(text[:100000])):
        candidate = match.group(0).rstrip('.,;:!?，。；：！？、）】》」\'')
        url = normalize_url(candidate)
        if url and url not in output:
            output.append(url)
        if len(output) >= limit:
            break
    return output


def urls_in_messages(messages, limit=12):
    output = []
    for message in messages:
        if message.get('role') != 'user':
            continue
        for url in urls_in_text(message.get('content', ''), limit):
            if url not in output:
                output.append(url)
            if len(output) >= limit:
                return output
    return output
