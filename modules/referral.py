from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import BTN_REFERRAL, CustomerButtonFilter, back_keyboard
from permissions import deny_callback, deny_message, is_admin, is_staff
from utils import edit_or_answer, answer_callback

referral_router = Router()


class ReferralAdminState(StatesGroup):
    reward = State()


def copy_code(value) -> str:
    return '`' + str(value).replace('`', '') + '`'


def format_toman(amount: int | None) -> str:
    return f'{int(amount or 0):,} تومان'


def referral_admin_keyboard() -> InlineKeyboardMarkup:
    condition = db.get_setting('referral_condition', 'payment')
    enabled = bool(db.get_setting('referral_enabled', True))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"وضعیت دعوت: {'روشن' if enabled else 'خاموش'}", callback_data='ref_admin:toggle')],
        [InlineKeyboardButton(text='💰 تنظیم پاداش دعوت', callback_data='ref_admin:reward')],
        [InlineKeyboardButton(text=f"شرط پاداش: {'بعد از پرداخت' if condition == 'payment' else 'فقط ورود با لینک'}", callback_data='ref_admin:condition')],
        [InlineKeyboardButton(text='📊 آمار دعوت ها', callback_data='ref_admin:stats')],
    ])


async def referral_admin_home_message(message: Message) -> None:
    reward = int(db.get_setting('referral_reward_toman', 0) or 0)
    condition = db.get_setting('referral_condition', 'payment')
    enabled = bool(db.get_setting('referral_enabled', True))
    await message.answer('👥 تنظیمات دعوت دوستان', reply_markup=back_keyboard())
    await message.answer(
        f"وضعیت: {'روشن' if enabled else 'خاموش'}\n"
        f'پاداش هر دعوت: {format_toman(reward)}\n'
        f"شرط پاداش: {'بعد از پرداخت دعوت شده' if condition == 'payment' else 'صرفا ورود با لینک دعوت'}",
        reply_markup=referral_admin_keyboard(),
    )


@referral_router.message(CustomerButtonFilter('referral'))
async def referral_menu(message: Message, bot):
    user_id = message.from_user.id if message.from_user else None
    if is_staff(user_id):
        await referral_admin_home_message(message)
        return
    if not bool(db.get_setting('referral_enabled', True)):
        await message.answer('دعوت دوستان فعلا غیرفعال است.')
        return
    stats = db.referral_stats(user_id)
    me = await bot.get_me()
    reward = int(db.get_setting('referral_reward_toman', 0) or 0)
    condition = db.get_setting('referral_condition', 'payment')
    condition_text = 'بعد از پرداخت دعوت شده' if condition == 'payment' else 'بعد از ورود با لینک'
    link = f'https://t.me/{me.username}?start=ref_{user_id}'
    await message.answer(
        '👥 دعوت دوستان\n\n'
        f'لینک دعوت شما:\n{copy_code(link)}\n\n'
        f'پاداش هر دعوت: {format_toman(reward)}\n'
        f'شرط دریافت پاداش: {condition_text}\n\n'
        f'کل دعوت ها: {copy_code(stats["total"])}\n'
        f'پاداش داده شده: {copy_code(stats["rewarded"])}\n'
        f'در انتظار: {copy_code(stats["pending"])}',
        parse_mode='Markdown',
    )


@referral_router.callback_query(F.data == 'ref_admin:toggle')
async def referral_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = bool(db.get_setting('referral_enabled', True))
    db.set_setting('referral_enabled', not current)
    await edit_or_answer(callback, f"وضعیت دعوت دوستان {'روشن' if not current else 'خاموش'} شد.", reply_markup=referral_admin_keyboard())
    await answer_callback(callback)


@referral_router.callback_query(F.data == 'ref_admin:condition')
async def referral_condition(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = db.get_setting('referral_condition', 'payment')
    new = 'join' if current == 'payment' else 'payment'
    db.set_setting('referral_condition', new)
    await edit_or_answer(callback, f"شرط پاداش تغییر کرد: {'صرفا ورود با لینک' if new == 'join' else 'بعد از پرداخت دعوت شده'}", reply_markup=referral_admin_keyboard())
    await answer_callback(callback)


@referral_router.callback_query(F.data == 'ref_admin:reward')
async def referral_reward_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(ReferralAdminState.reward)
    await edit_or_answer(callback, 'مبلغ پاداش هر دعوت را به تومان وارد کنید. مثال: 20000')
    await answer_callback(callback)


@referral_router.message(ReferralAdminState.reward)
async def referral_reward_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').replace(',', '').replace('٬', '').strip()
    if not raw.isdigit() or int(raw) < 0:
        await message.answer('لطفا عدد معتبر وارد کنید.')
        return
    db.set_setting('referral_reward_toman', int(raw))
    await state.clear()
    await message.answer(f'پاداش دعوت ثبت شد: {format_toman(int(raw))}', reply_markup=back_keyboard())
    await referral_admin_home_message(message)


@referral_router.callback_query(F.data == 'ref_admin:stats')
async def referral_stats_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    rows = db.fetchall('SELECT inviter_id, COUNT(*) AS total, SUM(reward_paid) AS rewarded FROM referrals GROUP BY inviter_id ORDER BY total DESC LIMIT 30')
    text = '📊 آمار دعوت ها\n\n'
    if not rows:
        text += 'هنوز دعوتی ثبت نشده است.'
    for row in rows:
        text += f"کاربر {copy_code(row['inviter_id'])} | کل: {copy_code(row['total'])} | پاداش گرفته: {copy_code(row['rewarded'] or 0)}\n"
    await edit_or_answer(callback, text, reply_markup=referral_admin_keyboard(), parse_mode='Markdown')
    await answer_callback(callback)
