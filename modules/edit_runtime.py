from __future__ import annotations

import contextlib
import contextvars
import hashlib
import inspect
import re
import secrets
import ast
import os
from pathlib import Path
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

# Stable live-edit IDs must not depend on where the project is installed or on
# surrounding source lines.  The old implementation looked for a literal
# ``/admin_bot_modular/`` path and hashed a five-line snippet; both properties
# made saved custom texts appear to disappear after moving/updating the bot.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TEXT_LEGACY_KEYS: dict[str, list[str]] = {}
_CALLSITE_SOURCE_CACHE: dict[str, dict[str, Any]] = {}


def _project_relative_source(filename: str) -> str | None:
    try:
        path = Path(filename).resolve()
        return path.relative_to(_PROJECT_ROOT).as_posix()
    except Exception:
        return None


def _redacted_ast(node: ast.AST) -> str:
    """Return a source-shape fingerprint while ignoring user-visible literals.

    Line numbers and string/number values are deliberately excluded, so inserting
    unrelated lines or tweaking the default wording does not change the stable ID.
    Names/call structure stay in the fingerprint to distinguish business paths.
    """
    class Redactor(ast.NodeTransformer):
        def visit_Constant(self, n):  # noqa: N802
            if isinstance(n.value, str):
                return ast.copy_location(ast.Constant(value='<str>'), n)
            if isinstance(n.value, (int, float, complex)) and not isinstance(n.value, bool):
                return ast.copy_location(ast.Constant(value=0), n)
            return n
    clone = Redactor().visit(ast.fix_missing_locations(ast.parse(ast.unparse(node))))
    return ast.dump(clone, annotate_fields=True, include_attributes=False)


def _callsite_ast_fingerprint(filename: str, lineno: int, func_name: str, kind: str) -> str:
    """Build a stable fingerprint while parsing each source file only once.

    v0.2.1 reparsed the whole file for every discovered call-site. On a project
    with 1,400+ calls that made startup migrations unnecessarily slow. This cache
    keeps the exact same stable-ID algorithm while reusing the parsed AST and
    precomputed call shapes/ordinals for the lifetime of the process.
    """
    rel = _project_relative_source(filename) or Path(filename).name
    cache_key = str(Path(filename).resolve())
    try:
        cached = _CALLSITE_SOURCE_CACHE.get(cache_key)
        if cached is None:
            source = Path(filename).read_text(encoding='utf-8')
            tree = ast.parse(source)
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            all_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
            shapes = {id(n): _redacted_ast(n) for n in all_calls}
            cached = {
                'tree': tree,
                'functions': functions,
                'all_calls': all_calls,
                'shapes': shapes,
                'scopes': {},
                'resolved': {},
            }
            _CALLSITE_SOURCE_CACHE[cache_key] = cached

        resolved_key = (int(lineno), str(func_name))
        resolved = cached['resolved'].get(resolved_key)
        if resolved is None:
            functions = [
                n for n in cached['functions']
                if n.name == func_name
                and getattr(n, 'lineno', 0) <= int(lineno) <= getattr(n, 'end_lineno', 10**9)
            ]
            func_node = min(
                functions,
                key=lambda n: getattr(n, 'end_lineno', lineno) - getattr(n, 'lineno', lineno),
            ) if functions else None
            scope = func_node or cached['tree']
            scope_id = id(scope)
            scope_data = cached['scopes'].get(scope_id)
            if scope_data is None:
                calls = [n for n in ast.walk(scope) if isinstance(n, ast.Call)]
                by_shape: dict[str, list[ast.Call]] = {}
                for node in calls:
                    shape = cached['shapes'].get(id(node))
                    if shape is None:
                        shape = _redacted_ast(node)
                        cached['shapes'][id(node)] = shape
                    by_shape.setdefault(shape, []).append(node)
                slots: dict[int, int] = {}
                for nodes in by_shape.values():
                    nodes.sort(key=lambda n: (getattr(n, 'lineno', 0), getattr(n, 'col_offset', 0)))
                    for idx, node in enumerate(nodes):
                        slots[id(node)] = idx
                scope_data = {'calls': calls, 'slots': slots}
                cached['scopes'][scope_id] = scope_data

            calls = [
                n for n in scope_data['calls']
                if getattr(n, 'lineno', 0) <= int(lineno) <= getattr(n, 'end_lineno', getattr(n, 'lineno', 0))
            ]
            if calls:
                target = min(
                    calls,
                    key=lambda n: (
                        getattr(n, 'end_lineno', lineno) - getattr(n, 'lineno', lineno),
                        len(ast.dump(n)),
                    ),
                )
                shape = cached['shapes'][id(target)]
                slot = scope_data['slots'].get(id(target), 0)
                resolved = (shape, slot)
            else:
                resolved = None
            cached['resolved'][resolved_key] = resolved

        if resolved is not None:
            shape, slot = resolved
            return f'{rel}:{func_name}:{kind}:{shape}:slot={slot}'
        return f'{rel}:{func_name}:{kind}:no-call'
    except Exception:
        return f'{rel}:{func_name}:{kind}:parse-fallback'


def _remember_legacy_text_key(stable_key: str, *legacy_keys: str) -> None:
    keys = _TEXT_LEGACY_KEYS.setdefault(str(stable_key), [])
    for key in legacy_keys:
        key = str(key or '').strip()
        if key and key != stable_key and key not in keys:
            keys.append(key)


def _copy_legacy_setting(prefix: str, stable_key: str):
    """Read-through migration: copy old value to stable v2 key without deleting it."""
    new_key = f'{prefix}:{stable_key}'
    value = db.get_setting(new_key, None)
    if value is not None:
        return value
    for legacy in _TEXT_LEGACY_KEYS.get(str(stable_key), []):
        old_key = f'{prefix}:{legacy}'
        old_value = db.get_setting(old_key, None)
        if old_value is None:
            continue
        # Non-destructive migration: keep the old key as a permanent fallback/audit trail.
        db.set_setting(new_key, old_value)
        db.set_setting(f'edit_text_migrated_from:{stable_key}', legacy)
        return old_value
    return None


def _function_name_for_line(tree: ast.AST, lineno: int) -> str:
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and getattr(node, 'lineno', 0) <= int(lineno) <= getattr(node, 'end_lineno', 10**9)
    ]
    if not matches:
        return '<module>'
    return min(matches, key=lambda n: getattr(n, 'end_lineno', lineno) - getattr(n, 'lineno', lineno)).name


def _legacy_key_for_line(filename: str, lineno: int, func_name: str, kind: str) -> str:
    rel = _project_relative_source(filename) or Path(filename).name
    try:
        import linecache
        start = max(1, int(lineno) - 2)
        snippet = ' '.join(linecache.getline(filename, n).strip() for n in range(start, int(lineno) + 3))
        snippet = re.sub(r'\s+', ' ', snippet).strip()
    except Exception:
        snippet = ''
    source = f'{rel}:{func_name}:{kind}:{snippet or lineno}'
    return hashlib.sha1(source.encode('utf-8')).hexdigest()[:24]


def _possible_kinds_for_call(node: ast.Call) -> list[str]:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return []
    attr = str(func.attr)
    mapping = {
        'reply': ['reply'],
        'edit_text': ['edit_text'],
        'answer_photo': ['photo_caption'],
        'answer_video': ['video_caption'],
        'answer_document': ['document_caption'],
        'answer_invoice': ['invoice_title', 'invoice_description'],
        'send_message': ['bot_send_message'],
        'edit_message_text': ['bot_edit_message_text'],
        'send_photo': ['bot_photo_caption'],
        'send_video': ['bot_video_caption'],
        'send_document': ['bot_document_caption'],
        'edit_message_caption': ['bot_edit_caption'],
    }
    if attr == 'answer':
        # ``message.answer`` and ``callback.answer`` share the method name. Trying
        # both is safe because migration only happens if that exact old key exists.
        return ['answer', 'callback_answer']
    return mapping.get(attr, [])


def migrate_all_legacy_live_edit_settings() -> dict[str, int]:
    """Migrate legacy live-edit keys to stable v2 IDs exactly once.

    Safety/performance guarantees:
    - Legacy keys are never deleted.
    - All relevant settings are loaded in one SQLite read instead of thousands of
      individual get_setting() calls.
    - New stable keys and the completion marker are committed in one transaction.
    - The completion marker is written only after a successful full scan, so a
      crash/interruption causes the migration to retry safely on the next start.
    - Once completed, subsequent bot restarts return immediately.
    """
    marker_key = 'stable_live_edit_migration_v2_done'
    if db.get_setting(marker_key, False):
        return {'scanned': 0, 'texts': 0, 'stickers': 0, 'skipped': 1}

    migrated_text = 0
    migrated_stickers = 0
    scanned = 0

    rows = db.fetchall(
        "SELECT key, value FROM settings "
        "WHERE key LIKE 'edit_text_override:%' "
        "OR key LIKE 'edit_text_stickers:%' "
        "OR key LIKE 'edit_text_migrated_from:%'"
    )
    settings_map: dict[str, Any] = {}
    import json as _json
    for row in rows:
        raw = row.get('value')
        try:
            settings_map[str(row.get('key'))] = _json.loads(raw)
        except Exception:
            settings_map[str(row.get('key'))] = raw

    pending: dict[str, Any] = {}
    roots = [_PROJECT_ROOT / 'modules', _PROJECT_ROOT]
    files: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root == _PROJECT_ROOT:
            files.update(p for p in root.glob('*.py') if p.is_file())
        else:
            files.update(p for p in root.rglob('*.py') if p.is_file() and '__pycache__' not in p.parts)

    for path in sorted(files):
        rel = _project_relative_source(str(path))
        if rel in {'modules/edit_runtime.py', 'modules/edit_mode.py', 'utils.py'}:
            continue
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kinds = _possible_kinds_for_call(node)
            if not kinds:
                continue
            lineno = int(getattr(node, 'lineno', 0) or 0)
            if lineno <= 0:
                continue
            func_name = _function_name_for_line(tree, lineno)
            for kind in kinds:
                scanned += 1
                semantic = _callsite_ast_fingerprint(str(path), lineno, func_name, kind)
                stable = 'v2_' + hashlib.sha1(semantic.encode('utf-8')).hexdigest()[:24]
                legacy = _legacy_key_for_line(str(path), lineno, func_name, kind)
                _remember_legacy_text_key(stable, legacy)

                new_text_key = f'edit_text_override:{stable}'
                old_text_key = f'edit_text_override:{legacy}'
                if new_text_key not in settings_map and new_text_key not in pending and old_text_key in settings_map:
                    pending[new_text_key] = settings_map[old_text_key]
                    pending[f'edit_text_migrated_from:{stable}'] = legacy
                    migrated_text += 1

                new_sticker_key = f'edit_text_stickers:{stable}'
                old_sticker_key = f'edit_text_stickers:{legacy}'
                if new_sticker_key not in settings_map and new_sticker_key not in pending and old_sticker_key in settings_map:
                    pending[new_sticker_key] = settings_map[old_sticker_key]
                    migrated_stickers += 1

    import datetime as _datetime
    timestamp = _datetime.datetime.utcnow().isoformat(timespec='seconds')
    marker_value = {
        'completed': True,
        'scanned': scanned,
        'texts': migrated_text,
        'stickers': migrated_stickers,
        'version': 2,
        'completed_at': timestamp,
    }
    with db.connect() as conn:
        for key, value in pending.items():
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, _json.dumps(value, ensure_ascii=False), timestamp),
            )
        conn.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (marker_key, _json.dumps(marker_value, ensure_ascii=False), timestamp),
        )
        conn.commit()

    db.clear_settings_cache()
    return {'scanned': scanned, 'texts': migrated_text, 'stickers': migrated_stickers, 'skipped': 0}

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
    value = _copy_legacy_setting('edit_text_override', stable_key)
    if value is None:
        return None
    return str(value)


def set_text_override(stable_key: str, value: str) -> None:
    db.set_setting(_text_override_key(stable_key), str(value))


def get_generic_stickers(stable_key: str) -> list[str]:
    value = _copy_legacy_setting('edit_text_stickers', stable_key)
    if value is None:
        value = []
    return [str(x) for x in value] if isinstance(value, list) else []


def set_generic_stickers(stable_key: str, values: list[str]) -> None:
    db.set_setting(f'edit_text_stickers:{stable_key}', [str(x) for x in values if str(x).strip()])


def render_simple_template(template: str, variables: dict[str, Any] | None = None) -> str:
    result = str(template or '')
    ctx = dict(variables or {})
    alias_groups = (
        ('order_id', 'entity_id'),
        ('service_name', 'service_username', 'customer_username', 'panel_username'),
        ('status', 'final_status'),
        ('sub_link', 'subscription_url'),
        ('ref_link', 'referral_link'),
    )
    for group in alias_groups:
        value = next((ctx.get(k) for k in group if ctx.get(k) not in (None, '')), None)
        if value is not None:
            for key in group:
                ctx.setdefault(key, value)
    for key, value in ctx.items():
        result = result.replace('{' + str(key) + '}', str(value if value is not None else ''))
    # Live-edit overrides must never expose unresolved internal template tokens.
    return re.sub(r'\{[A-Za-z_][A-Za-z0-9_]*\}', '-', result)


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
            rel_candidate = _project_relative_source(filename)
            if rel_candidate is None:
                continue
            if rel_candidate in {'modules/edit_runtime.py', 'modules/edit_mode.py', 'modules/qr_tools.py', 'utils.py'}:
                continue
            if frame.f_code.co_name.startswith('patched_'):
                continue
            chosen = frame
            break
        if not chosen:
            source = f'unknown:{kind}:{current_text[:80]}'
            return hashlib.sha1(source.encode()).hexdigest()[:24], current_text, {}
        filename = str(chosen.f_code.co_filename).replace('\\', '/')
        rel = _project_relative_source(filename) or Path(filename).name
        # Legacy v1 key: keep computing it exactly as before so existing custom
        # texts/stickers are copied forward the first time this page is rendered.
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
        legacy_src = f'{rel}:{chosen.f_code.co_name}:{kind}:{snippet or chosen.f_lineno}'
        legacy_key = hashlib.sha1(legacy_src.encode('utf-8')).hexdigest()[:24]

        # v2 key: independent of absolute install path, line number and surrounding
        # lines/default literals. This is the canonical ID used from now on.
        semantic_src = _callsite_ast_fingerprint(filename, int(chosen.f_lineno), chosen.f_code.co_name, kind)
        stable = 'v2_' + hashlib.sha1(semantic_src.encode('utf-8')).hexdigest()[:24]
        unknown_legacy = hashlib.sha1(f'unknown:{kind}:{current_text[:80]}'.encode()).hexdigest()[:24]
        _remember_legacy_text_key(stable, legacy_key, unknown_legacy)
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
    """True when an outgoing message originates from the live-editor UI itself.

    This must be independent of the installation directory.  Older builds looked
    for a literal ``/admin_bot_modular/`` path, so installing the exact same code
    under e.g. ``/root/pasarguard-tel-bot`` made editor prompts editable again.
    That recursively attached another "change this text" control to the editor's
    own messages and could create many duplicate edit prompts.
    """
    frame = inspect.currentframe()
    try:
        while frame:
            frame = frame.f_back
            if not frame:
                break
            rel = _project_relative_source(str(frame.f_code.co_filename))
            if rel is None:
                continue
            if rel in {'modules/edit_runtime.py', 'utils.py'}:
                continue
            return rel == 'modules/edit_mode.py'
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
