from __future__ import annotations
import html
import sqlite3
from pathlib import Path
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import api_client, db
from config import settings
from keyboards import EditableButtonFilter
from keyboards import BTN_PAYMENT_ENGINE, BTN_SELLER_PANEL, BTN_SYSTEM_ERRORS, BTN_SYSTEM_HEALTH, back_keyboard
from permissions import deny_callback, deny_message, is_admin, is_seller, is_super_admin
from utils import person_display, answer_callback, edit_or_answer

system_router = Router()

def system_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 بررسی اتصال API', callback_data='system:check_api')],
        [InlineKeyboardButton(text='🧹 پردازش صف ساخت سرویس', callback_data='system:run_jobs')],
        [InlineKeyboardButton(text='💾 بکاپ فوری دیتابیس', callback_data='system:backup_now')],
    ])

def errors_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 بروزرسانی خطاها', callback_data='system:errors:list')],
        [InlineKeyboardButton(text='✅ حل شده کردن همه خطاها', callback_data='system:errors:resolve_all')],
    ])


def _plain_clip(value, limit: int = 160) -> str:
    text = str(value if value is not None else '-').replace('\n', ' ').replace('\r', ' ').strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _error_button_text(row) -> str:
    mark = '✅' if row.get('is_resolved') else '🔴'
    error_type = _plain_clip(row.get('error_type') or 'Error', 18)
    source = _plain_clip(row.get('source') or '-', 24)
    return f"{mark} #{row['id']} | {error_type} | {source}"


def system_errors_keyboard(limit: int = 12) -> InlineKeyboardMarkup:
    rows = db.recent_system_errors(limit, unresolved_only=False)
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(text=_error_button_text(row), callback_data=f"system:error:{int(row['id'])}")])
    keyboard.append([InlineKeyboardButton(text='🔄 بروزرسانی خطاها', callback_data='system:errors:list')])
    keyboard.append([InlineKeyboardButton(text='✅ حل شده کردن همه خطاها', callback_data='system:errors:resolve_all')])
    keyboard.append([InlineKeyboardButton(text='🗑 حذف خطاهای حل شده', callback_data='system:errors:delete_resolved_confirm')])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_system_errors_text(limit: int = 12) -> str:
    rows = db.recent_system_errors(limit, unresolved_only=False)
    counts = db.system_errors_counts()
    if not rows and counts['total'] == 0:
        return '🚨 خطاهای سیستم\n\nخطایی ثبت نشده است.'
    return (
        '🚨 خطاهای سیستم\n\n'
        f'🔴 خطاهای حل نشده: {counts["unresolved"]}\n'
        f'✅ خطاهای حل شده: {counts["resolved"]}\n'
        f'📊 کل خطاهای موجود: {counts["total"]}\n\n'
        'برای دیدن جزئیات یا عملیات حل/حذف، روی دکمه همان خطا بزنید.'
    )


def build_error_detail_text(row) -> str:
    if not row:
        return 'خطا پیدا نشد.'
    detail = row.get('details') or '-'
    message = row.get('message') or '-'
    text = (
        '🚨 جزئیات خطای سیستم\n\n'
        f'شناسه: #{row.get("id")}\n'
        f'وضعیت: {"✅ حل شده" if row.get("is_resolved") else "🔴 حل نشده"}\n'
        f'منبع: {row.get("source") or "-"}\n'
        f'نوع: {row.get("error_type") or "-"}\n'
        f'کاربر: {person_display(row.get("user_id")) if row.get("user_id") else "-"}\n'
        f'موجودیت: {(row.get("entity_type") or "-")}:{(row.get("entity_id") or "-")}\n'
        f'زمان: {row.get("created_at") or "-"}\n\n'
        f'پیام:\n{message}\n\n'
        f'جزئیات:\n{detail}'
    )
    if len(text) > 3600:
        text = text[:3600] + '\n\n… ادامه متن کوتاه شد تا تلگرام پیام را رد نکند.'
    return text


def error_detail_keyboard(error_id: int, is_resolved: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if not is_resolved:
        buttons.append([InlineKeyboardButton(text='✅ حل شده شد', callback_data=f'system:error_resolve:{int(error_id)}')])
    buttons.append([InlineKeyboardButton(text='🗑 حذف این خطا', callback_data=f'system:error_delete_confirm:{int(error_id)}')])
    buttons.append([InlineKeyboardButton(text='🔙 برگشت به لیست خطاها', callback_data='system:errors:list')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📦 صف ساخت سرویس', callback_data='payment:jobs')],
        [InlineKeyboardButton(text='🧾 سفارش های نیازمند بررسی', callback_data='payment:review_orders')],
        [InlineKeyboardButton(text='🔁 تلاش دوباره صف', callback_data='system:run_jobs')],
    ])

async def build_health_text() -> str:
    db_ok = True
    db_message = 'سالم'
    try:
        db.fetchone('SELECT 1 AS ok')
    except Exception as exc:
        db_ok = False
        db_message = str(exc)
    jobs = db.service_job_stats()
    latest_backup = db.latest_backup()
    errors_count = db.fetchone('SELECT COUNT(*) AS c FROM system_errors WHERE is_resolved=0') or {'c': 0}
    orders_review = db.fetchone("SELECT COUNT(*) AS c FROM orders WHERE status IN ('needs_admin_review','build_queued','building_service')") or {'c': 0}
    db_size = Path(settings.db_path).stat().st_size if Path(settings.db_path).exists() else 0
    return (
        '🩺 وضعیت سیستم\n\n'
        f"دیتابیس: {'✅' if db_ok else '❌'} {db_message}\n"
        f"صف ساخت سرویس: queued={jobs.get('queued',0)} retry={jobs.get('retry',0)} running={jobs.get('running',0)} failed={jobs.get('failed',0)} done={jobs.get('done',0)}\n"
        f"سفارش های نیازمند بررسی: {int(orders_review['c'])}\n"
        f"خطاهای حل نشده: {int(errors_count['c'])}\n"
        f"آخرین بکاپ: {latest_backup.get('created_at') if latest_backup else 'ثبت نشده'}\n"
        f"حجم دیتابیس: {db_size:,} بایت\n"
    )

@system_router.message(EditableButtonFilter(BTN_SYSTEM_HEALTH))
async def system_health(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    await message.answer(await build_health_text(), reply_markup=back_keyboard())
    await message.answer('اقدام:', reply_markup=system_menu())

@system_router.callback_query(F.data == 'system:check_api')
async def check_api(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        await api_client.get_panel_token(force=True)
        await callback.answer('اتصال API سالم است.', show_alert=True)
    except Exception as exc:
        db.record_system_error('system.check_api', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await callback.answer('اتصال API خطا دارد. در خطاهای سیستم ثبت شد.', show_alert=True)

@system_router.message(EditableButtonFilter(BTN_SYSTEM_ERRORS))
async def system_errors(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    await message.answer(build_system_errors_text(), reply_markup=back_keyboard())
    await message.answer('آخرین خطاها:', reply_markup=system_errors_keyboard())


@system_router.callback_query(F.data == 'system:errors:list')
async def system_errors_refresh(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        await edit_or_answer(callback, build_system_errors_text(), reply_markup=system_errors_keyboard())
        await answer_callback(callback, 'بروزرسانی شد.')
    except Exception as exc:
        db.record_system_error('system.errors_refresh', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در نمایش خطاهای سیستم.', show_alert=True)



@system_router.callback_query(F.data.startswith('system:error:'))
async def system_error_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        error_id = int(callback.data.rsplit(':', 1)[1])
        row = db.get_system_error(error_id)
        is_resolved = bool(row and row.get('is_resolved'))
        await edit_or_answer(callback, build_error_detail_text(row), reply_markup=error_detail_keyboard(error_id, is_resolved=is_resolved))
        await answer_callback(callback)
    except Exception as exc:
        db.record_system_error('system.error_detail', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در نمایش جزئیات.', show_alert=True)


@system_router.callback_query(F.data.startswith('system:error_resolve:'))
async def system_error_resolve_one(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        error_id = int(callback.data.rsplit(':', 1)[1])
        db.resolve_system_error(error_id)
        row = db.get_system_error(error_id)
        await edit_or_answer(callback, build_error_detail_text(row), reply_markup=error_detail_keyboard(error_id, is_resolved=True))
        await answer_callback(callback, 'خطا علامت‌گذاری شد به عنوان حل شده.')
    except Exception as exc:
        db.record_system_error('system.error_resolve_one', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در تغییر وضعیت.', show_alert=True)


@system_router.callback_query(F.data.startswith('system:error_delete_confirm:'))
async def system_error_delete_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    error_id = int(callback.data.rsplit(':', 1)[1])
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⚠️ بله، این خطا را حذف کن', callback_data=f'system:error_delete_do:{error_id}')],
        [InlineKeyboardButton(text='🔙 انصراف و برگشت', callback_data=f'system:error:{error_id}')],
    ])
    await edit_or_answer(callback, f'آیا از حذف کامل خطای #{error_id} از لیست اطمینان دارید؟', reply_markup=confirm_kb)
    await answer_callback(callback)


@system_router.callback_query(F.data.startswith('system:error_delete_do:'))
async def system_error_delete_do(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        error_id = int(callback.data.rsplit(':', 1)[1])
        db.delete_system_error(error_id)
        await answer_callback(callback, 'خطا حذف شد.', show_alert=True)
        await edit_or_answer(callback, build_system_errors_text(), reply_markup=system_errors_keyboard())
    except Exception as exc:
        db.record_system_error('system.error_delete', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در حذف.', show_alert=True)


@system_router.callback_query(F.data == 'system:errors:delete_resolved_confirm')
async def system_errors_delete_resolved_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    counts = db.system_errors_counts()
    resolved_count = counts['resolved']
    if resolved_count <= 0:
        await callback.answer('خطای حل شده‌ای برای حذف وجود ندارد.', show_alert=True)
        return
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'⚠️ تایید حذف {resolved_count} خطای حل شده', callback_data='system:errors:delete_resolved_do')],
        [InlineKeyboardButton(text='🔙 انصراف', callback_data='system:errors:list')],
    ])
    await edit_or_answer(callback, f'آیا می‌خواهید تمام {resolved_count} خطای حل شده را حذف کنید؟\nاین عملیات غیرقابل برگشت است.', reply_markup=confirm_kb)
    await answer_callback(callback)


@system_router.callback_query(F.data == 'system:errors:delete_resolved_do')
async def system_errors_delete_resolved_do(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        deleted = db.delete_all_resolved_system_errors()
        await answer_callback(callback, f'{deleted} خطای حل شده حذف شد.', show_alert=True)
        await edit_or_answer(callback, build_system_errors_text(), reply_markup=system_errors_keyboard())
    except Exception as exc:
        db.record_system_error('system.errors_delete_resolved', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در حذف گروهی.', show_alert=True)


@system_router.callback_query(F.data == 'system:errors:resolve_all')
async def resolve_all_errors(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    db.execute('UPDATE system_errors SET is_resolved=1 WHERE COALESCE(is_deleted, 0)=0')
    await callback.answer('همه خطاها حل شده شدند.', show_alert=True)
    await edit_or_answer(callback, build_system_errors_text(), reply_markup=system_errors_keyboard())

def resolve_backup_file(preferred: Path) -> Path | None:
    """Return the preferred backup path if usable, else the newest valid one in the same dir."""
    try:
        if preferred.exists() and preferred.stat().st_size > 0:
            return preferred
        candidates = sorted(preferred.parent.glob('bot_backup_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        for cand in candidates:
            if cand.exists() and cand.stat().st_size > 0:
                return cand
    except Exception:
        return None
    return None


def make_backup() -> Path:
    src = Path(settings.db_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f'Database not found: {src}')
    backup_dir = (src.parent / 'backups').resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"bot_backup_{db.now_iso().replace(':','-')}.db"
    source = sqlite3.connect(str(src), timeout=30)
    target = sqlite3.connect(str(dest), timeout=30)
    try:
        source.execute('PRAGMA busy_timeout=30000')
        source.backup(target)
        target.commit()
        result = target.execute('PRAGMA integrity_check').fetchone()
        if not result or str(result[0]).lower() != 'ok':
            raise RuntimeError(f'Backup integrity_check failed: {result}')
    finally:
        target.close(); source.close()
    # The retention loop sorts by mtime, which lies if the server clock was
    # stepped backward or a restore preserved old timestamps: then the file we
    # just created sorts into backups[7:] and gets purged a moment later. Never
    # delete the file this call produced, and verify it survived before returning.
    try:
        backups = sorted(backup_dir.glob('bot_backup_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in backups[7:]:
            try:
                if old.resolve() != dest.resolve():
                    old.unlink()
            except Exception:
                pass
    except Exception:
        pass
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError(f'Backup file missing or empty right after creation: {dest}')
    db.log_backup(str(dest), 'done', 'sqlite_backup_api')
    return dest

@system_router.callback_query(F.data == 'system:backup_now')
async def backup_now(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        dest = make_backup()
        await callback.answer('بکاپ ساخته شد.', show_alert=True)
        await callback.message.answer(f'بکاپ ساخته شد:\n`{dest}`', parse_mode='Markdown')
    except Exception as exc:
        db.record_system_error('system.backup_now', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await callback.answer('خطا در ساخت بکاپ.', show_alert=True)

@system_router.message(EditableButtonFilter(BTN_PAYMENT_ENGINE))
async def payment_engine_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    stats = db.service_job_stats()
    text = '🧾 موتور پرداخت و ساخت سرویس\n\nاز این بخش سفارش های پرداخت شده، صف ساخت سرویس و سفارش های نیازمند بررسی کنترل می شوند.\n\n'
    text += f"صف: queued={stats.get('queued',0)} retry={stats.get('retry',0)} failed={stats.get('failed',0)} done={stats.get('done',0)}"
    await message.answer(text, reply_markup=back_keyboard())
    await message.answer('اقدام:', reply_markup=payment_menu())

@system_router.callback_query(F.data == 'payment:jobs')
async def payment_jobs(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    rows = db.fetchall('SELECT * FROM service_jobs ORDER BY id DESC LIMIT 30')
    text = '📦 صف ساخت سرویس\n\n'
    if not rows:
        text += 'صف خالی است.'
    for row in rows:
        text += f"#{row['id']} order=`{row['order_id']}` status={row['status']} attempts={row['attempts']} err={row.get('last_error') or '-'}\n"
    await edit_or_answer(callback, text, reply_markup=payment_menu(), parse_mode='Markdown')
    await answer_callback(callback)

@system_router.callback_query(F.data == 'payment:review_orders')
async def review_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    rows = db.fetchall("SELECT * FROM orders WHERE status IN ('needs_admin_review','build_queued','building_service') ORDER BY id DESC LIMIT 30")
    text = '🧾 سفارش های نیازمند بررسی\n\n'
    if not rows:
        text += 'موردی وجود ندارد.'
    for row in rows:
        text += f"#{row['id']} {person_display(row['user_id'])} package={row.get('package_name')} status={row.get('status')}\n"
    await edit_or_answer(callback, text, reply_markup=payment_menu(), parse_mode='Markdown')
    await answer_callback(callback)

@system_router.callback_query(F.data == 'system:run_jobs')
async def run_jobs_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try:
        from modules.customer import process_queued_service_jobs
        count = await process_queued_service_jobs(limit=10)
        await callback.answer(f'{count} job پردازش شد.', show_alert=True)
    except Exception as exc:
        db.record_system_error('system.run_jobs', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await callback.answer('خطا در پردازش صف.', show_alert=True)

@system_router.message(EditableButtonFilter(BTN_SELLER_PANEL))
async def seller_panel(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not (is_seller(uid) or is_admin(uid)):
        await deny_message(message); return
    stats = db.seller_commission_stats(uid)
    orders = db.fetchall('SELECT * FROM orders WHERE seller_id=? ORDER BY id DESC LIMIT 20', (int(uid),))
    text = '🤝 پنل فروشنده\n\n'
    text += f"آیدی عددی شما: `{uid}`\nکل کمیسیون: {stats['total']:,} تومان\nکمیسیون تسویه نشده: {stats['pending']:,} تومان\n\nآخرین سفارش های شما:\n"
    if not orders:
        text += 'سفارشی ثبت نشده است.'
    for row in orders:
        text += f"#{row['id']} | {person_display(row['user_id'])} | {row.get('package_name')} | {row.get('status')}\n"
    await message.answer(text, reply_markup=back_keyboard(), parse_mode='Markdown')
