from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import BTN_REFERRAL, CustomerButtonFilter, back_keyboard
from permissions import deny_callback, deny_message, is_admin, is_staff
from utils import edit_or_answer, answer_callback, answer_template, user_template_context

referral_router = Router()



REFERRAL_TEXT_DEFAULT = (
    '👥 دعوت دوستان\n\n'
    'لینک دعوت شما:\n{referral_link}\n\n'
    'پاداش هر دعوت: {reward}\n'
    'شرط دریافت پاداش: {condition}\n\n'
    'کل دعوت ها: {total}\n'
    'پاداش داده شده: {rewarded}\n'
    'در انتظار: {pending}'
)


async def send_referral_page(message: Message, bot) -> None:
    user_id = message.from_user.id if message.from_user else 0
    stats = db.referral_stats(user_id)
    me = await bot.get_me()
    reward = db.referral_reward_amount()
    condition = db.referral_condition()
    condition_text = 'بعد از خرید موفق بسته' if condition == 'purchase' else 'بعد از ورود با لینک'
    link = f'https://t.me/{me.username}?start=ref_{user_id}'
    support = str(db.get_setting('support_username', '') or '')
    context = user_template_context(message.from_user, support=support)
    context.update({
        'ref_link': link,
        'referral_link': link,
        'reward': format_toman(reward),
        'reward_raw': int(reward or 0),
        'condition': condition_text,
        'total': int(stats.get('total') or 0),
        'rewarded': int(stats.get('rewarded') or 0),
        'pending': int(stats.get('pending') or 0),
    })
    template = str(db.get_setting('referral_text', REFERRAL_TEXT_DEFAULT) or REFERRAL_TEXT_DEFAULT)
    await answer_template(message, template, context, content_key='referral_text')

class ReferralAdminState(StatesGroup):
    reward = State()


def copy_code(value) -> str:
    return '`' + str(value).replace('`', '') + '`'


def format_toman(amount: int | None) -> str:
    return f'{int(amount or 0):,} تومان'


def referral_admin_keyboard() -> InlineKeyboardMarkup:
    condition = db.referral_condition()
    enabled = bool(db.get_setting('referral_enabled', True))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"وضعیت دعوت: {'روشن' if enabled else 'خاموش'}", callback_data='ref_admin:toggle')],
        [InlineKeyboardButton(text='💰 تنظیم پاداش دعوت', callback_data='ref_admin:reward')],
        [InlineKeyboardButton(text=f"شرط پاداش: {'بعد از خرید موفق بسته' if condition == 'purchase' else 'فقط ورود با لینک'}", callback_data='ref_admin:condition')],
        [InlineKeyboardButton(text='📊 آمار دعوت ها', callback_data='ref_admin:stats')],
    ])


async def referral_admin_home_message(message: Message) -> None:
    reward = db.referral_reward_amount()
    condition = db.referral_condition()
    enabled = bool(db.get_setting('referral_enabled', True))
    await message.answer('👥 تنظیمات دعوت دوستان', reply_markup=back_keyboard())
    await message.answer(
        f"وضعیت: {'روشن' if enabled else 'خاموش'}\n"
        f'پاداش هر دعوت: {format_toman(reward)}\n'
        f"شرط پاداش: {'بعد از خرید موفق بسته' if condition == 'purchase' else 'صرفا ورود با لینک دعوت'}",
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
    await send_referral_page(message, bot)


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
    current = db.referral_condition()
    new = 'join' if current == 'purchase' else 'purchase'
    db.set_setting('referral_condition', new)
    await edit_or_answer(callback, f"شرط پاداش تغییر کرد: {'صرفا ورود با لینک' if new == 'join' else 'بعد از خرید موفق بسته'}", reply_markup=referral_admin_keyboard())
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
    db.set_setting('referral_reward_amount', int(raw))
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
