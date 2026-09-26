from __future__ import annotations
import time
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
import db

class ThrottleMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._bucket: dict[str, float] = {}
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        # Live edit mode re-feeds the original update only after admin explicitly
        # presses "perform action". Do not throttle that intentional replay.
        if data.get('edit_mode_replay'):
            return await handler(event, data)
        user_id = None; action = 'event'; interval = 0.8
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            # Do not throttle photo/document upload messages (payment receipts, etc.)
            if event.photo or event.document:
                return await handler(event, data)
            action = f'msg:{event.text or event.content_type}'; interval = 0.6
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            action = f'cb:{event.data or ""}'
            if action.startswith('cb:cust_service_configs'): interval = 10
            elif action.startswith('cb:cust_pay') or action.startswith('cb:cust_free') or action.startswith('cb:cust_wallet_activate'): interval = 5
            else: interval = 1.0
        if user_id:
            key = f'{user_id}:{action}'; now = time.monotonic(); last = self._bucket.get(key, 0)
            if now - last < interval:
                if isinstance(event, CallbackQuery):
                    try: await event.answer('کمی صبر کنید و دوباره بزنید.')
                    except Exception: pass
                    return None
                if isinstance(event, Message): return None
            self._bucket[key] = now
        try:
            return await handler(event, data)
        except Exception as exc:
            try: db.record_system_error('security.middleware', str(exc), user_id=user_id, error_type=type(exc).__name__)
            except Exception: pass
            raise


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        user_id = getattr(user, 'id', None) if user else None
        if not user_id:
            return await handler(event, data)

        # Staff accounts are never locked out by the customer blacklist.
        try:
            from permissions import is_staff
            if is_staff(user_id):
                return await handler(event, data)
        except Exception:
            pass

        row = db.fetchone('SELECT is_blocked FROM bot_users WHERE telegram_id=?', (int(user_id),))
        if row and int(row.get('is_blocked') or 0) == 1:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer('دسترسی شما به ربات مسدود شده است.', show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer('⛔️ دسترسی شما به ربات مسدود شده است.')
                except Exception:
                    pass
            return None

        return await handler(event, data)
