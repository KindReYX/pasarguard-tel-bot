from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def parse_ids(raw: str | None) -> set[int]:
    ids: set[int] = set()
    if not raw:
        return ids
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError:
            raise ValueError(f'Invalid Telegram ID in env: {item}')
    return ids


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str
    api_base_url: str
    panel_admin_username: str
    panel_admin_password: str
    super_admin_ids: set[int]
    admin_ids: set[int]
    seller_ids: set[int]
    support_username: str
    subscription_base_url: str
    payment_webhook_secret: str
    tetrapay_api_key: str
    tetrapay_create_invoice_url: str
    tetrapay_verify_url: str
    tetrapay_callback_url: str
    tetrapay_callback_secret: str
    tetrapay_default_email: str
    tetrapay_default_mobile: str
    payment_webhook_host: str
    payment_webhook_port: int
    crypto_provider_api_key: str
    navasan_api_key: str
    navasan_base_url: str
    navasan_usd_item: str
    plisio_api_key: str
    plisio_api_base_url: str
    plisio_callback_url: str
    plisio_success_url: str
    plisio_fail_url: str
    plisio_default_currency: str
    plisio_allowed_psys_cids: str
    plisio_default_email: str
    plisio_expire_min: int
    plisio_min_usd_amount: float


settings = Settings(
    bot_token=os.getenv('BOT_TOKEN', '').strip(),
    db_path=os.getenv('DB_PATH', 'bot.db').strip(),
    api_base_url=os.getenv('API_BASE_URL', '').strip().rstrip('/'),
    panel_admin_username=os.getenv('PANEL_ADMIN_USERNAME', '').strip(),
    panel_admin_password=os.getenv('PANEL_ADMIN_PASSWORD', '').strip(),
    super_admin_ids=parse_ids(os.getenv('SUPER_ADMIN_IDS')),
    admin_ids=parse_ids(os.getenv('ADMIN_IDS')),
    seller_ids=parse_ids(os.getenv('SELLER_IDS')),
    support_username=os.getenv('SUPPORT_USERNAME', '').strip(),
    subscription_base_url=os.getenv('SUBSCRIPTION_BASE_URL', '').strip().rstrip('/'),
    payment_webhook_secret=os.getenv('PAYMENT_WEBHOOK_SECRET', '').strip(),
    tetrapay_api_key=os.getenv('TETRAPAY_API_KEY', '').strip(),
    tetrapay_create_invoice_url=os.getenv('TETRAPAY_CREATE_INVOICE_URL', '').strip(),
    tetrapay_verify_url=os.getenv('TETRAPAY_VERIFY_URL', '').strip(),
    tetrapay_callback_url=os.getenv('TETRAPAY_CALLBACK_URL', '').strip(),
    tetrapay_callback_secret=os.getenv('TETRAPAY_CALLBACK_SECRET', os.getenv('PAYMENT_WEBHOOK_SECRET', '')).strip(),
    tetrapay_default_email=os.getenv('TETRAPAY_DEFAULT_EMAIL', 'customer@example.com').strip(),
    tetrapay_default_mobile=os.getenv('TETRAPAY_DEFAULT_MOBILE', '09000000000').strip(),
    payment_webhook_host=os.getenv('PAYMENT_WEBHOOK_HOST', '127.0.0.1').strip(),
    payment_webhook_port=int(os.getenv('PAYMENT_WEBHOOK_PORT', '8081') or 8081),
    crypto_provider_api_key=os.getenv('CRYPTO_PROVIDER_API_KEY', '').strip(),
    navasan_api_key=os.getenv('NAVASAN_API_KEY', '').strip(),
    navasan_base_url=os.getenv('NAVASAN_BASE_URL', 'http://example.com').strip().rstrip('/'),
    navasan_usd_item=os.getenv('NAVASAN_USD_ITEM', 'usd_sell').strip(),
    plisio_api_key=os.getenv('PLISIO_API_KEY', os.getenv('CRYPTO_PROVIDER_API_KEY', '')).strip(),
    plisio_api_base_url=os.getenv('PLISIO_API_BASE_URL', 'https://example.com/api/v1').strip().rstrip('/'),
    plisio_callback_url=os.getenv('PLISIO_CALLBACK_URL', '').strip(),
    plisio_success_url=os.getenv('PLISIO_SUCCESS_URL', '').strip(),
    plisio_fail_url=os.getenv('PLISIO_FAIL_URL', '').strip(),
    plisio_default_currency=os.getenv('PLISIO_DEFAULT_CURRENCY', 'USDT_TRX').strip(),
    plisio_allowed_psys_cids=os.getenv('PLISIO_ALLOWED_PSYS_CIDS', 'USDT_TRX,USDT_BSC,BTC,ETH,LTC,TRX').strip(),
    plisio_default_email=os.getenv('PLISIO_DEFAULT_EMAIL', 'customer@example.com').strip(),
    plisio_expire_min=int(os.getenv('PLISIO_EXPIRE_MIN', '60') or 60),
    plisio_min_usd_amount=float(os.getenv('PLISIO_MIN_USD_AMOUNT', '5.01') or 5.01),
)


def validate_settings() -> None:
    if not settings.bot_token:
        raise ValueError('BOT_TOKEN is missing in .env')
    if not settings.super_admin_ids:
        raise ValueError('SUPER_ADMIN_IDS is missing in .env')
    if not settings.api_base_url:
        raise ValueError('API_BASE_URL is missing in .env')
    if not settings.panel_admin_username:
        raise ValueError('PANEL_ADMIN_USERNAME is missing in .env')
    if not settings.panel_admin_password:
        raise ValueError('PANEL_ADMIN_PASSWORD is missing in .env')
