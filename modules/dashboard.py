from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_DASHBOARD, back_keyboard
from permissions import is_admin, deny_message, get_role
from utils import fmt_money


dashboard_router = Router()


def _get_dashboard_stats_cached() -> dict:
    cached = db.cache_get('dashboard:stats:v2')
    if isinstance(cached, dict):
        return cached
    # Single round-trip aggregation instead of 6 separate queries
    row = db.fetchone('''
        SELECT
            (SELECT COUNT(*) FROM bot_users) AS total_users,
            (SELECT COUNT(*) FROM bot_users WHERE is_active=1) AS active_users,
            (SELECT COUNT(*) FROM bot_users WHERE is_blocked=1) AS blocked_users,
            (SELECT COUNT(*) FROM orders) AS orders_total,
            (SELECT COUNT(*) FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready')) AS orders_paid,
            (SELECT COUNT(*) FROM tickets WHERE status!='closed') AS unread_tickets,
            (SELECT COUNT(*) FROM notifications WHERE is_read=0) AS unread_notif
    ''') or {}
    # Sales sums still need date filter; keep cached separate with short TTL
    sales7 = db.cache_get('dashboard:sales:7')
    if sales7 is None:
        sales7 = db.sales_sum(7)
        try:
            db.cache_set('dashboard:sales:7', sales7, 60)
        except Exception:
            pass
    sales30 = db.cache_get('dashboard:sales:30')
    if sales30 is None:
        sales30 = db.sales_sum(30)
        try:
            db.cache_set('dashboard:sales:30', sales30, 60)
        except Exception:
            pass
    out = dict(row)
    out['sales7'] = sales7
    out['sales30'] = sales30
    try:
        db.cache_set('dashboard:stats:v2', out, 25)
    except Exception:
        pass
    return out


@dashboard_router.message(EditableButtonFilter(BTN_DASHBOARD))
async def dashboard(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        await deny_message(message)
        return
    stats = _get_dashboard_stats_cached()
    text = (
        '📊 داشبورد مدیریت\n\n'
        f'نقش: {get_role(uid)}\n'
        f'کل کاربران ربات: {stats.get("total_users",0)}\n'
        f'کاربران فعال: {stats.get("active_users",0)}\n'
        f'کاربران مسدود: {stats.get("blocked_users",0)}\n\n'
        f'کل سفارش‌ها: {stats.get("orders_total",0)}\n'
        f'سفارش‌های موفق: {stats.get("orders_paid",0)}\n'
        f'فروش ۷ روز اخیر: {fmt_money(stats.get("sales7",0))}\n'
        f'فروش ۳۰ روز اخیر: {fmt_money(stats.get("sales30",0))}\n\n'
        f'تیکت‌های باز: {stats.get("unread_tickets",0)}\n'
        f'اعلان‌های خوانده نشده: {stats.get("unread_notif",0)}\n'
    )
    await message.answer(text, reply_markup=back_keyboard())
