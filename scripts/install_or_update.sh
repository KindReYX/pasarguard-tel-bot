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

echo "[5/7] Install/update systemd service + secure self-update helper"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__SERVICE_USER__|$SERVICE_USER|g" deploy/pasarguard-admin-bot.service > "/etc/systemd/system/$SERVICE_NAME"
if [ "$SERVICE_USER" != "root" ] && ! command -v sudo >/dev/null 2>&1; then
  apt-get update && apt-get install -y sudo
fi
mkdir -p /usr/local/libexec /etc/sudoers.d
install -o root -g root -m 0755 scripts/apply_github_update.sh /usr/local/libexec/pasarguard-apply-update
cat > /usr/local/sbin/pasarguard-bot-update <<EOF
#!/usr/bin/env bash
set -euo pipefail
TAG="\${1:-}"
if [[ ! "\$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "Invalid version tag" >&2; exit 2; fi
PROJECT_DIR='$PROJECT_DIR'
SERVICE_NAME='$SERVICE_NAME'
SERVICE_USER='$SERVICE_USER'
UNIT="pasarguard-bot-update-\$(date +%s)"
exec systemd-run --unit="\$UNIT" --collect --property=Type=oneshot \
  --setenv=PROJECT_DIR="\$PROJECT_DIR" --setenv=SERVICE_NAME="\$SERVICE_NAME" --setenv=SERVICE_USER="\$SERVICE_USER" \
  /usr/local/libexec/pasarguard-apply-update "\$TAG"
EOF
chmod 0755 /usr/local/sbin/pasarguard-bot-update
chown root:root /usr/local/sbin/pasarguard-bot-update /usr/local/libexec/pasarguard-apply-update
if [ "$SERVICE_USER" != "root" ]; then
  cat > /etc/sudoers.d/pasarguard-bot-updater <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/pasarguard-bot-update *
EOF
  chmod 0440 /etc/sudoers.d/pasarguard-bot-updater
  if command -v visudo >/dev/null 2>&1; then visudo -cf /etc/sudoers.d/pasarguard-bot-updater >/dev/null; fi
fi
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
