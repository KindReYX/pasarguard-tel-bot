from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import api_client
import db
from keyboards import back_keyboard
from permissions import deny_callback, deny_message, is_admin
from utils import edit_or_answer, answer_callback

# This module is opened from advanced/settings callbacks, not a reply button.
test_service_router = Router()


class TestServiceState(StatesGroup):
    template_id = State()
    cooldown_days = State()
    reminder_text = State()


def test_admin_keyboard() -> InlineKeyboardMarkup:
    template_id = db.get_setting('test_template_id', None)
    enabled = bool(db.get_setting('test_service_enabled', True))
    cooldown_days = int(db.get_setting('test_service_cooldown_days', 14) or 14)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"وضعیت تست: {'روشن' if enabled else 'خاموش'}", callback_data='test_admin:toggle')],
        [InlineKeyboardButton(text=f'بسته تست فعلی: {template_id or "تنظیم نشده"}', callback_data='test_admin:set_template')],
        [InlineKeyboardButton(text=f'⏳ دریافت مجدد بعد از {cooldown_days} روز', callback_data='test_admin:set_cooldown')],
        [InlineKeyboardButton(text='✏️ متن یادآوری دریافت مجدد', callback_data='test_admin:set_reminder_text')],
        [InlineKeyboardButton(text='🔄 ریست امکان دریافت تست برای همه', callback_data='test_admin:reset_all')],
    ])


async def test_admin_home_message(message: Message) -> None:
    await message.answer('🧪 تنظیمات سرویس تست', reply_markup=back_keyboard())
    await message.answer('مشخصات سرویس تست را مدیریت کنید:', reply_markup=test_admin_keyboard())


@test_service_router.callback_query(F.data == 'test_admin:home')
async def test_admin_home_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, '🧪 تنظیمات سرویس تست', reply_markup=test_admin_keyboard())
    await answer_callback(callback)


@test_service_router.callback_query(F.data == 'test_admin:toggle')
async def test_admin_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = bool(db.get_setting('test_service_enabled', True))
    db.set_setting('test_service_enabled', not current)
    await edit_or_answer(callback, '🧪 تنظیمات سرویس تست', reply_markup=test_admin_keyboard())
    await answer_callback(callback, 'تغییر کرد.')


@test_service_router.callback_query(F.data == 'test_admin:set_template')
async def test_template_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(TestServiceState.template_id)
    await edit_or_answer(callback, 'آیدی تمپلیت/بسته تست را وارد کنید. همان ID بسته داخل بخش بسته ها.')
    await answer_callback(callback)


@test_service_router.message(TestServiceState.template_id)
async def test_template_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    template_id = (message.text or '').strip()
    if not template_id:
        await message.answer('آیدی معتبر نیست.')
        return
    try:
        await api_client.get_user_template(template_id)
    except Exception as exc:
        await message.answer(f'این بسته از پنل خوانده نشد: {exc}')
        return
    db.set_setting('test_template_id', template_id)
    await state.clear()
    await message.answer('بسته تست ذخیره شد ✅', reply_markup=back_keyboard())
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:set_cooldown')
async def test_cooldown_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(TestServiceState.cooldown_days)
    current = int(db.get_setting('test_service_cooldown_days', 14) or 14)
    await edit_or_answer(callback, f'تعداد روز تا امکان دریافت مجدد سرویس تست را بفرستید.\nمقدار فعلی: {current} روز\nمثال: 14')
    await answer_callback(callback)


@test_service_router.message(TestServiceState.cooldown_days)
async def test_cooldown_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear()
        return
    try:
        days = int((message.text or '').strip())
        if days < 1 or days > 3650:
            raise ValueError
    except Exception:
        await message.answer('عدد معتبر بین 1 تا 3650 روز بفرستید.')
        return
    db.set_setting('test_service_cooldown_days', days)
    await state.clear()
    await message.answer(f'✅ دریافت مجدد سرویس تست روی {days} روز تنظیم شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:set_reminder_text')
async def test_reminder_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(TestServiceState.reminder_text)
    current = str(db.get_setting('test_service_reminder_text', '') or '')
    hint = ('متن یادآوری را ارسال کنید.\n\nمتغیرها: {name} {first_name} {username} {user_id} {cooldown_days} {support}')
    if current:
        hint += f'\n\nمتن فعلی:\n{current}'
    await edit_or_answer(callback, hint)
    await answer_callback(callback)


@test_service_router.message(TestServiceState.reminder_text)
async def test_reminder_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear()
        return
    text = (message.text or '').strip()
    if not text:
        await message.answer('متن نمی‌تواند خالی باشد.')
        return
    db.set_setting('test_service_reminder_text', text)
    await state.clear()
    await message.answer('✅ متن یادآوری سرویس تست ذخیره شد.')
    await test_admin_home_message(message)


@test_service_router.callback_query(F.data == 'test_admin:reset_all')
async def test_admin_reset_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    db.reset_test_usage_for_all()
    await callback.answer('همه کاربران دوباره می توانند سرویس تست بگیرند.', show_alert=True)
    await edit_or_answer(callback, '🧪 تنظیمات سرویس تست', reply_markup=test_admin_keyboard())
