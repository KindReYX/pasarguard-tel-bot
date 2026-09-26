#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def ok(msg: str): print(f'[OK] {msg}')
def warn(msg: str): print(f'[WARN] {msg}')
def fail(msg: str): print(f'[FAIL] {msg}')

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--migrate', action='store_true', help='run db.init_db() before checks')
    args = parser.parse_args()
    errors = 0
    env_file = ROOT / '.env'
    if not env_file.exists():
        fail(f'.env not found: {env_file}'); return 2
    try:
        from config import settings, validate_settings
        validate_settings(); ok('core .env settings')
    except Exception as exc:
        fail(f'core settings: {exc}'); return 2
    try:
        import db
        if args.migrate:
            db.init_db(); ok('database migrations')
        db_path = Path(settings.db_path)
        if not db_path.is_absolute(): db_path = ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            result = conn.execute('PRAGMA integrity_check').fetchone()
            if not result or str(result[0]).lower() != 'ok':
                fail(f'database integrity_check: {result}'); errors += 1
            else: ok('database integrity_check')
        if os.access(db_path.parent, os.W_OK): ok('database directory writable')
        else: fail(f'database directory not writable: {db_path.parent}'); errors += 1

        # Operational warnings: do not block launch because gateways can be disabled from bot.
        if bool(db.get_setting('payment_tetrapay_enabled', True)):
            from modules import tetrapay
            missing=[]
            if not tetrapay.get_api_key(): missing.append('API key')
            if not tetrapay.get_create_invoice_url(): missing.append('create URL')
            if not tetrapay.get_verify_url(): missing.append('verify URL')
            if not tetrapay.get_callback_url(): missing.append('callback URL')
            if missing: warn('TetraPay enabled but missing: ' + ', '.join(missing))
            else: ok('TetraPay configuration')
        if bool(db.get_setting('payment_plisio_enabled', True)):
            from modules import plisio_pay
            missing=[]
            if not plisio_pay.get_api_key(): missing.append('API key')
            if not plisio_pay.get_callback_url(): missing.append('callback URL')
            if missing: warn('Plisio enabled but missing: ' + ', '.join(missing))
            else: ok('Plisio configuration')
        if bool(db.get_setting('payment_card_enabled', False)):
            for key,label in [('card_number','card number'),('card_holder','card holder')]:
                if not str(db.get_setting(key,'') or '').strip(): warn(f'Card transfer enabled but {label} is empty')
        if bool(db.get_setting('navasan_auto_enabled', True)) and not str(db.get_setting('navasan_api_key', settings.navasan_api_key) or '').strip():
            warn('Navasan auto update enabled but API key is empty')
    except Exception as exc:
        fail(f'database/runtime preflight: {type(exc).__name__}: {exc}'); errors += 1

    try:
        import aiogram
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message
        InlineKeyboardButton(text='probe', callback_data='probe', style='primary', icon_custom_emoji_id='6068773293006000235')
        KeyboardButton(text='probe', style='success', icon_custom_emoji_id='6068773293006000235')
        from utils import split_button_custom_emoji, render_custom_emoji_output, normalize_custom_emoji_markup, format_display_prices, display_template_value
        probe_id = '6068773293006000235'
        probe_text, probe_emoji_id = split_button_custom_emoji(f'[{probe_id}] probe')
        if probe_text != 'probe' or probe_emoji_id != probe_id:
            raise RuntimeError('premium button emoji parser failed')
        import asyncio
        rendered_probe, rendered_mode = asyncio.run(render_custom_emoji_output(None, f'before [{probe_id}] after', None))
        if f'[{probe_id}]' in rendered_probe or f'emoji-id="{probe_id}"' not in rendered_probe or rendered_mode != 'HTML':
            raise RuntimeError('global premium message renderer failed')
        from utils import render_template_html
        copy_probe = asyncio.run(render_template_html(None, 'card={card_number} sub={sub_link} id={order_id}', {
            'card_number': '1111222233334444', 'sub_link': 'https://example.com/sub?a=1&b=2', 'order_id': 42,
        }))
        if '<code>1111222233334444</code>' not in copy_probe or '<code>42</code>' not in copy_probe or '<code>https://example.com/sub?a=1&amp;b=2</code>' not in copy_probe:
            raise RuntimeError('copyable template variable renderer failed')
        if format_display_prices('مبلغ: 145000 تومان | آیدی: 1234567890 | کارت: 1111222233334444') != 'مبلغ: 145,000 تومان | آیدی: 1234567890 | کارت: 1111222233334444':
            raise RuntimeError('display-only money formatter failed')
        if display_template_value('package_price_raw', 145000) != '145,000' or display_template_value('user_id', 145000) != '145000':
            raise RuntimeError('template money variable formatter failed')
        probe_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'[{probe_id}] probe', callback_data='probe2')]])
        fixed_markup = normalize_custom_emoji_markup(probe_markup)
        fixed_button = fixed_markup.inline_keyboard[0][0]
        if fixed_button.text != 'probe' or str(fixed_button.icon_custom_emoji_id or '') != probe_id:
            raise RuntimeError('global premium keyboard normalizer failed')
        from modules import tutorials as tutorials_module
        tutorials_module.user_tutorials_keyboard()
        from modules import ad_tracking as ad_tracking_module
        from modules import edit_mode as edit_mode_module
        from modules import edit_runtime as edit_runtime_module
        from modules import tasks as tasks_module
        from modules.customer import package_username_prompt_context
        from keyboards import EditableButtonFilter
        if not hasattr(ad_tracking_module, 'ad_tracking_router'):
            raise RuntimeError('ad tracking router missing')
        if not hasattr(edit_mode_module, 'edit_router'):
            raise RuntimeError('live edit-mode router missing')
        if not hasattr(edit_runtime_module, 'output_passthrough') or not hasattr(edit_runtime_module, 'output_passthrough_active'):
            raise RuntimeError('atomic output passthrough missing')
        if '{package_name}' not in tasks_module.RENEW_WARNING_DEFAULT or '{service_name}' not in tasks_module.RENEW_WARNING_DEFAULT or '{remaining_time}' not in tasks_module.RENEW_WARNING_DEFAULT:
            raise RuntimeError('renew warning template variables are incomplete')
        if tasks_module._remaining_text(7200) != '2 ساعت':
            raise RuntimeError('renew warning remaining-time formatter failed')
        if edit_runtime_module.button_stable_key('probe', 'probe:1') != edit_runtime_module.button_stable_key('changed label', 'probe:1'):
            raise RuntimeError('callback button identity is not stable across label changes')
        if not callable(EditableButtonFilter):
            raise RuntimeError('editable reply-button filter missing')
        package_ctx = package_username_prompt_context(
            {'id': 5, 'name': 'Probe', 'data_limit': 10 * 1024**3, 'expire_duration': 30 * 86400},
            {'template_id': '5', 'price_amount': 245000},
        )
        required_package_vars = {'package_name', 'package_id', 'package_price', 'package_price_raw', 'package_data_limit', 'package_duration', 'price', 'data_limit', 'duration'}
        if not required_package_vars.issubset(package_ctx):
            raise RuntimeError('package prompt variables are incomplete')
        if not hasattr(Message, 'answer_invoice'):
            raise RuntimeError('invoice message API missing')
        ok(f'aiogram {getattr(aiogram, "__version__", "unknown")} + tutorial/button style/premium emoji/ad tracking/live editor/package variables support')
    except Exception as exc:
        fail(f'aiogram/tutorial import: {exc}'); errors += 1
    try:
        import aiohttp, dotenv, qrcode, openpyxl
        ok('required Python imports')
    except Exception as exc:
        fail(f'Python dependency import: {exc}'); errors += 1

    if errors:
        print(f'Preflight failed with {errors} blocking error(s).')
        return 1
    print('Preflight passed. Review WARN lines before public launch.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
