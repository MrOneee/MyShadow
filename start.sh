#!/usr/bin/env bash
set -euo pipefail
shadow_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$shadow_root"
docker info >/dev/null
python3 prepare.py
if [[ ! -f bot.json ]]; then cp bot.json.example bot.json; fi
if [[ ! -f ai.json ]]; then cp ai.json.example ai.json; fi
chmod 600 .env ai.json bot.json
bash compose.sh config --quiet
bash compose.sh up -d --build
echo 'Desktop: http://127.0.0.1:18080 (SSH tunnel for remote access)'
echo 'Status: bash compose.sh logs --tail=40 desktop'
