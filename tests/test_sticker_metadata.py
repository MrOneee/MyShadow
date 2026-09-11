import unittest

from myshadow.sticker_metadata import sticker_description, sticker_text


def xml(description='', extra=''):
    return '<msg><emoji desc="' + description + '" ' + extra + '/></msg>'


class StickerMetadataTests(unittest.TestCase):
    def test_decodes_verified_wechat_locale_metadata(self):
        samples = {
            'Cg8KBXpoX2NuEgbngavlvbE=': '火影',
            'Cg4KB2RlZmF1bHQSA+WVig==': '啊',
            'ChEKB2RlZmF1bHQSBuWYu+WYuw==': '嘻嘻',
        }
        for encoded, expected in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(sticker_description(xml(encoded)), expected)
                self.assertEqual(sticker_text(xml(encoded)), '[表情，微信附带描述：' + expected + ']')

    def test_prefers_simplified_chinese_and_skips_empty_locales(self):
        encoded = 'ChIKBXpoX2NuEgnlk4jlk4jlk4gKCQoFemhfdHcSAAoLCgdkZWZhdWx0EgA='
        self.assertEqual(sticker_description(xml(encoded)), '哈哈哈')

    def test_missing_or_invalid_description_stays_generic(self):
        for message in ('<msg><emoji/></msg>', xml('%%%'), 'not xml', '<msg><emoji desc=""/></msg>'):
            with self.subTest(message=message):
                self.assertEqual(sticker_text(message), '[表情]')

    def test_does_not_expose_other_sticker_attributes(self):
        message = xml('', 'cdnurl="https://secret.invalid/token" aeskey="private"')
        self.assertEqual(sticker_text(message), '[表情]')

    def test_rejects_dtd_and_oversized_input(self):
        encoded = 'Cg8KBXpoX2NuEgbngavlvbE='
        self.assertEqual(sticker_text('<!DOCTYPE msg><msg><emoji desc="' + encoded + '"/></msg>'), '[表情]')
        self.assertEqual(sticker_text('<msg>' + 'x' * 65536 + '</msg>'), '[表情]')


if __name__ == '__main__':
    unittest.main()
