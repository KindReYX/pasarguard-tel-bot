from __future__ import annotations
import hmac, hashlib, json
from typing import Any
import db

ORDER_PENDING_PAYMENT = 'pending_payment'
ORDER_PAID = 'paid'
ORDER_BUILD_QUEUED = 'build_queued'
ORDER_BUILDING = 'building_service'
ORDER_READY = 'service_ready'
ORDER_REVIEW = 'needs_admin_review'
ORDER_CANCELLED = 'cancelled'
ORDER_REFUNDED = 'refunded'

def lock_key_for_order(order_id: int) -> str:
    return f'order:{int(order_id)}:processing'

def begin_order_processing(order_id: int, owner: str = 'payment_engine') -> bool:
    return db.acquire_lock(lock_key_for_order(order_id), owner=owner, ttl_seconds=300)

def finish_order_processing(order_id: int) -> None:
    db.release_lock(lock_key_for_order(order_id))

def mark_paid_and_queue(order_id: int, stars_amount: int = 0, telegram_payment_charge_id: str | None = None, provider_payment_charge_id: str | None = None) -> None:
    order = db.get_order(order_id)
    if not order:
        db.record_system_error('payment_engine.mark_paid_and_queue', 'Order not found', entity_type='order', entity_id=str(order_id))
        return
    if order.get('status') not in (ORDER_READY, ORDER_PAID, ORDER_BUILD_QUEUED, ORDER_BUILDING):
        db.mark_order_paid(order_id, stars_amount, telegram_payment_charge_id, provider_payment_charge_id)
    db.enqueue_service_job(order_id)

def build_receipt(order: dict[str, Any], service_message: str = '') -> str:
    return db.create_invoice_text(order, service_message)

def secure_compare_signature(raw_body: str, secret: str, received_signature: str) -> bool:
    if not secret or not received_signature:
        return False
    digest = hmac.new(secret.encode(), raw_body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, received_signature)

def register_webhook_event(provider: str, event_id: str, raw_body: str, secret: str, received_signature: str, order_id: int | None = None, amount: int | None = None, status: str = 'received') -> bool:
    signature_valid = secure_compare_signature(raw_body, secret, received_signature)
    return db.record_payment_webhook(provider, event_id, order_id, amount, status, signature_valid, raw_body)

def parse_json_body(raw_body: str) -> dict[str, Any]:
    try:
        return json.loads(raw_body or '{}')
    except Exception:
        return {}
