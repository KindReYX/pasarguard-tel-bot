from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from config import settings
import db

_token_cache: str | None = None
# L1 memory cache for panel lists (reduces db.api_cache roundtrips)
_L1_CACHE: dict[str, tuple[Any, float]] = {}
_L1_TTL = 20.0
import time as _t

# Shared aiohttp session for panel + subscription + payment providers (keep-alive)
_SHARED_SESSION: aiohttp.ClientSession | None = None
_SHARED_LOCK = asyncio.Lock()

async def get_shared_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        return _SHARED_SESSION
    async with _SHARED_LOCK:
        if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
            return _SHARED_SESSION
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=10, ttl_dns_cache=300, enable_cleanup_closed=True)
        _SHARED_SESSION = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30))
        return _SHARED_SESSION

async def close_shared_session() -> None:
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        try:
            await _SHARED_SESSION.close()
        except Exception:
            pass
    _SHARED_SESSION = None

def _l1_get(key: str) -> Any | None:
    try:
        val, exp = _L1_CACHE.get(key, (None, 0))
        if _t.monotonic() < exp:
            return val
        _L1_CACHE.pop(key, None)
    except Exception:
        pass
    return None

def _l1_set(key: str, value: Any, ttl: float = _L1_TTL) -> None:
    try:
        _L1_CACHE[key] = (value, _t.monotonic() + ttl)
    except Exception:
        pass

def _l1_invalidate(prefix: str) -> None:
    for k in list(_L1_CACHE.keys()):
        if k.startswith(prefix):
            _L1_CACHE.pop(k, None)


class PanelAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str, data: Any = None):
        super().__init__(message)
        self.status = status
        self.data = data


def _api_url(path: str) -> str:
    if path.startswith('http://') or path.startswith('https://'):
        return path
    return f'{settings.api_base_url}{path}'


async def get_panel_token(force: bool = False) -> str:
    global _token_cache
    if _token_cache and not force:
        return _token_cache
    session = await get_shared_session()
    async with session.post(
        f'{settings.api_base_url}/api/admin/token',
        data={'username': settings.panel_admin_username, 'password': settings.panel_admin_password},
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
    ) as response:
            text = await response.text()
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {'raw': text}
            if response.status >= 400:
                raise PanelAPIError(response.status, f'Panel auth failed: {response.status} {data}', data)
            token = data.get('access_token') if isinstance(data, dict) else None
            if not token:
                raise PanelAPIError(response.status, 'Panel did not return access_token', data)
            _token_cache = token
            return token


async def _request_once(method: str, path: str, params: dict | None = None, payload: dict | None = None) -> Any:
    token = await get_panel_token()
    session = await get_shared_session()
    async with session.request(
        method,
        _api_url(path),
        params=params,
        json=payload,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'Content-Type': 'application/json'},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
            text = await response.text()
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {'raw': text}
            if response.status == 401:
                await get_panel_token(force=True)
                return await request(method, path, params, payload)
            if response.status >= 400:
                raise PanelAPIError(response.status, f'Panel API error {response.status}: {data}', data)
            return data


async def _request_text_once(method: str, path: str, params: dict | None = None, payload: dict | None = None, auth: bool = True) -> str:
    headers = {'Accept': '*/*'}
    if auth:
        token = await get_panel_token()
        headers['Authorization'] = f'Bearer {token}'
    session = await get_shared_session()
    async with session.request(
        method,
        _api_url(path),
        params=params,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
            text = await response.text()
            if response.status == 401 and auth:
                await get_panel_token(force=True)
                return await request_text(method, path, params, payload, auth)
            if response.status >= 400:
                raise PanelAPIError(response.status, f'Panel text API error {response.status}: {text[:300]}', {'raw': text})
            return text


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, PanelAPIError):
        return exc.status in (408, 409, 425, 429, 500, 502, 503, 504)
    msg = str(exc).lower()
    return any(part in msg for part in ('timeout', 'temporarily', 'connection reset', 'server disconnected', '502', '503', '504'))


async def request(method: str, path: str, params: dict | None = None, payload: dict | None = None, retries: int = 3) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max(1, int(retries))):
        try:
            return await _request_once(method, path, params=params, payload=payload)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc) or attempt >= retries - 1:
                try:
                    import db
                    db.record_system_error('api_client.request', str(exc), entity_type='api', entity_id=f'{method} {path}', error_type=type(exc).__name__)
                except Exception:
                    pass
                raise
            await asyncio.sleep(0.6 * (attempt + 1))
    raise last_exc or RuntimeError('Unknown API error')


async def request_text(method: str, path: str, params: dict | None = None, payload: dict | None = None, auth: bool = True, retries: int = 3) -> str:
    last_exc: Exception | None = None
    for attempt in range(max(1, int(retries))):
        try:
            return await _request_text_once(method, path, params=params, payload=payload, auth=auth)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc) or attempt >= retries - 1:
                try:
                    import db
                    db.record_system_error('api_client.request_text', str(exc), entity_type='api', entity_id=f'{method} {path}', error_type=type(exc).__name__)
                except Exception:
                    pass
                raise
            await asyncio.sleep(0.6 * (attempt + 1))
    raise last_exc or RuntimeError('Unknown text API error')


async def _hydrate_public_priced_templates(templates: list[dict]) -> list[dict]:
    """Ensure public-priced templates have the fields required to calculate price.

    The panel's /simple endpoint may omit data_limit. Public per-GB pricing needs
    data_limit, and treating a missing field as zero makes an otherwise valid
    package disappear from the customer purchase list. Only templates that are
    configured to use product public pricing are hydrated, with small bounded
    concurrency and the existing detail cache/stale fallback.
    """
    if not isinstance(templates, list) or not templates:
        return templates
    try:
        rows = db.fetchall(
            'SELECT template_id FROM package_settings WHERE use_product_price=1 AND is_visible=1'
        )
        needed = {str(r.get('template_id')) for r in rows if r.get('template_id') is not None}
    except Exception:
        return templates
    if not needed:
        return templates

    by_id = {str(t.get('id')): t for t in templates if isinstance(t, dict) and t.get('id') is not None}
    missing = [tid for tid in needed if tid in by_id and int(by_id[tid].get('data_limit') or 0) <= 0]
    if not missing:
        return templates

    sem = asyncio.Semaphore(4)

    async def load_one(tid: str):
        async with sem:
            try:
                detail = await get_user_template(tid)
                return tid, detail if isinstance(detail, dict) else None
            except Exception:
                return tid, None

    results = await asyncio.gather(*(load_one(tid) for tid in missing))
    for tid, detail in results:
        if not detail:
            continue
        base = by_id.get(tid)
        if base is None:
            continue
        # Preserve any list-specific fields while filling full template fields.
        merged = dict(detail)
        merged.update(base)
        if int(merged.get('data_limit') or 0) <= 0 and int(detail.get('data_limit') or 0) > 0:
            merged['data_limit'] = detail.get('data_limit')
        base.clear()
        base.update(merged)
    return await _hydrate_public_priced_templates(templates)


async def get_user_templates() -> list[dict]:
    # L1 first. Even cached simple lists are hydrated for public-priced packs.
    l1 = _l1_get('panel:user_templates:simple')
    if l1 is not None:
        return await _hydrate_public_priced_templates(l1)
    try:
        import db
        cached = db.cache_get('panel:user_templates:simple')
        if cached is not None:
            _l1_set('panel:user_templates:simple', cached, 20)
            return await _hydrate_public_priced_templates(cached)
    except Exception:
        pass
    try:
        data = await request('GET', '/api/user_templates/simple', params={'all': 'true'})
        templates = data.get('templates', []) if isinstance(data, dict) else []
    except Exception:
        # Keep the admin/customer UI usable during a temporary slow panel.
        # The last successful list is deliberately cached much longer than the
        # normal freshness cache and is only used as a fallback.
        try:
            stale = db.cache_get('panel:user_templates:last_good')
            if isinstance(stale, list):
                _l1_set('panel:user_templates:simple', stale, 10)
                return await _hydrate_public_priced_templates(stale)
        except Exception:
            pass
        raise
    try:
        import db
        db.cache_set('panel:user_templates:simple', templates, 60)
        db.cache_set('panel:user_templates:last_good', templates, 7 * 24 * 3600)
        _l1_set('panel:user_templates:simple', templates, 20)
    except Exception:
        pass
    # The first network response from /simple can omit data_limit. Public-price
    # packages must be hydrated before the very first customer render as well,
    # otherwise they only appear on the second visit after the cache is warm.
    return await _hydrate_public_priced_templates(templates)


async def get_user_template(template_id: str | int, *, force: bool = False) -> dict:
    tid = str(template_id)
    key = f'panel:user_template:{tid}'
    stale_key = f'{key}:last_good'
    if not force:
        l1 = _l1_get(key)
        if isinstance(l1, dict):
            return l1
        try:
            cached = db.cache_get(key)
            if isinstance(cached, dict):
                _l1_set(key, cached, 60)
                return cached
        except Exception:
            pass
    try:
        data = await request('GET', f'/api/user_template/{tid}')
        if not isinstance(data, dict):
            raise RuntimeError('Invalid template response')
    except Exception:
        if not force:
            try:
                stale = db.cache_get(stale_key)
                if isinstance(stale, dict):
                    _l1_set(key, stale, 20)
                    return stale
            except Exception:
                pass
        raise
    try:
        db.cache_set(key, data, 120)
        db.cache_set(stale_key, data, 7 * 24 * 3600)
        _l1_set(key, data, 60)
    except Exception:
        pass
    return data


def _cache_template_detail(template: dict) -> None:
    if not isinstance(template, dict):
        return
    tid = str(template.get('id') or '')
    if not tid:
        return
    key = f'panel:user_template:{tid}'
    try:
        db.cache_set(key, template, 120)
        db.cache_set(f'{key}:last_good', template, 7 * 24 * 3600)
        _l1_set(key, template, 60)
    except Exception:
        pass


async def create_user_template(payload: dict) -> dict:
    data = await request('POST', '/api/user_template', payload=payload)
    _cache_template_detail(data if isinstance(data, dict) else {})
    _l1_invalidate('panel:user_templates:')
    try:
        db.cache_delete_prefix('panel:user_templates:')
    except Exception:
        pass
    return data


async def update_user_template(template_id: str | int, payload: dict) -> dict:
    data = await request('PUT', f'/api/user_template/{template_id}', payload=payload)
    _cache_template_detail(data if isinstance(data, dict) else {})
    _l1_invalidate('panel:user_templates:')
    try:
        db.cache_delete_prefix('panel:user_templates:')
    except Exception:
        pass
    return data


async def delete_user_template(template_id: str | int) -> Any:
    tid = str(template_id)
    data = await request('DELETE', f'/api/user_template/{tid}')
    _l1_invalidate(f'panel:user_template:{tid}')
    _l1_invalidate('panel:user_templates:')
    try:
        db.cache_delete_prefix(f'panel:user_template:{tid}')
        db.cache_delete_prefix('panel:user_templates:')
    except Exception:
        pass
    return data


async def get_groups() -> list[dict]:
    l1 = _l1_get('panel:groups:simple')
    if l1 is not None:
        return l1
    try:
        import db
        cached = db.cache_get('panel:groups:simple')
        if cached is not None:
            _l1_set('panel:groups:simple', cached, 40)
            return cached
    except Exception:
        pass
    data = await request('GET', '/api/groups/simple', params={'all': 'true'})
    groups = data.get('groups', []) if isinstance(data, dict) else []
    try:
        import db
        db.cache_set('panel:groups:simple', groups, 120)
        _l1_set('panel:groups:simple', groups, 40)
    except Exception:
        pass
    return groups


def template_username_prefix(template: dict) -> str:
    return str(template.get('username_prefix') or '').strip()


def template_username_suffix(template: dict) -> str:
    return str(template.get('username_suffix') or '').strip()


def apply_template_username(template: dict, username: str) -> str:
    raw = str(username or '').strip()
    return f'{template_username_prefix(template)}{raw}{template_username_suffix(template)}'


async def create_user(payload: dict) -> dict:
    data = await request('POST', '/api/user', payload=payload)
    if not isinstance(data, dict):
        raise RuntimeError('Invalid create user response')
    return data


def is_duplicate_username_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in (
        'already exists', 'already exist', 'already', 'duplicate', 'exists',
        'taken', 'unique', 'تکراری', 'وجود دارد', 'قبلا وجود دارد',
    ))



def custom_template_override(template: dict, *, gb: int, months: int, users: int | None = None) -> dict:
    out = dict(template)
    out['data_limit'] = int(gb) * 1024 * 1024 * 1024
    out['expire_duration'] = int(months) * 30 * 86400
    if users is not None and int(users) > 0:
        out['hwid_limit'] = int(users)
    return out

async def create_user_with_unique_username(
    template: dict,
    raw_username: str,
    note: str | None = None,
    attempts: int = 20,
) -> tuple[dict, str]:
    """Create a panel user, appending a random numeric suffix on collisions.

    The first attempt uses the exact username requested by the customer. If it
    already exists, later attempts use values such as ``username4821`` while
    preserving the package template prefix/suffix.
    """
    base = str(raw_username or '').strip()
    if not base:
        raise ValueError('username is empty')

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        candidate = base if attempt == 0 else f'{base}{random.randint(1000, 999999)}'
        payload = build_user_payload_from_template(template, candidate, note=note)
        try:
            created = await create_user(payload)
            actual = str(created.get('username') or apply_template_username(template, candidate))
            return created, actual
        except Exception as exc:
            last_error = exc
            if is_duplicate_username_error(exc):
                continue
            raise

    raise RuntimeError(f'Could not generate a unique username after {attempts} attempts: {last_error}')


async def delete_user(username: str) -> dict:
    username = str(username or '').strip()
    if not username:
        raise ValueError('username is empty')
    data = await request('DELETE', f'/api/user/{username}')
    return data if isinstance(data, dict) else {}


def build_user_payload_from_template(template: dict, username: str, note: str | None = None) -> dict:
    panel_username = apply_template_username(template, username)
    payload: dict = {
        'username': panel_username,
        'status': 'active',
    }

    # HWID is intentionally not sent by default, so the panel/server default is used.
    # Admin can set `default_hwid_limit` from settings. 0 means server default.
    try:
        import db
        default_hwid_limit = int(db.get_setting('default_hwid_limit', 0) or 0)
    except Exception:
        default_hwid_limit = 0

    if template.get('hwid_limit') not in (None, '', 0, '0'):
        payload['hwid_limit'] = int(template.get('hwid_limit'))
    elif default_hwid_limit > 0:
        payload['hwid_limit'] = default_hwid_limit

    if template.get('data_limit') is not None:
        payload['data_limit'] = template.get('data_limit')
    duration = template.get('expire_duration')
    try:
        duration_int = int(duration or 0)
    except (TypeError, ValueError):
        duration_int = 0
    if duration_int > 0:
        payload['expire'] = int(time.time()) + duration_int
    if template.get('data_limit_reset_strategy'):
        payload['data_limit_reset_strategy'] = template.get('data_limit_reset_strategy')
    if template.get('group_ids') is not None:
        payload['group_ids'] = template.get('group_ids')
    if note:
        payload['note'] = note
    return payload


async def get_user(username: str) -> dict:
    data = await request('GET', f'/api/user/{username}')
    if not isinstance(data, dict):
        raise RuntimeError('Invalid user response')
    return data


async def user_exists(username: str) -> bool:
    try:
        await get_user(username)
        return True
    except PanelAPIError as exc:
        if exc.status == 404:
            return False
        message = str(exc).lower()
        if 'not found' in message or '404' in message:
            return False
        raise
    except Exception as exc:
        message = str(exc).lower()
        if 'not found' in message or '404' in message:
            return False
        raise


async def generate_unique_test_username(template: dict | None = None, attempts: int = 30) -> str:
    template = template or {}
    for _ in range(attempts):
        raw = f'test_{random.randint(100000, 99999999)}'
        panel_username = apply_template_username(template, raw)
        try:
            exists = await user_exists(panel_username)
        except Exception:
            # If the existence check itself fails for a temporary reason, still try a random name.
            exists = False
        if not exists:
            return raw
    return f'test_{int(time.time())}_{random.randint(1000,9999)}'


async def update_user(username: str, payload: dict) -> dict:
    data = await request('PUT', f'/api/user/{username}', payload=payload)
    if not isinstance(data, dict):
        raise RuntimeError('Invalid update user response')
    return data


async def list_users(*, page_size: int = 200, max_users: int = 10000) -> list[dict]:
    """Return panel users using bounded pagination.

    Used by the customer-side service import flow to prove which panel user owns
    a submitted subscription/config. The response is kept in-memory only and is
    never written to logs because it may contain access credentials.
    """
    out: list[dict] = []
    offset = 0
    page_size = max(1, min(int(page_size or 200), 1000))
    max_users = max(1, int(max_users or 10000))
    while len(out) < max_users:
        data = await request('GET', '/api/users', params={'offset': offset, 'limit': page_size})
        if not isinstance(data, dict):
            break
        users = data.get('users') or []
        if not isinstance(users, list):
            break
        page = [u for u in users if isinstance(u, dict)]
        out.extend(page)
        total = int(data.get('total') or 0)
        if not page or len(page) < page_size or (total and len(out) >= total):
            break
        offset += len(page)
    return out[:max_users]


async def set_user_owner(username: str, admin_username: str) -> dict:
    """Transfer a panel user's owner to the bot's panel admin account.

    PasarGuard 5.4.1 exposes this as PUT /api/user/{username}/set_owner with
    admin_username as a query parameter. The configured panel account must be a
    full-access admin for cross-owner adoption.
    """
    username = str(username or '').strip()
    admin_username = str(admin_username or '').strip()
    if not username or not admin_username:
        raise ValueError('username/admin_username is empty')
    data = await request(
        'PUT',
        f'/api/user/{username}/set_owner',
        params={'admin_username': admin_username},
    )
    if not isinstance(data, dict):
        raise RuntimeError('Invalid set_owner response')
    return data


def _owner_username(user_data: dict | None) -> str | None:
    if not isinstance(user_data, dict):
        return None
    for key in ('admin', 'owner', 'owner_admin', 'admin_username', 'owner_username'):
        value = user_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for subkey in ('username', 'name'):
                v = value.get(subkey)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _decode_vmess_payload(link: str) -> dict:
    raw = str(link or '').strip()
    if not raw.lower().startswith('vmess://'):
        return {}
    payload = raw.split('://', 1)[1].split('#', 1)[0].strip()
    payload = payload.replace('-', '+').replace('_', '/')
    payload += '=' * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(payload, validate=False).decode('utf-8', errors='ignore')
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


UUID_RE = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f])")


def extract_access_uuids(value: Any) -> set[str]:
    """Extract canonical UUIDs from arbitrary submitted text or panel payload."""
    if value is None:
        return set()
    if isinstance(value, dict):
        out: set[str] = set()
        for item in value.values():
            out.update(extract_access_uuids(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for item in value:
            out.update(extract_access_uuids(item))
        return out
    text = str(value)
    return {m.group(1).lower() for m in UUID_RE.finditer(text)}


async def _submitted_uuids(raw_input: str) -> set[str]:
    """Get UUIDs from the message; for a subscription URL, inspect its configs too."""
    raw = str(raw_input or '').strip()
    found = extract_access_uuids(raw)
    if found:
        return found
    if raw.lower().startswith(('http://', 'https://')):
        try:
            configs = await fetch_subscription_configs(raw)
            for config in configs:
                found.update(extract_access_uuids(config))
        except Exception:
            pass
    return found


async def _users_by_proxy_uuid(proxy_uuid: str) -> list[dict]:
    """Use PasarGuard's native proxy_id filter to locate the owning user."""
    proxy_uuid = str(proxy_uuid or '').strip().lower()
    if not proxy_uuid:
        return []
    try:
        data = await request(
            'GET',
            '/api/users',
            params={'proxy_id': proxy_uuid, 'offset': 0, 'limit': 50},
        )
    except PanelAPIError as exc:
        # Some panel builds return 404 rather than an empty UsersResponse.
        if exc.status == 404:
            return []
        raise
    if not isinstance(data, dict):
        return []
    users = data.get('users') or []
    return [u for u in users if isinstance(u, dict)]


async def resolve_panel_user_from_access_input(raw_input: str) -> dict:
    """Resolve a service only by proxy UUID using the panel's official filter.

    PasarGuard 5.4.1 supports GET /api/users?proxy_id=<uuid>.  We extract UUIDs
    from the submitted config, or from configs returned by a subscription URL,
    and ask the panel directly which user owns each UUID.  No domain, username,
    display name, subscription path, or local heuristic participates in matching.
    """
    raw = str(raw_input or '').strip()
    if not raw:
        raise ValueError('empty service input')

    submitted_uuids = await _submitted_uuids(raw)
    if not submitted_uuids:
        raise LookupError('service_uuid_missing')

    matches: dict[str, dict] = {}
    for proxy_uuid in sorted(submitted_uuids):
        for user_data in await _users_by_proxy_uuid(proxy_uuid):
            username = str(user_data.get('username') or '').strip()
            user_id = user_data.get('id')
            key = username or (f'id:{user_id}' if user_id is not None else '')
            if key:
                matches[key] = user_data

    if not matches:
        raise LookupError('service_not_found')
    if len(matches) > 1:
        raise LookupError('service_match_ambiguous')

    return next(iter(matches.values()))


async def adopt_panel_user_for_bot(raw_input: str) -> dict:
    """Resolve service and ensure the bot panel admin owns it.

    This gives the configured bot panel account full management access to the
    service before it is attached to a Telegram customer.
    """
    user_data = await resolve_panel_user_from_access_input(raw_input)
    username = str(user_data.get('username') or '').strip()
    if not username:
        raise LookupError('service_username_missing')
    target_owner = str(settings.panel_admin_username or '').strip()
    current_owner = _owner_username(user_data)
    if target_owner and current_owner != target_owner:
        user_data = await set_user_owner(username, target_owner)
    elif target_owner and current_owner is None:
        # Some panel versions omit owner from UserResponse. Calling set-owner is
        # idempotent and guarantees the bot's full-access admin becomes owner.
        user_data = await set_user_owner(username, target_owner)
    return user_data


async def revoke_user_subscription(username: str) -> dict:
    try:
        data = await request('POST', f'/api/user/{username}/revoke_sub')
    except Exception:
        data = await request('POST', f'/api/user/{username}/revoke_subscription')
    return data if isinstance(data, dict) else {}


def normalize_subscription_url(value: str | None, user_data: dict | None = None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    base = str(db.get_setting('subscription_base_url', settings.subscription_base_url) or '').rstrip('/')
    if not base:
        return raw
    username = None
    token = raw.strip('/').split('/')[-1] if raw.strip('/') else raw
    if user_data:
        username = user_data.get('username') or user_data.get('name') or user_data.get('panel_username')
    if '{username}' in base:
        return base.format(username=str(username or token), token=token)
    if '{token}' in base:
        return base.format(username=str(username or token), token=token)
    if raw.startswith('/'):
        return base.rstrip('/') + raw
    return base.rstrip('/') + '/' + raw.lstrip('/')


def subscription_url_for_username(username: str) -> str | None:
    username = str(username or '').strip()
    if not username or not settings.subscription_base_url:
        return None
    base = str(db.get_setting('subscription_base_url', settings.subscription_base_url) or '').rstrip('/')
    if '{username}' in base or '{token}' in base:
        return base.format(username=username, token=username)
    return base.rstrip('/') + '/' + username.lstrip('/')


def extract_subscription_url(user_data: dict) -> str | None:
    for key in (
        'subscription_url', 'subscription', 'sub_url', 'sub_link', 'subscription_link',
        'subscription_path', 'sub_path', 'subscription_token', 'sub_token', 'token',
    ):
        value = user_data.get(key)
        if isinstance(value, str) and value.strip():
            normalized = normalize_subscription_url(value, user_data)
            if normalized:
                return normalized
    links = user_data.get('links')
    if isinstance(links, list) and links:
        first = links[0]
        if isinstance(first, dict):
            for key in ('subscription_url', 'sub_url', 'url', 'link'):
                if first.get(key):
                    normalized = normalize_subscription_url(str(first[key]), user_data)
                    if normalized:
                        return normalized
    username = user_data.get('username') or user_data.get('name') or user_data.get('panel_username')
    if username:
        return subscription_url_for_username(str(username))
    return None


CONFIG_URI_RE = re.compile(
    r'(?:(?:vless|vmess|trojan|ss|ssr|hysteria|hysteria2|hy2|tuic|warp|wireguard)://[^\s<>"\']+)',
    re.IGNORECASE,
)


def _clean_config_link(value: str) -> str | None:
    value = str(value or '').strip()
    if not value:
        return None
    value = value.strip('`').strip()
    if CONFIG_URI_RE.match(value):
        return value
    match = CONFIG_URI_RE.search(value)
    if match:
        return match.group(0).strip()
    return None


def _maybe_decode_base64_text(text: str) -> str | None:
    raw = str(text or '').strip()
    if not raw or '://' in raw:
        return None
    compact = ''.join(raw.split())
    if len(compact) < 16:
        return None
    if not re.fullmatch(r'[A-Za-z0-9+/=_-]+', compact):
        return None
    compact = compact.replace('-', '+').replace('_', '/')
    compact += '=' * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(compact, validate=False)
        text_decoded = decoded.decode('utf-8', errors='ignore').strip()
    except Exception:
        return None
    if '://' in text_decoded:
        return text_decoded
    return None


def _walk_for_configs(value: Any, result: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        cleaned = _clean_config_link(value)
        if cleaned:
            result.append(cleaned)
        else:
            for match in CONFIG_URI_RE.findall(value):
                result.append(match.strip())
            decoded = _maybe_decode_base64_text(value)
            if decoded:
                _walk_for_configs(decoded, result)
        return
    if isinstance(value, dict):
        # Prefer common config/link containers first, then recursively inspect everything.
        for key in (
            'config', 'configs', 'config_links', 'links', 'link', 'url', 'uri',
            'proxy', 'proxies', 'subscription', 'subscription_url', 'sub_url', 'data', 'result',
        ):
            if key in value:
                _walk_for_configs(value.get(key), result)
        for item in value.values():
            _walk_for_configs(item, result)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_for_configs(item, result)


def extract_config_links(user_data: dict) -> list[str]:
    result: list[str] = []
    _walk_for_configs(user_data, result)
    return list(dict.fromkeys(item for item in result if item))


def extract_config_links_from_text(text: str) -> list[str]:
    result: list[str] = []
    raw = str(text or '')
    try:
        parsed = json.loads(raw)
        _walk_for_configs(parsed, result)
    except Exception:
        pass
    for line in raw.splitlines():
        cleaned = _clean_config_link(line)
        if cleaned:
            result.append(cleaned)
    for match in CONFIG_URI_RE.findall(raw):
        result.append(match.strip())
    decoded = _maybe_decode_base64_text(raw)
    if decoded and decoded != raw:
        result.extend(extract_config_links_from_text(decoded))
    return list(dict.fromkeys(item for item in result if item))




def _safe_button_label(text: str, fallback: str, limit: int = 48) -> str:
    value = str(text or '').replace('\n', ' ').replace('\r', ' ').strip()
    value = re.sub(r'\s+', ' ', value)
    if not value:
        value = fallback
    if len(value) > limit:
        value = value[:limit - 1].rstrip() + '…'
    return value


def _decode_vmess_name(link: str) -> str | None:
    raw = str(link or '').strip()
    if not raw.lower().startswith('vmess://'):
        return None
    payload = raw.split('://', 1)[1].split('#', 1)[0].strip()
    payload = payload.replace('-', '+').replace('_', '/')
    payload += '=' * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(payload, validate=False).decode('utf-8', errors='ignore')
        data = json.loads(decoded)
    except Exception:
        return None
    for key in ('ps', 'name', 'remarks', 'remark', 'tag'):
        value = data.get(key) if isinstance(data, dict) else None
        if value:
            return str(value)
    return None


def config_display_name(link: str, index: int | None = None) -> str:
    """Return the same user-facing config name V2Ray clients usually show.

    VLESS/Trojan/SS links normally keep the display name in URL fragment after #.
    VMess links usually keep it in the base64 JSON field `ps`.
    """
    raw = str(link or '').strip()
    idx = int(index or 0)
    protocol = raw.split('://', 1)[0].upper() if '://' in raw else 'کانفیگ'
    fallback = f'{protocol} {idx}' if idx else protocol

    vmess_name = _decode_vmess_name(raw)
    if vmess_name:
        return _safe_button_label(unquote(vmess_name), fallback)

    try:
        parsed = urlparse(raw)
        if parsed.fragment:
            return _safe_button_label(unquote(parsed.fragment), fallback)
        query = parse_qs(parsed.query or '')
        for key in ('remarks', 'remark', 'name', 'tag', 'title', 'ps'):
            values = query.get(key)
            if values and values[0]:
                return _safe_button_label(unquote(values[0]), fallback)
        host = parsed.hostname or ''
        port = f':{parsed.port}' if parsed.port else ''
        if host:
            return _safe_button_label(f'{protocol} {host}{port}', fallback)
    except Exception:
        pass

    # Manual fragment fallback for unusual parsers or encoded links.
    if '#' in raw:
        frag = raw.rsplit('#', 1)[1]
        if frag:
            return _safe_button_label(unquote(frag), fallback)
    return _safe_button_label(fallback, fallback)

async def fetch_subscription_configs(subscription_url: str | None) -> list[str]:
    if not subscription_url:
        return []
    url = str(subscription_url).strip()
    if not url.startswith(('http://', 'https://')):
        return []
    session = await get_shared_session()
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30), headers={'Accept': '*/*'}) as response:
        if response.status >= 400:
            return []
        text = await response.text()
        return extract_config_links_from_text(text)


def _extract_links_from_any_response(data: Any) -> list[str]:
    if isinstance(data, str):
        return extract_config_links_from_text(data)
    if isinstance(data, dict):
        links = extract_config_links(data)
        raw = data.get('raw')
        if raw:
            links.extend(extract_config_links_from_text(str(raw)))
        return list(dict.fromkeys(item for item in links if item))
    if isinstance(data, list):
        result: list[str] = []
        for item in data:
            result.extend(_extract_links_from_any_response(item))
        return list(dict.fromkeys(item for item in result if item))
    return []


async def _try_user_config_endpoint(username: str, path: str) -> Any | None:
    try:
        return await request('GET', path.format(username=username))
    except Exception:
        try:
            text = await request_text('GET', path.format(username=username), auth=True)
            return {'raw': text}
        except Exception:
            return None


async def get_user_config_links(username: str) -> tuple[list[str], dict, str | None]:
    """Fetch current config links directly from the panel API for one user.

    The first and most important source is GET /api/user/{username}, because the
    OpenAPI used by this project exposes user details there. The returned object
    can contain `links` and/or `subscription_url`. Subscription text may be plain
    or base64, so both are parsed.
    """
    username = str(username or '').strip()
    if not username:
        return [], {}, None

    user_data: dict = {}
    subscription_url: str | None = None
    links: list[str] = []

    try:
        user_data = await get_user(username)
        subscription_url = extract_subscription_url(user_data)
        links.extend(extract_config_links(user_data))
    except Exception:
        user_data = {}

    # Prefer subscription from user object/.env when the direct object did not include configs.
    if not subscription_url:
        subscription_url = subscription_url_for_username(username)
    if not links and subscription_url:
        try:
            links.extend(await fetch_subscription_configs(subscription_url))
        except Exception:
            pass

    # Keep these as fallback only for panel variants that expose dedicated link endpoints.
    if not links:
        candidate_paths = [
            '/api/user/{username}/links',
            '/api/user/{username}/configs',
            '/api/user/{username}/config',
            '/api/user/{username}/subscription',
            '/api/user/{username}/proxies',
        ]
        for path in candidate_paths:
            data = await _try_user_config_endpoint(username, path)
            if data is None:
                continue
            if isinstance(data, dict) and not user_data:
                user_data = data
            if isinstance(data, dict):
                subscription_url = extract_subscription_url(data) or subscription_url
            links.extend(_extract_links_from_any_response(data))
            if links:
                break

    links = list(dict.fromkeys(item for item in links if item))
    return links, user_data, subscription_url


def panel_timestamp(value: Any) -> int:
    """Normalize panel date values (Unix seconds/ms or ISO-8601) to Unix seconds."""
    if value in (None, '', 0, '0'):
        return 0
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts // 1000 if ts > 10_000_000_000 else ts
    text = str(value).strip()
    if not text:
        return 0
    try:
        ts = int(float(text))
        return ts // 1000 if ts > 10_000_000_000 else ts
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return 0


def normalize_renewal_mode(value: Any) -> str:
    mode = str(value or 'add').strip().lower()
    return mode if mode in {'add', 'replace'} else 'add'


def build_renew_payload(current_user: dict, template: dict, renewal_mode: str = 'add', *, now_ts: int | None = None) -> dict:
    """Build a renewal PATCH payload.

    add: preserve remaining quota/time and add the selected package.
    replace: discard remaining quota/time and start the selected package from now.
    In replace mode the caller must also reset used traffic so the new data_limit
    is fully usable.
    """
    payload: dict = {}
    mode = normalize_renewal_mode(renewal_mode)
    now = int(now_ts if now_ts is not None else time.time())

    raw_template_limit = template.get('data_limit')
    if raw_template_limit is not None:
        template_limit = int(raw_template_limit or 0)
        current_limit = int(current_user.get('data_limit') or 0)
        if mode == 'replace':
            payload['data_limit'] = template_limit
        elif template_limit <= 0:
            # An unlimited package remains unlimited when added.
            payload['data_limit'] = 0
        else:
            payload['data_limit'] = current_limit + template_limit

    duration = int(template.get('expire_duration') or 0)
    if duration > 0:
        if mode == 'replace':
            payload['expire'] = now + duration
        else:
            current_expire = panel_timestamp(current_user.get('expire'))
            base = current_expire if current_expire > now else now
            payload['expire'] = base + duration

    # Keep valid panel states, but never echo legacy/derived states such as
    # "limited" or "expired" to APIs that only accept these three values.
    raw_status = str(current_user.get('status') or '').strip().lower()
    status_aliases = {'enabled': 'active', 'inactive': 'disabled'}
    normalized_status = status_aliases.get(raw_status, raw_status)
    payload['status'] = normalized_status if normalized_status in {'active', 'disabled', 'on_hold'} else 'active'
    return payload


async def reset_user_traffic(username: str) -> dict:
    """Reset panel usage counter (Marzban-compatible endpoint)."""
    username = str(username or '').strip()
    if not username:
        raise ValueError('username is empty')
    data = await request('POST', f'/api/user/{username}/reset')
    _l1_invalidate('users:')
    return data if isinstance(data, dict) else {}
