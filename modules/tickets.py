from __future__ import annotations

import html
from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

import db
from utils import edit_or_answer, person_display, message_text_with_custom_emoji_tokens
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_TICKETS
from permissions import is_staff, deny_message, deny_callback, get_role, can_message_customer


tickets_router = Router()


class TicketStates(StatesGroup):
    answer = State()


def tickets_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🎫 تیکت‌های باز', callback_data='tickets:list:open')],
        [InlineKeyboardButton(text='✅ تیکت‌های بسته', callback_data='tickets:list:closed')],
    ])


def ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✍️ پاسخ', callback_data=f'tickets:answer:{ticket_id}:{user_id}')],
        [InlineKeyboardButton(text='🔒 بستن تیکت', callback_data=f'tickets:close:{ticket_id}')],
    ])


def new_ticket_admin_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='👁 مشاهده تیکت', callback_data=f'tickets:view:{ticket_id}'),
            InlineKeyboardButton(text='✍️ پاسخ', callback_data=f'tickets:answer:{ticket_id}:{user_id}'),
        ],
        [InlineKeyboardButton(text='🎫 مدیریت تیکت‌ها', callback_data='tickets:list:open')],
    ])


async def notify_admins_new_ticket(bot: Bot, ticket_id: int, user_id: int, subject: str, message_text: str, created_at: str) -> int:
    """Send immediate notification to all admins/super_admins when a new ticket is opened."""
    from modules.card_transfer import _user_clickable_html, _approver_ids
    user_row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (int(user_id),)) or {}
    clickable, _ = _user_clickable_html(user_id, user_row)
    username = (user_row.get('username') or '').strip()
    username_line = f'\nیوزرنیم: @{html.escape(username)}' if username else ''

    admin_msg = (
        f'🎫 <b>تیکت جدید دریافت شد</b> #{ticket_id}\n\n'
        f'کاربر: {clickable}{username_line}\n'
        f'آیدی عددی: <code>{user_id}</code>\n'
        f'موضوع: <b>{html.escape(str(subject))}</b>\n'
        f'زمان ثبت: <code>{html.escape(str(created_at))}</code>\n\n'
        f'متن پیام:\n<blockquote>{html.escape(str(message_text))}</blockquote>'
    )

    markup = new_ticket_admin_keyboard(ticket_id, user_id)
    recipients = _approver_ids()
    sent = 0
    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, admin_msg, parse_mode='HTML', reply_markup=markup)
            sent += 1
        except Exception as exc:
            db.record_system_error('tickets.notify_admin', str(exc), entity_type='ticket', entity_id=str(ticket_id), error_type=type(exc).__name__)
    return sent


@tickets_router.message(EditableButtonFilter(BTN_TICKETS))
async def tickets_home(message: Message):
    if not is_staff(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer('🎫 مدیریت تیکت‌ها', reply_markup=back_keyboard())
    await message.answer('نوع تیکت را انتخاب کنید:', reply_markup=tickets_keyboard())


@tickets_router.callback_query(F.data.startswith('tickets:list:'))
async def list_tickets(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback)
        return
    status = callback.data.split(':')[-1]
    role = get_role(callback.from_user.id)
    if role == 'seller':
        condition = " AND user_id IN (SELECT telegram_id FROM bot_users WHERE owner_admin_id=?)"
        params = (callback.from_user.id,)
    else:
        condition = ''
        params = ()
    if status == 'open':
        rows = db.fetchall("SELECT * FROM tickets WHERE status!='closed'" + condition + " ORDER BY updated_at DESC LIMIT 15", params)
    else:
        rows = db.fetchall("SELECT * FROM tickets WHERE status='closed'" + condition + " ORDER BY updated_at DESC LIMIT 15", params)
    if not rows:
        await edit_or_answer(callback, 'تیکتی وجود ندارد.', reply_markup=tickets_keyboard())
        await callback.answer()
        return
    text = '🎫 تیکت‌ها\n\n'
    kb = []
    for row in rows:
        text += f"#{row['id']} | {person_display(row['user_id'])} | {row['subject'] or '-'} | {row['status']}\n"
        kb.append([InlineKeyboardButton(text=f"تیکت #{row['id']}", callback_data=f"tickets:view:{row['id']}")])
    await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@tickets_router.callback_query(F.data.startswith('tickets:view:'))
async def view_ticket(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback)
        return
    ticket_id = int(callback.data.split(':')[-1])
    ticket = db.fetchone('SELECT * FROM tickets WHERE id=?', (ticket_id,))
    if ticket and not can_message_customer(callback.from_user.id, int(ticket['user_id'])):
        await deny_callback(callback)
        return
    messages = db.fetchall('SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 5', (ticket_id,))
    if not ticket:
        await callback.answer('تیکت پیدا نشد.', show_alert=True)
        return
    text = f"🎫 تیکت #{ticket_id}\nکاربر: {person_display(ticket['user_id'], include_username=True)}\nموضوع: {ticket['subject']}\nوضعیت: {ticket['status']}\n\nپیام‌های اخیر:\n"
    for m in reversed(messages):
        text += f"{m['sender_id']}: {m['message']}\n"
    await edit_or_answer(callback, text, reply_markup=ticket_keyboard(ticket_id, ticket['user_id']))
    await callback.answer()


@tickets_router.callback_query(F.data.startswith('tickets:answer:'))
async def answer_start(callback: CallbackQuery, state: FSMContext):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback)
        return
    _, _, ticket_id, user_id = callback.data.split(':')
    if not can_message_customer(callback.from_user.id, int(user_id)):
        await deny_callback(callback); return
    await state.set_state(TicketStates.answer)
    await state.update_data(ticket_id=int(ticket_id), user_id=int(user_id))
    await callback.message.answer('پاسخ تیکت را ارسال کنید.')
    await callback.answer()


@tickets_router.message(TicketStates.answer)
async def answer_finish(message: Message, state: FSMContext, bot: Bot):
    if not is_staff(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    ticket_id = data['ticket_id']
    user_id = data['user_id']
    text = message_text_with_custom_emoji_tokens(message)
    if not can_message_customer(message.from_user.id if message.from_user else None, int(user_id)):
        await state.clear(); await deny_message(message); return
    db.execute('INSERT INTO ticket_messages (ticket_id, sender_id, message, created_at) VALUES (?, ?, ?, ?)', (ticket_id, message.from_user.id, text, db.now_iso()))
    db.execute('UPDATE tickets SET status=?, updated_at=? WHERE id=?', ('answered', db.now_iso(), ticket_id))
    try:
        await bot.send_message(user_id, f'پاسخ پشتیبانی:\n{text}')
    except Exception:
        pass
    db.add_log(message.from_user.id, 'answer_ticket', 'ticket', str(ticket_id))
    await message.answer('پاسخ ارسال شد.')
    await state.clear()


@tickets_router.callback_query(F.data.startswith('tickets:close:'))
async def close_ticket(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback)
        return
    ticket_id = int(callback.data.split(':')[-1])
    ticket = db.fetchone('SELECT user_id FROM tickets WHERE id=?', (ticket_id,))
    if not ticket or not can_message_customer(callback.from_user.id, int(ticket['user_id'])):
        await deny_callback(callback); return
    db.execute('UPDATE tickets SET status=?, updated_at=? WHERE id=?', ('closed', db.now_iso(), ticket_id))
    db.add_log(callback.from_user.id, 'close_ticket', 'ticket', str(ticket_id))
    await edit_or_answer(callback, 'تیکت بسته شد.')
    await callback.answer()
