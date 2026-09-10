#!/usr/bin/env python3
"""Check the local browser desktop without displaying its credentials."""
import base64
from pathlib import Path
import urllib.error
import urllib.request

root = Path(__file__).resolve().parent
values = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines()
              if line and not line.startswith('#') and '=' in line)
url = 'http://127.0.0.1:18080/'
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        print('Unauthenticated HTTP:', response.status)
except urllib.error.HTTPError as error:
    print('Unauthenticated HTTP:', error.code)
password = values['WECHAT_WEB_PASSWORD']
auth = base64.b64encode(('weixin:' + password).encode()).decode()
request = urllib.request.Request(url, headers={'Authorization': 'Basic ' + auth})
with urllib.request.urlopen(request, timeout=10) as response:
    print('Authenticated HTTP:', response.status)
    print('Response bytes:', len(response.read()))
