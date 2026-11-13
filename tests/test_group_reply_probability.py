import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from myshadow.selective_reply import SelectiveReply


class ProbabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'

    def write(self, default=1, groups=None):
        self.path.write_text(json.dumps({'default_probability': default,
                                       'groups': groups or {}}), encoding='utf-8')

    def policy(self):
        from myshadow.group_reply_probability import GroupReplyProbability
        return GroupReplyProbability(self.path)

    def test_default_override_and_live_reload(self):
        p = self.policy()
        self.assertEqual(p.probability('111@chatroom'), 1)
        self.write(.8, {'111@chatroom': .3})
        self.assertEqual(p.probability('111@chatroom'), .3)
        self.assertEqual(p.probability('222@chatroom'), .8)
        self.write(.2)
        self.assertEqual(p.probability('111@chatroom'), .2)

    def test_invalid_updates_keep_last_valid_snapshot(self):
        self.write(.3)
        p = self.policy()
        self.assertEqual(p.probability('111@chatroom'), .3)
        for value in (True, -.1, 1.1, '0.3', None, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.write(.9, {'111@chatroom': value})
                self.assertEqual(p.probability('222@chatroom'), .3)
        for text in ('{', '[]', '{"groups":[]}', '{"groups":{"wrong":0.2}}'):
            self.path.write_text(text, encoding='utf-8')
            self.assertEqual(p.probability('111@chatroom'), .3)
        self.path.unlink()
        self.assertEqual(p.probability('111@chatroom'), 1)

    def test_probability_boundaries(self):
        p = self.policy()
        for value, sample, expected in ((0, 0, False), (1, .999, True),
                                        (.3, .299, True), (.3, .3, False)):
            self.write(value)
            with patch('myshadow.group_reply_probability.random.random', return_value=sample):
                self.assertEqual(p.allows('111@chatroom'), expected)

    def participation(self, decision=True):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status,UNIQUE(group_id,shard,local_id))')
        ai = Mock(config={'model': 'test'})
        config = {'groups': ['111@chatroom'], 'probability_settings_file': str(self.path),
                  'decision': {'enabled': decision}}
        p = SelectiveReply(db, ai, config, 'bot')
        self.now = int(datetime(2026, 11, 13, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        self.clock = patch('myshadow.selective_reply.time.time', return_value=self.now).start()
        self.addCleanup(patch.stopall)
        return p, db, ai

    def add(self, p, text='大家有什么好玩的游戏推荐', ident=1):
        p.observe('111@chatroom', 'test', {'local_id': ident, 'sort_seq': ident,
                  'create_time': self.clock.return_value}, 'alice', text, None)
        self.clock.return_value += 3

    def test_rejected_decision_consumes_turn_without_retry(self):
        self.write(0)
        p, db, ai = self.participation()
        self.add(p)
        with patch.object(p.decision, 'judge', return_value={'action': 'brief', 'goal': '聊游戏', 'expression': None}) as judge:
            for _ in range(3):
                p.consider('111@chatroom')
            self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0], 0)
            self.assertEqual(judge.call_count, 1)
            self.assertEqual(db.execute('SELECT action FROM participation_turns').fetchone()[0], 'silent')

    def test_direct_call_bypasses_zero_probability(self):
        self.write(0)
        p, db, ai = self.participation()
        self.add(p, '影，来一下')
        p.consider('111@chatroom')
        self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0], 1)
        ai.request.assert_not_called()

    def test_wait_samples_only_final_reply(self):
        self.write(.3)
        p, db, ai = self.participation()
        self.add(p)
        with patch.object(p.decision, 'judge', side_effect=[{'action': 'wait', 'goal': '', 'expression': None},
                          {'action': 'answer', 'goal': '推荐游戏', 'expression': None}]), patch('myshadow.group_reply_probability.random.random', return_value=.2) as draw:
            p.consider('111@chatroom')
            draw.assert_not_called()
            self.clock.return_value += 11
            p.consider('111@chatroom')
            self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0], 1)
            draw.assert_called_once()

    def test_legacy_path_obeys_probability_and_no_retry(self):
        self.write(0)
        p, db, ai = self.participation(False)
        self.add(p)
        with patch.object(p, 'judge', return_value='public_question') as judge:
            p.consider('111@chatroom')
            p.consider('111@chatroom')
            self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0], 0)
            judge.assert_called_once()

    def test_idle_initiation_is_also_gated(self):
        self.write(0)
        p, db, ai = self.participation()
        self.add(p)
        self.clock.return_value += 301
        with patch.object(p.decision, 'judge', return_value={'action': 'initiate', 'goal': '接续游戏话题', 'expression': None}):
            p.consider('111@chatroom')
            self.assertEqual(db.execute('SELECT count(*) FROM replies').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
