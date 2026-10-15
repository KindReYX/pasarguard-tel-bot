from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import api_client
import db
from keyboards import (
    back_keyboard, BTN_TEST_SETTINGS, EditableButtonFilter,
    customer_button_enabled, set_customer_button_enabled,
)
from permissions import deny_callback, deny_message, is_admin
from utils import edit_or_answer, answer_callback, message_text_with_custom_emoji_tokens

# Opened from settings callbacks.
test_service_router = Router()


class TestServiceState(StatesGroup):
    cooldown_days = State()
    reminder_text = State()
    button_text = State()
    page_text = State()


def test_admin_keyboard() -> InlineKeyboardMarkup:
    enabled = bool(db.get_setting('test_service_enabled', True))
    cooldown_days = int(db.get_setting('test_service_cooldown_days', 14) or 14)
    configured = db.fetchone('SELECT COUNT(*) AS c FROM product_test_templates') or {'c': 0}
    button_visible = customer_button_enabled('test_service')
    scope_label = 'هر محصول مستقل' if db.test_service_scope() == 'per_product' else 'یک تست برای کل محصولات'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🧪 وضعیت سرویس تست: {'روشن' if enabled else 'خاموش'}", callback_data='test_admin:toggle')],
        [InlineKeyboardButton(text=f"👁 دکمه تست در منوی کاربر: {'نمایش' if button_visible else 'مخفی'}", callback_data='test_admin:toggle_button')],
        [InlineKeyboardButton(text=f"🛍 بسته تست محصولات ({configured['c']} تنظیم‌شده)", callback_data='test_admin:products')],
        [InlineKeyboardButton(text=f'🎯 سهمیه تست: {scope_label}', callback_data='test_admin:scope')],
        [InlineKeyboardButton(text=f'⏳ دریافت مجدد بعد از {cooldown_days} روز', callback_data='test_admin:set_cooldown')],
        [InlineKeyboardButton(text='✏️ متن دکمه سرویس تست', callback_data='test_admin:set_button_text')],
        [InlineKeyboardButton(text='📝 متن صفحه اصلی سرویس تست', callback_data='test_admin:set_page_text')],
        [InlineKeyboardButton(text='🔔 متن یادآوری دریافت مجدد', callback_data='test_admin:set_reminder_text')],
        [InlineKeyboardButton(text='🔄 ریست امکان دریافت تست برای همه', callback_data='test_admin:reset_all')],
    ])


def products_keyboard() -> InlineKeyboardMarkup:
    products = db.fetchall('SELECT * FROM products ORDER BY sort_order, id')
    configured = {int(r['product_id']): str(r['template_id']) for r in db.fetchall('SELECT * FROM product_test_templates')}
    rows = []
    for p in products:
        tid = configured.get(int(p['id']))
        mark = '✅' if tid else '➖'
        rows.append([InlineKeyboardButton(text=f"{mark} {p['name']} | تست: {tid or 'ندارد'}", callback_data=f"test_admin:product:{p['id']}")])
    if not rows:
        rows.append([InlineKeyboardButton(text='ابتدا از بخش محصولات یک محصول بسازید', callback_data='test_admin:noop')])
    rows.append([InlineKeyboardButton(text='🔙 تنظیمات تست', callback_data='test_admin:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def product_packs_keyboard(product_id: int) -> InlineKeyboardMarkup:
    metas = db.fetchall('SELECT * FROM package_settings WHERE product_id=? ORDER BY sort_order, template_id', (int(product_id),))
    panel = {str(p.get('id')): p for p in await api_client.get_user_templates()}
    current = db.fetchone('SELECT template_id FROM product_test_templates WHERE product_id=?', (int(product_id),))
    current_tid = str(current['template_id']) if current else None
    rows = []
    for meta in metas:
        tid = str(meta['template_id'])
        pack = panel.get(tid, {'id': tid, 'name': f'بسته {tid}'})
        mark = '✅' if tid == current_tid else '⬜️'
        rows.append([InlineKeyboardButton(text=f"{mark} {pack.get('name') or tid}", callback_data=f'test_admin:choose:{product_id}:{tid}')])
    rows.append([InlineKeyboardButton(text='📝 متن صفحه تست این محصول', callback_data=f'product:test_description:{product_id}')])
    if current_tid:
        rows.append([InlineKeyboardButton(text='🗑 حذف بسته تست این محصول', callback_data=f'test_admin:clear:{product_id}')])
    if not metas:
        rows.append([InlineKeyboardButton(text='این محصول هنوز بسته‌ای ندارد', callback_data='test_admin:noop')])
    rows.append([InlineKeyboardButton(text='🔙 محصولات', callback_data='test_admin:products')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def test_admin_home_message(message: Message) -> None:
    await message.answer('🧪 تنظیمات بسته تست', reply_markup=back_keyboard())
    await message.answer('همه تنظیمات سرویس و بسته تست از همین بخش مدیریت می‌شوند:', reply_markup=test_admin_keyboard())


@test_service_router.message(EditableButtonFilter(BTN_TEST_SETTINGS))
async def test_admin_main_menu(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:home')
async def test_admin_home_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await edit_or_answer(callback, '🧪 تنظیمات بسته تست\n\nهمه تنظیمات سرویس و بسته تست از همین بخش مدیریت می‌شوند.', reply_markup=test_admin_keyboard())
    await answer_callback(callback)


@test_service_router.callback_query(F.data == 'test_admin:toggle')
async def test_admin_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    current = bool(db.get_setting('test_service_enabled', True))
    db.set_setting('test_service_enabled', not current)
    await edit_or_answer(callback, '🧪 تنظیمات بسته تست', reply_markup=test_admin_keyboard())
    await answer_callback(callback, 'تغییر کرد.')


@test_service_router.callback_query(F.data == 'test_admin:toggle_button')
async def test_admin_toggle_button(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    current = customer_button_enabled('test_service')
    set_customer_button_enabled('test_service', not current)
    await edit_or_answer(callback, '🧪 تنظیمات بسته تست', reply_markup=test_admin_keyboard())
    await answer_callback(callback, 'نمایش دکمه تست تغییر کرد.')


@test_service_router.callback_query(F.data == 'test_admin:scope')
async def test_admin_scope(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    rows = [
        [InlineKeyboardButton(text='🔒 یک تست برای کل محصولات', callback_data='test_admin:scope_set:global')],
        [InlineKeyboardButton(text='🛍 یک تست از هر محصول، مستقل از بقیه', callback_data='test_admin:scope_set:per_product')],
        [InlineKeyboardButton(text='🔙 تنظیمات تست', callback_data='test_admin:home')],
    ]
    await edit_or_answer(callback, 'سهمیه دریافت تست را انتخاب کنید. در هر دو حالت، زمان دریافت مجدد طبق تنظیم روزهای انتظار محاسبه می‌شود. سوابق قبلی حفظ می‌شوند.', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@test_service_router.callback_query(F.data.startswith('test_admin:scope_set:'))
async def test_admin_scope_set(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    scope = callback.data.rsplit(':', 1)[-1]
    if scope not in {'global', 'per_product'}:
        await callback.answer('گزینه نامعتبر است.', show_alert=True); return
    db.set_setting('test_service_scope', scope)
    await edit_or_answer(callback, 'تنظیم سهمیه تست ذخیره شد ✅', reply_markup=test_admin_keyboard())
    await callback.answer()


@test_service_router.callback_query(F.data == 'test_admin:products')
async def test_admin_products(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await edit_or_answer(callback, '🛍 محصول را انتخاب کنید تا بسته تست آن را مشخص کنید:', reply_markup=products_keyboard())
    await callback.answer()


@test_service_router.callback_query(F.data.startswith('test_admin:product:'))
async def test_admin_product(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    product = db.fetchone('SELECT * FROM products WHERE id=?', (pid,))
    if not product:
        await callback.answer('محصول پیدا نشد.', show_alert=True); return
    await edit_or_answer(callback, f"🧪 بسته تست محصول «{product['name']}» را انتخاب کنید:", reply_markup=await product_packs_keyboard(pid))
    await callback.answer()


@test_service_router.callback_query(F.data.startswith('test_admin:choose:'))
async def test_admin_choose(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    _, _, pid, tid = callback.data.split(':', 3)
    exists = db.fetchone('SELECT 1 AS ok FROM package_settings WHERE product_id=? AND template_id=?', (int(pid), str(tid)))
    if not exists:
        await callback.answer('این بسته متعلق به این محصول نیست.', show_alert=True); return
    db.execute('''INSERT INTO product_test_templates(product_id, template_id, updated_at) VALUES(?,?,?)
                  ON CONFLICT(product_id) DO UPDATE SET template_id=excluded.template_id, updated_at=excluded.updated_at''', (int(pid), str(tid), db.now_iso()))
    await edit_or_answer(callback, 'بسته تست ذخیره شد ✅', reply_markup=await product_packs_keyboard(int(pid)))
    await callback.answer()


@test_service_router.callback_query(F.data.startswith('test_admin:clear:'))
async def test_admin_clear(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    db.execute('DELETE FROM product_test_templates WHERE product_id=?', (pid,))
    await edit_or_answer(callback, 'بسته تست این محصول حذف شد.', reply_markup=await product_packs_keyboard(pid))
    await callback.answer()


@test_service_router.callback_query(F.data == 'test_admin:set_cooldown')
async def test_cooldown_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(TestServiceState.cooldown_days)
    current = int(db.get_setting('test_service_cooldown_days', 14) or 14)
    await edit_or_answer(callback, f'تعداد روز تا امکان دریافت مجدد سرویس تست را بفرستید.\nمقدار فعلی: {current} روز\nمثال: 14')
    await answer_callback(callback)


@test_service_router.message(TestServiceState.cooldown_days)
async def test_cooldown_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear(); return
    try:
        days = int((message.text or '').strip())
        if days < 1 or days > 3650: raise ValueError
    except Exception:
        await message.answer('عدد معتبر بین 1 تا 3650 روز بفرستید.'); return
    db.set_setting('test_service_cooldown_days', days)
    await state.clear(); await message.answer(f'✅ دریافت مجدد سرویس تست روی {days} روز تنظیم شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:set_button_text')
async def test_button_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(TestServiceState.button_text)
    current = str(db.get_setting('test_service_button_text', '🧪 سرویس تست') or '🧪 سرویس تست')
    await edit_or_answer(callback, f'متن جدید دکمه سرویس تست را بفرستید.\n\nمتن فعلی:\n{current}')
    await answer_callback(callback)


@test_service_router.message(TestServiceState.button_text)
async def test_button_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear(); return
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن دکمه نمی‌تواند خالی باشد.'); return
    db.set_setting('test_service_button_text', text)
    await state.clear()
    await message.answer('✅ متن دکمه سرویس تست ذخیره شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:set_page_text')
async def test_page_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(TestServiceState.page_text)
    current = str(db.get_setting('test_service_text', 'سرویس تست') or 'سرویس تست')
    await edit_or_answer(callback, 'متن صفحه اصلی سرویس تست را ارسال کنید. این متن فقط صفحه انتخاب محصول است و متن تست هر محصول از داخل همان محصول جداست.\n\nمتغیرها: {name} {first_name} {username} {user_id} {product_id} {product_name} {support}\n\nمتن فعلی:\n' + current)
    await answer_callback(callback)


@test_service_router.message(TestServiceState.page_text)
async def test_page_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear(); return
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن صفحه نمی‌تواند خالی باشد.'); return
    db.set_setting('test_service_text', text)
    await state.clear()
    await message.answer('✅ متن صفحه سرویس تست ذخیره شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:set_reminder_text')
async def test_reminder_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(TestServiceState.reminder_text)
    current = str(db.get_setting('test_service_reminder_text', '') or '')
    hint = 'متن یادآوری را ارسال کنید.\n\nمتغیرها: {name} {first_name} {username} {user_id} {product_id} {product_name} {package_name} {cooldown_days} {support}'
    if current: hint += f'\n\nمتن فعلی:\n{current}'
    await edit_or_answer(callback, hint); await answer_callback(callback)


@test_service_router.message(TestServiceState.reminder_text)
async def test_reminder_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear(); return
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن نمی‌تواند خالی باشد.'); return
    db.set_setting('test_service_reminder_text', text)
    await state.clear(); await message.answer('✅ متن یادآوری سرویس تست ذخیره شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:reset_all')
async def test_admin_reset_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    db.reset_test_usage_for_all()
    await callback.answer('همه کاربران دوباره می توانند سرویس تست بگیرند.', show_alert=True)
    await edit_or_answer(callback, '🧪 تنظیمات بسته تست', reply_markup=test_admin_keyboard())


@test_service_router.callback_query(F.data == 'test_admin:noop')
async def test_noop(callback: CallbackQuery):
    await callback.answer()
