from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from utils import edit_or_answer, person_display
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_ORDERS
from permissions import deny_callback, deny_message, get_role, is_staff

orders_router = Router()


def orders_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🕒 در انتظار پرداخت', callback_data='orders:list:pending:0')],
        [InlineKeyboardButton(text='✅ موفق', callback_data='orders:list:paid:0')],
        [InlineKeyboardButton(text='❌ ناموفق/لغو', callback_data='orders:list:failed:0')],
        [InlineKeyboardButton(text='📋 همه سفارش ها', callback_data='orders:list:all:0')],
    ])


@orders_router.message(EditableButtonFilter(BTN_ORDERS))
async def orders_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        await deny_message(message)
        return
    await message.answer('🧾 مدیریت سفارش ها', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه‌ها را انتخاب کنید:', reply_markup=orders_menu())


@orders_router.callback_query(F.data.startswith('orders:list:'))
async def orders_list(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    _, _, status, offset_s = callback.data.split(':')
    offset = int(offset_s)
    role = get_role(uid)
    params = []
    where = []
    if status != 'all':
        if status == 'failed':
            where.append("status IN ('failed','cancelled')")
        elif status == 'paid':
            where.append("status IN ('paid','build_queued','building_service','service_ready')")
        else:
            where.append('status=?')
            params.append(status)
    if role == 'seller':
        where.append('seller_id=?')
        params.append(uid)
    where_sql = 'WHERE ' + ' AND '.join(where) if where else ''
    rows = db.fetchall(f'SELECT * FROM orders {where_sql} ORDER BY created_at DESC LIMIT 10 OFFSET ?', tuple(params + [offset]))
    total = db.fetchone(f'SELECT COUNT(*) AS c FROM orders {where_sql}', tuple(params)) or {'c': 0}
    if not rows:
        await edit_or_answer(callback, 'سفارشی پیدا نشد.', reply_markup=orders_menu())
        await callback.answer()
        return
    text = f'🧾 سفارش ها | وضعیت: {status}\n\n'
    kb = []
    for row in rows:
        text += f"#{row['id']} | {person_display(row['user_id'])} | {row.get('package_name') or row.get('package_id')} | {row['amount']} {row.get('currency') or 'XTR'} | {row['status']}\n"
        if role != 'seller':
            kb.append([InlineKeyboardButton(text=f"#{row['id']} تغییر وضعیت", callback_data=f"orders:status:{row['id']}")])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'orders:list:{status}:{max(0, offset-10)}'))
    if offset + 10 < int(total['c']):
        nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'orders:list:{status}:{offset+10}'))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='orders:home')])
    await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode='Markdown')
    await callback.answer()


@orders_router.callback_query(F.data == 'orders:home')
async def orders_back(callback: CallbackQuery):
    await edit_or_answer(callback, '🧾 مدیریت سفارش ها', reply_markup=orders_menu())
    await callback.answer()


@orders_router.callback_query(F.data.startswith('orders:status:'))
async def order_status_menu(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid) or get_role(uid) == 'seller':
        await deny_callback(callback)
        return
    order_id = int(callback.data.split(':')[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ paid', callback_data=f'orders:set:{order_id}:paid')],
        [InlineKeyboardButton(text='🕒 pending', callback_data=f'orders:set:{order_id}:pending')],
        [InlineKeyboardButton(text='❌ failed', callback_data=f'orders:set:{order_id}:failed')],
        [InlineKeyboardButton(text='🚫 cancelled', callback_data=f'orders:set:{order_id}:cancelled')],
    ])
    await edit_or_answer(callback, f'وضعیت جدید سفارش #{order_id} را انتخاب کنید:', reply_markup=kb)
    await callback.answer()


@orders_router.callback_query(F.data.startswith('orders:set:'))
async def order_status_set(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid) or get_role(uid) == 'seller':
        await deny_callback(callback)
        return
    _, _, order_id_s, status = callback.data.split(':')
    paid_at = db.now_iso() if status == 'paid' else None
    db.execute('UPDATE orders SET status=?, paid_at=COALESCE(?, paid_at) WHERE id=?', (status, paid_at, int(order_id_s)))
    db.add_log(uid, 'order_status_changed', 'order', order_id_s, {'status': status})
    db.add_notification('order', f'وضعیت سفارش #{order_id_s} به {status} تغییر کرد')
    await callback.answer('ثبت شد.', show_alert=True)
    await edit_or_answer(callback, 'وضعیت سفارش بروزرسانی شد.', reply_markup=orders_menu())
