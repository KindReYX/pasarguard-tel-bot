from __future__ import annotations

from html import escape
import html
import time
import json
import asyncio

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

import api_client
import db
from utils import edit_or_answer, person_display, get_button_style, infer_button_kind, message_text_with_custom_emoji_tokens, friendly_error
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_USERS, BTN_CANCEL
from permissions import is_staff, is_admin, deny_message, deny_callback, can_message_customer, get_role


users_router = Router()


class UserStates(StatesGroup):
    search = State()
    private_message = State()


class AdminServiceStates(StatesGroup):
    waiting_add_days = State()
    waiting_add_traffic = State()


def code(value) -> str:
    text = '-' if value is None else str(value)
    return f'<code>{escape(text)}</code>'


def plain(value) -> str:
    return escape('-' if value is None or value == '' else str(value))


def users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📋 لیست کاربران', callback_data='users:list:0')],
        [InlineKeyboardButton(text='🔍 جستجوی کاربر', callback_data='users:search')],
        [InlineKeyboardButton(text='📩 پیام به کاربر', callback_data='users:pm')],
    ])


def users_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔙 برگشت به مدیریت کاربران', callback_data='users:home')],
    ])


def user_action_keyboard(user_id: int) -> InlineKeyboardMarkup:
    row = db.fetchone('SELECT is_blocked FROM bot_users WHERE telegram_id=?', (int(user_id),)) or {}
    blocked = int(row.get('is_blocked') or 0) == 1
    block_text = '✅ رفع مسدودی' if blocked else '🚫 مسدود کردن کاربر'
    # Count services for badge
    try:
        svc_row = db.fetchone('SELECT COUNT(*) as c FROM user_services WHERE telegram_id=?', (int(user_id),))
        svc_count = int(svc_row['c']) if svc_row else 0
    except Exception:
        svc_count = 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'📦 سرویس‌ها ({svc_count})', callback_data=f'users:services:{user_id}:0', style=get_button_style('primary'))],
        [InlineKeyboardButton(text='📩 ارسال پیام', callback_data=f'users:pm_to:{user_id}', style=get_button_style('primary'))],
        [InlineKeyboardButton(text=block_text, callback_data=f'users:block_confirm:{user_id}', style=get_button_style('danger' if not blocked else 'success'))],
        [InlineKeyboardButton(text='🔙 برگشت به لیست کاربران', callback_data='users:list:0')],
    ])


def user_row_button(row: dict) -> InlineKeyboardButton:
    user_id = row.get('telegram_id')
    title = row.get('first_name') or row.get('username') or str(user_id)
    username = row.get('username')
    suffix = f' @{username}' if username else ''
    return InlineKeyboardButton(
        text=f'👤 {title}{suffix} - {user_id}',
        callback_data=f'users:view:{user_id}',
    )


def user_detail_text(row: dict) -> str:
    role = row.get('role') or 'user'
    return (
        '👤 اطلاعات کاربر\n\n'
        f'آیدی عددی: {code(row.get("telegram_id"))}\n'
        f'یوزرنیم: {code("@" + row.get("username") if row.get("username") else "-")}\n'
        f'نام: {plain(row.get("first_name"))}\n'
        f'نقش: {code(role)}\n'
        f'فعال: {code(row.get("is_active"))}\n'
        f'بلاک: {code(row.get("is_blocked"))}\n'
        f'فروشنده/مالک: {code(row.get("owner_admin_id") or "-")}\n'
        f'تاریخ عضویت: {code(row.get("created_at") or "-")}\n'
        f'آخرین بروزرسانی: {code(row.get("updated_at") or "-")}\n'
    )


def _format_bytes(value) -> str:
    try:
        size = int(value or 0)
    except Exception:
        return 'نامشخص'
    if size <= 0:
        return 'نامحدود' if size == 0 else '0 B'
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    f = float(size)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            if unit == 'B':
                return f'{size} B'
            return f'{f:.2f} {unit}'
        f /= 1024
    return f'{size} B'


def _format_timestamp(value) -> str:
    if value in (None, '', 0, '0'):
        return 'نامشخص'
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        import datetime
        return datetime.datetime.fromtimestamp(ts).strftime('%Y/%m/%d %H:%M')
    except Exception:
        try:
            import datetime
            return datetime.datetime.fromisoformat(str(value)).strftime('%Y/%m/%d %H:%M')
        except Exception:
            return str(value)


def _format_remaining(expire) -> str:
    try:
        exp = api_client.panel_timestamp(expire)
        now = int(time.time())
        if exp <= 0:
            return 'نامشخص'
        diff = exp - now
        if diff <= 0:
            return 'تمام شده'
        d = diff // 86400
        h = (diff % 86400) // 3600
        if d > 0:
            return f'{d} روز {h} ساعت باقی'
        if h > 0:
            return f'{h} ساعت باقی'
        m = (diff % 3600)//60
        return f'{m} دقیقه باقی'
    except Exception:
        return 'نامشخص'


def _service_status_emoji(service: dict, user_data: dict | None = None) -> str:
    status = str((user_data or {}).get('status') or service.get('status') or '').lower()
    is_active = int(service.get('is_active', 1) or 0)
    if not is_active:
        return '🗑 غیرفعال (DB)'
    if status in ('active','enabled','on'):
        return '✅ فعال'
    if status in ('disabled','inactive','off'):
        return '⛔ غیرفعال'
    if status == 'expired':
        return '⏳ منقضی'
    if status == 'limited':
        return '💢 محدود'
    return status or '❔ نامشخص'


def build_admin_service_detail_text(service: dict, user_data: dict | None = None) -> str:
    user_data = user_data or {}
    # Try to parse raw_json if user_data empty
    if not user_data and service.get('raw_json'):
        try:
            user_data = json.loads(service.get('raw_json') or '{}')
        except Exception:
            user_data = {}
    expire = user_data.get('expire') or service.get('expire')
    data_limit = int(user_data.get('data_limit') or service.get('data_limit') or 0)
    used = int(user_data.get('used_traffic') or service.get('used_traffic') or 0)
    remaining = max(0, data_limit - used) if data_limit > 0 else 0
    sub_url = service.get('subscription_url') or api_client.extract_subscription_url(user_data) or '-'
    return (
        f'📦 جزئیات سرویس\n\n'
        f'🆔 آی‌دی سرویس: {code(service.get("id"))}\n'
        f'👤 نام سرویس: {code(service.get("service_username"))}\n'
        f'🔑 پنل یوزرنیم: {code(service.get("panel_username") or service.get("service_username"))}\n'
        f'📦 بسته: {code(service.get("package_name") or service.get("package_id") or "-")}\n'
        f'📊 وضعیت: {code(_service_status_emoji(service, user_data))}\n'
        f'🗃 is_active (DB): {code(service.get("is_active"))}\n'
        f'🔗 لینک ساب: {code(sub_url)}\n\n'
        f'💾 حجم کل: {code(_format_bytes(data_limit))}\n'
        f'📥 مصرف شده: {code(_format_bytes(used))}\n'
        f'💢 باقی‌مانده: {code(_format_bytes(remaining))}\n\n'
        f'📅 انقضا: {code(_format_timestamp(expire))} ({plain(_format_remaining(expire))})\n'
        f'📶 آخرین اتصال: {code(_format_timestamp(user_data.get("online_at") or service.get("online_at")))}\n'
        f'🕒 آخرین آپدیت ساب: {code(service.get("last_subscription_update") or service.get("updated_at") or "-")}\n'
        f'🧾 سفارش: {code(service.get("order_id") or "-")}\n'
        f'👤 مالک تلگرام: {code(service.get("telegram_id"))}\n'
    )


def build_users_list_text(rows: list[dict], offset: int, total: int) -> str:
    if not rows:
        return 'کاربری پیدا نشد.'
    text = f'📋 لیست کاربران\n{code(offset + 1)} تا {code(offset + len(rows))} از {code(total)}\n\n'
    for row in rows:
        text += (
            f'👤 {plain(row.get("first_name") or row.get("username") or "بدون نام")}\n'
            f'آیدی: {code(row.get("telegram_id"))}\n'
            f'یوزرنیم: {code("@" + row.get("username") if row.get("username") else "-")}\n'
            f'بلاک: {code(row.get("is_blocked"))}\n\n'
        )
    return text


def build_users_list_keyboard(rows: list[dict], offset: int, total: int) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for row in rows:
        kb.append([user_row_button(row)])

    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'users:list:{max(0, offset - 10)}'))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'users:list:{offset + 10}'))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton(text='🔍 جستجو', callback_data='users:search')])
    kb.append([InlineKeyboardButton(text='🔙 مدیریت کاربران', callback_data='users:home')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_user_for_actor(actor_id: int, target_user_id: int) -> dict | None:
    row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (int(target_user_id),))
    if not row:
        return None
    if get_role(actor_id) == 'seller' and row.get('owner_admin_id') != actor_id:
        return None
    return row


def can_manage_services(actor_id: int | None, target_user_id: int) -> bool:
    if not is_staff(actor_id):
        return False
    role = get_role(actor_id)
    if role in ('super_admin', 'admin'):
        return True
    if role == 'seller':
        row = db.fetchone('SELECT owner_admin_id FROM bot_users WHERE telegram_id=?', (int(target_user_id),))
        return bool(row and int(row.get('owner_admin_id') or 0) == int(actor_id))
    return False


def build_user_services_list_text(user_id: int, rows: list[dict], offset: int, total: int) -> str:
    user = db.fetchone('SELECT username, first_name FROM bot_users WHERE telegram_id=?', (int(user_id),)) or {}
    title = user.get('first_name') or user.get('username') or str(user_id)
    header = f'📦 سرویس‌های کاربر {plain(title)} {code(user_id)}\n'
    header += f'{code(offset+1)} تا {code(offset+len(rows))} از {code(total)} | فعال: {code(len([r for r in rows if int(r.get("is_active") or 0)==1]))}\n\n'
    if not rows:
        header += 'این کاربر هیچ سرویسی ندارد.'
        return header
    for r in rows:
        status_emoji = _service_status_emoji(r)
        active_flag = '🟢' if int(r.get('is_active') or 0) else '🔴'
        header += (
            f'{active_flag} {plain(r.get("service_username"))} | {plain(r.get("package_name") or "-")}\n'
            f'   وضعیت: {plain(status_emoji)} | انقضا: {plain(_format_remaining(r.get("expire")))}\n'
            f'   حجم: {_format_bytes(r.get("data_limit"))} / مصرف: {_format_bytes(r.get("used_traffic"))}\n'
            f'   آی‌دی: {code(r.get("id"))}\n\n'
        )
    return header


def build_user_services_keyboard(user_id: int, rows: list[dict], offset: int, total: int) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for r in rows:
        sid = int(r.get('id'))
        is_active = int(r.get('is_active') or 0)
        icon = '🟢' if is_active else '🔴'
        label = f'{icon} {r.get("service_username")} | {r.get("package_name") or "-"}'
        # truncate for button
        if len(label) > 48:
            label = label[:47] + '…'
        kb.append([InlineKeyboardButton(text=label, callback_data=f'users:svc_view:{sid}')])
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'users:services:{user_id}:{max(0, offset-10)}'))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'users:services:{user_id}:{offset+10}'))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text='🔙 برگشت به کاربر', callback_data=f'users:view:{user_id}')])
    kb.append([InlineKeyboardButton(text='🔙 لیست کاربران', callback_data='users:list:0')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_admin_service_keyboard(service: dict) -> InlineKeyboardMarkup:
    sid = int(service.get('id'))
    uid = int(service.get('telegram_id'))
    # Determine current panel status for toggle label
    try:
        raw = service.get('raw_json') or '{}'
        jd = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        jd = {}
    status = str(jd.get('status') or service.get('status') or '').lower()
    is_active_db = int(service.get('is_active') or 0)
    if status in ('active','enabled') and is_active_db:
        toggle_text = '⛔ غیرفعال کردن (پنل)'
        toggle_cb = f'users:svc_toggle:{sid}'
    elif not is_active_db:
        toggle_text = '✅ فعال‌سازی مجدد (DB+پنل)'
        toggle_cb = f'users:svc_toggle:{sid}'
    else:
        toggle_text = '✅ فعال کردن (پنل)'
        toggle_cb = f'users:svc_toggle:{sid}'

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ افزودن روز', callback_data=f'users:svc_add_days:{sid}'),
         InlineKeyboardButton(text='➕ افزودن حجم (GB)', callback_data=f'users:svc_add_traffic:{sid}')],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [InlineKeyboardButton(text='♻️ ریووک / تغییر لینک ساب', callback_data=f'users:svc_revoke:{sid}')],
        [InlineKeyboardButton(text='🔄 بروزرسانی از پنل', callback_data=f'users:svc_refresh:{sid}')],
        [InlineKeyboardButton(text='🗑 حذف سرویس', callback_data=f'users:svc_delete_confirm:{sid}')],
        [InlineKeyboardButton(text='🔙 برگشت به سرویس‌ها', callback_data=f'users:services:{uid}:0')],
        [InlineKeyboardButton(text=f'👤 برگشت به کاربر {uid}', callback_data=f'users:view:{uid}')],
    ])


@users_router.message(EditableButtonFilter(BTN_USERS))
async def users_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        await deny_message(message)
        return
    await message.answer('👥 مدیریت کاربران', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه های زیر را انتخاب کنید:', reply_markup=users_menu())


@users_router.callback_query(F.data == 'users:home')
async def users_home_callback(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    await state.clear()
    await edit_or_answer(callback, '👥 مدیریت کاربران\n\nیکی از گزینه های زیر را انتخاب کنید:', reply_markup=users_menu())
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:list:'))
async def users_list(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return

    try:
        offset = int(callback.data.split(':')[-1])
    except Exception:
        offset = 0

    role = get_role(uid)
    if role == 'seller':
        rows = db.fetchall(
            'SELECT * FROM bot_users WHERE owner_admin_id=? ORDER BY created_at DESC LIMIT 10 OFFSET ?',
            (uid, offset),
        )
        total = db.user_count('WHERE owner_admin_id=?', (uid,))
    else:
        rows = db.fetchall('SELECT * FROM bot_users ORDER BY created_at DESC LIMIT 10 OFFSET ?', (offset,))
        total = db.user_count()

    await edit_or_answer(
        callback,
        build_users_list_text(rows, offset, total),
        reply_markup=build_users_list_keyboard(rows, offset, total),
        parse_mode='HTML',
    )
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:view:'))
async def users_view(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        target_user_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه کاربر نامعتبر است.', show_alert=True)
        return
    row = get_user_for_actor(uid, target_user_id)
    if not row:
        await callback.answer('کاربر پیدا نشد یا دسترسی ندارید.', show_alert=True)
        return
    await edit_or_answer(callback, user_detail_text(row), reply_markup=user_action_keyboard(target_user_id), parse_mode='HTML')
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:services:'))
async def users_services_list(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        # data = users:services:{user_id}:{offset}
        parts = callback.data.split(':')
        target_user_id = int(parts[2])
        offset = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        await callback.answer('شناسه نامعتبر است.', show_alert=True)
        return
    if not can_manage_services(uid, target_user_id):
        await deny_callback(callback)
        return
    # Fetch total
    row = db.fetchone('SELECT COUNT(*) as c FROM user_services WHERE telegram_id=?', (int(target_user_id),))
    total = int(row['c']) if row else 0
    rows = db.fetchall(
        'SELECT * FROM user_services WHERE telegram_id=? ORDER BY is_active DESC, expire DESC, updated_at DESC, id DESC LIMIT 10 OFFSET ?',
        (int(target_user_id), int(offset)),
    )
    # Auto-refresh from panel for accurate used/traffic/expire (fix: previously required manual refresh)
    # Refresh each service concurrently, update snapshot, then re-read rows for display
    if rows:
        async def _refresh_one(svc: dict) -> dict:
            panel_username = svc.get('panel_username') or svc.get('service_username')
            if not panel_username:
                return svc
            try:
                # Use timeout to avoid blocking whole list on one slow panel call
                user_data = await asyncio.wait_for(api_client.get_user(str(panel_username)), timeout=6)
                sub_url = api_client.extract_subscription_url(user_data) or svc.get('subscription_url')
                links = api_client.extract_config_links(user_data)
                db.update_user_service_snapshot(int(svc['id']), user_data, sub_url, links)
                updated = db.fetchone('SELECT * FROM user_services WHERE id=?', (int(svc['id']),))
                return updated or svc
            except asyncio.TimeoutError:
                try:
                    db.record_system_error('users.services_auto_refresh_timeout', f'timeout {panel_username}', user_id=uid, entity_type='service', entity_id=str(svc.get('id')))
                except Exception:
                    pass
                return svc
            except Exception as e:
                # Keep cached if not found or panel error; still show stale data
                try:
                    # Don't spam on 404; only log transient
                    msg = str(e).lower()
                    if 'not found' not in msg and '404' not in msg:
                        db.record_system_error('users.services_auto_refresh', str(e), user_id=uid, entity_type='service', entity_id=str(svc.get('id')), error_type=type(e).__name__)
                except Exception:
                    pass
                return svc
        try:
            # Inform admin that live data is being fetched (optional quick answer)
            # We already have rows; concurrently refresh up to 10 services
            refreshed = await asyncio.gather(*[_refresh_one(s) for s in rows])
            rows = list(refreshed)
        except Exception:
            pass
    text = build_user_services_list_text(int(target_user_id), rows, int(offset), total)
    # Indicate live data
    if rows:
        text += '\n\n<i>🔄 اطلاعات مصرف و انقضا به‌صورت خودکار از پنل بروز شد.</i>'
    kb = build_user_services_keyboard(int(target_user_id), rows, int(offset), total)
    await edit_or_answer(callback, text, reply_markup=kb, parse_mode='HTML')
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:svc_view:'))
async def admin_service_view(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه سرویس نامعتبر است.', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    target_user_id = int(svc.get('telegram_id'))
    if not can_manage_services(uid, target_user_id):
        await deny_callback(callback)
        return
    # Auto-refresh from panel so used_traffic/expire/status are live (fix: previously needed manual update)
    user_data = {}
    try:
        raw = svc.get('raw_json') or '{}'
        user_data = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        user_data = {}
    panel_username = svc.get('panel_username') or svc.get('service_username')
    refreshed_from_panel = False
    if panel_username:
        try:
            live_data = await asyncio.wait_for(api_client.get_user(str(panel_username)), timeout=6)
            sub_url = api_client.extract_subscription_url(live_data) or svc.get('subscription_url')
            links = api_client.extract_config_links(live_data)
            db.update_user_service_snapshot(int(svc_id), live_data, sub_url, links)
            svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (int(svc_id),)) or svc
            user_data = live_data
            refreshed_from_panel = True
        except asyncio.TimeoutError:
            try:
                db.record_system_error('users.svc_view_auto_refresh_timeout', f'timeout {panel_username}', user_id=uid, entity_type='service', entity_id=str(svc_id))
            except Exception:
                pass
        except Exception as e:
            msg = str(e).lower()
            if 'not found' not in msg and '404' not in msg:
                try:
                    db.record_system_error('users.svc_view_auto_refresh', str(e), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(e).__name__)
                except Exception:
                    pass
            # keep cached user_data
    text = build_admin_service_detail_text(svc, user_data)
    if refreshed_from_panel:
        text += '\n\n<i>🔄 اطلاعات به‌صورت خودکار از پنل بروز شد.</i>'
    kb = build_admin_service_keyboard(svc)
    await edit_or_answer(callback, text, reply_markup=kb, parse_mode='HTML')
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:svc_refresh:'))
async def admin_service_refresh(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    if not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    panel_username = svc.get('panel_username') or svc.get('service_username')
    try:
        user_data = await api_client.get_user(str(panel_username))
        sub_url = api_client.extract_subscription_url(user_data) or svc.get('subscription_url')
        links = api_client.extract_config_links(user_data)
        db.update_user_service_snapshot(svc_id, user_data, sub_url, links)
        refreshed = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,)) or svc
        text = build_admin_service_detail_text(refreshed, user_data)
        kb = build_admin_service_keyboard(refreshed)
        await edit_or_answer(callback, text, reply_markup=kb, parse_mode='HTML')
        await callback.answer('بروزرسانی شد ✅', show_alert=False)
    except Exception as exc:
        db.record_system_error('users.admin_service_refresh', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)


@users_router.callback_query(F.data.startswith('users:svc_revoke:'))
async def admin_service_revoke(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    if not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    panel_username = svc.get('panel_username') or svc.get('service_username')
    try:
        result = await api_client.revoke_user_subscription(str(panel_username))
        user_data = await api_client.get_user(str(panel_username))
        sub_url = api_client.extract_subscription_url(user_data) or api_client.extract_subscription_url(result) or svc.get('subscription_url')
        links = api_client.extract_config_links(user_data) or api_client.extract_config_links(result)
        db.update_user_service_snapshot(svc_id, user_data, sub_url, links)
        refreshed = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,)) or svc
        db.add_log(uid, 'admin_revoke_service', 'service', str(svc_id), {'panel_username': panel_username})
        text = build_admin_service_detail_text(refreshed, user_data) + '\n\n✅ لینک ساب تغییر کرد (revoke شد).'
        kb = build_admin_service_keyboard(refreshed)
        await edit_or_answer(callback, text, reply_markup=kb, parse_mode='HTML')
        await callback.answer('لینک با موفقیت تغییر کرد ♻️', show_alert=True)
    except Exception as exc:
        db.record_system_error('users.admin_service_revoke', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)


@users_router.callback_query(F.data.startswith('users:svc_toggle:'))
async def admin_service_toggle(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await callback.answer('سرویس پیدا نشد.', show_alert=True)
        return
    if not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    panel_username = svc.get('panel_username') or svc.get('service_username')
    try:
        current_user = await api_client.get_user(str(panel_username))
        cur_status = str(current_user.get('status') or svc.get('status') or '').lower()
        is_active_db = int(svc.get('is_active') or 0)
        # Decide new status: if currently active -> disable, else active
        if cur_status in ('active', 'enabled', 'on') and is_active_db:
            new_status = 'disabled'
            action_label = 'غیرفعال شد'
        else:
            new_status = 'active'
            action_label = 'فعال شد'
        updated = await api_client.update_user(str(panel_username), {'status': new_status})
        # Update DB is_active accordingly
        new_is_active = 1 if new_status == 'active' else 0
        # If reactivating DB-disabled, set is_active=1
        if new_status == 'active':
            db.execute('UPDATE user_services SET is_active=1, status=?, updated_at=? WHERE id=?', (new_status, db.now_iso(), svc_id))
        else:
            # Keep DB record but mark inactive? For panel disable we keep is_active=1 but status disabled
            # For full deactivate, we could keep is_active=1 and just update status; user asked deactive toggle
            # So we keep DB active but panel disabled; only if admin wants full hide they use delete
            db.execute('UPDATE user_services SET status=?, updated_at=? WHERE id=?', (new_status, db.now_iso(), svc_id))
        # Also snapshot
        sub_url = api_client.extract_subscription_url(updated) or svc.get('subscription_url')
        links = api_client.extract_config_links(updated)
        db.update_user_service_snapshot(svc_id, updated, sub_url, links)
        # Ensure is_active persisted if needed
        if new_status == 'active' and not is_active_db:
            db.execute('UPDATE user_services SET is_active=1 WHERE id=?', (svc_id,))
        refreshed = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,)) or svc
        db.add_log(uid, 'admin_toggle_service', 'service', str(svc_id), {'panel_username': panel_username, 'new_status': new_status})
        text = build_admin_service_detail_text(refreshed, updated) + f'\n\n✅ سرویس {action_label}.'
        kb = build_admin_service_keyboard(refreshed)
        await edit_or_answer(callback, text, reply_markup=kb, parse_mode='HTML')
        await callback.answer(action_label, show_alert=True)
    except Exception as exc:
        db.record_system_error('users.admin_service_toggle', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)


@users_router.callback_query(F.data.startswith('users:svc_add_days:'))
async def admin_service_add_days_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc or not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    await state.set_state(AdminServiceStates.waiting_add_days)
    await state.update_data(admin_svc_id=svc_id)
    await edit_or_answer(callback, f'➕ افزودن روز به سرویس {code(svc.get("service_username"))}\n\nتعداد روز را ارسال کنید. مثال: <code>30</code> برای ۳۰ روز، یا <code>-7</code> برای کم کردن ۷ روز.\nبرای لغو /cancel بفرستید.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data=f'users:svc_view:{svc_id}')]]), parse_mode='HTML')
    await callback.answer()


@users_router.message(AdminServiceStates.waiting_add_days)
async def admin_service_add_days_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        await state.clear()
        return
    data = await state.get_data()
    svc_id = int(data.get('admin_svc_id') or 0)
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await state.clear()
        await message.answer('سرویس پیدا نشد.')
        return
    if not can_manage_services(uid, int(svc.get('telegram_id'))):
        await state.clear()
        await message.answer('دسترسی ندارید.')
        return
    text = (message.text or '').strip()
    if text.lower() in ('/cancel', 'cancel', '❌', 'انصراف'):
        await state.clear()
        await message.answer('لغو شد.', reply_markup=back_keyboard())
        return
    try:
        days = int(text.replace(',', '').strip())
        if days == 0 or abs(days) > 3650:
            raise ValueError('days out of range')
    except Exception:
        await message.answer('عدد نامعتبر است. لطفا تعداد روز را به صورت عدد صحیح وارد کنید. مثال: 30 یا -5')
        return
    panel_username = svc.get('panel_username') or svc.get('service_username')
    try:
        current_user = await api_client.get_user(str(panel_username))
        cur_expire = api_client.panel_timestamp(current_user.get('expire') or svc.get('expire'))
        now = int(time.time())
        base = cur_expire if cur_expire > now else now
        new_expire = base + days * 86400
        if new_expire < now and days < 0:
            # allow reduction but not past? still allow
            pass
        updated = await api_client.update_user(str(panel_username), {'expire': new_expire})
        sub_url = api_client.extract_subscription_url(updated) or svc.get('subscription_url')
        links = api_client.extract_config_links(updated)
        db.update_user_service_snapshot(svc_id, updated, sub_url, links)
        # Ensure expire in DB is updated (snapshot does)
        refreshed = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,)) or svc
        db.add_log(uid, 'admin_add_days', 'service', str(svc_id), {'panel_username': panel_username, 'days': days, 'new_expire': new_expire})
        await state.clear()
        text_out = build_admin_service_detail_text(refreshed, updated) + f'\n\n✅ {days} روز {"افزوده شد" if days>0 else "کسر شد"}.'
        kb = build_admin_service_keyboard(refreshed)
        await message.answer(text_out, reply_markup=kb, parse_mode='HTML')
    except Exception as exc:
        db.record_system_error('users.admin_add_days', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await message.answer(f'خطا: {friendly_error(exc)}')
        await state.clear()


@users_router.callback_query(F.data.startswith('users:svc_add_traffic:'))
async def admin_service_add_traffic_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc or not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    await state.set_state(AdminServiceStates.waiting_add_traffic)
    await state.update_data(admin_svc_id=svc_id)
    await edit_or_answer(callback, f'➕ افزودن حجم به سرویس {code(svc.get("service_username"))}\n\nمقدار به گیگابایت ارسال کنید. مثال: <code>10</code> برای ۱۰ گیگ، یا <code>-5</code> برای کسر ۵ گیگ، یا <code>0.5</code> برای نیم گیگ.\nبرای لغو /cancel بفرستید.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data=f'users:svc_view:{svc_id}')]]), parse_mode='HTML')
    await callback.answer()


@users_router.message(AdminServiceStates.waiting_add_traffic)
async def admin_service_add_traffic_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        await state.clear()
        return
    data = await state.get_data()
    svc_id = int(data.get('admin_svc_id') or 0)
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc:
        await state.clear()
        await message.answer('سرویس پیدا نشد.')
        return
    if not can_manage_services(uid, int(svc.get('telegram_id'))):
        await state.clear()
        await message.answer('دسترسی ندارید.')
        return
    text = (message.text or '').strip().replace(',', '.')
    if text.lower() in ('/cancel', 'cancel', '❌', 'انصراف'):
        await state.clear()
        await message.answer('لغو شد.', reply_markup=back_keyboard())
        return
    try:
        gb = float(text)
        if gb == 0 or abs(gb) > 100000:
            raise ValueError('gb out of range')
    except Exception:
        await message.answer('مقدار نامعتبر است. مثال: 10 یا -5 یا 0.5')
        return
    delta = int(gb * 1024**3)
    panel_username = svc.get('panel_username') or svc.get('service_username')
    try:
        current_user = await api_client.get_user(str(panel_username))
        cur_limit = int(current_user.get('data_limit') or svc.get('data_limit') or 0)
        # cur_limit 0 means unlimited; treat as delta
        new_limit = max(0, cur_limit + delta) if cur_limit != 0 else max(0, delta)
        # Some panels require 0 = unlimited, so 0 is valid
        updated = await api_client.update_user(str(panel_username), {'data_limit': new_limit})
        sub_url = api_client.extract_subscription_url(updated) or svc.get('subscription_url')
        links = api_client.extract_config_links(updated)
        db.update_user_service_snapshot(svc_id, updated, sub_url, links)
        refreshed = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,)) or svc
        db.add_log(uid, 'admin_add_traffic', 'service', str(svc_id), {'panel_username': panel_username, 'gb': gb, 'new_limit': new_limit})
        await state.clear()
        text_out = build_admin_service_detail_text(refreshed, updated) + f'\n\n✅ حجم {"افزوده شد" if gb>0 else "کسر شد"}: {gb} GB.'
        kb = build_admin_service_keyboard(refreshed)
        await message.answer(text_out, reply_markup=kb, parse_mode='HTML')
    except Exception as exc:
        db.record_system_error('users.admin_add_traffic', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await message.answer(f'خطا: {friendly_error(exc)}')
        await state.clear()


@users_router.callback_query(F.data.startswith('users:svc_delete_confirm:'))
async def admin_service_delete_confirm(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc or not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🗑 بله، حذف شود', callback_data=f'users:svc_delete_do:{svc_id}', style=get_button_style('danger'))],
        [InlineKeyboardButton(text='❌ انصراف', callback_data=f'users:svc_view:{svc_id}')],
    ])
    await edit_or_answer(callback, f'🗑 حذف سرویس {code(svc.get("service_username"))}\n\nآیا مطمئن هستید؟ این عمل پنل را هم حذف می‌کند و غیرقابل بازگشت است.', reply_markup=kb, parse_mode='HTML')
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:svc_delete_do:'))
async def admin_service_delete_do(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    try:
        svc_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه نامعتبر', show_alert=True)
        return
    svc = db.fetchone('SELECT * FROM user_services WHERE id=?', (svc_id,))
    if not svc or not can_manage_services(uid, int(svc.get('telegram_id'))):
        await deny_callback(callback)
        return
    panel_username = svc.get('panel_username') or svc.get('service_username')
    target_uid = int(svc.get('telegram_id'))
    try:
        try:
            await api_client.delete_user(str(panel_username))
        except Exception as e:
            # If already not found, treat as success for DB
            msg = str(e).lower()
            if 'not found' not in msg and '404' not in msg:
                raise
        db.deactivate_user_service(svc_id, target_uid, status='deleted_by_admin')
        db.add_log(uid, 'admin_delete_service', 'service', str(svc_id), {'panel_username': panel_username})
        await edit_or_answer(callback, f'✅ سرویس {code(panel_username)} حذف شد.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 برگشت به سرویس‌ها', callback_data=f'users:services:{target_uid}:0')]]), parse_mode='HTML')
        await callback.answer('حذف شد', show_alert=True)
    except Exception as exc:
        db.record_system_error('users.admin_delete_service', str(exc), user_id=uid, entity_type='service', entity_id=str(svc_id), error_type=type(exc).__name__)
        await callback.answer(friendly_error(exc), show_alert=True)


@users_router.callback_query(F.data == 'users:search')
async def users_search_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    await state.set_state(UserStates.search)
    await edit_or_answer(
        callback,
        '🔍 جستجوی کاربر\n\nآیدی عددی یا یوزرنیم کاربر را بفرستید.\nمثال:\n<code>123456789</code>\n<code>@username</code>',
        reply_markup=users_back_keyboard(),
        parse_mode='HTML',
    )
    await callback.answer()


@users_router.message(UserStates.search)
async def users_search_finish(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        return
    q = (message.text or '').strip().lstrip('@')
    if not q:
        await message.answer('عبارت جستجو خالی است.')
        return

    role = get_role(uid)
    params: tuple
    query: str

    if q.isdigit():
        query = 'SELECT * FROM bot_users WHERE telegram_id=?'
        params = (int(q),)
    else:
        query = 'SELECT * FROM bot_users WHERE username LIKE ? COLLATE NOCASE'
        params = (f'%{q}%',)

    if role == 'seller':
        query += ' AND owner_admin_id=?'
        params = (*params, uid)

    row = db.fetchone(query + ' LIMIT 1', params)
    await state.clear()
    if not row:
        await message.answer('کاربر پیدا نشد.', reply_markup=back_keyboard())
        return
    await message.answer(user_detail_text(row), reply_markup=user_action_keyboard(row['telegram_id']), parse_mode='HTML')


@users_router.callback_query(F.data.startswith('users:block_confirm:'))
async def block_confirm(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_admin(uid):
        await deny_callback(callback)
        return
    try:
        user_id = int(callback.data.split(':')[-1])
    except Exception:
        await callback.answer('شناسه کاربر نامعتبر است.', show_alert=True)
        return
    row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (user_id,))
    if not row:
        await callback.answer('کاربر پیدا نشد.', show_alert=True)
        return
    blocked = int(row.get('is_blocked') or 0) == 1
    if blocked:
        text = '✅ رفع مسدودی کاربر\n\nآیا مطمئن هستید این کاربر دوباره به ربات دسترسی داشته باشد؟'
        action = 'users:unblock_do'
        btn = '✅ بله، رفع مسدودی'
    else:
        text = '🚫 مسدود کردن کاربر\n\nبعد از تایید، این کاربر دیگر نمی‌تواند از ربات استفاده کند. آیا مطمئن هستید؟'
        action = 'users:block_do'
        btn = '🚫 بله، مسدود شود'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn, callback_data=f'{action}:{user_id}')],
        [InlineKeyboardButton(text='❌ انصراف', callback_data=f'users:view:{user_id}')],
    ])
    await edit_or_answer(callback, text, reply_markup=kb)
    await callback.answer()


@users_router.callback_query(F.data.startswith('users:block_do:'))
async def block_user_do(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_admin(uid):
        await deny_callback(callback)
        return
    user_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (user_id,))
    if not row:
        await callback.answer('کاربر پیدا نشد.', show_alert=True)
        return
    db.execute('UPDATE bot_users SET is_blocked=1, updated_at=? WHERE telegram_id=?', (db.now_iso(), user_id))
    db.add_log(uid, 'block_user', 'user', str(user_id), {'is_blocked': 1})
    refreshed = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (user_id,))
    await edit_or_answer(callback, user_detail_text(refreshed), reply_markup=user_action_keyboard(user_id), parse_mode='HTML')
    await callback.answer('کاربر مسدود شد.', show_alert=True)


@users_router.callback_query(F.data.startswith('users:unblock_do:'))
async def unblock_user_do(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    if not is_admin(uid):
        await deny_callback(callback)
        return
    user_id = int(callback.data.split(':')[-1])
    row = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (user_id,))
    if not row:
        await callback.answer('کاربر پیدا نشد.', show_alert=True)
        return
    db.execute('UPDATE bot_users SET is_blocked=0, updated_at=? WHERE telegram_id=?', (db.now_iso(), user_id))
    db.add_log(uid, 'unblock_user', 'user', str(user_id), {'is_blocked': 0})
    refreshed = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (user_id,))
    await edit_or_answer(callback, user_detail_text(refreshed), reply_markup=user_action_keyboard(user_id), parse_mode='HTML')
    await callback.answer('مسدودی کاربر برداشته شد.', show_alert=True)


@users_router.callback_query(F.data.startswith('users:pm_to:'))
async def pm_to_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    user_id = int(callback.data.split(':')[-1])
    if not can_message_customer(uid, user_id):
        await deny_callback(callback)
        return
    await state.set_state(UserStates.private_message)
    await state.update_data(target_user_id=user_id)
    await edit_or_answer(
        callback,
        f'📩 پیام اختصاصی\n\nپیام موردنظر برای {plain(person_display(user_id, include_username=True))} را ارسال کنید.',
        reply_markup=users_back_keyboard(),
        parse_mode='HTML',
    )
    await callback.answer()


@users_router.callback_query(F.data == 'users:pm')
async def pm_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id if callback.from_user else None
    if not is_staff(uid):
        await deny_callback(callback)
        return
    await state.set_state(UserStates.private_message)
    await state.update_data(target_user_id=None)
    await edit_or_answer(
        callback,
        '📩 پیام به کاربر\n\nبه این فرمت ارسال کنید:\n<code>123456789 متن پیام</code>',
        reply_markup=users_back_keyboard(),
        parse_mode='HTML',
    )
    await callback.answer()


@users_router.message(UserStates.private_message)
async def pm_finish(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id if message.from_user else None
    data = await state.get_data()
    target = data.get('target_user_id')
    text = message_text_with_custom_emoji_tokens(message)
    if text == BTN_CANCEL:
        await state.clear()
        await message.answer('لغو شد.', reply_markup=back_keyboard())
        return
    if target is None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer('فرمت اشتباه است. مثال: <code>123456789 سلام</code>', parse_mode='HTML')
            return
        target = int(parts[0])
        text = parts[1]
    if not can_message_customer(uid, int(target)):
        await message.answer('دسترسی به این مشتری ندارید.')
        await state.clear()
        return
    try:
        await bot.send_message(int(target), text)
        db.add_log(uid, 'private_message_user', 'user', str(target))
        await message.answer('پیام ارسال شد.')
    except Exception as e:
        await message.answer(f'ارسال ناموفق بود: {plain(e)}')
    await state.clear()
