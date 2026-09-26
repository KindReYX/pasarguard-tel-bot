#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo ".env not found: $ENV_FILE"
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
DOMAIN="${PAYMENT_CALLBACK_DOMAIN:-}"
if [ -z "$DOMAIN" ] && [ -n "${TETRAPAY_CALLBACK_URL:-}" ]; then
  DOMAIN="$(python3 - <<PY
from urllib.parse import urlparse
print(urlparse('${TETRAPAY_CALLBACK_URL}').hostname or '')
PY
)"
fi
if [ -z "$DOMAIN" ] && [ -n "${PLISIO_CALLBACK_URL:-}" ]; then
  DOMAIN="$(python3 - <<PY
from urllib.parse import urlparse
print(urlparse('${PLISIO_CALLBACK_URL}').hostname or '')
PY
)"
fi
HOST="${PAYMENT_WEBHOOK_HOST:-127.0.0.1}"
PORT="${PAYMENT_WEBHOOK_PORT:-8081}"
echo "1) Internal app:"
curl -i "http://${HOST}:${PORT}/health" || true
echo
if [ -n "$DOMAIN" ]; then
  echo "2) Nginx by Host header:"
  curl -i "http://127.0.0.1/health" -H "Host: ${DOMAIN}" || true
  echo
  echo "3) Public HTTP:"
  curl -i "http://${DOMAIN}/health" || true
  echo
  echo "4) Public HTTPS:"
  curl -ik "https://${DOMAIN}/health" || true
  echo
  echo "5) Enabled nginx configs for domain:"
  sudo nginx -T 2>/dev/null | grep -n -A 28 -B 5 "$DOMAIN" || true
fi
