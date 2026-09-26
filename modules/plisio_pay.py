from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, ROUND_UP
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import aiohttp

import db
from config import settings
from modules import navasan


class PlisioError(RuntimeError):
    pass


class PlisioMinimumAmountError(PlisioError):
    def __init__(self, amount_usd: float, min_usd: float):
        self.amount_usd = float(amount_usd)
        self.min_usd = float(min_usd)
        super().__init__(f'مبلغ فاکتور رمز ارز کمتر از حداقل مجاز است. amount_usd={self.amount_usd} min_usd={self.min_usd}')


DEFAULT_API_BASE = 'https://example.com/api/v1'


def get_api_key() -> str:
    # Super admin can update from bot later; .env is fallback.
    return str(db.get_setting('plisio_api_key', getattr(settings, 'plisio_api_key', '') or '') or '').strip()


def get_api_base_url() -> str:
    return str(db.get_setting('plisio_api_base_url', getattr(settings, 'plisio_api_base_url', '') or DEFAULT_API_BASE) or DEFAULT_API_BASE).rstrip('/')


def get_callback_url() -> str:
    return str(db.get_setting('plisio_callback_url', getattr(settings, 'plisio_callback_url', '') or '') or '').strip()


def get_success_url() -> str:
    return str(db.get_setting('plisio_success_url', getattr(settings, 'plisio_success_url', '') or '') or '').strip()


def get_fail_url() -> str:
    return str(db.get_setting('plisio_fail_url', getattr(settings, 'plisio_fail_url', '') or '') or '').strip()


def get_default_currency() -> str:
    return str(db.get_setting('plisio_default_currency', getattr(settings, 'plisio_default_currency', '') or 'USDT_TRX') or 'USDT_TRX').strip()


def get_allowed_psys_cids() -> str:
    return str(db.get_setting('plisio_allowed_psys_cids', getattr(settings, 'plisio_allowed_psys_cids', '') or '') or '').strip()


def get_expire_min() -> int:
    try:
        return int(db.get_setting('plisio_expire_min', getattr(settings, 'plisio_expire_min', 60) or 60) or 60)
    except Exception:
        return 60


def is_enabled() -> bool:
    return bool(get_api_key() and get_callback_url())


def callback_url_json() -> str:
    """Plisio SDK docs recommend json=true so callbacks can be validated."""
    url = get_callback_url()
    if not url:
        return ''
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['json'] = 'true'
    return urlunparse(parsed._replace(query=urlencode(query)))


def get_min_usd_amount() -> Decimal:
    return Decimal(str(db.get_setting('plisio_min_usd_amount', getattr(settings, 'plisio_min_usd_amount', 5.01) or 5.01) or 5.01))


def toman_to_usd_amount(amount_toman: int) -> tuple[str, int, float]:
    usd_float, rate_toman = navasan.toman_to_usd(int(amount_toman))
    amount = Decimal(str(usd_float))
    # Plisio accepts fiat float/string. Round up to cents so we don't undercharge.
    amount = amount.quantize(Decimal('0.01'), rounding=ROUND_UP)
    return format(amount, 'f'), int(rate_toman), float(amount)


def _extract_data(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get('data')
    return nested if isinstance(nested, dict) else data


def extract_invoice_url(data: dict[str, Any]) -> str | None:
    payload = _extract_data(data)
    for key in ('invoice_url', 'url', 'payment_url', 'pay_url'):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return value
    return None


def extract_txn_id(data: dict[str, Any]) -> str | None:
    payload = _extract_data(data)
    for key in ('txn_id', 'id', 'operation_id'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value)
    return None


def extract_order_number(data: dict[str, Any]) -> str | None:
    for key in ('order_number', 'orderNumber', 'order_id'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value)
    payload = _extract_data(data)
    for key in ('order_number', 'orderNumber', 'order_id'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value)
    return None


def extract_status(data: dict[str, Any]) -> str:
    # Plisio operation/invoice responses commonly use top-level status=success for API request
    # while the actual payment state lives in data.status. Always prefer nested payment status.
    payload = _extract_data(data)
    if payload is not data:
        for key in ('status', 'invoice_status', 'operation_status'):
            value = payload.get(key)
            if value not in (None, ''):
                return str(value).strip().lower()
    for key in ('invoice_status', 'operation_status', 'status'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value).strip().lower()
    return ''

def is_success(data: dict[str, Any]) -> bool:
    status = extract_status(data)
    status_code = str(data.get('status_code') or _extract_data(data).get('status_code') or '').strip().lower()
    return status in {'completed', 'complete', 'paid'} or status_code in {'1', '2', 'completed', 'paid'}


def is_final_failed(data: dict[str, Any]) -> bool:
    status = extract_status(data)
    status_code = str(data.get('status_code') or _extract_data(data).get('status_code') or '').strip().lower()
    return status in {'cancelled', 'canceled', 'error', 'expired', 'mismatch', 'failed'} or status_code in {'0', '-1', 'cancelled', 'canceled', 'expired', 'failed'}


def extract_paid_usd(data: dict[str, Any]) -> float:
    payload = _extract_data(data)
    for key in ('source_amount', 'source_sum', 'invoice_total_sum', 'invoice_sum'):
        value = payload.get(key) if isinstance(payload, dict) else None
        if value not in (None, ''):
            try:
                return float(str(value).replace(',', ''))
            except Exception:
                pass
    return 0.0


def validate_callback(data: dict[str, Any]) -> bool:
    """Validate Plisio callback verify_hash when present.

    Official SDK validates by removing verify_hash, JSON-compacting the remaining
    data and comparing HMAC-SHA1 with the shop API key.
    """
    verify_hash = data.get('verify_hash')
    if not verify_hash:
        # Some setups may not send verify_hash; don't reject, but record signature_valid=False upstream.
        return False
    payload = dict(data)
    payload.pop('verify_hash', None)
    post_str = json.dumps(payload, separators=(',', ':'))
    digest = hmac.new(get_api_key().encode('utf-8'), post_str.encode('utf-8'), hashlib.sha1).hexdigest()
    return hmac.compare_digest(str(verify_hash), digest)


async def create_invoice(
    *,
    order_number: str,
    amount_toman: int,
    order_name: str,
    description: str,
    email: str | None = None,
) -> dict[str, Any]:
    if not is_enabled():
        raise PlisioError('تنظیمات Plisio کامل نیست. PLISIO_API_KEY و PLISIO_CALLBACK_URL را در .env تنظیم کنید.')
    if int(amount_toman) <= 0:
        raise PlisioError('مبلغ Plisio باید بزرگتر از صفر باشد.')
    source_amount, usd_rate_toman, source_amount_float = toman_to_usd_amount(int(amount_toman))
    min_usd = get_min_usd_amount()
    if Decimal(str(source_amount)) < min_usd:
        raise PlisioMinimumAmountError(source_amount_float, float(min_usd))
    params: dict[str, Any] = {
        'api_key': get_api_key(),
        'currency': get_default_currency(),
        'order_number': str(order_number),
        'order_name': str(order_name),
        'source_currency': 'USD',
        'source_amount': source_amount,
        'description': str(description),
        'callback_url': callback_url_json(),
        'email': email or str(db.get_setting('plisio_default_email', getattr(settings, 'plisio_default_email', 'customer@example.com') or 'customer@example.com')),
        'language': 'en_US',
        'expire_min': get_expire_min(),
        'plugin': 'pasarguard-admin-bot',
        'version': '1.0',
        'return_existing': 1,
    }
    allowed = get_allowed_psys_cids()
    if allowed:
        params['allowed_psys_cids'] = allowed
    success_url = get_success_url()
    fail_url = get_fail_url()
    if success_url:
        params['success_invoice_url'] = success_url
        params['success_callback_url'] = success_url
    if fail_url:
        params['fail_invoice_url'] = fail_url
        params['fail_callback_url'] = fail_url
    url = get_api_base_url() + '/invoices/new'
    timeout = aiohttp.ClientTimeout(total=30)
    # Reuse shared keep-alive session
    try:
        from api_client import get_shared_session
        shared = await get_shared_session()
    except Exception:
        shared = None
    if shared is not None and not shared.closed:
        async with shared.get(url, params=params, timeout=timeout) as resp:
            text = await resp.text()
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {'raw': text}
            if resp.status >= 400:
                raise PlisioError(f'خطای ساخت فاکتور Plisio: HTTP {resp.status} - {text[:500]}')
    else:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {'raw': text}
                if resp.status >= 400:
                    raise PlisioError(f'خطای ساخت فاکتور Plisio: HTTP {resp.status} - {text[:500]}')
    if str(data.get('status', '')).lower() == 'error':
        raise PlisioError(f'Plisio خطای ساخت فاکتور داد: {str(data)[:700]}')
    invoice_url = extract_invoice_url(data)
    txn_id = extract_txn_id(data)
    if not invoice_url or not txn_id:
        raise PlisioError(f'لینک یا txn_id از پاسخ Plisio پیدا نشد: {str(data)[:700]}')
    return {
        'request': {k: v for k, v in params.items() if k != 'api_key'},
        'response': data,
        'payment_url': invoice_url,
        'invoice_url': invoice_url,
        'txn_id': txn_id,
        'provider_invoice_id': txn_id,
        'order_number': str(order_number),
        'source_amount_usd': source_amount,
        'usd_rate_toman': usd_rate_toman,
        'source_amount_float': source_amount_float,
    }


async def get_operation(txn_id: str) -> dict[str, Any]:
    if not get_api_key():
        raise PlisioError('PLISIO_API_KEY تنظیم نشده است.')
    url = get_api_base_url() + f'/operations/{txn_id}'
    params = {'api_key': get_api_key()}
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        from api_client import get_shared_session
        shared = await get_shared_session()
    except Exception:
        shared = None
    if shared is not None and not shared.closed:
        async with shared.get(url, params=params, timeout=timeout) as resp:
            text = await resp.text()
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {'raw': text}
            data['_http_status'] = resp.status
            return data
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            text = await resp.text()
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {'raw': text}
            data['_http_status'] = resp.status
            return data
