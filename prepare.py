#!/usr/bin/env python3
"""Create project-local directories and a separate browser login secret."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent
os.umask(0o077)
for name in ('data/wechat', 'logs'):
    (root / name).mkdir(parents=True, exist_ok=True)
env_file = root / '.env'
if not env_file.exists():
    with env_file.open('x') as stream:
        stream.write('WECHAT_WEB_PASSWORD=' + secrets.token_urlsafe(30) + '\n')
env_file.chmod(0o600)
print('Project directories and private browser credentials prepared.')
