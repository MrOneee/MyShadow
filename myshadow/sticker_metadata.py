"""Safely extract the weak text hint carried by a WeChat sticker message."""
import base64
import binascii
import re
import unicodedata
import xml.etree.ElementTree as ET


MAX_XML_CHARS = 65536
MAX_DESC_CHARS = 4096
MAX_DECODED_BYTES = 2048
MAX_LABEL_CHARS = 40
LOCALE_PRIORITY = ('zh_cn', 'default', 'zh_tw')


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError('truncated varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
    raise ValueError('varint is too long')


def _fields(data):
    """Yield bounded protobuf fields without depending on generated schemas."""
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('invalid field number')
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire == 1:
            if offset + 8 > len(data):
                raise ValueError('truncated fixed64')
            value, offset = data[offset:offset + 8], offset + 8
        elif wire == 2:
            size, offset = _varint(data, offset)
            if size > MAX_DECODED_BYTES or offset + size > len(data):
                raise ValueError('invalid length-delimited field')
            value, offset = data[offset:offset + size], offset + size
        elif wire == 5:
            if offset + 4 > len(data):
                raise ValueError('truncated fixed32')
            value, offset = data[offset:offset + 4], offset + 4
        else:
            raise ValueError('unsupported wire type')
        yield number, wire, value


def _clean_label(value):
    value = ''.join(' ' if ch.isspace() or unicodedata.category(ch) in ('Cc', 'Cf') else ch for ch in value)
    value = re.sub(r'\s+', ' ', value).strip()
    value = value.replace('[', '［').replace(']', '］')
    return value[:MAX_LABEL_CHARS]


def sticker_description(message):
    """Return WeChat's publisher/client supplied sticker description, if any."""
    if not isinstance(message, str) or not message or len(message) > MAX_XML_CHARS:
        return ''
    if '<!DOCTYPE' in message.upper() or '<!ENTITY' in message.upper():
        return ''
    start = message.find('<msg')
    if start < 0:
        return ''
    try:
        root = ET.fromstring(message[start:])
        emoji = root if root.tag == 'emoji' else root.find('.//emoji')
        encoded = emoji.get('desc', '') if emoji is not None else ''
        if not encoded or len(encoded) > MAX_DESC_CHARS:
            return ''
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_DECODED_BYTES:
            return ''
        locales = []
        for number, wire, locale_message in _fields(raw):
            if number != 1 or wire != 2:
                continue
            locale = description = ''
            for inner_number, inner_wire, value in _fields(locale_message):
                if inner_wire != 2 or inner_number not in (1, 2):
                    continue
                decoded = value.decode('utf-8')
                if inner_number == 1:
                    locale = decoded.casefold()
                else:
                    description = _clean_label(decoded)
            if description:
                locales.append((locale, description))
        for preferred in LOCALE_PRIORITY:
            for locale, description in locales:
                if locale == preferred:
                    return description
        return locales[0][1] if locales else ''
    except (ValueError, UnicodeError, ET.ParseError, binascii.Error):
        return ''


def sticker_text(message='', description=None):
    if description is None:
        description = sticker_description(message)
    return '[表情，微信附带描述：' + description + ']' if description else '[表情]'
