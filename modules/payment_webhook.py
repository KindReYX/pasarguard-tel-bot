from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

import db
from config import settings
from keyboards import customer_keyboard
from utils import friendly_error
from modules import tetrapay, plisio_pay
import modules.payment_engine as payment_engine


class BotChatSender:
    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = int(chat_id)

    async def answer(self, text: str, **kwargs):
        return await self.bot.send_message(self.chat_id, text, **kwargs)

    async def answer_photo(self, photo, **kwargs):
        return await self.bot.send_photo(self.chat_id, photo, **kwargs)


async def health(_: web.Request) -> web.Response:
    return web.Response(text='ok')



async def plisio_success(_: web.Request) -> web.Response:
    return web.Response(text='Plisio payment received. You can return to Telegram bot.')


async def plisio_fail(_: web.Request) -> web.Response:
    return web.Response(text='Plisio payment failed or cancelled. You can return to Telegram bot.')

async def _request_data(request: web.Request) -> tuple[dict[str, Any], str]:
    raw = await request.text()
    data: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data.update(parsed)
        except Exception:
            pass
    if not data:
        try:
            post = await request.post()
            data.update({k: v for k, v in post.items()})
        except Exception:
            pass
    data.update(dict(request.query))
    return data, raw or json.dumps(data, ensure_ascii=False)


def _secret_ok(request: web.Request) -> bool:
    secret = tetrapay.get_callback_secret().strip()
    if not secret:
        return True
    received = request.query.get('secret') or request.headers.get('X-Webhook-Secret') or request.headers.get('X-TetraPay-Secret')
    return received == secret


def _parse_hash(hash_id: str) -> tuple[str | None, int | None]:
    raw = str(hash_id or '').strip()
    if raw.startswith('order_'):
        tail = raw.split('_', 1)[1]
        return 'order', int(tail) if tail.isdigit() else None
    if raw.startswith('wallet_'):
        tail = raw.split('_', 1)[1]
        return 'wallet', int(tail) if tail.isdigit() else None
    # Backward compatible fallbacks: order-123, invoice_123, plain 123.
    digits = ''.join(ch if ch.isdigit() else ' ' for ch in raw).split()
    if digits:
        return 'order', int(digits[-1])
    return None, None


async def tetrapay_callback(request: web.Request) -> web.Response:
    bot: Bot = example.com['bot']
    data, raw_body = await _request_data(request)
    if not _secret_ok(request):
        db.record_payment_webhook('tetrapay', 'bad-secret-' + db.now_iso(), None, None, 'bad_secret', False, raw_body)
        return web.json_response({'ok': False, 'error': 'bad_secret'}, status=403)

    hash_id = tetrapay.extract_hash_id(data)
    if not hash_id:
        db.record_payment_webhook('tetrapay', 'missing-hash-' + db.now_iso(), None, None, 'missing_hash_id', True, raw_body)
        return web.json_response({'ok': False, 'error': 'missing_hash_id'}, status=400)

    target_type, target_id = _parse_hash(hash_id)
    if not target_type or not target_id:
        db.record_payment_webhook('tetrapay', f'unknown-{hash_id}', None, None, 'unknown_hash_id', True, raw_body)
        return web.json_response({'ok': False, 'error': 'unknown_hash_id'}, status=400)

    authority = tetrapay.extract_authority(data)
    if not authority:
        db.record_payment_webhook('tetrapay', f'missing-authority-{hash_id}', target_id if target_type == 'order' else None, None, 'missing_authority', True, raw_body)
        return web.json_response({'ok': False, 'error': 'missing_authority'}, status=400)

    amount = tetrapay.extract_amount(data)
    tracking_id = tetrapay.extract_tracking_id(data) or authority
    event_id = f'tetrapay:{hash_id}:{authority}'
    inserted = db.record_payment_webhook('tetrapay', event_id, target_id if target_type == 'order' else None, amount, 'received', True, raw_body)
    if not inserted:
        return web.json_response({'ok': True, 'duplicate': True})

    try:
        if target_type == 'order':
            order = db.get_order(target_id)
            if not order:
                raise RuntimeError(f'Order not found: {target_id}')
            expected = int(order.get('payable_toman') or max(0, int(order.get('amount_toman') or order.get('amount') or 0) - int(order.get('wallet_used') or 0)))
            expected_authority = str(order.get('provider_invoice_id') or '')
            if expected_authority and expected_authority != str(authority):
                raise RuntimeError(f'Authority با سفارش همخوانی ندارد. expected={expected_authority}, received={authority}')
            verify_data = await tetrapay.verify_payment(authority)
            source = verify_data or data
            if not tetrapay.callback_is_success(source):
                raise RuntimeError('پرداخت توسط تتراپی موفق گزارش نشده است.')
            paid_amount = tetrapay.extract_amount(source) or amount
            if paid_amount and int(paid_amount) < expected:
                raise RuntimeError(f'مبلغ پرداخت کمتر از مبلغ سفارش است. expected={expected}, paid={paid_amount}')
            if order.get('status') == 'service_ready':
                return web.json_response({'ok': True, 'already_ready': True})
            db.mark_order_provider_paid(target_id, 'tetrapay', expected, tracking_id or authority, source)
            refreshed = db.get_order(target_id) or order
            from modules.customer import finish_paid_order
            sender = BotChatSender(bot, int(refreshed['user_id']))
            await finish_paid_order(sender, refreshed, 0, None, tracking_id)
            return web.json_response({'ok': True, 'type': 'order', 'id': target_id})

        if target_type == 'wallet':
            req = db.get_wallet_charge_request(target_id)
            if not req:
                raise RuntimeError(f'Wallet charge request not found: {target_id}')
            expected = int(req.get('amount_toman') or 0)
            expected_authority = str(req.get('provider_invoice_id') or '')
            if expected_authority and expected_authority != str(authority):
                raise RuntimeError(f'Authority با درخواست شارژ همخوانی ندارد. expected={expected_authority}, received={authority}')
            verify_data = await tetrapay.verify_payment(authority)
            source = verify_data or data
            if not tetrapay.callback_is_success(source):
                raise RuntimeError('پرداخت شارژ کیف پول موفق گزارش نشده است.')
            paid_amount = tetrapay.extract_amount(source) or amount
            if paid_amount and int(paid_amount) < expected:
                raise RuntimeError(f'مبلغ پرداخت کمتر از مبلغ شارژ است. expected={expected}, paid={paid_amount}')
            db.mark_wallet_charge_paid_provider(target_id, 'tetrapay', tracking_id or authority, source)
            balance = db.wallet_balance(int(req['user_id']))
            from modules.customer import wallet_charge_result_text
            await bot.send_message(int(req['user_id']), wallet_charge_result_text(target_id, balance), reply_markup=customer_keyboard())
            return web.json_response({'ok': True, 'type': 'wallet', 'id': target_id})

        return web.json_response({'ok': False, 'error': 'unsupported_type'}, status=400)
    except Exception as exc:
        logging.exception('TetraPay callback processing failed')
        db.record_system_error('payment_webhook.tetrapay_callback', str(exc), entity_type=target_type, entity_id=str(target_id), error_type=type(exc).__name__, details=data)
        try:
            if target_type == 'order':
                order = db.get_order(target_id)
                if order:
                    await bot.send_message(int(order['user_id']), friendly_error('پرداخت دریافت شد اما پردازش سفارش نیازمند بررسی است. به پشتیبانی پیام بدهید.'), reply_markup=customer_keyboard())
            elif target_type == 'wallet':
                req = db.get_wallet_charge_request(target_id)
                if req:
                    await bot.send_message(int(req['user_id']), friendly_error('پرداخت دریافت شد اما شارژ کیف پول نیازمند بررسی است. به پشتیبانی پیام بدهید.'), reply_markup=customer_keyboard())
        except TelegramBadRequest:
            pass
        return web.json_response({'ok': False, 'error': str(exc)}, status=500)


def _parse_plisio_order_number(order_number: str) -> tuple[str | None, int | None]:
    raw = str(order_number or '').strip()
    if raw.startswith('order_'):
        tail = raw.split('_', 1)[1]
        return 'order', int(tail) if tail.isdigit() else None
    if raw.startswith('wallet_'):
        tail = raw.split('_', 1)[1]
        return 'wallet', int(tail) if tail.isdigit() else None
    digits = ''.join(ch if ch.isdigit() else ' ' for ch in raw).split()
    if digits:
        return 'order', int(digits[-1])
    return None, None


async def plisio_callback(request: web.Request) -> web.Response:
    bot: Bot = example.com['bot']
    data, raw_body = await _request_data(request)
    order_number = plisio_pay.extract_order_number(data)
    txn_id = plisio_pay.extract_txn_id(data) or str(data.get('txn_id') or data.get('id') or '')
    event_id = f"plisio:{txn_id or 'no-txn'}:{order_number or 'no-order'}:{db.now_iso()}"
    signature_valid = plisio_pay.validate_callback(data)
    target_type, target_id = _parse_plisio_order_number(order_number or '')
    db.record_payment_webhook('plisio', event_id, target_id if target_type == 'order' else None, None, plisio_pay.extract_status(data) or 'received', signature_valid, raw_body)

    if not signature_valid:
        db.record_system_error('payment_webhook.plisio_callback', 'Invalid or missing Plisio verify_hash', entity_type=target_type, entity_id=str(target_id or ''), error_type='InvalidSignature', details=data)
        return web.json_response({'ok': False, 'error': 'invalid_signature'}, status=403)

    if not order_number or not target_type or not target_id:
        return web.json_response({'ok': False, 'error': 'unknown_order_number'}, status=400)

    try:
        if target_type == 'order':
            order = db.get_order(target_id)
            if not order:
                raise RuntimeError(f'Order not found: {target_id}')
            expected_txn = str(order.get('provider_invoice_id') or '')
            if expected_txn and txn_id and expected_txn != txn_id:
                raise RuntimeError(f'txn_id mismatch. expected={expected_txn}, received={txn_id}')
            if order.get('status') == 'service_ready':
                return web.json_response({'ok': True, 'already_ready': True})
            verified = await plisio_pay.get_operation(txn_id or expected_txn) if (txn_id or expected_txn) else data
            if not plisio_pay.is_success(verified):
                if plisio_pay.is_final_failed(verified):
                    db.set_order_payment_provider(target_id, 'plisio', int(order.get('payable_toman') or 0), expected_txn, order.get('provider_payment_url'), provider_status='failed', raw=verified)
                return web.json_response({'ok': True, 'paid': False, 'status': plisio_pay.extract_status(verified)})
            data = verified
            expected = int(order.get('payable_toman') or max(0, int(order.get('amount_toman') or order.get('amount') or 0) - int(order.get('wallet_used') or 0)))
            db.mark_order_provider_paid(target_id, 'plisio', expected, txn_id or expected_txn, data)
            refreshed = db.get_order(target_id) or order
            from modules.customer import finish_paid_order
            sender = BotChatSender(bot, int(refreshed['user_id']))
            await finish_paid_order(sender, refreshed, 0, None, txn_id or expected_txn)
            return web.json_response({'ok': True, 'type': 'order', 'id': target_id})

        if target_type == 'wallet':
            req = db.get_wallet_charge_request(target_id)
            if not req:
                raise RuntimeError(f'Wallet charge request not found: {target_id}')
            expected_txn = str(req.get('provider_invoice_id') or '')
            if expected_txn and txn_id and expected_txn != txn_id:
                raise RuntimeError(f'txn_id mismatch. expected={expected_txn}, received={txn_id}')
            if str(req.get('status') or '') == 'paid':
                return web.json_response({'ok': True, 'already_paid': True})
            verified = await plisio_pay.get_operation(txn_id or expected_txn) if (txn_id or expected_txn) else data
            if not plisio_pay.is_success(verified):
                if plisio_pay.is_final_failed(verified):
                    db.set_wallet_charge_provider(target_id, 'plisio', expected_txn, req.get('provider_payment_url'), provider_status='failed', raw=verified)
                return web.json_response({'ok': True, 'paid': False, 'status': plisio_pay.extract_status(verified)})
            db.mark_wallet_charge_paid_provider(target_id, 'plisio', txn_id or expected_txn, verified)
            balance = db.wallet_balance(int(req['user_id']))
            from modules.customer import wallet_charge_result_text
            await bot.send_message(int(req['user_id']), wallet_charge_result_text(target_id, balance, 'کیف پول شما با پرداخت رمز ارز شارژ شد ✅'), reply_markup=customer_keyboard())
            return web.json_response({'ok': True, 'type': 'wallet', 'id': target_id})

        return web.json_response({'ok': False, 'error': 'unsupported_type'}, status=400)
    except Exception as exc:
        logging.exception('Plisio callback processing failed')
        db.record_system_error('payment_webhook.plisio_callback', str(exc), entity_type=target_type, entity_id=str(target_id), error_type=type(exc).__name__, details=data)
        return web.json_response({'ok': False, 'error': str(exc)}, status=500)


async def start_payment_webhook_server(bot: Bot):
    if not tetrapay.get_callback_url() and not settings.payment_webhook_secret and not plisio_pay.get_callback_url():
        return None
    app = web.Application()
    app['bot'] = bot
    app.router.add_get('/health', health)
    app.router.add_get('/payments/tetrapay/callback', tetrapay_callback)
    app.router.add_post('/payments/tetrapay/callback', tetrapay_callback)
    app.router.add_get('/payments/plisio/callback', plisio_callback)
    app.router.add_post('/payments/plisio/callback', plisio_callback)
    app.router.add_get('/payments/plisio/success', plisio_success)
    app.router.add_get('/payments/plisio/fail', plisio_fail)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.payment_webhook_host, settings.payment_webhook_port)
    await site.start()
    example.com('Payment webhook server started on %s:%s', settings.payment_webhook_host, settings.payment_webhook_port)
    return runner
