from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import api_client
import db
from keyboards import BTN_PRODUCTS, EditableButtonFilter, back_keyboard
from modules.packs import format_price, get_pack_meta, pack_title, pack_display_name, upsert_pack_meta, effective_pack_price
from permissions import can_manage_packs, can_view_packs, deny_callback, deny_message
from utils import edit_or_answer, answer_callback, message_text_with_custom_emoji_tokens

products_router = Router()


class ProductStates(StatesGroup):
    create_name = State()
    rename = State()
    description_text = State()
    test_description_text = State()


def products() -> list[dict]:
    return db.fetchall('SELECT * FROM products ORDER BY sort_order, id')


def active_products() -> list[dict]:
    return db.fetchall('SELECT * FROM products WHERE is_active=1 ORDER BY sort_order, id')


def product_row(product_id: int) -> dict | None:
    return db.fetchone('SELECT * FROM products WHERE id=?', (int(product_id),))


def product_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='➕ محصول جدید', callback_data='product:create')]]
    for p in products():
        icon = '✅' if int(p.get('is_active', 1)) else '⛔️'
        count = db.fetchone('SELECT COUNT(*) AS c FROM package_settings WHERE product_id=?', (p['id'],)) or {'c': 0}
        rows.append([InlineKeyboardButton(text=f"{icon} {p['name']} ({count['c']} بسته)", callback_data=f"product:view:{p['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📦 بسته‌های محصول', callback_data=f'product:packs:{product_id}')],
        [InlineKeyboardButton(text='🎛 بسته دلخواه', callback_data=f'custom_admin:view:{product_id}'), InlineKeyboardButton(text='💵 قیمت عمومی هر گیگ', callback_data=f'custom_admin:public:{product_id}')],
        [InlineKeyboardButton(text='➕ ساخت بسته جدید', callback_data=f'product:pack_create:{product_id}')],
        [InlineKeyboardButton(text='🔗 افزودن بسته موجود', callback_data=f'product:attach:{product_id}')],
        [InlineKeyboardButton(text='📝 توضیحات صفحه خرید', callback_data=f'product:description:{product_id}')],
        [InlineKeyboardButton(text='🧪 توضیحات صفحه تست', callback_data=f'product:test_description:{product_id}')],
        [InlineKeyboardButton(text='✏️ تغییر نام', callback_data=f'product:rename:{product_id}')],
        [InlineKeyboardButton(text='🔄 فعال/غیرفعال', callback_data=f'product:toggle:{product_id}')],
        [InlineKeyboardButton(text='⬆️ بالا', callback_data=f'product:sort:{product_id}:-1'), InlineKeyboardButton(text='⬇️ پایین', callback_data=f'product:sort:{product_id}:1')],
        [InlineKeyboardButton(text='🗑 حذف محصول', callback_data=f'product:delete:{product_id}')],
        [InlineKeyboardButton(text='🔙 محصولات', callback_data='product:list')],
    ])



def product_input_back_kb(product_id: int | None = None) -> InlineKeyboardMarkup:
    target = f'product:view:{int(product_id)}' if product_id else 'product:list'
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'product:input_back:{target}')]])

async def product_pack_keyboard(product_id: int) -> InlineKeyboardMarkup:
    panel = {str(p.get('id')): p for p in await api_client.get_user_templates()}
    metas = db.fetchall('SELECT * FROM package_settings WHERE product_id=? ORDER BY sort_order, template_id', (int(product_id),))
    rows = []
    for meta in metas:
        tid = str(meta['template_id'])
        pack = panel.get(tid, {'id': tid, 'name': f'بسته {tid}'})
        eye = '👁' if int(meta.get('is_visible', 1)) else '🙈'
        rows.append([InlineKeyboardButton(text=f"{eye} {pack_display_name(pack, meta)} | {format_price(effective_pack_price(meta, pack))}", callback_data=f'pack:view:{tid}')])
    cfg = product_row(product_id) or {}
    if int(cfg.get('custom_enabled') or 0):
        rows.append([InlineKeyboardButton(text='🎛 بسته دلخواه | فعال', callback_data=f'custom_admin:view:{product_id}')])
    else:
        rows.append([InlineKeyboardButton(text='🎛 بسته دلخواه | غیرفعال', callback_data=f'custom_admin:view:{product_id}')])
    if not rows:
        rows.append([InlineKeyboardButton(text='هنوز بسته‌ای ندارد', callback_data='product:noop')])
    rows.append([InlineKeyboardButton(text='➕ ساخت بسته', callback_data=f'product:pack_create:{product_id}')])
    rows.append([InlineKeyboardButton(text='🔗 افزودن بسته موجود', callback_data=f'product:attach:{product_id}')])
    rows.append([InlineKeyboardButton(text='🔙 محصول', callback_data=f'product:view:{product_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@products_router.message(EditableButtonFilter(BTN_PRODUCTS))
async def products_home(message: Message):
    if not can_view_packs(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer('🛍 محصولات', reply_markup=back_keyboard())
    await message.answer('محصولات و دسته‌بندی بسته‌ها:', reply_markup=product_keyboard())


@products_router.callback_query(F.data == 'product:list')
async def products_list(callback: CallbackQuery):
    if not can_view_packs(callback.from_user.id):
        await deny_callback(callback); return
    await edit_or_answer(callback, '🛍 محصولات', reply_markup=product_keyboard())
    await callback.answer()


@products_router.callback_query(F.data.startswith('product:input_back:'))
async def product_input_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    target=callback.data.split('product:input_back:',1)[1]
    if target == 'product:list':
        await edit_or_answer(callback,'🛍 محصولات',reply_markup=product_keyboard())
    elif target.startswith('product:view:'):
        pid=int(target.split(':')[-1]); p=product_row(pid)
        if p:
            status='فعال' if int(p.get('is_active',1)) else 'غیرفعال'; count=db.fetchone('SELECT COUNT(*) AS c FROM package_settings WHERE product_id=?',(pid,)) or {'c':0}
            await edit_or_answer(callback,f"🛍 {p['name']}\n\nوضعیت: {status}\nقیمت عمومی هر گیگ: {int(p.get('public_price_amount') or 0):,} تومان\nتعداد بسته‌ها: {count['c']}",reply_markup=product_detail_keyboard(pid))
        else:
            await edit_or_answer(callback,'🛍 محصولات',reply_markup=product_keyboard())
    await callback.answer('برگشتیم')

@products_router.callback_query(F.data == 'product:create')
async def product_create_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    await state.set_state(ProductStates.create_name)
    await edit_or_answer(callback, 'نام محصول جدید را بفرستید:', reply_markup=product_input_back_kb())
    await callback.answer()


@products_router.message(ProductStates.create_name)
async def product_create_finish(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None): return
    name = (message.text or '').strip()
    if not name:
        await message.answer('نام نمی‌تواند خالی باشد.'); return
    max_sort = db.fetchone('SELECT COALESCE(MAX(sort_order),0) AS n FROM products') or {'n': 0}
    db.execute('INSERT INTO products(name, sort_order, is_active, created_at, updated_at) VALUES(?,?,?,?,?)', (name, int(max_sort['n']) + 10, 1, db.now_iso(), db.now_iso()))
    await state.clear()
    await message.answer('محصول ساخته شد ✅\n\n🛍 محصولات', reply_markup=product_keyboard())


@products_router.callback_query(F.data.startswith('product:view:'))
async def product_view(callback: CallbackQuery):
    pid = int(callback.data.split(':')[-1]); p = product_row(pid)
    if not p:
        await callback.answer('محصول پیدا نشد.', show_alert=True); return
    status = 'فعال' if int(p.get('is_active',1)) else 'غیرفعال'
    count = db.fetchone('SELECT COUNT(*) AS c FROM package_settings WHERE product_id=?', (pid,)) or {'c':0}
    await edit_or_answer(callback, f"🛍 {p['name']}\n\nوضعیت: {status}\nقیمت عمومی هر گیگ: {int(p.get('public_price_amount') or 0):,} تومان\nتعداد بسته‌ها: {count['c']}", reply_markup=product_detail_keyboard(pid))
    await callback.answer()


@products_router.callback_query(F.data.startswith('product:packs:'))
async def product_packs(callback: CallbackQuery):
    pid = int(callback.data.split(':')[-1])
    # Acknowledge Telegram immediately. Panel calls can occasionally take many
    # seconds on the first request and callback queries expire quickly.
    await answer_callback(callback, '⏳ در حال بارگذاری بسته‌ها…')
    await edit_or_answer(callback, '⏳ در حال دریافت بسته‌های این محصول از پنل…')
    try:
        keyboard = await product_pack_keyboard(pid)
    except Exception as exc:
        await edit_or_answer(
            callback,
            '⚠️ ارتباط با پنل برای دریافت بسته‌ها طول کشید یا ناموفق بود.\n\nمی‌توانید دوباره تلاش کنید.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔄 تلاش دوباره', callback_data=f'product:packs:{pid}')],
                [InlineKeyboardButton(text='🔙 محصول', callback_data=f'product:view:{pid}')],
            ]),
        )
        return
    await edit_or_answer(callback, '📦 بسته‌های این محصول', reply_markup=keyboard)


@products_router.callback_query(F.data.startswith('product:attach:'))
async def product_attach(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    await answer_callback(callback, '⏳ در حال دریافت بسته‌های پنل…')
    await edit_or_answer(callback, '⏳ در حال دریافت بسته‌های موجود از پنل…')
    try:
        packs = await api_client.get_user_templates()
    except Exception:
        await edit_or_answer(
            callback,
            '⚠️ دریافت بسته‌ها از پنل ناموفق بود. دوباره تلاش کنید.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔄 تلاش دوباره', callback_data=f'product:attach:{pid}')],
                [InlineKeyboardButton(text='🔙 بسته‌های محصول', callback_data=f'product:packs:{pid}')],
            ]),
        )
        return
    rows = []
    for pack in packs:
        tid = str(pack.get('id')); meta = get_pack_meta(tid)
        owner = meta.get('product_id')
        mark = '✅' if owner and int(owner) == pid else ('↪️' if owner else '➕')
        rows.append([InlineKeyboardButton(text=f'{mark} {pack_display_name(pack, meta)}', callback_data=f'product:attach_do:{pid}:{tid}')])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت', callback_data=f'product:packs:{pid}')])
    await edit_or_answer(callback, 'یک بسته را انتخاب کنید. بسته‌ای که قبلاً در محصول دیگری باشد، به این محصول منتقل می‌شود:', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@products_router.callback_query(F.data.startswith('product:attach_do:'))
async def product_attach_do(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    _, _, pid, tid = callback.data.split(':', 3)
    meta = get_pack_meta(tid)
    upsert_pack_meta(tid, price_amount=int(meta.get('price_amount') or 0), is_visible=int(meta.get('is_visible',1)), sort_order=int(meta.get('sort_order',1000)))
    db.execute('DELETE FROM product_test_templates WHERE template_id=?', (str(tid),))
    p = product_row(int(pid)) or {}
    use_public = 1 if int(p.get('public_price_amount') or 0) > 0 else int(meta.get('use_product_price') or 0)
    db.execute('UPDATE package_settings SET product_id=?, use_product_price=?, updated_at=? WHERE template_id=?', (int(pid), use_public, db.now_iso(), str(tid)))
    await edit_or_answer(callback, 'بسته به محصول وصل شد ✅', reply_markup=await product_pack_keyboard(int(pid)))
    await callback.answer()


@products_router.callback_query(F.data.startswith('product:pack_create:'))
async def product_pack_create(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    from modules.packs import PackStates
    await state.set_state(PackStates.create_value)
    await state.update_data(step=0, payload={}, price_amount=None, product_id=pid)
    await callback.message.answer('نام بسته جدید را وارد کنید:')
    await callback.answer()



@products_router.callback_query(F.data.startswith('product:description:'))
async def product_description_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    p = product_row(pid)
    if not p:
        await callback.answer('محصول پیدا نشد.', show_alert=True); return
    current = str(p.get('description_text') or '').strip()
    if not current:
        legacy = str(db.get_setting(f'product_category_text:{pid}', '') or '').strip()
        current = legacy or '🛍 {product_name}\n\nبسته موردنظر را انتخاب کنید:'
    await state.set_state(ProductStates.description_text)
    await state.update_data(product_id=pid)
    await edit_or_answer(
        callback,
        '📝 توضیحات صفحه خرید این دسته را بفرستید.\n\n'
        'این متن فقط برای همین دسته ذخیره می‌شود و هیچ دسته دیگری تغییر نمی‌کند.\n'
        'متغیرهای عمومی ربات همگی قابل استفاده‌اند؛ نمونه: {product_name} {product_id} {name} {user_id}\n\n'
        f'متن فعلی:\n{current}',
        reply_markup=product_input_back_kb(pid),
    )
    await callback.answer()


@products_router.message(ProductStates.description_text)
async def product_description_finish(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        await state.clear(); return
    data = await state.get_data(); pid = int(data.get('product_id') or 0)
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن نمی‌تواند خالی باشد.'); return
    db.execute('UPDATE products SET description_text=?, updated_at=? WHERE id=?', (text, db.now_iso(), pid))
    db.set_setting(f'product_description:{pid}', text)
    # Remove old generic-live-edit key so it can never override another category.
    try: db.execute("DELETE FROM settings WHERE key=?", (f'product_category_text:{pid}',))
    except Exception: pass
    await state.clear()
    await message.answer('✅ توضیحات صفحه خرید فقط برای همین دسته ذخیره شد.', reply_markup=product_detail_keyboard(pid))


@products_router.callback_query(F.data.startswith('product:test_description:'))
async def product_test_description_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    p = product_row(pid)
    if not p:
        await callback.answer('محصول پیدا نشد.', show_alert=True); return
    current = str(p.get('test_description_text') or '').strip() or '🧪 تست {product_name}\n\nبرای دریافت سرویس تست روی دکمه زیر بزنید.'
    await state.set_state(ProductStates.test_description_text)
    await state.update_data(product_id=pid)
    await edit_or_answer(
        callback,
        '🧪 توضیحات صفحه تست این دسته را بفرستید.\n\n'
        'این متن مستقل از متن اصلی سرویس تست و مستقل از بقیه دسته‌هاست.\n'
        'تمام متغیرهای عمومی قابل استفاده‌اند؛ نمونه: {product_name} {package_name} {name} {user_id}\n\n'
        f'متن فعلی:\n{current}',
        reply_markup=product_input_back_kb(pid),
    )
    await callback.answer()


@products_router.message(ProductStates.test_description_text)
async def product_test_description_finish(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        await state.clear(); return
    data = await state.get_data(); pid = int(data.get('product_id') or 0)
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن نمی‌تواند خالی باشد.'); return
    db.execute('UPDATE products SET test_description_text=?, updated_at=? WHERE id=?', (text, db.now_iso(), pid))
    db.set_setting(f'test_service_product_text:{pid}', text)
    await state.clear()
    await message.answer('✅ توضیحات تست فقط برای همین دسته ذخیره شد.', reply_markup=product_detail_keyboard(pid))

@products_router.callback_query(F.data.startswith('product:rename:'))
async def product_rename_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid = int(callback.data.split(':')[-1])
    await state.set_state(ProductStates.rename); await state.update_data(product_id=pid)
    await edit_or_answer(callback, 'نام جدید محصول را بفرستید:', reply_markup=product_input_back_kb(pid)); await callback.answer()


@products_router.message(ProductStates.rename)
async def product_rename_finish(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None): return
    data = await state.get_data(); pid = int(data.get('product_id') or 0); name=(message.text or '').strip()
    if not name: await message.answer('نام نمی‌تواند خالی باشد.'); return
    db.execute('UPDATE products SET name=?, updated_at=? WHERE id=?', (name, db.now_iso(), pid)); await state.clear()
    p=product_row(pid) or {}; await message.answer(f"نام محصول تغییر کرد ✅\n\n🛍 {p.get('name','')}", reply_markup=product_detail_keyboard(pid))


@products_router.callback_query(F.data.startswith('product:toggle:'))
async def product_toggle(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid=int(callback.data.split(':')[-1]); p=product_row(pid)
    if p: db.execute('UPDATE products SET is_active=?, updated_at=? WHERE id=?', (0 if int(p.get('is_active',1)) else 1, db.now_iso(), pid))
    await product_view(callback)


@products_router.callback_query(F.data.startswith('product:sort:'))
async def product_sort(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    _,_,pid,delta=callback.data.split(':'); p=product_row(int(pid))
    if p: db.execute('UPDATE products SET sort_order=?, updated_at=? WHERE id=?', (int(p.get('sort_order') or 0)+int(delta)*10, db.now_iso(), int(pid)))
    await edit_or_answer(callback, '🛍 محصولات', reply_markup=product_keyboard()); await callback.answer()


@products_router.callback_query(F.data.startswith('product:delete:'))
async def product_delete(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    pid=int(callback.data.split(':')[-1])
    count=db.fetchone('SELECT COUNT(*) AS c FROM package_settings WHERE product_id=?',(pid,)) or {'c':0}
    if int(count['c'])>0:
        await callback.answer('اول بسته‌های این محصول را به محصول دیگری منتقل کنید.', show_alert=True); return
    db.execute('DELETE FROM product_test_templates WHERE product_id=?',(pid,)); db.execute('DELETE FROM products WHERE id=?',(pid,))
    await edit_or_answer(callback,'محصول حذف شد.',reply_markup=product_keyboard()); await callback.answer()


@products_router.callback_query(F.data == 'product:noop')
async def product_noop(callback: CallbackQuery):
    await callback.answer()
