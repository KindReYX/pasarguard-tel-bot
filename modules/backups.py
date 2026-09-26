from __future__ import annotations

import html
from pathlib import Path

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from config import settings
from keyboards import EditableButtonFilter
from keyboards import BTN_BACKUP
from permissions import is_super_admin, deny_message
from utils import edit_or_answer, answer_callback, retry_async


backups_router = Router()


class BackupSettingsState(StatesGroup):
    custom_interval_hours = State()
    backup_chat_id = State()


BACKUP_INTERVAL_CHOICES: list[tuple[int, str]] = [
    (1, 'هر ۱ ساعت'),
    (3, 'هر ۳ ساعت'),
    (6, 'هر ۶ ساعت'),
    (12, 'هر ۱۲ ساعت'),
    (24, 'هر ۲۴ ساعت (روزانه)'),
    (72, 'هر ۳ روز'),
    (168, 'هر ۷ روز'),
]


def _interval_label(hours: int) -> str:
    for value, label in BACKUP_INTERVAL_CHOICES:
        if value == hours:
            return label
    return f'هر {hours} ساعت (سفارشی)'


def _fmt_dt(value: str | None) -> str:
    return str(value) if value else 'ثبت نشده'


def backup_home_text() -> str:
    enabled = bool(db.get_setting('auto_backup_enabled', True))
    hours = db.get_setting('auto_backup_interval_hours', 6)
    chat_id = str(db.get_setting('auto_backup_chat_id', '') or '').strip()
    next_run = db.get_setting('auto_backup_next_run_at', '')
    last_success = db.get_setting('auto_backup_last_success_at', '')
    last_attempt = db.get_setting('auto_backup_last_attempt_at', '')
    last_status = db.get_setting('auto_backup_last_status', '')
    last_error = str(db.get_setting('auto_backup_last_error', '') or '').strip()
    latest = db.latest_backup()

    enabled_text = '✅ روشن' if enabled else '⛔️ خاموش'
    chat_text = f'<code>{html.escape(chat_id)}</code>' if chat_id else 'سوپرادمین های فعال (پیشفرض)'
    last_status_text = {'success': '✅ موفق', 'failed': '❌ ناموفق'}.get(last_status, last_status or 'ثبت نشده')

    text = (
        '💾 بخش بکاپ\n\n'
        'برای تهیه فوری یک بکاپ جدید، از دکمه «💾 تهیه بکاپ دستی» استفاده کنید.\n\n'
        '🤖 <b>تنظیمات بکاپ خودکار</b>\n'
        f'وضعیت: <b>{enabled_text}</b>\n'
        f'بازه ارسال: <b>{_interval_label(int(hours))}</b>\n'
        f'مقصد ارسال: {chat_text}\n'
        f'اجرای بعدی: {_fmt_dt(next_run)}\n'
        f'آخرین بکاپ موفق: {_fmt_dt(last_success)}\n'
        f'آخرین تلاش: {_fmt_dt(last_attempt)}\n'
        f'وضعیت آخرین تلاش: {last_status_text}\n'
    )
    if last_error:
        text += f'آخرین خطا: <code>{html.escape(last_error[:200])}</code>\n'
    text += f'آخرین فایل بکاپ ثبت شده: {_fmt_dt(latest.get("created_at") if latest else None)}'
    return text


def backup_home_keyboard() -> InlineKeyboardMarkup:
    enabled = bool(db.get_setting('auto_backup_enabled', True))
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text='💾 تهیه بکاپ دستی', callback_data='backup:manual')],
    ]
    if enabled:
        rows.append([InlineKeyboardButton(text='⛔️ غیرفعال کردن بکاپ خودکار', callback_data='backup:auto:disable')])
    else:
        rows.append([InlineKeyboardButton(text='✅ فعال کردن بکاپ خودکار', callback_data='backup:auto:enable')])
    rows.append([InlineKeyboardButton(text='⏱ تغییر بازه ارسال', callback_data='backup:auto:interval')])
    rows.append([InlineKeyboardButton(text='📥 تغییر مقصد ارسال', callback_data='backup:auto:dest')])
    rows.append([InlineKeyboardButton(text='🚀 اجرای بکاپ خودکار همین الان', callback_data='backup:auto:run_now')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def interval_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for value, label in BACKUP_INTERVAL_CHOICES:
        rows.append([InlineKeyboardButton(text=label, callback_data=f'backup:auto:interval:{value}')])
    rows.append([InlineKeyboardButton(text='✏️ بازه سفارشی (ساعت)', callback_data='backup:auto:interval:custom')])
    rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='backup:auto:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@backups_router.message(EditableButtonFilter(BTN_BACKUP))
async def backup_db(message: Message):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer(backup_home_text(), reply_markup=backup_home_keyboard(), parse_mode='HTML')


async def _deliver_manual_backup(bot, chat_id: int, admin_id: int) -> bool:
    from modules.system import make_backup, resolve_backup_file
    try:
        db_path = make_backup()
    except Exception as exc:
        db.record_system_error('backups.manual_generate', str(exc), user_id=admin_id, error_type=type(exc).__name__)
        return False
    db_path = resolve_backup_file(Path(db_path))
    if db_path is None:
        db.record_system_error('backups.manual_send', f'Backup file missing or empty: {db_path}', user_id=admin_id, error_type='BackupFileMissing')
        return False
    try:
        await retry_async(
            lambda: bot.send_document(chat_id, FSInputFile(db_path), caption='💾 بکاپ سالم دیتابیس ربات'),
            attempts=4,
            base_delay=2.0,
        )
    except Exception as exc:
        db.record_system_error('backups.manual_send', str(exc), user_id=admin_id, error_type=type(exc).__name__)
        db.log_backup(str(db_path), 'failed_delivery', str(exc)[:500])
        return False
    return True


@backups_router.callback_query(F.data == 'backup:manual')
async def backup_manual_cb(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    admin_id = callback.from_user.id
    if await _deliver_manual_backup(callback.bot, admin_id, admin_id):
        await answer_callback(callback, 'بکاپ ساخته و ارسال شد ✅')
    else:
        await answer_callback(callback, 'بکاپ یا ارسال فایل ناموفق بود. جزئیات در بخش خطاهای سیستم ثبت شد.', show_alert=True)
    try:
        await edit_or_answer(callback, backup_home_text(), reply_markup=backup_home_keyboard(), parse_mode='HTML')
    except Exception as exc:
        db.record_system_error('backups.manual_home', str(exc), user_id=admin_id, error_type=type(exc).__name__)


@backups_router.callback_query(F.data == 'backup:auto:home')
async def backup_home_cb(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    try:
        await edit_or_answer(callback, backup_home_text(), reply_markup=backup_home_keyboard(), parse_mode='HTML')
        await answer_callback(callback)
    except Exception as exc:
        db.record_system_error('backups.home', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در نمایش بکاپ.', show_alert=True)


@backups_router.callback_query(F.data == 'backup:auto:enable')
async def backup_enable(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    db.set_setting('auto_backup_enabled', True)
    # If no schedule exists yet, schedule next run one interval from now.
    if not str(db.get_setting('auto_backup_next_run_at', '') or '').strip():
        from datetime import timedelta
        hours = int(db.get_setting('auto_backup_interval_hours', 6) or 6)
        db.set_setting('auto_backup_next_run_at', (db.now_iso_dt() + timedelta(hours=hours)).isoformat(timespec='seconds'))
    db.add_log(callback.from_user.id, 'auto_backup_toggled', 'settings', 'auto_backup_enabled', {'value': True})
    await callback.answer('بکاپ خودکار روشن شد.')
    await backup_home_cb(callback)


@backups_router.callback_query(F.data == 'backup:auto:disable')
async def backup_disable(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    db.set_setting('auto_backup_enabled', False)
    db.add_log(callback.from_user.id, 'auto_backup_toggled', 'settings', 'auto_backup_enabled', {'value': False})
    await callback.answer('بکاپ خودکار خاموش شد.')
    await backup_home_cb(callback)


@backups_router.callback_query(F.data == 'backup:auto:interval')
async def backup_interval_menu(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    hours = int(db.get_setting('auto_backup_interval_hours', 6) or 6)
    await edit_or_answer(
        callback,
        f'⏱ بازه فعلی: <b>{_interval_label(hours)}</b>\n\nیک بازه جدید انتخاب کنید:',
        reply_markup=interval_keyboard(),
        parse_mode='HTML',
    )
    await answer_callback(callback)


@backups_router.callback_query(F.data.startswith('backup:auto:interval:'))
async def backup_interval_set(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    raw = callback.data.rsplit(':', 1)[-1]
    if raw == 'interval':
        return
    if raw == 'custom':
        await state.set_state(BackupSettingsState.custom_interval_hours)
        await callback.message.answer(
            '✏️ بازه سفارشی به ساعت وارد کنید (عدد بین ۱ تا 720).\nمثال: 48',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data='backup:auto:interval')]]),
        )
        await answer_callback(callback)
        return
    try:
        hours = int(raw)
    except ValueError:
        await answer_callback(callback, 'مقدار نامعتبر است.', show_alert=True)
        return
    if not 1 <= hours <= 720:
        await answer_callback(callback, 'بازه باید بین ۱ تا ۷۲۰ ساعت باشد.', show_alert=True)
        return
    db.set_setting('auto_backup_interval_hours', hours)
    from datetime import timedelta
    db.set_setting('auto_backup_next_run_at', (db.now_iso_dt() + timedelta(hours=hours)).isoformat(timespec='seconds'))
    db.add_log(callback.from_user.id, 'auto_backup_interval_changed', 'settings', 'auto_backup_interval_hours', {'hours': hours})
    await answer_callback(callback, f'بازه ذخیره شد: {_interval_label(hours)}')
    await backup_interval_menu(callback)


@backups_router.message(BackupSettingsState.custom_interval_hours)
async def backup_custom_interval(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip()
    if not raw.isdigit():
        await message.answer('لطفا فقط عدد ساعت وارد کنید. مثال: 48')
        return
    hours = int(raw)
    if not 1 <= hours <= 720:
        await message.answer('بازه باید بین ۱ تا ۷۲۰ ساعت باشد.')
        return
    db.set_setting('auto_backup_interval_hours', hours)
    from datetime import timedelta
    db.set_setting('auto_backup_next_run_at', (db.now_iso_dt() + timedelta(hours=hours)).isoformat(timespec='seconds'))
    db.add_log(message.from_user.id, 'auto_backup_interval_changed', 'settings', 'auto_backup_interval_hours', {'hours': hours})
    await state.clear()
    await message.answer(f'بازه سفارشی ذخیره شد: {_interval_label(hours)}')


@backups_router.callback_query(F.data == 'backup:auto:dest')
async def backup_dest_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    await state.set_state(BackupSettingsState.backup_chat_id)
    current = str(db.get_setting('auto_backup_chat_id', '') or '').strip()
    await callback.message.answer(
        '📥 مقصد بکاپ خودکار را مشخص کنید.\n\n'
        'یک شناسه چت/کانال عددی بفرستید (مثال: <code>-1001234567890</code>).\n'
        'برای ارسال به همه سوپرادمین ها کلمه <code>clear</code> را بفرستید.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 انصراف', callback_data='backup:auto:home')]]),
        parse_mode='HTML',
    )
    await answer_callback(callback)


@backups_router.message(BackupSettingsState.backup_chat_id)
async def backup_dest_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip()
    if raw.lower() in ('clear', 'پاک', 'حذف'):
        db.set_setting('auto_backup_chat_id', '')
        db.add_log(message.from_user.id, 'auto_backup_dest_changed', 'settings', 'auto_backup_chat_id', {'value': ''})
        await state.clear()
        await message.answer('مقصد بکاپ خودکار به حالت پیشفرض (سوپرادمین ها) برگشت.')
        return
    value = raw.lstrip()
    if not value.lstrip('-').isdigit():
        await message.answer('شناسه چت باید عددی باشد. مثال: -1001234567890')
        return
    db.set_setting('auto_backup_chat_id', value)
    db.add_log(message.from_user.id, 'auto_backup_dest_changed', 'settings', 'auto_backup_chat_id', {'value': value})
    await state.clear()
    await message.answer(f'مقصد بکاپ خودکار ذخیره شد: <code>{html.escape(value)}</code>', parse_mode='HTML')


@backups_router.callback_query(F.data == 'backup:auto:run_now')
async def backup_run_now(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        from permissions import deny_callback
        await deny_callback(callback)
        return
    from modules.tasks import run_auto_backup_job
    await answer_callback(callback, 'در حال اجرای بکاپ خودکار...')
    try:
        ok = await run_auto_backup_job(callback.bot)
        if ok:
            await callback.message.answer('✅ بکاپ خودکار با موفقیت اجرا و ارسال شد.')
        else:
            await callback.message.answer('❌ اجرای بکاپ خودکار ناموفق بود. جزئیات در بخش خطاهای سیستم ثبت شد.')
        await backup_home_cb(callback)
    except Exception as exc:
        db.record_system_error('backups.run_now', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        await answer_callback(callback, 'خطا در اجرای بکاپ.', show_alert=True)
