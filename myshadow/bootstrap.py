"""Wait for a real client login before initializing the bot; never log secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .wechat_db import ROOT, databases, extract_keys, private_json, snapshot


def prepare_ui():
    target = Path('/config/bot-ui')
    target.mkdir(parents=True, exist_ok=True)
    for name in ('ui.py', 'native_stickers.py'):
        shutil.copyfile(ROOT / 'bot_ui' / name, target / name)
    os.environ['WECHAT_UI_SCRIPT'] = str(target / 'ui.py')
    os.environ['WECHAT_NATIVE_STICKER_SCRIPT'] = str(target / 'native_stickers.py')


def readiness():
    result = subprocess.run([sys.executable, os.environ['WECHAT_UI_SCRIPT'], 'logged-in'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    if result.returncode:
        return 'waiting_for_wechat_login'
    base, paths = databases()
    if not all(p.is_file() for p in paths) or len(paths) < 3:
        return 'waiting_for_database_sync'
    config = json.loads((ROOT / 'bot.json').read_text(encoding='utf-8'))
    for variable, field in (('WECHAT_NAME', 'bot_name'), ('WECHAT_BOT_ID', 'bot_id'), ('BOT_MODE', 'mode')):
        if os.environ.get(variable):
            config[field] = os.environ[variable]
    if not config.get('bot_id') or not config.get('bot_name'):
        return 'waiting_for_bot_identity_config'
    if base.parent.name != config['bot_id'] and not base.parent.name.startswith(config['bot_id'] + '_'):
        return 'configured_bot_account_mismatch'
    if config.get('mode') not in ('preview', 'send'):
        return 'invalid_bot_mode'
    from .ai_client import AIClient
    AIClient()  # Validate configuration without making an API request.
    try:
        with snapshot('contact/contact.db') as connection:
            connection.execute('SELECT 1').fetchone()
    except (OSError, ValueError, KeyError, RuntimeError):
        extract_keys()
    private_json(ROOT / 'bot.json', config)
    return 'ready'


def main():
    os.umask(0o077)
    prepare_ui()
    previous = None
    while True:
        try:
            state = readiness()
        except Exception as exc:
            state = 'waiting_for_initialization:' + type(exc).__name__
        private_json(ROOT / 'bot-health.json', {'status': state, 'checked_at': int(time.time())})
        if state != previous:
            print(json.dumps({'event': state}), flush=True)
            previous = state
        if state == 'ready':
            os.execv(sys.executable, [sys.executable, '-u', '-m', 'myshadow.bot', 'run'])
        time.sleep(15)


if __name__ == '__main__':
    main()
