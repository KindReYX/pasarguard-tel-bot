from __future__ import annotations

import html
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

import db
import keyboards as kb
from config import settings
from permissions import ROLE_LABELS, deny_callback, deny_message, get_role, is_admin
from utils import (
    BUTTON_STYLE_VALUES,
    answer_template,
    button_detail_id,
    get_button_detail_override,
    get_content_stickers,
    message_text_with_custom_emoji_tokens,
    resolved_button_style,
    set_content_stickers,
    split_button_custom_emoji,
    user_template_context,
)
from modules import edit_runtime

edit_router = Router()

STYLE_LABELS = {
    'default': '⚪ پیش‌فرض',
    'primary': '🔵 آبی',
    'success': '🟢 سبز',
    'danger': '🔴 قرمز',
}

ADMIN_REPLY_BUTTONS = [
    kb.BTN_DASHBOARD,
    kb.BTN_USERS,
    kb.BTN_ORDERS,
    kb.BTN_REPORTS,
    kb.BTN_PACKAGES,
    kb.BTN_COUPONS,
    kb.BTN_WALLET,
    kb.BTN_STARS,
    kb.BTN_BROADCAST,
    kb.BTN_TICKETS,
    kb.BTN_SETTINGS,
    kb.BTN_EDIT_MODE,
    kb.BTN_MAINTENANCE,
    kb.BTN_EXPORTS,
    kb.BTN_NOTIFICATIONS,
    kb.BTN_FORCE_JOIN,
    kb.BTN_ADVANCED_SETTINGS,
    kb.BTN_REFERRAL,
    kb.BTN_SYSTEM_HEALTH,
    kb.BTN_SYSTEM_ERRORS,
    kb.BTN_PAYMENT_ENGINE,
    kb.BTN_CARD_RECEIPTS,
    kb.BTN_CAMPAIGNS,
    kb.BTN_TUTORIALS_ADMIN,
    kb.BTN_AD_TRACKING,
    kb.BTN_SELLER_PANEL,
    kb.BTN_ADMINS,
    kb.BTN_LOGS,
    kb.BTN_BACKUP,
    kb.BTN_BACK,
]


def scope_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='👤 کاربر', callback_data='editmode:scope:user', style='primary'),
        InlineKeyboardButton(text='🛠 ادمین', callback_data='editmode:scope:admin', style='success'),
    ]])


def _button_raw_from_rendered(button: Any, fallback: str = '') -> str:
    text = str(getattr(button, 'text', None) or fallback or '')
    emoji_id = getattr(button, 'icon_custom_emoji_id', None)
    if emoji_id:
        return f'[{emoji_id}] {text}'.strip()
    return text


def _effective_style(target: edit_runtime.ButtonTarget) -> str:
    generic = edit_runtime.get_button_style_override(target.stable_key)
    if generic:
        return generic
    if target.detail_id:
        has, style = get_button_detail_override(target.detail_id)
        if has:
            return style or 'default'
    raw = str(target.current_style or '').strip().lower()
    if raw in STYLE_LABELS:
        return raw
    inferred = resolved_button_style(target.raw_text, target.callback_data, target.url)
    return str(inferred or 'default') if str(inferred or 'default') in STYLE_LABELS else 'default'


def button_editor_markup(target: edit_runtime.ButtonTarget) -> InlineKeyboardMarkup:
    current_style = _effective_style(target)
    rows = [[InlineKeyboardButton(
        text='✏️ تغییر متن دکمه',
        callback_data=f'editmode:button_text:{target.token}',
        style='primary',
    )]]
    style_row = []
    for value in ('default', 'primary', 'success', 'danger'):
        label = STYLE_LABELS[value]
        if value == current_style:
            label += ' ✅'
        style_row.append(InlineKeyboardButton(
            text=label,
            callback_data=f'editmode:button_style:{target.token}:{value}',
            style=BUTTON_STYLE_VALUES.get(value),
        ))
        if len(style_row) == 2:
            rows.append(style_row)
            style_row = []
    if style_row:
        rows.append(style_row)
    # These two controls are intentionally available ONLY for the first customer menu.
    if target.customer_button_id:
        enabled = kb.customer_button_enabled(target.customer_button_id)
        rows.append([InlineKeyboardButton(
            text=('🚫 غیرفعال کردن دکمه' if enabled else '✅ فعال کردن دکمه'),
            callback_data=f'editmode:customer_toggle:{target.token}',
            style=('danger' if enabled else 'success'),
        )])
        rows.append([InlineKeyboardButton(
            text='↔️ جابجایی جایگاه در منوی اول',
            callback_data=f'editmode:customer_move:{target.token}',
            style='primary',
        )])
    if target.kind == 'url' and target.url:
        rows.append([InlineKeyboardButton(
            text='🌐 انجام عملکرد / باز کردن لینک',
            url=target.url,
            style='success',
        )])
    else:
        rows.append([InlineKeyboardButton(
            text='▶️ انجام عملکرد دکمه',
            callback_data=f'editmode:run:{target.token}',
            style='success',
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_button_editor(message: Message, target: edit_runtime.ButtonTarget) -> None:
    visible, emoji_id = split_button_custom_emoji(target.raw_text)
    emoji_note = f'\nPremium Emoji ID: {emoji_id}' if emoji_id else ''
    menu_note = ''
    if target.customer_button_id:
        order = kb.customer_menu_order()
        position = order.index(target.customer_button_id) + 1 if target.customer_button_id in order else '-'
        enabled = kb.customer_button_enabled(target.customer_button_id)
        menu_note = f'\nوضعیت منوی اول: {"✅ فعال" if enabled else "⛔ غیرفعال"} | جایگاه: {position}'
    await message.answer(
        '🛠 ویرایش دکمه انتخاب‌شده\n\n'
        f'متن فعلی: {html.escape(visible)}{emoji_note}{menu_note}\n'
        'تغییر متن فقط ظاهر را عوض می‌کند؛ callback/عملکرد اصلی دست‌نخورده می‌ماند.\n\n'
        'برای دیدن عملکرد واقعی، «▶️ انجام عملکرد دکمه» را بزن.',
        reply_markup=button_editor_markup(target),
        parse_mode='HTML',
    )


def _customer_reply_target(message: Message) -> edit_runtime.ButtonTarget | None:
    incoming = str(message.text or '').strip()
    for button_id, (setting_key, default_text) in kb.CUSTOMER_BUTTON_SETTINGS.items():
        current_raw = kb.customer_button_text(button_id)
        current_visible, _ = split_button_custom_emoji(current_raw)
        default_visible, _ = split_button_custom_emoji(default_text)
        accepted = {current_visible, default_visible}
        aliases = db.get_setting(f'button_text_aliases:{setting_key}', [])
        if isinstance(aliases, list):
            for item in aliases:
                alias, _ = split_button_custom_emoji(str(item))
                if alias:
                    accepted.add(alias)
        if incoming not in accepted:
            continue
        detail = kb.CUSTOMER_BUTTON_DETAIL_IDS.get(button_id)
        stable = edit_runtime.button_stable_key(default_text, None, None, detail)
        return edit_runtime.register_button_target(
            kind='message',
            stable_key=stable,
            display_text=current_visible,
            raw_text=current_raw,
            detail_id=detail,
            setting_key=setting_key,
            customer_button_id=button_id,
            event=message,
            current_style=None,
        )
    return None


def _admin_reply_target(message: Message) -> edit_runtime.ButtonTarget | None:
    incoming = str(message.text or '').strip()
    for original in ADMIN_REPLY_BUTTONS:
        detail = button_detail_id(original, None, None)
        stable = edit_runtime.button_stable_key(original, None, None, detail)
        override = edit_runtime.get_button_text_override(stable)
        raw = str(override if override is not None else original)
        visible, _ = split_button_custom_emoji(raw)
        original_visible, _ = split_button_custom_emoji(original)
        accepted = {visible, original_visible}
        aliases = db.get_setting(f'edit_button_aliases:{stable}', [])
        if isinstance(aliases, list):
            for item in aliases:
                alias, _ = split_button_custom_emoji(str(item))
                if alias:
                    accepted.add(alias)
        if incoming not in accepted:
            continue
        return edit_runtime.register_button_target(
            kind='message',
            stable_key=stable,
            display_text=visible,
            raw_text=raw,
            detail_id=detail,
            event=message,
            current_style=None,
        )
    return None


def resolve_reply_target(message: Message, scope: str) -> edit_runtime.ButtonTarget | None:
    if scope == 'user':
        return _customer_reply_target(message)
    return _admin_reply_target(message)


def _find_inline_button(callback: CallbackQuery):
    markup = getattr(callback.message, 'reply_markup', None)
    if not markup:
        return None
    for row in getattr(markup, 'inline_keyboard', []) or []:
        for button in row:
            if str(getattr(button, 'callback_data', '') or '') == str(callback.data or ''):
                return button
    return None


def inline_target(callback: CallbackQuery) -> edit_runtime.ButtonTarget:
    button = _find_inline_button(callback)
    raw = _button_raw_from_rendered(button, fallback=str(callback.data or 'دکمه'))
    display, _ = split_button_custom_emoji(raw)
    detail = button_detail_id(raw, callback.data, getattr(button, 'url', None) if button else None)
    stable = edit_runtime.button_stable_key(raw, callback.data, getattr(button, 'url', None) if button else None, detail)
    return edit_runtime.register_button_target(
        kind='callback',
        stable_key=stable,
        display_text=display,
        raw_text=raw,
        detail_id=detail,
        callback_data=str(callback.data or ''),
        url=getattr(button, 'url', None) if button else None,
        event=callback,
        original_message=callback.message,
        current_style=getattr(button, 'style', None) if button else None,
    )


class EditModeInputFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else None
        return bool(uid and edit_runtime.is_active(uid) and edit_runtime.INPUT_WAITERS.get(int(uid)))


class EditModeReplyButtonFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else None
        session = edit_runtime.get_session(uid)
        if not session or not message.text:
            return False
        if str(message.text).startswith('/'):
            return False
        if edit_runtime.consume_message_bypass(int(uid), int(message.message_id)):
            return False
        return resolve_reply_target(message, session.scope) is not None


class EditModeCallbackFilter(Filter):
    async def __call__(self, callback: CallbackQuery) -> bool:
        uid = callback.from_user.id if callback.from_user else None
        if not edit_runtime.is_active(uid):
            return False
        if str(callback.data or '').startswith('editmode:'):
            return False
        if edit_runtime.consume_callback_bypass(callback.id):
            return False
        return True


async def _render_scope_home(message: Message, session: edit_runtime.EditSession) -> None:
    with edit_runtime.editor_render(session):
        if session.scope == 'user':
            support = db.get_setting('support_username', settings.support_username)
            await answer_template(
                message,
                db.get_setting('welcome_text', 'سلام {name}! به ربات خوش آمدید.'),
                user_template_context(message.from_user, support=support),
                reply_markup=kb.customer_keyboard(),
                content_key='welcome_text',
            )
        else:
            role = get_role(message.from_user.id if message.from_user else None)
            await message.answer(
                f'پنل مدیریت\nنقش شما: {ROLE_LABELS.get(role, role)}',
                reply_markup=kb.main_admin_keyboard(role),
            )


@edit_router.message(F.text == '/exit')
async def edit_exit(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not edit_runtime.is_active(uid):
        return
    edit_runtime.stop_session(int(uid))
    try:
        await state.clear()
    except Exception:
        pass
    role = get_role(uid)
    await message.answer('حالت ویرایش بسته شد ✅', reply_markup=kb.main_admin_keyboard(role))


@edit_router.message(kb.EditableButtonFilter(kb.BTN_EDIT_MODE))
async def edit_start(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        await deny_message(message)
        return
    # If already inside admin preview, this is itself an editable menu button.
    session = edit_runtime.get_session(uid)
    if session and session.scope == 'admin':
        target = _admin_reply_target(message)
        if target:
            await show_button_editor(message, target)
            return
    await message.answer(
        'کجا رو میخوای ویرایش کنی؟\n\nدر حالت ویرایش، ربات همان مسیر واقعی را اجرا می‌کند. برای خروج از هر مرحله فقط /exit بفرست.',
        reply_markup=scope_menu(),
    )


@edit_router.callback_query(F.data.startswith('editmode:scope:'))
async def edit_scope(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    scope = str(callback.data).split(':')[-1]
    if scope not in {'user', 'admin'}:
        await callback.answer('بخش نامعتبر است.', show_alert=True)
        return
    try:
        await state.clear()
    except Exception:
        pass
    session = edit_runtime.start_session(callback.from_user.id, scope)
    await callback.message.answer(
        f'✅ حالت ویرایش {"کاربر" if scope == "user" else "ادمین"} فعال شد.\n'
        'هر دکمه را بزنی اول تنظیم همان دکمه باز می‌شود.\n'
        'برای خروج در هر لحظه: /exit'
    )
    await _render_scope_home(callback.message, session)
    await callback.answer()


@edit_router.message(EditModeInputFilter())
async def edit_input(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not is_admin(uid):
        return
    if message.text == '/exit':
        return
    waiter = edit_runtime.INPUT_WAITERS.get(uid)
    if not waiter:
        return
    kind = waiter.get('kind')

    if kind == 'customer_menu_move':
        target = edit_runtime.BUTTON_TARGETS.get(str(waiter.get('token') or ''))
        if not target or not target.customer_button_id:
            edit_runtime.INPUT_WAITERS.pop(uid, None)
            await message.answer('این جابجایی منقضی شده؛ دوباره دکمه منوی اول را انتخاب کن.')
            return
        if message.text is None or not str(message.text).strip().isdigit():
            await message.answer('فقط شماره مقصد از 1 تا 8 را بفرست؛ برای خروج کامل /exit.')
            return
        position = int(str(message.text).strip())
        if position < 1 or position > len(kb.CUSTOMER_MENU_DEFAULT_ORDER):
            await message.answer(f'شماره باید بین 1 تا {len(kb.CUSTOMER_MENU_DEFAULT_ORDER)} باشد.')
            return
        kb.swap_customer_menu_position(target.customer_button_id, position)
        edit_runtime.INPUT_WAITERS.pop(uid, None)
        db.add_log(uid, 'customer_menu_reorder', 'button', target.customer_button_id, {'destination': position})
        await message.answer(f'جای دکمه با شماره {position} عوض شد ✅')
        session = edit_runtime.get_session(uid)
        if session and session.scope == 'user':
            await _render_scope_home(message, session)
        return

    if kind == 'button_text':
        target = edit_runtime.BUTTON_TARGETS.get(str(waiter.get('token') or ''))
        if not target:
            edit_runtime.INPUT_WAITERS.pop(uid, None)
            await message.answer('این ویرایش منقضی شده؛ دوباره دکمه را انتخاب کن.')
            return
        if message.text is None:
            await message.answer('متن جدید دکمه را بفرست. ایموجی پریمیوم را می‌توانی مستقیم داخل همان پیام بگذاری.')
            return
        new_value = message_text_with_custom_emoji_tokens(message).strip()
        if not new_value:
            await message.answer('متن دکمه نمی‌تواند خالی باشد.')
            return
        old_raw = target.raw_text
        if target.setting_key:
            aliases = db.get_setting(f'button_text_aliases:{target.setting_key}', [])
            aliases = list(aliases) if isinstance(aliases, list) else []
            if old_raw and old_raw not in aliases:
                aliases.append(old_raw)
            db.set_setting(f'button_text_aliases:{target.setting_key}', aliases[-12:])
            db.set_setting(target.setting_key, new_value)
        else:
            aliases = db.get_setting(f'edit_button_aliases:{target.stable_key}', [])
            aliases = list(aliases) if isinstance(aliases, list) else []
            if old_raw and old_raw not in aliases:
                aliases.append(old_raw)
            db.set_setting(f'edit_button_aliases:{target.stable_key}', aliases[-12:])
            edit_runtime.set_button_text_override(target.stable_key, new_value)
        target.raw_text = new_value
        target.display_text = split_button_custom_emoji(new_value)[0]
        edit_runtime.INPUT_WAITERS.pop(uid, None)
        db.add_log(uid, 'live_edit_button_text', 'button', target.stable_key, {'value': new_value})
        await message.answer('متن دکمه ذخیره و روی مسیر واقعی اعمال شد ✅')
        await show_button_editor(message, target)
        return

    if kind in {'text_value', 'text_stickers'}:
        target = edit_runtime.TEXT_TARGETS.get(str(waiter.get('token') or ''))
        if not target:
            edit_runtime.INPUT_WAITERS.pop(uid, None)
            await message.answer('این ویرایش منقضی شده؛ دوباره «تغییر متن این بخش» را بزن.')
            return
        if kind == 'text_value':
            if message.text == '/done':
                waiter['pending_text'] = waiter.get('current_text', target.template)
            elif message.text is None:
                await message.answer('متن کامل جدید را بفرست؛ یا /done برای نگه داشتن متن فعلی.')
                return
            else:
                waiter['pending_text'] = message_text_with_custom_emoji_tokens(message)

            # QR/photo captions intentionally stay a single media message. Their
            # text is editable but extra sticker messages are disabled for that target.
            if not target.allow_stickers:
                pending_text = str(waiter.get('pending_text', waiter.get('current_text', target.template)))
                if target.setting_key:
                    db.set_setting(target.setting_key, pending_text)
                else:
                    edit_runtime.set_text_override(target.stable_key, pending_text)
                edit_runtime.INPUT_WAITERS.pop(uid, None)
                db.add_log(uid, 'live_edit_text', 'text', target.setting_key or target.stable_key, {'stickers_changed': False})
                await message.answer('متن این بخش ذخیره و روی خروجی واقعی ربات اعمال شد ✅')
                return

            waiter['kind'] = 'text_stickers'
            if message.text == '/done':
                await message.answer('متن بدون تغییر می‌ماند. اگر استیکر جدید می‌خواهی بفرست؛ در غیر این صورت /done بزن.')
            else:
                await message.answer('متن دریافت شد ✅\nاگر استیکر این بخش را هم می‌خواهی عوض کنی، استیکرها را بفرست. اگر نه /done بزن.')
            return

        if message.sticker:
            if not waiter.get('stickers_replaced'):
                waiter['pending_stickers'] = []
                waiter['stickers_replaced'] = True
            waiter.setdefault('pending_stickers', []).append(message.sticker.file_id)
            await message.answer(f'استیکر #{len(waiter["pending_stickers"])} ثبت شد. استیکر بعدی یا /done')
            return
        if message.text != '/done':
            await message.answer('در این مرحله استیکر بفرست یا /done. برای خروج کامل /exit.')
            return
        pending_text = str(waiter.get('pending_text', waiter.get('current_text', target.template)))
        if target.setting_key:
            db.set_setting(target.setting_key, pending_text)
        else:
            edit_runtime.set_text_override(target.stable_key, pending_text)
        if waiter.get('stickers_replaced'):
            stickers = list(waiter.get('pending_stickers') or [])
            if target.content_key:
                set_content_stickers(target.content_key, stickers)
            else:
                edit_runtime.set_generic_stickers(target.stable_key, stickers)
        edit_runtime.INPUT_WAITERS.pop(uid, None)
        db.add_log(uid, 'live_edit_text', 'text', target.setting_key or target.stable_key, {
            'stickers_changed': bool(waiter.get('stickers_replaced')),
        })
        await message.answer('متن این بخش ذخیره و روی خروجی واقعی ربات اعمال شد ✅')
        return


@edit_router.callback_query(F.data.startswith('editmode:reply:'))
async def edit_reply_proxy_button(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id):
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target:
        await callback.answer('این دکمه منقضی شده؛ صفحه را دوباره باز کن.', show_alert=True)
        return
    await show_button_editor(callback.message, target)
    await callback.answer()


@edit_router.callback_query(F.data.startswith('editmode:url:'))
async def edit_url_button(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id):
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target:
        await callback.answer('این دکمه منقضی شده؛ صفحه را دوباره باز کن.', show_alert=True)
        return
    await show_button_editor(callback.message, target)
    await callback.answer()


@edit_router.callback_query(F.data.startswith('editmode:button_text:'))
async def edit_button_text_start(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id):
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target:
        await callback.answer('این دکمه منقضی شده؛ دوباره آن را انتخاب کن.', show_alert=True)
        return
    edit_runtime.INPUT_WAITERS[callback.from_user.id] = {'kind': 'button_text', 'token': token}
    await callback.message.answer(
        'متن کامل جدید دکمه را بفرست.\n\n'
        f'مقدار فعلی:\n{target.raw_text}\n\n'
        'ایموجی پریمیوم: هم خود ایموجی را مستقیم می‌توانی بفرستی، هم روش [CUSTOM_EMOJI_ID] متن همچنان فعال است.\n'
        'برای خروج کامل از حالت ویرایش: /exit'
    )
    await callback.answer()


@edit_router.callback_query(F.data.startswith('editmode:button_style:'))
async def edit_button_style(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id):
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    parts = str(callback.data).split(':')
    if len(parts) < 4:
        await callback.answer('داده نامعتبر است.', show_alert=True)
        return
    token, value = parts[-2], parts[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target or value not in STYLE_LABELS:
        await callback.answer('این دکمه منقضی شده یا رنگ نامعتبر است.', show_alert=True)
        return
    edit_runtime.set_button_style_override(target.stable_key, value)
    db.add_log(callback.from_user.id, 'live_edit_button_style', 'button', target.stable_key, {'style': value})
    try:
        await callback.message.edit_reply_markup(reply_markup=button_editor_markup(target))
    except Exception:
        pass
    await callback.answer(f'{STYLE_LABELS[value]} اعمال شد.')


def customer_menu_matrix_markup(target: edit_runtime.ButtonTarget) -> InlineKeyboardMarkup:
    order = kb.customer_menu_order()
    rows = []
    for index in range(0, len(order), 2):
        row = []
        for pos in range(index, min(index + 2, len(order))):
            button_id = order[pos]
            label = str(pos + 1)
            if button_id == target.customer_button_id:
                label += ' ✅'
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f'editmode:customer_swap:{target.token}:{pos + 1}',
                style=('success' if button_id == target.customer_button_id else 'default'),
            ))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def customer_menu_matrix_text(target: edit_runtime.ButtonTarget) -> str:
    order = kb.customer_menu_order()
    lines = ['↔️ ماتریس جایگاه منوی اول کاربر', '']
    for index in range(0, len(order), 2):
        cells = []
        for pos in range(index, min(index + 2, len(order))):
            button_id = order[pos]
            raw = kb.customer_button_text(button_id)
            visible, _ = split_button_custom_emoji(raw)
            state = '' if kb.customer_button_enabled(button_id) else ' ⛔'
            cells.append(f'{pos + 1}) {visible}{state}')
        lines.append('    |    '.join(cells))
    current = order.index(target.customer_button_id) + 1 if target.customer_button_id in order else '-'
    lines.extend(['', f'جایگاه فعلی دکمه انتخاب‌شده: {current}', 'شماره مقصد را بزن یا همان عدد را به‌صورت پیام بفرست. جای دو دکمه با هم عوض می‌شود.'])
    return '\n'.join(lines)


@edit_router.callback_query(F.data.startswith('editmode:customer_toggle:'))
async def edit_customer_menu_toggle(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id, 'user'):
        await callback.answer('این قابلیت فقط در حالت ویرایش کاربر فعال است.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target or not target.customer_button_id:
        await callback.answer('این گزینه فقط برای دکمه‌های منوی اول کاربر است.', show_alert=True)
        return
    enabled = kb.customer_button_enabled(target.customer_button_id)
    kb.set_customer_button_enabled(target.customer_button_id, not enabled)
    db.add_log(callback.from_user.id, 'customer_menu_toggle', 'button', target.customer_button_id, {'enabled': not enabled})
    try:
        await callback.message.edit_reply_markup(reply_markup=button_editor_markup(target))
    except Exception:
        pass
    await callback.answer('دکمه فعال شد ✅' if not enabled else 'دکمه برای کاربران غیرفعال شد ⛔')
    session = edit_runtime.get_session(callback.from_user.id)
    if session:
        await _render_scope_home(callback.message, session)


@edit_router.callback_query(F.data.startswith('editmode:customer_move:'))
async def edit_customer_menu_move(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id, 'user'):
        await callback.answer('این قابلیت فقط در حالت ویرایش کاربر فعال است.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target or not target.customer_button_id:
        await callback.answer('این گزینه فقط برای دکمه‌های منوی اول کاربر است.', show_alert=True)
        return
    edit_runtime.INPUT_WAITERS[callback.from_user.id] = {'kind': 'customer_menu_move', 'token': token}
    await callback.message.answer(
        customer_menu_matrix_text(target),
        reply_markup=customer_menu_matrix_markup(target),
    )
    await callback.answer()


@edit_router.callback_query(F.data.startswith('editmode:customer_swap:'))
async def edit_customer_menu_swap(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id, 'user'):
        await callback.answer('این قابلیت فقط در حالت ویرایش کاربر فعال است.', show_alert=True)
        return
    parts = str(callback.data).split(':')
    if len(parts) < 4 or not parts[-1].isdigit():
        await callback.answer('مقصد نامعتبر است.', show_alert=True)
        return
    token, position = parts[-2], int(parts[-1])
    target = edit_runtime.BUTTON_TARGETS.get(token)
    if not target or not target.customer_button_id:
        await callback.answer('این جابجایی منقضی شده.', show_alert=True)
        return
    try:
        kb.swap_customer_menu_position(target.customer_button_id, position)
    except ValueError:
        await callback.answer('مقصد نامعتبر است.', show_alert=True)
        return
    edit_runtime.INPUT_WAITERS.pop(callback.from_user.id, None)
    db.add_log(callback.from_user.id, 'customer_menu_reorder', 'button', target.customer_button_id, {'destination': position})
    await callback.answer(f'جای دکمه با شماره {position} عوض شد ✅')
    session = edit_runtime.get_session(callback.from_user.id)
    if session:
        await _render_scope_home(callback.message, session)


@edit_router.callback_query(F.data.startswith('editmode:text:'))
async def edit_text_start(callback: CallbackQuery):
    if not edit_runtime.is_active(callback.from_user.id):
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.TEXT_TARGETS.get(token)
    if not target:
        await callback.answer('این متن منقضی شده؛ صفحه را دوباره باز کن.', show_alert=True)
        return
    if target.setting_key:
        current = str(db.get_setting(target.setting_key, target.template) or '')
    else:
        current = str(edit_runtime.get_text_override(target.stable_key) or target.template)
    variables = ' '.join('{' + str(key) + '}' for key in target.variables.keys()) or 'ندارد / متن ثابت'
    if target.content_key:
        stickers = get_content_stickers(target.content_key)
    else:
        stickers = edit_runtime.get_generic_stickers(target.stable_key)
    edit_runtime.INPUT_WAITERS[callback.from_user.id] = {
        'kind': 'text_value',
        'token': token,
        'current_text': current,
        'pending_text': current,
        'pending_stickers': list(stickers),
        'stickers_replaced': False,
    }
    sticker_line = (f'استیکرهای فعلی: {len(stickers)}\n' if target.allow_stickers else 'این بخش کپشن رسانه است؛ برای حفظ ارسال یک‌پیامه، استیکر جدا ندارد.\n')
    await callback.message.answer(
        f'✏️ {target.label}\n\n'
        f'متغیرهای قابل استفاده:\n{variables}\n\n'
        + sticker_line
        + 'پیام بعدی فقط متن کامل فعلی است؛ آن را Copy کن، فقط بخش دلخواه را عوض کن و کامل برگردان.\n'
        + 'اگر متن را نمی‌خواهی عوض کنی /done بزن. برای خروج کامل /exit.'
    )
    await callback.message.answer(current or ' ')
    await callback.answer()


@edit_router.callback_query(F.data.startswith('editmode:run:'))
async def edit_run_real_action(callback: CallbackQuery):
    uid = callback.from_user.id
    session = edit_runtime.get_session(uid)
    if not session:
        await callback.answer('حالت ویرایش فعال نیست.', show_alert=True)
        return
    token = str(callback.data).split(':')[-1]
    target = edit_runtime.BUTTON_TARGETS.get(token)
    dispatcher = edit_runtime.get_dispatcher()
    if not target or dispatcher is None:
        await callback.answer('عملکرد منقضی شده؛ دوباره دکمه را انتخاب کن.', show_alert=True)
        return

    try:
        with edit_runtime.editor_render(session):
            if target.kind == 'message' and target.event is not None:
                original: Message = target.event
                edit_runtime.BYPASS_MESSAGES.add((uid, int(original.message_id)))
                update = Update(update_id=int(time.time() * 1000) % 2147483647, message=original)
                await dispatcher.feed_update(callback.bot, update, edit_mode_replay=True)
            elif target.kind == 'message_proxy':
                # Reply-keyboard buttons are rendered as inline proxies only in edit preview.
                # Recreate the real incoming user message and feed it to the SAME dispatcher,
                # preserving all original business handlers/FSM transitions.
                base: Message = target.event if isinstance(target.event, Message) else callback.message
                synthetic_id = (int(time.time() * 1000000) % 1900000000) + 1000
                synthetic = base.model_copy(update={
                    'message_id': synthetic_id,
                    'from_user': callback.from_user,
                    'chat': callback.message.chat,
                    'date': callback.message.date,
                    'text': target.display_text,
                    'entities': None,
                    'caption': None,
                    'reply_markup': None,
                })
                edit_runtime.BYPASS_MESSAGES.add((uid, synthetic_id))
                update = Update(update_id=int(time.time() * 1000) % 2147483647, message=synthetic)
                await dispatcher.feed_update(callback.bot, update, edit_mode_replay=True)
            elif target.kind == 'callback' and target.original_message is not None:
                synthetic = callback.model_copy(update={
                    'data': target.callback_data,
                    'message': target.original_message,
                })
                edit_runtime.BYPASS_CALLBACKS.add(synthetic.id)
                update = Update(update_id=int(time.time() * 1000) % 2147483647, callback_query=synthetic)
                await dispatcher.feed_update(callback.bot, update, edit_mode_replay=True)
            else:
                await callback.answer('این دکمه عملکرد قابل اجرا ندارد.', show_alert=True)
                return
        try:
            await callback.answer('عملکرد اصلی اجرا شد ✅')
        except Exception:
            pass
    except Exception as exc:
        try:
            db.record_system_error(
                'edit_mode.run_real_action',
                str(exc),
                user_id=uid,
                entity_type='edit_button',
                entity_id=target.stable_key,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        await callback.message.answer(f'اجرای عملکرد اصلی خطا داد: {html.escape(str(exc)[:500])}', parse_mode='HTML')
        try:
            await callback.answer('خطا در اجرای عملکرد', show_alert=True)
        except Exception:
            pass


@edit_router.message(EditModeReplyButtonFilter())
async def edit_intercept_reply(message: Message):
    uid = message.from_user.id if message.from_user else None
    session = edit_runtime.get_session(uid)
    if not session:
        return
    target = resolve_reply_target(message, session.scope)
    if target:
        await show_button_editor(message, target)


@edit_router.callback_query(EditModeCallbackFilter())
async def edit_intercept_callback(callback: CallbackQuery):
    target = inline_target(callback)
    await show_button_editor(callback.message, target)
    try:
        await callback.answer()
    except Exception:
        pass
