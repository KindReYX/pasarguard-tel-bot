from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, Message

import db
from utils import get_button_style, infer_button_kind, split_button_custom_emoji, get_button_detail_override, button_detail_id, BUTTON_STYLE_VALUES

BTN_DASHBOARD = '📊 داشبورد'
BTN_USERS = '👥 کاربران'
BTN_ORDERS = '🧾 سفارش‌ها'
BTN_REPORTS = '📈 گزارش‌ها'
BTN_PACKAGES = '📦 بسته‌ها'
BTN_COUPONS = '🏷 کد تخفیف'
BTN_WALLET = '💰 کیف پول'
BTN_STARS = '⭐ پرداخت‌های Stars'
BTN_LOGS = '🧾 لاگ ادمین‌ها'
BTN_TEXTS = '📝 متن‌های ربات'
BTN_EDIT_MODE = '🛠 حالت ویرایش'
BTN_BROADCAST = '📣 پیام همگانی'
BTN_TICKETS = '🎫 تیکت‌ها'
BTN_BACKUP = '💾 بکاپ'
BTN_EXPORTS = '📤 خروجی اکسل'
BTN_SETTINGS = '⚙️ تنظیمات'
BTN_MAINTENANCE = '🚧 حالت تعمیر'
BTN_ADMINS = '🛡 مدیریت ادمین‌ها'
BTN_NOTIFICATIONS = '🔔 اعلان‌ها'
BTN_FORCE_JOIN = '🔐 جوین اجباری'
BTN_ADVANCED_SETTINGS = '🧩 تنظیمات پیشرفته'
BTN_REFERRAL = '👥 دعوت دوستان'
BTN_TEST_SERVICE = '🧪 سرویس تست'
BTN_SYSTEM_HEALTH = '🩺 وضعیت سیستم'
BTN_SYSTEM_ERRORS = '🚨 خطاهای سیستم'
BTN_PAYMENT_ENGINE = '🧾 موتور پرداخت'
BTN_CARD_RECEIPTS = '💳 فیش های کارت به کارت'
BTN_CAMPAIGNS = '🎯 کمپین ها'
BTN_TUTORIALS_ADMIN = '🎓 مدیریت آموزش‌ها'
BTN_AD_TRACKING = '📣 نظارت بر تبلیغات'
BTN_SELLER_PANEL = '🤝 پنل فروشنده'
BTN_BACK = '🔙 بازگشت'
BTN_BUY_SUBSCRIPTION = '🛒 خرید اشتراک'
BTN_MY_SERVICES = '👤 سرویس های من'
BTN_CUSTOMER_WALLET = '💳 کیف پول من'
BTN_SUPPORT = '🎫 پشتیبانی'
BTN_RULES = '📜 قوانین'
BTN_TUTORIALS = '🎓 آموزش'
BTN_CANCEL = '❌ انصراف'
BTN_SKIP = '⏭ رد کردن'
BTN_CONFIRM = '✅ تایید'


CUSTOMER_BUTTON_SETTINGS = {
    # Every customer reply-menu button is configurable. The stable button id is
    # deliberately independent from its visible label, so renaming a button does
    # not break routing or its per-button Telegram color.
    'buy': ('buy_button_text', BTN_BUY_SUBSCRIPTION),
    'test_service': ('test_service_button_text', BTN_TEST_SERVICE),
    'services': ('services_button_text', BTN_MY_SERVICES),
    'wallet': ('wallet_button_text', BTN_CUSTOMER_WALLET),
    'referral': ('referral_button_text', BTN_REFERRAL),
    'tutorials': ('tutorials_button_text', BTN_TUTORIALS),
    'support': ('support_button_text', BTN_SUPPORT),
    'rules': ('rules_button_text', BTN_RULES),
}

CUSTOMER_BUTTON_DETAIL_IDS = {
    'buy': 'buy',
    'test_service': 'test_service',
    'services': 'my_services',
    'wallet': 'customer_wallet',
    'referral': 'referral',
    'tutorials': 'tutorials',
    'support': 'support',
    'rules': 'rules',
}


CUSTOMER_MENU_DEFAULT_ORDER = [
    'buy', 'test_service',
    'services', 'wallet',
    'referral', 'tutorials',
    'support', 'rules',
]


def customer_menu_order() -> list[str]:
    """Return a complete, duplicate-free order for the first customer menu."""
    raw = db.get_setting('customer_menu_order', CUSTOMER_MENU_DEFAULT_ORDER)
    raw = list(raw) if isinstance(raw, list) else []
    valid = set(CUSTOMER_MENU_DEFAULT_ORDER)
    order: list[str] = []
    for item in raw:
        key = str(item)
        if key in valid and key not in order:
            order.append(key)
    for key in CUSTOMER_MENU_DEFAULT_ORDER:
        if key not in order:
            order.append(key)
    return order


def set_customer_menu_order(order: list[str]) -> None:
    normalized = [str(x) for x in order if str(x) in CUSTOMER_MENU_DEFAULT_ORDER]
    # Always keep all known buttons in storage, even disabled ones, so an admin can
    # re-enable them later without losing their position.
    for key in CUSTOMER_MENU_DEFAULT_ORDER:
        if key not in normalized:
            normalized.append(key)
    db.set_setting('customer_menu_order', normalized)


def customer_menu_disabled() -> set[str]:
    raw = db.get_setting('customer_menu_disabled', [])
    if not isinstance(raw, list):
        return set()
    valid = set(CUSTOMER_MENU_DEFAULT_ORDER)
    return {str(x) for x in raw if str(x) in valid}


def customer_button_enabled(button_id: str) -> bool:
    return str(button_id) not in customer_menu_disabled()


def set_customer_button_enabled(button_id: str, enabled: bool) -> None:
    button_id = str(button_id)
    if button_id not in CUSTOMER_MENU_DEFAULT_ORDER:
        raise ValueError(f'Unknown customer button id: {button_id}')
    disabled = customer_menu_disabled()
    if enabled:
        disabled.discard(button_id)
    else:
        disabled.add(button_id)
    db.set_setting('customer_menu_disabled', [x for x in CUSTOMER_MENU_DEFAULT_ORDER if x in disabled])


def swap_customer_menu_position(button_id: str, destination_position: int) -> list[str]:
    order = customer_menu_order()
    if button_id not in order:
        raise ValueError('Unknown customer menu button')
    destination = int(destination_position) - 1
    if destination < 0 or destination >= len(order):
        raise ValueError('Invalid customer menu position')
    source = order.index(button_id)
    order[source], order[destination] = order[destination], order[source]
    set_customer_menu_order(order)
    return order


def customer_button_text(button_id: str) -> str:
    setting_key, default_text = CUSTOMER_BUTTON_SETTINGS[button_id]
    value = str(db.get_setting(setting_key, default_text) or '').strip()
    return value or default_text


def customer_menu_title() -> str:
    value = str(db.get_setting('customer_menu_title', 'منوی کاربری:') or '').strip()
    return value or 'منوی کاربری:'


class EditableButtonFilter(Filter):
    """Match a reply-keyboard button even after live editor changed its label.

    The handler remains bound to the original semantic action. Premium emoji ids are
    stripped before comparison because Telegram sends only the visible button text.
    """

    def __init__(self, original_text: str):
        self.original_text = str(original_text)

    async def __call__(self, message: Message) -> bool:
        incoming = str(message.text or '').strip()
        if not incoming:
            return False
        detail = button_detail_id(self.original_text, None, None)
        try:
            from modules import edit_runtime
            stable = edit_runtime.button_stable_key(self.original_text, None, None, detail)
            override = edit_runtime.get_button_text_override(stable)
        except Exception:
            stable = ''
            override = None
        current_raw = str(override if override is not None else self.original_text)
        current, _ = split_button_custom_emoji(current_raw)
        original_visible, _ = split_button_custom_emoji(self.original_text)
        accepted = {current, original_visible}
        if stable:
            aliases = db.get_setting(f'edit_button_aliases:{stable}', [])
            if isinstance(aliases, list):
                for item in aliases:
                    visible, _ = split_button_custom_emoji(str(item))
                    if visible:
                        accepted.add(visible)
        return incoming in accepted


class CustomerButtonFilter(Filter):
    """Match the current configurable customer button label.

    The built-in default is also accepted so an old ReplyKeyboard that is still
    visible on a user's Telegram client does not become dead immediately after
    the admin renames a button.
    """

    def __init__(self, button_id: str):
        if button_id not in CUSTOMER_BUTTON_SETTINGS:
            raise ValueError(f'Unknown customer button id: {button_id}')
        self.button_id = button_id

    async def __call__(self, message: Message) -> bool:
        text = str(message.text or '').strip()
        # Disabled first-menu buttons are not executable by real users, including
        # stale Telegram ReplyKeyboards. During admin live-preview we still allow the
        # action so the editor can test it before re-enabling the button.
        if not customer_button_enabled(self.button_id):
            try:
                from modules import edit_runtime
                session = edit_runtime.current_render_session()
                if not session or session.scope != 'user':
                    return False
            except Exception:
                return False
        setting_key, default_text = CUSTOMER_BUTTON_SETTINGS[self.button_id]
        current_text, _ = split_button_custom_emoji(customer_button_text(self.button_id))
        default_visible, _ = split_button_custom_emoji(default_text)
        accepted = {current_text, default_visible}
        aliases = db.get_setting(f'button_text_aliases:{setting_key}', [])
        if isinstance(aliases, list):
            for item in aliases:
                visible, _ = split_button_custom_emoji(str(item).strip())
                if visible:
                    accepted.add(visible)
        return bool(text) and text in accepted


def _kb(text: str, detail_id: str | None = None) -> KeyboardButton:
    # Use a semantic id whenever possible. This keeps live-editor text/color attached
    # even after the visible label no longer contains words like "خرید" or "تنظیمات".
    semantic_id = detail_id or button_detail_id(text, None, None)
    rendered_text = text
    try:
        from modules import edit_runtime
        stable = edit_runtime.button_stable_key(text, None, None, semantic_id)
        text_override = edit_runtime.get_button_text_override(stable)
        if text_override is not None:
            rendered_text = text_override
        style_override = edit_runtime.get_button_style_override(stable)
        if style_override is not None:
            return KeyboardButton(text=rendered_text, style=BUTTON_STYLE_VALUES.get(style_override))
    except Exception:
        pass
    if semantic_id:
        has_override, override = get_button_detail_override(semantic_id)
        if has_override:
            return KeyboardButton(text=rendered_text, style=override)
    return KeyboardButton(text=rendered_text, style=get_button_style(infer_button_kind(text)))


def main_admin_keyboard(role: str) -> ReplyKeyboardMarkup:
    rows = [
        [_kb(BTN_DASHBOARD)],
        [_kb(BTN_USERS), _kb(BTN_PACKAGES)],
        [_kb(BTN_ORDERS), _kb(BTN_REPORTS)],
        [_kb(BTN_BROADCAST), _kb(BTN_TICKETS)],
        [_kb(BTN_COUPONS), _kb(BTN_WALLET)],
        [_kb(BTN_STARS)],
        [_kb(BTN_SETTINGS), _kb(BTN_EDIT_MODE)],
        [_kb(BTN_MAINTENANCE), _kb(BTN_EXPORTS)],
        [_kb(BTN_NOTIFICATIONS), _kb(BTN_FORCE_JOIN)],
        [_kb(BTN_ADVANCED_SETTINGS), _kb(BTN_REFERRAL)],
        [_kb(BTN_SYSTEM_HEALTH), _kb(BTN_PAYMENT_ENGINE)],
        [_kb(BTN_SYSTEM_ERRORS), _kb(BTN_SELLER_PANEL)],
        [_kb(BTN_CARD_RECEIPTS), _kb(BTN_CAMPAIGNS)],
        [_kb(BTN_TUTORIALS_ADMIN), _kb(BTN_AD_TRACKING)],
    ]
    if role == 'super_admin':
        rows.append([_kb(BTN_ADMINS), _kb(BTN_LOGS)])
        rows.append([_kb(BTN_BACKUP)])
    elif role == 'seller':
        rows = [
            [_kb(BTN_DASHBOARD)],
            [_kb(BTN_USERS), _kb(BTN_PACKAGES)],
            [_kb(BTN_TICKETS), _kb(BTN_NOTIFICATIONS)],
            [_kb(BTN_REFERRAL), _kb(BTN_SELLER_PANEL)],
        ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[_kb(BTN_BACK)]], resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[_kb(BTN_CANCEL)]], resize_keyboard=True)


def _customer_kb(button_id: str) -> KeyboardButton:
    return _kb(customer_button_text(button_id), CUSTOMER_BUTTON_DETAIL_IDS[button_id])


def customer_keyboard() -> ReplyKeyboardMarkup:
    # Only the FIRST customer menu is reorderable/disableable. In user Edit Mode all
    # buttons remain visible (disabled ones are marked by the inline preview layer),
    # otherwise disabled buttons are completely absent for real users.
    include_disabled = False
    try:
        from modules import edit_runtime
        session = edit_runtime.current_render_session()
        include_disabled = bool(session and session.scope == 'user')
    except Exception:
        pass
    button_ids = customer_menu_order()
    if not include_disabled:
        button_ids = [button_id for button_id in button_ids if customer_button_enabled(button_id)]
    rows = []
    for index in range(0, len(button_ids), 2):
        rows.append([_customer_kb(button_id) for button_id in button_ids[index:index + 2]])
    # Telegram accepts an empty keyboard poorly; keep a harmless placeholder only if
    # the admin deliberately disabled every first-menu button.
    if not rows:
        rows = [[KeyboardButton(text='—')]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
