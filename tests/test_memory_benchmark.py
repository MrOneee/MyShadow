"""Benchmark protocol checks; no paid API calls and no WeChat dependencies."""
import tempfile
import unittest
from pathlib import Path

from benchmarks.memory_eval import (
    MemoryService, evidence, identities, parse_time, selections, sessions,
)


class MemoryBenchmarkProtocolTests(unittest.TestCase):
    def test_lme_oracle_only_contains_gold_sessions_not_answer(self):
        obj = {'answer': 'SECRET_GOLD', 'answer_session_ids': ['gold'],
               'haystack_dates': ['2023/05/20 (Sat) 02:21', '2023/05/21 (Sun) 02:21'],
               'haystack_session_ids': ['noise', 'gold'],
               'haystack_sessions': [[{'role': 'user', 'content': 'noise'}],
                                    [{'role': 'user', 'content': 'evidence'}]]}
        result = evidence('lme', obj, obj)
        self.assertEqual([x['session_id'] for x in result], ['gold'])
        self.assertNotIn('SECRET_GOLD', str(result))

    def test_locomo_oracle_does_not_import_annotations(self):
        obj = {'conversation': {'speaker_a': 'Alice', 'speaker_b': 'Bob',
               'session_1_date_time': '1:56 pm on 8 May, 2023',
               'session_1': [{'dia_id': 'D1:1', 'speaker': 'Alice', 'text': 'fact'},
                             {'dia_id': 'D1:2', 'speaker': 'Bob', 'text': 'noise'}]},
               'event_summary': 'FORBIDDEN_SUMMARY'}
        result = evidence('locomo', obj, {'evidence': ['D1:1'], 'answer': 'SECRET_GOLD'})
        self.assertEqual([x['dia_id'] for x in result], ['D1:1'])
        self.assertNotIn('SECRET_GOLD', str(result))
        self.assertNotIn('FORBIDDEN_SUMMARY', str(result))
        self.assertEqual(len(identities('locomo', obj)[1]), 2)

    def test_sampling_is_repeatable_and_missing_category_is_not_filled(self):
        data = {'lme': [{'question_id': str(i), 'question_type': 'single-session-user'} for i in range(10)]
                + [{'question_id': str(i) + '_abs', 'question_type': 'single-session-user'} for i in range(10)],
                'locomo': [{'qa': [{'category': 1} for _ in range(5)]}]}
        picked = selections(data)
        self.assertEqual(picked, selections(data))
        self.assertEqual(len(picked), 10)
        self.assertEqual(sum(x['category'] == 'abstention' for x in picked), 4)
        self.assertEqual(len(set((x['dataset'], x['qid']) for x in picked)), 10)

    def test_historical_fixture_uses_production_admission_and_separate_stores(self):
        with tempfile.TemporaryDirectory() as temp:
            a = MemoryService(Path(temp) / 'a')
            b = MemoryService(Path(temp) / 'b')
            with a.db() as db:
                db.execute("UPDATE meta SET value='0' WHERE key='started'")
            a.observe('user', 'user', 'source', parse_time('2023/05/20 (Sat) 02:21', 'lme'), 'I enjoy hiking.')
            with a.db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
            with b.db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
