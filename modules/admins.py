from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from utils import edit_or_answer, person_display, get_button_style
from config import settings
from keyboards import EditableButtonFilter
from keyboards import BTN_ADMINS, back_keyboard, cancel_keyboard
from permissions import ROLE_LABELS, deny_callback, deny_message, is_super_admin

admins_router = Router()


class AdminStates(StatesGroup):
    add = State()


def admins_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📋 لیست ادمین‌ها', callback_data='admins:list')],
        [InlineKeyboardButton(text='➕ افزودن ادمین/فروشنده', callback_data='admins:add')],
    ])


def role_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='ادمین', callback_data=f'admins:role:{telegram_id}:admin')],
        [InlineKeyboardButton(text='فروشنده', callback_data=f'admins:role:{telegram_id}:seller')],
        [InlineKeyboardButton(text='فعال/غیرفعال', callback_data=f'admins:toggle:{telegram_id}')],
        [InlineKeyboardButton(text='🗑 حذف از لیست ادمین‌ها', callback_data=f'admins:delete:{telegram_id}', style=get_button_style('danger'))],
        [InlineKeyboardButton(text='🔙 برگشت به لیست', callback_data='admins:list')],
    ])


def admin_detail_text(row: dict) -> str:
    return (
        '🛡 جزئیات دسترسی\n\n'
        f"شخص: {person_display(row['telegram_id'], include_username=True)}\n"
        f"نقش: {ROLE_LABELS.get(row['role'], row['role'])}\n"
        f"نام نمایشی: {row.get('display_name') or '-'}\n"
        f"وضعیت: {'فعال' if row['is_active'] else 'غیرفعال'}\n"
        f"ساخته شده در: {row.get('created_at') or '-'}"
    )


@admins_router.message(EditableButtonFilter(BTN_ADMINS))
async def admins_home(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await state.clear()
    await message.answer('🛡 مدیریت ادمین‌ها', reply_markup=back_keyboard())
    await message.answer('یک گزینه را انتخاب کنید:', reply_markup=admins_keyboard())


@admins_router.callback_query(F.data == 'admins:list')
async def admins_list(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    rows = db.fetchall('SELECT * FROM bot_admins ORDER BY role, telegram_id')
    if not rows:
        await edit_or_answer(callback, 'هیچ ادمینی ثبت نشده است.', reply_markup=admins_keyboard())
        await callback.answer()
        return
    kb = []
    for row in rows:
        active = '✅' if row['is_active'] else '🚫'
        role = ROLE_LABELS.get(row['role'], row['role'])
        name = person_display(row['telegram_id'])
        kb.append([InlineKeyboardButton(text=f'{active} {name} | {role}', callback_data=f"admins:view:{row['telegram_id']}")])
    kb.append([InlineKeyboardButton(text='➕ افزودن ادمین/فروشنده', callback_data='admins:add')])
    await edit_or_answer(callback, '📋 لیست ادمین‌ها:', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@admins_router.callback_query(F.data.startswith('admins:view:'))
async def admin_view(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    telegram_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT * FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    if not row:
        await callback.answer('ادمین پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=role_keyboard(telegram_id))
    await callback.answer()


@admins_router.callback_query(F.data == 'admins:add')
async def admins_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(AdminStates.add)
    await edit_or_answer(callback, 
        'فرمت افزودن/ویرایش دسترسی:\n\n'
        'telegram_id role\n\n'
        'role یکی از این دو مقدار است:\n'
        'admin یا seller\n\n'
        'مثال:\n123456789 seller',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='❌ لغو', callback_data='admins:cancel_add')],
        ]),
    )
    await callback.answer()


@admins_router.callback_query(F.data == 'admins:cancel_add')
async def admins_add_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    await edit_or_answer(callback, 'افزودن ادمین لغو شد.', reply_markup=admins_keyboard())
    await callback.answer('لغو شد.')


@admins_router.message(AdminStates.add)
async def admins_add_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    parts = (message.text or '').split()
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in {'admin', 'seller'}:
        await message.answer('فرمت اشتباه است.', reply_markup=cancel_keyboard())
        return
    telegram_id, role = int(parts[0]), parts[1]
    db.execute(
        '''INSERT INTO bot_admins (telegram_id, role, display_name, is_active, created_by, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET role=excluded.role, is_active=1, updated_at=excluded.updated_at''',
        (telegram_id, role, person_display(telegram_id).rsplit(' - ',1)[0], message.from_user.id, db.now_iso(), db.now_iso()),
    )
    try:
        db.invalidate_role_cache(telegram_id)
    except Exception:
        pass
    db.add_log(message.from_user.id, 'add_admin_role', 'admin', str(telegram_id), {'role': role})
    await state.clear()
    await message.answer('ثبت شد.', reply_markup=back_keyboard())


@admins_router.callback_query(F.data.startswith('admins:role:'))
async def admin_change_role(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    _, _, telegram_id_s, role = callback.data.split(':')
    telegram_id = int(telegram_id_s)
    if telegram_id in settings.super_admin_ids:
        await callback.answer('سوپرادمین‌های env قابل تغییر درجه نیستند.', show_alert=True)
        return
    db.execute('UPDATE bot_admins SET role=?, is_active=1, updated_at=? WHERE telegram_id=?', (role, db.now_iso(), telegram_id))
    try:
        db.invalidate_role_cache(telegram_id)
    except Exception:
        pass
    db.add_log(callback.from_user.id, 'admin_role_changed', 'admin', str(telegram_id), {'role': role})
    row = db.fetchone('SELECT * FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=role_keyboard(telegram_id))
    await callback.answer('درجه تغییر کرد.')


@admins_router.callback_query(F.data.startswith('admins:toggle:'))
async def admin_toggle(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    telegram_id = int(callback.data.split(':')[-1])
    if telegram_id in settings.super_admin_ids:
        await callback.answer('سوپرادمین‌های env قابل غیرفعال‌سازی نیستند.', show_alert=True)
        return
    row = db.fetchone('SELECT is_active FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    if not row:
        await callback.answer('ادمین پیدا نشد.', show_alert=True)
        return
    new_value = 0 if row['is_active'] else 1
    db.execute('UPDATE bot_admins SET is_active=?, updated_at=? WHERE telegram_id=?', (new_value, db.now_iso(), telegram_id))
    try:
        db.invalidate_role_cache(telegram_id)
    except Exception:
        pass
    db.add_log(callback.from_user.id, 'admin_status_changed', 'admin', str(telegram_id), {'is_active': new_value})
    row = db.fetchone('SELECT * FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=role_keyboard(telegram_id))
    await callback.answer('وضعیت تغییر کرد.')


@admins_router.callback_query(F.data.startswith('admins:delete:'))
async def admin_delete_confirm(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    telegram_id = int(callback.data.split(':')[-1])
    if telegram_id in settings.super_admin_ids:
        await callback.answer('سوپرادمین‌های env قابل حذف نیستند.', show_alert=True)
        return
    row = db.fetchone('SELECT * FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    if not row:
        await callback.answer('ادمین پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, 
        f'دسترسی این کاربر حذف شود؟\n\n{admin_detail_text(row)}',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ بله، حذف شود', callback_data=f'admins:delete_confirm:{telegram_id}')],
            [InlineKeyboardButton(text='❌ لغو', callback_data=f'admins:view:{telegram_id}')],
        ]),
    )
    await callback.answer()


@admins_router.callback_query(F.data.startswith('admins:delete_confirm:'))
async def admin_delete(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    telegram_id = int(callback.data.split(':')[-1])
    if telegram_id in settings.super_admin_ids:
        await callback.answer('سوپرادمین‌های env قابل حذف نیستند.', show_alert=True)
        return
    db.execute('DELETE FROM bot_admins WHERE telegram_id=?', (telegram_id,))
    try:
        db.invalidate_role_cache(telegram_id)
    except Exception:
        pass
    db.add_log(callback.from_user.id, 'admin_deleted', 'admin', str(telegram_id))
    await callback.answer('حذف شد.')
    rows = db.fetchall('SELECT * FROM bot_admins ORDER BY role, telegram_id')
    kb = []
    for row in rows:
        active = '✅' if row['is_active'] else '🚫'
        role = ROLE_LABELS.get(row['role'], row['role'])
        name = person_display(row['telegram_id'])
        kb.append([InlineKeyboardButton(text=f'{active} {name} | {role}', callback_data=f"admins:view:{row['telegram_id']}")])
    kb.append([InlineKeyboardButton(text='➕ افزودن ادمین/فروشنده', callback_data='admins:add')])
    await edit_or_answer(callback, '📋 لیست ادمین‌ها:', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
