from __future__ import annotations

import json
import html
import math
import re
import time
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message
from aiogram.exceptions import TelegramBadRequest

import api_client
import db
from config import settings
from keyboards import (
    BTN_BUY_SUBSCRIPTION,
    BTN_CUSTOMER_WALLET,
    BTN_REFERRAL,
    BTN_TEST_SERVICE,
    BTN_MY_SERVICES,
    BTN_RULES,
    BTN_SUPPORT,
    CustomerButtonFilter,
    customer_keyboard,
    customer_menu_title,
)
from utils import answer_template, edit_or_answer, friendly_error, user_template_context, render_template_html, render_custom_emoji_output
import modules.payment_engine as payment_engine
from modules import edit_runtime
from modules.qr_tools import send_qr_photo, make_qr_file
from modules import tetrapay, plisio_pay

customer_router = Router()

USERNAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{1,30}[A-Za-z0-9]$')


class CustomerBuyState(StatesGroup):
    waiting_username = State()
    waiting_coupon = State()


class CustomerTicketState(StatesGroup):
    waiting_subject = State()
    waiting_message = State()


class CustomerTransferState(StatesGroup):
    waiting_target_user_id = State()


class CustomerDeleteState(StatesGroup):
    waiting_service_delete_confirm = State()


class CustomerWalletState(StatesGroup):
    waiting_charge_amount = State()


def copy_code(value: Any) -> str:
    text = str(value if value is not None else '-')
    return '`' + text.replace('`', '') + '`'

def copy_html(value: Any) -> str:
    text = str(value if value is not None else '-')
    return '<code>' + html.escape(text).replace('`', '') + '</code>'


def qr_safe_caption(text: str, limit: int = 900) -> str:
    text = str(text or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + '…'


def short_alert(text: str, limit: int = 180) -> str:
    """Telegram callback answers have a small text limit.

    Never send raw provider/API exceptions here; they can be thousands of
    characters and Telegram returns MESSAGE_TOO_LONG, hiding the real error.
    Full details must be stored in system_errors instead.
    """
    value = str(text or '').replace('\n', ' ').strip()
    if len(value) <= limit:
        return value
    return value[:limit - 1] + '…'


async def safe_callback_answer(callback: CallbackQuery, text: str, *, show_alert: bool = False) -> None:
    try:
        await callback.answer(short_alert(text), show_alert=show_alert)
    except TelegramBadRequest:
        # Last-resort fallback: acknowledge the callback so the button does not
        # keep loading, even if Telegram rejects the alert text.
        try:
            await callback.answer('خطای موقت رخ داد. جزئیات برای ادمین ثبت شد.', show_alert=show_alert)
        except Exception:
            pass


async def send_subscription_delivery(message: Message, service: dict, sub_url: str | None = None, title: str | None = None, invoice_text: str | None = None) -> None:
    """Deliver subscription as exactly one QR photo with the subscription in caption.

    This path is shared by normal purchase, wallet/card payment, test service and link
    revoke. The mandatory copyable subscription URL remains the first caption line.
    """
    username = service.get('service_username') or service.get('panel_username') or '-'
    sub = sub_url or service.get('subscription_url')
    if not sub:
        try:
            refreshed, user_data = await refresh_service_from_panel(service)
            service = refreshed
            sub = service.get('subscription_url') or api_client.extract_subscription_url(user_data)
        except Exception:
            sub = None
    if not sub:
        await message.answer('لینک اشتراک برای این سرویس پیدا نشد.')
        return

    context = user_template_context(
        getattr(message, 'from_user', None),
        support=str(db.get_setting('support_username', settings.support_username) or ''),
    )
    context.update({
        'sub_link': str(sub),
        'service_name': str(username),
        'package_name': str(service.get('package_name') or 'نامشخص'),
        'order_id': str(service.get('order_id') or '-'),
        'price': str(service.get('amount_toman') or service.get('amount') or ''),
        'title': str(title or '✅ سرویس آماده شد'),
    })
    default_body = f'{title or "✅ سرویس آماده شد"}\n👤 نام سرویس: {{service_name}}'
    template = str(db.get_setting('service_ready_text', default_body) or default_body)
    body = await render_template_html(message.bot, template, context)
    # Never move this line below the editable body: the user must always see/copy it first.
    caption = f'🔗 {copy_html(sub)}\n\n{body}'
    if invoice_text:
        caption += f'\n\n{invoice_text}'
    # Render premium/custom emoji tokens stored by the admin editor before sending the caption.
    caption, _ = await render_custom_emoji_output(message.bot, caption, 'HTML')

    reply_markup = None
    session = edit_runtime.current_render_session()
    if session is not None:
        target = edit_runtime.register_text_target(
            stable_key='setting:service_ready_text',
            template=template,
            variables=context,
            setting_key='service_ready_text',
            content_key='service_ready_text',
            allow_stickers=False,
            label='متن تحویل سرویس (زیر لینک ساب)',
        )
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text='✏️ تغییر این متن',
                callback_data=f'editmode:text:{target.token}',
                style='primary',
            )
        ]])

    bot = getattr(message, 'bot', None)
    chat = getattr(message, 'chat', None)
    chat_id = getattr(chat, 'id', None) if chat is not None else getattr(message, 'chat_id', None)

    async def _send_photo_once(photo_file, photo_caption: str, *, parse_mode: str | None = 'HTML'):
        # IMPORTANT: subscription delivery is intentionally atomic.  Never send the
        # URL in a second message after the QR.  Every successful path below is one
        # Telegram photo message with the subscription URL in that photo's caption.
        # This caption is already finalized (Premium Emoji rendered + mandatory
        # copyable subscription URL on line 1). Bypass the generic live-edit output
        # hook so an old call-site override can never replace/split this atomic delivery.
        with edit_runtime.output_passthrough():
            if bot is not None and chat_id is not None:
                return await bot.send_photo(
                    chat_id=int(chat_id),
                    photo=photo_file,
                    caption=photo_caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            return await message.answer_photo(
                photo_file,
                caption=photo_caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )

    try:
        qr_file = make_qr_file(sub, filename=f'subscription_{username}.png')
        await _send_photo_once(qr_file, caption, parse_mode='HTML')
        return
    except Exception as first_exc:
        # Editable text can become too long for Telegram's media-caption limit, or an
        # admin may accidentally save broken HTML.  Do NOT fall back to a separate text
        # message. Retry the SAME QR delivery with a compact, HTML-safe caption instead.
        try:
            compact_title = html.escape(str(title or '✅ سرویس آماده شد'))
            compact_name = html.escape(str(username))
            compact_caption = (
                f'🔗 {copy_html(sub)}\n\n'
                f'{compact_title}\n'
                f'👤 نام سرویس: {compact_name}'
            )
            qr_file = make_qr_file(sub, filename=f'subscription_{username}_retry.png')
            await _send_photo_once(qr_file, compact_caption, parse_mode='HTML')
            try:
                db.record_system_error(
                    'customer.send_subscription_delivery.caption_retry',
                    str(first_exc),
                    user_id=getattr(getattr(message, 'from_user', None), 'id', None),
                    entity_type='service',
                    entity_id=str(service.get('id') or username),
                    error_type=type(first_exc).__name__,
                )
            except Exception:
                pass
            return
        except Exception as retry_exc:
            try:
                db.record_system_error(
                    'customer.send_subscription_delivery',
                    f'primary={first_exc}; retry={retry_exc}',
                    user_id=getattr(getattr(message, 'from_user', None), 'id', None),
                    entity_type='service',
                    entity_id=str(service.get('id') or username),
                    error_type=type(retry_exc).__name__,
                )
            except Exception:
                pass
            # Never produce a split QR + subscription-link delivery. If even the compact
            # photo cannot be sent, report a generic failure only; a later retry can send
            # the complete atomic photo again.
            await message.answer('⚠️ ارسال QR و لینک اشتراک ناموفق بود. لطفاً دوباره تلاش کنید.')
            return


async def send_config_delivery(message: Message, service: dict, config_link: str, index: int = 0) -> None:
    username = service.get('service_username') or service.get('panel_username') or '-'
    label = api_client.config_display_name(config_link, index + 1)
    # Config QR delivery is atomic too: never send config text first and QR second.
    # The copyable config link lives inside the QR photo caption.
    caption = (
        f'🔗 {copy_html(config_link)}\n\n'
        f'📄 کانفیگ {copy_html(label)}\n'
        f'👤 سرویس: {copy_html(username)}'
    )
    sent = await send_qr_photo(
        message,
        config_link,
        caption=caption,
        filename=f'config_{username}_{index + 1}.png',
        parse_mode='HTML',
    )
    if not sent:
        await message.answer('⚠️ امکان ساخت QR برای این کانفیگ وجود نداشت.')


def format_toman(amount: int | None) -> str:
    return f'{int(amount or 0):,} تومان'


def format_bytes(value: Any) -> str:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return 'نامشخص'
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    result = float(size)
    for unit in units:
        if result < 1024 or unit == units[-1]:
            if unit == 'B':
                return f'{size} B'
            return f'{result:.2f} {unit}'
        result /= 1024
    return f'{size} B'


def format_timestamp(value: Any) -> str:
    if value in (None, '', 0, '0'):
        return 'نامشخص'
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts).strftime('%Y/%m/%d %H:%M:%S')
    except Exception:
        try:
            return datetime.fromisoformat(str(value)).strftime('%Y/%m/%d %H:%M:%S')
        except Exception:
            return str(value)


def format_remaining(expire: Any) -> str:
    try:
        exp = api_client.panel_timestamp(expire)
        now = int(time.time())
        if exp <= 0:
            return 'نامشخص'
        seconds = exp - now
        if seconds <= 0:
            return 'تمام شده'
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        return f'{days} روز {hours} ساعت {minutes} دقیقه دیگر'
    except Exception:
        return 'نامشخص'


def validate_customer_username(username: str) -> str:
    username = (username or '').strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(
            'نام کاربری معتبر نیست.\n\n'
            '⚠️ نام کاربری باید بدون کاراکترهای اضافه مانند @ ، فاصله ، خط تیره باشد.\n'
            '⚠️ نام کاربری باید انگلیسی باشد.\n'
            '✅ نمونه صحیح: ali12 | mahdi | ws1_ksdf\n'
            '❌ نمونه اشتباه: ali_ | tele@ | _mahdi | محسن'
        )
    if '__' in username:
        raise ValueError('نام کاربری نباید شامل دو آندرلاین پشت سر هم باشد.')
    return username


def build_panel_note_for_order(order: dict) -> str:
    """Build panel note as '@username - ID_CODE' per user request.

    - username: Telegram @username if available (from bot_users), otherwise customer_username (service username)
    - ID_CODE: Telegram numeric ID (order['user_id'])
    Replaces old 'Telegram order #XX' format.
    """
    user_id = order.get('user_id')
    try:
        user_id_int = int(user_id) if user_id is not None else 0
    except Exception:
        user_id_int = 0
    customer_username = str(order.get('customer_username') or '').strip().lstrip('@')
    tg_username: str | None = None
    if user_id_int:
        try:
            row = db.fetchone('SELECT username FROM bot_users WHERE telegram_id=?', (user_id_int,))
            if row and row.get('username'):
                tg_username = str(row['username']).strip().lstrip('@')
                if not tg_username:
                    tg_username = None
        except Exception:
            tg_username = None
    at_name = (tg_username or customer_username or '').strip().lstrip('@')
    if at_name and user_id_int:
        return f'@{at_name} - {user_id_int}'
    if at_name:
        return f'@{at_name}'
    if user_id_int:
        return str(user_id_int)
    return 'unknown'


def build_panel_note_for_test(user_id: int) -> str:
    """Same '@username - ID_CODE' format for test services."""
    try:
        uid = int(user_id)
    except Exception:
        uid = 0
    tg_username: str | None = None
    if uid:
        try:
            row = db.fetchone('SELECT username FROM bot_users WHERE telegram_id=?', (uid,))
            if row and row.get('username'):
                tg_username = str(row['username']).strip().lstrip('@')
                if not tg_username:
                    tg_username = None
        except Exception:
            tg_username = None
    if tg_username and uid:
        return f'@{tg_username} - {uid}'
    if tg_username:
        return f'@{tg_username}'
    if uid:
        return str(uid)
    return 'test'


def get_pack_meta(template_id: str) -> dict:
    row = db.fetchone('SELECT * FROM package_settings WHERE template_id=?', (str(template_id),))
    if row:
        return row
    return {'template_id': str(template_id), 'price_amount': 0, 'price_currency': 'TOMAN', 'sort_order': 1000, 'is_visible': 1}


def pack_title(pack: dict) -> str:
    return pack.get('name') or f"بسته {pack.get('id')}"


def visible_pack_rows(packs: list[dict]) -> list[tuple[dict, dict]]:
    result = []
    metas = {str(r['template_id']): r for r in db.fetchall('SELECT * FROM package_settings')}
    for pack in packs:
        template_id = str(pack.get('id'))
        meta = metas.get(template_id) or get_pack_meta(template_id)
        if not int(meta.get('is_visible', 1)):
            continue
        price = int(meta.get('price_amount') or 0)
        if price <= 0:
            continue
        result.append((pack, meta))
    result.sort(key=lambda item: (int(item[1].get('sort_order', 1000)), item[0].get('id', 0)))
    return result


def customer_packs_keyboard(items: list[tuple[dict, dict]]) -> InlineKeyboardMarkup:
    rows = []
    for pack, meta in items:
        template_id = str(pack.get('id'))
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {pack_title(pack)} | {format_toman(meta.get('price_amount'))}",
                callback_data=f'cust_pack:{template_id}',
            )
        ])
    rows.append([InlineKeyboardButton(text='❌ انصراف و بازگشت', callback_data='cust_cancel')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def customer_renew_packs_keyboard(items: list[tuple[dict, dict]], service_id: int) -> InlineKeyboardMarkup:
    rows = []
    for pack, meta in items:
        template_id = str(pack.get('id'))
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {pack_title(pack)} | {format_toman(meta.get('price_amount'))}",
                callback_data=f'cust_renew_pack:{service_id}:{template_id}',
            )
        ])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت به سرویس', callback_data=f'cust_service:{service_id}')])
    rows.append([InlineKeyboardButton(text='❌ انصراف و منوی اصلی', callback_data='cust_cancel')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preinvoice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 پرداخت و دریافت سرویس', callback_data='cust_pay_menu')],
        [InlineKeyboardButton(text='🎁 اعمال کد تخفیف', callback_data='cust_coupon')],
        [InlineKeyboardButton(text='❌ انصراف و بازگشت به منوی اصلی', callback_data='cust_cancel')],
    ])


def coupon_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔙 بازگشت به پیش فاکتور', callback_data='cust_preinvoice')],
        [InlineKeyboardButton(text='❌ انصراف و بازگشت به منوی اصلی', callback_data='cust_cancel')],
    ])


def _gateway_enabled(key: str, default: bool = True) -> bool:
    return bool(db.get_setting(key, default))


def payment_keyboard(stars_amount: int, final_toman: int, wallet_used: int = 0, payable_toman: int | None = None) -> InlineKeyboardMarkup:
    payable_toman = final_toman if payable_toman is None else payable_toman
    rows = []
    if int(final_toman) <= 0:
        rows.append([InlineKeyboardButton(text='✅ دریافت رایگان سرویس', callback_data='cust_free_activate')])
    elif int(payable_toman) <= 0:
        rows.append([InlineKeyboardButton(text='✅ دریافت سرویس با کیف پول', callback_data='cust_wallet_activate')])
    else:
        if _gateway_enabled('payment_stars_enabled', True) and int(stars_amount) > 0:
            rows.append([InlineKeyboardButton(text=f'⭐ پرداخت با استارز معادل {stars_amount:,} استار', callback_data='cust_pay_stars')])
        if _gateway_enabled('payment_tetrapay_enabled', True):
            rows.append([InlineKeyboardButton(text='💳 پرداخت با درگاه تتراپی', callback_data='cust_pay_tetrapay')])
        if _gateway_enabled('payment_plisio_enabled', True):
            rows.append([InlineKeyboardButton(text='₿ پرداخت با رمز ارز', callback_data='cust_pay_crypto')])
        if _gateway_enabled('payment_card_enabled', False):
            rows.append([InlineKeyboardButton(text=str(db.get_setting('card_payment_button_text', '💳 کارت به کارت')), callback_data='cust_pay_card')])
        if not rows:
            rows.append([InlineKeyboardButton(text='در حال حاضر روش پرداخت فعالی وجود ندارد', callback_data='cust_no_payment')])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت به پیش فاکتور', callback_data='cust_preinvoice')])
    rows.append([InlineKeyboardButton(text='❌ انصراف و بازگشت به منوی اصلی', callback_data='cust_cancel')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_list_keyboard(services: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for service in services:
        rows.append([
            InlineKeyboardButton(
                text=f"📡 {service.get('service_username')} | {service.get('package_name') or 'سرویس'}",
                callback_data=f"cust_service:{service['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت به منوی اصلی', callback_data='cust_cancel')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_detail_keyboard(service_id: int) -> InlineKeyboardMarkup:
    specs = [
        ('service_btn_refresh_text','service_btn_refresh_style','🔄 بروزرسانی',f'cust_service_refresh:{service_id}'),
        ('service_btn_configs_text','service_btn_configs_style','📋 دریافت کانفیگ ها',f'cust_service_configs:{service_id}'),
        ('service_btn_revoke_text','service_btn_revoke_style','♻️ تغییر لینک',f'cust_service_revoke:{service_id}'),
        ('service_btn_renew_text','service_btn_renew_style','🔁 تمدید سرویس',f'cust_service_renew:{service_id}'),
        ('service_btn_auto_renew_text','service_btn_auto_renew_style','🤖 تمدید خودکار',f'cust_service_auto_renew:{service_id}'),
        ('service_btn_transfer_text','service_btn_transfer_style','👤 انتقال سرویس',f'cust_service_transfer:{service_id}'),
        ('service_btn_delete_text','service_btn_delete_style','🗑 حذف سرویس',f'cust_service_delete_start:{service_id}'),
        ('service_btn_back_text','service_btn_back_style','🔙 بازگشت به سرویس های من','cust_services'),
    ]
    rows=[]
    for text_key,style_key,default,callback_data in specs:
        text=str(db.get_setting(text_key,default) or default)
        style=str(db.get_setting(style_key,'default') or 'default')
        rows.append([InlineKeyboardButton(text=text, callback_data=callback_data, style=(None if style=='default' else style))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def configs_keyboard(service_id: int, links: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for index, link in enumerate(links[:30], start=1):
        label = api_client.config_display_name(link, index)
        rows.append([InlineKeyboardButton(text=f'📄 {label}', callback_data=f'cust_config:{service_id}:{index-1}')])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت به سرویس', callback_data=f'cust_service:{service_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_delete_first_keyboard(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⚠️ مرحله اول تایید حذف', callback_data=f'cust_service_delete_confirm1:{service_id}')],
        [InlineKeyboardButton(text='🔙 انصراف', callback_data=f'cust_service:{service_id}')],
    ])


def service_delete_second_keyboard(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🧨 تایید دوم و ادامه حذف', callback_data=f'cust_service_delete_confirm2:{service_id}')],
        [InlineKeyboardButton(text='🔙 انصراف', callback_data=f'cust_service:{service_id}')],
    ])


def ticket_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data='cust_cancel')]])


def wallet_customer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ شارژ کیف پول', callback_data='cust_wallet_charge')],
        [InlineKeyboardButton(text='🧾 تاریخچه کیف پول', callback_data='cust_wallet_history')],
    ])


def wallet_charge_method_keyboard(stars_amount: int) -> InlineKeyboardMarkup:
    rows = []
    if _gateway_enabled('payment_stars_enabled', True) and int(stars_amount) > 0:
        rows.append([InlineKeyboardButton(text=f'⭐ شارژ با استارز معادل {stars_amount:,} استار', callback_data='cust_wallet_charge_stars')])
    if _gateway_enabled('payment_tetrapay_enabled', True):
        rows.append([InlineKeyboardButton(text='💳 شارژ با درگاه تتراپی', callback_data='cust_wallet_charge_tetrapay')])
    if _gateway_enabled('payment_plisio_enabled', True):
        rows.append([InlineKeyboardButton(text='₿ شارژ با رمز ارز', callback_data='cust_wallet_charge_crypto')])
    if _gateway_enabled('payment_card_enabled', False):
        rows.append([InlineKeyboardButton(text=str(db.get_setting('card_payment_button_text', '💳 کارت به کارت')), callback_data='cust_wallet_charge_card')])
    if not rows:
        rows.append([InlineKeyboardButton(text='در حال حاضر روش شارژ فعالی وجود ندارد', callback_data='cust_no_payment')])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت به کیف پول', callback_data='cust_wallet_home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)



@customer_router.callback_query(F.data == 'cust_no_payment')
async def customer_no_payment(callback: CallbackQuery):
    await callback.answer('در حال حاضر روش پرداخت فعالی وجود ندارد. لطفا بعدا دوباره تلاش کنید.', show_alert=True)


def tetrapay_pay_link_keyboard(payment_url: str, check_callback: str, back_callback: str = 'cust_pay_menu') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🌐 ورود به صفحه پرداخت تتراپی', url=payment_url)],
        [InlineKeyboardButton(text='✅ بررسی وضعیت پرداخت', callback_data=check_callback)],
        [InlineKeyboardButton(text='🔙 بازگشت', callback_data=back_callback)],
    ])


def crypto_pay_link_keyboard(payment_url: str, check_callback: str, back_callback: str = 'cust_pay_menu') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='₿ ورود به صفحه پرداخت رمز ارز', url=payment_url)],
        [InlineKeyboardButton(text='✅ بررسی وضعیت پرداخت', callback_data=check_callback)],
        [InlineKeyboardButton(text='🔙 بازگشت', callback_data=back_callback)],
    ])


def test_service_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ دریافت سرویس تست', callback_data='cust_test_get')],
        [InlineKeyboardButton(text='❌ بازگشت', callback_data='cust_cancel')],
    ])


PACKAGE_USERNAME_PROMPT_DEFAULT = (
    '📦 بسته انتخابی: {package_name}\n\n'
    'لطفا نام کاربری سرویس را ارسال کنید.\n\n'
    '⚠️ نام کاربری باید بدون کاراکترهای اضافه مانند @ ، فاصله ، خط تیره باشد.\n'
    '⚠️ نام کاربری باید انگلیسی باشد.\n'
    '✅ نام کاربری های صحیح: ali12 | mahdi | ws1_ksdf\n'
    '❌ نام کاربری های نادرست: ali_ | tele@ | _mahdi | محسن'
)


def format_duration_seconds(value: Any) -> str:
    try:
        seconds = max(0, int(value or 0))
    except (TypeError, ValueError):
        return 'نامشخص'
    if seconds <= 0:
        return 'نامشخص'
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f'{days} روز')
    if hours:
        parts.append(f'{hours} ساعت')
    if minutes:
        parts.append(f'{minutes} دقیقه')
    if secs and not days:
        parts.append(f'{secs} ثانیه')
    return ' '.join(parts) or f'{seconds} ثانیه'


def package_username_prompt_context(pack: dict, meta: dict) -> dict[str, Any]:
    price_raw = int(meta.get('price_amount') or 0)
    data_limit_raw = int(pack.get('data_limit') or 0)
    duration_seconds = int(pack.get('expire_duration') or 0)
    package_name = pack_title(pack)
    package_id = str(pack.get('id') or meta.get('template_id') or '')
    username_prefix = str(pack.get('username_prefix') or '')
    username_suffix = str(pack.get('username_suffix') or '')
    hwid_limit = pack.get('hwid_limit')
    ctx = {
        'package_name': package_name,
        'package_id': package_id,
        'package_price': format_toman(price_raw),
        'package_price_raw': price_raw,
        'package_data_limit': format_bytes(data_limit_raw),
        'package_data_limit_raw': data_limit_raw,
        'package_duration': format_duration_seconds(duration_seconds),
        'package_duration_seconds': duration_seconds,
        'username_prefix': username_prefix or 'ندارد',
        'username_suffix': username_suffix or 'ندارد',
        'hwid_limit': hwid_limit if hwid_limit not in (None, '') else 'دیفالت سرور',
        'package_status': str(pack.get('status') or ''),
        # Short aliases are intentionally included for easier templates.
        'price': format_toman(price_raw),
        'price_raw': price_raw,
        'data_limit': format_bytes(data_limit_raw),
        'data_limit_raw': data_limit_raw,
        'duration': format_duration_seconds(duration_seconds),
        'duration_seconds': duration_seconds,
    }
    return ctx


def username_rules_text(pack: dict, meta: dict, user: Any = None) -> tuple[str, str, dict[str, Any]]:
    template = str(db.get_setting('package_username_prompt_text', PACKAGE_USERNAME_PROMPT_DEFAULT) or PACKAGE_USERNAME_PROMPT_DEFAULT)
    context = package_username_prompt_context(pack, meta)
    if user is not None:
        context.update(user_template_context(
            user,
            support=str(db.get_setting('support_username', settings.support_username) or ''),
        ))
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace('{' + key + '}', str(value))
    return rendered, template, context


def star_rate_info() -> tuple[int, str]:
    mode = db.get_setting('stars_rate_mode', 'manual')
    if mode == 'auto':
        auto_rate = int(db.get_setting('auto_star_toman_rate', 0) or 0)
        if auto_rate > 0:
            return auto_rate, 'auto'
    manual_rate = int(db.get_setting('star_toman_rate', 0) or 0)
    return manual_rate, 'manual'


def toman_to_stars(amount_toman: int) -> tuple[int, int, str]:
    rate, mode = star_rate_info()
    if int(amount_toman) <= 0:
        return 0, rate, mode
    if rate <= 0:
        raise ValueError('نرخ تبدیل Stars تنظیم نشده است. لطفا ادمین نرخ هر ۱ استار به تومان را تنظیم کند.')
    stars = int(math.ceil(int(amount_toman) / rate))
    return stars, rate, mode


def coupon_is_valid_for(coupon: dict, package_id: str) -> tuple[bool, str]:
    if not coupon:
        return False, 'کد تخفیف پیدا نشد.'
    if not int(coupon.get('is_active', 0)):
        return False, 'این کد تخفیف غیرفعال است.'
    max_uses = coupon.get('max_uses')
    if max_uses is not None and int(max_uses) > 0 and int(coupon.get('used_count') or 0) >= int(max_uses):
        return False, 'ظرفیت استفاده از این کد تمام شده است.'
    package_limit = coupon.get('package_id')
    if package_limit and str(package_limit) != str(package_id):
        return False, 'این کد برای بسته انتخاب شده قابل استفاده نیست.'
    expires_at = coupon.get('expires_at')
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) < datetime.utcnow():
                return False, 'تاریخ انقضای این کد گذشته است.'
        except Exception:
            pass
    return True, 'ok'


def calculate_discount(price: int, coupon: dict | None) -> int:
    if not coupon:
        return 0
    percent = int(coupon.get('discount_percent') or 0)
    fixed = int(coupon.get('discount_amount') or 0)
    discount = int(price * percent / 100) + fixed
    return max(0, min(price, discount))


def build_preinvoice_text(data: dict) -> str:
    price = int(data.get('price_toman') or 0)
    discount = int(data.get('discount_amount') or 0)
    final_price = max(0, price - discount)
    coupon_code = data.get('coupon_code') or 'ندارد'
    service_id = data.get('renew_service_id')
    mode_text = 'تمدید سرویس' if service_id else 'خرید سرویس'
    return (
        f'🧾 پیش فاکتور {mode_text}\n\n'
        f"بسته: {copy_code(data.get('package_name'))}\n"
        f"نام کاربری سرویس: {copy_code(data.get('service_username'))}\n"
        f"قیمت اصلی: {format_toman(price)}\n"
        f"کد تخفیف: {copy_code(coupon_code)}\n"
        f"مبلغ تخفیف: {format_toman(discount)}\n"
        f"مبلغ نهایی: {format_toman(final_price)}\n"
    )


def service_status_label(status: Any) -> str:
    status = str(status or '').lower()
    if status in ('active', 'enabled'):
        return '✅ فعال'
    if status in ('disabled', 'inactive'):
        return '🚫 غیرفعال'
    if status in ('expired',):
        return '⏳ منقضی شده'
    if status in ('limited',):
        return '💢 محدود شده'
    return status or 'نامشخص'


SERVICE_DETAIL_TEXT_DEFAULT = (
    '🔗 {sub_link}\n'
    '📊 وضعیت سرویس : {status}\n'
    '👤 نام سرویس : {service_name}\n\n'
    '🌍 موقعیت سرویس : 🌟 پلن حجمی\n'
    '🗂 نام محصول : {package_name}\n'
    '🧾 شماره سفارش : {order_id}\n'
    '🗓 تاریخ خرید : {purchase_date}\n\n'
    '🔋 ترافیک : {data_limit}\n'
    '📥 حجم مصرفی : {used_traffic}\n'
    '💢 حجم باقی مانده : {remaining_traffic} ({remaining_percent}%)\n\n'
    '📅 تاریخ اتمام : {expire_at} ({expire_remaining})\n\n'
    '📶 آخرین زمان اتصال : {last_online}\n'
    '🔄 آخرین زمان آپدیت لینک اشتراک : {subscription_updated}\n'
    '#️⃣ کلاینت متصل شده : {client}\n\n'
    '💡 برای قطع دسترسی دیگران کافیست روی گزینه «تغییر لینک» کلیک کنید.'
)

SERVICE_DETAIL_COPY_VARS = {'sub_link', 'service_name', 'order_id', 'purchase_date', 'subscription_updated', 'client'}

def _service_detail_context(service: dict, user_data: dict | None = None, sub_url: str | None = None) -> dict[str, str]:
    user_data = user_data or {}
    data_limit = int(user_data.get('data_limit') or service.get('data_limit') or 0)
    used = int(user_data.get('used_traffic') or service.get('used_traffic') or 0)
    remaining = max(0, data_limit - used) if data_limit > 0 else 0
    percent = (remaining / data_limit * 100) if data_limit > 0 else 0
    expire = user_data.get('expire') or service.get('expire')
    online_at = user_data.get('online_at') or user_data.get('last_online') or service.get('online_at')
    sub_updated = service.get('last_subscription_update') or service.get('updated_at') or '-'
    client = user_data.get('client_type') or user_data.get('last_client') or user_data.get('user_agent') or service.get('client_type') or 'نامشخص'
    return {
        'sub_link': str(sub_url or service.get('subscription_url') or api_client.extract_subscription_url(user_data) or '-'),
        'status': service_status_label(user_data.get('status') or service.get('status')),
        'service_name': str(service.get('service_username') or '-'),
        'package_name': str(service.get('package_name') or 'نامشخص'),
        'order_id': str(service.get('order_id') or '-'),
        'purchase_date': str(service.get('created_at') or 'نامشخص'),
        'data_limit': format_bytes(data_limit),
        'used_traffic': format_bytes(used),
        'remaining_traffic': format_bytes(remaining),
        'remaining_percent': f'{percent:.2f}',
        'expire_at': format_timestamp(expire),
        'expire_remaining': format_remaining(expire),
        'last_online': format_timestamp(online_at),
        'subscription_updated': str(sub_updated),
        'client': str(client),
    }

def _render_service_detail_plain(template: str, context: dict[str, str]) -> str:
    text = str(template or SERVICE_DETAIL_TEXT_DEFAULT)
    for key, value in context.items():
        text = text.replace('{' + key + '}', str(value))
    return text

def _render_service_detail_html(template: str, context: dict[str, str]) -> str:
    # Escape admin text first, then insert variables safely. Copy-sensitive values
    # stay inside <code> so the subscription link and identifiers remain easy to copy.
    text = html.escape(str(template or SERVICE_DETAIL_TEXT_DEFAULT))
    for key, value in context.items():
        safe = html.escape(str(value))
        if key in SERVICE_DETAIL_COPY_VARS:
            safe = '<code>' + safe.replace('`', '') + '</code>'
        text = text.replace('{' + key + '}', safe)
    # Keep premium/custom emoji support identical to the global text renderer.
    # Accept both [ID] tokens and pasted Telegram tg-emoji entities.
    text = re.sub(r"\\[(\\d{10,24})\\]", r'<tg-emoji emoji-id="\\1">⭐</tg-emoji>', text)
    text = re.sub(
        r'&lt;tg-emoji\\s+emoji-id=[\'\"](\\d{10,24})[\'\"][^&]*?&gt;.*?&lt;/tg-emoji&gt;',
        r'<tg-emoji emoji-id="\\1">⭐</tg-emoji>',
        text,
    )
    return text

def build_service_detail_text(service: dict, user_data: dict | None = None) -> str:
    template = str(db.get_setting('service_detail_text', SERVICE_DETAIL_TEXT_DEFAULT) or SERVICE_DETAIL_TEXT_DEFAULT)
    return _render_service_detail_plain(template, _service_detail_context(service, user_data))

def build_service_detail_caption(service: dict, user_data: dict | None = None, sub_url: str | None = None) -> str:
    """Editable photo caption for service status."""
    template = str(db.get_setting('service_detail_text', SERVICE_DETAIL_TEXT_DEFAULT) or SERVICE_DETAIL_TEXT_DEFAULT)
    return _render_service_detail_html(template, _service_detail_context(service, user_data, sub_url=sub_url))


async def show_service_detail_photo(callback: CallbackQuery, service: dict, user_data: dict | None = None) -> bool:
    """Replace the current customer screen with QR photo + full service caption."""
    user_data = user_data or {}
    sub = service.get('subscription_url') or api_client.extract_subscription_url(user_data)
    if not sub:
        return False
    detail_context = _service_detail_context(service, user_data, sub_url=sub)
    template = str(db.get_setting('service_detail_text', SERVICE_DETAIL_TEXT_DEFAULT) or SERVICE_DETAIL_TEXT_DEFAULT)
    caption = _render_service_detail_html(template, detail_context)
    # Expose the ORIGINAL template and all variables to live edit mode while keeping
    # the normal customer output as one QR photo with caption + management buttons.
    from modules.edit_runtime import text_hint
    with text_hint(
        setting_key='service_detail_text',
        template=template,
        variables=detail_context,
        content_key='service_detail_text',
        label='متن جزئیات/وضعیت سرویس',
        allow_stickers=False,
    ):
        sent = await send_qr_photo(
            callback.message,
            sub,
            caption=caption,
            filename=f"service_{service.get('service_username') or service.get('id') or 'status'}.png",
            parse_mode='HTML',
            reply_markup=service_detail_keyboard(int(service['id'])),
        )
    if sent:
        try:
            await callback.message.delete()
        except Exception:
            pass
    return sent

def links_from_service(service: dict) -> list[str]:
    try:
        links = json.loads(service.get('links_json') or '[]')
        if isinstance(links, list):
            return [str(item) for item in links if str(item).strip()]
    except Exception:
        pass
    return []



async def get_service_config_links(service: dict) -> list[str]:
    # Always try the panel API first, based on the service id -> stored panel username.
    # Saved links are only a fallback, so the user gets the latest config list.
    username = service.get('panel_username') or service.get('service_username')
    links: list[str] = []
    user_data: dict = {}
    sub_url = service.get('subscription_url')

    if username:
        try:
            links, user_data, server_sub_url = await api_client.get_user_config_links(str(username))
            sub_url = server_sub_url or sub_url
        except Exception:
            links = []
            user_data = {}

    if not links:
        links = links_from_service(service)

    if not links and sub_url:
        try:
            links = await api_client.fetch_subscription_configs(sub_url)
        except Exception:
            links = []

    if links or user_data or sub_url:
        try:
            db.update_user_service_snapshot(int(service['id']), user_data or {}, sub_url, links)
        except Exception:
            pass

    return list(dict.fromkeys(item for item in links if item))


async def show_customer_home(message: Message) -> None:
    support = db.get_setting('support_username', settings.support_username)
    await answer_template(
        message,
        customer_menu_title(),
        user_template_context(message.from_user, support=support),
        reply_markup=customer_keyboard(),
        content_key='customer_menu_title',
    )


async def show_preinvoice(callback_or_message, state: FSMContext) -> None:
    data = await state.get_data()
    text = build_preinvoice_text(data)
    if isinstance(callback_or_message, CallbackQuery):
        await edit_or_answer(callback_or_message, text, reply_markup=preinvoice_keyboard(), parse_mode='Markdown')
        await callback_or_message.answer()
    else:
        await callback_or_message.answer(text, reply_markup=preinvoice_keyboard(), parse_mode='Markdown')


async def refresh_service_from_panel(service: dict) -> tuple[dict, dict]:
    username = service.get('panel_username') or service.get('service_username')
    user_data = await api_client.get_user(username)
    sub_url = api_client.extract_subscription_url(user_data) or service.get('subscription_url')
    links = api_client.extract_config_links(user_data)
    db.update_user_service_snapshot(int(service['id']), user_data, sub_url, links)
    updated = db.get_user_service(int(service['id']), int(service['telegram_id'])) or service
    return updated, user_data


async def create_or_renew_service_for_order(order: dict) -> tuple[bool, str, dict | None]:
    try:
        template = await api_client.get_user_template(order['package_id'])
        user_id = int(order['user_id'])
        if order.get('order_type') == 'renew' and order.get('service_id'):
            service = db.get_user_service(int(order['service_id']), user_id)
            if not service:
                raise RuntimeError('سرویس برای تمدید پیدا نشد.')
            username = service.get('panel_username') or service.get('service_username')
            current_user = await api_client.get_user(username)
            renewal_mode = api_client.normalize_renewal_mode(db.get_setting('renewal_mode', 'add'))
            renew_payload = api_client.build_renew_payload(current_user, template, renewal_mode=renewal_mode)
            updated_user = await api_client.update_user(username, renew_payload)
            if renewal_mode == 'replace':
                # Replacement means the old remaining traffic is burned. Reset usage
                # after applying the new quota so the whole requested package is usable.
                await api_client.reset_user_traffic(username)
                updated_user = await api_client.get_user(username)
            sub_url = api_client.extract_subscription_url(updated_user) or service.get('subscription_url')
            links = api_client.extract_config_links(updated_user)
            if not links and sub_url:
                try:
                    links = await api_client.fetch_subscription_configs(sub_url)
                except Exception:
                    links = []
            db.save_user_service(
                telegram_id=user_id,
                order_id=int(order['id']),
                package_id=str(order['package_id']),
                package_name=str(order['package_name']),
                service_username=username,
                user_data=updated_user,
                subscription_url=sub_url,
                links=links,
            )
            return True, 'سرویس با موفقیت تمدید شد ✅', updated_user

        raw_username = str(order['customer_username'])
        created_user, actual_username = await api_client.create_user_with_unique_username(
            template,
            raw_username,
            note=build_panel_note_for_order(order),
        )
        links, user_data, sub_url = await api_client.get_user_config_links(actual_username)
        if not user_data:
            user_data = created_user
        sub_url = sub_url or api_client.extract_subscription_url(created_user)
        if not links:
            links = api_client.extract_config_links(created_user)
        db.save_user_service(
            telegram_id=user_id,
            order_id=int(order['id']),
            package_id=str(order['package_id']),
            package_name=str(order['package_name']),
            service_username=actual_username,
            user_data=user_data or created_user,
            subscription_url=sub_url,
            links=links,
        )
        return True, f'سرویس با موفقیت ساخته شد ✅\nنام نهایی سرویس: {actual_username}', user_data or created_user
    except Exception as exc:
        db.add_notification('service_create_failed', f'سفارش #{order.get("id")} پرداخت شد اما عملیات سرویس خطا داد: {exc}')
        return False, f'پرداخت ثبت شد اما ساخت/تمدید سرویس نیاز به بررسی ادمین دارد. خطا: {exc}', None

async def process_one_service_job(job: dict) -> tuple[bool, str]:
    job_id = int(job['id'])
    order_id = int(job['order_id'])
    if not payment_engine.begin_order_processing(order_id, owner=f'job:{job_id}'):
        return False, 'این سفارش همین الان در حال پردازش است.'
    try:
        db.mark_service_job_running(job_id)
        order = db.get_order(order_id)
        if not order:
            raise RuntimeError('سفارش پیدا نشد.')
        db.execute("UPDATE orders SET status='building_service' WHERE id=?", (order_id,))
        ok, service_message, user_data = await create_or_renew_service_for_order(order)
        if ok:
            db.mark_service_job_done(job_id)
            db.execute("UPDATE orders SET status='service_ready' WHERE id=?", (order_id,))
            return True, service_message
        raise RuntimeError(service_message)
    except Exception as exc:
        db.mark_service_job_failed(job_id, str(exc), retry_delay_seconds=120)
        db.execute("UPDATE orders SET status='needs_admin_review' WHERE id=?", (order_id,))
        db.record_system_error('customer.process_one_service_job', str(exc), entity_type='order', entity_id=str(order_id), error_type=type(exc).__name__)
        return False, str(exc)
    finally:
        payment_engine.finish_order_processing(order_id)


async def process_queued_service_jobs(limit: int = 5) -> int:
    count = 0
    for job in db.next_service_jobs(limit):
        await process_one_service_job(job)
        count += 1
    return count


async def finish_paid_order(message: Message, order: dict, stars_amount: int, charge_id: str | None = None, provider_charge_id: str | None = None) -> None:
    order_id = int(order['id'])
    if not payment_engine.begin_order_processing(order_id, owner='finish_paid_order'):
        await message.answer('این سفارش قبلا در حال پردازش است. نتیجه تا چند لحظه دیگر مشخص می‌شود.', reply_markup=customer_keyboard())
        return
    try:
        current = db.get_order(order_id) or order
        if current.get('status') not in ('paid', 'service_ready', 'build_queued', 'building_service'):
            db.mark_order_paid(order_id, stars_amount, charge_id, provider_charge_id)
        db.finalize_order_payment_effects(order_id)
        db.enqueue_service_job(order_id)
        db.execute("UPDATE orders SET status='build_queued' WHERE id=?", (order_id,))
    finally:
        payment_engine.finish_order_processing(order_id)

    job = db.fetchone('SELECT * FROM service_jobs WHERE order_id=?', (order_id,))
    ok, service_message = await process_one_service_job(job) if job else (False, 'صف ساخت پیدا نشد.')
    final_order = db.get_order(order_id) or order
    db.add_notification('payment', f'پرداخت سفارش #{order_id}: {stars_amount} Stars')
    receipt = payment_engine.build_receipt(final_order, '\n' + service_message)
    
    # Receipt is attached to the QR photo caption by send_subscription_delivery.
    # Do not send a separate receipt message here.
    if ok:
        service = db.fetchone(
            'SELECT * FROM user_services WHERE order_id=? AND telegram_id=? AND is_active=1 ORDER BY id DESC LIMIT 1',
            (order_id, int(final_order.get('user_id') or order.get('user_id'))),
        )
        if service:
            await send_subscription_delivery(message, service, title='✅ سرویس آماده شد. لینک اشتراک', invoice_text=receipt)
        after_purchase = db.get_setting('after_purchase_text', '')
        if after_purchase:
            support = db.get_setting('support_username', settings.support_username)
            ctx = user_template_context(message.from_user, support=support)
            ctx.update({
                'order_id': final_order.get('id', ''),
                'package_name': final_order.get('package_name', ''),
                'service_name': final_order.get('customer_username', ''),
                'price': final_order.get('amount_toman') or final_order.get('amount') or 0,
            })
            await answer_template(message, after_purchase, ctx, reply_markup=customer_keyboard(), content_key='after_purchase_text')
    else:
        await message.answer(friendly_error(service_message), reply_markup=customer_keyboard())


@customer_router.message(CustomerButtonFilter('buy'))
async def buy_subscription(message: Message, state: FSMContext):
    await state.clear()
    packs = await api_client.get_user_templates()
    items = visible_pack_rows(packs)
    if not items:
        await message.answer('فعلا بسته فعالی برای خرید وجود ندارد.', reply_markup=customer_keyboard())
        return
    await message.answer(
        '🛒 خرید اشتراک\n\nبسته موردنظر را انتخاب کنید:',
        reply_markup=customer_packs_keyboard(items),
    )


@customer_router.callback_query(F.data.startswith('cust_pack:'))
async def choose_pack(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split(':', 1)[1]
    pack = await api_client.get_user_template(template_id)
    meta = get_pack_meta(template_id)
    price = int(meta.get('price_amount') or 0)
    if price <= 0 or not int(meta.get('is_visible', 1)):
        await callback.answer('این بسته در حال حاضر قابل خرید نیست.', show_alert=True)
        return
    await state.set_state(CustomerBuyState.waiting_username)
    await state.update_data(
        package_id=str(template_id),
        package_name=pack_title(pack),
        price_toman=price,
        discount_amount=0,
        coupon_id=None,
        coupon_code=None,
        renew_service_id=None,
        order_type='new',
    )
    prompt_text, prompt_template, prompt_context = username_rules_text(pack, meta, callback.from_user)
    # This page must expose the ORIGINAL template and all package values in live edit
    # mode. In particular package name/price/volume/duration are real variables instead
    # of hardcoded rendered text.
    with edit_runtime.text_hint(
        setting_key='package_username_prompt_text',
        template=prompt_template,
        variables=prompt_context,
        content_key='package_username_prompt_text',
        label='متن بعد از انتخاب بسته / دریافت نام سرویس',
        allow_stickers=True,
    ):
        await edit_or_answer(callback, prompt_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='❌ انصراف و بازگشت', callback_data='cust_cancel')]
        ]))
    await callback.answer()


@customer_router.message(CustomerBuyState.waiting_username)
async def receive_username(message: Message, state: FSMContext):
    try:
        username = validate_customer_username(message.text or '')
    except Exception as exc:
        await message.answer(str(exc))
        return
    await state.update_data(service_username=username)
    await state.set_state(None)
    await show_preinvoice(message, state)


@customer_router.callback_query(F.data == 'cust_preinvoice')
async def preinvoice_back(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await show_preinvoice(callback, state)


@customer_router.callback_query(F.data == 'cust_coupon')
async def coupon_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get('package_id'):
        await callback.answer('ابتدا یک بسته انتخاب کنید.', show_alert=True)
        return
    await state.set_state(CustomerBuyState.waiting_coupon)
    await edit_or_answer(callback, '🎁 کد تخفیف را ارسال کنید:', reply_markup=coupon_back_keyboard())
    await callback.answer()


@customer_router.message(CustomerBuyState.waiting_coupon)
async def coupon_receive(message: Message, state: FSMContext):
    code = (message.text or '').strip().upper()
    data = await state.get_data()
    package_id = str(data.get('package_id'))
    price = int(data.get('price_toman') or 0)
    coupon = db.coupon_by_code(code)
    ok, reason = coupon_is_valid_for(coupon, package_id)
    if not ok:
        await message.answer(f'❌ {reason}\nکد دیگری بفرستید یا از دکمه بازگشت استفاده کنید.', reply_markup=coupon_back_keyboard())
        return
    discount = calculate_discount(price, coupon)
    await state.update_data(
        discount_amount=discount,
        coupon_id=int(coupon['id']),
        coupon_code=coupon['code'],
    )
    await state.set_state(None)
    await message.answer('کد تخفیف اعمال شد ✅')
    await show_preinvoice(message, state)


@customer_router.callback_query(F.data == 'cust_pay_menu')
async def payment_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get('package_id') or not data.get('service_username'):
        await callback.answer('اطلاعات خرید کامل نیست.', show_alert=True)
        return
    final_price = max(0, int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))
    wallet_balance = db.wallet_balance(callback.from_user.id)
    wallet_used = min(wallet_balance, final_price)
    payable_toman = max(0, final_price - wallet_used)
    stars_amount, rate, mode = 0, 0, 'disabled'
    if _gateway_enabled('payment_stars_enabled', True) and payable_toman > 0:
        try:
            stars_amount, rate, mode = toman_to_stars(payable_toman)
        except Exception as exc:
            db.record_system_error('customer.payment_menu.stars_rate', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
            stars_amount, rate, mode = 0, 0, 'unavailable'
    await state.update_data(final_toman=final_price, wallet_used=wallet_used, payable_toman=payable_toman, stars_amount=stars_amount, star_rate_toman=rate, star_rate_mode=mode)
    rate_line = f'نرخ تبدیل: هر ۱ استار = {format_toman(rate)}\n' if payable_toman > 0 and stars_amount > 0 else ''
    text = (
        '💳 انتخاب روش پرداخت\n\n'
        f'مبلغ نهایی سرویس: {format_toman(final_price)}\n'
        f'موجودی کیف پول شما: {format_toman(wallet_balance)}\n'
        f'مبلغ کسر از کیف پول: {format_toman(wallet_used)}\n'
        f'مانده قابل پرداخت: {format_toman(payable_toman)}\n'
        f'{rate_line}'
        + (f'مبلغ قابل پرداخت با Stars: {stars_amount:,} ⭐️' if stars_amount > 0 else '')
    )
    await edit_or_answer(callback, text, reply_markup=payment_keyboard(stars_amount, final_price, wallet_used, payable_toman))
    await callback.answer()


@customer_router.callback_query(F.data == 'cust_free_activate')
async def free_activate(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    final_toman = max(0, int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))
    if final_toman > 0:
        await callback.answer('این سفارش رایگان نیست.', show_alert=True)
        return
    order_id = db.create_customer_order(
        user_id=user_id,
        package_id=str(data.get('package_id')),
        package_name=str(data.get('package_name')),
        customer_username=str(data.get('service_username')),
        amount_toman=0,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=0,
        wallet_used=int(data.get('wallet_used') or 0),
        status='pending',
        order_type=str(data.get('order_type') or 'new'),
        service_id=data.get('renew_service_id'),
    )
    order = db.get_order(order_id)
    await state.clear()
    await edit_or_answer(callback, 'در حال ساخت یا تمدید سرویس...')
    await finish_paid_order(callback.message, order, 0, None, None)
    await callback.answer('سفارش رایگان انجام شد.')


@customer_router.callback_query(F.data == 'cust_wallet_activate')
async def wallet_activate(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    final_toman = max(0, int(data.get('final_toman') or (int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))))
    wallet_used = int(data.get('wallet_used') or 0)
    payable_toman = max(0, final_toman - wallet_used)
    if payable_toman > 0:
        await callback.answer('هنوز مبلغی برای پرداخت باقی مانده است.', show_alert=True)
        return
    order_id = db.create_customer_order(
        user_id=user_id,
        package_id=str(data.get('package_id')),
        package_name=str(data.get('package_name')),
        customer_username=str(data.get('service_username')),
        amount_toman=final_toman,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=0,
        wallet_used=wallet_used,
        status='pending',
        order_type=str(data.get('order_type') or 'new'),
        service_id=data.get('renew_service_id'),
    )
    order = db.get_order(order_id)
    await state.clear()
    await edit_or_answer(callback, 'در حال ساخت یا تمدید سرویس...')
    await finish_paid_order(callback.message, order, 0, None, None)
    await callback.answer('سفارش انجام شد.')


@customer_router.callback_query(F.data == 'cust_pay_tetrapay')
async def pay_with_tetrapay(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_tetrapay_enabled', True):
        await callback.answer('این روش پرداخت فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    user_id = callback.from_user.id
    final_toman = max(0, int(data.get('final_toman') or (int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))))
    wallet_used = int(data.get('wallet_used') or 0)
    payable_toman = max(0, int(data.get('payable_toman') or (final_toman - wallet_used)))
    if payable_toman <= 0:
        await callback.answer('این سفارش مبلغ قابل پرداخت ندارد.', show_alert=True)
        return
    order_id = db.create_customer_order(
        user_id=user_id,
        package_id=str(data.get('package_id')),
        package_name=str(data.get('package_name')),
        customer_username=str(data.get('service_username')),
        amount_toman=final_toman,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=0,
        wallet_used=wallet_used,
        status='pending',
        order_type=str(data.get('order_type') or 'new'),
        service_id=data.get('renew_service_id'),
    )
    hash_id = f'order_{order_id}'
    description = f'پرداخت سفارش #{order_id} - {data.get("package_name") or "سرویس"}'
    try:
        invoice = await tetrapay.create_invoice(
            hash_id=hash_id,
            amount_toman=payable_toman,
            description=description,
        )
    except Exception as exc:
        db.record_system_error('customer.pay_with_tetrapay', str(exc), entity_type='order', entity_id=str(order_id), error_type=type(exc).__name__)
        await safe_callback_answer(callback, 'خطا در ساخت لینک پرداخت. جزئیات کامل در بخش خطاهای سیستم ثبت شد.', show_alert=True)
        return
    db.set_order_payment_provider(
        order_id,
        'tetrapay',
        payable_toman,
        provider_invoice_id=invoice.get('provider_invoice_id'),
        provider_payment_url=invoice.get('payment_url'),
        provider_status='pending',
        raw=invoice,
    )
    await state.clear()
    await edit_or_answer(
        callback,
        '💳 پرداخت با تتراپی\n\n'
        f'شماره سفارش: {copy_code(order_id)}\n'
        f'مبلغ قابل پرداخت: {format_toman(payable_toman)}\n\n'
        'بعد از پرداخت، نتیجه از طریق CallbackURL به ربات اعلام می‌شود و سرویس به صورت خودکار ساخته می‌شود.',
        reply_markup=tetrapay_pay_link_keyboard(str(invoice['payment_url']), f'cust_tetrapay_check:{order_id}'),
        parse_mode='Markdown',
    )
    await callback.answer('لینک پرداخت ساخته شد.')


@customer_router.callback_query(F.data.startswith('cust_tetrapay_check:'))
async def check_tetrapay_order(callback: CallbackQuery):
    order_id = int(callback.data.split(':', 1)[1])
    order = db.get_order(order_id)
    if not order or int(order.get('user_id') or 0) != callback.from_user.id:
        await callback.answer('سفارش پیدا نشد.', show_alert=True)
        return
    status = str(order.get('status') or 'pending')
    if status == 'service_ready':
        await callback.answer('پرداخت تایید شده و سرویس آماده است ✅', show_alert=True)
        return
    if status in {'paid', 'build_queued', 'building_service'}:
        await callback.answer('پرداخت تایید شده و سرویس در حال آماده سازی است.', show_alert=True)
        return
    if status == 'needs_admin_review':
        await callback.answer('پرداخت یا ساخت سرویس نیازمند بررسی ادمین است.', show_alert=True)
        return

    authority = str(order.get('provider_invoice_id') or '')
    if not authority:
        await callback.answer('Authority سفارش پیدا نشد. منتظر callback بمانید یا به پشتیبانی پیام بدهید.', show_alert=True)
        return
    try:
        verify_data = await tetrapay.verify_payment(authority)
    except Exception as exc:
        db.record_system_error('customer.check_tetrapay_order', str(exc), entity_type='order', entity_id=str(order_id), error_type=type(exc).__name__)
        await callback.answer('اتصال به verify تتراپی برقرار نشد. تنظیمات TETRAPAY_VERIFY_URL و اینترنت سرور را چک کنید.', show_alert=True)
        return
    if verify_data:
        http_status = int(verify_data.get('_http_status') or 0)
        if http_status >= 400:
            db.record_system_error('customer.check_tetrapay_order.verify_pending_http', str(verify_data)[:800], entity_type='order', entity_id=str(order_id), error_type='TetraPayVerifyNotPaid')
    if verify_data and tetrapay.callback_is_success(verify_data):
        expected = int(order.get('payable_toman') or max(0, int(order.get('amount_toman') or order.get('amount') or 0) - int(order.get('wallet_used') or 0)))
        paid_amount = tetrapay.extract_amount(verify_data)
        if paid_amount and paid_amount < expected:
            await callback.answer('مبلغ پرداخت با سفارش همخوانی ندارد و نیازمند بررسی است.', show_alert=True)
            db.record_system_error('customer.check_tetrapay_order', f'کمتر بودن مبلغ پرداخت. expected={expected}, paid={paid_amount}', entity_type='order', entity_id=str(order_id), error_type='AmountMismatch')
            return
        tracking_id = tetrapay.extract_tracking_id(verify_data) or authority
        db.mark_order_provider_paid(order_id, 'tetrapay', expected, tracking_id, verify_data)
        refreshed = db.get_order(order_id) or order
        await callback.answer('پرداخت تایید شد. سرویس در حال آماده سازی است ✅', show_alert=True)
        await finish_paid_order(callback.message, refreshed, 0, None, tracking_id)
        return
    await callback.answer('هنوز پرداخت توسط تتراپی تایید نشده است. اگر داخل ربات تتراپی مرحله احراز هویت یا پرداخت را می بینید، اول همان را کامل کنید و بعد دوباره بررسی بزنید.', show_alert=True)


@customer_router.callback_query(F.data == 'cust_pay_stars')
async def pay_with_stars(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_stars_enabled', True):
        await callback.answer('این روش پرداخت فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    user_id = callback.from_user.id
    final_toman = max(0, int(data.get('final_toman') or (int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))))
    wallet_used = int(data.get('wallet_used') or 0)
    payable_toman = max(0, int(data.get('payable_toman') or (final_toman - wallet_used)))
    try:
        stars_amount, rate, mode = toman_to_stars(payable_toman)
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if stars_amount <= 0:
        await callback.answer('این سفارش رایگان یا کامل با کیف پول است.', show_alert=True)
        return
    order_id = db.create_customer_order(
        user_id=user_id,
        package_id=str(data.get('package_id')),
        package_name=str(data.get('package_name')),
        customer_username=str(data.get('service_username')),
        amount_toman=final_toman,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=stars_amount,
        wallet_used=int(data.get('wallet_used') or 0),
        status='pending',
        order_type=str(data.get('order_type') or 'new'),
        service_id=data.get('renew_service_id'),
    )
    payload = f'customer_order:{order_id}'
    title = f"خرید {str(data.get('package_name'))[:24]}"
    description = f"پرداخت سفارش #{order_id} برای کاربر {data.get('service_username')}"
    await callback.message.answer_invoice(
        title=title,
        description=description,
        payload=payload,
        provider_token='',
        currency='XTR',
        prices=[LabeledPrice(label='پرداخت با Telegram Stars', amount=stars_amount)],
    )
    await callback.answer('فاکتور پرداخت ارسال شد.')


@customer_router.message(F.successful_payment)
async def customer_successful_payment(message: Message):
    payment = message.successful_payment
    if not payment:
        return
    payload = payment.invoice_payload or ''
    if payload.startswith('wallet_charge:'):
        request_id = int(payload.split(':', 1)[1])
        charge_id = payment.telegram_payment_charge_id
        if charge_id and db.fetchone('SELECT id FROM star_payments WHERE telegram_payment_charge_id=?', (charge_id,)):
            await message.answer('این پرداخت قبلا ثبت شده است.', reply_markup=customer_keyboard())
            return
        stars_amount = int(payment.total_amount or 0)
        db.save_star_payment(message.from_user.id if message.from_user else None, stars_amount, payload, charge_id, payment.provider_payment_charge_id, 'paid')
        db.mark_wallet_charge_paid(request_id, stars_amount, charge_id)
        balance = db.wallet_balance(message.from_user.id)
        await message.answer(wallet_charge_result_text(request_id, balance), reply_markup=customer_keyboard())
        return
    if not payload.startswith('customer_order:'):
        return
    order_id = int(payload.split(':', 1)[1])
    order = db.get_order(order_id)
    if not order:
        await message.answer('پرداخت دریافت شد، اما سفارش پیدا نشد. لطفا به پشتیبانی پیام بدهید.', reply_markup=customer_keyboard())
        return
    charge_id = payment.telegram_payment_charge_id
    if charge_id and db.fetchone('SELECT id FROM star_payments WHERE telegram_payment_charge_id=?', (charge_id,)):
        await message.answer('این پرداخت قبلا ثبت شده است.', reply_markup=customer_keyboard())
        return
    stars_amount = int(payment.total_amount or order.get('stars_amount') or 0)
    provider_charge_id = payment.provider_payment_charge_id
    db.save_star_payment(
        user_id=message.from_user.id if message.from_user else None,
        amount=stars_amount,
        payload=payload,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id=provider_charge_id,
        status='paid',
    )
    await finish_paid_order(message, order, stars_amount, charge_id, provider_charge_id)


@customer_router.message(CustomerButtonFilter('services'))
async def my_services_message(message: Message):
    user_id = message.from_user.id if message.from_user else None
    services = db.user_services(user_id)
    if not services:
        await message.answer('شما هنوز سرویسی ندارید.', reply_markup=customer_keyboard())
        return
    await message.answer('👤 سرویس های من\n\nیک سرویس را انتخاب کنید:', reply_markup=services_list_keyboard(services))


@customer_router.callback_query(F.data == 'cust_services')
async def my_services_callback(callback: CallbackQuery):
    services = db.user_services(callback.from_user.id)
    if not services:
        await edit_or_answer(callback, 'شما هنوز سرویسی ندارید.')
        await callback.answer()
        return
    await edit_or_answer(callback, '👤 سرویس های من\n\nیک سرویس را انتخاب کنید:', reply_markup=services_list_keyboard(services))
    await callback.answer()


@customer_router.callback_query(F.data.startswith('cust_service:'))
async def service_detail(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    user_data = {}
    try:
        service, user_data = await refresh_service_from_panel(service)
    except Exception:
        try:
            user_data = json.loads(service.get('raw_json') or '{}')
        except Exception:
            user_data = {}
    sent = await show_service_detail_photo(callback, service, user_data)
    if not sent:
        await edit_or_answer(callback, build_service_detail_text(service, user_data), reply_markup=service_detail_keyboard(service_id), parse_mode='Markdown')
    await callback.answer()


@customer_router.callback_query(F.data.startswith('cust_service_refresh:'))
async def service_refresh(callback: CallbackQuery):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    try:
        service, user_data = await refresh_service_from_panel(service)
        sent = await show_service_detail_photo(callback, service, user_data)
        if not sent:
            await edit_or_answer(callback, build_service_detail_text(service, user_data), reply_markup=service_detail_keyboard(service_id), parse_mode='Markdown')
        await callback.answer('بروزرسانی شد.')
    except Exception as exc:
        db.record_system_error('customer.service_refresh', str(exc), user_id=callback.from_user.id, entity_type='service', entity_id=str(service_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)


@customer_router.callback_query(F.data.startswith('cust_service_sub:'))
async def service_subscription(callback: CallbackQuery):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    sub = service.get('subscription_url')
    if not sub:
        try:
            service, user_data = await refresh_service_from_panel(service)
            sub = service.get('subscription_url') or api_client.extract_subscription_url(user_data)
        except Exception:
            pass
    if not sub:
        await callback.answer('لینک اشتراک برای این سرویس پیدا نشد.', show_alert=True)
        return
    await send_subscription_delivery(callback.message, service, sub_url=sub)
    await callback.answer('ارسال شد.')


@customer_router.callback_query(F.data.startswith('cust_service_configs:'))
async def service_configs(callback: CallbackQuery):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    links = await get_service_config_links(service)
    if not links:
        await callback.answer('کانفیگی از API سرور پیدا نشد. ابتدا سرویس را بروزرسانی کنید یا لینک اشتراک را بررسی کنید.', show_alert=True)
        return
    await edit_or_answer(callback, '📋 کانفیگ های سرویس\n\nکانفیگ موردنظر را انتخاب کنید:', reply_markup=configs_keyboard(service_id, links))
    await callback.answer()


@customer_router.callback_query(F.data.startswith('cust_config:'))
async def service_config_send(callback: CallbackQuery):
    _, service_id_s, index_s = callback.data.split(':')
    service_id = int(service_id_s)
    index = int(index_s)
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    links = await get_service_config_links(service)
    if index < 0 or index >= len(links):
        await callback.answer('کانفیگ پیدا نشد.', show_alert=True)
        return
    await send_config_delivery(callback.message, service, links[index], index=index)
    await callback.answer('ارسال شد.')


@customer_router.callback_query(F.data.startswith('cust_service_revoke:'))
async def service_revoke(callback: CallbackQuery):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    try:
        username = service.get('panel_username') or service.get('service_username')
        result = await api_client.revoke_user_subscription(username)
        user_data = await api_client.get_user(username)
        sub_url = api_client.extract_subscription_url(user_data) or api_client.extract_subscription_url(result) or service.get('subscription_url')
        links = api_client.extract_config_links(user_data) or api_client.extract_config_links(result)
        db.update_user_service_snapshot(service_id, user_data, sub_url, links)
        updated_service = db.get_user_service(service_id, callback.from_user.id) or service
        await callback.message.answer('لینک با موفقیت تغییر کرد ✅')
        await send_subscription_delivery(callback.message, updated_service, sub_url=sub_url, title='🔗 لینک اشتراک جدید')
        await callback.answer('تغییر لینک انجام شد.')
    except Exception as exc:
        db.record_system_error('customer.service_revoke', str(exc), user_id=callback.from_user.id, entity_type='service', entity_id=str(service_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)



@customer_router.callback_query(F.data.startswith('cust_service_auto_renew:'))
async def service_auto_renew_toggle(callback: CallbackQuery):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    key = f'auto_renew:{callback.from_user.id}:{service_id}'
    current = bool(db.get_setting(key, False))
    db.set_setting(key, not current)
    await callback.answer(f"تمدید خودکار {'روشن' if not current else 'خاموش'} شد.", show_alert=True)


@customer_router.callback_query(F.data.startswith('cust_service_renew:'))
async def service_renew(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return

    # Offer visible renewal plans so user can renew with their current plan or choose another plan
    try:
        packs = await api_client.get_user_templates()
        items = visible_pack_rows(packs)
    except Exception:
        items = []

    if items:
        # Show renewal plan options
        await state.clear()
        text = (
            f'🔁 تمدید سرویس <code>{html.escape(str(service.get("service_username")))}</code>\n\n'
            'لطفا بسته مورد نظر برای تمدید را انتخاب کنید:'
        )
        await edit_or_answer(callback, text, reply_markup=customer_renew_packs_keyboard(items, service_id), parse_mode='HTML')
        await callback.answer()
        return

    # Fallback to current plan if pack list is empty or API unavailable
    meta = get_pack_meta(str(service.get('package_id')))
    price = int(meta.get('price_amount') or 0)
    if price <= 0:
        await callback.answer('قیمت این بسته برای تمدید تنظیم نشده است.', show_alert=True)
        return
    await state.clear()
    await state.update_data(
        package_id=str(service.get('package_id')),
        package_name=str(service.get('package_name')),
        price_toman=price,
        discount_amount=0,
        coupon_id=None,
        coupon_code=None,
        service_username=str(service.get('service_username')),
        renew_service_id=service_id,
        order_type='renew',
    )
    await show_preinvoice(callback, state)


@customer_router.callback_query(F.data.startswith('cust_renew_pack:'))
async def choose_renew_pack(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    service_id = int(parts[1])
    template_id = parts[2]
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    pack = await api_client.get_user_template(template_id)
    meta = get_pack_meta(template_id)
    price = int(meta.get('price_amount') or 0)
    if price <= 0 or not int(meta.get('is_visible', 1)):
        await callback.answer('این بسته برای تمدید در دسترس نیست.', show_alert=True)
        return
    await state.clear()
    await state.update_data(
        package_id=str(template_id),
        package_name=pack_title(pack),
        price_toman=price,
        discount_amount=0,
        coupon_id=None,
        coupon_code=None,
        service_username=str(service.get('service_username')),
        renew_service_id=service_id,
        order_type='renew',
    )
    await show_preinvoice(callback, state)


@customer_router.callback_query(F.data.startswith('cust_service_transfer:'))
async def service_transfer_start(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    await state.set_state(CustomerTransferState.waiting_target_user_id)
    await state.update_data(transfer_service_id=service_id)
    await edit_or_answer(
        callback,
        '👤 انتقال سرویس\n\nآیدی عددی کاربر مقصد را ارسال کنید.\nآیدی عددی از بخش کیف پول من قابل کپی است.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data=f'cust_service:{service_id}')]]),
    )
    await callback.answer()


@customer_router.message(CustomerTransferState.waiting_target_user_id)
async def service_transfer_finish(message: Message, state: FSMContext):
    user_id = message.from_user.id if message.from_user else None
    text = (message.text or '').strip()
    if not text.isdigit():
        await message.answer('آیدی مقصد باید عددی باشد.')
        return
    target_id = int(text)
    data = await state.get_data()
    service_id = int(data.get('transfer_service_id'))
    service = db.get_user_service(service_id, user_id)
    if not service:
        await state.clear()
        await message.answer('سرویس پیدا نشد.', reply_markup=customer_keyboard())
        return
    if target_id == user_id:
        await message.answer('این سرویس همین حالا متعلق به خود شماست.')
        return
    db.transfer_user_service(service_id, user_id, target_id)
    db.add_notification('service_transfer', f'سرویس {service_id} از {user_id} به {target_id} منتقل شد')
    await state.clear()
    await message.answer(
        f'سرویس با موفقیت منتقل شد ✅\n\nاز: {copy_code(user_id)}\nبه: {copy_code(target_id)}',
        reply_markup=customer_keyboard(),
        parse_mode='Markdown',
    )


@customer_router.callback_query(F.data.startswith('cust_service_delete_start:'))
async def service_delete_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    text = (
        '🗑 حذف سرویس\n\n'
        f'سرویس: {copy_code(service.get("service_username"))}\n\n'
        '⚠️ این عملیات غیر قابل بازگشت است.\n'
        '⚠️ بعد از حذف، دسترسی سرویس از سرور قطع می شود.\n'
        '⚠️ هیچ مبلغی به کیف پول یا حساب شما برگشت داده نمی شود.\n\n'
        'برای ادامه روی دکمه تایید مرحله اول بزنید.'
    )
    await edit_or_answer(callback, text, reply_markup=service_delete_first_keyboard(service_id), parse_mode='Markdown')
    await callback.answer()


@customer_router.callback_query(F.data.startswith('cust_service_delete_confirm1:'))
async def service_delete_confirm1(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    text = (
        '⚠️ تایید دوم حذف سرویس\n\n'
        f'نام سرویس: {copy_code(service.get("service_username"))}\n\n'
        'با زدن دکمه بعدی، وارد تایید نهایی می شوید.\n'
        'در تایید نهایی باید نام سرویس را دقیقا تایپ کنید.\n\n'
        '❌ این کار قابل بازگشت نیست و مبلغی برگشت داده نمی شود.'
    )
    await edit_or_answer(callback, text, reply_markup=service_delete_second_keyboard(service_id), parse_mode='Markdown')
    await callback.answer()


@customer_router.callback_query(F.data.startswith('cust_service_delete_confirm2:'))
async def service_delete_confirm2(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(':', 1)[1])
    service = db.get_user_service(service_id, callback.from_user.id)
    if not service:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    username = str(service.get('service_username') or '').strip()
    await state.set_state(CustomerDeleteState.waiting_service_delete_confirm)
    await state.update_data(delete_service_id=service_id, delete_service_username=username)
    text = (
        '🧨 تایید نهایی حذف سرویس\n\n'
        f'برای حذف دائمی، نام سرویس را دقیقا مثل متن زیر ارسال کنید:\n{copy_code(username)}\n\n'
        '⚠️ اگر نام را دقیق نفرستید حذف انجام نمی شود.\n'
        '⚠️ بعد از حذف، سرویس از سرور حذف می شود و مبلغی برگشت داده نمی شود.'
    )
    await edit_or_answer(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 انصراف', callback_data=f'cust_service:{service_id}')]]),
        parse_mode='Markdown',
    )
    await callback.answer()


@customer_router.message(CustomerDeleteState.waiting_service_delete_confirm)
async def service_delete_finish(message: Message, state: FSMContext):
    user_id = message.from_user.id if message.from_user else None
    data = await state.get_data()
    service_id = int(data.get('delete_service_id') or 0)
    expected_username = str(data.get('delete_service_username') or '').strip()
    typed = (message.text or '').strip()
    service = db.get_user_service(service_id, user_id)
    if not service:
        await state.clear()
        await message.answer('سرویس پیدا نشد یا قبلا حذف شده است.', reply_markup=customer_keyboard())
        return
    if typed != expected_username:
        await message.answer(
            'نام سرویس را دقیق وارد نکردید. حذف انجام نشد.\n\n'
            f'برای تایید باید دقیقا این متن را بفرستید:\n{copy_code(expected_username)}',
            parse_mode='Markdown',
        )
        return
    try:
        panel_username = service.get('panel_username') or service.get('service_username')
        await api_client.delete_user(str(panel_username))
        db.deactivate_user_service(service_id, user_id, status='deleted_by_customer')
        db.add_notification('service_deleted', f'کاربر {user_id} سرویس {panel_username} را حذف کرد')
        await state.clear()
        await message.answer(
            f'سرویس با موفقیت حذف شد ✅\n\nنام سرویس حذف شده: {copy_code(panel_username)}\n\n'
            'این عملیات غیر قابل بازگشت است و مبلغی برگشت داده نمی شود.',
            reply_markup=customer_keyboard(),
            parse_mode='Markdown',
        )
    except Exception as exc:
        await message.answer(
            f'خطا در حذف سرویس از سرور: {exc}\n\n'
            'سرویس از لیست شما حذف نشد تا اطلاعات ربات با سرور ناهماهنگ نشود.',
            reply_markup=customer_keyboard(),
        )


@customer_router.message(F.text == '__disabled_referral__')
async def customer_referral(message: Message, bot):
    user_id = message.from_user.id if message.from_user else None
    stats = db.referral_stats(user_id)
    me = await bot.get_me()
    reward = int(db.get_setting('referral_reward_toman', 0) or 0)
    condition = db.get_setting('referral_condition', 'payment')
    condition_text = 'بعد از پرداخت دعوت شده' if condition == 'payment' else 'بعد از ورود با لینک'
    link = f'https://example.com/{me.username}?start=ref_{user_id}'
    await message.answer(
        '👥 دعوت دوستان\n\n'
        f'لینک دعوت شما:\n{copy_code(link)}\n\n'
        f'پاداش هر دعوت: {format_toman(reward)}\n'
        f'شرط دریافت پاداش: {condition_text}\n\n'
        f'کل دعوت ها: {copy_code(stats["total"])}\n'
        f'پاداش داده شده: {copy_code(stats["rewarded"])}\n'
        f'در انتظار: {copy_code(stats["pending"])}',
        reply_markup=customer_keyboard(),
        parse_mode='Markdown',
    )


@customer_router.message(CustomerButtonFilter('test_service'))
async def customer_test_service(message: Message):
    if not bool(db.get_setting('test_service_enabled', True)):
        await message.answer('فعلا سرویس تست غیرفعال است.', reply_markup=customer_keyboard())
        return
    template_id = db.get_setting('test_template_id', None)
    if not template_id:
        await message.answer('فعلا سرویس تست توسط ادمین فعال نشده است.', reply_markup=customer_keyboard())
        return
    eligible, remaining = db.test_service_eligibility(message.from_user.id)
    if not eligible:
        if remaining > 0:
            days = max(1, math.ceil(remaining / 86400))
            msg = f'شما قبلا سرویس تست را دریافت کرده اید. حدود {days} روز دیگر دوباره امکان دریافت تست دارید.'
        else:
            msg = 'شما قبلا سرویس تست را دریافت کرده اید.'
        await message.answer(msg, reply_markup=customer_keyboard())
        return
    await message.answer(
        '🧪 سرویس تست\n\nبرای دریافت سرویس تست روی دکمه زیر بزنید.',
        reply_markup=test_service_keyboard(),
    )


@customer_router.callback_query(F.data == 'cust_test_get')
async def customer_test_get(callback: CallbackQuery):
    user_id = callback.from_user.id
    template_id = db.get_setting('test_template_id', None)
    if not template_id:
        await callback.answer('سرویس تست فعال نیست.', show_alert=True)
        return
    eligible, remaining = db.test_service_eligibility(user_id)
    if not eligible:
        if remaining > 0:
            days = max(1, math.ceil(remaining / 86400))
            msg = f'حدود {days} روز دیگر دوباره می توانید سرویس تست بگیرید.'
        else:
            msg = 'شما قبلا سرویس تست را گرفته اید.'
        await callback.answer(msg, show_alert=True)
        return
    try:
        template = await api_client.get_user_template(template_id)
        last_error = None
        created_user = None
        actual_username = None
        for _ in range(12):
            raw_username = await api_client.generate_unique_test_username(template)
            actual_username = api_client.apply_template_username(template, raw_username)
            payload = api_client.build_user_payload_from_template(template, raw_username, note=build_panel_note_for_test(user_id))
            try:
                created_user = await api_client.create_user(payload)
                actual_username = str(created_user.get('username') or actual_username)
                break
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                if 'already' in text or 'duplicate' in text or 'exists' in text or 'تکراری' in text:
                    continue
                raise
        if created_user is None or actual_username is None:
            raise RuntimeError(f'نام تست یکتا ساخته نشد: {last_error}')
        links, user_data, sub_url = await api_client.get_user_config_links(actual_username)
        if not user_data:
            user_data = created_user
        sub_url = sub_url or api_client.extract_subscription_url(created_user)
        if not links:
            links = api_client.extract_config_links(created_user)
        service_id = db.save_user_service(
            telegram_id=user_id,
            order_id=None,
            package_id=str(template_id),
            package_name='سرویس تست',
            service_username=actual_username,
            user_data=user_data or created_user,
            subscription_url=sub_url,
            links=links,
        )
        db.mark_test_used(user_id, service_id)
        saved_service = db.get_user_service(service_id, user_id) or {'service_username': actual_username, 'subscription_url': sub_url}
        await edit_or_answer(callback, f'سرویس تست ساخته شد ✅\n\nنام سرویس: {copy_code(actual_username)}', parse_mode='Markdown')
        await send_subscription_delivery(callback.message, saved_service, sub_url=sub_url, title='🔗 لینک اشتراک سرویس تست')
        await callback.answer('ساخته شد.')
    except Exception as exc:
        await safe_callback_answer(callback, 'خطا در ساخت سرویس تست. جزئیات کامل در بخش خطاهای سیستم ثبت شد.', show_alert=True)

@customer_router.message(CustomerButtonFilter('wallet'))
async def customer_wallet(message: Message):
    user_id = message.from_user.id if message.from_user else None
    balance = db.wallet_balance(user_id)
    await message.answer(
        '💳 کیف پول من\n\n'
        f'آیدی عددی شما: {copy_code(user_id)}\n'
        f'موجودی کیف پول: {format_toman(balance)}\n\n'
        'برای انتقال سرویس، آیدی عددی بالا را کپی و برای مالک سرویس ارسال کنید.',
        reply_markup=wallet_customer_keyboard(),
        parse_mode='Markdown',
    )


@customer_router.callback_query(F.data == 'cust_wallet_home')
async def customer_wallet_home_callback(callback: CallbackQuery):
    balance = db.wallet_balance(callback.from_user.id)
    await edit_or_answer(
        callback,
        '💳 کیف پول من\n\n'
        f'آیدی عددی شما: {copy_code(callback.from_user.id)}\n'
        f'موجودی کیف پول: {format_toman(balance)}',
        reply_markup=wallet_customer_keyboard(),
        parse_mode='Markdown',
    )
    await callback.answer()


@customer_router.callback_query(F.data == 'cust_wallet_history')
async def customer_wallet_history(callback: CallbackQuery):
    rows = db.fetchall('SELECT * FROM wallet_transactions WHERE user_id=? ORDER BY id DESC LIMIT 20', (callback.from_user.id,))
    text = '🧾 تاریخچه کیف پول\n\n'
    if not rows:
        text += 'تراکنشی ندارید.'
    for row in rows:
        sign = '+' if int(row['amount']) >= 0 else ''
        text += f"#{row['id']} | {sign}{format_toman(row['amount'])} | {row.get('reason') or '-'} | {copy_code(row['created_at'])}\n"
    await edit_or_answer(callback, text, reply_markup=wallet_customer_keyboard(), parse_mode='Markdown')
    await callback.answer()


def wallet_charge_result_text(request_id: int, balance: int, prefix: str = 'کیف پول شما با موفقیت شارژ شد ✅') -> str:
    req = db.get_wallet_charge_request(request_id) or {}
    bonus = int(req.get('campaign_bonus_amount') or 0)
    base = int(req.get('amount_toman') or 0)
    text = prefix + f'\n\nمبلغ شارژ: {format_toman(base)}'
    if bonus > 0:
        text += f'\n🎁 بونوس کمپین: +{format_toman(bonus)}'
        text += f'\n💰 مجموع واریزی کیف پول: {format_toman(base + bonus)}'
    text += f'\n\nموجودی جدید: {format_toman(balance)}'
    return text


@customer_router.callback_query(F.data == 'cust_wallet_charge')
async def customer_wallet_charge_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CustomerWalletState.waiting_charge_amount)
    await edit_or_answer(callback, '➕ شارژ کیف پول\n\nمبلغ شارژ را به تومان وارد کنید. مثال: 100000')
    await callback.answer()


@customer_router.message(CustomerWalletState.waiting_charge_amount)
async def customer_wallet_charge_amount(message: Message, state: FSMContext):
    raw = (message.text or '').replace(',', '').replace('٬', '').strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer('لطفا مبلغ را به تومان و عددی وارد کنید.')
        return
    amount = int(raw)
    stars_amount, rate, mode = 0, 0, 'disabled'
    if _gateway_enabled('payment_stars_enabled', True):
        try:
            stars_amount, rate, mode = toman_to_stars(amount)
        except Exception as exc:
            db.record_system_error('customer.wallet_charge.stars_rate', str(exc), user_id=message.from_user.id if message.from_user else None, error_type=type(exc).__name__)
            stars_amount, rate, mode = 0, 0, 'unavailable'
    await state.update_data(wallet_charge_amount=amount, wallet_charge_stars=stars_amount)
    await state.set_state(None)
    campaign = db.get_active_wallet_campaign()
    campaign_text = ''
    if campaign and float(campaign.get('bonus_percent') or 0) > 0:
        percent = float(campaign.get('bonus_percent') or 0)
        bonus = int(round(amount * percent / 100.0))
        total_credit = amount + bonus
        rule_text = 'شروع پرداخت در زمان کمپین کافی است' if str(campaign.get('timing_rule') or 'payment_time') == 'payment_time' else 'تایید پرداخت هم باید قبل از پایان کمپین انجام شود'
        campaign_text = f'\n🎯 کمپین فعال: {percent:g}٪ شارژ بیشتر\n🎁 بونوس احتمالی: {format_toman(bonus)}\n💰 مجموع شارژ در صورت احراز شرایط: {format_toman(total_credit)}\n📌 {rule_text}\n'
    await message.answer(
        'روش شارژ کیف پول را انتخاب کنید:\n\n'
        f'مبلغ شارژ: {format_toman(amount)}\n'
        + (f'معادل Stars: {stars_amount:,} ⭐️' if stars_amount > 0 else 'Stars در حال حاضر برای این شارژ در دسترس نیست.') + campaign_text,
        reply_markup=wallet_charge_method_keyboard(stars_amount),
    )


@customer_router.callback_query(F.data == 'cust_wallet_charge_tetrapay')
async def customer_wallet_charge_tetrapay(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_tetrapay_enabled', True):
        await callback.answer('این روش شارژ فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    amount = int(data.get('wallet_charge_amount') or 0)
    if amount <= 0:
        await callback.answer('مبلغ شارژ پیدا نشد. دوباره تلاش کنید.', show_alert=True)
        return
    request_id = db.create_wallet_charge_request(callback.from_user.id, amount, 0, 'tetrapay')
    hash_id = f'wallet_{request_id}'
    try:
        invoice = await tetrapay.create_invoice(
            hash_id=hash_id,
            amount_toman=amount,
            description=f'شارژ کیف پول #{request_id}',
        )
    except Exception as exc:
        db.record_system_error('customer.wallet_charge_tetrapay', str(exc), entity_type='wallet_charge', entity_id=str(request_id), error_type=type(exc).__name__)
        await safe_callback_answer(callback, 'خطا در ساخت لینک پرداخت. جزئیات کامل در بخش خطاهای سیستم ثبت شد.', show_alert=True)
        return
    db.set_wallet_charge_provider(
        request_id,
        'tetrapay',
        provider_invoice_id=invoice.get('provider_invoice_id'),
        provider_payment_url=invoice.get('payment_url'),
        provider_status='pending',
        raw=invoice,
    )
    await state.clear()
    await edit_or_answer(
        callback,
        '💳 شارژ کیف پول با تتراپی\n\n'
        f'شماره درخواست: {copy_code(request_id)}\n'
        f'مبلغ شارژ: {format_toman(amount)}\n\n'
        'بعد از پرداخت، کیف پول شما خودکار شارژ می‌شود.',
        reply_markup=tetrapay_pay_link_keyboard(str(invoice['payment_url']), f'cust_wallet_tetrapay_check:{request_id}', 'cust_wallet_home'),
        parse_mode='Markdown',
    )
    await callback.answer('لینک پرداخت ساخته شد.')


@customer_router.callback_query(F.data.startswith('cust_wallet_tetrapay_check:'))
async def check_wallet_tetrapay(callback: CallbackQuery):
    request_id = int(callback.data.split(':', 1)[1])
    req = db.get_wallet_charge_request(request_id)
    if not req or int(req.get('user_id') or 0) != callback.from_user.id:
        await callback.answer('درخواست شارژ پیدا نشد.', show_alert=True)
        return
    if str(req.get('status') or '') == 'paid':
        await callback.answer('پرداخت تایید شده و کیف پول شارژ شده است ✅', show_alert=True)
        return
    authority = str(req.get('provider_invoice_id') or '')
    if not authority:
        await callback.answer('Authority پرداخت پیدا نشد. منتظر callback بمانید یا به پشتیبانی پیام بدهید.', show_alert=True)
        return
    try:
        verify_data = await tetrapay.verify_payment(authority)
    except Exception as exc:
        db.record_system_error('customer.check_wallet_tetrapay', str(exc), entity_type='wallet_charge', entity_id=str(request_id), error_type=type(exc).__name__)
        await callback.answer('اتصال به verify تتراپی برقرار نشد. تنظیمات TETRAPAY_VERIFY_URL و اینترنت سرور را چک کنید.', show_alert=True)
        return
    if verify_data:
        http_status = int(verify_data.get('_http_status') or 0)
        if http_status >= 400:
            db.record_system_error('customer.check_wallet_tetrapay.verify_pending_http', str(verify_data)[:800], entity_type='wallet_charge', entity_id=str(request_id), error_type='TetraPayVerifyNotPaid')
    if verify_data and tetrapay.callback_is_success(verify_data):
        expected = int(req.get('amount_toman') or 0)
        paid_amount = tetrapay.extract_amount(verify_data)
        if paid_amount and paid_amount < expected:
            await callback.answer('مبلغ پرداخت با درخواست شارژ همخوانی ندارد و نیازمند بررسی است.', show_alert=True)
            db.record_system_error('customer.check_wallet_tetrapay', f'کمتر بودن مبلغ پرداخت. expected={expected}, paid={paid_amount}', entity_type='wallet_charge', entity_id=str(request_id), error_type='AmountMismatch')
            return
        tracking_id = tetrapay.extract_tracking_id(verify_data) or authority
        db.mark_wallet_charge_paid_provider(request_id, 'tetrapay', tracking_id, verify_data)
        balance = db.wallet_balance(callback.from_user.id)
        await callback.message.answer(wallet_charge_result_text(request_id, balance), reply_markup=customer_keyboard())
        await callback.answer('پرداخت تایید شد و کیف پول شارژ شد ✅', show_alert=True)
        return
    await callback.answer('هنوز پرداخت توسط تتراپی تایید نشده است. اگر داخل ربات تتراپی مرحله احراز هویت یا پرداخت را می بینید، اول همان را کامل کنید و بعد دوباره بررسی بزنید.', show_alert=True)


@customer_router.callback_query(F.data == 'cust_wallet_charge_stars')
async def customer_wallet_charge_stars(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_stars_enabled', True):
        await callback.answer('این روش شارژ فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    amount = int(data.get('wallet_charge_amount') or 0)
    stars_amount = int(data.get('wallet_charge_stars') or 0)
    if amount <= 0 or stars_amount <= 0:
        await callback.answer('مبلغ شارژ پیدا نشد. دوباره تلاش کنید.', show_alert=True)
        return
    request_id = db.create_wallet_charge_request(callback.from_user.id, amount, stars_amount, 'stars')
    payload = f'wallet_charge:{request_id}'
    await callback.message.answer_invoice(
        title='شارژ کیف پول',
        description=f'شارژ کیف پول به مبلغ {format_toman(amount)}',
        payload=payload,
        provider_token='',
        currency='XTR',
        prices=[LabeledPrice(label='شارژ کیف پول با Stars', amount=stars_amount)],
    )
    await callback.answer('فاکتور شارژ ارسال شد.')


@customer_router.callback_query(F.data == 'cust_pay_crypto')
async def pay_with_crypto(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_plisio_enabled', True):
        await callback.answer('این روش پرداخت فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    user_id = callback.from_user.id
    final_toman = max(0, int(data.get('final_toman') or (int(data.get('price_toman') or 0) - int(data.get('discount_amount') or 0))))
    wallet_used = int(data.get('wallet_used') or 0)
    payable_toman = max(0, int(data.get('payable_toman') or (final_toman - wallet_used)))
    if payable_toman <= 0:
        await callback.answer('این سفارش مبلغ قابل پرداخت ندارد.', show_alert=True)
        return
    order_id = db.create_customer_order(
        user_id=user_id,
        package_id=str(data.get('package_id')),
        package_name=str(data.get('package_name')),
        customer_username=str(data.get('service_username')),
        amount_toman=final_toman,
        discount_amount=int(data.get('discount_amount') or 0),
        discount_code=data.get('coupon_code'),
        stars_amount=0,
        wallet_used=wallet_used,
        status='pending',
        order_type=str(data.get('order_type') or 'new'),
        service_id=data.get('renew_service_id'),
    )
    order_number = f'order_{order_id}'
    description = f'پرداخت رمز ارز سفارش #{order_id} - {data.get("package_name") or "سرویس"}'
    try:
        invoice = await plisio_pay.create_invoice(
            order_number=order_number,
            amount_toman=payable_toman,
            order_name=f'Order #{order_id}',
            description=description,
        )
    except Exception as exc:
        db.record_system_error('customer.pay_with_crypto', str(exc), entity_type='order', entity_id=str(order_id), error_type=type(exc).__name__)
        if isinstance(exc, plisio_pay.PlisioMinimumAmountError):
            user_msg = 'مبلغ این پرداخت برای پرداخت رمز ارز کمتر از حداقل مجاز است. لطفا یک روش پرداخت دیگر انتخاب کنید.'
        else:
            user_msg = 'در حال حاضر امکان ساخت فاکتور رمز ارز وجود ندارد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'
        await safe_callback_answer(callback, user_msg, show_alert=True)
        try:
            await callback.message.answer('⚠️ ' + user_msg)
        except Exception:
            pass
        return
    db.set_order_payment_provider(
        order_id,
        'plisio',
        payable_toman,
        provider_invoice_id=invoice.get('provider_invoice_id'),
        provider_payment_url=invoice.get('payment_url'),
        provider_status='pending',
        raw=invoice,
    )
    await state.clear()
    await edit_or_answer(
        callback,
        '₿ پرداخت با رمز ارز\n\n'
        f'شماره سفارش: {copy_code(order_id)}\n'
        f'مبلغ قابل پرداخت: {format_toman(payable_toman)}\n'
        f'مبلغ دلاری فاکتور: {invoice.get("source_amount_usd")} USD\n'
        f'نرخ دلار استفاده شده: {int(invoice.get("usd_rate_toman") or 0):,} تومان\n\n'
        'بعد از پرداخت، نتیجه از طریق Callback به ربات اعلام می شود و سرویس خودکار ساخته می شود.',
        reply_markup=crypto_pay_link_keyboard(str(invoice['payment_url']), f'cust_plisio_check:{order_id}'),
        parse_mode='Markdown',
    )
    await callback.answer('لینک پرداخت رمز ارز ساخته شد.')


@customer_router.callback_query(F.data.startswith('cust_plisio_check:'))
async def check_plisio_order(callback: CallbackQuery):
    order_id = int(callback.data.split(':', 1)[1])
    order = db.get_order(order_id)
    if not order or int(order.get('user_id') or 0) != callback.from_user.id:
        await callback.answer('سفارش پیدا نشد.', show_alert=True)
        return
    status = str(order.get('status') or 'pending')
    if status == 'service_ready':
        await callback.answer('پرداخت تایید شده و سرویس آماده است ✅', show_alert=True)
        return
    txn_id = str(order.get('provider_invoice_id') or '')
    if not txn_id:
        await callback.answer('شناسه فاکتور Plisio پیدا نشد. منتظر callback بمانید یا به پشتیبانی پیام بدهید.', show_alert=True)
        return
    try:
        op = await plisio_pay.get_operation(txn_id)
    except Exception as exc:
        db.record_system_error('customer.check_plisio_order', str(exc), entity_type='order', entity_id=str(order_id), error_type=type(exc).__name__)
        await callback.answer('بررسی پرداخت فعلا انجام نشد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.', show_alert=True)
        return
    if plisio_pay.is_success(op):
        expected = int(order.get('payable_toman') or max(0, int(order.get('amount_toman') or order.get('amount') or 0) - int(order.get('wallet_used') or 0)))
        db.mark_order_provider_paid(order_id, 'plisio', expected, txn_id, op)
        refreshed = db.get_order(order_id) or order
        await callback.answer('پرداخت رمز ارز تایید شد. سرویس در حال آماده سازی است ✅', show_alert=True)
        await finish_paid_order(callback.message, refreshed, 0, None, txn_id)
        return
    if plisio_pay.is_final_failed(op):
        await callback.answer('پرداخت رمز ارز ناموفق یا منقضی شده است.', show_alert=True)
        return
    await callback.answer('هنوز پرداخت رمز ارز تایید نشده است. بعد از واریز و تایید شبکه دوباره بررسی بزنید.', show_alert=True)


@customer_router.callback_query(F.data == 'cust_wallet_charge_crypto')
async def customer_wallet_charge_crypto(callback: CallbackQuery, state: FSMContext):
    if not _gateway_enabled('payment_plisio_enabled', True):
        await callback.answer('این روش شارژ فعلا غیرفعال است.', show_alert=True)
        return
    data = await state.get_data()
    amount = int(data.get('wallet_charge_amount') or 0)
    if amount <= 0:
        await callback.answer('مبلغ شارژ پیدا نشد. دوباره تلاش کنید.', show_alert=True)
        return
    request_id = db.create_wallet_charge_request(callback.from_user.id, amount, 0, 'plisio')
    order_number = f'wallet_{request_id}'
    try:
        invoice = await plisio_pay.create_invoice(
            order_number=order_number,
            amount_toman=amount,
            order_name=f'Wallet #{request_id}',
            description=f'شارژ کیف پول #{request_id}',
        )
    except Exception as exc:
        db.record_system_error('customer.wallet_charge_crypto', str(exc), entity_type='wallet_charge', entity_id=str(request_id), error_type=type(exc).__name__)
        if isinstance(exc, plisio_pay.PlisioMinimumAmountError):
            user_msg = 'مبلغ شارژ برای پرداخت رمز ارز کمتر از حداقل مجاز است. لطفا مبلغ بیشتری وارد کنید یا یک روش پرداخت دیگر انتخاب کنید.'
        else:
            user_msg = 'در حال حاضر امکان ساخت فاکتور رمز ارز وجود ندارد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'
        await safe_callback_answer(callback, user_msg, show_alert=True)
        try:
            await callback.message.answer('⚠️ ' + user_msg)
        except Exception:
            pass
        return
    db.set_wallet_charge_provider(
        request_id,
        'plisio',
        provider_invoice_id=invoice.get('provider_invoice_id'),
        provider_payment_url=invoice.get('payment_url'),
        provider_status='pending',
        raw=invoice,
    )
    await state.clear()
    await edit_or_answer(
        callback,
        '₿ شارژ کیف پول با رمز ارز\n\n'
        f'شماره درخواست: {copy_code(request_id)}\n'
        f'مبلغ شارژ: {format_toman(amount)}\n'
        f'مبلغ دلاری فاکتور: {invoice.get("source_amount_usd")} USD\n'
        f'نرخ دلار استفاده شده: {int(invoice.get("usd_rate_toman") or 0):,} تومان\n\n'
        'بعد از پرداخت، کیف پول شما خودکار شارژ می شود.',
        reply_markup=crypto_pay_link_keyboard(str(invoice['payment_url']), f'cust_wallet_plisio_check:{request_id}', 'cust_wallet_home'),
        parse_mode='Markdown',
    )
    await callback.answer('لینک پرداخت رمز ارز ساخته شد.')


@customer_router.callback_query(F.data.startswith('cust_wallet_plisio_check:'))
async def check_wallet_plisio(callback: CallbackQuery):
    request_id = int(callback.data.split(':', 1)[1])
    req = db.get_wallet_charge_request(request_id)
    if not req or int(req.get('user_id') or 0) != callback.from_user.id:
        await callback.answer('درخواست شارژ پیدا نشد.', show_alert=True)
        return
    if str(req.get('status') or '') == 'paid':
        await callback.answer('پرداخت تایید شده و کیف پول شارژ شده است ✅', show_alert=True)
        return
    txn_id = str(req.get('provider_invoice_id') or '')
    if not txn_id:
        await callback.answer('شناسه فاکتور Plisio پیدا نشد. منتظر callback بمانید یا به پشتیبانی پیام بدهید.', show_alert=True)
        return
    try:
        op = await plisio_pay.get_operation(txn_id)
    except Exception as exc:
        db.record_system_error('customer.check_wallet_plisio', str(exc), entity_type='wallet_charge', entity_id=str(request_id), error_type=type(exc).__name__)
        await callback.answer('بررسی پرداخت فعلا انجام نشد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.', show_alert=True)
        return
    if plisio_pay.is_success(op):
        db.mark_wallet_charge_paid_provider(request_id, 'plisio', txn_id, op)
        balance = db.wallet_balance(callback.from_user.id)
        await callback.message.answer(wallet_charge_result_text(request_id, balance, 'کیف پول شما با پرداخت رمز ارز شارژ شد ✅'), reply_markup=customer_keyboard())
        await callback.answer('پرداخت تایید شد و کیف پول شارژ شد ✅', show_alert=True)
        return
    if plisio_pay.is_final_failed(op):
        await callback.answer('پرداخت رمز ارز ناموفق یا منقضی شده است.', show_alert=True)
        return
    await callback.answer('هنوز پرداخت رمز ارز تایید نشده است. بعد از واریز و تایید شبکه دوباره بررسی بزنید.', show_alert=True)


@customer_router.message(CustomerButtonFilter('support'))
async def support_start(message: Message, state: FSMContext):
    support_mode = db.get_setting('support_mode', 'support_message')
    support = db.get_setting('support_username', settings.support_username)

    if support_mode == 'support_contact':
        # Mode A: Contact Only. Do not start a ticket flow; show the contact.
        contact_display = str(support or '').strip()
        if not contact_display:
            contact_display = 'پشتیبانی در حال حاضر تنظیم نشده است.'
        elif not contact_display.startswith(('http://', 'https://', '@')):
            contact_display = f'@{contact_display}'

        text = (
            '📞 <b>ارتباط با پشتیبانی</b>\n\n'
            'جهت ارتباط با بخش پشتیبانی می‌توانید از طریق آیدی یا لینک زیر اقدام فرمایید:\n\n'
            f'💬 {contact_display}'
        )
        # Add direct inline button if it's a URL or username
        markup = None
        clean_contact = contact_display.lstrip('@')
        if contact_display.startswith(('http://', 'https://')):
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='پیام به پشتیبانی', url=contact_display)]])
        elif contact_display.startswith('@') and clean_contact:
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='پیام به پشتیبانی', url=f'https://example.com/{clean_contact}')]])

        await message.answer(text, parse_mode='HTML', reply_markup=markup)
        return

    # Mode B: Receive Support Messages Inside Bot (Ticket flow)
    await state.set_state(CustomerTicketState.waiting_subject)
    custom = db.get_setting('support_text', '')
    if custom:
        await answer_template(message, custom, user_template_context(message.from_user, support=support), content_key='support_text')
    await message.answer('🎫 پشتیبانی\n\nموضوع تیکت را ارسال کنید.', reply_markup=ticket_cancel_keyboard())


@customer_router.message(CustomerTicketState.waiting_subject)
async def support_subject(message: Message, state: FSMContext):
    subject = (message.text or '').strip()
    if not subject:
        await message.answer('موضوع نمی تواند خالی باشد.')
        return
    await state.update_data(ticket_subject=subject)
    await state.set_state(CustomerTicketState.waiting_message)
    await message.answer('پیام خود را ارسال کنید.', reply_markup=ticket_cancel_keyboard())


@customer_router.message(CustomerTicketState.waiting_message)
async def support_message(message: Message, state: FSMContext):
    text = (message.text or '').strip()
    if not text:
        await message.answer('پیام نمی تواند خالی باشد.')
        return
    data = await state.get_data()
    subject = data.get('ticket_subject') or 'پشتیبانی'
    user_id = message.from_user.id if message.from_user else None
    ts = db.now_iso()
    with db.connect() as conn:
        cur = conn.execute(
            'INSERT INTO tickets (user_id, subject, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, subject, 'open', ts, ts),
        )
        ticket_id = int(cur.lastrowid)
        conn.execute(
            'INSERT INTO ticket_messages (ticket_id, sender_id, message, created_at) VALUES (?, ?, ?, ?)',
            (ticket_id, user_id, text, ts),
        )
        conn.commit()
    db.add_notification('ticket', f'تیکت جدید #{ticket_id} از کاربر {user_id}')
    
    # Real-time immediate admin notification
    try:
        from modules.tickets import notify_admins_new_ticket
        await notify_admins_new_ticket(message.bot, ticket_id, int(user_id), subject, text, ts)
    except Exception as exc:
        db.record_system_error('customer.support_message.notify_admin', str(exc), entity_type='ticket', entity_id=str(ticket_id), error_type=type(exc).__name__)

    await state.clear()
    await message.answer(
        f'تیکت شما ثبت شد ✅\n\nشماره تیکت: {copy_code(ticket_id)}',
        reply_markup=customer_keyboard(),
        parse_mode='Markdown',
    )


@customer_router.callback_query(F.data == 'cust_cancel')
async def customer_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_or_answer(callback, 'عملیات لغو شد. از منوی پایین استفاده کنید.')
    support = db.get_setting('support_username', settings.support_username)
    await answer_template(callback.message, customer_menu_title(), user_template_context(callback.from_user, support=support), reply_markup=customer_keyboard())
    await callback.answer('لغو شد.')


@customer_router.message(CustomerButtonFilter('rules'))
async def rules_text(message: Message):
    support = db.get_setting('support_username', settings.support_username)
    await answer_template(
        message,
        db.get_setting('rules_text', 'قوانین هنوز تنظیم نشده است.'),
        user_template_context(message.from_user, support=support),
        reply_markup=customer_keyboard(),
        content_key='rules_text',
    )
