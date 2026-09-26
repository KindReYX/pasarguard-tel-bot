from __future__ import annotations

import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_AD_TRACKING, back_keyboard
from permissions import deny_callback, deny_message, is_admin

ad_tracking_router = Router()


class AdState(StatesGroup):
    wait_name = State()


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _money(value: int) -> str:
    return f'{int(value or 0):,} تومان'


async def _campaign_link(bot, slug: str) -> str:
    me = await bot.get_me()
    return f'https://example.com/{me.username}?start=ad_{slug}'


def _slug(name: str) -> str:
    base = re.sub(r'[^a-zA-Z0-9]+', '-', name.strip()).strip('-').lower()[:24] or 'ad'
    # db.unique_ad_slug now normalizes; keep local lower for predictability
    return db.unique_ad_slug(base.lower())


def menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='➕ تبلیغ جدید', callback_data='ads:new', style='success')]]
    for row in db.list_ad_campaigns():
        mark = '🟢' if int(row.get('is_active') or 0) else '⚫️'
        rows.append([InlineKeyboardButton(text=f'{mark} {row["name"]}', callback_data=f'ads:view:{row["id"]}')])
    return _kb(rows)


@ad_tracking_router.message(EditableButtonFilter(BTN_AD_TRACKING))
async def ads_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    await message.answer('📣 نظارت بر تبلیغات\n\nبرای هر تبلیغ یک لینک اختصاصی بسازید و ورودی، خریدار و مبلغ فروش آن را ببینید.', reply_markup=back_keyboard())
    await message.answer('تبلیغات تعریف‌شده:', reply_markup=menu_keyboard())


@ad_tracking_router.callback_query(F.data == 'ads:new')
async def ads_new(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(AdState.wait_name)
    await callback.message.answer('نام تبلیغ/کمپین را بفرستید.\nمثال: کانال فلان - مرداد ۱۴۰۵')
    await callback.answer()


@ad_tracking_router.message(AdState.wait_name)
async def ads_new_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None): return
    name = (message.text or '').strip()
    if not name:
        await message.answer('نام معتبر بفرستید.'); return
    cid = db.create_ad_campaign(name, _slug(name), message.from_user.id)
    await state.clear()
    row = db.get_ad_campaign(cid)
    link = await _campaign_link(message.bot, row['slug'])
    await message.answer(f'✅ تبلیغ ساخته شد.\n\nنام: {row["name"]}\nلینک اختصاصی:\n<code>{link}</code>', parse_mode='HTML', reply_markup=menu_keyboard())


@ad_tracking_router.callback_query(F.data.startswith('ads:view:'))
async def ads_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    cid = int(callback.data.rsplit(':',1)[1])
    row = db.get_ad_campaign(cid)
    if not row:
        await callback.answer('تبلیغ پیدا نشد.', show_alert=True); return
    stats = db.ad_campaign_stats(cid)
    link = await _campaign_link(callback.bot, row['slug'])
    # New stats expose both total hits and unique logic (visits vs visits_unique)
    total_hits = stats.get('visits', 0)
    unique_visitors = stats.get('visits_unique', stats.get('users', 0))
    attributed = stats.get('users', 0)
    text = (
        f'📣 {row["name"]}\n\n'
        f'🔗 لینک:\n<code>{link}</code>\n'
        f'🆔 اسلاگ: <code>{row.get("slug","")}</code>\n\n'
        f'👁 ورود کل (هر کلیک): {total_hits:,}\n'
        f'👤 بازدیدکنندگان یکتا: {unique_visitors:,}\n'
        f'🎯 منتسب فعلی (last-touch): {attributed:,}\n'
        f'🛒 خریداران: {stats["buyers"]:,}\n'
        f'🧾 تعداد خرید: {stats["orders"]:,}\n'
        f'💰 فروش: {_money(stats["revenue"])}\n'
        f'📈 تبدیل (خریدار/یکتا): {stats.get("conversion",0):.1f}% | (خریدار/منتسب): {stats.get("conversion_attributed",0):.1f}%\n'
        f'وضعیت: {"فعال ✅" if int(row.get("is_active") or 0) else "غیرفعال ❌"}'
    )
    # Show last visits debug hint if large discrepancy
    rows = [
        [InlineKeyboardButton(text='🔄 بروزرسانی آمار', callback_data=f'ads:view:{cid}', style='primary')],
        [InlineKeyboardButton(text=('⏸ غیرفعال کردن' if int(row.get('is_active') or 0) else '▶️ فعال کردن'), callback_data=f'ads:toggle:{cid}')],
        [InlineKeyboardButton(text='🗑 حذف', callback_data=f'ads:delete:{cid}', style='danger')],
        [InlineKeyboardButton(text='🔙 لیست تبلیغات', callback_data='ads:list')],
    ]
    try:
        await callback.message.edit_text(text, parse_mode='HTML', reply_markup=_kb(rows))
    except Exception:
        await callback.message.answer(text, parse_mode='HTML', reply_markup=_kb(rows))
    await callback.answer()


@ad_tracking_router.callback_query(F.data == 'ads:list')
async def ads_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    try: await callback.message.edit_text('تبلیغات تعریف‌شده:', reply_markup=menu_keyboard())
    except Exception: await callback.message.answer('تبلیغات تعریف‌شده:', reply_markup=menu_keyboard())
    await callback.answer()


@ad_tracking_router.callback_query(F.data.startswith('ads:toggle:'))
async def ads_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    cid = int(callback.data.rsplit(':',1)[1])
    db.toggle_ad_campaign(cid)
    callback.data = f'ads:view:{cid}'
    await ads_view(callback)


@ad_tracking_router.callback_query(F.data.startswith('ads:delete:'))
async def ads_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    cid = int(callback.data.rsplit(':',1)[1])
    db.delete_ad_campaign(cid)
    await callback.answer('حذف شد.')
    try: await callback.message.edit_text('تبلیغات تعریف‌شده:', reply_markup=menu_keyboard())
    except Exception: await callback.message.answer('تبلیغات تعریف‌شده:', reply_markup=menu_keyboard())
