from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_CAMPAIGNS, back_keyboard
from permissions import get_role, is_super_admin
from utils import person_display, edit_or_answer, answer_callback

campaigns_router = Router()


class CampaignState(StatesGroup):
    title = State()
    percent = State()
    duration = State()


def _permission_ids() -> set[int]:
    raw = db.get_setting('campaign_admin_ids', [])
    if not isinstance(raw, list):
        return set()
    out = set()
    for x in raw:
        try:
            out.add(int(x))
        except Exception:
            pass
    return out


def can_manage_campaigns(user_id: int | None) -> bool:
    if not user_id:
        return False
    if is_super_admin(user_id):
        return True
    return get_role(user_id) == 'admin' and int(user_id) in _permission_ids()


def _rule_label(rule: str) -> str:
    return 'شروع پرداخت داخل زمان کمپین کافی است' if rule == 'payment_time' else 'تایید نهایی هم باید داخل زمان کمپین باشد'


def _status_text() -> str:
    active = db.get_active_wallet_campaign()
    lines = ['🎯 کمپین شارژ کیف پول', '']
    if active:
        lines += [
            f'✅ کمپین فعال: #{active["id"]} - {active.get("title") or "کمپین شارژ"}',
            f'🎁 بونوس: {float(active.get("bonus_percent") or 0):g}٪',
            f'⏱ مدت: {int(active.get("duration_hours") or 0)} ساعت' if int(active.get('duration_hours') or 0) > 0 else '⏱ مدت: نامحدود تا توقف دستی',
            f'📌 قانون زمان: {_rule_label(str(active.get("timing_rule") or "payment_time"))}',
            f'🕐 شروع: {active.get("started_at") or "-"}',
            f'🕛 پایان: {active.get("ends_at") or "توقف دستی"}',
            f'👤 سازنده: {person_display(active.get("created_by"))}',
        ]
    else:
        lines.append('❌ در حال حاضر کمپین فعالی وجود ندارد.')
    return '\n'.join(lines)


def _home_kb(user_id: int) -> InlineKeyboardMarkup:
    active = db.get_active_wallet_campaign()
    rows = [[InlineKeyboardButton(text='➕ ساخت و شروع کمپین جدید', callback_data='campaign:create')]]
    if active:
        rows.append([InlineKeyboardButton(text='⏹ توقف کمپین فعال', callback_data=f'campaign:stop:{active["id"]}')])
    rows.append([InlineKeyboardButton(text='📚 تاریخچه کمپین ها', callback_data='campaign:history')])
    if is_super_admin(user_id):
        rows.append([InlineKeyboardButton(text='👥 دسترسی ادمین ها', callback_data='campaign:permissions')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@campaigns_router.message(EditableButtonFilter(BTN_CAMPAIGNS))
async def campaign_home_message(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not can_manage_campaigns(uid):
        await message.answer('شما دسترسی مدیریت کمپین ها را ندارید.')
        return
    await message.answer('🎯 مدیریت کمپین ها', reply_markup=back_keyboard())
    await message.answer(_status_text(), reply_markup=_home_kb(int(uid)))


@campaigns_router.callback_query(F.data == 'campaign:home')
async def campaign_home_callback(callback: CallbackQuery):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.', show_alert=True); return
    await edit_or_answer(callback, _status_text(), reply_markup=_home_kb(callback.from_user.id))
    await answer_callback(callback)


@campaigns_router.callback_query(F.data == 'campaign:create')
async def campaign_create_start(callback: CallbackQuery, state: FSMContext):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.', show_alert=True); return
    await state.set_state(CampaignState.title)
    await edit_or_answer(callback, '➕ ساخت کمپین جدید\n\nیک نام برای کمپین بفرستید. مثال: جشنواره آخر هفته\nبرای نام پیش فرض، فقط - بفرستید.')
    await answer_callback(callback)


@campaigns_router.message(CampaignState.title)
async def campaign_title(message: Message, state: FSMContext):
    if not can_manage_campaigns(message.from_user.id if message.from_user else None):
        await state.clear(); return
    title = (message.text or '').strip()
    if not title:
        await message.answer('نام کمپین را بفرستید.'); return
    if title == '-':
        title = 'کمپین شارژ کیف پول'
    await state.update_data(campaign_title=title[:120])
    await state.set_state(CampaignState.percent)
    await message.answer('🎁 چند درصد بیشتر از مبلغ شارژ به کیف پول اضافه شود؟\nمثال: 10')


@campaigns_router.message(CampaignState.percent)
async def campaign_percent(message: Message, state: FSMContext):
    if not can_manage_campaigns(message.from_user.id if message.from_user else None):
        await state.clear(); return
    raw = (message.text or '').strip().replace(',', '.')
    try:
        value = float(raw)
    except Exception:
        await message.answer('درصد معتبر بفرستید. مثال: 10 یا 12.5'); return
    if value <= 0 or value > 1000:
        await message.answer('درصد باید بیشتر از 0 و حداکثر 1000 باشد.'); return
    await state.update_data(campaign_percent=value)
    await state.set_state(CampaignState.duration)
    await message.answer('⏱ مدت کمپین را به ساعت بفرستید.\nمثال: 24\nاگر 0 بفرستید، کمپین تا وقتی دستی متوقفش کنید فعال می ماند.')


@campaigns_router.message(CampaignState.duration)
async def campaign_duration(message: Message, state: FSMContext):
    if not can_manage_campaigns(message.from_user.id if message.from_user else None):
        await state.clear(); return
    raw=(message.text or '').strip()
    if not raw.isdigit():
        await message.answer('مدت را عددی و به ساعت بفرستید. 0 یعنی بدون پایان خودکار.'); return
    hours=int(raw)
    if hours < 0 or hours > 8760:
        await message.answer('مدت باید بین 0 تا 8760 ساعت باشد.'); return
    await state.update_data(campaign_duration=hours)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 شروع پرداخت داخل زمان کافی است', callback_data='campaign:rule:payment_time')],
        [InlineKeyboardButton(text='✅ تایید نهایی هم باید داخل زمان باشد', callback_data='campaign:rule:confirm_time')],
        [InlineKeyboardButton(text='❌ لغو', callback_data='campaign:cancel')],
    ])
    await message.answer('📌 قانون زمانی کمپین را انتخاب کنید:\n\nحالت اول: اگر کاربر در زمان کمپین درخواست پرداخت را شروع کند، حتی اگر تایید بعد از پایان انجام شود بونوس می گیرد.\n\nحالت دوم: پرداخت/فیش باید قبل از پایان کمپین تایید نهایی شود.', reply_markup=kb)


@campaigns_router.callback_query(F.data.startswith('campaign:rule:'))
async def campaign_rule(callback: CallbackQuery, state: FSMContext):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.',show_alert=True); return
    rule=callback.data.rsplit(':',1)[1]
    if rule not in {'payment_time','confirm_time'}:
        await callback.answer('قانون نامعتبر است.',show_alert=True); return
    data=await state.get_data()
    title=str(data.get('campaign_title') or 'کمپین شارژ کیف پول')
    percent=float(data.get('campaign_percent') or 0)
    hours=int(data.get('campaign_duration') or 0)
    if percent <= 0:
        await callback.answer('اطلاعات کمپین ناقص است.',show_alert=True); return
    await state.update_data(campaign_rule=rule)
    duration_text=f'{hours} ساعت' if hours else 'تا توقف دستی'
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ شروع کمپین', callback_data='campaign:confirm')],
        [InlineKeyboardButton(text='❌ لغو', callback_data='campaign:cancel')],
    ])
    await edit_or_answer(callback, f'🎯 تایید کمپین\n\nنام: {title}\nبونوس: {percent:g}٪\nمدت: {duration_text}\nقانون: {_rule_label(rule)}\n\nبا شروع کمپین، کمپین فعال قبلی در صورت وجود متوقف می شود.', reply_markup=kb)
    await answer_callback(callback)


@campaigns_router.callback_query(F.data == 'campaign:confirm')
async def campaign_confirm(callback: CallbackQuery, state: FSMContext):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.',show_alert=True); return
    data=await state.get_data()
    title=str(data.get('campaign_title') or 'کمپین شارژ کیف پول')
    percent=float(data.get('campaign_percent') or 0)
    hours=int(data.get('campaign_duration') or 0)
    rule=str(data.get('campaign_rule') or 'payment_time')
    if percent <= 0 or rule not in {'payment_time','confirm_time'}:
        await callback.answer('اطلاعات کمپین ناقص است.',show_alert=True); return
    cid=db.create_wallet_campaign(title,percent,hours,rule,callback.from_user.id)
    db.add_log(callback.from_user.id,'wallet_campaign_started','campaign',str(cid),{'percent':percent,'hours':hours,'timing_rule':rule})
    await state.clear()
    await callback.answer('کمپین فعال شد ✅',show_alert=True)
    await campaign_home_callback(callback)


@campaigns_router.callback_query(F.data == 'campaign:cancel')
async def campaign_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if can_manage_campaigns(callback.from_user.id):
        await callback.answer('لغو شد.')
        await campaign_home_callback(callback)


@campaigns_router.callback_query(F.data.startswith('campaign:stop:'))
async def campaign_stop(callback: CallbackQuery):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.',show_alert=True); return
    cid=int(callback.data.rsplit(':',1)[1])
    if db.stop_wallet_campaign(cid, callback.from_user.id):
        db.add_log(callback.from_user.id,'wallet_campaign_stopped','campaign',str(cid))
        await callback.answer('کمپین متوقف شد ⏹',show_alert=True)
    else:
        await callback.answer('کمپین فعال پیدا نشد.',show_alert=True)
    await campaign_home_callback(callback)


@campaigns_router.callback_query(F.data == 'campaign:history')
async def campaign_history(callback: CallbackQuery):
    if not can_manage_campaigns(callback.from_user.id):
        await callback.answer('دسترسی ندارید.',show_alert=True); return
    rows=db.fetchall('SELECT * FROM wallet_campaigns ORDER BY id DESC LIMIT 20')
    if not rows:
        text='📚 هنوز کمپینی ثبت نشده است.'
    else:
        lines=['📚 آخرین کمپین ها','']
        for r in rows:
            icon='✅' if r.get('status')=='active' else ('⏹' if r.get('status')=='stopped' else '⌛')
            lines.append(f'{icon} #{r["id"]} | {r.get("title") or "کمپین"} | {float(r.get("bonus_percent") or 0):g}٪ | {r.get("status")}')
        text='\n'.join(lines)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 برگشت',callback_data='campaign:home')]])
    await edit_or_answer(callback,text,reply_markup=kb); await answer_callback(callback)


@campaigns_router.callback_query(F.data == 'campaign:permissions')
async def campaign_permissions(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer('فقط سوپرادمین.',show_alert=True); return
    selected=_permission_ids()
    rows=db.fetchall("SELECT telegram_id, role FROM bot_admins WHERE is_active=1 AND role IN ('admin','super_admin') ORDER BY role, telegram_id")
    kb=[]
    for row in rows:
        uid=int(row['telegram_id']); locked=row.get('role')=='super_admin'
        mark='✅' if locked or uid in selected else '◻️'
        kb.append([InlineKeyboardButton(text=f'{mark} {person_display(uid)}' + (' | سوپرادمین' if locked else ''), callback_data=f'campaign:perm_toggle:{uid}')])
    kb.append([InlineKeyboardButton(text='🔙 برگشت',callback_data='campaign:home')])
    await edit_or_answer(callback,'👥 دسترسی مدیریت کمپین\n\nادمین های انتخاب شده می توانند کمپین بسازند، شروع کنند و متوقف کنند. سوپرادمین همیشه دسترسی دارد.',reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await answer_callback(callback)


@campaigns_router.callback_query(F.data.startswith('campaign:perm_toggle:'))
async def campaign_permission_toggle(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer('فقط سوپرادمین.',show_alert=True); return
    uid=int(callback.data.rsplit(':',1)[1])
    row=db.fetchone('SELECT role FROM bot_admins WHERE telegram_id=? AND is_active=1',(uid,))
    if not row:
        await callback.answer('ادمین پیدا نشد.',show_alert=True); return
    if row.get('role')=='super_admin':
        await callback.answer('سوپرادمین همیشه دسترسی دارد.',show_alert=True); return
    selected=_permission_ids()
    if uid in selected: selected.remove(uid)
    else: selected.add(uid)
    db.set_setting('campaign_admin_ids',sorted(selected))
    db.add_log(callback.from_user.id,'campaign_permission_changed','admin',str(uid),{'enabled':uid in selected})
    await callback.answer('دسترسی بروزرسانی شد.')
    await campaign_permissions(callback)
