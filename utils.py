from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

T = TypeVar('T')

RETRYABLE_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TelegramNetworkError,
    ConnectionError,
    TimeoutError,
    OSError,
)


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    retry_on: tuple[type[BaseException], ...] = RETRYABLE_NETWORK_EXCEPTIONS,
) -> T:
    """Run an async operation with bounded retries on transient network failures.

    Telegram uploads behind unstable links fail with TelegramNetworkError /
    ClientOSError (TLS resets, "Can not write request body" while streaming a
    file). Each attempt re-invokes the callable so FSInputFile streams are
    re-opened fresh. TelegramRetryAfter is honored with its server-provided wait.
    """
    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await operation()
        except TelegramRetryAfter as exc:
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(float(getattr(exc, 'retry_after', 0) or base_delay))
        except retry_on as exc:
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(min(max_delay, base_delay * (2 ** (attempt - 1))))
    assert last_error is not None
    raise last_error


async def edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: str | None = None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        msg = str(exc).lower()
        if 'message is not modified' in msg or 'message to edit not found' in msg:
            return
        try:
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramBadRequest as inner_exc:
            inner_msg = str(inner_exc).lower()
            if 'message is not modified' in inner_msg:
                return
            raise


async def answer_callback(callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await callback.answer(text or '', show_alert=show_alert)
    except Exception:
        pass


async def safe_delete(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def fmt_money(amount: int | float | None, currency: str = 'XTR') -> str:
    amount = amount or 0
    return f'{amount:,} {currency}'


import html
import re
import time
from typing import Any

_RATE_BUCKET: dict[str, float] = {}


CUSTOM_EMOJI_RE = re.compile(r"\[(\d{10,24})\]")
BUTTON_TG_EMOJI_RE = re.compile(
    r"<tg-emoji\s+emoji-id=['\"](\d{10,24})['\"][^>]*>.*?</tg-emoji>",
    re.IGNORECASE | re.DOTALL,
)
ESCAPED_TG_EMOJI_RE = re.compile(
    r"&lt;tg-emoji\s+emoji-id=(?:&quot;|&#x27;|[\"\'])(\d{10,24})(?:&quot;|&#x27;|[\"\'])[^&]*?&gt;.*?&lt;/tg-emoji&gt;",
    re.IGNORECASE | re.DOTALL,
)


def _custom_emoji_ids(text: str | None) -> list[str]:
    raw = str(text or '')
    ids: list[str] = []
    for pattern in (CUSTOM_EMOJI_RE, BUTTON_TG_EMOJI_RE, ESCAPED_TG_EMOJI_RE):
        for match in pattern.finditer(raw):
            emoji_id = str(match.group(1))
            if emoji_id not in ids:
                ids.append(emoji_id)
    return ids


def has_custom_emoji_markup(text: str | None) -> bool:
    raw = str(text or '')
    return bool(
        CUSTOM_EMOJI_RE.search(raw)
        or BUTTON_TG_EMOJI_RE.search(raw)
        or ESCAPED_TG_EMOJI_RE.search(raw)
    )


async def _custom_emoji_alts(bot: Bot | None, ids: list[str]) -> dict[str, str]:
    alts = {emoji_id: '⭐' for emoji_id in ids}
    if bot is None or not ids:
        return alts
    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=ids)
        for emoji_id, sticker in zip(ids, stickers):
            alt = getattr(sticker, 'emoji', None)
            if alt:
                alts[emoji_id] = str(alt)
    except Exception:
        pass
    return alts


async def render_custom_emoji_output(
    bot: Bot | None,
    text: str | None,
    parse_mode: str | None = None,
) -> tuple[str, str | None]:
    """Guarantee that premium-emoji tokens never leak to Telegram as visible IDs.

    All admin editors store premium emoji as ``[CUSTOM_EMOJI_ID]``. This function is
    intentionally applied at the final output boundary (Message.* and Bot.send_*), so
    every text/caption path gets the same conversion even if an older module bypasses
    ``answer_template``. Existing HTML custom-emoji tags are kept intact.

    If the original message was not HTML, we escape it as literal text before switching
    to HTML. That preserves what the user would have seen while allowing Telegram to
    render the custom emoji entity instead of exposing its numeric ID.
    """
    raw = str(text or '')
    if not has_custom_emoji_markup(raw):
        return raw, parse_mode

    # Recover tags that were accidentally HTML-escaped by an older rendering path.
    raw = ESCAPED_TG_EMOJI_RE.sub(lambda m: f'[{m.group(1)}]', raw)
    mode = str(parse_mode or '').lower()

    if mode == 'html':
        ids = []
        for match in CUSTOM_EMOJI_RE.finditer(raw):
            emoji_id = str(match.group(1))
            if emoji_id not in ids:
                ids.append(emoji_id)
        alts = await _custom_emoji_alts(bot, ids)
        raw = CUSTOM_EMOJI_RE.sub(
            lambda m: f'<tg-emoji emoji-id="{m.group(1)}">{html.escape(alts.get(str(m.group(1)), "⭐"))}</tg-emoji>',
            raw,
        )
        return raw, 'HTML'

    # A tg-emoji tag cannot render under plain/Markdown modes. Normalize existing tags
    # back to tokens, escape the surrounding text, and emit one valid HTML payload.
    normalized = BUTTON_TG_EMOJI_RE.sub(lambda m: f'[{m.group(1)}]', raw)
    rendered = await render_template_html(bot, normalized, {})
    return rendered, 'HTML'


async def render_custom_emoji_plain(bot: Bot | None, text: str | None) -> str:
    """Remove premium-emoji IDs from plain-only Telegram surfaces (e.g. callback toasts).

    answerCallbackQuery does not accept parse entities, so the actual premium entity cannot
    be attached there. We still resolve its Unicode fallback instead of exposing the ID.
    """
    raw = str(text or '')
    if not has_custom_emoji_markup(raw):
        return raw
    raw = ESCAPED_TG_EMOJI_RE.sub(lambda m: f'[{m.group(1)}]', raw)
    raw = BUTTON_TG_EMOJI_RE.sub(lambda m: f'[{m.group(1)}]', raw)
    ids = []
    for match in CUSTOM_EMOJI_RE.finditer(raw):
        emoji_id = str(match.group(1))
        if emoji_id not in ids:
            ids.append(emoji_id)
    alts = await _custom_emoji_alts(bot, ids)
    return CUSTOM_EMOJI_RE.sub(lambda m: alts.get(str(m.group(1)), '⭐'), raw)


def normalize_custom_emoji_markup(markup):
    """Normalize premium emoji for every outgoing Inline/Reply keyboard.

    This is a final safety net in addition to the constructor patch. It also fixes
    keyboard objects created before startup patches or reconstructed from saved models.
    """
    if markup is None:
        return None
    try:
        from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
    except Exception:
        return markup

    attr = None
    if isinstance(markup, InlineKeyboardMarkup):
        attr = 'inline_keyboard'
    elif isinstance(markup, ReplyKeyboardMarkup):
        attr = 'keyboard'
    if attr is None:
        return markup

    changed = False
    rows = []
    for row in list(getattr(markup, attr, None) or []):
        out_row = []
        for button in list(row or []):
            raw_text = str(getattr(button, 'text', '') or '')
            visible, parsed_id = split_button_custom_emoji(raw_text)
            current_id = getattr(button, 'icon_custom_emoji_id', None)
            emoji_id = current_id or parsed_id
            if visible != raw_text or (parsed_id and not current_id):
                update = {'text': visible}
                if emoji_id:
                    update['icon_custom_emoji_id'] = str(emoji_id)
                try:
                    button = button.model_copy(update=update)
                except Exception:
                    # Pydantic v1 compatibility / defensive fallback.
                    try:
                        button = button.copy(update=update)
                    except Exception:
                        pass
                changed = True
            out_row.append(button)
        rows.append(out_row)
    if not changed:
        return markup
    try:
        return markup.model_copy(update={attr: rows})
    except Exception:
        try:
            return markup.copy(update={attr: rows})
        except Exception:
            return markup



def message_text_with_custom_emoji_tokens(message: Message) -> str:
    """Convert premium/custom emoji entities in an incoming text message to [ID] tokens.

    This lets admins configure button icons either by pasting ``[CUSTOM_EMOJI_ID]``
    or by sending the actual Telegram premium emoji. Telegram entity offsets are
    UTF-16 based, so replacements are performed on UTF-16 code units.
    """
    text_value = getattr(message, 'text', None)
    if text_value is None:
        text_value = getattr(message, 'caption', None)
        entities = list(getattr(message, 'caption_entities', None) or [])
    else:
        entities = list(getattr(message, 'entities', None) or [])
    text = str(text_value or '')
    custom = [
        ent for ent in entities
        if str(getattr(ent, 'type', '')) == 'custom_emoji' and getattr(ent, 'custom_emoji_id', None)
    ]
    if not custom:
        return text

    raw = text.encode('utf-16-le')
    # Replace from the end so earlier Telegram offsets stay valid.
    for ent in sorted(custom, key=lambda x: int(getattr(x, 'offset', 0)), reverse=True):
        start = int(getattr(ent, 'offset', 0)) * 2
        end = (int(getattr(ent, 'offset', 0)) + int(getattr(ent, 'length', 0))) * 2
        token = f"[{getattr(ent, 'custom_emoji_id')}]".encode('utf-16-le')
        raw = raw[:start] + token + raw[end:]
    return raw.decode('utf-16-le')


def split_button_custom_emoji(text: str | None) -> tuple[str, str | None]:
    """Return the visible Telegram button label and optional premium emoji id.

    Admin-editable button labels use the same shorthand as editable message text:
    ``[CUSTOM_EMOJI_ID] Button text``. Telegram button custom emoji are not HTML
    entities; Bot API expects the id in ``icon_custom_emoji_id``.

    A pasted ``<tg-emoji emoji-id="...">...</tg-emoji>`` form is accepted too.
    If more than one custom emoji token is present, Telegram supports only one button
    icon, so the first id is used and all id tokens are removed from the visible label.
    """
    raw = str(text or '')
    ids: list[str] = []

    def collect_html(match: re.Match) -> str:
        ids.append(match.group(1))
        return ' '

    cleaned = BUTTON_TG_EMOJI_RE.sub(collect_html, raw)

    def collect_bracket(match: re.Match) -> str:
        ids.append(match.group(1))
        return ' '

    cleaned = CUSTOM_EMOJI_RE.sub(collect_bracket, cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Telegram requires button text even when an icon is present. Keep it visually
    # empty when the admin intentionally configured an icon-only button.
    if ids and not cleaned:
        cleaned = '\u200b'
    return cleaned, (ids[0] if ids else None)


def truncate_custom_emoji_text(text: str | None, max_visible: int, suffix: str = '…') -> str:
    """Truncate without ever cutting a [CUSTOM_EMOJI_ID] / tg-emoji token in half."""
    raw = str(text or '')
    if max_visible <= 0:
        return ''
    token_re = re.compile(
        r"\[\d{10,24}\]|<tg-emoji\s+emoji-id=[\"\']\d{10,24}[\"\'][^>]*>.*?</tg-emoji>|&lt;tg-emoji\s+emoji-id=.*?&lt;/tg-emoji&gt;",
        re.IGNORECASE | re.DOTALL,
    )
    parts = []
    pos = 0
    visible = 0
    truncated = False
    for match in token_re.finditer(raw):
        before = raw[pos:match.start()]
        room = max_visible - visible
        if len(before) > room:
            parts.append(before[:max(0, room)])
            truncated = True
            break
        parts.append(before)
        visible += len(before)
        if visible >= max_visible:
            truncated = match.start() < len(raw)
            break
        parts.append(match.group(0))
        visible += 1
        pos = match.end()
    else:
        tail = raw[pos:]
        room = max_visible - visible
        if len(tail) > room:
            parts.append(tail[:max(0, room)])
            truncated = True
        else:
            parts.append(tail)
    result = ''.join(parts)
    if truncated and suffix:
        # Make room for the suffix in visible terms, recursively and token-safely.
        suffix_len = len(suffix)
        if suffix_len < max_visible:
            result = truncate_custom_emoji_text(result, max_visible - suffix_len, suffix='') + suffix
        else:
            result = suffix[:max_visible]
    return result


# Price formatting is display-only. Stored numeric values and payment calculations stay untouched.
# We deliberately limit automatic grouping to currency-adjacent numbers and known financial
# template variables so IDs, card numbers, traffic, dates and subscription links cannot be altered.
_MONEY_KEY_PARTS = (
    'price', 'amount', 'payable', 'discount', 'wallet_used', 'balance', 'revenue',
    'reward', 'commission', 'income', 'profit', 'fee', 'cost', 'toman', 'rial',
)
_CURRENCY_AFTER_NUMBER_RE = re.compile(r'(?<![\d,])([0-9]{4,})(?=\s*(?:تومان|تومن|ریال|﷼))')
_CURRENCY_BEFORE_NUMBER_RE = re.compile(r'((?:تومان|تومن|ریال|﷼)\s*)([0-9]{4,})(?![\d,])')


def _money_key(key: str) -> bool:
    key = str(key or '').lower()
    return any(part in key for part in _MONEY_KEY_PARTS)


def _group_plain_number(value: Any) -> str:
    """Add comma grouping only when value itself is an integer-like number.

    Existing separators/currency text are preserved and normalized by format_display_prices().
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f'{value:,}'
    if isinstance(value, float) and value.is_integer():
        return f'{int(value):,}'
    raw = str(value if value is not None else '')
    compact = raw.strip()
    if re.fullmatch(r'-?[0-9]+', compact):
        sign = '-' if compact.startswith('-') else ''
        digits = compact[1:] if sign else compact
        return sign + f'{int(digits):,}'
    return format_display_prices(raw)


def display_template_value(key: str, value: Any) -> str:
    """Format known financial template values for display without mutating source data."""
    if _money_key(key):
        return _group_plain_number(value)
    return str(value if value is not None else '')


def format_display_prices(text: str | None) -> str:
    """Group currency amounts in outgoing text (145000 تومان -> 145,000 تومان).

    The regex requires an explicit currency word, therefore unrelated long numbers such as
    Telegram IDs, card numbers and timestamps are left byte-for-byte unchanged.
    """
    raw = str(text or '')
    raw = _CURRENCY_AFTER_NUMBER_RE.sub(lambda m: f'{int(m.group(1)):,}', raw)
    raw = _CURRENCY_BEFORE_NUMBER_RE.sub(lambda m: m.group(1) + f'{int(m.group(2)):,}', raw)
    return raw


def render_template_text(template: str | None, context: dict[str, Any] | None = None) -> str:
    """Plain-text template renderer kept for non-HTML use."""
    text = str(template or '')
    context = context or {}
    for key, value in context.items():
        text = text.replace('{' + str(key) + '}', display_template_value(str(key), value))
    return text


def user_template_context(user: Any = None, *, support: str = '') -> dict[str, Any]:
    first_name = getattr(user, 'first_name', None) or ''
    username = getattr(user, 'username', None) or ''
    user_id = getattr(user, 'id', None) or ''
    display_name = first_name or (('@' + username) if username else str(user_id or 'کاربر'))
    return {
        'name': display_name,
        'first_name': first_name,
        'username': ('@' + username) if username else '',
        'user_id': user_id,
        'support': support or '',
    }


# Variables whose values are identifiers/links/codes and should be easy to copy in Telegram.
# The admin still edits templates using the normal {variable} syntax; wrapping happens only
# when the final message is rendered, so it cannot corrupt stored templates or Premium Emoji tokens.
COPYABLE_TEMPLATE_VARS = {
    'card_number', 'sub_link', 'subscription_url', 'order_id', 'user_id', 'package_id',
    'service_name', 'service_username', 'panel_username', 'username', 'client',
    'receipt_id', 'entity_id', 'ticket_id', 'coupon_code', 'payment_id', 'transaction_id',
}

def _template_value_html(key: str, value: Any) -> str:
    safe = html.escape(display_template_value(str(key), value))
    if str(key) in COPYABLE_TEMPLATE_VARS and safe:
        return '<code>' + safe.replace('`', '') + '</code>'
    return safe


async def render_template_html(bot: Bot | None, template: str | None, context: dict[str, Any] | None = None) -> str:
    """Render admin-editable text safely as Telegram HTML.

    Supported variables use {key}. Custom emoji shorthand is [CUSTOM_EMOJI_ID].
    The custom emoji's real fallback emoji is resolved through Telegram when possible.
    """
    raw = str(template or '')
    context = context or {}

    # Protect custom emoji tokens before escaping the admin supplied text.
    ids = []
    def protect(match):
        emoji_id = match.group(1)
        if emoji_id not in ids:
            ids.append(emoji_id)
        return f'\x00CUSTOM_EMOJI_{len(ids)-1}\x00'
    protected = CUSTOM_EMOJI_RE.sub(protect, raw)

    # Protect template variables too, so inserted user values are escaped independently.
    variable_tokens: dict[str, str] = {}
    for idx, (key, value) in enumerate(context.items()):
        marker = f'\x00TPLVAR_{idx}\x00'
        needle = '{' + str(key) + '}'
        if needle in protected:
            protected = protected.replace(needle, marker)
            variable_tokens[marker] = _template_value_html(str(key), value)

    rendered = html.escape(protected)
    for marker, value in variable_tokens.items():
        rendered = rendered.replace(marker, value)

    alts = ['🙂'] * len(ids)
    if bot is not None and ids:
        try:
            stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=ids)
            for idx, sticker in enumerate(stickers[:len(ids)]):
                alt = getattr(sticker, 'emoji', None)
                if alt:
                    alts[idx] = str(alt)
        except Exception:
            # A valid fallback emoji keeps the outgoing message valid.
            pass

    for idx, emoji_id in enumerate(ids):
        tag = f'<tg-emoji emoji-id="{emoji_id}">{html.escape(alts[idx])}</tg-emoji>'
        rendered = rendered.replace(f'\x00CUSTOM_EMOJI_{idx}\x00', tag)
    return rendered


def content_sticker_setting_key(content_key: str) -> str:
    return f"{content_key}__stickers"


def get_content_stickers(content_key: str | None) -> list[str]:
    if not content_key:
        return []
    try:
        import db
        value = db.get_setting(content_sticker_setting_key(content_key), [])
    except Exception:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return []


def set_content_stickers(content_key: str, sticker_file_ids: list[str]) -> None:
    import db
    db.set_setting(content_sticker_setting_key(content_key), [str(x) for x in sticker_file_ids if str(x).strip()])


async def answer_template(
    message: Message,
    template: str | None,
    context: dict[str, Any] | None = None,
    reply_markup=None,
    *,
    content_key: str | None = None,
) -> None:
    rendered = await render_template_html(message.bot, template, context)
    # In editor mode this hint lets the generic output patch show the ORIGINAL
    # template (with {variables}) instead of only the rendered customer text.
    try:
        from modules.edit_runtime import text_hint
        hint_cm = text_hint(
            setting_key=content_key,
            template=str(template or ''),
            variables=context or {},
            content_key=content_key,
            label='متن این بخش',
            allow_stickers=True,
        )
    except Exception:
        import contextlib
        hint_cm = contextlib.nullcontext()
    with hint_cm:
        try:
            await message.answer(rendered, reply_markup=reply_markup, parse_mode='HTML')
        except TelegramBadRequest:
            # Last-resort plain text keeps the customer flow alive if Telegram rejects an entity.
            await message.answer(render_template_text(template, context), reply_markup=reply_markup)

    # Optional large Telegram stickers attached to editable content. Each sticker is sent
    # as its own message after the text, exactly as configured by an admin.
    for sticker_file_id in get_content_stickers(content_key):
        try:
            await message.answer_sticker(sticker=sticker_file_id)
        except Exception as exc:
            try:
                import db
                db.record_system_error(
                    'utils.answer_template.sticker',
                    str(exc),
                    entity_type='content',
                    entity_id=str(content_key or ''),
                    error_type=type(exc).__name__,
                )
            except Exception:
                pass



BUTTON_STYLE_VALUES = {'default': None, 'primary': 'primary', 'success': 'success', 'danger': 'danger'}

def get_button_style(kind: str = 'neutral') -> str | None:
    """Return Telegram button style configured by super admin.

    kind: primary, success, danger, neutral
    """
    defaults = {'primary': 'primary', 'success': 'success', 'danger': 'danger', 'neutral': 'default'}
    key = f'button_style_{kind}'
    try:
        import db
        raw = str(db.get_setting(key, defaults.get(kind, 'default')) or 'default').lower().strip()
    except Exception:
        raw = defaults.get(kind, 'default')
    return BUTTON_STYLE_VALUES.get(raw)

def infer_button_kind(text: str) -> str:
    t = str(text or '')
    low = t.lower()
    if any(x in t for x in ('❌','🗑','🚫','حذف','لغو','رد')):
        return 'danger'
    if any(x in t for x in ('✅','✔','تایید','ثبت','فعال')):
        return 'success'
    if any(x in t for x in ('🔙','⬅','بازگشت','برگشت')):
        return 'neutral'
    return 'primary'

def person_display(user_id: int | str | None, *, include_username: bool = False) -> str:
    """Human-friendly admin label: Name - numeric ID."""
    try:
        uid = int(user_id)
    except Exception:
        return str(user_id or '-')
    try:
        import db
        u = db.fetchone('SELECT telegram_id, first_name, username FROM bot_users WHERE telegram_id=?', (uid,)) or {}
        a = db.fetchone('SELECT display_name FROM bot_admins WHERE telegram_id=?', (uid,)) or {}
        name = str(u.get('first_name') or '').strip() or str(a.get('display_name') or '').strip()
        username = str(u.get('username') or '').strip()
        if not name and username:
            name = '@' + username
        if not name:
            name = 'بدون نام'
        if include_username and username and ('@'+username) != name:
            name += f' (@{username})'
        return f'{name} - {uid}'
    except Exception:
        return str(uid)

def code(value: Any) -> str:
    return '`' + str(value if value is not None else '-').replace('`', '') + '`'


def html_code(value: Any) -> str:
    return '<code>' + html.escape(str(value if value is not None else '-')) + '</code>'


def friendly_error(exc: Exception | str) -> str:
    text = str(exc)
    low = text.lower()
    if any(x in low for x in ('timeout', 'temporarily', 'connection', 'server disconnected', '502', '503', '504')):
        return 'ارتباط با سرور موقتا برقرار نشد. لطفا چند دقیقه دیگر دوباره تلاش کنید.'
    if 'not found' in low or '404' in low:
        return 'اطلاعات موردنظر پیدا نشد. لطفا صفحه را بروزرسانی کنید.'
    return 'خطای موقت رخ داد. لطفا دوباره تلاش کنید یا به پشتیبانی پیام بدهید.'


def allow_memory_action(key: str, seconds: int) -> bool:
    now = time.monotonic()
    last = _RATE_BUCKET.get(key, 0)
    if now - last < seconds:
        return False
    _RATE_BUCKET[key] = now
    return True

# --- Fine-grained Telegram button style controls (v32) ---
BUTTON_DETAIL_GROUPS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    'common': ('🧭 عمومی', [
        ('back', 'همه دکمه های بازگشت'), ('cancel', 'همه دکمه های لغو/انصراف'),
        ('confirm', 'همه دکمه های تایید'), ('delete', 'همه دکمه های حذف/رد'),
        ('refresh', 'همه دکمه های بروزرسانی/بررسی'),
    ]),
    'customer': ('👤 منوی مشتری', [
        ('buy', 'خرید اشتراک'), ('test_service', 'سرویس تست'), ('my_services', 'سرویس های من'),
        ('customer_wallet', 'کیف پول مشتری'), ('referral', 'دعوت دوستان'), ('tutorials', 'آموزش'), ('support', 'پشتیبانی'), ('rules', 'قوانین'),
    ]),
    'payments': ('💳 پرداخت ها', [
        ('gateway_stars', 'درگاه Stars'), ('gateway_tetrapay', 'درگاه تتراپی'),
        ('gateway_plisio', 'درگاه رمز ارز / Plisio'), ('gateway_card', 'درگاه کارت به کارت'),
        ('payment_check', 'بررسی وضعیت پرداخت'), ('wallet_charge', 'شارژ کیف پول'),
    ]),
    'services': ('🛠 سرویس ها', [
        ('service_sub', 'لینک اشتراک'), ('service_configs', 'دریافت کانفیگ ها'), ('service_revoke', 'تغییر لینک'),
        ('service_renew', 'تمدید سرویس'), ('service_auto_renew', 'تمدید خودکار'), ('service_transfer', 'انتقال سرویس'),
        ('service_delete', 'حذف سرویس'),
    ]),
    'admin_actions': ('⚙️ عملیات ادمین', [
        ('pack_create', 'ساخت بسته'), ('pack_price', 'قیمت بسته'), ('pack_bulk_price', 'تغییر درصدی قیمت بسته ها'),
        ('coupon_create', 'ساخت کد تخفیف'), ('coupon_toggle', 'فعال/غیرفعال کد تخفیف'), ('coupon_delete', 'حذف کد تخفیف'),
        ('admin_add', 'افزودن ادمین/فروشنده'), ('admin_role', 'تغییر نقش ادمین'), ('admin_toggle', 'فعال/غیرفعال ادمین'), ('admin_delete', 'حذف ادمین'),
        ('broadcast_send', 'تایید ارسال همگانی'), ('wallet_manual', 'شارژ/کسر دستی کیف پول'), ('wallet_lookup', 'مشاهده موجودی کاربر'),
        ('join_add', 'افزودن جوین اجباری'), ('join_toggle', 'فعال/غیرفعال جوین'), ('join_delete', 'حذف جوین'), ('join_check', 'بررسی عضویت'),
        ('notification_read', 'خواندن اعلان ها'), ('notification_toggle', 'روشن/خاموش اعلان'),
        ('referral_reward', 'تنظیم پاداش دعوت'), ('test_toggle', 'روشن/خاموش سرویس تست'), ('test_reset', 'ریست سرویس تست'),
        ('card_approve', 'تایید فیش کارت به کارت'), ('card_reject', 'رد فیش کارت به کارت'),
        ('campaign_create', 'ساخت کمپین'), ('campaign_stop', 'توقف کمپین'), ('campaign_permissions', 'دسترسی کمپین'),
    ]),
    'admin': ('🛡 پنل ادمین', [
        ('admin_dashboard', 'داشبورد'), ('admin_users', 'کاربران'), ('admin_packages', 'بسته ها'), ('admin_orders', 'سفارش ها'),
        ('admin_reports', 'گزارش ها'), ('admin_broadcast', 'پیام همگانی'), ('admin_tickets', 'تیکت ها'), ('admin_coupons', 'کد تخفیف'),
        ('admin_wallet', 'کیف پول ادمین'), ('admin_stars', 'پرداخت های Stars'), ('admin_texts', 'متن های ربات'), ('admin_settings', 'تنظیمات'),
        ('admin_maintenance', 'حالت تعمیر'), ('admin_exports', 'خروجی اکسل'), ('admin_notifications', 'اعلان ها'), ('admin_force_join', 'جوین اجباری'),
        ('admin_advanced', 'تنظیمات پیشرفته'), ('admin_referral', 'دعوت دوستان ادمین'), ('admin_health', 'وضعیت سیستم'), ('admin_payment_engine', 'موتور پرداخت'),
        ('admin_errors', 'خطاهای سیستم'), ('admin_seller', 'پنل فروشنده'), ('admin_card_receipts', 'فیش های کارت به کارت'),
        ('admin_admins', 'مدیریت ادمین ها'), ('admin_logs', 'لاگ ادمین ها'), ('admin_backup', 'بکاپ'), ('admin_campaigns', 'کمپین ها'), ('admin_tutorials', 'مدیریت آموزش ها'),
    ]),
}


def _detail_style_setting_key(detail_id: str) -> str:
    return f'button_detail_style:{detail_id}'


def get_button_detail_override(detail_id: str | None) -> tuple[bool, str | None]:
    if not detail_id:
        return False, None
    try:
        import db
        raw = db.get_setting(_detail_style_setting_key(detail_id), '__inherit__')
    except Exception:
        raw = '__inherit__'
    raw = str(raw or '__inherit__').lower().strip()
    if raw == '__inherit__':
        return False, None
    return True, BUTTON_STYLE_VALUES.get(raw)


def set_button_detail_style(detail_id: str, value: str) -> None:
    if value not in {'__inherit__', 'default', 'primary', 'success', 'danger'}:
        raise ValueError('invalid button style')
    import db
    db.set_setting(_detail_style_setting_key(detail_id), value)


def button_detail_id(text: str | None = None, callback_data: str | None = None, url: str | None = None) -> str | None:
    t = str(text or '')
    c = str(callback_data or '')
    low = t.lower()
    # Specific payment buttons before generic wallet/service matching.
    if 'stars' in c or 'استار' in t or 'Stars' in t: return 'gateway_stars'
    if 'tetrapay' in c or 'تتراپی' in t: return 'gateway_tetrapay'
    if 'plisio' in c or 'crypto' in c or 'رمز ارز' in t: return 'gateway_plisio'
    if ('card' in c and ('pay' in c or 'charge' in c)) or 'کارت به کارت' in t: return 'gateway_card'
    if 'check' in c or 'بررسی وضعیت پرداخت' in t: return 'payment_check'
    if 'wallet_charge' in c or 'شارژ کیف پول' in t: return 'wallet_charge'
    # Fine-grained admin operations by callback id/prefix.
    if c.startswith('pack:create'): return 'pack_create'
    if c.startswith('pack:price'): return 'pack_price'
    if c.startswith('pack:bulk_price'): return 'pack_bulk_price'
    if c.startswith('coupon:create'): return 'coupon_create'
    if c.startswith('coupon:toggle'): return 'coupon_toggle'
    if c.startswith('coupon:delete'): return 'coupon_delete'
    if c.startswith('admins:add'): return 'admin_add'
    if c.startswith('admins:role'): return 'admin_role'
    if c.startswith('admins:toggle'): return 'admin_toggle'
    if c.startswith('admins:delete'): return 'admin_delete'
    if c == 'bcast:send': return 'broadcast_send'
    if c == 'wallet:charge': return 'wallet_manual'
    if c == 'wallet:balance': return 'wallet_lookup'
    if c == 'join:add': return 'join_add'
    if c.startswith('join:toggle'): return 'join_toggle'
    if c.startswith('join:delete'): return 'join_delete'
    if c == 'join:check': return 'join_check'
    if c == 'notif:read_all': return 'notification_read'
    if c == 'notif:toggle': return 'notification_toggle'
    if c == 'ref_admin:reward': return 'referral_reward'
    if c == 'test_admin:toggle': return 'test_toggle'
    if c == 'test_admin:reset_all': return 'test_reset'
    if c.startswith('cardadmin:approve'): return 'card_approve'
    if c.startswith('cardadmin:reject'): return 'card_reject'
    if c == 'campaign:create' or c == 'campaign:confirm': return 'campaign_create'
    if c.startswith('campaign:stop'): return 'campaign_stop'
    if c.startswith('campaign:perm'): return 'campaign_permissions'

    # Common actions.
    if any(x in t for x in ('🔙', '⬅', 'بازگشت', 'برگشت')): return 'back'
    if any(x in t for x in ('انصراف', 'لغو')): return 'cancel'
    if any(x in t for x in ('🗑', 'حذف سرویس')) or 'delete' in c: return 'service_delete' if 'service' in c else 'delete'
    if any(x in t for x in ('✅', '✔', 'تایید')) and 'عضویت' not in t: return 'confirm'
    if any(x in t for x in ('🔄', 'بروزرسانی', 'بررسی')): return 'refresh'
    # Customer menu/actions.
    if 'خرید اشتراک' in t or c.startswith('cust_buy'): return 'buy'
    if 'سرویس تست' in t or 'test' in c: return 'test_service'
    if 'سرویس های من' in t or c == 'cust_services': return 'my_services'
    if 'کیف پول من' in t or c in {'cust_wallet_home','cust_wallet'}: return 'customer_wallet'
    if 'دعوت دوستان' in t or c.startswith('ref_'): return 'referral'
    if 'پشتیبانی' in t: return 'support'
    if t.strip() == '🎓 آموزش': return 'tutorials'
    if 'قوانین' in t: return 'rules'
    # Service details.
    if 'service_sub' in c or 'لینک اشتراک' in t: return 'service_sub'
    if 'service_config' in c or 'کانفیگ' in t: return 'service_configs'
    if 'service_revoke' in c or 'تغییر لینک' in t: return 'service_revoke'
    if 'auto_renew' in c or 'تمدید خودکار' in t: return 'service_auto_renew'
    if 'service_renew' in c or 'تمدید سرویس' in t: return 'service_renew'
    if 'service_transfer' in c or 'انتقال سرویس' in t: return 'service_transfer'
    # Admin reply menu labels.
    admin_map = {
        'داشبورد':'admin_dashboard','کاربران':'admin_users','بسته':'admin_packages','سفارش':'admin_orders','گزارش':'admin_reports',
        'پیام همگانی':'admin_broadcast','تیکت':'admin_tickets','کد تخفیف':'admin_coupons','پرداخت های Stars':'admin_stars',
        'پرداخت‌های Stars':'admin_stars','متن های ربات':'admin_texts','متن‌های ربات':'admin_texts','تنظیمات پیشرفته':'admin_advanced',
        'تنظیمات':'admin_settings','حالت تعمیر':'admin_maintenance','خروجی اکسل':'admin_exports','اعلان':'admin_notifications',
        'جوین اجباری':'admin_force_join','وضعیت سیستم':'admin_health','موتور پرداخت':'admin_payment_engine','خطاهای سیستم':'admin_errors',
        'پنل فروشنده':'admin_seller','فیش های کارت به کارت':'admin_card_receipts','مدیریت ادمین':'admin_admins','لاگ ادمین':'admin_logs',
        'بکاپ':'admin_backup','کمپین':'admin_campaigns','کیف پول':'admin_wallet','دعوت دوستان':'admin_referral','مدیریت آموزش':'admin_tutorials',
    }
    for needle, detail in admin_map.items():
        if needle in t:
            if needle == 'دعوت دوستان': return 'admin_referral'
            if needle == 'کیف پول' and 'من' not in t: return 'admin_wallet'
            return detail
    return None


def resolved_button_style(text: str | None = None, callback_data: str | None = None, url: str | None = None, fallback_kind: str | None = None) -> str | None:
    detail = button_detail_id(text, callback_data, url)
    has_override, style = get_button_detail_override(detail)
    if has_override:
        return style
    return get_button_style(fallback_kind or infer_button_kind(str(text or '')))


_BUTTON_STYLE_PATCHED = False

def install_button_style_patch() -> None:
    """Apply per-button style overrides to every InlineKeyboardButton/KeyboardButton.

    This patches aiogram constructors once at startup, so existing modules automatically
    inherit the detailed style system without rewriting hundreds of button definitions.
    """
    global _BUTTON_STYLE_PATCHED
    if _BUTTON_STYLE_PATCHED:
        return
    from aiogram.types import InlineKeyboardButton, KeyboardButton
    inline_init = InlineKeyboardButton.__init__
    keyboard_init = KeyboardButton.__init__

    def patched_inline(self, *args, **kwargs):
        source_text = kwargs.get('text')
        callback_data = kwargs.get('callback_data')
        url = kwargs.get('url')
        detail = button_detail_id(source_text, callback_data, url)
        raw_text = source_text
        generic_style = None
        # Edit-mode overrides are resolved before premium emoji parsing. Callback
        # data stays untouched, so changing a label never changes its real action.
        if not str(callback_data or '').startswith('editmode:') and not str(callback_data or '').startswith(('cust_service_refresh:', 'cust_service_configs:', 'cust_service_revoke:', 'cust_service_renew:', 'cust_service_auto_renew:', 'cust_service_transfer:', 'cust_service_delete_start:')) and str(callback_data or '') != 'cust_services':
            try:
                from modules import edit_runtime
                stable = edit_runtime.button_stable_key(source_text, callback_data, url, detail)
                text_override = edit_runtime.get_button_text_override(stable)
                if text_override is not None:
                    raw_text = text_override
                generic_style = edit_runtime.get_button_style_override(stable)
            except Exception:
                generic_style = None
        text, custom_emoji_id = split_button_custom_emoji(raw_text)
        if source_text is not None:
            kwargs['text'] = text
        if custom_emoji_id and not kwargs.get('icon_custom_emoji_id'):
            kwargs['icon_custom_emoji_id'] = custom_emoji_id
        if generic_style is not None:
            kwargs['style'] = BUTTON_STYLE_VALUES.get(generic_style)
        else:
            has_override, override = get_button_detail_override(detail)
            if has_override and 'style' not in kwargs:
                kwargs['style'] = override
            elif 'style' not in kwargs:
                kwargs['style'] = resolved_button_style(source_text, callback_data, url)
        return inline_init(self, *args, **kwargs)

    def patched_keyboard(self, *args, **kwargs):
        source_text = kwargs.get('text')
        detail = button_detail_id(source_text, None, None)
        raw_text = source_text
        generic_style = None
        try:
            from modules import edit_runtime
            stable = edit_runtime.button_stable_key(source_text, None, None, detail)
            text_override = edit_runtime.get_button_text_override(stable)
            if text_override is not None:
                raw_text = text_override
            generic_style = edit_runtime.get_button_style_override(stable)
        except Exception:
            generic_style = None
        text, custom_emoji_id = split_button_custom_emoji(raw_text)
        if source_text is not None:
            kwargs['text'] = text
        if custom_emoji_id and not kwargs.get('icon_custom_emoji_id'):
            kwargs['icon_custom_emoji_id'] = custom_emoji_id
        if generic_style is not None:
            kwargs['style'] = BUTTON_STYLE_VALUES.get(generic_style)
        else:
            has_override, override = get_button_detail_override(detail)
            if has_override:
                kwargs['style'] = override
            elif 'style' not in kwargs:
                kwargs['style'] = resolved_button_style(source_text)
        return keyboard_init(self, *args, **kwargs)

    InlineKeyboardButton.__init__ = patched_inline
    KeyboardButton.__init__ = patched_keyboard
    _BUTTON_STYLE_PATCHED = True


# --- Live edit-mode output patch (v47) -------------------------------------
_OUTPUT_EDIT_PATCHED = False


def install_edit_mode_output_patch() -> None:
    """Make every bot page editable while an admin is in live edit mode.

    Direct ``message.answer`` / ``edit_text`` calls get a stable call-site key, so
    legacy pages that were never added to the old text registry are still editable.
    Known templates use ``text_hint`` and therefore expose their original {variables}.

    Outside edit mode the patch is intentionally invisible, except that saved generic
    overrides are applied at the same call-site. This is what makes an edit affect real
    users instead of only the admin preview.
    """
    global _OUTPUT_EDIT_PATCHED
    if _OUTPUT_EDIT_PATCHED:
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
    from modules import edit_runtime
    import contextvars
    import db

    # Message.answer()/edit_text()/answer_photo() internally call Bot.send_*.
    # Keep one editing boundary per outgoing message so the Bot-level safety net
    # does not register a second target or append duplicate editor controls.
    _nested_message_boundary = contextvars.ContextVar('nested_message_edit_boundary', default=False)

    original_answer = Message.answer
    original_reply = Message.reply
    original_edit_text = Message.edit_text
    original_answer_photo = Message.answer_photo
    original_answer_video = Message.answer_video
    original_answer_document = Message.answer_document
    original_answer_invoice = getattr(Message, 'answer_invoice', None)
    original_edit_caption = Message.edit_caption
    original_edit_reply_markup = Message.edit_reply_markup
    original_callback_answer = CallbackQuery.answer

    # Direct Bot.* sends bypass Message.answer. Patch both boundaries so modules such as
    # card transfer, notifications and QR/subscription delivery cannot leak [emoji IDs].
    original_bot_send_message = Bot.send_message
    original_bot_edit_message_text = Bot.edit_message_text
    original_bot_send_photo = Bot.send_photo
    original_bot_send_video = Bot.send_video
    original_bot_send_document = Bot.send_document
    original_bot_edit_message_caption = Bot.edit_message_caption
    original_bot_edit_message_reply_markup = Bot.edit_message_reply_markup
    original_bot_send_animation = getattr(Bot, 'send_animation', None)
    original_bot_send_audio = getattr(Bot, 'send_audio', None)
    original_bot_send_voice = getattr(Bot, 'send_voice', None)

    def _session_for_chat(chat_id=None):
        session = edit_runtime.current_render_session()
        supplied = chat_id is not None
        try:
            cid = int(chat_id) if supplied else None
        except Exception:
            cid = None
        # A render context belongs to one admin. Never inject edit controls into
        # a different recipient (for example another receipt approver/channel).
        if session is not None and ((not supplied) or (cid is not None and int(session.user_id) == cid)):
            return session
        if cid is not None:
            return edit_runtime.get_session(cid)
        return None

    async def _call_message_original(method, *args, **kwargs):
        token = _nested_message_boundary.set(True)
        try:
            return await method(*args, **kwargs)
        finally:
            _nested_message_boundary.reset(token)

    def _editorize_url_buttons(rows):
        """Make URL buttons selectable while previewing, without changing normal users."""
        converted = []
        for row in rows:
            out_row = []
            for button in row:
                callback_data = str(getattr(button, 'callback_data', '') or '')
                url = str(getattr(button, 'url', '') or '')
                if callback_data or not url:
                    out_row.append(button)
                    continue
                visible = str(getattr(button, 'text', '') or 'لینک')
                emoji_id = getattr(button, 'icon_custom_emoji_id', None)
                raw = (f'[{emoji_id}] {visible}' if emoji_id else visible).strip()
                detail = button_detail_id(raw, None, url)
                stable = edit_runtime.button_stable_key(raw, None, url, detail)
                target = edit_runtime.register_button_target(
                    kind='url',
                    stable_key=stable,
                    display_text=visible,
                    raw_text=raw,
                    detail_id=detail,
                    url=url,
                    current_style=getattr(button, 'style', None),
                )
                kwargs = {
                    'text': raw,
                    'callback_data': f'editmode:url:{target.token}',
                }
                style = getattr(button, 'style', None)
                if style:
                    kwargs['style'] = style
                out_row.append(InlineKeyboardButton(**kwargs))
            converted.append(out_row)
        return converted

    def _reply_button_identity(button):
        """Resolve a ReplyKeyboard button to the same stable setting/action used by real handlers.

        This is deliberately independent from the visible label. It means a button can be renamed
        repeatedly in live edit mode without losing its color, text setting or original action.
        """
        visible = str(getattr(button, 'text', '') or '')
        emoji_id = getattr(button, 'icon_custom_emoji_id', None)
        raw = (f'[{emoji_id}] {visible}' if emoji_id else visible).strip()
        setting_key = None
        detail = button_detail_id(raw, None, None)
        stable = None
        try:
            import keyboards as _kb
            # Customer main-menu buttons already have dedicated setting keys.
            for button_id, (candidate_setting, default_text) in _kb.CUSTOMER_BUTTON_SETTINGS.items():
                current_raw = _kb.customer_button_text(button_id)
                current_visible, _ = split_button_custom_emoji(current_raw)
                default_visible, _ = split_button_custom_emoji(default_text)
                accepted = {current_visible, default_visible}
                aliases = db.get_setting(f'button_text_aliases:{candidate_setting}', [])
                if isinstance(aliases, list):
                    for alias_raw in aliases:
                        alias_visible, _ = split_button_custom_emoji(str(alias_raw))
                        if alias_visible:
                            accepted.add(alias_visible)
                if visible in accepted:
                    detail = _kb.CUSTOMER_BUTTON_DETAIL_IDS.get(button_id)
                    stable = edit_runtime.button_stable_key(default_text, None, None, detail)
                    setting_key = candidate_setting
                    raw = current_raw
                    return stable, setting_key, detail, raw, button_id

            # Other reply buttons (admin menus, back/cancel/confirm/etc.) use their semantic
            # default text as the stable identity. Check every BTN_* constant so renamed labels
            # can still be mapped back to the original action.
            seen = set()
            for name, default_text in vars(_kb).items():
                if not name.startswith('BTN_') or not isinstance(default_text, str) or default_text in seen:
                    continue
                seen.add(default_text)
                candidate_detail = button_detail_id(default_text, None, None)
                candidate_stable = edit_runtime.button_stable_key(default_text, None, None, candidate_detail)
                override = edit_runtime.get_button_text_override(candidate_stable)
                current_raw = str(override if override is not None else default_text)
                current_visible, _ = split_button_custom_emoji(current_raw)
                default_visible, _ = split_button_custom_emoji(default_text)
                accepted = {current_visible, default_visible}
                aliases = db.get_setting(f'edit_button_aliases:{candidate_stable}', [])
                if isinstance(aliases, list):
                    for alias_raw in aliases:
                        alias_visible, _ = split_button_custom_emoji(str(alias_raw))
                        if alias_visible:
                            accepted.add(alias_visible)
                if visible in accepted:
                    return candidate_stable, None, candidate_detail, current_raw, None
        except Exception:
            pass
        if stable is None:
            stable = edit_runtime.button_stable_key(raw, None, None, detail)
        return stable, setting_key, detail, raw, None

    def _reply_to_inline(markup, message):
        """Convert ReplyKeyboard to selectable editor proxies for the preview only.

        Telegram cannot attach ReplyKeyboardMarkup and InlineKeyboardMarkup to the same message.
        In live edit mode we therefore render the exact reply-menu buttons as inline proxies.
        Normal users keep the original ReplyKeyboard unchanged.
        """
        if not isinstance(markup, ReplyKeyboardMarkup):
            return None
        rows = []
        for row in markup.keyboard or []:
            out_row = []
            for button in row:
                stable, setting_key, detail, raw, customer_button_id = _reply_button_identity(button)
                visible, _ = split_button_custom_emoji(raw)
                target = edit_runtime.register_button_target(
                    kind='message_proxy',
                    stable_key=stable,
                    display_text=visible,
                    raw_text=raw,
                    detail_id=detail,
                    setting_key=setting_key,
                    customer_button_id=customer_button_id,
                    event=message,
                    current_style=getattr(button, 'style', None),
                )
                display_raw = raw
                if customer_button_id:
                    try:
                        import keyboards as _kb
                        if not _kb.customer_button_enabled(customer_button_id):
                            display_raw = f'⛔ {raw}'
                    except Exception:
                        pass
                kwargs = {
                    'text': display_raw,
                    'callback_data': f'editmode:reply:{target.token}',
                }
                style = getattr(button, 'style', None)
                if style:
                    kwargs['style'] = style
                out_row.append(InlineKeyboardButton(**kwargs))
            if out_row:
                rows.append(out_row)
        return rows

    def _inline_with_edit(markup, target, message):
        # In edit mode every outgoing page carries its own edit control on THAT message.
        # No separate "editor tools" message is needed.
        if isinstance(markup, InlineKeyboardMarkup):
            rows = _editorize_url_buttons([list(row) for row in markup.inline_keyboard])
        elif isinstance(markup, ReplyKeyboardMarkup):
            rows = _reply_to_inline(markup, message) or []
        elif markup is None:
            rows = []
        else:
            # Unknown/special markup types are left intact to avoid breaking Telegram flows.
            return markup, False
        if not any(
            str(getattr(button, 'callback_data', '') or '').startswith('editmode:text:')
            for row in rows for button in row
        ):
            rows.append([
                InlineKeyboardButton(
                    text='✏️ تغییر متن این بخش',
                    callback_data=f'editmode:text:{target.token}',
                    style='primary',
                )
            ])
        return InlineKeyboardMarkup(inline_keyboard=rows), True

    async def _resolve_text(message: Message, kind: str, outgoing: str) -> tuple[str, Any | None, bool]:
        # Editor-control messages themselves must never recursively acquire
        # another "change this text" button.
        if edit_runtime.is_editor_internal_call() or edit_runtime.output_passthrough_active():
            return str(outgoing or ''), None, False
        hint = edit_runtime.current_text_hint()
        session = _session_for_chat(getattr(getattr(message, 'chat', None), 'id', None))
        if hint:
            setting_key = hint.get('setting_key')
            stable = 'setting:' + str(setting_key or hint.get('content_key') or kind)
            hinted_template = str(hint.get('template') or outgoing or '')
            variables = dict(hint.get('variables') or {})
            # IMPORTANT: always re-read the persisted setting immediately before send.
            # Some callers prepared/rendered their text before the admin changed it, which
            # used to make the previous value leak for one or more messages. The database
            # is now the single source of truth at the final output boundary.
            template = str(db.get_setting(str(setting_key), hinted_template) if setting_key else hinted_template)
            final_text = await render_template_html(message.bot, template, variables)
            target = None
            if session:
                target = edit_runtime.register_text_target(
                    stable_key=stable,
                    template=template,
                    variables=variables,
                    setting_key=str(setting_key) if setting_key else None,
                    content_key=str(hint.get('content_key')) if hint.get('content_key') else None,
                    allow_stickers=bool(hint.get('allow_stickers', True)),
                    label=str(hint.get('label') or 'متن این بخش'),
                )
            return final_text, target, True

        stable, template, variables = edit_runtime.caller_text_target(kind, str(outgoing or ''))
        override = edit_runtime.get_text_override(stable)
        final_text = str(outgoing or '')
        if override is not None:
            # Generic legacy pages can use recovered scalar {variables}. Premium emoji
            # tokens remain supported in these newly editable texts too.
            if CUSTOM_EMOJI_RE.search(str(override)):
                final_text = await render_template_html(message.bot, override, variables)
            else:
                final_text = edit_runtime.render_simple_template(str(override), variables)
        target = None
        if session:
            target = edit_runtime.register_text_target(
                stable_key=stable,
                template=str(override) if override is not None else template,
                variables=variables,
                allow_stickers=True,
                label='متن این بخش',
            )
        return final_text, target, bool(override is not None and CUSTOM_EMOJI_RE.search(str(override)))

    async def _resolve_bot_text(bot: Bot, chat_id, kind: str, outgoing: str) -> tuple[str, Any | None, bool]:
        """Same live-editor path for direct Bot.send_* calls.

        Several payment/receipt/background paths send messages directly through Bot
        instead of Message.answer. Those messages must still be editable when the
        recipient admin is previewing user/admin mode, and saved overrides must affect
        later real users too.
        """
        if edit_runtime.is_editor_internal_call() or edit_runtime.output_passthrough_active() or _nested_message_boundary.get():
            return str(outgoing or ''), None, False
        hint = edit_runtime.current_text_hint()
        session = _session_for_chat(chat_id)
        if hint:
            setting_key = hint.get('setting_key')
            stable = 'setting:' + str(setting_key or hint.get('content_key') or kind)
            hinted_template = str(hint.get('template') or outgoing or '')
            variables = dict(hint.get('variables') or {})
            # Same DB-at-send guarantee for direct Bot.send_* paths.
            template = str(db.get_setting(str(setting_key), hinted_template) if setting_key else hinted_template)
            final_text = await render_template_html(bot, template, variables)
            target = None
            if session:
                target = edit_runtime.register_text_target(
                    stable_key=stable,
                    template=template,
                    variables=variables,
                    setting_key=str(setting_key) if setting_key else None,
                    content_key=str(hint.get('content_key')) if hint.get('content_key') else None,
                    allow_stickers=bool(hint.get('allow_stickers', True)),
                    label=str(hint.get('label') or 'متن این بخش'),
                )
            return final_text, target, True

        stable, template, variables = edit_runtime.caller_text_target(kind, str(outgoing or ''))
        override = edit_runtime.get_text_override(stable)
        final_text = str(outgoing or '')
        if override is not None:
            if CUSTOM_EMOJI_RE.search(str(override)):
                final_text = await render_template_html(bot, override, variables)
            else:
                final_text = edit_runtime.render_simple_template(str(override), variables)
        target = None
        if session:
            target = edit_runtime.register_text_target(
                stable_key=stable,
                template=str(override) if override is not None else template,
                variables=variables,
                allow_stickers=True,
                label='متن این بخش',
            )
        return final_text, target, bool(override is not None and CUSTOM_EMOJI_RE.search(str(override)))

    def _attach_bot_edit_control(kwargs: dict, target) -> None:
        if target is None:
            return
        markup = kwargs.get('reply_markup')
        new_markup, embedded = _inline_with_edit(markup, target, None)
        if embedded:
            kwargs['reply_markup'] = new_markup
            return
        # Most Telegram send methods accept InlineKeyboardMarkup. If an unusual markup
        # cannot be converted, leave it alone rather than breaking the business flow.
        if markup is None:
            kwargs['reply_markup'] = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text='✏️ تغییر متن این بخش',
                    callback_data=f'editmode:text:{target.token}',
                    style='primary',
                )
            ]])

    async def _send_generic_stickers(message: Message, target_stable: str | None) -> None:
        if not target_stable:
            return
        for sticker_id in edit_runtime.get_generic_stickers(target_stable):
            try:
                await message.answer_sticker(sticker=sticker_id)
            except Exception:
                pass

    async def _finalize_text(bot: Bot | None, text: str, kwargs: dict, *, field: str = 'text') -> str:
        # Display-only money grouping. This never touches DB/payment values and only changes
        # explicit currency amounts, so IDs/links/card numbers remain intact.
        text = format_display_prices(text)
        final, mode = await render_custom_emoji_output(bot, text, kwargs.get('parse_mode'))
        if mode:
            kwargs['parse_mode'] = mode
        # Never let a stale explicit entities list conflict with the newly generated HTML.
        if mode and str(mode).lower() == 'html':
            if field == 'caption':
                kwargs.pop('caption_entities', None)
            else:
                kwargs.pop('entities', None)
        return final

    def _finalize_markup(kwargs: dict) -> None:
        if 'reply_markup' in kwargs and kwargs.get('reply_markup') is not None:
            kwargs['reply_markup'] = normalize_custom_emoji_markup(kwargs.get('reply_markup'))

    async def patched_answer(self: Message, text: str, *args, **kwargs):
        final_text, target, force_html = await _resolve_text(self, 'answer', str(text or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        separate_control = False
        if target is not None:
            markup = kwargs.get('reply_markup')
            new_markup, embedded = _inline_with_edit(markup, target, self)
            kwargs['reply_markup'] = new_markup
            separate_control = not embedded
        final_text = await _finalize_text(self.bot, final_text, kwargs, field='text')
        _finalize_markup(kwargs)
        result = await _call_message_original(original_answer, self, final_text, *args, **kwargs)
        if target is not None and separate_control:
            control = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='✏️ تغییر متن این بخش', callback_data=f'editmode:text:{target.token}', style='primary')
            ]])
            await _call_message_original(original_answer, self, '✏️ ابزار ویرایش همین بخش', reply_markup=control)
        # Generic stickers must also appear for normal users after an edit was saved.
        hint = edit_runtime.current_text_hint()
        if hint is None:
            stable, _, _ = edit_runtime.caller_text_target('answer', str(text or ''))
            await _send_generic_stickers(self, stable)
        return result

    async def patched_reply(self: Message, text: str, *args, **kwargs):
        # Keep reply semantics while applying the same final premium-emoji safety net.
        final_text, target, force_html = await _resolve_text(self, 'reply', str(text or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        if target is not None:
            markup = kwargs.get('reply_markup')
            new_markup, _ = _inline_with_edit(markup, target, self)
            kwargs['reply_markup'] = new_markup
        final_text = await _finalize_text(self.bot, final_text, kwargs, field='text')
        _finalize_markup(kwargs)
        return await _call_message_original(original_reply, self, final_text, *args, **kwargs)

    async def patched_edit_text(self: Message, text: str, *args, **kwargs):
        final_text, target, force_html = await _resolve_text(self, 'edit_text', str(text or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        if target is not None:
            markup = kwargs.get('reply_markup')
            new_markup, embedded = _inline_with_edit(markup, target, self)
            kwargs['reply_markup'] = new_markup
            if not embedded:
                # edit_text can only carry InlineKeyboardMarkup. If the original page
                # had no inline keyboard, add a one-row editor keyboard.
                kwargs['reply_markup'] = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text='✏️ تغییر متن این بخش', callback_data=f'editmode:text:{target.token}', style='primary')
                ]])
        final_text = await _finalize_text(self.bot, final_text, kwargs, field='text')
        _finalize_markup(kwargs)
        return await _call_message_original(original_edit_text, self, final_text, *args, **kwargs)

    async def _patched_media(original_method, self: Message, media, args, kwargs, kind: str):
        args = list(args)
        caption = kwargs.get('caption')
        caption_positional = False
        if caption is None and args and isinstance(args[0], str):
            caption = args[0]
            caption_positional = True

        # Resolve even an empty caption. In live edit mode this gives EVERY media message
        # its own edit button and lets the admin add a caption where none existed before.
        final_caption, target, force_html = await _resolve_text(self, kind, str(caption or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        if caption_positional:
            args[0] = final_caption
        elif caption is not None or final_caption:
            kwargs['caption'] = final_caption
        if target is not None:
            markup = kwargs.get('reply_markup')
            new_markup, embedded = _inline_with_edit(markup, target, self)
            if not embedded:
                new_markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text='✏️ تغییر متن این بخش', callback_data=f'editmode:text:{target.token}', style='primary')
                ]])
            kwargs['reply_markup'] = new_markup
        final_caption = await _finalize_text(self.bot, final_caption, kwargs, field='caption')
        if caption_positional:
            args[0] = final_caption
        elif caption is not None or final_caption:
            kwargs['caption'] = final_caption
        _finalize_markup(kwargs)
        return await _call_message_original(original_method, self, media, *args, **kwargs)

    async def patched_answer_photo(self: Message, photo, *args, **kwargs):
        return await _patched_media(original_answer_photo, self, photo, args, kwargs, 'photo_caption')

    async def patched_answer_video(self: Message, video, *args, **kwargs):
        return await _patched_media(original_answer_video, self, video, args, kwargs, 'video_caption')

    async def patched_answer_document(self: Message, document, *args, **kwargs):
        return await _patched_media(original_answer_document, self, document, args, kwargs, 'document_caption')

    async def patched_answer_invoice(self: Message, *args, **kwargs):
        if original_answer_invoice is None:
            raise RuntimeError('answer_invoice is unavailable')
        # Telegram invoice title/description are plain-text fields and cannot carry
        # HTML custom-emoji entities. They still get live-edit targets; premium emoji
        # tokens fall back to their Unicode representation instead of leaking IDs.
        session = None if edit_runtime.is_editor_internal_call() else _session_for_chat(getattr(getattr(self, 'chat', None), 'id', None))
        targets = []
        for field, kind, label in (
            ('title', 'invoice_title', 'عنوان فاکتور'),
            ('description', 'invoice_description', 'توضیح فاکتور'),
        ):
            if field not in kwargs:
                continue
            outgoing = str(kwargs.get(field) or '')
            stable, template, variables = edit_runtime.caller_text_target(kind, outgoing)
            override = edit_runtime.get_text_override(stable)
            final_value = edit_runtime.render_simple_template(str(override), variables) if override is not None else outgoing
            final_value = await render_custom_emoji_plain(self.bot, final_value)
            kwargs[field] = final_value
            if session:
                targets.append(edit_runtime.register_text_target(
                    stable_key=stable,
                    template=str(override) if override is not None else template,
                    variables=variables,
                    allow_stickers=False,
                    label=label,
                ))
        if targets:
            markup = kwargs.get('reply_markup')
            rows = []
            if isinstance(markup, InlineKeyboardMarkup):
                rows = [list(row) for row in markup.inline_keyboard]
            elif markup is None:
                # Bot API requires the first custom invoice button to be a Pay button.
                rows = [[InlineKeyboardButton(text='💳 پرداخت', pay=True)]]
            for target in targets:
                rows.append([InlineKeyboardButton(
                    text=f'✏️ تغییر {target.label}',
                    callback_data=f'editmode:text:{target.token}',
                    style='primary',
                )])
            kwargs['reply_markup'] = InlineKeyboardMarkup(inline_keyboard=rows)
            _finalize_markup(kwargs)
        return await _call_message_original(original_answer_invoice, self, *args, **kwargs)

    async def patched_edit_caption(self: Message, *args, **kwargs):
        args = list(args)
        caption = kwargs.get('caption')
        positional = False
        if caption is None and args and isinstance(args[0], str):
            caption = args[0]
            positional = True
        target = None
        if caption is not None:
            final_caption, target, force_html = await _resolve_text(self, 'edit_caption', str(caption or ''))
            if force_html:
                kwargs['parse_mode'] = 'HTML'
            if positional:
                args[0] = final_caption
            else:
                kwargs['caption'] = final_caption
        if target is not None:
            markup = kwargs.get('reply_markup')
            new_markup, embedded = _inline_with_edit(markup, target, self)
            if not embedded:
                new_markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text='✏️ تغییر متن این بخش', callback_data=f'editmode:text:{target.token}', style='primary')
                ]])
            kwargs['reply_markup'] = new_markup
        if caption is not None:
            final_caption = args[0] if positional else kwargs.get('caption', '')
            final_caption = await _finalize_text(self.bot, str(final_caption or ''), kwargs, field='caption')
            if positional:
                args[0] = final_caption
            else:
                kwargs['caption'] = final_caption
        _finalize_markup(kwargs)
        return await _call_message_original(original_edit_caption, self, *args, **kwargs)

    async def patched_callback_answer(self: CallbackQuery, text: str | None = None, *args, **kwargs):
        if text is None or edit_runtime.is_editor_internal_call():
            if text is not None:
                text = await render_custom_emoji_plain(getattr(self, 'bot', None), text)
            return await original_callback_answer(self, text, *args, **kwargs)

        # Callback toasts/alerts cannot physically contain an inline keyboard. In edit
        # mode we therefore keep the real Telegram alert AND send one mirror message
        # with «change this text». The saved override is then applied to the real alert
        # for normal users too.
        stable, template, variables = edit_runtime.caller_text_target('callback_answer', str(text or ''))
        override = edit_runtime.get_text_override(stable)
        final_text = edit_runtime.render_simple_template(str(override), variables) if override is not None else str(text or '')
        final_text = await render_custom_emoji_plain(getattr(self, 'bot', None), final_text)
        result = await original_callback_answer(self, final_text, *args, **kwargs)

        chat_id = getattr(getattr(self, 'message', None), 'chat', None)
        chat_id = getattr(chat_id, 'id', None)
        session = _session_for_chat(chat_id)
        if session and chat_id is not None:
            target = edit_runtime.register_text_target(
                stable_key=stable,
                template=str(override) if override is not None else template,
                variables=variables,
                allow_stickers=False,
                label='متن اعلان/هشدار این بخش',
            )
            mirror_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text='✏️ تغییر این متن',
                    callback_data=f'editmode:text:{target.token}',
                    style='primary',
                )
            ]])
            try:
                await original_bot_send_message(self.bot, chat_id, final_text or ' ', reply_markup=mirror_markup)
            except Exception:
                pass
        return result

    async def patched_edit_reply_markup(self: Message, *args, **kwargs):
        args = list(args)
        if args and kwargs.get('reply_markup') is None:
            # aiogram currently exposes reply_markup as a keyword argument, but keep a
            # defensive positional path so the safety net survives minor API changes.
            candidate = args[0]
            fixed = normalize_custom_emoji_markup(candidate)
            if fixed is not candidate:
                args[0] = fixed
        _finalize_markup(kwargs)
        return await _call_message_original(original_edit_reply_markup, self, *args, **kwargs)

    async def patched_bot_send_message(self: Bot, chat_id, text, *args, **kwargs):
        text, target, force_html = await _resolve_bot_text(self, chat_id, 'bot_send_message', str(text or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        _attach_bot_edit_control(kwargs, target)
        text = await _finalize_text(self, str(text or ''), kwargs, field='text')
        _finalize_markup(kwargs)
        return await original_bot_send_message(self, chat_id, text, *args, **kwargs)

    async def patched_bot_edit_message_text(self: Bot, text, *args, **kwargs):
        chat_id = kwargs.get('chat_id')
        if chat_id is None and args:
            chat_id = args[0]
        text, target, force_html = await _resolve_bot_text(self, chat_id, 'bot_edit_message_text', str(text or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        _attach_bot_edit_control(kwargs, target)
        text = await _finalize_text(self, str(text or ''), kwargs, field='text')
        _finalize_markup(kwargs)
        return await original_bot_edit_message_text(self, text, *args, **kwargs)

    async def _patched_bot_media(original_method, self: Bot, chat_id, media, args, kwargs, kind: str = 'bot_media_caption'):
        caption = kwargs.get('caption')
        final_caption, target, force_html = await _resolve_bot_text(self, chat_id, kind, str(caption or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        _attach_bot_edit_control(kwargs, target)
        # Even a media message that originally had no caption becomes editable: after
        # the admin saves text, the same call-site will receive that caption for users.
        if caption is not None or final_caption:
            kwargs['caption'] = await _finalize_text(self, str(final_caption or ''), kwargs, field='caption')
        _finalize_markup(kwargs)
        return await original_method(self, chat_id, media, *args, **kwargs)

    async def patched_bot_send_photo(self: Bot, chat_id, photo, *args, **kwargs):
        return await _patched_bot_media(original_bot_send_photo, self, chat_id, photo, args, kwargs, 'bot_photo_caption')

    async def patched_bot_send_video(self: Bot, chat_id, video, *args, **kwargs):
        return await _patched_bot_media(original_bot_send_video, self, chat_id, video, args, kwargs, 'bot_video_caption')

    async def patched_bot_send_document(self: Bot, chat_id, document, *args, **kwargs):
        return await _patched_bot_media(original_bot_send_document, self, chat_id, document, args, kwargs, 'bot_document_caption')

    async def patched_bot_edit_message_caption(self: Bot, *args, **kwargs):
        chat_id = kwargs.get('chat_id')
        if chat_id is None and args:
            chat_id = args[0]
        caption = kwargs.get('caption')
        final_caption, target, force_html = await _resolve_bot_text(self, chat_id, 'bot_edit_caption', str(caption or ''))
        if force_html:
            kwargs['parse_mode'] = 'HTML'
        _attach_bot_edit_control(kwargs, target)
        if caption is not None or final_caption:
            kwargs['caption'] = await _finalize_text(self, str(final_caption or ''), kwargs, field='caption')
        _finalize_markup(kwargs)
        return await original_bot_edit_message_caption(self, *args, **kwargs)

    async def patched_bot_edit_message_reply_markup(self: Bot, *args, **kwargs):
        _finalize_markup(kwargs)
        return await original_bot_edit_message_reply_markup(self, *args, **kwargs)

    def _make_optional_bot_media_patch(original_method, kind: str):
        async def wrapper(self: Bot, chat_id, media, *args, **kwargs):
            return await _patched_bot_media(original_method, self, chat_id, media, args, kwargs, kind)
        return wrapper

    Message.answer = patched_answer
    Message.reply = patched_reply
    Message.edit_text = patched_edit_text
    Message.answer_photo = patched_answer_photo
    Message.answer_video = patched_answer_video
    Message.answer_document = patched_answer_document
    if original_answer_invoice is not None:
        Message.answer_invoice = patched_answer_invoice
    Message.edit_caption = patched_edit_caption
    Message.edit_reply_markup = patched_edit_reply_markup
    CallbackQuery.answer = patched_callback_answer

    Bot.send_message = patched_bot_send_message
    Bot.edit_message_text = patched_bot_edit_message_text
    Bot.send_photo = patched_bot_send_photo
    Bot.send_video = patched_bot_send_video
    Bot.send_document = patched_bot_send_document
    Bot.edit_message_caption = patched_bot_edit_message_caption
    Bot.edit_message_reply_markup = patched_bot_edit_message_reply_markup
    if original_bot_send_animation is not None:
        Bot.send_animation = _make_optional_bot_media_patch(original_bot_send_animation, 'bot_animation_caption')
    if original_bot_send_audio is not None:
        Bot.send_audio = _make_optional_bot_media_patch(original_bot_send_audio, 'bot_audio_caption')
    if original_bot_send_voice is not None:
        Bot.send_voice = _make_optional_bot_media_patch(original_bot_send_voice, 'bot_voice_caption')
    _OUTPUT_EDIT_PATCHED = True
