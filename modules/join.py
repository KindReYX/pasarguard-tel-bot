from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_FORCE_JOIN, back_keyboard, cancel_keyboard
from permissions import deny_callback, deny_message, is_admin, is_staff
from utils import answer_callback, edit_or_answer

join_router = Router()

# Cache: user_id -> (expires_at, missing_list). Short TTL to avoid hammering getChatMember.
_JOIN_CACHE: dict[int, tuple[float, list[dict]]] = {}
_JOIN_CACHE_TTL = 90.0  # seconds; configurable via db setting join_cache_ttl
# Cache for channel list itself (rarely changes) to avoid DB hit per message
_CHANNELS_CACHE: tuple[float, list[dict]] | None = None
_CHANNELS_CACHE_TTL = 30.0

JOINED_STATUSES = {'member', 'administrator', 'creator'}


class JoinState(StatesGroup):
    waiting_for_channel = State()


def channel_text(row: dict) -> str:
    active = 'فعال ✅' if row.get('is_active') else 'غیرفعال ❌'
    return (
        '🔐 کانال/گروه جوین اجباری\n\n'
        f"ID: {row.get('id')}\n"
        f"عنوان: {row.get('title') or '-'}\n"
        f"Chat ID: {row.get('chat_id')}\n"
        f"لینک: {row.get('invite_link') or 'ثبت نشده'}\n"
        f"وضعیت: {active}"
    )


def join_admin_keyboard() -> InlineKeyboardMarkup:
    channels = db.required_join_list(include_inactive=True)
    rows = [[InlineKeyboardButton(text='➕ افزودن کانال/گروه', callback_data='join:add')]]
    for channel in channels:
        icon = '✅' if channel.get('is_active') else '🚫'
        title = channel.get('title') or channel.get('chat_id')
        rows.append([InlineKeyboardButton(text=f'{icon} {title}', callback_data=f"join:detail:{channel['id']}")])
    rows.append([InlineKeyboardButton(text='🔄 بروزرسانی', callback_data='join:list')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def join_detail_keyboard(channel_id: int, is_active: int) -> InlineKeyboardMarkup:
    toggle_text = '🚫 غیرفعال کردن' if is_active else '✅ فعال کردن'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f'join:toggle:{channel_id}')],
        [InlineKeyboardButton(text='🗑 حذف', callback_data=f'join:delete:{channel_id}')],
        [InlineKeyboardButton(text='🔙 برگشت به لیست', callback_data='join:list')],
    ])


def user_join_keyboard(missing_channels: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for channel in missing_channels:
        title = channel.get('title') or channel.get('chat_id') or 'عضویت'
        invite_link = channel.get('invite_link')
        chat_id = str(channel.get('chat_id') or '')
        if not invite_link and chat_id.startswith('@'):
            invite_link = f'https://t.me/{chat_id[1:]}'
        if invite_link:
            rows.append([InlineKeyboardButton(text=f'عضویت در {title}', url=invite_link)])
    rows.append([InlineKeyboardButton(text='✅ بررسی عضویت', callback_data='join:check')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_join_text(missing_channels: list[dict]) -> str:
    names = '\n'.join(f"• {item.get('title') or item.get('chat_id')}" for item in missing_channels)
    return (
        'برای استفاده از ربات، ابتدا باید در کانال/گروه‌های زیر عضو شوید:\n\n'
        f'{names}\n\n'
        'بعد از عضویت، دکمه «بررسی عضویت» را بزنید.'
    )


def _get_cached_channels() -> list[dict] | None:
    global _CHANNELS_CACHE
    if _CHANNELS_CACHE is not None:
        exp, vals = _CHANNELS_CACHE
        if _time.monotonic() < exp:
            return vals
        _CHANNELS_CACHE = None
    return None


def _set_cached_channels(channels: list[dict]) -> None:
    global _CHANNELS_CACHE
    try:
        ttl = float(db.get_setting('join_cache_ttl', _CHANNELS_CACHE_TTL) or _CHANNELS_CACHE_TTL)
    except Exception:
        ttl = _CHANNELS_CACHE_TTL
    _CHANNELS_CACHE = (_time.monotonic() + max(5.0, ttl), list(channels))


def invalidate_join_cache() -> None:
    global _JOIN_CACHE, _CHANNELS_CACHE
    _JOIN_CACHE.clear()
    _CHANNELS_CACHE = None


async def missing_required_join_channels(bot, user_id: int, *, use_cache: bool = True, force_refresh: bool = False) -> list[dict]:
    # Fast-path: staff never needs check (handled upstream), but keep here too
    try:
        if is_staff(user_id):
            return []
    except Exception:
        pass
    # Per-user cache (skip when force_refresh or join:check)
    if use_cache and not force_refresh:
        try:
            cached = _JOIN_CACHE.get(int(user_id))
            if cached is not None:
                exp, vals = cached
                if _time.monotonic() < exp:
                    return list(vals)
                _JOIN_CACHE.pop(int(user_id), None)
        except Exception:
            pass
    # Channels list with short cache
    channels = _get_cached_channels()
    if channels is None:
        channels = db.required_join_list(include_inactive=False)
        _set_cached_channels(channels)
    if not channels:
        if use_cache:
            _JOIN_CACHE[int(user_id)] = (_time.monotonic() + _JOIN_CACHE_TTL, [])
        return []
    # Parallel getChatMember with timeout gathering; fallback to sequential on error
    async def _check_one(ch: dict) -> tuple[dict, bool]:
        chat_id = ch.get('chat_id')
        if not chat_id:
            return ch, True
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            is_missing = member.status not in JOINED_STATUSES
            return ch, is_missing
        except Exception:
            return ch, True

    try:
        results = await asyncio.gather(*(_check_one(ch) for ch in channels), return_exceptions=False)
    except Exception:
        # Fallback sequential
        results = []
        for ch in channels:
            results.append(await _check_one(ch))
    missing = [ch for ch, is_missing in results if is_missing]
    if use_cache:
        try:
            ttl = float(db.get_setting('join_cache_ttl', _JOIN_CACHE_TTL) or _JOIN_CACHE_TTL)
        except Exception:
            ttl = _JOIN_CACHE_TTL
        _JOIN_CACHE[int(user_id)] = (_time.monotonic() + max(10.0, ttl), list(missing))
    return missing


def _clear_user_join_cache(user_id: int) -> None:
    _JOIN_CACHE.pop(int(user_id), None)


async def ensure_force_join(message: Message, bot) -> bool:
    user = message.from_user
    if not user or is_staff(user.id):
        return True
    missing = await missing_required_join_channels(bot, user.id)
    if not missing:
        return True
    await message.answer(user_join_text(missing), reply_markup=user_join_keyboard(missing))
    return False


class RequiredJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, 'from_user', None)
        user_id = user.id if user else None
        if not user_id or is_staff(user_id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == 'join:check':
            return await handler(event, data)
        bot = data.get('bot')
        if not bot:
            return await handler(event, data)
        missing = await missing_required_join_channels(bot, user_id)
        if not missing:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await answer_callback(event, 'ابتدا عضویت را کامل کنید.', show_alert=True)
            try:
                await event.message.answer(user_join_text(missing), reply_markup=user_join_keyboard(missing))
            except Exception:
                pass
            return None
        if isinstance(event, Message):
            await event.answer(user_join_text(missing), reply_markup=user_join_keyboard(missing))
            return None
        return None


@join_router.message(EditableButtonFilter(BTN_FORCE_JOIN))
async def join_home(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await state.clear()
    await message.answer('🔐 مدیریت جوین اجباری', reply_markup=back_keyboard())
    await message.answer('کانال‌ها/گروه‌های جوین اجباری:', reply_markup=join_admin_keyboard())


@join_router.callback_query(F.data == 'join:list')
async def join_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, 'کانال‌ها/گروه‌های جوین اجباری:', reply_markup=join_admin_keyboard())
    await answer_callback(callback)


@join_router.callback_query(F.data == 'join:add')
async def join_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(JoinState.waiting_for_channel)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='join:list')]])
    await edit_or_answer(
        callback,
        'اطلاعات کانال/گروه را بفرستید.\n\n'
        'فرمت پیشنهادی برای عمومی:\n'
        '@channel_username\n\n'
        'برای خصوصی:\n'
        '-1001234567890 | نام کانال | https://t.me/+invite\n\n'
        'نکته: ربات باید داخل کانال/گروه عضو باشد و بهتر است ادمین باشد.',
        reply_markup=kb,
    )
    await answer_callback(callback)


@join_router.message(JoinState.waiting_for_channel)
async def join_add_value(message: Message, state: FSMContext, bot):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    if not message.text:
        await message.answer('لطفا مقدار متنی ارسال کنید.')
        return
    parts = [part.strip() for part in message.text.split('|')]
    raw_chat_id = parts[0]
    manual_title = parts[1] if len(parts) > 1 and parts[1] else None
    manual_link = parts[2] if len(parts) > 2 and parts[2] else None
    chat_id = raw_chat_id
    title = manual_title or raw_chat_id
    invite_link = manual_link
    try:
        chat = await bot.get_chat(raw_chat_id)
        chat_id = str(chat.id)
        title = manual_title or (chat.title or chat.full_name or raw_chat_id)
        if not invite_link:
            if getattr(chat, 'username', None):
                invite_link = f'https://t.me/{chat.username}'
            elif getattr(chat, 'invite_link', None):
                invite_link = chat.invite_link
    except Exception:
        pass
    if raw_chat_id.startswith('@') and not invite_link:
        invite_link = f'https://t.me/{raw_chat_id[1:]}'
    db.required_join_add(chat_id=chat_id, title=title, invite_link=invite_link, created_by=message.from_user.id)
    db.add_log(message.from_user.id, 'required_join_added', 'required_join', chat_id, {'title': title, 'invite_link': invite_link})
    invalidate_join_cache()
    await state.clear()
    await message.answer('کانال/گروه جوین اجباری اضافه شد ✅', reply_markup=back_keyboard())
    await message.answer('لیست جوین اجباری:', reply_markup=join_admin_keyboard())


@join_router.callback_query(F.data.startswith('join:detail:'))
async def join_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    channel_id = int(callback.data.split(':')[-1])
    row = db.required_join_get(channel_id)
    if not row:
        await answer_callback(callback, 'مورد پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, channel_text(row), reply_markup=join_detail_keyboard(channel_id, row['is_active']))
    await answer_callback(callback)


@join_router.callback_query(F.data.startswith('join:toggle:'))
async def join_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    channel_id = int(callback.data.split(':')[-1])
    row = db.required_join_get(channel_id)
    if not row:
        await answer_callback(callback, 'مورد پیدا نشد.', show_alert=True)
        return
    new_value = 0 if row['is_active'] else 1
    db.required_join_set_active(channel_id, new_value)
    db.add_log(callback.from_user.id, 'required_join_toggled', 'required_join', str(channel_id), {'is_active': bool(new_value)})
    invalidate_join_cache()
    row = db.required_join_get(channel_id)
    await edit_or_answer(callback, channel_text(row), reply_markup=join_detail_keyboard(channel_id, row['is_active']))
    await answer_callback(callback, 'وضعیت تغییر کرد.')


@join_router.callback_query(F.data.startswith('join:delete:'))
async def join_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    channel_id = int(callback.data.split(':')[-1])
    db.required_join_delete(channel_id)
    db.add_log(callback.from_user.id, 'required_join_deleted', 'required_join', str(channel_id))
    invalidate_join_cache()
    await edit_or_answer(callback, 'حذف شد.\n\nکانال‌ها/گروه‌های جوین اجباری:', reply_markup=join_admin_keyboard())
    await answer_callback(callback, 'حذف شد.')


@join_router.callback_query(F.data == 'join:check')
async def user_join_check(callback: CallbackQuery, bot):
    _clear_user_join_cache(callback.from_user.id)
    missing = await missing_required_join_channels(bot, callback.from_user.id, use_cache=False, force_refresh=True)
    if missing:
        await answer_callback(callback, 'هنوز عضویت کامل نشده است.', show_alert=True)
        try:
            await edit_or_answer(callback, user_join_text(missing), reply_markup=user_join_keyboard(missing))
        except Exception:
            pass
        return
    await answer_callback(callback, 'عضویت تایید شد ✅', show_alert=True)
    try:
        await edit_or_answer(callback, 'عضویت شما تایید شد ✅\nحالا می‌توانید از ربات استفاده کنید.')
    except Exception:
        pass
