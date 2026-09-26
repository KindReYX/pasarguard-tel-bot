from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramRetryAfter

import db
import modules.payment_engine as payment_engine
from modules import edit_runtime
from keyboards import EditableButtonFilter
from keyboards import BTN_CARD_RECEIPTS, customer_keyboard
from permissions import is_super_admin
from utils import render_template_html, user_template_context, person_display, get_button_style, RETRYABLE_NETWORK_EXCEPTIONS

card_transfer_router = Router()


class CardTransferState(StatesGroup):
    waiting_receipt = State()
    waiting_reject_reason = State()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approver_ids() -> list[int]:
    raw = db.get_setting('card_receipt_admin_ids', [])
    out: list[int] = []
    if isinstance(raw, list):
        for value in raw:
            try:
                out.append(int(value))
            except Exception:
                pass
    # Super admins always retain access even if not explicitly ticked.
    rows = db.fetchall("SELECT telegram_id FROM bot_admins WHERE role='super_admin' AND is_active=1")
    for row in rows:
        uid = int(row['telegram_id'])
        if uid not in out:
            out.append(uid)
    return out


def _can_review(user_id: int | None) -> bool:
    return bool(user_id and (is_super_admin(user_id) or int(user_id) in _approver_ids()))


def _format_toman(value: int) -> str:
    return f'{int(value):,} تومان'


CARD_PAYMENT_DEFAULT = (
    '💳 پرداخت کارت به کارت\n\n'
    'مبلغ: {price}\nشماره کارت: {card_number}\nبه نام: {card_holder}\n'
    'بانک: {bank}\nشماره سفارش: {order_id}\n\n'
    'بعد از واریز، تصویر فیش را همینجا ارسال کنید.'
)

CARD_RECEIPT_UPLOAD_PROMPT_DEFAULT = (
    '📎 حالا تصویر فیش یا فایل فیش را ارسال کنید. برای انصراف /cancel را بفرستید.'
)

CARD_RECEIPT_SUBMITTED_DEFAULT = (
    '✅ فیش شما ثبت شد و برای بررسی ارسال شد. نتیجه تایید یا رد همینجا به شما اعلام می‌شود.'
)


async def _payment_text(bot: Bot, user: Any, amount: int, entity_id: int) -> tuple[str, str, dict[str, Any]]:
    template = str(db.get_setting('card_payment_text', CARD_PAYMENT_DEFAULT) or CARD_PAYMENT_DEFAULT)
    card_number = str(db.get_setting('card_number', '') or '-')
    ctx = user_template_context(user)
    ctx.update({
        'price': _format_toman(amount),
        'price_raw': int(amount),
        'card_number': '__CARD_NUMBER_COPY__',
        'card_holder': str(db.get_setting('card_holder', '') or '-'),
        'bank': str(db.get_setting('card_bank', '') or '-'),
        'order_id': entity_id,
    })
    rendered = await render_template_html(bot, template, ctx)
    rendered = rendered.replace('__CARD_NUMBER_COPY__', f'<code>{html.escape(card_number)}</code>')
    edit_ctx = dict(ctx)
    edit_ctx['card_number'] = card_number
    return rendered, template, edit_ctx


def _is_privacy_restricted_error(exc: BaseException) -> bool:
    """Telegram rejects tg://user?id URL buttons when the bot cannot mention that user."""
    return 'button_user_privacy_restricted' in str(exc).lower()


def _has_user_link_button(markup: InlineKeyboardMarkup | None) -> bool:
    if markup is None:
        return False
    for row in markup.inline_keyboard:
        for button in row:
            if str(getattr(button, 'url', '') or '').startswith('tg://user?id='):
                return True
    return False


def _strip_user_link_buttons(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Drop tg://user?id buttons so the receipt still reaches the admin.

    The privacy error invalidates the ENTIRE message, so removing the button is
    the only way to deliver approve/reject actions. Username example.com buttons are
    kept because those are not affected by mention privacy.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard:
        filtered = [b for b in row if not str(getattr(b, 'url', '') or '').startswith('tg://user?id=')]
        if filtered:
            rows.append(filtered)
    if not rows:
        rows = [[InlineKeyboardButton(text='🔄 وضعیت', callback_data='cardadmin:view:0')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _receipt_review_keyboard(receipt_id: int, user_id: int | None = None, username: str | None = None) -> InlineKeyboardMarkup:
    # Direct chat button: tg://user?id= opens chat without needing username
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='✅ تایید فیش', callback_data=f'cardadmin:approve:{receipt_id}', style=get_button_style('success')),
            InlineKeyboardButton(text='❌ رد فیش', callback_data=f'cardadmin:reject:{receipt_id}', style=get_button_style('danger')),
        ],
    ]
    if user_id:
        # Primary: tg direct link works even if user has no username or privacy
        try:
            uid = int(user_id)
            rows.append([InlineKeyboardButton(text='💬 چت با کاربر', url=f'tg://user?id={uid}')])
            # Alternative if username exists (some clients prefer example.com)
            if username:
                uname = str(username).lstrip('@')
                if uname:
                    rows.append([InlineKeyboardButton(text=f'🔗 @{uname}', url=f'https://example.com/{uname}')])
        except Exception:
            pass
    rows.append([InlineKeyboardButton(text='🔄 وضعیت', callback_data=f'cardadmin:view:{receipt_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _user_clickable_html(user_id: int, user_row: dict | None = None) -> tuple[str, str | None]:
    """Return (html_link, username) for a user row. Uses tg://user?id for guaranteed click."""
    user_row = user_row or {}
    name = (user_row.get('first_name') or '').strip()
    username = (user_row.get('username') or '').strip().lstrip('@')
    if not name and username:
        name = '@' + username
    if not name:
        name = str(user_id)
    # HTML-escaped display name inside link
    safe_name = html.escape(name)
    # Clickable mention via tg://user?id — works even without username
    link = f'<a href="tg://user?id={int(user_id)}">{safe_name}</a>'
    return link, (username or None)


def _receipt_caption(receipt: dict) -> str:
    order_type = str(receipt.get('order_type') or 'new').lower()
    if receipt.get('kind') == 'wallet':
        kind = 'شارژ کیف پول'
    elif order_type == 'renew' or receipt.get('service_id'):
        kind = 'تمدید سرویس'
    else:
        kind = 'خرید جدید'

    user = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (int(receipt['user_id']),)) or {}
    clickable, _ = _user_clickable_html(int(receipt['user_id']), user)
    username = (user.get('username') or '').strip()
    username_line = f'\nیوزرنیم: @{html.escape(username)}' if username else ''

    # Selected service/plan info
    details = []
    service_id = receipt.get('service_id')
    package_name = receipt.get('package_name')
    customer_username = receipt.get('customer_username')
    if not package_name and receipt.get('kind') == 'order':
        ord_row = db.get_order(int(receipt['entity_id']))
        if ord_row:
            package_name = ord_row.get('package_name')
            customer_username = customer_username or ord_row.get('customer_username')
            if not service_id:
                service_id = ord_row.get('service_id')

    if service_id:
        svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (int(service_id),))
        svc_name = (svc or {}).get('service_username') or str(service_id)
        details.append(f'سرویس انتخابی: <code>{html.escape(str(svc_name))}</code> (شناسه: {service_id})')
    elif customer_username:
        details.append(f'نام سرویس: <code>{html.escape(str(customer_username))}</code>')

    if package_name:
        details.append(f'بسته/پلن: {html.escape(str(package_name))}')

    details_block = ('\n' + '\n'.join(details)) if details else ''
    submission_time = receipt.get('submitted_at') or _now()

    return (
        f'🧾 فیش کارت به کارت #{receipt["id"]}\n\n'
        f'نوع تراکنش: {kind}\n'
        f'کاربر: {clickable}{username_line}\n'
        f'آیدی عددی: <code>{receipt["user_id"]}</code>\n'
        f'مبلغ: {_format_toman(int(receipt["amount_toman"]))}\n'
        f'شناسه درخواست: <code>{receipt["entity_id"]}</code>'
        f'{details_block}\n'
        f'زمان ثبت: <code>{html.escape(str(submission_time))}</code>\n'
        f'وضعیت: {receipt.get("status") or "pending"}'
    )


def _approved_caption_with_link(receipt: dict, sub_url: str | None) -> str:
    base = _receipt_caption(receipt)
    # Normalize status text (DB stores 'approved', caption shows pending by default if not refreshed)
    base = base.replace('وضعیت: approved', 'وضعیت: ✅ تایید شده').replace('وضعیت: pending', 'وضعیت: ✅ تایید شده')
    # Only orders have a subscription link; wallet top-ups don't
    if receipt.get('kind') == 'order':
        if sub_url and str(sub_url).strip():
            safe = html.escape(str(sub_url).strip())
            base += f'\n\n🔗 لینک اشتراک:\n<code>{safe}</code>'
        else:
            base += '\n\n⚠️ لینک اشتراک: در حال ساخت / نیازمند بررسی (سرویس ساخته نشد)'
    return base


def _approved_keyboard(receipt: dict, sub_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    try:
        uid = int(receipt['user_id'])
        rows.append([InlineKeyboardButton(text='💬 چت با کاربر', url=f'tg://user?id={uid}')])
        user = db.fetchone('SELECT username FROM bot_users WHERE telegram_id=?', (uid,)) or {}
        uname = (user.get('username') or '').strip().lstrip('@') if isinstance(user, dict) else ''
        if uname:
            rows.append([InlineKeyboardButton(text=f'🔗 @{uname}', url=f'https://example.com/{uname}')])
    except Exception:
        pass
    if receipt.get('kind') == 'order' and sub_url and str(sub_url).strip().startswith(('http://', 'https://')):
        rows.append([InlineKeyboardButton(text='🔗 باز کردن لینک اشتراک', url=str(sub_url).strip())])
    rows.append([InlineKeyboardButton(text='🔄 وضعیت', callback_data=f'cardadmin:view:{receipt["id"]}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_receipt_to_admin_safe(bot: Bot, admin_id: int, receipt: dict) -> int | None:
    """Send receipt to a single admin with retry on network error, returning the sent message ID."""
    caption = _receipt_caption(receipt)
    try:
        user = db.fetchone('SELECT username FROM bot_users WHERE telegram_id=?', (int(receipt['user_id']),)) or {}
        username = (user.get('username') or '').strip() if isinstance(user, dict) else None
    except Exception:
        username = None
    markup = _receipt_review_keyboard(int(receipt['id']), int(receipt['user_id']), username)
    user_id_plain_hint = f'\n👤 شناسه کاربر: <code>{int(receipt["user_id"])}</code>' if receipt.get('user_id') else ''

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if receipt['file_type'] == 'photo':
                msg = await bot.send_photo(admin_id, receipt['file_id'], caption=caption, reply_markup=markup, parse_mode='HTML')
            else:
                msg = await bot.send_document(admin_id, receipt['file_id'], caption=caption, reply_markup=markup, parse_mode='HTML')
            return int(msg.message_id)
        except Exception as exc:
            last_err = exc
            if _is_privacy_restricted_error(exc) and _has_user_link_button(markup):
                # The customer's privacy blocks the tg://user?id button; retry the
                # exact same message without it so approve/reject still arrive.
                caption = caption + user_id_plain_hint
                markup = _strip_user_link_buttons(markup)
                continue
            if isinstance(exc, TelegramRetryAfter):
                await asyncio.sleep(float(getattr(exc, 'retry_after', 1) or 1))
                continue
            if isinstance(exc, RETRYABLE_NETWORK_EXCEPTIONS):
                await asyncio.sleep(0.5)
                continue
            break

    if last_err:
        raise last_err
    return None


async def _notify_approvers(bot: Bot, receipt: dict) -> int:
    """Deliver the receipt to all configured approvers idempotently and update delivery state."""
    receipt_id = int(receipt['id'])
    approver_ids = _approver_ids()
    if not approver_ids:
        err_msg = 'هیچ ادمینی برای دریافت فیش تنظیم نشده است.'
        logging.error(f'Receipt #{receipt_id}: {err_msg}')
        db.record_system_error('card_transfer.notify_admin', err_msg, entity_type='card_receipt', entity_id=str(receipt_id), error_type='NoApproversConfigured')
        db.update_card_receipt_delivery(
            receipt_id,
            status='failed',
            error=err_msg,
            increment_attempts=True,
            next_retry_at=(db.now_iso_dt() + timedelta(seconds=60)).isoformat(timespec='seconds'),
        )
        return 0

    try:
        raw_notified = receipt.get('notified_admin_ids')
        notified_set = set(json.loads(raw_notified)) if raw_notified else set()
    except Exception:
        notified_set = set()

    try:
        raw_msg_ids = receipt.get('admin_message_ids')
        msg_ids_dict = json.loads(raw_msg_ids) if raw_msg_ids else {}
    except Exception:
        msg_ids_dict = {}

    sent = 0
    errors: list[str] = []

    for admin_id in approver_ids:
        if admin_id in notified_set:
            continue
        try:
            msg_id = await send_receipt_to_admin_safe(bot, admin_id, receipt)
            notified_set.add(admin_id)
            if msg_id:
                msg_ids_dict[str(admin_id)] = msg_id
            sent += 1
            example.com(f'Receipt #{receipt_id} sent to admin {admin_id} (msg_id={msg_id})')
        except Exception as exc:
            err_str = f'Admin {admin_id}: {exc}'
            errors.append(err_str)
            logging.error(f'Failed to send receipt #{receipt_id} to admin {admin_id}: {exc}')
            db.record_system_error('card_transfer.notify_admin', str(exc), entity_type='card_receipt', entity_id=str(receipt_id), error_type=type(exc).__name__)

    # Update delivery tracking
    all_done = len(notified_set) >= len(approver_ids)
    delivery_status = 'delivered' if (sent > 0 or all_done) else 'failed'
    err_summary = '; '.join(errors) if errors else None
    next_retry = (db.now_iso_dt() + timedelta(seconds=90)).isoformat(timespec='seconds') if errors else None

    db.update_card_receipt_delivery(
        receipt_id,
        status=delivery_status,
        notified_admin_ids=sorted(notified_set),
        admin_message_ids=msg_ids_dict,
        error=err_summary,
        increment_attempts=True,
        next_retry_at=next_retry,
    )
    return sent


async def process_payment_receipt(
    bot: Bot,
    *,
    user_id: int,
    kind: str,
    entity_id: int,
    amount: int,
    file_type: str,
    file_id: str,
    caption: str = '',
    order_type: str = 'new',
    service_id: int | None = None,
    package_id: str | None = None,
    package_name: str | None = None,
    customer_username: str | None = None,
    receipt_id: int | None = None,
) -> tuple[bool, dict, int]:
    """Shared single receipt processing helper for all flows (new purchase, renewal, wallet charge).
    
    Returns (success, receipt_row, approvers_notified_count).
    """
    example.com(f'Processing receipt for user={user_id}, kind={kind}, entity={entity_id}, amount={amount}, type={order_type}')
    
    # 1. Safely persist / update the receipt record
    receipt_id = db.create_or_update_card_receipt(
        user_id=user_id,
        kind=kind,
        entity_id=entity_id,
        amount_toman=amount,
        file_type=file_type,
        file_id=file_id,
        caption=caption,
        order_type=order_type,
        service_id=service_id,
        package_id=package_id,
        package_name=package_name,
        customer_username=customer_username,
        receipt_id=receipt_id,
    )

    # 2. Update parent entity status
    if kind == 'order':
        db.execute("UPDATE orders SET provider_status='receipt_pending', status='receipt_pending' WHERE id=?", (entity_id,))
    else:
        db.execute("UPDATE wallet_charge_requests SET provider_status='receipt_pending' WHERE id=?", (entity_id,))

    # 3. Deliver to admin(s)
    receipt = db.get_card_receipt(receipt_id) or {}
    sent = await _notify_approvers(bot, receipt)
    
    # Refresh receipt after delivery attempt
    receipt = db.get_card_receipt(receipt_id) or receipt

    delivery_ok = (sent > 0) or (receipt.get('admin_delivery_status') == 'delivered')
    db.add_log(None, 'card_receipt_submitted', 'card_receipt', str(receipt_id), {
        'user_id': user_id,
        'approvers_notified': sent,
        'delivery_status': receipt.get('admin_delivery_status'),
    })

    return delivery_ok, receipt, sent


async def _start_receipt_flow(callback: CallbackQuery, state: FSMContext, *, kind: str, entity_id: int, amount: int, order_type: str = 'new', service_id: int | None = None, package_id: str | None = None, package_name: str | None = None, customer_username: str | None = None) -> None:
    if not str(db.get_setting('card_number', '') or '').strip():
        await callback.answer('پرداخت کارت به کارت فعلا آماده نیست.', show_alert=True)
        return

    # Pre-create / register a pending card receipt in DB so that even if FSM is wiped on restart,
    # the pending payment request is safely persisted in the database with waiting_receipt status.
    receipt_id = db.create_or_update_card_receipt(
        user_id=callback.from_user.id,
        kind=kind,
        entity_id=entity_id,
        amount_toman=amount,
        file_type='',
        file_id='',
        caption='',
        order_type=order_type,
        service_id=service_id,
        package_id=package_id,
        package_name=package_name,
        customer_username=customer_username,
    )

    await state.set_state(CardTransferState.waiting_receipt)
    await state.update_data(
        card_receipt_id=receipt_id,
        card_kind=kind,
        card_entity_id=entity_id,
        card_amount=amount,
        card_order_type=order_type,
        card_service_id=service_id,
        card_package_id=package_id,
        card_package_name=package_name,
        card_customer_username=customer_username,
    )
    text, payment_template, payment_context = await _payment_text(callback.bot, callback.from_user, amount, entity_id)
    with edit_runtime.text_hint(
        setting_key='card_payment_text',
        template=payment_template,
        variables=payment_context,
        content_key='card_payment_text',
        label='متن اطلاعات کارت به کارت',
        allow_stickers=True,
    ):
        await callback.message.answer(text, parse_mode='HTML')

    prompt_template = str(db.get_setting('card_receipt_upload_prompt_text', CARD_RECEIPT_UPLOAD_PROMPT_DEFAULT) or CARD_RECEIPT_UPLOAD_PROMPT_DEFAULT)
    prompt_context = {
        'order_id': entity_id,
        'price': _format_toman(amount),
        'price_raw': int(amount),
        'payment_kind': kind,
    }
    prompt_text = prompt_template
    for key, value in prompt_context.items():
        prompt_text = prompt_text.replace('{' + key + '}', str(value))
    with edit_runtime.text_hint(
        setting_key='card_receipt_upload_prompt_text',
        template=prompt_template,
        variables=prompt_context,
        content_key='card_receipt_upload_prompt_text',
        label='متن درخواست ارسال فیش',
        allow_stickers=True,
    ):
        await callback.message.answer(prompt_text)
    await callback.answer()


@card_transfer_router.callback_query(F.data == 'cust_pay_card')
async def customer_pay_card(callback: CallbackQuery, state: FSMContext):
    if not bool(db.get_setting('payment_card_enabled', False)):
        await callback.answer('این روش پرداخت فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    final_toman = max(0, int(data.get('final_toman') or (int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))))
    wallet_used = int(data.get('wallet_used') or 0)
    payable = max(0, int(data.get('payable_toman') or (final_toman - wallet_used)))
    if payable <= 0 or not data.get('package_id') or not data.get('service_username'):
        await callback.answer('اطلاعات سفارش کامل نیست. دوباره از پیش فاکتور وارد شوید.', show_alert=True)
        return
    order_type = str(data.get('order_type') or 'new')
    service_id = data.get('renew_service_id')
    package_id = str(data.get('package_id'))
    package_name = str(data.get('package_name'))
    customer_username = str(data.get('service_username'))

    order_id = db.create_customer_order(
        user_id=callback.from_user.id,
        package_id=package_id,
        package_name=package_name,
        customer_username=customer_username,
        amount_toman=final_toman,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=0,
        wallet_used=wallet_used,
        status='pending_card_receipt',
        order_type=order_type,
        service_id=service_id,
    )
    db.set_order_payment_provider(order_id, 'card_transfer', payable, provider_status='waiting_receipt')
    await _start_receipt_flow(
        callback,
        state,
        kind='order',
        entity_id=order_id,
        amount=payable,
        order_type=order_type,
        service_id=service_id,
        package_id=package_id,
        package_name=package_name,
        customer_username=customer_username,
    )


@card_transfer_router.callback_query(F.data == 'cust_wallet_charge_card')
async def customer_wallet_card(callback: CallbackQuery, state: FSMContext):
    if not bool(db.get_setting('payment_card_enabled', False)):
        await callback.answer('این روش پرداخت فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    amount = int(data.get('wallet_charge_amount') or 0)
    if amount <= 0:
        await callback.answer('مبلغ شارژ پیدا نشد.', show_alert=True)
        return
    request_id = db.create_wallet_charge_request(callback.from_user.id, amount, 0, 'card_transfer')
    db.set_wallet_charge_provider(request_id, 'card_transfer', provider_status='waiting_receipt')
    await _start_receipt_flow(callback, state, kind='wallet', entity_id=request_id, amount=amount)


@card_transfer_router.message(CardTransferState.waiting_receipt, F.text == '/cancel')
async def receipt_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('پرداخت کارت به کارت لغو شد.', reply_markup=customer_keyboard())


@card_transfer_router.message(CardTransferState.waiting_receipt)
async def receive_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    receipt_id = data.get('card_receipt_id')
    kind = str(data.get('card_kind') or '')
    entity_id = int(data.get('card_entity_id') or 0)
    amount = int(data.get('card_amount') or 0)
    order_type = str(data.get('card_order_type') or 'new')
    service_id = data.get('card_service_id')
    package_id = data.get('card_package_id')
    package_name = data.get('card_package_name')
    customer_username = data.get('card_customer_username')

    # Fallback if state was lost (e.g., bot restarted while user was taking screenshot)
    if not kind or entity_id <= 0:
        active = db.find_active_card_receipt_for_user(message.from_user.id)
        if active:
            receipt_id = active['id']
            kind = active['kind']
            entity_id = int(active['entity_id'])
            amount = int(active['amount_toman'])
            order_type = str(active.get('order_type') or 'new')
            service_id = active.get('service_id')
            package_id = active.get('package_id')
            package_name = active.get('package_name')
            customer_username = active.get('customer_username')

    file_type = ''
    file_id = ''
    if message.photo:
        file_type = 'photo'
        file_id = message.photo[-1].file_id
    elif message.document:
        file_type = 'document'
        file_id = message.document.file_id
    else:
        await message.answer('لطفا تصویر فیش یا فایل فیش (PDF یا عکس به صورت فایل) را ارسال کنید. اگر منصرف شدید /cancel را بفرستید.')
        return

    if kind not in {'order', 'wallet'} or entity_id <= 0:
        await state.clear()
        await message.answer('درخواست پرداخت پیدا نشد. دوباره از بخش پرداخت شروع کنید.', reply_markup=customer_keyboard())
        return

    delivery_ok, receipt, sent = await process_payment_receipt(
        message.bot,
        user_id=message.from_user.id,
        kind=kind,
        entity_id=entity_id,
        amount=amount,
        file_type=file_type,
        file_id=file_id,
        caption=message.caption or '',
        order_type=order_type,
        service_id=service_id,
        package_id=package_id,
        package_name=package_name,
        customer_username=customer_username,
        receipt_id=receipt_id,
    )

    actual_receipt_id = int(receipt.get('id') or receipt_id or 0)
    await state.clear()

    if delivery_ok:
        submitted_template = str(db.get_setting('card_receipt_submitted_text', CARD_RECEIPT_SUBMITTED_DEFAULT) or CARD_RECEIPT_SUBMITTED_DEFAULT)
        submitted_context = {
            'receipt_id': actual_receipt_id,
            'order_id': entity_id,
            'price': _format_toman(amount),
            'price_raw': int(amount),
            'payment_kind': kind,
        }
        submitted_text = submitted_template
        for key, value in submitted_context.items():
            submitted_text = submitted_text.replace('{' + key + '}', str(value))
        with edit_runtime.text_hint(
            setting_key='card_receipt_submitted_text',
            template=submitted_template,
            variables=submitted_context,
            content_key='card_receipt_submitted_text',
            label='متن ثبت شدن فیش',
            allow_stickers=True,
        ):
            await message.answer(submitted_text, reply_markup=customer_keyboard())
    else:
        # Delivery failed temporarily but request is safely saved in database
        await message.answer(
            '⚠️ فیش شما در سیستم ذخیره شد، اما به دلیل اختلال موقت در ارتباط تلگرام، تحویل فوری به ادمین با تاخیر مواجه شد.\n\n'
            f'شماره پیگیری فیش: <code>{actual_receipt_id}</code>\n'
            'سیستم خودکار تا دقایقی دیگر فیش شما را مجددا ارسال می‌کند و نیازی به ارسال مجدد نیست.',
            parse_mode='HTML',
            reply_markup=customer_keyboard(),
        )


async def _complete_order(bot: Bot, receipt: dict) -> tuple[bool, str, str | None]:
    from modules import customer
    order_id = int(receipt['entity_id'])
    order = db.get_order(order_id)
    if not order:
        return False, 'سفارش پیدا نشد.', None
    if order.get('status') in {'service_ready', 'build_queued', 'building_service'}:
        # Already built — fetch existing link for admin edit
        existing_service = db.fetchone('SELECT subscription_url FROM user_services WHERE order_id=? AND telegram_id=? AND is_active=1 ORDER BY id DESC LIMIT 1', (order_id, int(order['user_id'] if order.get('user_id') else receipt['user_id'])))
        existing_sub = (existing_service or {}).get('subscription_url') if existing_service else None
        return True, 'سفارش قبلا پردازش شده است.', existing_sub
    payable = int(order.get('payable_toman') or receipt['amount_toman'] or 0)
    db.mark_order_provider_paid(order_id, 'card_transfer', payable, str(receipt['id']), {'receipt_id': receipt['id']})
    current = db.get_order(order_id) or order
    db.finalize_order_payment_effects(order_id)
    db.enqueue_service_job(order_id)
    db.execute("UPDATE orders SET status='build_queued' WHERE id=?", (order_id,))
    job = db.fetchone('SELECT * FROM service_jobs WHERE order_id=?', (order_id,))
    ok, service_message = await customer.process_one_service_job(job) if job else (False, 'صف ساخت پیدا نشد.')
    final = db.get_order(order_id) or current
    receipt_text = payment_engine.build_receipt(final, '\n' + service_message)
    sub_url: str | None = None
    # Receipt is attached to the QR photo caption by send_subscription_delivery.
    # Do not send a separate receipt message here.
    if ok:
        service = db.fetchone('SELECT * FROM user_services WHERE order_id=? AND telegram_id=? AND is_active=1 ORDER BY id DESC LIMIT 1', (order_id, int(final['user_id'])))
        if service:
            sub = service.get('subscription_url')
            sub_url = str(sub).strip() if sub else None
            if sub_url:
                class _DeliverySender:
                    def __init__(self, target_bot: Bot, chat_id: int):
                        self.bot = target_bot
                        self.chat_id = int(chat_id)

                    async def answer(self, text: str, **kwargs):
                        return await self.bot.send_message(self.chat_id, text, **kwargs)

                    async def answer_photo(self, photo, **kwargs):
                        return await self.bot.send_photo(self.chat_id, photo, **kwargs)

                sender = _DeliverySender(bot, int(final['user_id']))
                await customer.send_subscription_delivery(sender, service, sub_url=sub_url, title='✅ سرویس آماده شد', invoice_text=receipt_text)
            else:
                # No sub_url in DB — try to keep receipt for admin even if empty
                sub_url = None
        after_purchase = str(db.get_setting('after_purchase_text', '') or '')
        if after_purchase:
            user_row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (int(final['user_id']),)) or {}
            class U: pass
            u=U(); u.id=final['user_id']; u.first_name=user_row.get('first_name') or ''; u.username=user_row.get('username') or ''
            ctx = user_template_context(u, support=str(db.get_setting('support_username', '') or ''))
            ctx.update({'order_id': order_id, 'package_name': final.get('package_name',''), 'service_name': final.get('customer_username',''), 'price': final.get('amount_toman') or 0})
            rendered = await render_template_html(bot, after_purchase, ctx)
            await bot.send_message(int(final['user_id']), rendered, parse_mode='HTML', reply_markup=customer_keyboard())
    return ok, service_message, sub_url


@card_transfer_router.callback_query(F.data.startswith('cardadmin:approve:'))
async def approve_receipt(callback: CallbackQuery, state: FSMContext):
    if not _can_review(callback.from_user.id):
        await callback.answer('دسترسی بررسی فیش ندارید.', show_alert=True)
        return
    receipt_id = int(callback.data.rsplit(':', 1)[1])
    receipt = db.fetchone('SELECT * FROM card_receipts WHERE id=?', (receipt_id,))
    if not receipt:
        await callback.answer('فیش پیدا نشد.', show_alert=True); return
    if receipt['status'] != 'pending':
        await callback.answer(f'این فیش قبلا {receipt["status"]} شده است.', show_alert=True); return
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute("UPDATE card_receipts SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=? AND status='pending'", (callback.from_user.id, _now(), receipt_id))
        if cur.rowcount != 1:
            conn.rollback()
            await callback.answer('این فیش همزمان توسط ادمین دیگری بررسی شده است.', show_alert=True)
            return
        conn.commit()
    receipt = db.fetchone('SELECT * FROM card_receipts WHERE id=?', (receipt_id,))
    sub_url: str | None = None
    try:
        if receipt['kind'] == 'wallet':
            req = db.get_wallet_charge_request(int(receipt['entity_id']))
            if not req:
                raise RuntimeError('درخواست شارژ پیدا نشد.')
            db.mark_wallet_charge_paid_provider(int(receipt['entity_id']), 'card_transfer', str(receipt_id), {'receipt_id': receipt_id})
            balance = db.wallet_balance(int(receipt['user_id']))
            from modules.customer import wallet_charge_result_text
            await callback.bot.send_message(int(receipt['user_id']), wallet_charge_result_text(int(receipt['entity_id']), balance, '✅ فیش شما تایید شد و کیف پول شارژ شد.'), reply_markup=customer_keyboard())
        else:
            ok, result, sub = await _complete_order(callback.bot, receipt)
            sub_url = sub
            if not ok:
                db.record_system_error('card_transfer.approve_order', result, entity_type='order', entity_id=str(receipt['entity_id']), error_type='ServiceBuildError')
            # Fallback: if _complete_order didn't return link (e.g., already built), fetch from DB
            if not sub_url:
                svc = db.fetchone('SELECT subscription_url FROM user_services WHERE order_id=? AND telegram_id=? AND is_active=1 ORDER BY id DESC LIMIT 1', (int(receipt['entity_id']), int(receipt['user_id'])))
                if svc and svc.get('subscription_url'):
                    sub_url = str(svc.get('subscription_url')).strip()
        db.add_log(callback.from_user.id, 'card_receipt_approved', 'card_receipt', str(receipt_id))
        await callback.answer('فیش تایید شد ✅', show_alert=True)
        # Update admin's receipt message: show clickable user + link, and replace approve keyboard with link keyboard
        try:
            # Re-fetch to get approved status
            updated = db.fetchone('SELECT * FROM card_receipts WHERE id=?', (receipt_id,)) or receipt
            updated['status'] = 'approved'
            caption = _approved_caption_with_link(updated, sub_url)
            markup = _approved_keyboard(updated, sub_url)
            # Try edit caption (photo) first, fallback to edit text if it was document caption as text
            try:
                await callback.message.edit_caption(caption=caption, reply_markup=markup, parse_mode='HTML')
            except Exception as cap_exc:
                low = str(cap_exc).lower()
                if 'button_user_privacy_restricted' in low and _has_user_link_button(markup):
                    # Customer privacy blocks the tg://user?id button: retry without it.
                    markup = _strip_user_link_buttons(markup)
                    try:
                        await callback.message.edit_caption(caption=caption, reply_markup=markup, parse_mode='HTML')
                    except Exception:
                        try:
                            await callback.message.edit_reply_markup(reply_markup=markup)
                        except Exception:
                            pass
                elif 'there is no caption' in low or 'message has no caption' in low:
                    try:
                        await callback.message.edit_text(caption, reply_markup=markup, parse_mode='HTML')
                    except Exception:
                        # Last resort: send new message with link
                        try:
                            await callback.message.answer(caption, reply_markup=markup, parse_mode='HTML')
                        except Exception:
                            pass
                else:
                    # Try edit reply markup at least
                    try:
                        await callback.message.edit_reply_markup(reply_markup=markup)
                    except Exception:
                        pass
                    # If caption edit still failed but markup succeeded, at least try caption again without markup
                    try:
                        await callback.message.edit_caption(caption=caption, parse_mode='HTML')
                    except Exception:
                        pass
        except Exception:
            pass
    except Exception as exc:
        db.execute("UPDATE card_receipts SET status='needs_review' WHERE id=? AND status='approved'", (receipt_id,))
        db.record_system_error('card_transfer.approve', str(exc), entity_type='card_receipt', entity_id=str(receipt_id), error_type=type(exc).__name__)
        await callback.answer('تایید انجام نشد. خطا برای سوپرادمین ثبت شد.', show_alert=True)


@card_transfer_router.callback_query(F.data.startswith('cardadmin:reject:'))
async def reject_receipt_start(callback: CallbackQuery, state: FSMContext):
    if not _can_review(callback.from_user.id):
        await callback.answer('دسترسی بررسی فیش ندارید.', show_alert=True); return
    receipt_id = int(callback.data.rsplit(':', 1)[1])
    receipt = db.fetchone('SELECT * FROM card_receipts WHERE id=?', (receipt_id,))
    if not receipt or receipt['status'] != 'pending':
        await callback.answer('این فیش قابل رد کردن نیست.', show_alert=True); return
    await state.set_state(CardTransferState.waiting_reject_reason)
    await state.update_data(card_reject_receipt_id=receipt_id)
    await callback.message.answer(f'❌ علت رد فیش #{receipt_id} را ارسال کنید.\nاین متن برای مشتری ارسال می‌شود. برای لغو /cancel را بفرستید.')
    await callback.answer()


@card_transfer_router.message(CardTransferState.waiting_reject_reason, F.text == '/cancel')
async def reject_cancel(message: Message, state: FSMContext):
    await state.clear(); await message.answer('رد فیش لغو شد.')


@card_transfer_router.message(CardTransferState.waiting_reject_reason)
async def reject_receipt_reason(message: Message, state: FSMContext):
    if not _can_review(message.from_user.id if message.from_user else None):
        await state.clear(); return
    reason = (message.text or '').strip()
    if not reason:
        await message.answer('علت رد را به صورت متن ارسال کنید.'); return
    data = await state.get_data(); receipt_id = int(data.get('card_reject_receipt_id') or 0)
    receipt = db.fetchone('SELECT * FROM card_receipts WHERE id=?', (receipt_id,))
    if not receipt or receipt['status'] != 'pending':
        await state.clear(); await message.answer('فیش دیگر در انتظار بررسی نیست.'); return
    db.execute("UPDATE card_receipts SET status='rejected', reviewed_by=?, reviewed_at=?, reject_reason=? WHERE id=? AND status='pending'", (message.from_user.id, _now(), reason[:1000], receipt_id))
    if receipt['kind'] == 'order':
        db.execute("UPDATE orders SET status='payment_rejected', provider_status='rejected' WHERE id=?", (int(receipt['entity_id']),))
    else:
        db.execute("UPDATE wallet_charge_requests SET status='rejected', provider_status='rejected' WHERE id=?", (int(receipt['entity_id']),))
    await message.bot.send_message(int(receipt['user_id']), f'❌ فیش پرداخت شما رد شد.\n\nعلت: {html.escape(reason)}', parse_mode='HTML', reply_markup=customer_keyboard())
    db.add_log(message.from_user.id, 'card_receipt_rejected', 'card_receipt', str(receipt_id), {'reason': reason[:500]})
    await state.clear(); await message.answer('فیش رد شد و علت برای مشتری ارسال شد.')


@card_transfer_router.callback_query(F.data == 'cardadmin:approvers')
async def approvers_menu(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer('فقط سوپرادمین.', show_alert=True); return
    selected = set(_approver_ids())
    rows = db.fetchall("SELECT telegram_id, display_name, role, is_active FROM bot_admins WHERE is_active=1 AND role IN ('super_admin','admin') ORDER BY role, telegram_id")
    kb=[]
    for row in rows:
        uid=int(row['telegram_id']); mark='✅' if uid in selected else '◻️'; name=person_display(uid)
        locked = row.get('role') == 'super_admin'
        label=f'{mark} {name}' + (' | سوپرادمین' if locked else '')
        kb.append([InlineKeyboardButton(text=label, callback_data=f'cardadmin:approver_toggle:{uid}')])
    kb.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='adv:cat:card')])
    await callback.message.edit_text('👥 ادمین های مجاز بررسی فیش\n\nسوپرادمین ها همیشه دسترسی دارند. روی ادمین ها بزنید تا دسترسی شان روشن/خاموش شود.', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@card_transfer_router.callback_query(F.data.startswith('cardadmin:approver_toggle:'))
async def approver_toggle(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer('فقط سوپرادمین.', show_alert=True); return
    uid=int(callback.data.rsplit(':',1)[1])
    row=db.fetchone('SELECT role FROM bot_admins WHERE telegram_id=? AND is_active=1',(uid,))
    if not row: await callback.answer('ادمین پیدا نشد.',show_alert=True); return
    if row['role']=='super_admin': await callback.answer('سوپرادمین همیشه دسترسی دارد.',show_alert=True); return
    raw=db.get_setting('card_receipt_admin_ids',[]); selected={int(x) for x in raw if str(x).isdigit()} if isinstance(raw,list) else set()
    if uid in selected: selected.remove(uid)
    else: selected.add(uid)
    db.set_setting('card_receipt_admin_ids',sorted(selected))
    await approvers_menu(callback)


@card_transfer_router.message(EditableButtonFilter(BTN_CARD_RECEIPTS))
async def pending_receipts_message(message: Message):
    if not _can_review(message.from_user.id if message.from_user else None):
        await message.answer('شما دسترسی بررسی فیش های کارت به کارت را ندارید.')
        return
    rows=db.fetchall("SELECT * FROM card_receipts WHERE status='pending' ORDER BY id DESC LIMIT 20")
    if not rows:
        await message.answer('🧾 فیش در انتظار بررسی وجود ندارد.')
        return
    kb=[]
    for r in rows:
        kb.append([InlineKeyboardButton(text=f'🧾 #{r["id"]} | {person_display(r["user_id"])} | {_format_toman(int(r["amount_toman"]))}',callback_data=f'cardadmin:view:{r["id"]}')])
    await message.answer('🧾 فیش های در انتظار بررسی:', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@card_transfer_router.callback_query(F.data == 'cardadmin:pending')
async def pending_receipts(callback: CallbackQuery):
    if not _can_review(callback.from_user.id):
        await callback.answer('دسترسی ندارید.', show_alert=True); return
    rows=db.fetchall("SELECT * FROM card_receipts WHERE status='pending' ORDER BY id DESC LIMIT 20")
    if not rows:
        await callback.message.edit_text('🧾 فیش در انتظار بررسی وجود ندارد.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 برگشت',callback_data='adv:cat:card')]])); await callback.answer(); return
    kb=[]
    for r in rows:
        kb.append([InlineKeyboardButton(text=f'🧾 #{r["id"]} | {person_display(r["user_id"])} | {_format_toman(int(r["amount_toman"]))}',callback_data=f'cardadmin:view:{r["id"]}')])
    kb.append([InlineKeyboardButton(text='🔙 برگشت',callback_data='adv:cat:card')])
    await callback.message.edit_text('🧾 فیش های در انتظار بررسی:',reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()


@card_transfer_router.callback_query(F.data.startswith('cardadmin:view:'))
async def view_receipt(callback: CallbackQuery):
    if not _can_review(callback.from_user.id):
        await callback.answer('دسترسی ندارید.', show_alert=True); return
    rid=int(callback.data.rsplit(':',1)[1]); receipt=db.fetchone('SELECT * FROM card_receipts WHERE id=?',(rid,))
    if not receipt: await callback.answer('فیش پیدا نشد.',show_alert=True); return
    try:
        await send_receipt_to_admin_safe(callback.bot, callback.from_user.id, receipt)
        await callback.answer('فیش ارسال شد.')
    except Exception as exc:
        db.record_system_error('card_transfer.view_receipt',str(exc),entity_type='card_receipt',entity_id=str(rid),error_type=type(exc).__name__)
        await callback.answer('ارسال فیش ناموفق بود.',show_alert=True)
