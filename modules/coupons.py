from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from utils import edit_or_answer
from keyboards import EditableButtonFilter
from keyboards import BTN_COUPONS, back_keyboard
from permissions import deny_callback, deny_message, is_admin

coupons_router = Router()


class CouponState(StatesGroup):
    create = State()


def coupons_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📋 لیست کدها', callback_data='coupon:list')],
        [InlineKeyboardButton(text='➕ ساخت کد تخفیف', callback_data='coupon:create')],
    ])


def coupon_row_keyboard(coupon_id: int, is_active: int) -> InlineKeyboardMarkup:
    toggle_text = '🚫 غیرفعال کردن' if is_active else '✅ فعال کردن'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f'coupon:toggle:{coupon_id}')],
        [InlineKeyboardButton(text='🗑 حذف کد', callback_data=f'coupon:delete:{coupon_id}')],
        [InlineKeyboardButton(text='🔙 برگشت به لیست', callback_data='coupon:list')],
    ])


def coupon_text(row: dict) -> str:
    return (
        '🎟 جزئیات کد تخفیف\n\n'
        f"کد: {row['code']}\n"
        f"درصد: {row['discount_percent']}\n"
        f"مبلغ ثابت: {row['discount_amount']}\n"
        f"تعداد استفاده: {row['used_count']}/{row['max_uses'] or '∞'}\n"
        f"بسته خاص: {row['package_id'] or 'همه'}\n"
        f"انقضا: {row['expires_at'] or 'ندارد'}\n"
        f"فعال: {'بله' if row['is_active'] else 'خیر'}"
    )


def coupon_list_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    kb = []
    for row in rows:
        status = '✅' if row['is_active'] else '🚫'
        label = f"{status} {row['code']} | %{row['discount_percent']} | {row['used_count']}/{row['max_uses'] or '∞'}"
        kb.append([InlineKeyboardButton(text=label, callback_data=f"coupon:view:{row['id']}")])
    kb.append([InlineKeyboardButton(text='➕ ساخت کد تخفیف', callback_data='coupon:create')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@coupons_router.message(EditableButtonFilter(BTN_COUPONS))
async def coupons_home(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await state.clear()
    await message.answer('🎟 مدیریت کد تخفیف', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه‌ها را انتخاب کنید:', reply_markup=coupons_menu())


@coupons_router.callback_query(F.data == 'coupon:list')
async def coupon_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    rows = db.fetchall('SELECT * FROM coupons ORDER BY created_at DESC LIMIT 50')
    if not rows:
        await edit_or_answer(callback, '🎟 کدهای تخفیف\n\nکدی ثبت نشده است.', reply_markup=coupons_menu())
        await callback.answer()
        return
    await edit_or_answer(callback, 
        '🎟 کدهای تخفیف\n\nبرای مدیریت هر کد، روی دکمه آن بزنید:',
        reply_markup=coupon_list_keyboard(rows),
    )
    await callback.answer()


@coupons_router.callback_query(F.data.startswith('coupon:view:'))
async def coupon_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    coupon_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT * FROM coupons WHERE id=?', (coupon_id,))
    if not row:
        await callback.answer('کد پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, coupon_text(row), reply_markup=coupon_row_keyboard(coupon_id, int(row['is_active'])))
    await callback.answer()


@coupons_router.callback_query(F.data == 'coupon:create')
async def coupon_create_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(CouponState.create)
    await edit_or_answer(callback, 
        'کد تخفیف را با این فرمت بفرستید:\n'
        'CODE percent amount max_uses package_id\n\n'
        'مثال: OFF20 20 0 100 0\n'
        'برای بدون محدودیت package_id را 0 بزنید.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='❌ لغو ساخت کد', callback_data='coupon:create:cancel')],
        ]),
    )
    await callback.answer()


@coupons_router.callback_query(F.data == 'coupon:create:cancel')
async def coupon_create_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    await edit_or_answer(callback, 'ساخت کد تخفیف لغو شد.', reply_markup=coupons_menu())
    await callback.answer('لغو شد.')


@coupons_router.message(CouponState.create)
async def coupon_create_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        return
    parts = (message.text or '').split()
    if len(parts) != 5:
        await message.answer('فرمت اشتباه است. برای لغو از دکمه داخل پیام قبلی استفاده کنید.')
        return
    code, percent, amount, max_uses, package_id = parts
    try:
        db.execute(
            'INSERT INTO coupons (code, discount_percent, discount_amount, max_uses, package_id, is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)',
            (code.upper(), int(percent), int(amount), int(max_uses), None if package_id == '0' else package_id, db.now_iso()),
        )
        db.add_log(uid, 'coupon_created', 'coupon', code.upper())
        await state.clear()
        await message.answer('کد تخفیف ساخته شد.', reply_markup=back_keyboard())
    except Exception as e:
        await message.answer(f'خطا در ساخت کد: {e}')


@coupons_router.callback_query(F.data.startswith('coupon:toggle:'))
async def coupon_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    coupon_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT * FROM coupons WHERE id=?', (coupon_id,))
    if not row:
        await callback.answer('کد پیدا نشد.', show_alert=True)
        return
    new_value = 0 if row['is_active'] else 1
    db.execute('UPDATE coupons SET is_active=? WHERE id=?', (new_value, coupon_id))
    db.add_log(callback.from_user.id, 'coupon_status_changed', 'coupon', str(coupon_id), {'is_active': new_value})
    row = db.fetchone('SELECT * FROM coupons WHERE id=?', (coupon_id,))
    await edit_or_answer(callback, coupon_text(row), reply_markup=coupon_row_keyboard(coupon_id, int(row['is_active'])))
    await callback.answer('فعال شد.' if new_value else 'غیرفعال شد.')


@coupons_router.callback_query(F.data.startswith('coupon:delete:'))
async def coupon_delete_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    coupon_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT code FROM coupons WHERE id=?', (coupon_id,))
    if not row:
        await callback.answer('کد پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, 
        f"کد {row['code']} حذف شود؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ بله، حذف شود', callback_data=f'coupon:delete_confirm:{coupon_id}')],
            [InlineKeyboardButton(text='❌ لغو', callback_data=f'coupon:view:{coupon_id}')],
        ]),
    )
    await callback.answer()


@coupons_router.callback_query(F.data.startswith('coupon:delete_confirm:'))
async def coupon_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    coupon_id = int(callback.data.split(':')[-1])
    db.execute('DELETE FROM coupons WHERE id=?', (coupon_id,))
    db.add_log(callback.from_user.id, 'coupon_deleted', 'coupon', str(coupon_id))

    rows = db.fetchall('SELECT * FROM coupons ORDER BY created_at DESC LIMIT 50')
    if not rows:
        await edit_or_answer(callback, '🎟 کدی ثبت نشده است.', reply_markup=coupons_menu())
        await callback.answer('کد حذف شد.')
        return
    await edit_or_answer(callback, 
        '🎟 کدهای تخفیف\n\nبرای مدیریت هر کد، روی دکمه آن بزنید:',
        reply_markup=coupon_list_keyboard(rows),
    )
    await callback.answer('کد حذف شد.')
