import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from myshadow.bot import Bot, ContextBudgetExceeded, estimate_tokens


class PromptBudgetTests(unittest.TestCase):
    def make_bot(self):
        bot = Bot.__new__(Bot)
        bot.config = {'context_token_budget': 12800, 'mode': 'preview',
                      'max_age_seconds': 300, 'bot_name': '影', 'bot_id': 'wxid_bot',
                      'system_prompt': (Path(__file__).resolve().parents[1] / 'persona.txt').read_text(encoding='utf-8')}
        bot.ai = Mock(config={'max_tokens': 1024})
        bot.history_search = Mock()
        bot.scheduler = Mock()
        bot.scheduler.authorized.return_value = True
        bot.trigger_sender = Mock(return_value='wxid_admin')
        bot.require_group = Mock(return_value={'group_id': 'wxid_admin', 'group_name': '管理员'})
        bot.context_for = Mock(return_value=([], '管理员'))
        bot.memory_for = Mock(return_value='')
        bot.image_context_for = Mock(return_value='')
        return bot

    def test_private_reminder_fits_with_history_available(self):
        bot = self.make_bot()
        trigger = {'group_id': 'wxid_admin', 'prompt': '5分钟后提醒我喝水'}
        messages, _ = bot.messages_for(trigger)
        self.assertGreater(bot.input_budget_for(trigger), 6800)
        self.assertLess(estimate_tokens(messages), bot.input_budget_for(trigger))
        self.assertIn(trigger['prompt'], messages[-1]['content'])
        bot.history_search.handler.assert_not_called()
        bot.scheduler.manage.assert_not_called()

    def test_history_availability_reserves_schema_not_two_future_results(self):
        bot = self.make_bot()
        trigger = {'group_id': 'wxid_admin', 'prompt': '你好'}
        with_history = bot.input_budget_for(trigger)
        bot.history_search = None
        self.assertLess(bot.input_budget_for(trigger) - with_history, 1500)
        bot.ai.config['max_tokens'] += 500
        self.assertEqual(bot.input_budget_for(trigger), 12800 - 1024 - 500 - 512)

    def test_fixed_overflow_is_handled_once_without_task_or_ai_call(self):
        bot = self.make_bot()
        bot.state = sqlite3.connect(':memory:')
        bot.state.row_factory = sqlite3.Row
        self.addCleanup(bot.state.close)
        bot.state.execute('CREATE TABLE replies(id INTEGER PRIMARY KEY,group_id,shard,local_id,created,prompt,reply,status)')
        bot.state.execute("INSERT INTO replies VALUES(1,'wxid_admin','test',1,?,'5分钟后提醒我喝水',NULL,'pending')", (int(time.time()),))
        bot.participation_current = Mock(return_value=True)
        bot.messages_for = Mock(side_effect=ContextBudgetExceeded('too long'))
        bot.process_group('wxid_admin')
        bot.process_group('wxid_admin')
        bot.messages_for.assert_called_once()
        bot.ai.complete.assert_not_called()
        bot.scheduler.manage.assert_not_called()
        row = bot.state.execute('SELECT status,reply FROM replies').fetchone()
        self.assertEqual(row['status'], 'preview')
        self.assertIn('没有设置或修改任务', row['reply'])

    def test_persona_character_budget_and_identity(self):
        root = Path(__file__).resolve().parents[1]
        for path, name in [('persona.txt', '影'), ('personas/laoying.md', '牢影'), ('personas/jiangcheng.md', '蒋丞')]:
            text = (root / path).read_text(encoding='utf-8')
            self.assertLessEqual(len(text), 3000)
            self.assertIn(name, text)
            self.assertIn('task_id只在工具内部使用', text)
            self.assertNotIn('小说', text)
            self.assertNotIn('王也', text)


if __name__ == '__main__':
    unittest.main()
