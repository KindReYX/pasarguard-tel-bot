#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:?PROJECT_DIR is required}"
SERVICE_NAME="${SERVICE_NAME:-pasarguard-admin-bot.service}"
SERVICE_USER="${SERVICE_USER:?SERVICE_USER is required}"
TAG="${1:-}"
STATUS_FILE="$PROJECT_DIR/.update_status.json"
PROGRESS_FILE="$PROJECT_DIR/.update_progress.json"

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid release tag: $TAG" >&2
  exit 2
fi
if [ ! -d "$PROJECT_DIR/.git" ]; then
  echo "Project is not a git clone. Install it with git clone first." >&2
  exit 3
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  exit 4
fi

run_as_service() {
  if [ "$SERVICE_USER" = "root" ]; then
    "$@"
  else
    runuser -u "$SERVICE_USER" -- "$@"
  fi
}

run_project_shell() {
  local command="$1"
  if [ "$SERVICE_USER" = "root" ]; then
    /bin/bash -c "cd \"$PROJECT_DIR\" && $command"
  else
    runuser -u "$SERVICE_USER" -- /bin/bash -c "cd \"$PROJECT_DIR\" && $command"
  fi
}

write_progress() {
  local step="$1" percent="$2" detail="${3:-}"
  local tmp
  tmp="$(mktemp)"
  python3 - "$step" "$percent" "$TAG" "$detail" "$tmp" <<'PYCODE'
import json, sys
step, percent, tag, detail, path = sys.argv[1:6]
with open(path, 'w', encoding='utf-8') as f:
    json.dump({'step': step, 'percent': int(percent), 'tag': tag, 'detail': detail}, f, ensure_ascii=False)
PYCODE
  install -o "$SERVICE_USER" -g "$(id -gn "$SERVICE_USER")" -m 0660 "$tmp" "$PROGRESS_FILE"
  rm -f "$tmp"
}

write_status() {
  local state="$1" detail="${2:-}"
  local tmp
  tmp="$(mktemp)"
  python3 - "$state" "$TAG" "$detail" "$tmp" <<'PYCODE'
import json, sys
state, tag, detail, path = sys.argv[1:5]
with open(path, 'w', encoding='utf-8') as f:
    json.dump({'status': state, 'tag': tag, 'detail': detail}, f, ensure_ascii=False)
PYCODE
  install -o "$SERVICE_USER" -g "$(id -gn "$SERVICE_USER")" -m 0660 "$tmp" "$STATUS_FILE"
  rm -f "$tmp"
}

REMOTE_URL="$(run_as_service git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
case "$REMOTE_URL" in
  https://github.com/KindReYX/pasarguard-tel-bot|https://github.com/KindReYX/pasarguard-tel-bot.git|git@github.com:KindReYX/pasarguard-tel-bot.git) ;;
  *) echo "Unexpected git origin: $REMOTE_URL" >&2; exit 5 ;;
esac

OLD_SHA="$(run_as_service git -C "$PROJECT_DIR" rev-parse HEAD)"
write_progress starting 5

# Back up the live DB before touching code or schema. Keep the exact backup path
# so a failed migration can restore both code and data atomically.
write_progress backup 15
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
  BACKUP_OUTPUT="$(run_project_shell 'venv/bin/python scripts/db_backup.py')"
else
  BACKUP_OUTPUT="$(run_project_shell 'python3 scripts/db_backup.py')"
fi
BACKUP_PATH="$(printf '%s\n' "$BACKUP_OUTPUT" | tail -n 1)"
if [ -n "$BACKUP_PATH" ] && [ ! -f "$BACKUP_PATH" ]; then BACKUP_PATH=""; fi

# Schema migrations must never run while the bot is serving requests.
WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE_NAME"; then WAS_ACTIVE=1; fi
write_progress stop 22
systemctl stop "$SERVICE_NAME" || true
UPDATE_FINISHED=0
emergency_cleanup() {
  if [ "$UPDATE_FINISHED" -eq 0 ] && [ "$WAS_ACTIVE" -eq 1 ] && ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
  fi
}
trap emergency_cleanup EXIT

restore_database_backup() {
  [ -n "${BACKUP_PATH:-}" ] || return 0
  local db_path
  db_path="$(grep -E '^DB_PATH=' "$PROJECT_DIR/.env" | tail -1 | cut -d= -f2- || true)"
  db_path="${db_path:-bot.db}"
  case "$db_path" in /*) ;; *) db_path="$PROJECT_DIR/$db_path" ;; esac
  python3 - "$BACKUP_PATH" <<'PYRESTORE'
import sqlite3, sys
p=sys.argv[1]
c=sqlite3.connect(p); r=c.execute('PRAGMA integrity_check').fetchone(); c.close()
if not r or str(r[0]).lower() != 'ok': raise SystemExit(f'backup integrity failed: {r}')
PYRESTORE
  cp -f "$BACKUP_PATH" "$db_path"
  rm -f "${db_path}-wal" "${db_path}-shm"
  chown "$SERVICE_USER:$(id -gn "$SERVICE_USER")" "$db_path" 2>/dev/null || true
}

write_progress fetch 30
run_as_service git -C "$PROJECT_DIR" fetch --tags --force origin
if ! run_as_service git -C "$PROJECT_DIR" rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null; then
  echo "Release tag does not exist on origin: $TAG" >&2
  exit 6
fi
TARGET_SHA="$(run_as_service git -C "$PROJECT_DIR" rev-parse "refs/tags/$TAG^{commit}")"
if [ "$TARGET_SHA" = "$OLD_SHA" ]; then
  if [ "$WAS_ACTIVE" -eq 1 ]; then systemctl start "$SERVICE_NAME" || true; fi
  UPDATE_FINISHED=1
  write_status success "Already on requested release"
  exit 0
fi

write_progress checkout 45
run_as_service git -C "$PROJECT_DIR" reset --hard "$TAG"
write_progress verify 55
EXPECTED_VERSION="${TAG#v}"
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
ACTUAL_VERSION="$(run_project_shell "$PYTHON -c 'from version import __version__; print(__version__)'" 2>/dev/null || true)"
if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]; then
  run_as_service git -C "$PROJECT_DIR" reset --hard "$OLD_SHA"
  restore_database_backup || true
  if [ "$WAS_ACTIVE" -eq 1 ]; then systemctl start "$SERVICE_NAME" || true; fi
  UPDATE_FINISHED=1
  write_status failed "version.py does not match release tag"
  echo "version.py ($ACTUAL_VERSION) does not match $TAG" >&2
  exit 7
fi

update_runtime() {
  if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "venv is missing; run scripts/install_or_update.sh manually once" >&2
    return 1
  fi
  write_progress dependencies 70
  run_project_shell 'venv/bin/python -m pip install -r requirements.txt' || return 1
  write_progress compile 82
  run_project_shell 'venv/bin/python -W error::SyntaxWarning -m py_compile ./*.py modules/*.py scripts/*.py' || return 1
  write_progress migrate 90
  run_project_shell 'venv/bin/python scripts/preflight.py --migrate' || return 1
}

if update_runtime; then
  write_progress restart 96
  systemctl restart "$SERVICE_NAME"
  sleep 3
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    write_progress success 100
    UPDATE_FINISHED=1
    write_status success "Updated from $OLD_SHA to $TARGET_SHA"
    exit 0
  fi
fi

ERROR_MSG="update failed; rolling back to $OLD_SHA"
write_progress rollback 92 "$ERROR_MSG"
echo "$ERROR_MSG" >&2
run_as_service git -C "$PROJECT_DIR" reset --hard "$OLD_SHA"
restore_database_backup || true
if update_runtime; then
  write_progress restart 96
  systemctl restart "$SERVICE_NAME" || true
  sleep 2
fi
if systemctl is-active --quiet "$SERVICE_NAME"; then
  write_progress failed 100 "$ERROR_MSG; rollback succeeded"
  write_status failed "$ERROR_MSG; rollback succeeded"
else
  write_progress failed 100 "$ERROR_MSG; rollback restart also failed - manual intervention required"
  write_status failed "$ERROR_MSG; rollback restart also failed - manual intervention required"
fi
UPDATE_FINISHED=1
exit 8
