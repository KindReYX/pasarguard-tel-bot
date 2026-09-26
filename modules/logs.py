from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_LOGS, back_keyboard
from permissions import deny_message, is_super_admin

logs_router = Router()


@logs_router.message(EditableButtonFilter(BTN_LOGS))
async def logs_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_super_admin(uid):
        await deny_message(message)
        return
    rows = db.fetchall('SELECT * FROM admin_logs ORDER BY id DESC LIMIT 30')
    text = '🧾 آخرین لاگ های ادمین\n\n'
    if not rows:
        text += 'لاگی ثبت نشده است.'
    else:
        for row in rows:
            text += f"#{row['id']} | admin={row['admin_id']} | {row['action']} | {row.get('entity_type') or '-'}:{row.get('entity_id') or '-'} | {row['created_at']}\n"
    await message.answer(text, reply_markup=back_keyboard())
