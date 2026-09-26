from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import aiohttp

from config import settings
import db


class TetraPayError(RuntimeError):
    pass


def _setting_str(key: str, env_value: str = '', default: str = '') -> str:
    value = db.get_setting(key, env_value or default)
    return str(value or '').strip()


def get_api_key() -> str:
    return _setting_str('tetrapay_api_key', settings.tetrapay_api_key)


def get_create_invoice_url() -> str:
    return _setting_str('tetrapay_create_invoice_url', settings.tetrapay_create_invoice_url)


def get_verify_url() -> str:
    return _setting_str('tetrapay_verify_url', settings.tetrapay_verify_url)


def get_callback_url() -> str:
    return _setting_str('tetrapay_callback_url', settings.tetrapay_callback_url)


def get_callback_secret() -> str:
    return _setting_str('tetrapay_callback_secret', settings.tetrapay_callback_secret)


def get_default_email() -> str:
    return _setting_str('tetrapay_default_email', settings.tetrapay_default_email, 'customer@example.com') or 'customer@example.com'


def get_default_mobile() -> str:
    return _setting_str('tetrapay_default_mobile', settings.tetrapay_default_mobile, '09000000000') or '09000000000'


def is_enabled() -> bool:
    return bool(
        get_api_key()
        and get_create_invoice_url()
        and get_callback_url()
    )


def callback_url_with_secret() -> str:
    """Return CallbackURL with optional shared secret.

    TetraPay calls the exact CallbackURL sent in create_order. If you set
    TETRAPAY_CALLBACK_SECRET, the secret is appended as a query string and is
    checked before processing the payment callback.

    Leave TETRAPAY_CALLBACK_SECRET empty if your provider panel rejects callback
    URLs with query parameters.
    """
    url = get_callback_url().strip()
    secret = get_callback_secret().strip()
    if not url or not secret:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query.get('secret') == secret:
        return url
    query['secret'] = secret
    return urlunparse(parsed._replace(query=urlencode(query)))


def _nested_dict(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in ('data', 'result', 'invoice', 'order'):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return None


def extract_payment_url(data: dict[str, Any]) -> str | None:
    """Prefer Telegram bot payment URL, then web URL, then common fallbacks."""
    candidates = [
        'payment_url_bot', 'payment_url_web',
        'PaymentURL', 'PaymentUrl', 'payment_url', 'paymentUrl', 'pay_url', 'payUrl',
        'URL', 'Url', 'url', 'Link', 'link', 'redirect_url', 'redirectUrl', 'invoice_url',
    ]
    for key in candidates:
        value = data.get(key)
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return value
    nested = _nested_dict(data)
    if nested:
        return extract_payment_url(nested)
    return None


def extract_payment_url_web(data: dict[str, Any]) -> str | None:
    value = data.get('payment_url_web')
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        return value
    nested = _nested_dict(data)
    if nested:
        return extract_payment_url_web(nested)
    return None


def extract_authority(data: dict[str, Any]) -> str | None:
    """Extract TetraPay Authority from create_order/verify/callback payload."""
    for key in ('Authority', 'authority', 'AUTHORITY'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value)
    nested = _nested_dict(data)
    if nested:
        return extract_authority(nested)
    return None


def extract_tracking_id(data: dict[str, Any]) -> str | None:
    for key in ('tracking_id', 'trackingid', 'trackingId', 'TrackingId', 'TrackingID', 'tracking_code', 'ref_id'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value)
    nested = _nested_dict(data)
    if nested:
        return extract_tracking_id(nested)
    return None


def extract_hash_id(data: dict[str, Any]) -> str | None:
    for key in ('hash_id', 'Hash_id', 'Hash_ID', 'HashId', 'hashId'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value)
    nested = _nested_dict(data)
    if nested:
        return extract_hash_id(nested)
    return None


def extract_provider_invoice_id(data: dict[str, Any], fallback: str) -> str:
    return extract_authority(data) or extract_tracking_id(data) or fallback


def extract_status(data: dict[str, Any]) -> str:
    for key in ('status', 'Status', 'STATUS'):
        if key in data:
            return str(data.get(key)).strip().lower()
    nested = _nested_dict(data)
    if nested:
        return extract_status(nested)
    return ''


def callback_is_success(data: dict[str, Any]) -> bool:
    status = extract_status(data)
    return status in {'100', '1', 'true', 'ok', 'success', 'successful', 'paid', 'done', 'completed', 'complete', '200'}


def extract_amount(data: dict[str, Any]) -> int:
    for key in ('Amount', 'amount', 'PaidAmount', 'paid_amount', 'price', 'Price', 'paid', 'Paid'):
        value = data.get(key)
        if value not in (None, ''):
            try:
                return int(float(str(value).replace(',', '').replace('٬', '').strip()))
            except Exception:
                pass
    nested = _nested_dict(data)
    if nested:
        return extract_amount(nested)
    return 0


def _validate_create_response(data: dict[str, Any], raw: str) -> None:
    status = extract_status(data)
    # TetraPay documents success as status == "100".
    if status and status != '100':
        raise TetraPayError(f'تتراپی ساخت سفارش را ناموفق اعلام کرد. status={status} response={raw[:300]}')
    if not extract_authority(data):
        raise TetraPayError(f'Authority از پاسخ تتراپی پیدا نشد: {raw[:300]}')
    if not extract_payment_url(data):
        raise TetraPayError(f'لینک پرداخت از پاسخ تتراپی پیدا نشد: {raw[:300]}')


async def create_invoice(
    *,
    hash_id: str,
    amount_toman: int,
    description: str,
    email: str | None = None,
    mobile: str | None = None,
) -> dict[str, Any]:
    """Create TetraPay order using the documented create_order JSON body."""
    if not is_enabled():
        raise TetraPayError('تنظیمات تتراپی کامل نیست. از تنظیمات پیشرفته یا .env مقدارهای تتراپی را کامل کنید.')
    if int(amount_toman) <= 0:
        raise TetraPayError('مبلغ تتراپی باید بزرگتر از صفر باشد.')

    payload = {
        'ApiKey': get_api_key(),
        'Hash_id': str(hash_id),
        'Amount': int(amount_toman),
        'Description': str(description),
        'Email': (email or get_default_email()),
        'Mobile': (mobile or get_default_mobile()),
        'CallbackURL': callback_url_with_secret(),
    }
    # Use shared keep-alive session to avoid TLS handshake per invoice
    try:
        from api_client import get_shared_session
        session = await get_shared_session()
    except Exception:
        session = None
    if session is not None and not session.closed:
        async with session.post(get_create_invoice_url(), json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw)
            except Exception:
                data = {'raw': raw}
            if resp.status >= 400:
                raise TetraPayError(f'خطای ساخت سفارش تتراپی: HTTP {resp.status} - {raw[:300]}')
            _validate_create_response(data, raw)
            authority = extract_authority(data) or str(hash_id)
            tracking_id = extract_tracking_id(data)
            return {
                'request': payload,
                'response': data,
                'payment_url': extract_payment_url(data),
                'payment_url_bot': data.get('payment_url_bot') or (_nested_dict(data) or {}).get('payment_url_bot'),
                'payment_url_web': extract_payment_url_web(data),
                'authority': authority,
                'tracking_id': tracking_id,
                'provider_invoice_id': authority,
            }
    # Fallback isolated session (rare)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as fallback:
        async with fallback.post(get_create_invoice_url(), json=payload) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw)
            except Exception:
                data = {'raw': raw}
            if resp.status >= 400:
                raise TetraPayError(f'خطای ساخت سفارش تتراپی: HTTP {resp.status} - {raw[:300]}')
            _validate_create_response(data, raw)
            authority = extract_authority(data) or str(hash_id)
            tracking_id = extract_tracking_id(data)
            return {
                'request': payload,
                'response': data,
                'payment_url': extract_payment_url(data),
                'payment_url_bot': data.get('payment_url_bot') or (_nested_dict(data) or {}).get('payment_url_bot'),
                'payment_url_web': extract_payment_url_web(data),
                'authority': authority,
                'tracking_id': tracking_id,
                'provider_invoice_id': authority,
            }


async def verify_payment(authority: str) -> dict[str, Any] | None:
    """Verify TetraPay payment using documented verify JSON body.

    Request:
        {"authority": "...", "ApiKey": "..."}

    Important: some gateways return HTTP 4xx or a non-100 status while the user
    has not completed payment/authentication yet. That is not a bot failure.
    Therefore this function returns the parsed response for non-2xx responses too
    and leaves success/failure interpretation to callback_is_success(). It only
    raises for missing config, empty authority, network errors, and timeouts.
    """
    if not get_verify_url():
        raise TetraPayError('آدرس Verify تتراپی تنظیم نشده است. از تنظیمات پیشرفته مقدار tetrapay_verify_url را کامل کنید.')
    if not authority:
        raise TetraPayError('Authority برای verify خالی است.')
    payload: dict[str, Any] = {
        'authority': str(authority),
        'ApiKey': get_api_key(),
    }
    try:
        from api_client import get_shared_session
        session = await get_shared_session()
    except Exception:
        session = None
    if session is not None and not session.closed:
        async with session.post(get_verify_url(), json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    data = {'data': data}
            except Exception:
                data = {'raw': raw}
            data['_http_status'] = resp.status
            data['_raw_text'] = raw
            data['_request_payload'] = {'authority': str(authority), 'ApiKey': '***'}
            return data
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as fallback:
        async with fallback.post(get_verify_url(), json=payload) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    data = {'data': data}
            except Exception:
                data = {'raw': raw}
            data['_http_status'] = resp.status
            data['_raw_text'] = raw
            data['_request_payload'] = {'authority': str(authority), 'ApiKey': '***'}
            return data
