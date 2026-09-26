from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

import api_client
import db
from utils import edit_or_answer
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
) -> None:
    current = get_pack_meta(str(template_id))
    if price_amount is None:
        price_amount = int(current.get('price_amount') or 0)
    if is_visible is None:
        is_visible = int(current.get('is_visible', 1))
    if sort_order is None:
        sort_order = int(current.get('sort_order', 1000))
    db.execute(
        '''INSERT INTO package_settings
           (template_id, price_amount, price_currency, is_visible, sort_order, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(template_id) DO UPDATE SET
             price_amount=excluded.price_amount,
             price_currency=excluded.price_currency,
             is_visible=excluded.is_visible,
             sort_order=excluded.sort_order,
             updated_at=excluded.updated_at''',
        (str(template_id), int(price_amount), price_currency, int(is_visible), int(sort_order), db.now_iso()),
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
        price = format_price(meta.get('price_amount', 0), meta.get('price_currency', 'TOMAN'))
        rows.append([InlineKeyboardButton(text=f'{eye} {pack_title(pack)} | {price}', callback_data=f'pack:view:{pid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def detail_text(pack: dict) -> str:
    template_id = str(pack.get('id'))
    meta = get_pack_meta(template_id)
    price = format_price(meta.get('price_amount', 0), meta.get('price_currency', 'TOMAN'))
    visible = 'بله' if meta.get('is_visible', 1) else 'خیر'
    return (
        '📦 جزئیات بسته\n\n'
        f'ID: {pack.get("id")}\n'
        f'نام: {pack.get("name")}\n'
        f'قیمت فروش: {price}\n'
        f'نمایش در ربات: {visible}\n'
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
    rows = [[InlineKeyboardButton(text='🔙 لیست بسته‌ها', callback_data='pack:list')]]
    if can_manage:
        rows = [
            [InlineKeyboardButton(text='💰 قیمت', callback_data=f'pack:price:{template_id}')],
            [InlineKeyboardButton(text='✏️ نام', callback_data=f'pack:edit:{template_id}:name'), InlineKeyboardButton(text='📦 حجم', callback_data=f'pack:edit:{template_id}:data_limit')],
            [InlineKeyboardButton(text='⏳ مدت', callback_data=f'pack:edit:{template_id}:expire_duration'), InlineKeyboardButton(text='👥 گروه‌ها', callback_data=f'pack:edit:{template_id}:group_ids')],
            [InlineKeyboardButton(text='⬅️ پیش از نام', callback_data=f'pack:edit:{template_id}:username_prefix'), InlineKeyboardButton(text='➡️ پس از نام', callback_data=f'pack:edit:{template_id}:username_suffix')],
            [InlineKeyboardButton(text='👁 نمایش/عدم نمایش', callback_data=f'pack:visible:{template_id}')],
            [InlineKeyboardButton(text='⬆️ بالا', callback_data=f'pack:sort:{template_id}:-1'), InlineKeyboardButton(text='⬇️ پایین', callback_data=f'pack:sort:{template_id}:1')],
            [InlineKeyboardButton(text='🗑 حذف', callback_data=f'pack:delete:{template_id}')],
            [InlineKeyboardButton(text='🔙 لیست بسته‌ها', callback_data='pack:list')],
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def list_packs_message(message: Message):
    packs = await api_client.get_user_templates()
    await message.answer('📦 بسته‌ها', reply_markup=back_keyboard())
    await message.answer('لیست بسته‌ها:', reply_markup=list_keyboard(packs, can_manage_packs(message.from_user.id if message.from_user else None)))


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
    packs = await api_client.get_user_templates()
    await edit_or_answer(callback, '📦 بسته‌ها', reply_markup=list_keyboard(packs, can_manage_packs(callback.from_user.id)))
    await callback.answer()


@packs_router.callback_query(F.data.startswith('pack:view:'))
async def pack_view(callback: CallbackQuery):
    if not can_view_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    template_id = callback.data.split(':')[-1]
    pack = await api_client.get_user_template(template_id)
    await edit_or_answer(callback, detail_text(pack), reply_markup=detail_keyboard(template_id, can_manage_packs(callback.from_user.id)))
    await callback.answer()


@packs_router.callback_query(F.data == 'pack:create')
async def pack_create_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_packs(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(PackStates.create_value)
    await state.update_data(step=0, payload={}, price_amount=None)
    await callback.message.answer('نام بسته را وارد کنید:')
    await callback.answer()


@packs_router.message(PackStates.create_value)
async def pack_create_value(message: Message, state: FSMContext):
    if not can_manage_packs(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    step = data.get('step', 0)
    payload = data.get('payload', {})
    field = STEPS[step]
    try:
        if field == 'name':
            value = (message.text or '').strip()
            if not value:
                raise ValueError('نام بسته نمی‌تواند خالی باشد.')
            payload[field] = value
        elif field == 'price_amount':
            await state.update_data(price_amount=parse_price(message.text or ''), step=step + 1, payload=payload)
        elif field == 'data_limit':
            payload[field] = parse_bytes(message.text or '')
        elif field == 'expire_duration':
            payload[field] = parse_duration(message.text or '')
        if field != 'price_amount':
            await state.update_data(payload=payload, step=step + 1)
    except Exception as exc:
        await message.answer(f'خطا: {exc}')
        return
    next_step = step + 1
    if next_step == 1:
        await message.answer('قیمت بسته را به تومان وارد کنید. مثال: 90000')
    elif next_step == 2:
        await message.answer('حجم بسته را وارد کنید. مثال: 50GB')
    elif next_step == 3:
        await message.answer('مدت اعتبار را وارد کنید. مثال: 30d یا 30 روز')
    elif next_step == 4:
        groups = await api_client.get_groups()
        await state.set_state(PackStates.create_groups)
        await message.answer('گروه‌ها را انتخاب کنید:', reply_markup=group_keyboard(groups, [], 'create'))


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
    upsert_pack_meta(template_id, price_amount=int(price_amount))
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
        await message.answer(f'خطا: {exc}')


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
        await message.answer(f'خطا: {exc}')


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
        await message.answer(f'خطا: {exc}')


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
    await packs_list(callback)


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
    await api_client.delete_user_template(template_id)
    db.execute('DELETE FROM package_settings WHERE template_id=?', (template_id,))
    db.add_log(callback.from_user.id, 'delete_pack', 'pack', template_id)
    await packs_list(callback)
