from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_REPORTS, back_keyboard
from permissions import deny_message, is_staff
from utils import fmt_money

reports_router = Router()


@reports_router.message(EditableButtonFilter(BTN_REPORTS))
async def reports_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        await deny_message(message)
        return
    role = db.fetchone('SELECT role FROM bot_admins WHERE telegram_id=? AND is_active=1', (uid,))
    seller_filter = ' AND seller_id=?' if role and role['role'] == 'seller' else ''
    params = (uid,) if seller_filter else ()
    total = db.fetchone(f"SELECT COALESCE(SUM(amount_toman),0) AS s, COUNT(*) AS c FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready'){seller_filter}", params) or {'s': 0, 'c': 0}
    pending = db.fetchone(f"SELECT COUNT(*) AS c FROM orders WHERE status='pending'{seller_filter}", params) or {'c': 0}
    top = db.fetchall(f"SELECT package_name, COUNT(*) AS c, COALESCE(SUM(amount_toman),0) AS s FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready'){seller_filter} GROUP BY package_id, package_name ORDER BY c DESC LIMIT 5", params)
    text = (
        '📈 گزارش فروش\n\n'
        f'فروش کل: {fmt_money(total["s"])}\n'
        f'تعداد سفارش موفق: {total["c"]}\n'
        f'سفارش در انتظار: {pending["c"]}\n'
        f'فروش ۷ روز اخیر: {fmt_money((db.fetchone(f"SELECT COALESCE(SUM(amount_toman),0) AS s FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready') AND paid_at >= datetime('now','-7 day'){seller_filter}", params) or {'s':0})['s'])}\n'
        f'فروش ۳۰ روز اخیر: {fmt_money((db.fetchone(f"SELECT COALESCE(SUM(amount_toman),0) AS s FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready') AND paid_at >= datetime('now','-30 day'){seller_filter}", params) or {'s':0})['s'])}\n\n'
        'پرفروش ترین بسته ها:\n'
    )
    if not top:
        text += 'هنوز داده ای ثبت نشده است.'
    else:
        for row in top:
            text += f"- {row.get('package_name') or 'بدون نام'} | {row['c']} سفارش | {fmt_money(row['s'])}\n"
    await message.answer(text, reply_markup=back_keyboard())
