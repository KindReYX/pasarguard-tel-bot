from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp

import db
from config import settings

DEFAULT_BASE_URL = 'http://example.com'
DEFAULT_ITEM = 'usd_sell'
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def _setting_str(key: str, default: str = '') -> str:
    value = db.get_setting(key, default)
    return str(value or '').strip()


def get_api_key() -> str:
    # Super admin can update this from inside the bot. .env is used as fallback.
    return _setting_str('navasan_api_key', getattr(settings, 'navasan_api_key', '') or '')


def get_base_url() -> str:
    return _setting_str('navasan_base_url', getattr(settings, 'navasan_base_url', '') or DEFAULT_BASE_URL).rstrip('/')


def get_item() -> str:
    return _setting_str('navasan_usd_item', getattr(settings, 'navasan_usd_item', '') or DEFAULT_ITEM)


def _to_int_rate(value: Any) -> int:
    text = str(value or '').strip().replace(',', '').replace('٬', '').replace(' ', '')
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:
        return 0


def _extract_rate(data: Any, item: str) -> tuple[int, dict[str, Any]]:
    if not isinstance(data, dict):
        return 0, {}
    payload: Any = data.get(item)
    if payload is None and 'value' in data:
        payload = data
    if payload is None:
        # Fallback: find first object with a value field.
        for value in data.values():
            if isinstance(value, dict) and 'value' in value:
                payload = value
                break
    if isinstance(payload, dict):
        rate = _to_int_rate(payload.get('value'))
        return rate, payload
    rate = _to_int_rate(payload)
    return rate, {'value': payload}


def _store_rate(rate_toman: int, payload: dict[str, Any], source: str = 'navasan') -> None:
    global _RATE_CACHE
    _RATE_CACHE = None
    now = db.now_iso()
    db.set_setting('navasan_rate_toman', int(rate_toman))
    db.set_setting('navasan_rate_rial', int(rate_toman) * 10)
    db.set_setting('navasan_rate_payload', payload)
    db.set_setting('navasan_last_fetch_at', now)
    db.set_setting('navasan_last_error', '')
    db.set_setting('crypto_usd_rate_mode', source)
    db.set_setting('crypto_usd_toman_rate', int(rate_toman))
    db.set_setting('crypto_usd_rial_rate', int(rate_toman) * 10)
    db.set_setting('crypto_usd_rate_updated_at', now)
    db.set_setting('crypto_usd_rate_source', source)
    if payload.get('timestamp') is not None:
        db.set_setting('navasan_rate_timestamp', payload.get('timestamp'))
    if payload.get('date') is not None:
        db.set_setting('navasan_rate_date', payload.get('date'))


_RATE_CACHE: tuple[float, int] | None = None  # (expires, rate)
_RATE_TTL = 30.0

async def fetch_usd_rate_from_navasan(force_api_key: str | None = None) -> dict[str, Any]:
    api_key = (force_api_key or get_api_key()).strip()
    if not api_key:
        raise ValueError('کلید API نوسان تنظیم نشده است.')
    item = get_item()
    base_url = get_base_url()
    url = f"{base_url}/latest/?{urlencode({'api_key': api_key, 'item': item})}"
    timeout = aiohttp.ClientTimeout(total=25)
    try:
        from api_client import get_shared_session
        shared = await get_shared_session()
    except Exception:
        shared = None
    if shared is not None and not shared.closed:
        async with shared.get(url, timeout=timeout) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f'Navasan HTTP {response.status}: {text[:500]}')
            try:
                data = await response.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(f'Navasan invalid JSON: {text[:500]}') from exc
    else:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                text = await response.text()
                if response.status != 200:
                    raise RuntimeError(f'Navasan HTTP {response.status}: {text[:500]}')
                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    raise RuntimeError(f'Navasan invalid JSON: {text[:500]}') from exc
    rate, payload = _extract_rate(data, item)
    if rate <= 0:
        raise RuntimeError(f'نرخ دلار از پاسخ نوسان قابل استخراج نبود: {str(data)[:500]}')
    _store_rate(rate, payload, source='navasan')
    return {
        'rate_toman': rate,
        'rate_rial': rate * 10,
        'item': item,
        'payload': payload,
        'raw': data,
    }


async def update_rate_and_log(source: str = 'scheduled') -> bool:
    if not bool(db.get_setting('navasan_auto_enabled', True)):
        return False
    try:
        result = await fetch_usd_rate_from_navasan()
        db.add_log(None, 'navasan_rate_updated', 'settings', 'crypto_usd_toman_rate', {
            'source': source,
            'rate_toman': result['rate_toman'],
            'item': result['item'],
        })
        return True
    except Exception as exc:
        msg = str(exc)
        db.set_setting('navasan_last_error', msg)
        db.set_setting('navasan_last_error_at', db.now_iso())
        db.record_system_error('navasan.update_rate', msg, error_type=type(exc).__name__)
        return False


def get_last_usd_rate_toman() -> int:
    global _RATE_CACHE
    now = asyncio.get_event_loop().time() if asyncio._get_running_loop() else 0  # fallback
    try:
        import time as _t
        now = _t.monotonic()
    except Exception:
        pass
    if _RATE_CACHE is not None:
        exp, val = _RATE_CACHE
        if now < exp and val > 0:
            return val
    val = int(db.get_setting('crypto_usd_toman_rate', 0) or db.get_setting('navasan_rate_toman', 0) or 0)
    try:
        import time as _t
        _RATE_CACHE = (_t.monotonic() + _RATE_TTL, val)
    except Exception:
        pass
    return val


def toman_to_usd(amount_toman: int, margin_percent: float | None = None) -> tuple[float, int]:
    rate = get_last_usd_rate_toman()
    if rate <= 0:
        manual = int(db.get_setting('crypto_usd_toman_manual', 0) or 0)
        rate = manual
    if rate <= 0:
        raise ValueError('نرخ دلار برای پرداخت کریپتو تنظیم نشده است.')
    margin = float(margin_percent if margin_percent is not None else db.get_setting('crypto_usd_margin_percent', 0) or 0)
    usd = int(amount_toman) / rate
    if margin:
        usd = usd * (1 + margin / 100)
    return round(usd, 2), rate


def format_status_text() -> str:
    rate = get_last_usd_rate_toman()
    rial = int(db.get_setting('crypto_usd_rial_rate', 0) or (rate * 10 if rate else 0))
    item = get_item()
    auto = bool(db.get_setting('navasan_auto_enabled', True))
    last_fetch = db.get_setting('navasan_last_fetch_at', 'ثبت نشده')
    date = db.get_setting('navasan_rate_date', '-')
    last_error = db.get_setting('navasan_last_error', '')
    key_status = 'ثبت شده' if get_api_key() else 'ثبت نشده'
    text = (
        '💵 نرخ دلار برای پرداخت کریپتو\n\n'
        f'وضعیت دریافت خودکار: {"روشن" if auto else "خاموش"}\n'
        f'کلید API نوسان: {key_status}\n'
        f'آیتم نوسان: `{item}`\n'
        f'آخرین نرخ: `{rate:,}` تومان | `{rial:,}` ریال\n'
        f'زمان آخرین دریافت ربات: `{last_fetch}`\n'
        f'زمان اعلام شده نوسان: `{date}`\n'
        'زمان‌بندی: هر روز ساعت ۱۲ ظهر و ۱۲ شب به وقت تهران\n'
    )
    if last_error:
        text += f'\nآخرین خطا: `{str(last_error)[:800]}`'
    return text


def should_run_scheduled_fetch(now: datetime | None = None) -> bool:
    now = now or datetime.now(TEHRAN_TZ)
    # Run shortly after 00:00 and 12:00 Tehran time. Store slot key to avoid duplicates.
    if now.minute > 10:
        return False
    if now.hour not in (0, 12):
        return False
    slot = now.strftime('%Y-%m-%d-%H')
    if db.get_setting('navasan_last_schedule_slot', '') == slot:
        return False
    db.set_setting('navasan_last_schedule_slot', slot)
    return True


async def scheduled_rate_loop() -> None:
    # Quick startup sync if no saved rate exists.
    await asyncio.sleep(5)
    if bool(db.get_setting('navasan_fetch_on_startup', True)) and get_api_key() and get_last_usd_rate_toman() <= 0:
        await update_rate_and_log('startup')
    while True:
        try:
            if should_run_scheduled_fetch():
                await update_rate_and_log('schedule')
        except Exception as exc:
            logging.exception('Navasan scheduler failed')
            db.record_system_error('navasan.scheduled_rate_loop', str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(60)
