import unittest

from myshadow.reply_style import reply_style, reply_bubbles, readable_reply, style_instruction


class ReplyStyleTests(unittest.TestCase):
    def test_same_turn_is_deterministic_and_profiles_vary(self):
        always={'style':{'casual_target_chars':'4到15字','split_probability':1,
                         'three_bubble_probability':1,'natural_markers':['好']}}
        never={'style':{'split_probability':0}}
        self.assertEqual(reply_style(always,'在不在','g:1'),reply_style(always,'在不在','g:1'))
        self.assertEqual(reply_style(always,'在不在','g:1')['bubbles'],3)
        self.assertEqual(reply_style(never,'在不在','g:1')['bubbles'],1)

    def test_serious_and_detailed_turns_do_not_split(self):
        profile={'style':{'split_probability':1,'three_bubble_probability':1}}
        self.assertEqual(reply_style(profile,'我今天很难受','x')['bubbles'],1)
        self.assertEqual(reply_style(profile,'给我一个完整的实现方案','x')['bubbles'],1)

    def test_protocol_is_internal_and_bounded(self):
        text='行<|next_bubble|>我看看<|next_bubble|>等下回你'
        self.assertEqual(reply_bubbles(text),['行','我看看','等下回你'])
        self.assertEqual(readable_reply(text),'行\n我看看\n等下回你')
        malformed='a<|next_bubble|>b<|next_bubble|>c<|next_bubble|>d'
        self.assertEqual(len(reply_bubbles(malformed)),1)
        self.assertNotIn('<|next_bubble|>',reply_bubbles(malformed)[0])
        self.assertIn('恰好3条',style_instruction({'detailed':False,'serious':False,
            'bubbles':3,'target':'4到15字','markers':[],'sticker_intents':[]}))


if __name__=='__main__':unittest.main()
