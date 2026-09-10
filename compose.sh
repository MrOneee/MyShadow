#!/usr/bin/env bash
set -euo pipefail
weixin_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export DOCKER_CONFIG="${SHADOW_DOCKER_CONFIG:-$weixin_root/.docker-client}"
mkdir -p "$DOCKER_CONFIG"
chmod 700 "$DOCKER_CONFIG"
exec docker compose \
  --project-directory "$weixin_root" \
  --env-file "$weixin_root/.env" \
  -f "$weixin_root/compose.yaml" "$@"
