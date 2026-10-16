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
    'درآمد از خرید موفق دوستان: {purchase_percent}%\n'
    'درآمد از شارژ کیف پول دوستان: {wallet_percent}%\n\n'
    'کل دعوت ها: {total}\n'
    'دعوت‌های دارای پاداش: {rewarded}\n'
    'درآمد کل شما: {earned}'
)


async def send_referral_page(message: Message, bot) -> None:
    user_id = message.from_user.id if message.from_user else 0
    stats = db.referral_stats(user_id)
    me = await bot.get_me()
    purchase_percent = db.referral_purchase_percent()
    wallet_percent = db.referral_wallet_percent()
    link = f'https://t.me/{me.username}?start=ref_{user_id}'
    support = str(db.get_setting('support_username', '') or '')
    context = user_template_context(message.from_user, support=support)
    context.update({
        'ref_link': link,
        'referral_link': link,
        'reward': f'{purchase_percent:g}% خرید + {wallet_percent:g}% شارژ',
        'reward_raw': f'{purchase_percent:g}% + {wallet_percent:g}%',
        'condition': 'هر خرید و شارژ موفق کیف پول دوست دعوت‌شده',
        'purchase_percent': purchase_percent,
        'wallet_percent': wallet_percent,
        'total': int(stats.get('total') or 0),
        'rewarded': int(stats.get('rewarded') or 0),
        'pending': int(stats.get('pending') or 0),
        'earned': format_toman(int(stats.get('total_earned') or 0)),
        'earned_raw': int(stats.get('total_earned') or 0),
    })
    template = str(db.get_setting('referral_text', REFERRAL_TEXT_DEFAULT) or REFERRAL_TEXT_DEFAULT)
    await answer_template(message, template, context, content_key='referral_text')

class ReferralAdminState(StatesGroup):
    purchase_percent = State()
    wallet_percent = State()


def copy_code(value) -> str:
    return '`' + str(value).replace('`', '') + '`'


def format_toman(amount: int | None) -> str:
    return f'{int(amount or 0):,} تومان'


def referral_admin_keyboard() -> InlineKeyboardMarkup:
    enabled = bool(db.get_setting('referral_enabled', True))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"وضعیت دعوت: {'روشن' if enabled else 'خاموش'}", callback_data='ref_admin:toggle')],
        [InlineKeyboardButton(text=f"🛒 درصد خرید: {db.referral_purchase_percent():g}%", callback_data='ref_admin:purchase_percent')],
        [InlineKeyboardButton(text=f"💳 درصد شارژ کیف پول: {db.referral_wallet_percent():g}%", callback_data='ref_admin:wallet_percent')],
        [InlineKeyboardButton(text='📊 آمار دعوت ها', callback_data='ref_admin:stats')],
    ])


async def referral_admin_home_message(message: Message) -> None:
    enabled = bool(db.get_setting('referral_enabled', True))
    await message.answer('👥 تنظیمات دعوت دوستان', reply_markup=back_keyboard())
    await message.answer(
        f"وضعیت: {'روشن' if enabled else 'خاموش'}\n"
        f'پاداش از هر خرید موفق: {db.referral_purchase_percent():g}%\n'
        f'پاداش از هر شارژ کیف پول: {db.referral_wallet_percent():g}%\n'
        'پاداش به کیف پول دعوت‌کننده واریز می‌شود.',
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


@referral_router.callback_query(F.data.in_({'ref_admin:purchase_percent', 'ref_admin:wallet_percent', 'ref_admin:reward'}))
async def referral_percent_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    setting = callback.data.rsplit(':', 1)[-1]
    if setting == 'reward':  # Old inline keyboards remain usable after upgrade.
        setting = 'purchase_percent'
    await state.set_state(getattr(ReferralAdminState, setting))
    label = 'خرید موفق' if setting == 'purchase_percent' else 'شارژ کیف پول'
    await edit_or_answer(callback, f'درصد پاداش از هر {label} را بفرستید (۰ تا ۱۰۰). برای مثال: 5 یا 2.5')
    await answer_callback(callback)


@referral_router.message(ReferralAdminState.purchase_percent)
async def referral_purchase_percent_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    value = _parse_referral_percent(message.text)
    if value is None:
        await message.answer('درصد معتبر بین ۰ تا ۱۰۰ وارد کنید. مثال: 5 یا 2.5')
        return
    db.set_setting('referral_purchase_percent', value)
    await state.clear()
    await message.answer(f'درصد پاداش خرید ثبت شد: {value:g}%', reply_markup=back_keyboard())
    await referral_admin_home_message(message)


@referral_router.message(ReferralAdminState.wallet_percent)
async def referral_wallet_percent_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    value = _parse_referral_percent(message.text)
    if value is None:
        await message.answer('درصد معتبر بین ۰ تا ۱۰۰ وارد کنید. مثال: 5 یا 2.5')
        return
    db.set_setting('referral_wallet_percent', value)
    await state.clear()
    await message.answer(f'درصد پاداش شارژ کیف پول ثبت شد: {value:g}%', reply_markup=back_keyboard())
    await referral_admin_home_message(message)


def _parse_referral_percent(raw: str | None) -> float | None:
    try:
        value = float(str(raw or '').replace(',', '.').replace('٬', '').strip())
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


@referral_router.callback_query(F.data == 'ref_admin:condition')
async def referral_legacy_condition(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await callback.answer('تنظیم ثابت قبلی حذف شده؛ درصد خرید و شارژ را از منوی دعوت تعیین کنید.', show_alert=True)


async def notify_pending_referral_commissions(bot, limit: int = 20) -> int:
    """Deliver durable commission notifications; wallet credit is already committed."""
    delivered = 0
    for commission in db.pending_referral_commissions(limit):
        event_type = str(commission.get('event_type') or '')
        if event_type == 'wallet_charge':
            source_label = 'شارژ کیف پول دوست دعوت‌شده'
        elif event_type == 'purchase':
            source_label = 'خرید موفق دوست دعوت‌شده'
        else:
            source_label = 'تراکنش دوست دعوت‌شده'
        reward = int(commission.get('reward_amount') or 0)
        text = (
            '🎉 پاداش دعوت به کیف پول شما واریز شد.\n\n'
            f'{source_label}: {format_toman(int(commission.get("base_amount") or 0))}\n'
            f'درصد پاداش: {float(commission.get("percent") or 0):g}%\n'
            f'مبلغ واریزی به کیف پول شما: +{format_toman(reward)}'
        )
        try:
            await bot.send_message(int(commission['inviter_id']), text)
        except Exception as exc:
            db.mark_referral_commission_notification_failed(int(commission['id']), type(exc).__name__)
            db.record_system_error(
                'referral.notify_commission', str(exc), user_id=int(commission['inviter_id']),
                entity_type='referral_commission', entity_id=str(commission['id']),
                error_type=type(exc).__name__,
            )
            continue
        db.mark_referral_commission_notified(int(commission['id']))
        delivered += 1
    return delivered


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
