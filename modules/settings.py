from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from modules import navasan
from utils import answer_callback, edit_or_answer
from keyboards import EditableButtonFilter
from keyboards import BTN_MAINTENANCE, BTN_SETTINGS, back_keyboard
from permissions import deny_callback, deny_message, is_super_admin

settings_router = Router()


class SettingsState(StatesGroup):
    star_rate = State()
    default_hwid_limit = State()
    navasan_api_key = State()
    navasan_item = State()
    crypto_margin = State()
    support_contact_value = State()


def format_toman(amount: int | None) -> str:
    return f'{int(amount or 0):,} تومان'


def settings_menu() -> InlineKeyboardMarkup:
    maintenance = bool(db.get_setting('maintenance_mode', False))
    rate_mode = db.get_setting('stars_rate_mode', 'manual')
    rate = int(db.get_setting('star_toman_rate', 0) or 0)
    hwid_limit = int(db.get_setting('default_hwid_limit', 0) or 0)
    hwid_text = 'دیفالت سرور' if hwid_limit <= 0 else str(hwid_limit)
    support_mode = db.get_setting('support_mode', 'support_message')
    support_mode_text = 'پیام/تیکت داخل ربات' if support_mode == 'support_message' else 'فقط نمایش آیدی'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🚧 حالت تعمیر: {'روشن' if maintenance else 'خاموش'}", callback_data='settings:toggle_maintenance')],
        [InlineKeyboardButton(text=f"🎫 حالت پشتیبانی: {support_mode_text}", callback_data='settings:support_mode')],
        [InlineKeyboardButton(text=f"⭐ نرخ Stars: {format_toman(rate)} | {'خودکار' if rate_mode == 'auto' else 'دستی'}", callback_data='settings:stars_rate')],
        [InlineKeyboardButton(text=f'👥 تعداد کاربر همزمان: {hwid_text}', callback_data='settings:default_hwid')],
        [InlineKeyboardButton(text='💵 نرخ دلار کریپتو / نوسان', callback_data='settings:navasan')],
        [InlineKeyboardButton(text='📋 نمایش تنظیمات', callback_data='settings:list')],
        [InlineKeyboardButton(text='🔁 تنظیمات تمدید', callback_data='settings:renewal')],
        [InlineKeyboardButton(text='🧪 تنظیمات سرویس تست', callback_data='test_admin:home')],
    ])


def support_mode_keyboard() -> InlineKeyboardMarkup:
    mode = db.get_setting('support_mode', 'support_message')
    mark_msg = '✅ ' if mode == 'support_message' else ''
    mark_contact = '✅ ' if mode == 'support_contact' else ''
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'{mark_msg}📨 دریافت پیام داخل ربات (Mode B)', callback_data='settings:support_mode:set:support_message')],
        [InlineKeyboardButton(text=f'{mark_contact}📞 فقط نمایش آیدی/لینک پشتیبانی (Mode A)', callback_data='settings:support_mode:set:support_contact')],
        [InlineKeyboardButton(text='✏️ ویرایش آیدی/لینک پشتیبانی', callback_data='settings:support_mode:set_contact')],
        [InlineKeyboardButton(text='🔙 برگشت', callback_data='settings:home')],
    ])


def stars_rate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✏️ تنظیم دستی نرخ هر ۱ استار', callback_data='settings:stars_rate:manual')],
        [InlineKeyboardButton(text='🤖 حالت خودکار نرخ Stars', callback_data='settings:stars_rate:auto')],
        [InlineKeyboardButton(text='🔙 برگشت', callback_data='settings:home')],
    ])


def default_hwid_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='0 | دیفالت سرور', callback_data='settings:default_hwid:set:0')],
        [InlineKeyboardButton(text='1 کاربر', callback_data='settings:default_hwid:set:1'), InlineKeyboardButton(text='2 کاربر', callback_data='settings:default_hwid:set:2')],
        [InlineKeyboardButton(text='3 کاربر', callback_data='settings:default_hwid:set:3'), InlineKeyboardButton(text='5 کاربر', callback_data='settings:default_hwid:set:5')],
        [InlineKeyboardButton(text='✏️ مقدار دلخواه', callback_data='settings:default_hwid:custom')],
        [InlineKeyboardButton(text='🔙 برگشت', callback_data='settings:home')],
    ])




def renewal_settings_keyboard() -> InlineKeyboardMarkup:
    mode = str(db.get_setting('renewal_mode', 'add') or 'add').lower()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅ ' if mode == 'add' else ''}➕ افزودن زمان و حجم باقی‌مانده", callback_data='settings:renewal:set:add')],
        [InlineKeyboardButton(text=f"{'✅ ' if mode == 'replace' else ''}♻️ جایگزینی و سوزاندن باقی‌مانده", callback_data='settings:renewal:set:replace')],
        [InlineKeyboardButton(text='🔙 برگشت', callback_data='settings:home')],
    ])


def renewal_settings_text() -> str:
    mode = str(db.get_setting('renewal_mode', 'add') or 'add').lower()
    current = '➕ افزودن' if mode == 'add' else '♻️ جایگزین'
    return (
        '🔁 <b>تنظیمات تمدید</b>\n\n'
        f'حالت فعلی: <b>{current}</b>\n\n'
        '➕ <b>افزودن:</b> زمان باقی‌مانده و حجم فعلی حفظ می‌شود و زمان/حجم پلن تمدید روی آن اضافه می‌شود.\n'
        'مثال: ۱ روز و ۲ گیگ مانده + پلن ۳۰ روزه ۱۰ گیگ = ۳۱ روز و مجموع سقف حجم قبلی + ۱۰ گیگ.\n\n'
        '♻️ <b>جایگزین:</b> باقی‌مانده قبلی می‌سوزد؛ زمان از لحظه تمدید دوباره محاسبه و مصرف ترافیک ریست می‌شود تا حجم کامل پلن جدید قابل استفاده باشد.'
    )

def navasan_settings_keyboard() -> InlineKeyboardMarkup:
    auto = bool(db.get_setting('navasan_auto_enabled', True))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 دریافت و ذخیره نرخ همین الان', callback_data='settings:navasan:fetch_now')],
        [InlineKeyboardButton(text='🔑 بروزرسانی API key نوسان', callback_data='settings:navasan:set_key')],
        [InlineKeyboardButton(text='💱 تغییر آیتم نرخ دلار', callback_data='settings:navasan:set_item')],
        [InlineKeyboardButton(text='📈 درصد حاشیه کریپتو', callback_data='settings:navasan:set_margin')],
        [InlineKeyboardButton(text=f"⏱ دریافت خودکار: {'روشن' if auto else 'خاموش'}", callback_data='settings:navasan:toggle_auto')],
        [InlineKeyboardButton(text='🔙 برگشت', callback_data='settings:home')],
    ])

async def settings_home_text(message: Message) -> None:
    await message.answer('⚙️ تنظیمات عمومی ربات', reply_markup=back_keyboard())
    await message.answer('یکی از گزینه‌ها را انتخاب کنید:', reply_markup=settings_menu())


@settings_router.message(EditableButtonFilter(BTN_SETTINGS))
async def settings_home(message: Message):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await settings_home_text(message)


@settings_router.message(EditableButtonFilter(BTN_MAINTENANCE))
async def maintenance_quick_toggle(message: Message):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    current = bool(db.get_setting('maintenance_mode', False))
    db.set_setting('maintenance_mode', not current)
    db.add_log(message.from_user.id, 'maintenance_mode_changed', 'settings', 'maintenance_mode', {'value': not current})
    await message.answer(
        f"حالت تعمیر {'روشن' if not current else 'خاموش'} شد.",
        reply_markup=back_keyboard(),
    )
    await message.answer('تنظیمات:', reply_markup=settings_menu())


@settings_router.callback_query(F.data == 'settings:toggle_maintenance')
async def toggle_maintenance(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = bool(db.get_setting('maintenance_mode', False))
    db.set_setting('maintenance_mode', not current)
    db.add_log(callback.from_user.id, 'maintenance_mode_changed', 'settings', 'maintenance_mode', {'value': not current})
    await edit_or_answer(callback, '⚙️ تنظیمات عمومی ربات', reply_markup=settings_menu())
    await callback.answer(f"حالت تعمیر {'روشن' if not current else 'خاموش'} شد.")



@settings_router.callback_query(F.data == 'settings:renewal')
async def renewal_settings_home(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, renewal_settings_text(), reply_markup=renewal_settings_keyboard(), parse_mode='HTML')
    await answer_callback(callback)


@settings_router.callback_query(F.data.startswith('settings:renewal:set:'))
async def renewal_settings_set(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    mode = str(callback.data or '').split(':')[-1].lower()
    if mode not in {'add', 'replace'}:
        await callback.answer('حالت نامعتبر است.', show_alert=True)
        return
    db.set_setting('renewal_mode', mode)
    db.add_log(callback.from_user.id, 'renewal_mode_changed', 'settings', 'renewal_mode', {'mode': mode})
    await edit_or_answer(callback, renewal_settings_text(), reply_markup=renewal_settings_keyboard(), parse_mode='HTML')
    await callback.answer('تنظیمات تمدید ذخیره شد.')

@settings_router.callback_query(F.data == 'settings:list')
async def settings_list(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    rows = db.fetchall('SELECT * FROM settings ORDER BY key')
    text = '⚙️ تنظیمات:\n\n'
    if not rows:
        text += 'تنظیمی ثبت نشده است.'
    for row in rows:
        text += f"{row['key']} = {row['value']}\n"
    await edit_or_answer(callback, text, reply_markup=settings_menu())
    await answer_callback(callback)


@settings_router.callback_query(F.data == 'settings:home')
async def settings_home_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, '⚙️ تنظیمات عمومی ربات', reply_markup=settings_menu())
    await answer_callback(callback)


@settings_router.callback_query(F.data == 'settings:default_hwid')
async def default_hwid_home(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    hwid_limit = int(db.get_setting('default_hwid_limit', 0) or 0)
    current_text = 'دیفالت سرور' if hwid_limit <= 0 else f'{hwid_limit} کاربر همزمان'
    text = (
        '👥 تنظیم تعداد کاربر همزمان\n\n'
        f'مقدار فعلی: {current_text}\n\n'
        'اگر مقدار 0 باشد، ربات مقدار HWID را هنگام ساخت سرویس ارسال نمی‌کند و دیفالت خود سرور اعمال می‌شود.\n'
        'اگر عددی مثل 2 وارد کنید، از این به بعد همان عدد به عنوان hwid_limit ارسال می‌شود.'
    )
    await edit_or_answer(callback, text, reply_markup=default_hwid_keyboard())
    await answer_callback(callback)


@settings_router.callback_query(F.data.startswith('settings:default_hwid:set:'))
async def default_hwid_set_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    raw = callback.data.rsplit(':', 1)[1]
    try:
        value = int(raw)
    except ValueError:
        await callback.answer('مقدار نامعتبر است.', show_alert=True)
        return
    if value < 0:
        await callback.answer('عدد نمی‌تواند منفی باشد.', show_alert=True)
        return
    db.set_setting('default_hwid_limit', value)
    db.add_log(callback.from_user.id, 'default_hwid_limit_changed', 'settings', 'default_hwid_limit', {'value': value})
    await callback.answer('ثبت شد.')
    await default_hwid_home(callback)


@settings_router.callback_query(F.data == 'settings:default_hwid:custom')
async def default_hwid_custom_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(SettingsState.default_hwid_limit)
    await edit_or_answer(
        callback,
        'عدد تعداد کاربر همزمان را وارد کنید.\n\n0 یعنی دیفالت سرور و ارسال نکردن hwid_limit.\nمثال: 2',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='settings:default_hwid')]]),
    )
    await answer_callback(callback)


@settings_router.message(SettingsState.default_hwid_limit)
async def default_hwid_custom_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip().replace(',', '').replace('٬', '')
    if not raw.isdigit():
        await message.answer('لطفا فقط عدد وارد کنید. مقدار 0 یعنی دیفالت سرور.')
        return
    value = int(raw)
    db.set_setting('default_hwid_limit', value)
    db.add_log(message.from_user.id, 'default_hwid_limit_changed', 'settings', 'default_hwid_limit', {'value': value})
    await state.clear()
    text = 'دیفالت سرور' if value <= 0 else f'{value} کاربر همزمان'
    await message.answer(f'تعداد کاربر همزمان ثبت شد: {text}')
    await settings_home_text(message)



@settings_router.callback_query(F.data == 'settings:navasan')
async def navasan_home(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, navasan.format_status_text(), reply_markup=navasan_settings_keyboard(), parse_mode='Markdown')
    await answer_callback(callback)


@settings_router.callback_query(F.data == 'settings:navasan:fetch_now')
async def navasan_fetch_now(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    try:
        await callback.answer('در حال دریافت نرخ از نوسان...')
    except Exception:
        pass
    try:
        result = await navasan.fetch_usd_rate_from_navasan()
        db.add_log(callback.from_user.id, 'navasan_rate_updated_manual', 'settings', 'crypto_usd_toman_rate', {'rate_toman': result['rate_toman'], 'item': result['item']})
        await edit_or_answer(callback, navasan.format_status_text(), reply_markup=navasan_settings_keyboard(), parse_mode='Markdown')
        msg = (
            f"✅ نرخ دلار از نوسان دریافت و ذخیره شد.\n\n"
            f"آیتم: `{result['item']}`\n"
            f"نرخ: `{result['rate_toman']:,}` تومان\n"
            f"معادل ریال: `{int(result['rate_toman']) * 10:,}` ریال"
        )
        await callback.message.answer(msg, parse_mode='Markdown')
    except Exception as exc:
        db.set_setting('navasan_last_error', str(exc))
        db.set_setting('navasan_last_error_at', db.now_iso())
        db.record_system_error('settings.navasan_fetch_now', str(exc), user_id=callback.from_user.id, error_type=type(exc).__name__)
        try:
            await edit_or_answer(callback, navasan.format_status_text(), reply_markup=navasan_settings_keyboard(), parse_mode='Markdown')
        except Exception:
            pass
        await callback.message.answer('❌ دریافت نرخ دلار از نوسان انجام نشد. جزئیات کامل داخل خطاهای سیستم ثبت شد.')


@settings_router.callback_query(F.data == 'settings:navasan:toggle_auto')
async def navasan_toggle_auto(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = bool(db.get_setting('navasan_auto_enabled', True))
    db.set_setting('navasan_auto_enabled', not current)
    db.add_log(callback.from_user.id, 'navasan_auto_toggled', 'settings', 'navasan_auto_enabled', {'value': not current})
    await edit_or_answer(callback, navasan.format_status_text(), reply_markup=navasan_settings_keyboard(), parse_mode='Markdown')
    await callback.answer(f"دریافت خودکار {'روشن' if not current else 'خاموش'} شد.")


@settings_router.callback_query(F.data == 'settings:navasan:set_key')
async def navasan_set_key_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(SettingsState.navasan_api_key)
    await edit_or_answer(
        callback,
        '🔑 کلید API جدید نوسان را ارسال کنید.\n\nاین مقدار داخل دیتابیس ربات ذخیره می‌شود و از مقدار داخل `.env` مهم‌تر است.\nبرای لغو، دکمه زیر را بزنید.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='settings:navasan')]]),
        parse_mode='Markdown',
    )
    await answer_callback(callback)


@settings_router.message(SettingsState.navasan_api_key)
async def navasan_set_key_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    key = (message.text or '').strip()
    if len(key) < 8:
        await message.answer('کلید API کوتاه یا نامعتبر است. دوباره ارسال کنید.')
        return
    db.set_setting('navasan_api_key', key)
    db.add_log(message.from_user.id, 'navasan_api_key_updated', 'settings', 'navasan_api_key', {'length': len(key)})
    await state.clear()
    await message.answer('کلید API نوسان ذخیره شد. الان یک بار دریافت نرخ را تست می‌کنم...')
    try:
        result = await navasan.fetch_usd_rate_from_navasan(force_api_key=key)
        await message.answer(f"✅ نرخ دریافت و ذخیره شد: {result['rate_toman']:,} تومان")
    except Exception as exc:
        db.set_setting('navasan_last_error', str(exc))
        db.record_system_error('settings.navasan_set_key_finish', str(exc), user_id=message.from_user.id, error_type=type(exc).__name__)
        await message.answer('کلید ذخیره شد، اما دریافت تستی نرخ خطا داد. از بخش تنظیمات می‌توانید دوباره تست کنید.')
    await settings_home_text(message)


@settings_router.callback_query(F.data == 'settings:navasan:set_item')
async def navasan_set_item_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(SettingsState.navasan_item)
    await edit_or_answer(
        callback,
        '💱 آیتم نرخ دلار نوسان را وارد کنید.\n\nپیشنهاد برای فروش دلار تهران: `usd_sell`\nگزینه‌های رایج دیگر: `usd_buy` ، `harat_naghdi_sell` ، `dolar_harat_sell`',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='settings:navasan')]]),
        parse_mode='Markdown',
    )
    await answer_callback(callback)


@settings_router.message(SettingsState.navasan_item)
async def navasan_set_item_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    item = (message.text or '').strip()
    if not item or not item.replace('_', '').isalnum():
        await message.answer('آیتم نامعتبر است. مثال صحیح: usd_sell')
        return
    db.set_setting('navasan_usd_item', item)
    db.add_log(message.from_user.id, 'navasan_item_updated', 'settings', 'navasan_usd_item', {'item': item})
    await state.clear()
    await message.answer(f'آیتم نوسان ثبت شد: `{item}`', parse_mode='Markdown')
    await settings_home_text(message)


@settings_router.callback_query(F.data == 'settings:navasan:set_margin')
async def navasan_set_margin_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = db.get_setting('crypto_usd_margin_percent', 0)
    await state.set_state(SettingsState.crypto_margin)
    await edit_or_answer(
        callback,
        f'📈 درصد حاشیه امنیت پرداخت کریپتو را وارد کنید.\n\nمقدار فعلی: `{current}` درصد\nمثال: `3` یعنی مبلغ دلاری ۳ درصد بیشتر شود.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='settings:navasan')]]),
        parse_mode='Markdown',
    )
    await answer_callback(callback)


@settings_router.message(SettingsState.crypto_margin)
async def navasan_set_margin_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip().replace(',', '.')
    try:
        value = float(raw)
    except ValueError:
        await message.answer('لطفا عدد وارد کنید. مثال: 3')
        return
    if value < 0 or value > 50:
        await message.answer('عدد باید بین 0 تا 50 باشد.')
        return
    db.set_setting('crypto_usd_margin_percent', value)
    db.add_log(message.from_user.id, 'crypto_usd_margin_changed', 'settings', 'crypto_usd_margin_percent', {'value': value})
    await state.clear()
    await message.answer(f'درصد حاشیه کریپتو ثبت شد: {value:g}%')
    await settings_home_text(message)

@settings_router.callback_query(F.data == 'settings:stars_rate')
async def stars_rate_home(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    mode = db.get_setting('stars_rate_mode', 'manual')
    manual = int(db.get_setting('star_toman_rate', 0) or 0)
    auto = int(db.get_setting('auto_star_toman_rate', 0) or 0)
    text = (
        '⭐ تنظیم نرخ تبدیل Stars\n\n'
        f"حالت فعلی: {'خودکار' if mode == 'auto' else 'دستی'}\n"
        f'نرخ دستی هر ۱ استار: {format_toman(manual)}\n'
        f'نرخ خودکار ذخیره‌شده: {format_toman(auto) if auto else "تنظیم نشده"}\n\n'
        'در پرداخت مشتری، قیمت تومان بسته بر این نرخ تقسیم و به Stars تبدیل می‌شود.\n'
        'اگر حالت خودکار روشن باشد ولی نرخ خودکار ثبت نشده باشد، از نرخ دستی استفاده می‌شود.'
    )
    await edit_or_answer(callback, text, reply_markup=stars_rate_keyboard())
    await answer_callback(callback)


@settings_router.callback_query(F.data == 'settings:stars_rate:auto')
async def stars_rate_auto(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    db.set_setting('stars_rate_mode', 'auto')
    db.add_log(callback.from_user.id, 'stars_rate_mode_changed', 'settings', 'stars_rate_mode', {'mode': 'auto'})
    await callback.answer('حالت خودکار فعال شد.')
    await stars_rate_home(callback)


@settings_router.callback_query(F.data == 'settings:stars_rate:manual')
async def stars_rate_manual_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(SettingsState.star_rate)
    await edit_or_answer(
        callback,
        'نرخ هر ۱ استار را به تومان وارد کنید.\n\nمثال: اگر هر ۱ استار را 3000 تومان حساب می‌کنید، بنویسید:\n3000',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='settings:stars_rate')]]),
    )
    await answer_callback(callback)


@settings_router.message(SettingsState.star_rate)
async def stars_rate_manual_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip().replace(',', '').replace('٬', '')
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer('لطفا یک عدد صحیح بزرگ‌تر از صفر وارد کنید.')
        return
    rate = int(raw)
    db.set_setting('star_toman_rate', rate)
    db.set_setting('stars_rate_mode', 'manual')
    db.add_log(message.from_user.id, 'stars_rate_manual_set', 'settings', 'star_toman_rate', {'rate': rate})
    await state.clear()
    await message.answer(f'نرخ Stars ثبت شد: هر ۱ استار = {format_toman(rate)}')
    await settings_home_text(message)


@settings_router.callback_query(F.data == 'settings:support_mode')
async def support_mode_menu(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    mode = db.get_setting('support_mode', 'support_message')
    contact = db.get_setting('support_username', '')
    mode_title = 'دریافت پیام و تیکت داخل ربات (Mode B)' if mode == 'support_message' else 'فقط نمایش آیدی و راه ارتباطی (Mode A)'
    text = (
        '🎫 تنظیمات حالت پشتیبانی\n\n'
        f'حالت فعلی: <b>{mode_title}</b>\n'
        f'آیدی/لینک فعلی پشتیبانی: <code>{contact or "تنظیم نشده"}</code>\n\n'
        '• <b>حالت A (تماس مستقیم):</b> با زدن دکمه پشتیبانی، فقط آیدی یا لینک پشتیبانی به کاربر نمایش داده می‌شود.\n'
        '• <b>حالت B (پیام در ربات):</b> با زدن دکمه پشتیبانی، از کاربر پیام یا تیکت دریافت می‌شود و برای ادمین ارسال می‌گردد.'
    )
    await edit_or_answer(callback, text, reply_markup=support_mode_keyboard(), parse_mode='HTML')
    await answer_callback(callback)


@settings_router.callback_query(F.data.startswith('settings:support_mode:set:'))
async def support_mode_set(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    new_mode = callback.data.split(':')[-1]
    if new_mode not in {'support_message', 'support_contact'}:
        await callback.answer('حالت نامعتبر است.', show_alert=True)
        return
    db.set_setting('support_mode', new_mode)
    db.add_log(callback.from_user.id, 'support_mode_changed', 'settings', 'support_mode', {'mode': new_mode})
    await callback.answer('حالت پشتیبانی ذخیره شد.')
    await support_mode_menu(callback)


@settings_router.callback_query(F.data == 'settings:support_mode:set_contact')
async def support_mode_contact_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(SettingsState.support_contact_value)
    current = db.get_setting('support_username', '')
    text = (
        '✏️ آیدی یا لینک پشتیبانی را ارسال کنید.\n\n'
        f'مقدار فعلی: <code>{current or "خالی"}</code>\n\n'
        'مثال: @SupportUsername یا https://example.com/SupportUsername'
    )
    await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف', callback_data='settings:support_mode')]]), parse_mode='HTML')
    await answer_callback(callback)


@settings_router.message(SettingsState.support_contact_value)
async def support_mode_contact_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    val = (message.text or '').strip()
    db.set_setting('support_username', val)
    db.add_log(message.from_user.id, 'support_username_changed', 'settings', 'support_username', {'value': val})
    await state.clear()
    await message.answer(f'آیدی/لینک پشتیبانی به <code>{val}</code> تغییر یافت.', parse_mode='HTML')
    await settings_home_text(message)
