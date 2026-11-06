"""Verify the confirmed-reply queue preserves the memory service's input limits."""
import ast
from pathlib import Path
import sqlite3
import tempfile
import time
from types import MethodType, SimpleNamespace
import unittest

from myshadow.member_memory import MemoryService
from myshadow.member_retrieval import USER_LIMIT, ASSISTANT_LIMIT


def queue_method():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'myshadow/bot.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Bot')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'queue_member_memory')
    namespace = {'USER_LIMIT': USER_LIMIT, 'ASSISTANT_LIMIT': ASSISTANT_LIMIT}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'Bot.queue_member_memory', 'exec'), namespace)
    return namespace['queue_member_memory']


class MemoryDeliveryWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryService(Path(self.temp.name))
        self.state = sqlite3.connect(':memory:')
        self.state.row_factory = sqlite3.Row
        self.addCleanup(self.state.close)
        self.state.execute('CREATE TABLE memory_outbox(group_id,sender,source,kind,created,text,assistant)')
        self.bot = SimpleNamespace(memory_v2=True, social=self.memory, state=self.state)
        self.bot.queue_member_memory = MethodType(queue_method(), self.bot)

    def test_long_confirmed_reply_survives_queue_and_observe(self):
        reply = 'General astronomy context. ' * 55 + 'The recommended focal length is 825 millimeters.'
        self.bot.queue_member_memory('group', 'member', 'delivery-1', time.time(),
                                     'Please explain the observatory optics plan.', 'exchange', reply)
        row = self.state.execute('SELECT * FROM memory_outbox').fetchone()
        self.assertEqual(row['assistant'], reply)
        self.memory.observe(row['group_id'], row['sender'], row['source'], row['created'],
                            row['text'], row['kind'], row['assistant'])
        with self.memory.db() as db:
            self.assertEqual(db.execute('SELECT assistant FROM events').fetchone()[0], reply)

    def test_queue_limits_match_service_limits(self):
        self.bot.queue_member_memory('group', 'member', 'delivery-2', time.time(),
                                     'Learning astronomy. ' * 100, 'exchange', 'Telescope advice. ' * 400)
        row = self.state.execute('SELECT * FROM memory_outbox').fetchone()
        self.assertEqual(len(row['text']), USER_LIMIT)
        self.assertEqual(len(row['assistant']), ASSISTANT_LIMIT)

    def test_full_reply_is_screened_before_truncation(self):
        safe_prefix = 'General astronomy context. ' * 200
        self.bot.queue_member_memory('group', 'member', 'delivery-3', time.time(),
                                     'Please explain the observatory optics plan.', 'exchange',
                                     safe_prefix + '密码是不能记忆的敏感内容。')
        row = self.state.execute('SELECT * FROM memory_outbox').fetchone()
        self.assertEqual(row['assistant'], '[已确认回应]')


if __name__ == '__main__':
    unittest.main()
