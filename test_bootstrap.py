"""Initialization must not start the bot until login and identity are ready."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'wxid_test_abcd' / 'db_storage'
        self.base.mkdir(parents=True)
        self.paths = [self.base / name for name in ('contact.db', 'session.db', 'message_0.db')]
        for path in self.paths:
            path.touch()
        (self.root / 'bot.json').write_text(json.dumps({'bot_id': 'wxid_test', 'bot_name': 'Tester', 'mode': 'preview'}))
        for patcher in (
            patch.object(bootstrap, 'ROOT', self.root),
            patch.dict(bootstrap.os.environ, {'WECHAT_UI_SCRIPT': 'ui.py'}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_not_logged_in_does_not_read_databases(self):
        with patch.object(bootstrap.subprocess, 'run', return_value=Mock(returncode=1)), patch.object(bootstrap, 'databases') as databases:
            self.assertEqual(bootstrap.readiness(), 'waiting_for_wechat_login')
            databases.assert_not_called()

    def check(self):
        with patch.object(bootstrap.subprocess, 'run', return_value=Mock(returncode=0)), patch.object(bootstrap, 'databases', return_value=(self.base, self.paths)):
            return bootstrap.readiness()

    def test_missing_identity_waits(self):
        (self.root / 'bot.json').write_text('{}')
        self.assertEqual(self.check(), 'waiting_for_bot_identity_config')

    def test_wrong_account_waits(self):
        with patch.dict(bootstrap.os.environ, {'WECHAT_BOT_ID': 'wxid_other'}):
            self.assertEqual(self.check(), 'configured_bot_account_mismatch')

    def test_incomplete_sync_waits(self):
        self.paths[0].unlink()
        self.assertEqual(self.check(), 'waiting_for_database_sync')

    def test_environment_overrides_mode_and_name(self):
        with patch.dict(bootstrap.os.environ, {'WECHAT_NAME': 'New name', 'BOT_MODE': 'preview'}), patch('ai_client.AIClient'), patch.object(bootstrap, 'snapshot'), patch.object(bootstrap, 'extract_keys') as extract:
            self.assertEqual(self.check(), 'ready')
            extract.assert_not_called()
        config = json.loads((self.root / 'bot.json').read_text())
        self.assertEqual(config['bot_name'], 'New name')
        self.assertEqual(config['mode'], 'preview')

    def test_stale_keys_are_refreshed(self):
        with patch('ai_client.AIClient'), patch.object(bootstrap, 'snapshot', side_effect=ValueError('stale')), patch.object(bootstrap, 'extract_keys') as extract:
            self.assertEqual(self.check(), 'ready')
            extract.assert_called_once_with()
