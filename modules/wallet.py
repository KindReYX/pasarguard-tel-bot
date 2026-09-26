from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_WALLET
from permissions import deny_callback, deny_message, is_admin
from utils import fmt_money, person_display

wallet_router = Router()


class WalletState(StatesGroup):
    charge = State()


def wallet_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ شارژ/کسر دستی', callback_data='wallet:charge')],
        [InlineKeyboardButton(text='🔍 موجودی کاربر', callback_data='wallet:balance')],
    ])


@wallet_router.message(EditableButtonFilter(BTN_WALLET))
async def wallet_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer('💰 مدیریت کیف پول', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه‌ها را انتخاب کنید:', reply_markup=wallet_menu())


@wallet_router.callback_query(F.data.in_({'wallet:charge', 'wallet:balance'}))
async def wallet_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(WalletState.charge)
    await state.update_data(mode=callback.data.split(':')[1])
    if callback.data.endswith('balance'):
        await callback.message.answer('آیدی عددی کاربر را بفرستید.')
    else:
        await callback.message.answer('برای شارژ یا کسر، این فرمت را بفرستید:\nuser_id amount reason\nمثال: 123456 100 هدیه\nبرای کسر مبلغ، amount منفی باشد.')
    await callback.answer()


@wallet_router.message(WalletState.charge)
async def wallet_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        return
    data = await state.get_data()
    mode = data.get('mode')
    text = message.text or ''
    if mode == 'balance':
        if not text.strip().isdigit():
            await message.answer('آیدی باید عددی باشد.')
            return
        user_id = int(text.strip())
        await message.answer(f'موجودی {person_display(user_id, include_username=True)}: {fmt_money(db.wallet_balance(user_id))}')
        await state.clear()
        return
    parts = text.split(maxsplit=2)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer('فرمت اشتباه است.')
        return
    user_id = int(parts[0])
    amount = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ''
    db.execute(
        'INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)',
        (user_id, uid, amount, reason, db.now_iso()),
    )
    db.add_log(uid, 'wallet_manual_transaction', 'user', str(user_id), {'amount': amount, 'reason': reason})
    db.add_notification('wallet', f'تراکنش کیف پول برای {person_display(user_id)}: {amount}')
    await message.answer(f'ثبت شد برای {person_display(user_id, include_username=True)}. موجودی جدید: {fmt_money(db.wallet_balance(user_id))}')
    await state.clear()
