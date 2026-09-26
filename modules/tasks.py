from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from aiogram import Bot
import db
from utils import retry_async
from datetime import datetime, timezone
import math
_TASKS_STARTED = False



def _to_timestamp(value) -> int:
    if value in (None, '', 0, '0'):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0

def start_background_tasks(bot: Bot) -> None:
    global _TASKS_STARTED
    if _TASKS_STARTED: return
    _TASKS_STARTED = True
    asyncio.create_task(service_job_loop())
    asyncio.create_task(auto_backup_loop(bot))
    asyncio.create_task(service_warning_loop(bot))
    asyncio.create_task(test_service_reminder_loop(bot))
    asyncio.create_task(navasan_rate_loop())
    asyncio.create_task(receipt_delivery_retry_loop(bot))
    from modules.updater import github_update_loop
    asyncio.create_task(github_update_loop(bot))


async def receipt_delivery_retry_loop(bot: Bot) -> None:
    """Background retry loop for any receipts whose delivery to admins failed."""
    while True:
        try:
            from modules.card_transfer import _notify_approvers
            pending = db.pending_receipt_deliveries(limit=5)
            for receipt in pending:
                try:
                    await _notify_approvers(bot, receipt)
                except Exception as exc:
                    db.record_system_error('tasks.receipt_retry', str(exc), entity_type='card_receipt', entity_id=str(receipt['id']), error_type=type(exc).__name__)
        except Exception as exc:
            db.record_system_error('tasks.receipt_delivery_retry_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(45)

async def service_job_loop() -> None:
    while True:
        try:
            from modules.customer import process_queued_service_jobs
            await process_queued_service_jobs(limit=3)
        except Exception as exc:
            db.record_system_error('tasks.service_job_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(60)

AUTO_BACKUP_LOCK_KEY = 'auto_backup:run'
AUTO_BACKUP_FAILURE_RETRY_SECONDS = 600


def _auto_backup_interval_hours() -> int:
    raw = db.get_setting('auto_backup_interval_hours', 6)
    try:
        hours = int(raw)
    except Exception:
        hours = 6
    return max(1, min(hours, 24 * 30))


def _auto_backup_next_run_at() -> str:
    """Next scheduled run in UTC iso (seconds precision) based on persisted state."""
    from datetime import timedelta
    now = db.now_iso_dt()
    raw = db.get_setting('auto_backup_next_run_at', '')
    if raw:
        try:
            next_run = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            if next_run.tzinfo is not None:
                next_run = next_run.astimezone(timezone.utc).replace(tzinfo=None)
            # If overdue, run now (catch-up) — do not push the schedule forward on restarts.
            if next_run < now:
                return now.isoformat(timespec='seconds')
            return next_run.replace(microsecond=0).isoformat(timespec='seconds')
        except Exception:
            pass
    # First start after deployment: match previous behavior of one immediate backup.
    return now.isoformat(timespec='seconds')


def _auto_backup_destinations() -> list[int]:
    """Delivery destination for scheduled backups: configured chat or all active super admins."""
    raw = str(db.get_setting('auto_backup_chat_id', '') or '').strip()
    if raw:
        if raw.lstrip('-').isdigit():
            return [int(raw)]
    rows = db.fetchall("SELECT telegram_id FROM bot_admins WHERE role='super_admin' AND is_active=1")
    return [int(row['telegram_id']) for row in rows]


def _set_auto_backup_state(*, status: str, error: str | None = None, success: bool) -> None:
    db.set_setting('auto_backup_last_attempt_at', db.now_iso())
    db.set_setting('auto_backup_last_status', status)
    if success:
        db.set_setting('auto_backup_last_success_at', db.now_iso())
        from datetime import timedelta
        next_run = (db.now_iso_dt() + timedelta(hours=_auto_backup_interval_hours())).isoformat(timespec='seconds')
        db.set_setting('auto_backup_next_run_at', next_run)
        db.set_setting('auto_backup_last_error', '')
    else:
        if error is not None:
            db.set_setting('auto_backup_last_error', str(error)[:1000])
        from datetime import timedelta
        next_run = (db.now_iso_dt() + timedelta(seconds=AUTO_BACKUP_FAILURE_RETRY_SECONDS)).isoformat(timespec='seconds')
        db.set_setting('auto_backup_next_run_at', next_run)


async def run_auto_backup_job(bot: Bot) -> bool:
    """One scheduled backup execution: generate via existing make_backup, verify, deliver, record."""
    from aiogram.types import FSInputFile
    dest_path = None
    try:
        from modules.system import make_backup, resolve_backup_file
        dest_path = resolve_backup_file(Path(make_backup()))
        if dest_path is None:
            raise RuntimeError('Backup file missing or empty after generation')
    except Exception as exc:
        logging.error(f'Auto backup generation failed: {exc}')
        db.log_backup(None, 'failed', str(exc))
        db.record_system_error('tasks.auto_backup.generate', str(exc), error_type=type(exc).__name__)
        _set_auto_backup_state(status='failed', error=str(exc), success=False)
        return False

    # Deliver to configured destination(s) using the same mechanism as manual backup.
    delivered_any = False
    delivery_errors: list[str] = []
    for chat_id in _auto_backup_destinations():
        try:
            await retry_async(
                lambda chat_id=chat_id: bot.send_document(
                    chat_id,
                    FSInputFile(dest_path),
                    caption=f'💾 بکاپ خودکار دیتابیس ربات\n{Path(dest_path).name}',
                ),
                attempts=4,
                base_delay=2.0,
            )
            delivered_any = True
        except Exception as exc:
            delivery_errors.append(f'chat {chat_id}: {exc}')
            logging.error(f'Auto backup delivery to {chat_id} failed: {exc}')

    if delivered_any:
        db.log_backup(str(dest_path), 'delivered_auto', '; '.join(delivery_errors)[:900])
        _set_auto_backup_state(status='success', success=True)
        return True

    err = '; '.join(delivery_errors) or 'No destination configured'
    db.log_backup(str(dest_path), 'failed_delivery', err[:900])
    db.record_system_error('tasks.auto_backup.deliver', err, error_type='AutoBackupDeliveryError')
    _set_auto_backup_state(status='failed', error=err, success=False)
    return False


async def auto_backup_loop(bot: Bot) -> None:
    """Reliable persisted auto backup scheduler.

    - Reads configuration from the settings table every cycle (admin changes apply live).
    - Persists next_run_at so restarts never duplicate or lose the schedule.
    - Uses an operation lock so multiple instances/workers never run the same backup.
    """
    from datetime import timedelta
    while True:
        try:
            enabled = bool(db.get_setting('auto_backup_enabled', True))
            if enabled:
                next_run_raw = _auto_backup_next_run_at()
                try:
                    next_run = datetime.fromisoformat(next_run_raw.replace('Z', '+00:00'))
                    if next_run.tzinfo is not None:
                        next_run = next_run.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    next_run = db.now_iso_dt()
                if db.now_iso_dt() >= next_run:
                    interval_hours = _auto_backup_interval_hours()
                    # Cross-instance idempotency: only one worker executes the scheduled backup.
                    if db.acquire_lock(AUTO_BACKUP_LOCK_KEY, owner='auto_backup_loop', ttl_seconds=max(300, interval_hours * 3600 // 2)):
                        try:
                            await run_auto_backup_job(bot)
                        finally:
                            db.release_lock(AUTO_BACKUP_LOCK_KEY)
        except Exception as exc:
            db.record_system_error('tasks.auto_backup_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(30)

RENEW_WARNING_DEFAULT = (
    '⏰ سرویس شما رو به اتمام است.\n\n'
    '📦 بسته: {package_name}\n'
    '👤 سرویس: {service_name}\n'
    '⏳ زمان باقی‌مانده: {remaining_time}\n'
    '📅 تاریخ اتمام: {expire_at}\n\n'
    'برای جلوگیری از قطع شدن، از بخش سرویس‌های من تمدید کنید.'
)


def _warning_expire_at(timestamp: int) -> str:
    if timestamp <= 0:
        return '-'
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(timestamp)


def _remaining_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        days = max(1, math.ceil(seconds / 86400))
        return f'{days} روز'
    if seconds >= 3600:
        hours = max(1, math.ceil(seconds / 3600))
        return f'{hours} ساعت'
    minutes = max(1, math.ceil(seconds / 60))
    return f'{minutes} دقیقه'


async def _send_expire_warning(bot: Bot, service: dict, seconds: int, expire: int) -> None:
    from utils import render_template_html

    uid = int(service.get('telegram_id') or 0)
    if not uid:
        return
    user = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (uid,)) or {}
    support = str(db.get_setting('support_username', '') or '')
    package_name = str(service.get('package_name') or service.get('package_id') or 'نامشخص')
    service_name = str(service.get('service_username') or service.get('panel_username') or '-')
    days = max(0, math.ceil(max(0, seconds) / 86400))
    hours = max(0, math.ceil(max(0, seconds) / 3600))
    template = str(db.get_setting('renew_warning_text', RENEW_WARNING_DEFAULT) or RENEW_WARNING_DEFAULT)
    context = {
        'name': str(user.get('first_name') or user.get('username') or uid),
        'first_name': str(user.get('first_name') or ''),
        'username': ('@' + str(user.get('username'))) if user.get('username') else '',
        'user_id': uid,
        'service_name': service_name,
        'package_name': package_name,
        'package_id': str(service.get('package_id') or ''),
        'days': days,
        'hours': hours,
        'remaining_time': _remaining_text(seconds),
        'expire_at': _warning_expire_at(expire),
        'support': support,
    }
    text = await render_template_html(bot, template, context)
    await bot.send_message(uid, text, parse_mode='HTML')


async def service_warning_loop(bot: Bot) -> None:
    while True:
        try:
            if not bool(db.get_setting('service_warnings_enabled', True)):
                await asyncio.sleep(3600); continue
            import time
            now = int(time.time())
            # Stream only active services that need attention: expire in next 3 days OR traffic > 70%
            # Avoid SELECT * on potentially large table; fetch only needed columns and batch.
            rows = db.fetchall('''
                SELECT id, telegram_id, package_id, package_name, service_username, panel_username, expire, data_limit, used_traffic
                FROM user_services
                WHERE is_active=1 AND (
                    (expire IS NOT NULL AND expire != '' AND CAST(expire AS INTEGER) > 0)
                    OR (data_limit > 0 AND used_traffic >= data_limit * 0.7)
                )
                LIMIT 2000
            ''')
            # Fallback if expire stored as text ISO: we still iterate but with smaller set
            for service in rows:
                uid = int(service.get('telegram_id') or 0); sid = int(service.get('id') or 0)
                expire = _to_timestamp(service.get('expire'))
                if uid and sid and expire > 0:
                    seconds = expire - now
                    # Exactly one expiry reminder per expiry cycle, during the final 24 hours.
                    # Include the expiry timestamp in the dedupe key so a later renewal can
                    # generate one new reminder for the new expiration date.
                    warning_key = f'expire_24h:{expire}'
                    if 0 < seconds <= 86400 and db.register_service_warning(sid, uid, warning_key):
                        try:
                            await _send_expire_warning(bot, service, seconds, expire)
                        except Exception as exc:
                            db.record_system_error('tasks.expire_warning_send', str(exc), entity_type='service', entity_id=str(sid), error_type=type(exc).__name__)
                        await asyncio.sleep(0.05)
                limit = int(service.get('data_limit') or 0); used = int(service.get('used_traffic') or 0)
                if uid and sid and limit > 0 and used >= int(limit * 0.8) and db.register_service_warning(sid, uid, 'traffic_80'):
                    try: await bot.send_message(uid, '⚠️ بیش از ۸۰ درصد حجم سرویس شما مصرف شده است. می‌توانید سرویس را تمدید کنید.')
                    except Exception: pass
                    await asyncio.sleep(0.05)
                # Rate-limit API calls per iteration batch
                if rows and len(rows) > 500:
                    await asyncio.sleep(0.01)
        except Exception as exc:
            db.record_system_error('tasks.service_warning_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(3600)


TEST_SERVICE_REMINDER_DEFAULT = (
    '🧪 دوباره می‌توانید سرویس تست بگیرید.\n\n'
    'از منوی ربات وارد بخش «سرویس تست» شوید و سرویس جدیدتان را دریافت کنید.'
)


async def test_service_reminder_loop(bot: Bot) -> None:
    """Notify each user once when their test-service cooldown has finished."""
    from utils import render_template_html
    while True:
        try:
            if bool(db.get_setting('test_service_enabled', True)):
                cooldown_days = int(db.get_setting('test_service_cooldown_days', 14) or 14)
                if cooldown_days > 0:
                    rows = db.due_test_service_reminders(cooldown_days, limit=200)
                    template = str(db.get_setting('test_service_reminder_text', TEST_SERVICE_REMINDER_DEFAULT) or TEST_SERVICE_REMINDER_DEFAULT)
                    support = str(db.get_setting('support_username', '') or '')
                    for row in rows:
                        uid = int(row.get('user_id') or 0)
                        if not uid:
                            continue
                        user = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (uid,)) or {}
                        context = {
                            'name': str(user.get('first_name') or user.get('username') or uid),
                            'first_name': str(user.get('first_name') or ''),
                            'username': ('@' + str(user.get('username'))) if user.get('username') else '',
                            'user_id': uid,
                            'cooldown_days': cooldown_days,
                            'support': support,
                        }
                        try:
                            text = await render_template_html(bot, template, context)
                            await bot.send_message(uid, text, parse_mode='HTML')
                            db.mark_test_reminder_sent(uid, str(row.get('created_at') or ''))
                        except Exception as exc:
                            db.record_system_error('tasks.test_service_reminder_send', str(exc), user_id=uid, error_type=type(exc).__name__)
                        await asyncio.sleep(0.05)
        except Exception as exc:
            db.record_system_error('tasks.test_service_reminder_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(3600)


async def navasan_rate_loop() -> None:
    try:
        from modules.navasan import scheduled_rate_loop
        await scheduled_rate_loop()
    except Exception as exc:
        db.record_system_error('tasks.navasan_rate_loop', str(exc), error_type=type(exc).__name__)
