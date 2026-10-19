from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import BTN_VARIABLES, EditableButtonFilter, back_keyboard
from permissions import deny_callback, deny_message, is_staff
from utils import edit_or_answer

variables_router = Router()

# Canonical variable catalog. Every variable here is accepted by the global renderer.
VARIABLE_CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ('user', '👤 کاربر', [
        ('name', 'نام نمایشی کاربر'), ('first_name', 'نام کاربر'), ('username', 'یوزرنیم تلگرام'),
        ('user_id', 'آیدی عددی تلگرام'), ('telegram_id', 'آیدی عددی تلگرام'), ('support', 'آیدی/لینک پشتیبانی'),
    ]),
    ('product', '🛍 محصول / دسته‌بندی', [
        ('product_id', 'شناسه محصول'), ('product_name', 'نام محصول'),
        ('product_public_price_per_gb', 'قیمت عمومی هر گیگ با واحد'),
        ('product_public_price_per_gb_raw', 'قیمت عمومی هر گیگ خام'),
        ('custom_volume_gb', 'حجم بسته دلخواه'), ('custom_months', 'ماه بسته دلخواه'), ('custom_users', 'تعداد کاربر بسته دلخواه'),
    ]),
    ('package', '📦 بسته', [
        ('package_id', 'شناسه بسته'), ('template_id', 'شناسه قالب پنل'), ('package_name', 'نام نمایشی بسته'), ('name', 'نام نمایشی'),
        ('package_price', 'قیمت بسته با واحد'), ('package_price_raw', 'قیمت خام بسته'), ('price', 'قیمت با واحد'), ('price_raw', 'قیمت خام'),
        ('price_toman', 'قیمت تومان'), ('volume_gb', 'حجم به گیگ'), ('traffic_gb', 'حجم به گیگ'), ('data_gb', 'حجم به گیگ'), ('gb', 'حجم به گیگ'),
        ('package_data_limit', 'حجم بسته با واحد'), ('package_data_limit_raw', 'حجم خام بایت'), ('data_limit', 'حجم با واحد'), ('data_limit_raw', 'حجم خام بایت'),
        ('package_duration', 'مدت بسته'), ('package_duration_seconds', 'مدت خام ثانیه'), ('duration', 'مدت'), ('duration_seconds', 'مدت خام ثانیه'),
        ('duration_days', 'مدت به روز'), ('duration_months', 'مدت به ماه'), ('days', 'روز'), ('hours', 'ساعت'),
        ('username_prefix', 'پیشوند نام سرویس'), ('username_suffix', 'پسوند نام سرویس'), ('hwid_limit', 'محدودیت دستگاه'), ('package_status', 'وضعیت بسته'),
    ]),
    ('order', '🧾 سفارش / پرداخت', [
        ('order_id', 'شماره سفارش'), ('entity_id', 'شناسه موجودیت/سفارش'), ('order_type', 'نوع سفارش'),
        ('amount', 'مبلغ'), ('discount', 'تخفیف'), ('discount_raw', 'تخفیف خام'),
        ('after_discount', 'مبلغ پس از تخفیف، پیش از کیف پول'), ('after_discount_raw', 'مبلغ خام پس از تخفیف، پیش از کیف پول'),
        ('wallet_used', 'مصرف کیف پول'), ('wallet_used_raw', 'مصرف کیف پول خام'),
        ('payable', 'مبلغ قابل پرداخت'), ('payable_raw', 'مبلغ قابل پرداخت خام'),
        ('stars', 'مقدار Stars'), ('stars_amount', 'مقدار خام Stars'), ('status', 'وضعیت'), ('final_status', 'وضعیت نهایی'),
        ('created_at', 'تاریخ ایجاد'), ('payment_id', 'شناسه پرداخت'), ('transaction_id', 'شناسه تراکنش'), ('receipt_id', 'شناسه فیش'),
        ('coupon_code', 'کد تخفیف استفاده‌شده'), ('discount_code', 'کد تخفیف استفاده‌شده'), ('service_message', 'پیام وضعیت سرویس'),
    ]),
    ('service', '📡 سرویس', [
        ('service_id', 'شناسه سرویس'), ('service_name', 'نام سرویس'), ('service_username', 'نام کاربری سرویس'),
        ('customer_username', 'نام کاربری سرویس'), ('panel_username', 'نام کاربری پنل'),
        ('sub_link', 'لینک اشتراک'), ('subscription_url', 'لینک اشتراک'), ('client', 'کلاینت'),
        ('purchase_date', 'تاریخ خرید'), ('used_traffic', 'حجم مصرف‌شده'), ('remaining_traffic', 'حجم باقی‌مانده'),
        ('remaining_percent', 'درصد باقی‌مانده'), ('expire_at', 'تاریخ انقضا'), ('expire_remaining', 'زمان تا انقضا'),
        ('remaining_time', 'زمان باقی‌مانده'), ('last_online', 'آخرین اتصال'), ('subscription_updated', 'آخرین بروزرسانی اشتراک'),
    ]),
    ('referral', '👥 دعوت دوستان', [
        ('ref_link', 'لینک دعوت'), ('referral_link', 'لینک دعوت'), ('reward', 'درصدهای پاداش دعوت'), ('reward_raw', 'درصدهای خام'),
        ('condition', 'شرط پاداش'), ('purchase_percent', 'درصد پاداش خرید'), ('wallet_percent', 'درصد پاداش شارژ'),
        ('purchase_scope', 'محدوده پاداش خرید'), ('wallet_scope', 'محدوده پاداش شارژ'),
        ('friend_name', 'نام دوست دعوت‌شده'), ('friend_username', 'یوزرنیم دوست دعوت‌شده'), ('friend_id', 'شناسه عددی دوست'),
        ('event_type', 'نوع تراکنش دوست'), ('source_amount', 'مبلغ خرید/شارژ با واحد'), ('source_amount_raw', 'مبلغ خرید/شارژ خام'),
        ('reward_amount', 'پاداش واریزی با واحد'), ('reward_amount_raw', 'پاداش واریزی خام'), ('percent', 'درصد پاداش'),
        ('charge_id', 'شناسه درخواست شارژ'),
        ('total', 'کل دعوت‌ها'), ('rewarded', 'دعوت‌های دارای پاداش'), ('pending', 'در انتظار'), ('earned', 'درآمد پاداش'), ('earned_raw', 'درآمد خام'),
    ]),
    ('test', '🧪 سرویس تست', [
        ('cooldown_days', 'روز تا دریافت مجدد'),
    ]),
    ('misc', '🧩 عمومی / سایر', [
        ('ticket_id', 'شناسه تیکت'), ('card_number', 'شماره کارت'), ('card_holder', 'صاحب کارت'),
    ]),
]

VARIABLE_DESCRIPTIONS = {name: desc for _, _, items in VARIABLE_CATEGORIES for name, desc in items}
ALL_VARIABLES = tuple(VARIABLE_DESCRIPTIONS.keys())


def all_variable_defaults() -> dict[str, str]:
    return {name: '' for name in ALL_VARIABLES}


def variable_categories_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f'vars:cat:{key}')] for key, title, _ in VARIABLE_CATEGORIES]
    rows.append([InlineKeyboardButton(text='🔎 نمایش همه متغیرها', callback_data='vars:all:0')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_keyboard(key: str) -> InlineKeyboardMarkup:
    items = next((items for cat_key, _, items in VARIABLE_CATEGORIES if cat_key == key), [])
    rows = []
    for name, desc in items:
        token = '{' + name + '}'
        rows.append([InlineKeyboardButton(text=f'{token} — {desc}', copy_text=CopyTextButton(text=token))])
    rows.append([InlineKeyboardButton(text='🔙 دسته‌بندی متغیرها', callback_data='vars:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _all_keyboard(page: int = 0, per_page: int = 12) -> InlineKeyboardMarkup:
    items = [(name, VARIABLE_DESCRIPTIONS[name]) for name in ALL_VARIABLES]
    start = max(0, int(page)) * per_page
    chunk = items[start:start + per_page]
    rows = [[InlineKeyboardButton(text=f'{{{name}}} — {desc}', copy_text=CopyTextButton(text='{' + name + '}'))] for name, desc in chunk]
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text='⬅️ قبلی', callback_data=f'vars:all:{page - 1}'))
    if start + per_page < len(items):
        nav.append(InlineKeyboardButton(text='بعدی ➡️', callback_data=f'vars:all:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text='🔙 دسته‌بندی', callback_data='vars:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@variables_router.message(EditableButtonFilter(BTN_VARIABLES))
async def variables_home_message(message: Message):
    if not is_staff(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    await message.answer('🧬 متغیرها', reply_markup=back_keyboard())
    await message.answer(
        'همه متغیرهای قابل استفاده در قالب‌های ربات اینجا دسته‌بندی شده‌اند.\n\n'
        'روی هر متغیر بزن تا همان متغیر به‌تنهایی کپی شود. Premium Emoji را هم می‌توانی مستقیم داخل قالب بفرستی.',
        reply_markup=variable_categories_keyboard(),
    )


@variables_router.callback_query(F.data == 'vars:home')
async def variables_home(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback); return
    await edit_or_answer(callback, '🧬 متغیرها\n\nدسته موردنظر را انتخاب کنید:', reply_markup=variable_categories_keyboard())
    await callback.answer()


@variables_router.callback_query(F.data.startswith('vars:cat:'))
async def variables_category(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback); return
    key = callback.data.split(':', 2)[2]
    cat = next(((title, items) for cat_key, title, items in VARIABLE_CATEGORIES if cat_key == key), None)
    if not cat:
        await callback.answer('دسته پیدا نشد.', show_alert=True); return
    title, items = cat
    body = title + '\n\n' + '\n'.join(f'• {{{name}}} — {desc}' for name, desc in items)
    await edit_or_answer(callback, body, reply_markup=_category_keyboard(key))
    await callback.answer()


@variables_router.callback_query(F.data.startswith('vars:all:'))
async def variables_all(callback: CallbackQuery):
    if not is_staff(callback.from_user.id):
        await deny_callback(callback); return
    try:
        page = max(0, int(callback.data.split(':')[-1]))
    except Exception:
        page = 0
    await edit_or_answer(callback, f'🔎 همه متغیرها\n\nصفحه {page + 1}', reply_markup=_all_keyboard(page))
    await callback.answer()
