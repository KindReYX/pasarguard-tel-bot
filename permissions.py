from __future__ import annotations

from typing import Iterable

from aiogram.types import CallbackQuery, Message

import db

SUPER_ADMIN = 'super_admin'
ADMIN = 'admin'
SELLER = 'seller'
USER = 'user'

ROLE_LABELS = {
    SUPER_ADMIN: 'سوپر ادمین',
    ADMIN: 'ادمین',
    SELLER: 'فروشنده',
    USER: 'کاربر',
}


def get_role(user_id: int | None) -> str:
    if not user_id:
        return USER
    # Use db-level TTL cache to avoid DB hit per permission check (hot path)
    try:
        # Prefer db.get_role_cached if available (faster, shared TTL)
        if hasattr(db, 'get_role_cached'):
            return db.get_role_cached(user_id)
    except Exception:
        pass
    row = db.fetchone('SELECT role FROM bot_admins WHERE telegram_id=? AND is_active=1', (user_id,))
    if row:
        return row['role']
    return USER


def is_super_admin(user_id: int | None) -> bool:
    return get_role(user_id) == SUPER_ADMIN


def is_admin(user_id: int | None) -> bool:
    return get_role(user_id) in {SUPER_ADMIN, ADMIN}


def is_staff(user_id: int | None) -> bool:
    return get_role(user_id) in {SUPER_ADMIN, ADMIN, SELLER}


def can_manage_packs(user_id: int | None) -> bool:
    return get_role(user_id) in {SUPER_ADMIN, ADMIN}


def can_view_packs(user_id: int | None) -> bool:
    return get_role(user_id) in {SUPER_ADMIN, ADMIN, SELLER}


def can_message_customer(actor_id: int | None, customer_id: int) -> bool:
    role = get_role(actor_id)
    if role in {SUPER_ADMIN, ADMIN}:
        return True
    if role == SELLER:
        row = db.fetchone('SELECT owner_admin_id FROM bot_users WHERE telegram_id=?', (customer_id,))
        return bool(row and row.get('owner_admin_id') == actor_id)
    return False


def allowed(user_id: int | None, roles: Iterable[str]) -> bool:
    return get_role(user_id) in set(roles)


async def deny_message(message: Message) -> None:
    await message.answer('شما دسترسی این بخش را ندارید.')


async def deny_callback(callback: CallbackQuery) -> None:
    await callback.answer('دسترسی ندارید.', show_alert=True)


def is_seller(telegram_id: int | None) -> bool:
    return get_role(telegram_id) == 'seller'
