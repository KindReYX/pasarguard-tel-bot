from __future__ import annotations

import re

import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

import db
from utils import edit_or_answer, message_text_with_custom_emoji_tokens, render_custom_emoji_output
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_BROADCAST
from permissions import is_admin, deny_message, deny_callback

broadcast_router = Router()


def convert_html_code_to_markdown(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r'<code>(.*?)</code>', r'`\1`', text, flags=re.DOTALL)

FILTERS = {
    'all': 'همه کاربران',
    'active': 'کاربران فعال',
    'blocked': 'کاربران مسدود',
    'today': 'عضویت امروز',
    'week': 'عضویت هفته اخیر',
    'buyers': 'دارای سفارش موفق',
    'no_buyers': 'بدون سفارش موفق',
    'sellers_customers': 'مشتریان فروشندگان',
    'expired_services': 'سرویس منقضی، بدون سرویس فعال',
    'expired_test_only': 'فقط سرویس تست منقضی',
}


class BroadcastStates(StatesGroup):
    text = State()
    confirm = State()


def filter_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f'bcast:filter:{key}')] for key, label in FILTERS.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def users_by_filter(filter_key: str, *, limit: int | None = None, offset: int = 0) -> list[int]:
    # Use LIMIT/OFFSET for streaming to avoid OOM on large tables; default fetches all for backward compat but internal broadcast_send streams
    base = None
    params: tuple = ()
    if filter_key == 'active':
        base = 'SELECT telegram_id FROM bot_users WHERE is_active=1 AND is_blocked=0'
    elif filter_key == 'blocked':
        base = 'SELECT telegram_id FROM bot_users WHERE is_blocked=1'
    elif filter_key == 'today':
        base = "SELECT telegram_id FROM bot_users WHERE created_at >= datetime('now','-1 day')"
    elif filter_key == 'week':
        base = "SELECT telegram_id FROM bot_users WHERE created_at >= datetime('now','-7 day')"
    elif filter_key == 'buyers':
        base = "SELECT DISTINCT user_id AS telegram_id FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready')"
    elif filter_key == 'no_buyers':
        base = "SELECT telegram_id FROM bot_users WHERE telegram_id NOT IN (SELECT DISTINCT user_id FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready'))"
    elif filter_key == 'sellers_customers':
        base = 'SELECT telegram_id FROM bot_users WHERE owner_admin_id IS NOT NULL'
    elif filter_key == 'expired_services':
        # At least one expired service, but absolutely no currently unexpired service.
        # This intentionally excludes e.g. a user with 3 services where 1 is expired
        # and 2 are still OK. Both Unix timestamps and ISO-8601 expiries are supported.
        exp_s = "(CASE WHEN CAST(s.expire AS INTEGER) > 1000000000 THEN CAST(s.expire AS INTEGER) ELSE CAST(strftime('%s', s.expire) AS INTEGER) END)"
        exp_a = "(CASE WHEN CAST(a.expire AS INTEGER) > 1000000000 THEN CAST(a.expire AS INTEGER) ELSE CAST(strftime('%s', a.expire) AS INTEGER) END)"
        base = f'''SELECT DISTINCT s.telegram_id
                   FROM user_services s
                   JOIN bot_users b ON b.telegram_id=s.telegram_id
                   WHERE s.is_active=1 AND b.is_active=1 AND b.is_blocked=0
                     AND {exp_s} > 0 AND {exp_s} <= CAST(strftime('%s','now') AS INTEGER)
                     AND NOT EXISTS (
                         SELECT 1 FROM user_services a
                         WHERE a.telegram_id=s.telegram_id AND a.is_active=1
                           AND {exp_a} > CAST(strftime('%s','now') AS INTEGER)
                     )'''
    elif filter_key == 'expired_test_only':
        # Users who have only ever received test services, at least one of those tests
        # is expired, and they have no unexpired service at all. A paid/normal service
        # (or a successful order) disqualifies the user from this audience.
        exp_t = "(CASE WHEN CAST(t.expire AS INTEGER) > 1000000000 THEN CAST(t.expire AS INTEGER) ELSE CAST(strftime('%s', t.expire) AS INTEGER) END)"
        exp_a = "(CASE WHEN CAST(a.expire AS INTEGER) > 1000000000 THEN CAST(a.expire AS INTEGER) ELSE CAST(strftime('%s', a.expire) AS INTEGER) END)"
        base = f'''SELECT DISTINCT t.telegram_id
                   FROM user_services t
                   JOIN bot_users b ON b.telegram_id=t.telegram_id
                   WHERE t.is_active=1 AND b.is_active=1 AND b.is_blocked=0
                     AND t.order_id IS NULL AND t.package_name='سرویس تست'
                     AND {exp_t} > 0 AND {exp_t} <= CAST(strftime('%s','now') AS INTEGER)
                     AND NOT EXISTS (
                         SELECT 1 FROM user_services a
                         WHERE a.telegram_id=t.telegram_id AND a.is_active=1
                           AND {exp_a} > CAST(strftime('%s','now') AS INTEGER)
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM user_services n
                         WHERE n.telegram_id=t.telegram_id
                           AND NOT (n.order_id IS NULL AND n.package_name='سرویس تست')
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM orders o
                         WHERE o.user_id=t.telegram_id
                           AND o.status IN ('paid','build_queued','building_service','service_ready')
                     )'''
    else:
        base = 'SELECT telegram_id FROM bot_users WHERE is_active=1'
    if limit is not None:
        base += ' LIMIT ? OFFSET ?'
        params = (int(limit), int(offset))
    rows = db.fetchall(base, params)
    return [int(r['telegram_id']) for r in rows]


def users_count_by_filter(filter_key: str) -> int:
    # Fast COUNT without fetching IDs for preview
    if filter_key == 'active':
        return db.user_count('WHERE is_active=1 AND is_blocked=0')
    if filter_key == 'blocked':
        return db.user_count('WHERE is_blocked=1')
    if filter_key == 'today':
        row = db.fetchone("SELECT COUNT(*) AS c FROM bot_users WHERE created_at >= datetime('now','-1 day')")
        return int(row['c'] if row else 0)
    if filter_key == 'week':
        row = db.fetchone("SELECT COUNT(*) AS c FROM bot_users WHERE created_at >= datetime('now','-7 day')")
        return int(row['c'] if row else 0)
    if filter_key == 'buyers':
        row = db.fetchone("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready')")
        return int(row['c'] if row else 0)
    if filter_key == 'no_buyers':
        row = db.fetchone("SELECT COUNT(*) AS c FROM bot_users WHERE telegram_id NOT IN (SELECT DISTINCT user_id FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready'))")
        return int(row['c'] if row else 0)
    if filter_key == 'sellers_customers':
        row = db.fetchone('SELECT COUNT(*) AS c FROM bot_users WHERE owner_admin_id IS NOT NULL')
        return int(row['c'] if row else 0)
    if filter_key == 'expired_services':
        exp_s = "(CASE WHEN CAST(s.expire AS INTEGER) > 1000000000 THEN CAST(s.expire AS INTEGER) ELSE CAST(strftime('%s', s.expire) AS INTEGER) END)"
        exp_a = "(CASE WHEN CAST(a.expire AS INTEGER) > 1000000000 THEN CAST(a.expire AS INTEGER) ELSE CAST(strftime('%s', a.expire) AS INTEGER) END)"
        row = db.fetchone(f'''SELECT COUNT(DISTINCT s.telegram_id) AS c
            FROM user_services s JOIN bot_users b ON b.telegram_id=s.telegram_id
            WHERE s.is_active=1 AND b.is_active=1 AND b.is_blocked=0
              AND {exp_s} > 0 AND {exp_s} <= CAST(strftime('%s','now') AS INTEGER)
              AND NOT EXISTS (SELECT 1 FROM user_services a
                  WHERE a.telegram_id=s.telegram_id AND a.is_active=1
                    AND {exp_a} > CAST(strftime('%s','now') AS INTEGER))''')
        return int(row['c'] if row else 0)
    if filter_key == 'expired_test_only':
        exp_t = "(CASE WHEN CAST(t.expire AS INTEGER) > 1000000000 THEN CAST(t.expire AS INTEGER) ELSE CAST(strftime('%s', t.expire) AS INTEGER) END)"
        exp_a = "(CASE WHEN CAST(a.expire AS INTEGER) > 1000000000 THEN CAST(a.expire AS INTEGER) ELSE CAST(strftime('%s', a.expire) AS INTEGER) END)"
        row = db.fetchone(f'''SELECT COUNT(DISTINCT t.telegram_id) AS c
            FROM user_services t JOIN bot_users b ON b.telegram_id=t.telegram_id
            WHERE t.is_active=1 AND b.is_active=1 AND b.is_blocked=0
              AND t.order_id IS NULL AND t.package_name='سرویس تست'
              AND {exp_t} > 0 AND {exp_t} <= CAST(strftime('%s','now') AS INTEGER)
              AND NOT EXISTS (SELECT 1 FROM user_services a
                  WHERE a.telegram_id=t.telegram_id AND a.is_active=1
                    AND {exp_a} > CAST(strftime('%s','now') AS INTEGER))
              AND NOT EXISTS (SELECT 1 FROM user_services n
                  WHERE n.telegram_id=t.telegram_id
                    AND NOT (n.order_id IS NULL AND n.package_name='سرویس تست'))
              AND NOT EXISTS (SELECT 1 FROM orders o
                  WHERE o.user_id=t.telegram_id
                    AND o.status IN ('paid','build_queued','building_service','service_ready'))''')
        return int(row['c'] if row else 0)
    return db.user_count('WHERE is_active=1')


@broadcast_router.message(EditableButtonFilter(BTN_BROADCAST))
async def broadcast_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer('📣 پیام همگانی', reply_markup=back_keyboard())
    await message.answer('فیلتر مخاطبان پیام همگانی را انتخاب کنید:', reply_markup=filter_keyboard())


@broadcast_router.callback_query(F.data.startswith('bcast:filter:'))
async def broadcast_choose_filter(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    filter_key = callback.data.split(':')[-1]
    await state.update_data(filter_key=filter_key)
    await state.set_state(BroadcastStates.text)
    await callback.message.answer(f'فیلتر: {FILTERS.get(filter_key)}\nمتن پیام را بفرستید. عکس/فایل در نسخه بعدی کپی پیام اضافه می‌شود.')
    await callback.answer()


@broadcast_router.message(BroadcastStates.text)
async def broadcast_get_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    if not message.text:
        await message.answer('فعلا پیام متنی ارسال کنید.')
        return
    data = await state.get_data()
    filter_key = data.get('filter_key', 'all')
    # Preview count without loading all IDs into FSM memory
    count = users_count_by_filter(filter_key)
    if count == 0:
        await message.answer('هیچ کاربری با این فیلتر پیدا نشد.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 انتخاب فیلتر', callback_data='bcast:resend')]]))
        return
    # Store only filter + rendered text; stream IDs during send
    await state.update_data(text=message_text_with_custom_emoji_tokens(message), filter_key=filter_key)
    await state.set_state(BroadcastStates.confirm)
    await message.answer(f'پیام برای {count:,} نفر ارسال می‌شود. تایید؟\n\n⚠️ ارسال به صورت دسته‌ای با رعایت محدودیت تلگرام انجام می‌شود.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ تایید ارسال', callback_data='bcast:send')],
        [InlineKeyboardButton(text='❌ لغو', callback_data='bcast:cancel')],
    ]))


@broadcast_router.callback_query(F.data == 'bcast:cancel')
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_or_answer(callback, 'ارسال پیام لغو شد.')
    await callback.answer()


@broadcast_router.callback_query(F.data == 'bcast:send')
async def broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    data = await state.get_data()
    raw_text = data.get('text') or ''
    filter_key = data.get('filter_key', 'all')
    # Support legacy targets list if present
    legacy_targets = data.get('targets')
    await edit_or_answer(callback, '⏳ ارسال همگانی شروع شد... لطفا صبر کنید. پیشرفت به صورت دوره‌ای گزارش می‌شود.')
    await callback.answer()
    # Prepare rendered content with premium emoji + HTML
    try:
        rendered, parse_mode = await render_custom_emoji_output(bot, raw_text, 'HTML')
    except Exception:
        rendered, parse_mode = raw_text, 'HTML'
    sent = failed = 0
    batch_size = 50
    delay_between_batches = 0.7
    semaphore = asyncio.Semaphore(15)

    async def _send_one(uid: int) -> bool:
        async with semaphore:
            for attempt in range(3):
                try:
                    await bot.send_message(uid, rendered, parse_mode=parse_mode)
                    return True
                except TelegramRetryAfter as e:
                    try:
                        await asyncio.sleep(float(getattr(e, 'retry_after', 1)) + 0.5)
                    except Exception:
                        await asyncio.sleep(1)
                    continue
                except TelegramBadRequest as e:
                    low = str(e).lower()
                    if 'retry after' in low:
                        await asyncio.sleep(1.2)
                        continue
                    return False
                except Exception:
                    return False
            return False

    if isinstance(legacy_targets, list) and legacy_targets:
        # Legacy path: chunk legacy list to avoid spike
        total = len(legacy_targets)
        for i in range(0, total, batch_size):
            batch = legacy_targets[i:i+batch_size]
            results = await asyncio.gather(*(_send_one(int(uid)) for uid in batch))
            for ok in results:
                if ok:
                    sent += 1
                else:
                    failed += 1
            if i + batch_size < total:
                await asyncio.sleep(delay_between_batches)
                # Periodic progress
                try:
                    await bot.send_message(callback.from_user.id, f'📣 پیشرفت: {min(i+batch_size, total)}/{total} — موفق {sent} | ناموفق {failed}')
                except Exception:
                    pass
        db.add_log(callback.from_user.id, 'advanced_broadcast', 'broadcast', '', {'sent': sent, 'failed': failed, 'targets': total, 'mode': 'legacy_batched'})
        await state.clear()
        await callback.message.answer(f'ارسال تمام شد. موفق: {sent} | ناموفق: {failed} از {total}')
        return

    # Streaming path: fetch IDs in chunks from DB to keep memory low
    offset = 0
    total_streamed = 0
    while True:
        batch = users_by_filter(filter_key, limit=batch_size, offset=offset)
        if not batch:
            break
        total_streamed += len(batch)
        results = await asyncio.gather(*(_send_one(int(uid)) for uid in batch))
        for ok in results:
            if ok:
                sent += 1
            else:
                failed += 1
        offset += batch_size
        if len(batch) == batch_size:
            await asyncio.sleep(delay_between_batches)
            # Optional throttle reduction if many fails?
            if (sent + failed) % 200 == 0:
                try:
                    await bot.send_message(callback.from_user.id, f'📣 پیشرفت: {sent+failed} — موفق {sent} | ناموفق {failed}')
                except Exception:
                    pass
        else:
            break
    # Final count includes streamed total
    db.add_log(callback.from_user.id, 'advanced_broadcast', 'broadcast', '', {'sent': sent, 'failed': failed, 'targets': total_streamed, 'mode': 'streamed'})
    await state.clear()
    await callback.message.answer(f'ارسال تمام شد. موفق: {sent} | ناموفق: {failed} از {total_streamed}')

# Backwards compatibility: cancel handler already exists via bcast:cancel above, add alias for resend flow
@broadcast_router.callback_query(F.data == 'bcast:resend')
async def broadcast_resend(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_or_answer(callback, 'فیلتر مخاطبان پیام همگانی را انتخاب کنید:', reply_markup=filter_keyboard())
    await callback.answer()
