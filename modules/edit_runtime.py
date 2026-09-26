from __future__ import annotations

import contextlib
import contextvars
import hashlib
import inspect
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

import db


@dataclass
class EditSession:
    user_id: int
    scope: str


@dataclass
class ButtonTarget:
    token: str
    kind: str  # message | message_proxy | callback | url
    stable_key: str
    display_text: str
    raw_text: str
    detail_id: str | None = None
    callback_data: str | None = None
    url: str | None = None
    setting_key: str | None = None
    # Set ONLY for the eight buttons in the first customer menu. This is the guard
    # that prevents disable/reorder controls from leaking into any inner/admin button.
    customer_button_id: str | None = None
    event: Any = None
    original_message: Any = None
    current_style: str | None = None


@dataclass
class TextTarget:
    token: str
    stable_key: str
    template: str
    variables: dict[str, Any] = field(default_factory=dict)
    setting_key: str | None = None
    content_key: str | None = None
    allow_stickers: bool = True
    label: str = 'متن این بخش'


EDIT_SESSIONS: dict[int, EditSession] = {}
BUTTON_TARGETS: dict[str, ButtonTarget] = {}
TEXT_TARGETS: dict[str, TextTarget] = {}
INPUT_WAITERS: dict[int, dict[str, Any]] = {}
BYPASS_MESSAGES: set[tuple[int, int]] = set()
BYPASS_CALLBACKS: set[str] = set()
_DISPATCHER: Any = None

# Active only while the real business handler is being executed on behalf of edit mode.
_RENDER_SESSION: contextvars.ContextVar[EditSession | None] = contextvars.ContextVar('edit_render_session', default=None)
_TEXT_HINT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar('edit_text_hint', default=None)
_OUTPUT_PASSTHROUGH: contextvars.ContextVar[bool] = contextvars.ContextVar('edit_output_passthrough', default=False)
_PATCHED = False


def set_dispatcher(dispatcher: Any) -> None:
    global _DISPATCHER
    _DISPATCHER = dispatcher


def get_dispatcher() -> Any:
    return _DISPATCHER


def start_session(user_id: int, scope: str) -> EditSession:
    session = EditSession(int(user_id), str(scope))
    EDIT_SESSIONS[int(user_id)] = session
    INPUT_WAITERS.pop(int(user_id), None)
    return session


def stop_session(user_id: int) -> None:
    EDIT_SESSIONS.pop(int(user_id), None)
    INPUT_WAITERS.pop(int(user_id), None)


def get_session(user_id: int | None) -> EditSession | None:
    if user_id is None:
        return None
    return EDIT_SESSIONS.get(int(user_id))


def is_active(user_id: int | None, scope: str | None = None) -> bool:
    session = get_session(user_id)
    if not session:
        return False
    return scope is None or session.scope == scope


def current_render_session() -> EditSession | None:
    return _RENDER_SESSION.get()


def output_passthrough_active() -> bool:
    return bool(_OUTPUT_PASSTHROUGH.get())


@contextlib.contextmanager
def output_passthrough():
    """Bypass generic live-edit rewriting for already-finalized atomic outputs.

    Used by subscription QR delivery: its caption is explicitly rendered and must stay
    attached to the same photo. Global output hooks must not replace it with a stale
    generic call-site override.
    """
    token = _OUTPUT_PASSTHROUGH.set(True)
    try:
        yield
    finally:
        _OUTPUT_PASSTHROUGH.reset(token)


@contextlib.contextmanager
def editor_render(session: EditSession):
    token = _RENDER_SESSION.set(session)
    try:
        yield
    finally:
        _RENDER_SESSION.reset(token)


@contextlib.contextmanager
def text_hint(*, setting_key: str | None, template: str, variables: dict[str, Any] | None = None,
              content_key: str | None = None, label: str = 'متن این بخش', allow_stickers: bool = True):
    payload = {
        'setting_key': setting_key,
        'template': str(template or ''),
        'variables': dict(variables or {}),
        'content_key': content_key,
        'label': label,
        'allow_stickers': bool(allow_stickers),
    }
    token = _TEXT_HINT.set(payload)
    try:
        yield
    finally:
        _TEXT_HINT.reset(token)


def current_text_hint() -> dict[str, Any] | None:
    return _TEXT_HINT.get()


def _token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(6).replace('-', '').replace('_', '')[:9]


def register_button_target(**kwargs: Any) -> ButtonTarget:
    token = _token('b')
    target = ButtonTarget(token=token, **kwargs)
    BUTTON_TARGETS[token] = target
    # Avoid unbounded growth during long polling.
    if len(BUTTON_TARGETS) > 1500:
        for key in list(BUTTON_TARGETS)[:500]:
            BUTTON_TARGETS.pop(key, None)
    return target


def register_text_target(*, stable_key: str, template: str, variables: dict[str, Any] | None = None,
                         setting_key: str | None = None, content_key: str | None = None,
                         allow_stickers: bool = True, label: str = 'متن این بخش') -> TextTarget:
    token = _token('t')
    target = TextTarget(
        token=token,
        stable_key=stable_key,
        template=str(template or ''),
        variables=dict(variables or {}),
        setting_key=setting_key,
        content_key=content_key,
        allow_stickers=allow_stickers,
        label=label,
    )
    TEXT_TARGETS[token] = target
    if len(TEXT_TARGETS) > 1500:
        for key in list(TEXT_TARGETS)[:500]:
            TEXT_TARGETS.pop(key, None)
    return target


def normalize_callback_key(callback_data: str | None) -> str:
    value = str(callback_data or '')
    # Exact callback data is intentionally retained. Dynamic rows (package/service/tutorial)
    # can therefore be edited independently instead of one override corrupting every row.
    return value


def button_stable_key(text: str | None, callback_data: str | None = None, url: str | None = None,
                      detail_id: str | None = None) -> str:
    if callback_data:
        source = 'cb|' + normalize_callback_key(callback_data)
    elif url:
        source = 'url|' + str(url)
    elif detail_id:
        source = 'detail|' + str(detail_id)
    else:
        source = 'reply|' + str(text or '')
    return hashlib.sha1(source.encode('utf-8')).hexdigest()[:24]


def _button_text_key(stable_key: str) -> str:
    return f'edit_button_text:{stable_key}'


def _button_style_key(stable_key: str) -> str:
    return f'edit_button_style:{stable_key}'


def get_button_text_override(stable_key: str) -> str | None:
    value = db.get_setting(_button_text_key(stable_key), None)
    if value is None:
        return None
    return str(value)


def set_button_text_override(stable_key: str, value: str) -> None:
    db.set_setting(_button_text_key(stable_key), str(value))


def get_button_style_override(stable_key: str) -> str | None:
    value = db.get_setting(_button_style_key(stable_key), None)
    if value in {'default', 'primary', 'success', 'danger'}:
        return str(value)
    return None


def set_button_style_override(stable_key: str, value: str) -> None:
    if value not in {'default', 'primary', 'success', 'danger'}:
        raise ValueError('invalid Telegram button style')
    db.set_setting(_button_style_key(stable_key), value)


def _text_override_key(stable_key: str) -> str:
    return f'edit_text_override:{stable_key}'


def get_text_override(stable_key: str) -> str | None:
    value = db.get_setting(_text_override_key(stable_key), None)
    if value is None:
        return None
    return str(value)


def set_text_override(stable_key: str, value: str) -> None:
    db.set_setting(_text_override_key(stable_key), str(value))


def get_generic_stickers(stable_key: str) -> list[str]:
    value = db.get_setting(f'edit_text_stickers:{stable_key}', [])
    return [str(x) for x in value] if isinstance(value, list) else []


def set_generic_stickers(stable_key: str, values: list[str]) -> None:
    db.set_setting(f'edit_text_stickers:{stable_key}', [str(x) for x in values if str(x).strip()])


def render_simple_template(template: str, variables: dict[str, Any] | None = None) -> str:
    result = str(template or '')
    for key, value in (variables or {}).items():
        result = result.replace('{' + str(key) + '}', str(value if value is not None else ''))
    return result


def caller_text_target(kind: str, current_text: str) -> tuple[str, str, dict[str, Any]]:
    """Create a stable per-call-site editable text target for direct message.answer calls.

    Known templates use text_hint() and keep their exact variables. For legacy/direct strings,
    this fallback still makes the section editable and exposes safe scalar locals as variables.
    """
    frame = inspect.currentframe()
    chosen = None
    try:
        while frame:
            frame = frame.f_back
            if not frame:
                break
            filename = str(frame.f_code.co_filename).replace('\\', '/')
            if '/admin_bot_modular/' not in filename:
                continue
            if filename.endswith('/modules/edit_runtime.py') or filename.endswith('/modules/edit_mode.py') or filename.endswith('/modules/qr_tools.py') or filename.endswith('/utils.py'):
                continue
            if frame.f_code.co_name.startswith('patched_'):
                continue
            chosen = frame
            break
        if not chosen:
            source = f'unknown:{kind}:{current_text[:80]}'
            return hashlib.sha1(source.encode()).hexdigest()[:24], current_text, {}
        filename = str(chosen.f_code.co_filename).replace('\\', '/')
        rel = filename.split('/admin_bot_modular/', 1)[-1]
        try:
            import linecache
            start = max(1, int(chosen.f_lineno) - 2)
            snippet = ' '.join(
                linecache.getline(filename, n).strip()
                for n in range(start, int(chosen.f_lineno) + 3)
            )
            snippet = re.sub(r'\s+', ' ', snippet).strip()
        except Exception:
            snippet = ''
        # Source-context based key survives simple line shifts in future updates.
        stable_src = f'{rel}:{chosen.f_code.co_name}:{kind}:{snippet or chosen.f_lineno}'
        stable = hashlib.sha1(stable_src.encode('utf-8')).hexdigest()[:24]
        vars_map: dict[str, Any] = {}
        for key, value in chosen.f_locals.items():
            if key.startswith('_') or key in {'message', 'callback', 'state', 'bot', 'self'}:
                continue
            if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 300:
                vars_map[key] = value
                continue
            # Expose useful scalar values nested inside dicts too. A lot of customer
            # flows keep package/order/service values in a dict, so without this the
            # live editor showed a rendered value but no reusable {variable} for it.
            if isinstance(value, dict):
                for subkey, subvalue in value.items():
                    if str(subkey).startswith('_'):
                        continue
                    if not isinstance(subvalue, (str, int, float, bool)):
                        continue
                    if len(str(subvalue)) > 300:
                        continue
                    subname = re.sub(r'[^A-Za-z0-9_]+', '_', str(subkey)).strip('_')
                    if not subname:
                        continue
                    # Natural short name when it does not collide (e.g. {package_name}),
                    # plus a namespaced alias (e.g. {final_package_name}).
                    vars_map.setdefault(subname, subvalue)
                    vars_map[f'{key}_{subname}'] = subvalue
        template = str(current_text or '')
        # Best-effort recovery of variables for legacy f-strings/direct answers.
        # Exact known templates use text_hint() and do not rely on this heuristic.
        candidates = sorted(vars_map.items(), key=lambda item: len(str(item[1])), reverse=True)
        for key, value in candidates:
            rendered = str(value)
            if not rendered or rendered in {'True', 'False', 'None'}:
                continue
            if len(rendered) < 3 and not (rendered.isdigit() and len(rendered) >= 2):
                continue
            if rendered in template:
                template = template.replace(rendered, '{' + str(key) + '}')
        return stable, template, vars_map
    finally:
        del frame
        if chosen is not None:
            del chosen


def consume_message_bypass(user_id: int, message_id: int) -> bool:
    key = (int(user_id), int(message_id))
    if key in BYPASS_MESSAGES:
        BYPASS_MESSAGES.discard(key)
        return True
    return False


def consume_callback_bypass(callback_id: str) -> bool:
    if str(callback_id) in BYPASS_CALLBACKS:
        BYPASS_CALLBACKS.discard(str(callback_id))
        return True
    return False


def is_editor_internal_call() -> bool:
    """True when an outgoing message originates from edit_mode.py itself."""
    frame = inspect.currentframe()
    try:
        while frame:
            frame = frame.f_back
            if not frame:
                break
            filename = str(frame.f_code.co_filename).replace('\\', '/')
            if '/admin_bot_modular/' not in filename:
                continue
            if filename.endswith('/modules/edit_runtime.py') or filename.endswith('/utils.py'):
                continue
            return filename.endswith('/modules/edit_mode.py')
        return False
    finally:
        del frame


try:
    from aiogram import BaseMiddleware
except Exception:  # pragma: no cover - only for static tooling without aiogram installed
    BaseMiddleware = object  # type: ignore


class EditModeRenderMiddleware(BaseMiddleware):
    """Keep edit rendering active through FSM text-input steps too.

    The initial button action is re-fed by edit_mode, but subsequent username/coupon/
    amount/etc. messages arrive as normal updates. This middleware ensures every page
    produced by those real handlers still receives live edit controls until /exit.
    """

    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        uid = getattr(user, 'id', None) if user else None
        session = get_session(uid)
        if not session or current_render_session() is not None:
            return await handler(event, data)
        with editor_render(session):
            return await handler(event, data)
