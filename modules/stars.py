from __future__ import annotations

from datetime import datetime
from typing import Any

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery

import db
from utils import person_display, edit_or_answer
from config import settings
from keyboards import EditableButtonFilter
from keyboards import BTN_STARS, back_keyboard
from permissions import deny_callback, deny_message, is_admin, is_super_admin

stars_router = Router()


class StarsState(StatesGroup):
    refund = State()


async def telegram_api(method: str, payload: dict[str, Any] | None = None) -> Any:
    url = f'https://api.telegram.org/bot{settings.bot_token}/{method}'
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload or {}) as response:
            data = await response.json(content_type=None)
            if not data.get('ok'):
                raise RuntimeError(data.get('description') or str(data))
            return data.get('result')


def stars_menu(super_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text='⭐️ موجودی Stars ربات', callback_data='stars:balance')],
        [InlineKeyboardButton(text='📜 تراکنش‌های Stars تلگرام', callback_data='stars:transactions:0')],
        [InlineKeyboardButton(text='💳 پرداختی‌های ثبت‌شده داخلی', callback_data='stars:local_payments')],
    ]
    if super_admin:
        rows.append([InlineKeyboardButton(text='↩️ ریفاند پرداخت Stars', callback_data='stars:refund')])
        rows.append([InlineKeyboardButton(text='🏦 راهنمای برداشت Stars', callback_data='stars:withdraw_help')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_star_amount(value: Any) -> str:
    if isinstance(value, dict):
        amount = value.get('amount', 0)
        nano = value.get('nanostar_amount') or 0
        if nano:
            return f'{amount}.{str(nano).zfill(9).rstrip("0")} ⭐️'
        return f'{amount} ⭐️'
    if isinstance(value, (int, float)):
        return f'{value} ⭐️'
    return '0 ⭐️'


@stars_router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    payload = str(query.invoice_payload or '')
    try:
        if query.currency != 'XTR':
            await query.answer(ok=False, error_message='ارز پرداخت نامعتبر است.')
            return
        if payload.startswith('customer_order:'):
            order_id = int(payload.split(':', 1)[1])
            order = db.get_order(order_id)
            if not order or int(order.get('user_id') or 0) != int(query.from_user.id):
                await query.answer(ok=False, error_message='سفارش معتبر نیست.')
                return
            if int(order.get('stars_amount') or 0) != int(query.total_amount or 0):
                await query.answer(ok=False, error_message='مبلغ سفارش تغییر کرده است. دوباره سفارش را باز کنید.')
                return
        elif payload.startswith('wallet_charge:'):
            req_id = int(payload.split(':', 1)[1])
            req = db.get_wallet_charge_request(req_id)
            if not req or int(req.get('user_id') or 0) != int(query.from_user.id):
                await query.answer(ok=False, error_message='درخواست شارژ معتبر نیست.')
                return
            if int(req.get('stars_amount') or 0) != int(query.total_amount or 0):
                await query.answer(ok=False, error_message='مبلغ شارژ تغییر کرده است. دوباره تلاش کنید.')
                return
        await query.answer(ok=True)
    except Exception:
        await query.answer(ok=False, error_message='امکان تایید این پرداخت وجود ندارد. دوباره تلاش کنید.')


@stars_router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payment = message.successful_payment
    if not payment:
        return
    user_id = message.from_user.id if message.from_user else None
    amount = int(payment.total_amount or 0)
    payload = payment.invoice_payload
    if payload and payload.startswith('customer_order:'):
        return
    telegram_charge_id = payment.telegram_payment_charge_id
    provider_charge_id = payment.provider_payment_charge_id

    db.save_star_payment(
        user_id=user_id,
        amount=amount,
        payload=payload,
        telegram_payment_charge_id=telegram_charge_id,
        provider_payment_charge_id=provider_charge_id,
        status='paid',
    )

    db.execute(
        '''INSERT INTO orders (
            user_id, seller_id, package_id, package_name, amount, currency, status,
            created_at, paid_at, telegram_payment_charge_id, provider_payment_charge_id, invoice_payload
        ) VALUES (?, NULL, ?, ?, ?, 'XTR', 'paid', ?, ?, ?, ?, ?)''',
        (user_id, payload, payload, amount, db.now_iso(), db.now_iso(), telegram_charge_id, provider_charge_id, payload),
    )
    db.add_notification('payment', f'پرداخت Stars جدید: user={user_id} amount={amount}')
    await message.answer('پرداخت شما با موفقیت ثبت شد ✅')


@stars_router.message(EditableButtonFilter(BTN_STARS))
async def stars_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        await deny_message(message)
        return
    await message.answer('⭐️ مدیریت پرداخت‌های Telegram Stars', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه‌ها را انتخاب کنید:', reply_markup=stars_menu(is_super_admin(uid)))


@stars_router.callback_query(F.data == 'stars:balance')
async def stars_balance(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await deny_callback(callback)
        return
    try:
        balance = await telegram_api('getMyStarBalance')
        local_sum = db.star_payments_sum()
        await edit_or_answer(callback, 
            '⭐️ موجودی Stars ربات\n\n'
            f'موجودی Bot API: {format_star_amount(balance)}\n'
            f'جمع پرداختی‌های ثبت‌شده داخلی: {local_sum} ⭐️',
            reply_markup=stars_menu(is_super_admin(uid)),
        )
        await callback.answer('موجودی بروزرسانی شد.')
    except Exception as e:
        await callback.answer(f'خطا: {e}', show_alert=True)


@stars_router.callback_query(F.data.startswith('stars:transactions:'))
async def stars_transactions(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await deny_callback(callback)
        return
    offset = int(callback.data.split(':')[-1])
    try:
        result = await telegram_api('getStarTransactions', {'offset': offset, 'limit': 10})
        transactions = result.get('transactions', []) if isinstance(result, dict) else []
        text = '📜 تراکنش‌های Stars تلگرام\n\n'
        if not transactions:
            text += 'تراکنشی از Bot API دریافت نشد.'
        for tx in transactions:
            date = tx.get('date')
            date_text = datetime.fromtimestamp(date).strftime('%Y-%m-%d %H:%M') if date else '-'
            tx_id = tx.get('id', '-')
            amount = format_star_amount(tx.get('amount'))
            source = tx.get('source') or {}
            receiver = tx.get('receiver') or {}
            source_type = source.get('type', '-') if isinstance(source, dict) else '-'
            receiver_type = receiver.get('type', '-') if isinstance(receiver, dict) else '-'
            text += f'• {date_text} | {amount} | id={tx_id}\n  source={source_type} | receiver={receiver_type}\n'
        rows = []
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'stars:transactions:{max(0, offset - 10)}'))
        if len(transactions) == 10:
            nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'stars:transactions:{offset + 10}'))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='stars:home')])
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()
    except Exception as e:
        await callback.answer(f'خطا: {e}', show_alert=True)


@stars_router.callback_query(F.data == 'stars:local_payments')
async def local_payments(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await deny_callback(callback)
        return
    rows = db.fetchall('SELECT * FROM star_payments ORDER BY created_at DESC LIMIT 30')
    text = '💳 پرداختی‌های ثبت‌شده داخلی\n\n'
    if not rows:
        text += 'پرداختی داخل دیتابیس ثبت نشده است.'
    for row in rows:
        charge = row.get('telegram_payment_charge_id') or '-'
        text += f"#{row['id']} | {person_display(row['user_id'])} | {row['amount']}⭐️ | {row['status']} | charge=`{charge}`\n"
    await edit_or_answer(callback, text, reply_markup=stars_menu(is_super_admin(uid)), parse_mode='Markdown')
    await callback.answer()


@stars_router.callback_query(F.data == 'stars:refund')
async def stars_refund_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(StarsState.refund)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو ریفاند', callback_data='stars:refund:cancel')]])
    await edit_or_answer(callback, 
        'برای ریفاند Stars این فرمت را بفرستید:\n\nuser_id telegram_payment_charge_id\n\nمثال:\n123456789 abcdef12345',
        reply_markup=kb,
    )
    await callback.answer()


@stars_router.callback_query(F.data == 'stars:refund:cancel')
async def stars_refund_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_or_answer(callback, 'ریفاند لغو شد.', reply_markup=stars_menu(is_super_admin(callback.from_user.id)))
    await callback.answer('لغو شد.')


@stars_router.message(StarsState.refund)
async def stars_refund_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_super_admin(uid):
        return
    parts = (message.text or '').split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer('فرمت اشتباه است.')
        return
    user_id = int(parts[0])
    charge_id = parts[1]
    try:
        await telegram_api('refundStarPayment', {'user_id': user_id, 'telegram_payment_charge_id': charge_id})
        db.add_star_refund(user_id, charge_id, uid)
        db.add_log(uid, 'star_refund', 'payment', charge_id, {'user_id': user_id})
        await state.clear()
        await message.answer('ریفاند با موفقیت انجام شد.', reply_markup=back_keyboard())
    except Exception as e:
        await message.answer(f'خطا در ریفاند: {e}')


@stars_router.callback_query(F.data == 'stars:withdraw_help')
async def withdraw_help(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, 
        '🏦 برداشت Stars\n\n'
        'کاربر Stars را به خود ربات پرداخت می‌کند و موجودی در بالانس Stars همان ربات دیده می‌شود.\n'
        'در Bot API متد رسمی برای برداشت مستقیم Stars به حساب شخصی وجود ندارد.\n\n'
        'کاری که از داخل ربات می‌شود انجام داد: نمایش موجودی، نمایش تراکنش‌ها و ریفاند پرداخت موفق.\n'
        'برداشت درآمد باید از مسیر رسمی تلگرام/Fragment یا ابزارهای مالک ربات انجام شود.',
        reply_markup=stars_menu(True),
    )
    await callback.answer('راهنمای برداشت نمایش داده شد.')


@stars_router.callback_query(F.data == 'stars:home')
async def stars_back(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, '⭐️ مدیریت پرداخت‌های Telegram Stars', reply_markup=stars_menu(is_super_admin(uid)))
    await callback.answer()
