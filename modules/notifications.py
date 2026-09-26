from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_NOTIFICATIONS, back_keyboard
from permissions import deny_callback, deny_message, is_admin

notifications_router = Router()


def notif_kb() -> InlineKeyboardMarkup:
    enabled = bool(db.get_setting('notifications_enabled', True))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='خواندن همه', callback_data='notif:read_all')],
        [InlineKeyboardButton(text=f"ارسال اعلان: {'روشن' if enabled else 'خاموش'}", callback_data='notif:toggle')],
    ])


@notifications_router.message(EditableButtonFilter(BTN_NOTIFICATIONS))
async def notifications_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    rows = db.fetchall('SELECT * FROM notifications ORDER BY id DESC LIMIT 30')
    text = '🔔 اعلان ها\n\n'
    if not rows:
        text += 'اعلانی وجود ندارد.'
    else:
        for row in rows:
            mark = '✅' if row['is_read'] else '🔴'
            text += f"{mark} #{row['id']} | {row['type']} | {row['message']} | {row['created_at']}\n"
    await message.answer(text, reply_markup=back_keyboard())
    await message.answer('اقدام:', reply_markup=notif_kb())


@notifications_router.callback_query(F.data == 'notif:read_all')
async def notifications_read_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    db.execute('UPDATE notifications SET is_read=1')
    await callback.answer('همه خوانده شدند.')



@notifications_router.callback_query(F.data == 'notif:toggle')
async def notifications_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = bool(db.get_setting('notifications_enabled', True))
    db.set_setting('notifications_enabled', not current)
    await callback.answer(f"اعلان ها {'روشن' if not current else 'خاموش'} شد.")
    await callback.message.edit_reply_markup(reply_markup=notif_kb())
