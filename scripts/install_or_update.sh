#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-pasarguard-admin-bot.service}"
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo/root"; exit 1; fi
if [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "Missing $PROJECT_DIR/.env"; echo "Create it from .env.example first."; exit 1
fi
EXISTING_USER="$(systemctl cat "$SERVICE_NAME" 2>/dev/null | sed -n 's/^User=//p' | head -1 || true)"
PROJECT_OWNER="$(stat -c '%U' "$PROJECT_DIR")"
SERVICE_USER="${SERVICE_USER:-${EXISTING_USER:-$PROJECT_OWNER}}"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER"; exit 1
fi
cd "$PROJECT_DIR"
echo "[1/7] Safe pre-update database backup"
python3 scripts/db_backup.py || { echo "Backup failed; update aborted."; exit 1; }
echo "[2/7] Stop service"
WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then WAS_ACTIVE=1; fi
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
FAILED=1
cleanup() {
  if [ "$FAILED" -ne 0 ] && [ "$WAS_ACTIVE" -eq 1 ]; then
    echo "Update failed; trying to start service again with current files..."
    systemctl start "$SERVICE_NAME" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "[3/7] Python environment"
if ! command -v python3 >/dev/null 2>&1; then apt-get update && apt-get install -y python3 python3-venv python3-pip; fi
if ! python3 -m venv --help >/dev/null 2>&1; then apt-get update && apt-get install -y python3-venv python3-pip; fi
if [ ! -x venv/bin/python ]; then python3 -m venv venv; fi
if ! venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "pip missing from virtualenv; repairing..."
  venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || {
    echo "Could not repair pip; recreating virtualenv..."
    rm -rf venv
    python3 -m venv venv
  }
fi
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt

echo "[4/7] Compile + migrate + preflight"
venv/bin/python -W error::SyntaxWarning -m py_compile ./*.py modules/*.py scripts/*.py
venv/bin/python scripts/preflight.py --migrate

echo "[5/7] Install/update systemd service"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__SERVICE_USER__|$SERVICE_USER|g" deploy/pasarguard-admin-bot.service > "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null

echo "[6/7] Nginx callback config (when callback domain exists)"
if grep -qE '^PAYMENT_CALLBACK_DOMAIN=.+$|^TETRAPAY_CALLBACK_URL=.+$|^PLISIO_CALLBACK_URL=.+$' .env; then
  bash scripts/setup_payment_nginx.sh
else
  echo "No payment callback domain/URL; skipped nginx setup."
fi

echo "[7/7] Start service"
systemctl restart "$SERVICE_NAME"
sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "Service did not become active. Recent logs:"
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  exit 1
fi
systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,25p'
FAILED=0
trap - EXIT
echo "Done. Live log: journalctl -u $SERVICE_NAME -f"
