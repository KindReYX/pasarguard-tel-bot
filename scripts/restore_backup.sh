#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-pasarguard-admin-bot.service}"
BACKUP="${1:-}"
if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
  echo "Usage: sudo bash scripts/restore_backup.sh /path/to/backup.db"; exit 1
fi
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
if [ ! -f "$ENV_FILE" ]; then echo ".env not found"; exit 1; fi
DB_PATH="$(grep -E '^DB_PATH=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
DB_PATH="${DB_PATH:-bot.db}"
case "$DB_PATH" in /*) ;; *) DB_PATH="$PROJECT_DIR/$DB_PATH" ;; esac
python3 - "$BACKUP" <<'PY'
import sqlite3,sys
p=sys.argv[1]
c=sqlite3.connect(p); r=c.execute('PRAGMA integrity_check').fetchone(); c.close()
if not r or str(r[0]).lower()!='ok': raise SystemExit(f'Backup is corrupt: {r}')
print('Backup integrity: OK')
PY
systemctl stop "$SERVICE_NAME" || true
if [ -f "$DB_PATH" ]; then
  cp -a "$DB_PATH" "${DB_PATH}.before-restore-$(date +%Y%m%d-%H%M%S)"
fi
cp -a "$BACKUP" "$DB_PATH"
rm -f "${DB_PATH}-wal" "${DB_PATH}-shm"
chown --reference="$PROJECT_DIR" "$DB_PATH" 2>/dev/null || true
systemctl start "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,22p'
