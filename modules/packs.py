from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

import api_client
import db
from utils import edit_or_answer, answer_callback, message_text_with_custom_emoji_tokens, split_button_custom_emoji
from keyboards import EditableButtonFilter
from keyboards import BTN_PACKAGES, back_keyboard
from permissions import can_manage_packs, can_view_packs, deny_message, deny_callback

packs_router = Router()

# Required for creating a sellable package inside the bot. price_amount is local-only
# and is stored in package_settings; the rest is sent to the panel API.
REQUIRED = {'name', 'price_amount', 'data_limit', 'expire_duration', 'group_ids'}
STEPS = ['name', 'price_amount', 'data_limit', 'expire_duration', 'group_ids']


class PackStates(StatesGroup):
    create_value = State()
    create_groups = State()
    edit_value = State()
    edit_groups = State()
    price_value = State()
    bulk_price_percent = State()
    button_template_value = State()

def pack_create_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ بازگشت', callback_data='pack:create_cancel')]])


def parse_bytes(text: str) -> int:
    m = re.fullmatch(r'(\d+)\s*(b|kb|mb|gb|tb)?', (text or '').strip().lower())
    if not m:
        raise ValueError('مثال درست حجم: 50GB')
    n, unit = int(m.group(1)), m.group(2) or 'b'
    return n * {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}[unit]


def parse_duration(text: str) -> int:
    value = (text or '').strip().lower().replace(' ', '')
    if value.endswith('روز') or value.endswith('روزه'):
        value = value.replace('روزه', '').replace('روز', '')
        if value.isdigit():
            return int(value) * 86400
    m = re.fullmatch(r'(\d+)(s|m|h|d)?', value)
    if not m:
        raise ValueError('مثال درست مدت: 30d یا 30 روز')
    n, unit = int(m.group(1)), m.group(2) or 's'
    return n * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]


def parse_price(text: str) -> int:
    value = (text or '').strip().replace(',', '').replace('٬', '')
    value = value.replace('تومان', '').replace('تومن', '').replace('ریال', '').strip()
    if not re.fullmatch(r'\d+', value):
        raise ValueError('مبلغ باید عدد صحیح باشد. مثال: 90000')
    amount = int(value)
    if amount < 0:
        raise ValueError('مبلغ نمی‌تواند منفی باشد.')
    return amount


def parse_percent(text: str) -> Decimal:
    value = (text or '').strip().replace('%', '').replace(',', '.')
    if not re.fullmatch(r'\d+(\.\d{1,4})?', value):
        raise ValueError('درصد باید عدد مثبت باشد. مثال: 10 یا 12.5')
    percent = Decimal(value)
    if percent <= 0:
        raise ValueError('درصد باید بزرگ‌تر از صفر باشد.')
    if percent > 1000:
        raise ValueError('درصد خیلی بزرگ است.')
    return percent


def parse_username_affix(text: str) -> str:
    value = (text or '').strip()
    if value in {'-', 'حذف', 'خالی', '0'}:
        return ''
    if not value:
        return ''
    if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', value):
        raise ValueError('پیشوند/پسوند فقط می‌تواند شامل حروف انگلیسی، عدد و آندرلاین باشد. فاصله، @ و خط تیره مجاز نیست.')
    return value


def pack_volume_gb(pack: dict | None) -> Decimal:
    if not pack:
        return Decimal('0')
    try:
        raw = Decimal(str(pack.get('data_limit') or 0))
    except Exception:
        return Decimal('0')
    if raw <= 0:
        return Decimal('0')
    return raw / Decimal(1024 ** 3)


def public_price_for_pack(product_id: int | str | None, pack: dict | None) -> int:
    if not product_id:
        return 0
    row = db.fetchone('SELECT public_price_amount FROM products WHERE id=?', (int(product_id),)) or {}
    per_gb = int(row.get('public_price_amount') or 0)
    if per_gb <= 0:
        return 0
    gb = pack_volume_gb(pack)
    if gb <= 0:
        return 0
    return int((gb * Decimal(per_gb)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def effective_pack_price(meta: dict, pack: dict | None = None) -> int:
    if int(meta.get('use_product_price') or 0) and meta.get('product_id'):
        public_total = public_price_for_pack(meta.get('product_id'), pack)
        if public_total > 0:
            return public_total
    return int(meta.get('price_amount') or 0)




PACKAGE_BUTTON_DEFAULT_TEMPLATE = "📦 {package_name} | {price}"
PACKAGE_BUTTON_VARIABLES = {
    'package_name', 'name', 'package_id', 'template_id', 'volume_gb', 'traffic_gb', 'data_gb', 'gb',
    'price', 'price_toman', 'price_raw', 'duration_days', 'days', 'duration_months',
    'product_name', 'product_id',
}


def _pretty_decimal(value: Decimal) -> str:
    q = value.quantize(Decimal('0.01'))
    text = format(q, 'f').rstrip('0').rstrip('.')
    return text or '0'


def package_button_context(pack: dict, meta: dict) -> dict[str, str]:
    gb = pack_volume_gb(pack)
    seconds = int(pack.get('expire_duration') or 0)
    days = Decimal(seconds) / Decimal(86400) if seconds > 0 else Decimal('0')
    months = days / Decimal(30) if days > 0 else Decimal('0')
    price_raw = effective_pack_price(meta, pack)
    product_id = meta.get('product_id')
    product_name = ''
    if product_id:
        row = db.fetchone('SELECT name FROM products WHERE id=?', (int(product_id),)) or {}
        product_name = str(row.get('name') or '')
    name = pack_title(pack)
    tid = str(pack.get('id') or meta.get('template_id') or '')
    return {
        'package_name': name, 'name': name, 'package_id': tid, 'template_id': tid,
        'volume_gb': _pretty_decimal(gb), 'traffic_gb': _pretty_decimal(gb), 'data_gb': _pretty_decimal(gb), 'gb': _pretty_decimal(gb),
        'price': format_price(price_raw), 'price_toman': f'{int(price_raw):,}', 'price_raw': str(int(price_raw)),
        'duration_days': _pretty_decimal(days), 'days': _pretty_decimal(days),
        'duration_months': _pretty_decimal(months),
        'product_name': product_name, 'product_id': str(product_id or ''),
    }


def validate_package_button_template(template: str) -> str:
    template = str(template or '').strip()
    if not template:
        raise ValueError('قالب دکمه نمی‌تواند خالی باشد.')
    fields = set(re.findall(r'\{([A-Za-z0-9_]+)\}', template))
    unknown = sorted(fields - PACKAGE_BUTTON_VARIABLES)
    if unknown:
        raise ValueError('متغیر ناشناخته: ' + ', '.join('{' + x + '}' for x in unknown))
    return template


def package_button_template(meta: dict) -> str:
    custom = str(meta.get('button_template') or '').strip()
    if custom:
        return custom
    return str(db.get_setting('customer_pack_button_template', PACKAGE_BUTTON_DEFAULT_TEMPLATE) or PACKAGE_BUTTON_DEFAULT_TEMPLATE)


def render_package_button_text(pack: dict, meta: dict) -> str:
    template = package_button_template(meta)
    ctx = package_button_context(pack, meta)
    try:
        rendered = template.format_map(ctx)
    except Exception:
        rendered = PACKAGE_BUTTON_DEFAULT_TEMPLATE.format_map(ctx)
    # Button custom emoji token(s) are deliberately kept here; utils' global
    # button constructor converts the first token to icon_custom_emoji_id.
    return str(rendered).strip() or PACKAGE_BUTTON_DEFAULT_TEMPLATE.format_map(ctx)

def format_price(amount: int | None, currency: str = 'TOMAN') -> str:
    amount = int(amount or 0)
    return f'{amount:,} تومان'


def pack_title(pack: dict) -> str:
    return pack.get('name') or f"بسته {pack.get('id')}"


def get_pack_meta(template_id: str) -> dict:
    row = db.fetchone('SELECT * FROM package_settings WHERE template_id=?', (str(template_id),))
    if row:
        return row
    return {'template_id': str(template_id), 'price_amount': 0, 'price_currency': 'TOMAN', 'sort_order': 1000, 'is_visible': 1}


def upsert_pack_meta(
    template_id: str,
    *,
    price_amount: int | None = None,
    price_currency: str = 'TOMAN',
    is_visible: int | None = None,
    sort_order: int | None = None,
    use_product_price: int | None = None,
) -> None:
    current = get_pack_meta(str(template_id))
    if price_amount is None:
        price_amount = int(current.get('price_amount') or 0)
    if is_visible is None:
        is_visible = int(current.get('is_visible', 1))
    if sort_order is None:
        sort_order = int(current.get('sort_order', 1000))
    if use_product_price is None:
        use_product_price = int(current.get('use_product_price') or 0)
    db.execute(
        '''INSERT INTO package_settings
           (template_id, price_amount, price_currency, is_visible, sort_order, use_product_price, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(template_id) DO UPDATE SET
             price_amount=excluded.price_amount,
             price_currency=excluded.price_currency,
             is_visible=excluded.is_visible,
             sort_order=excluded.sort_order,
             use_product_price=excluded.use_product_price,
             updated_at=excluded.updated_at''',
        (str(template_id), int(price_amount), price_currency, int(is_visible), int(sort_order), int(use_product_price), db.now_iso()),
    )


def local_meta_map() -> dict[str, dict]:
    return {str(r['template_id']): r for r in db.fetchall('SELECT * FROM package_settings')}


def list_keyboard(packs: list[dict], can_manage: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_manage:
        rows.append([InlineKeyboardButton(text='➕ ساخت بسته جدید', callback_data='pack:create')])
        rows.append([
            InlineKeyboardButton(text='📈 افزایش درصدی قیمت‌ها', callback_data='pack:bulk_price:inc'),
            InlineKeyboardButton(text='📉 کاهش درصدی قیمت‌ها', callback_data='pack:bulk_price:dec'),
        ])
    local = local_meta_map()
    packs.sort(key=lambda p: (local.get(str(p.get('id')), {}).get('sort_order', 1000), p.get('id', 0)))
    for pack in packs:
        pid = str(pack.get('id'))
        meta = local.get(pid, {})
        eye = '👁' if meta.get('is_visible', 1) else '🙈'
        price = format_price(effective_pack_price(meta, pack), meta.get('price_currency', 'TOMAN'))
        rows.append([InlineKeyboardButton(text=f'{eye} {pack_title(pack)} | {price}', callback_data=f'pack:view:{pid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def detail_text(pack: dict) -> str:
    template_id = str(pack.get('id'))
    meta = get_pack_meta(template_id)
    price = format_price(effective_pack_price(meta, pack), meta.get('price_currency', 'TOMAN'))
    price_mode = 'قیمت عمومی هر گیگ × حجم بسته' if int(meta.get('use_product_price') or 0) else 'قیمت اختصاصی'
    visible = 'بله' if meta.get('is_visible', 1) else 'خیر'
    return (
        '📦 جزئیات بسته\n\n'
        f'ID: {pack.get("id")}\n'
        f'نام: {pack.get("name")}\n'
        f'قیمت فروش: {price} ({price_mode})\n'
        + (f"قیمت عمومی هر گیگ: {int((db.fetchone('SELECT public_price_amount FROM products WHERE id=?', (int(meta.get('product_id')),)) or {}).get('public_price_amount') or 0):,} تومان\n" if int(meta.get('use_product_price') or 0) and meta.get('product_id') else '')
        + f'نمایش در ربات: {visible}\n'
        + f'قالب دکمه مشتری: {"اختصاصی" if str(meta.get("button_template") or "").strip() else "عمومی"}\n'
        f'ترتیب نمایش: {meta.get("sort_order", 1000)}\n'
        f'پیش از نام: {pack.get("username_prefix") or "ندارد"}\n'
        f'پس از نام: {pack.get("username_suffix") or "ندارد"}\n\n'
        f'حجم: {pack.get("data_limit")} بایت\n'
        f'HWID: {pack.get("hwid_limit") or "دیفالت سرور"}\n'
        f'مدت: {pack.get("expire_duration")} ثانیه\n'
        f'گروه‌ها: {pack.get("group_ids")}\n'
        f'وضعیت: {pack.get("status")}\n'
        f'غیرفعال: {pack.get("is_disabled")}\n'
    )


def detail_keyboard(template_id: str, can_manage: bool) -> InlineKeyboardMarkup:
    meta = get_pack_meta(template_id)
    product_id = meta.get('product_id')
    back_cb = f'product:packs:{int(product_id)}' if product_id else 'product:list'
    back_text = '🔙 بسته‌های محصول' if product_id else '🔙 محصولات'
    rows = [[InlineKeyboardButton(text=back_text, callback_data=back_cb)]]
    if can_manage:
        rows = [
            [InlineKeyboardButton(text='💰 قیمت', callback_data=f'pack:price:{template_id}'), InlineKeyboardButton(text='🔗 قیمت عمومی', callback_data=f'pack:public_price:{template_id}')],
            [InlineKeyboardButton(text='✏️ نام', callback_data=f'pack:edit:{template_id}:name'), InlineKeyboardButton(text='📦 حجم', callback_data=f'pack:edit:{template_id}:data_limit')],
            [InlineKeyboardButton(text='⏳ مدت', callback_data=f'pack:edit:{template_id}:expire_duration'), InlineKeyboardButton(text='👥 گروه‌ها', callback_data=f'pack:edit:{template_id}:group_ids')],
            [InlineKeyboardButton(text='⬅️ پیش از نام', callback_data=f'pack:edit:{template_id}:username_prefix'), InlineKeyboardButton(text='➡️ پس از نام', callback_data=f'pack:edit:{template_id}:username_suffix')],
            [InlineKeyboardButton(text='🧩 قالب دکمه مشتری', callback_data=f'pack:button_template:{template_id}')],
            [InlineKeyboardButton(text='👁 نمایش/عدم نمایش', callback_data=f'pack:visible:{template_id}')],
            [InlineKeyboardButton(text='⬆️ بالا', callback_data=f'pack:sort:{template_id}:-1'), InlineKeyboardButton(text='⬇️ پایین', callback_data=f'pack:sort:{template_id}:1')],
            [InlineKeyboardButton(text='🗑 حذف', callback_data=f'pack:delete:{template_id}')],
            [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_keyboard(groups: list[dict], selected: list[int], mode: str, template_id: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    selected_set = set(selected)
    for group in groups:
        gid = int(group['id'])
        mark = '✅' if gid in selected_set else '⬜️'
        cb = f'pack:g:create:{gid}' if mode == 'create' else f'pack:g:edit:{template_id}:{gid}'
        rows.append([InlineKeyboardButton(text=f'{mark} {group.get("name")}', callback_data=cb)])
    rows.append([InlineKeyboardButton(text='✅ انتخاب تمام گروه‌ها', callback_data='pack:g:create_all' if mode == 'create' else f'pack:g:edit_all:{template_id}')])
    rows.append([InlineKeyboardButton(text='☑️ تایید گروه‌ها', callback_data='pack:g:create_done' if mode == 'create' else f'pack:g:edit_done:{template_id}')])
    rows.append([InlineKeyboardButton(text='⬅️ بازگشت', callback_data='pack:create_cancel' if mode == 'create' else f'pack:view:{template_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def list_packs_message(message: Message):
    # The public admin entry is Products now; keep this helper as a compatibility
    # redirect for stale keyboards and old callbacks.
    from modules.products import product_keyboard
    await message.answer('🛍 محصولات', reply_markup=back_keyboard())
    await message.answer('محصولات و دسته‌بندی بسته‌ها:', reply_markup=product_keyboard())


@packs_router.message(EditableButtonFilter(BTN_PACKAGES))
async def packs_home(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not can_view_packs(uid):
        await deny_message(message)
        return
    await list_packs_message(message)


@packs_router.callback_query(F.data == 'pack:list')
async def packs_list(callback: CallbackQuery):
    if not can_view_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    from modules.products import product_keyboard
    await edit_or_answer(callback, '🛍 محصولات', reply_markup=product_keyboard())
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:view:'))
async def pack_view(callback: CallbackQuery):
    if not can_view_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    await answer_callback(callback, '⏳ در حال بارگذاری جزئیات…')
    try:
        pack = await api_client.get_user_template(template_id)
    except Exception:
        await edit_or_answer(
            callback,
            '⚠️ دریافت جزئیات بسته از پنل طول کشید یا ناموفق بود.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔄 تلاش دوباره', callback_data=f'pack:view:{template_id}')],
                [InlineKeyboardButton(text='⬅️ بازگشت', callback_data='pack:list')],
            ]),
        )
        return
    await edit_or_answer(callback, detail_text(pack), reply_markup=detail_keyboard(template_id, can_manage_packs(callback.from_user.id)))


@packs_router.callback_query(F.data == 'pack:create')
async def pack_create_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(PackStates.create_value)
    await state.update_data(step=0, payload={}, price_amount=None)
    await callback.message.answer('نام بسته را وارد کنید:', reply_markup=pack_create_back_keyboard())
    await callback.answer()


@packs_router.message(PackStates.create_value)
async def pack_create_value(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    step = data.get('step', 0)
    payload = data.get('payload', {})

    # Only the first four STEPS are text-input stages. group_ids is selected
    # via inline callbacks. Older/interrupted FSM data could leave step=4 while
    # still in create_value; recover instead of indexing past the input stages.
    input_steps = STEPS[:-1]
    if not isinstance(step, int) or step < 0:
        step = 0
        await state.update_data(step=0)
    if step >= len(input_steps):
        await state.set_state(PackStates.create_groups)
        try:
            groups = await api_client.get_groups()
        except Exception:
            await message.answer(
                'اطلاعات مراحل ساخت بسته حفظ شده ✅\n'
                'اما دریافت گروه‌های پنل موقتاً خطا داد. دوباره تلاش کنید:',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔄 تلاش دوباره برای گروه‌ها', callback_data='pack:g:create_load')],[InlineKeyboardButton(text='⬅️ بازگشت', callback_data='pack:create_cancel')]]),
            )
            return
        await message.answer('گروه‌ها را انتخاب کنید:', reply_markup=group_keyboard(groups, data.get('selected_groups', []), 'create'))
        return

    field = input_steps[step]
    try:
        if field == 'name':
            value = (message.text or '').strip()
            if not value:
                raise ValueError('نام بسته نمی‌تواند خالی باشد.')
            payload[field] = value
        elif field == 'price_amount':
            price_amount = parse_price(message.text or '')
            await state.update_data(price_amount=price_amount, payload=payload)
        elif field == 'data_limit':
            payload[field] = parse_bytes(message.text or '')
        elif field == 'expire_duration':
            payload[field] = parse_duration(message.text or '')

        next_step = step + 1
        if next_step == len(input_steps):
            # Change FSM state first so a duplicate/fast second message cannot
            # observe create_value together with terminal step=4.
            await state.set_state(PackStates.create_groups)
            await state.update_data(payload=payload, step=next_step, selected_groups=[])
        else:
            await state.update_data(payload=payload, step=next_step)
    except Exception as exc:
        await message.answer(f'خطا: {exc}', reply_markup=pack_create_back_keyboard())
        return

    if next_step == 1:
        await message.answer('قیمت بسته را به تومان وارد کنید. مثال: 90000', reply_markup=pack_create_back_keyboard())
    elif next_step == 2:
        await message.answer('حجم بسته را وارد کنید. مثال: 50GB', reply_markup=pack_create_back_keyboard())
    elif next_step == 3:
        await message.answer('مدت اعتبار را وارد کنید. مثال: 30d یا 30 روز', reply_markup=pack_create_back_keyboard())
    elif next_step == 4:
        # State was already switched before step=4 was persisted. Now loading
        # panel groups is safe even with duplicate/very fast user messages.
        try:
            groups = await api_client.get_groups()
        except Exception as exc:
            await message.answer(
                'مدت بسته ثبت شد ✅\n'
                'اما دریافت گروه‌های پنل موقتاً خطا داد. دوباره تلاش کنید:',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔄 تلاش دوباره برای گروه‌ها', callback_data='pack:g:create_load')],[InlineKeyboardButton(text='⬅️ بازگشت', callback_data='pack:create_cancel')]]),
            )
            return
        await message.answer('گروه‌ها را انتخاب کنید:', reply_markup=group_keyboard(groups, [], 'create'))


@packs_router.callback_query(F.data == 'pack:create_cancel')
async def pack_create_cancel(callback: CallbackQuery, state: FSMContext):
    data=await state.get_data(); pid=int(data.get('product_id') or 0); await state.clear()
    from modules.products import product_pack_keyboard, product_keyboard
    if pid:
        await edit_or_answer(callback,'📦 بسته‌های این محصول',reply_markup=await product_pack_keyboard(pid))
    else:
        await edit_or_answer(callback,'🛍 محصولات',reply_markup=product_keyboard())
    await callback.answer('برگشتیم')


@packs_router.callback_query(F.data == 'pack:g:create_load')
async def pack_create_groups_load(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    data = await state.get_data()
    try:
        groups = await api_client.get_groups()
    except Exception:
        await callback.answer('هنوز ارتباط با گروه‌های پنل برقرار نشد. دوباره امتحان کنید.', show_alert=True)
        return
    await state.set_state(PackStates.create_groups)
    selected = data.get('selected_groups', [])
    await edit_or_answer(callback, 'گروه‌ها را انتخاب کنید:', reply_markup=group_keyboard(groups, selected, 'create'))
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:g:create:'))
async def pack_create_group_toggle(callback: CallbackQuery, state: FSMContext):
    gid = int(callback.data.split(':')[-1])
    data = await state.get_data()
    selected = data.get('selected_groups', [])
    if gid in selected:
        selected.remove(gid)
    else:
        selected.append(gid)
    await state.update_data(selected_groups=selected)
    await callback.message.edit_reply_markup(reply_markup=group_keyboard(await api_client.get_groups(), selected, 'create'))
    await callback.answer()


@packs_router.callback_query(F.data == 'pack:g:create_all')
async def pack_create_group_all(callback: CallbackQuery, state: FSMContext):
    groups = await api_client.get_groups()
    selected = [int(g['id']) for g in groups]
    await state.update_data(selected_groups=selected)
    await callback.message.edit_reply_markup(reply_markup=group_keyboard(groups, selected, 'create'))
    await callback.answer('همه انتخاب شدند.')


@packs_router.callback_query(F.data == 'pack:g:create_done')
async def pack_create_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_groups', [])
    if not selected:
        await callback.answer('حداقل یک گروه انتخاب کنید.', show_alert=True)
        return
    payload = data.get('payload', {})
    price_amount = data.get('price_amount')
    if price_amount is None:
        await callback.answer('قیمت بسته ثبت نشده است.', show_alert=True)
        return
    payload['group_ids'] = selected
    payload.setdefault('data_limit_reset_strategy', 'no_reset')
    pack = await api_client.create_user_template(payload)
    template_id = str(pack.get('id'))
    product_id = data.get('product_id')
    use_public = 0
    if product_id:
        prow = db.fetchone('SELECT public_price_amount FROM products WHERE id=?', (int(product_id),)) or {}
        use_public = 1 if int(prow.get('public_price_amount') or 0) > 0 else 0
    upsert_pack_meta(template_id, price_amount=int(price_amount), use_product_price=use_public)
    if product_id:
        db.execute('UPDATE package_settings SET product_id=?, use_product_price=?, updated_at=? WHERE template_id=?', (int(product_id), use_public, db.now_iso(), template_id))
    db.add_log(callback.from_user.id, 'create_pack', 'pack', template_id, {**payload, 'price_amount': price_amount})
    await state.clear()
    fresh = await api_client.get_user_template(template_id)
    await edit_or_answer(callback, 'بسته ساخته شد.\n\n' + detail_text(fresh), reply_markup=detail_keyboard(template_id, True))
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:price:'))
async def pack_price_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    meta = get_pack_meta(template_id)
    await state.set_state(PackStates.price_value)
    await state.update_data(template_id=template_id)
    await callback.message.answer(
        'قیمت جدید بسته را به تومان وارد کنید.\n'
        f'قیمت فعلی: {format_price(meta.get("price_amount", 0), meta.get("price_currency", "TOMAN"))}\n'
        'مثال: 90000'
    )
    await callback.answer()


@packs_router.message(PackStates.price_value)
async def pack_price_value(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    template_id = str(data.get('template_id'))
    try:
        amount = parse_price(message.text or '')
        meta = get_pack_meta(template_id)
        upsert_pack_meta(
            template_id,
            price_amount=amount,
            price_currency='TOMAN',
            is_visible=int(meta.get('is_visible', 1)),
            sort_order=int(meta.get('sort_order', 1000)),
        )
        db.add_log(message.from_user.id, 'edit_pack_price', 'pack', template_id, {'price_amount': amount})
        await state.clear()
        pack = await api_client.get_user_template(template_id)
        await message.answer('قیمت بسته ویرایش شد.\n\n' + detail_text(pack), reply_markup=detail_keyboard(template_id, True))
    except Exception as exc:
        await message.answer(f'خطا: {exc}', reply_markup=pack_create_back_keyboard())


@packs_router.callback_query(F.data.startswith('pack:bulk_price:'))
async def bulk_price_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    mode = callback.data.split(':')[-1]
    await state.set_state(PackStates.bulk_price_percent)
    await state.update_data(mode=mode)
    label = 'افزایش' if mode == 'inc' else 'کاهش'
    await edit_or_answer(
        callback,
        f'درصد {label} قیمت همه بسته‌ها را وارد کنید.\n\nمثال: 10 یعنی {label} ۱۰ درصدی',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='pack:list')]]),
    )
    await callback.answer()


@packs_router.message(PackStates.bulk_price_percent)
async def bulk_price_percent_value(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    mode = data.get('mode', 'inc')
    try:
        percent = parse_percent(message.text or '')
        factor = Decimal('1') + (percent / Decimal('100')) if mode == 'inc' else Decimal('1') - (percent / Decimal('100'))
        if factor < 0:
            factor = Decimal('0')
        packs = await api_client.get_user_templates()
        changed = 0
        unchanged_zero = 0
        for pack in packs:
            template_id = str(pack.get('id'))
            if not template_id or template_id == 'None':
                continue
            meta = get_pack_meta(template_id)
            old_price = int(meta.get('price_amount') or 0)
            if old_price == 0:
                unchanged_zero += 1
                upsert_pack_meta(
                    template_id,
                    price_amount=0,
                    price_currency='TOMAN',
                    is_visible=int(meta.get('is_visible', 1)),
                    sort_order=int(meta.get('sort_order', 1000)),
                )
                continue
            new_price = int((Decimal(old_price) * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if new_price < 0:
                new_price = 0
            upsert_pack_meta(
                template_id,
                price_amount=new_price,
                price_currency='TOMAN',
                is_visible=int(meta.get('is_visible', 1)),
                sort_order=int(meta.get('sort_order', 1000)),
            )
            changed += 1
        db.add_log(message.from_user.id, 'bulk_update_pack_prices', 'pack', None, {'mode': mode, 'percent': str(percent), 'changed': changed, 'zero': unchanged_zero})
        await state.clear()
        direction = 'افزایش' if mode == 'inc' else 'کاهش'
        await message.answer(
            f'قیمت‌ها با موفقیت بروزرسانی شدند.\n'
            f'نوع تغییر: {direction} {percent}%\n'
            f'تعداد بسته‌های تغییرکرده: {changed}\n'
            f'بسته‌های با قیمت صفر: {unchanged_zero}',
            reply_markup=back_keyboard(),
        )
        await list_packs_message(message)
    except Exception as exc:
        await message.answer(f'خطا: {exc}', reply_markup=pack_create_back_keyboard())


@packs_router.callback_query(F.data.startswith('pack:edit:'))
async def pack_edit_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    _, _, template_id, field = callback.data.split(':', 3)
    if field == 'group_ids':
        pack = await api_client.get_user_template(template_id)
        selected = [int(x) for x in pack.get('group_ids') or []]
        await state.update_data(template_id=template_id, selected_groups=selected)
        await state.set_state(PackStates.edit_groups)
        await edit_or_answer(callback, 'گروه‌ها را انتخاب کنید:', reply_markup=group_keyboard(await api_client.get_groups(), selected, 'edit', template_id))
        await callback.answer()
        return
    await state.set_state(PackStates.edit_value)
    await state.update_data(template_id=template_id, field=field)
    if field in {'username_prefix', 'username_suffix'}:
        current = (await api_client.get_user_template(template_id)).get(field) or 'ندارد'
        await callback.message.answer(
            f'مقدار جدید را ارسال کنید. مقدار فعلی: {current}\n\n'
            'برای حذف کامل، یک خط تیره بفرستید: -\n'
            'فقط حروف انگلیسی، عدد و آندرلاین مجاز است.'
        )
    else:
        await callback.message.answer('مقدار جدید را ارسال کنید:')
    await callback.answer()


@packs_router.message(PackStates.edit_value)
async def pack_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    template_id, field = data.get('template_id'), data.get('field')
    pack = await api_client.get_user_template(template_id)
    payload = dict(pack)
    try:
        if field == 'name':
            value = (message.text or '').strip()
            if not value:
                raise ValueError('نام نمی‌تواند خالی باشد.')
            payload[field] = value
        elif field == 'data_limit':
            payload[field] = parse_bytes(message.text or '')
        elif field == 'expire_duration':
            payload[field] = parse_duration(message.text or '')
        elif field in {'username_prefix', 'username_suffix'}:
            payload[field] = parse_username_affix(message.text or '')
        updated = await api_client.update_user_template(template_id, payload)
        db.add_log(message.from_user.id, 'edit_pack', 'pack', str(template_id), {field: payload[field]})
        await state.clear()
        await message.answer('ویرایش شد.\n\n' + detail_text(updated), reply_markup=detail_keyboard(str(template_id), True))
    except Exception as exc:
        await message.answer(f'خطا: {exc}', reply_markup=pack_create_back_keyboard())


@packs_router.callback_query(F.data.startswith('pack:g:edit:'))
async def pack_edit_group_toggle(callback: CallbackQuery, state: FSMContext):
    _, _, _, template_id, gid = callback.data.split(':')
    gid = int(gid)
    data = await state.get_data()
    selected = data.get('selected_groups', [])
    if gid in selected:
        selected.remove(gid)
    else:
        selected.append(gid)
    await state.update_data(selected_groups=selected)
    await callback.message.edit_reply_markup(reply_markup=group_keyboard(await api_client.get_groups(), selected, 'edit', template_id))
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:g:edit_all:'))
async def pack_edit_group_all(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split(':')[-1]
    groups = await api_client.get_groups()
    selected = [int(g['id']) for g in groups]
    await state.update_data(selected_groups=selected)
    await callback.message.edit_reply_markup(reply_markup=group_keyboard(groups, selected, 'edit', template_id))
    await callback.answer('همه انتخاب شدند.')


@packs_router.callback_query(F.data.startswith('pack:g:edit_done:'))
async def pack_edit_group_done(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split(':')[-1]
    data = await state.get_data()
    selected = data.get('selected_groups', [])
    if not selected:
        await callback.answer('حداقل یک گروه انتخاب کنید.', show_alert=True)
        return
    pack = await api_client.get_user_template(template_id)
    pack['group_ids'] = selected
    updated = await api_client.update_user_template(template_id, pack)
    db.add_log(callback.from_user.id, 'edit_pack_groups', 'pack', str(template_id), {'group_ids': selected})
    await state.clear()
    await edit_or_answer(callback, 'ذخیره شد.\n\n' + detail_text(updated), reply_markup=detail_keyboard(str(template_id), True))
    await callback.answer()



@packs_router.callback_query(F.data.startswith('pack:button_template:'))
async def pack_button_template_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    template_id = callback.data.split(':')[-1]
    meta = get_pack_meta(template_id)
    try:
        pack = await api_client.get_user_template(template_id)
    except Exception:
        await callback.answer('جزئیات بسته از پنل دریافت نشد.', show_alert=True); return
    current = package_button_template(meta)
    preview = render_package_button_text(pack, meta)
    await state.set_state(PackStates.button_template_value)
    await state.update_data(template_id=template_id)
    rows = [[InlineKeyboardButton(text='↩️ استفاده از قالب عمومی', callback_data=f'pack:button_template_reset:{template_id}')],
            [InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'pack:view:{template_id}')]]
    await callback.message.answer(
        '🧩 قالب دکمه همین بسته\n\n'
        f'قالب فعلی:\n{current}\n\n'
        f'پیش‌نمایش فعلی:\n{preview}\n\n'
        'متغیرها:\n'
        '{package_name} {volume_gb} {price} {price_raw} {duration_days} {duration_months} '
        '{product_name} {package_id}\n\n'
        'می‌توانی قبل/بین/بعد متغیرها متن یا Premium Emoji بگذاری. قالب جدید را بفرست.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@packs_router.message(PackStates.button_template_value)
async def pack_button_template_value(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data(); template_id = str(data.get('template_id') or '')
    try:
        raw = message_text_with_custom_emoji_tokens(message)
        template = validate_package_button_template(raw)
        meta = get_pack_meta(template_id)
        pack = await api_client.get_user_template(template_id)
        test_meta = dict(meta); test_meta['button_template'] = template
        preview = render_package_button_text(pack, test_meta)
        visible, _ = split_button_custom_emoji(preview)
        if len(visible) > 64:
            raise ValueError(f'متن نهایی دکمه {len(visible)} کاراکتر است؛ حداکثر 64 کاراکتر باشد.')
        db.execute('UPDATE package_settings SET button_template=?, updated_at=? WHERE template_id=?', (template, db.now_iso(), template_id))
        db.add_log(message.from_user.id, 'package_button_template_changed', 'pack', template_id, {'template': template})
        await state.clear()
        await message.answer('قالب دکمه ذخیره شد ✅\n\nپیش‌نمایش: ' + preview, reply_markup=detail_keyboard(template_id, True))
    except Exception as exc:
        await message.answer(f'خطا: {exc}')


@packs_router.callback_query(F.data.startswith('pack:button_template_reset:'))
async def pack_button_template_reset(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    template_id = callback.data.split(':')[-1]
    db.execute('UPDATE package_settings SET button_template=NULL, updated_at=? WHERE template_id=?', (db.now_iso(), template_id))
    db.add_log(callback.from_user.id, 'package_button_template_reset', 'pack', template_id, {})
    await state.clear()
    pack = await api_client.get_user_template(template_id)
    await edit_or_answer(callback, 'قالب اختصاصی حذف شد و این بسته از قالب عمومی استفاده می‌کند.\n\n' + detail_text(pack), reply_markup=detail_keyboard(template_id, True))
    await callback.answer('قالب عمومی فعال شد ✅')


@packs_router.callback_query(F.data.startswith('pack:public_price:'))
async def pack_public_price_toggle(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback); return
    template_id = callback.data.split(':')[-1]
    meta = get_pack_meta(template_id)
    if not meta.get('product_id'):
        await callback.answer('این بسته به محصولی وصل نیست.', show_alert=True); return
    new_value = 0 if int(meta.get('use_product_price') or 0) else 1
    if new_value:
        product = db.fetchone('SELECT public_price_amount FROM products WHERE id=?', (int(meta.get('product_id')),)) or {}
        per_gb = int(product.get('public_price_amount') or 0)
        if per_gb <= 0:
            await callback.answer('اول قیمت عمومی هر گیگ این محصول را روی عدد بیشتر از صفر تنظیم کنید.', show_alert=True)
            return
        try:
            pack = await api_client.get_user_template(template_id)
        except Exception as exc:
            await callback.answer(f'جزئیات بسته از پنل دریافت نشد؛ قیمت عمومی فعال نشد. {type(exc).__name__}', show_alert=True)
            return
        if pack_volume_gb(pack) <= 0:
            await callback.answer('حجم این بسته در پنل صفر یا نامعتبر است؛ قیمت عمومی فعال نشد.', show_alert=True)
            return
    upsert_pack_meta(template_id, use_product_price=new_value)
    pack = await api_client.get_user_template(template_id)
    await edit_or_answer(callback, detail_text(pack), reply_markup=detail_keyboard(template_id, True))
    await callback.answer('از این پس قیمت بسته از قیمت عمومی هر گیگ × حجم محاسبه می‌شود.' if new_value else 'قیمت اختصاصی بسته فعال شد.')

@packs_router.callback_query(F.data.startswith('pack:visible:'))
async def pack_visible(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    meta = get_pack_meta(template_id)
    new_value = 0 if int(meta.get('is_visible', 1)) else 1
    upsert_pack_meta(
        template_id,
        price_amount=int(meta.get('price_amount') or 0),
        price_currency=meta.get('price_currency', 'TOMAN'),
        is_visible=new_value,
        sort_order=int(meta.get('sort_order', 1000)),
    )
    db.add_log(callback.from_user.id, 'toggle_pack_visibility', 'pack', template_id, {'is_visible': new_value})
    await callback.answer('انجام شد.')
    pack = await api_client.get_user_template(template_id)
    await edit_or_answer(callback, detail_text(pack), reply_markup=detail_keyboard(template_id, True))


@packs_router.callback_query(F.data.startswith('pack:sort:'))
async def pack_sort(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    _, _, template_id, delta = callback.data.split(':')
    meta = get_pack_meta(template_id)
    order = int(meta.get('sort_order', 1000)) + int(delta)
    upsert_pack_meta(
        template_id,
        price_amount=int(meta.get('price_amount') or 0),
        price_currency=meta.get('price_currency', 'TOMAN'),
        is_visible=int(meta.get('is_visible', 1)),
        sort_order=order,
    )
    product_id = meta.get('product_id')
    if product_id:
        from modules.products import product_pack_keyboard
        await edit_or_answer(callback, '📦 بسته‌های این محصول', reply_markup=await product_pack_keyboard(int(product_id)))
        await callback.answer()
    else:
        await products_fallback(callback)


async def products_fallback(callback: CallbackQuery):
    from modules.products import product_keyboard
    await edit_or_answer(callback, '🛍 محصولات', reply_markup=product_keyboard())
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:delete:'))
async def pack_delete_confirm(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    await edit_or_answer(callback, 'آیا از حذف بسته مطمئن هستید؟', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ بله حذف شود', callback_data=f'pack:delete_yes:{template_id}')],
        [InlineKeyboardButton(text='❌ لغو', callback_data=f'pack:view:{template_id}')],
    ]))
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:delete_yes:'))
async def pack_delete(callback: CallbackQuery):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    meta = get_pack_meta(template_id)
    product_id = meta.get('product_id')
    await api_client.delete_user_template(template_id)
    db.execute('DELETE FROM product_test_templates WHERE template_id=?', (template_id,))
    db.execute('DELETE FROM package_settings WHERE template_id=?', (template_id,))
    db.add_log(callback.from_user.id, 'delete_pack', 'pack', template_id)
    if product_id:
        from modules.products import product_pack_keyboard
        await edit_or_answer(callback, 'بسته حذف شد.\n\n📦 بسته‌های این محصول', reply_markup=await product_pack_keyboard(int(product_id)))
        await callback.answer()
    else:
        await products_fallback(callback)
