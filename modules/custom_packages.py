from __future__ import annotations
import json
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import api_client, db
from permissions import can_manage_packs, deny_callback
from utils import edit_or_answer
from modules.packs import pack_title
from modules.customer import CustomerBuyState, username_rules_text, get_pack_meta, visible_pack_rows_for_product, customer_packs_keyboard

custom_packages_router = Router()

class CustomAdminState(StatesGroup):
    public_price=State(); volume=State(); months=State(); base_per_gb=State(); month_values=State(); users=State(); user_values=State()
class CustomBuyState(StatesGroup):
    volume=State()

def row(pid:int): return db.fetchone('SELECT * FROM products WHERE id=?',(pid,)) or {}
def loads(v, default):
    try: return json.loads(v) if v else default
    except Exception: return default

def admin_back_kb(pid:int):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'custom_admin:cancel:{pid}')]])

def customer_back_kb(pid:int):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'cust_custom_back:{pid}')]])

MONTH_MODE_LABELS = {
    'per_gb': 'قیمت هر گیگ برای هر ماه',
    'fixed': 'مبلغ ثابت ماهانه',
    'multiplier': 'ضریب قیمت هر ماه',
}

def month_mode_label(mode: str) -> str:
    return MONTH_MODE_LABELS.get(str(mode or 'per_gb'), 'قیمت هر گیگ برای هر ماه')

def month_values_button_text(mode: str) -> str:
    mode = str(mode or 'per_gb')
    if mode == 'fixed':
        return '💵 مبلغ ثابت ماه‌ها'
    if mode == 'multiplier':
        return '✖️ ضرایب ماه‌ها'
    return '💰 قیمت هر گیگ ماه‌ها'

def month_values_help(mode: str) -> str:
    mode = str(mode or 'per_gb')
    if mode == 'fixed':
        return (
            '💵 مدل فعلی: مبلغ ثابت ماهانه\n\n'
            'برای هر مدت، مبلغ ثابت هر ماه را مشخص کنید.\n'
            'فرمت: ماه=مبلغ ماهانه\n'
            'مثال: 1=50000,2=45000,3=40000\n\n'
            'مثال: اگر 2=45000 باشد، سرویس دوماهه 90,000 تومان هزینه ثابت ماه خواهد داشت.\n'
            'این مبلغ به حجم وابسته نیست.'
        )
    if mode == 'multiplier':
        return (
            '✖️ مدل فعلی: ضریب قیمت هر ماه\n\n'
            'قیمت پایه = حجم × قیمت پایه هر گیگ\n'
            'بعد ضریب مدت روی آن اعمال می‌شود.\n'
            'فرمت: ماه=ضریب\n'
            'مثال: 1=1,2=1.8,3=2.5\n\n'
            'مثال: 20GB با قیمت پایه هر گیگ 10,000 و ضریب 2ماهه 1.8 => 360,000 تومان.'
        )
    return (
        '💰 مدل فعلی: قیمت هر گیگ برای هر ماه\n\n'
        'برای هر مدت، قیمت هر گیگ را جدا تعیین کنید.\n'
        'فرمت: ماه=قیمت هر گیگ\n'
        'مثال: 1=12000,2=11000,3=10000\n\n'
        'مثال: 20GB دوماهه با 2=11000 => 220,000 تومان.'
    )

def format_month_values(p: dict) -> str:
    mode = str(p.get('custom_month_pricing_mode') or 'per_gb')
    vals = loads(p.get('custom_month_values_json'), {})
    months = loads(p.get('custom_months_json'), [1])
    parts=[]
    for m in months:
        v=vals.get(str(m))
        if v is None:
            parts.append(f'{m}ماه: تنظیم نشده')
        elif mode == 'multiplier':
            parts.append(f'{m}ماه: ×{float(v):g}')
        else:
            parts.append(f'{m}ماه: {int(float(v)):,} تومان')
    return ' | '.join(parts)

async def admin_saved(message: Message, state: FSMContext, pid: int, text: str='ذخیره شد ✅'):
    await state.clear()
    await message.answer(text + '\n\n' + summary(pid), reply_markup=admin_kb(pid))

def admin_kb(pid:int):
    p=row(pid); enabled='✅' if int(p.get('custom_enabled') or 0) else '⛔️'; users='✅' if int(p.get('custom_users_enabled') or 0) else '⛔️'
    month_mode=str(p.get('custom_month_pricing_mode') or 'per_gb')
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=f'{enabled} روشن/خاموش بسته دلخواه',callback_data=f'custom_admin:toggle:{pid}')],
      [InlineKeyboardButton(text='🧩 قالب پایه',callback_data=f'custom_admin:base:{pid}'), InlineKeyboardButton(text='📦 بازه حجم',callback_data=f'custom_admin:volume:{pid}')],
      [InlineKeyboardButton(text='🗓 ماه‌های مجاز',callback_data=f'custom_admin:months:{pid}'), InlineKeyboardButton(text='💰 قیمت پایه هر گیگ',callback_data=f'custom_admin:basegb:{pid}')],
      [InlineKeyboardButton(text=f'🧮 مدل ماه: {month_mode_label(month_mode)}',callback_data=f'custom_admin:monthmode:{pid}')],
      [InlineKeyboardButton(text=month_values_button_text(month_mode),callback_data=f'custom_admin:monthvalues:{pid}')],
      [InlineKeyboardButton(text=f'{users} تعداد کاربر',callback_data=f'custom_admin:userstoggle:{pid}'), InlineKeyboardButton(text='👥 گزینه‌های کاربر',callback_data=f'custom_admin:users:{pid}')],
      [InlineKeyboardButton(text='🧮 مدل قیمت کاربر',callback_data=f'custom_admin:usermode:{pid}'), InlineKeyboardButton(text='🔢 مقادیر قیمت کاربر',callback_data=f'custom_admin:uservalues:{pid}')],
      [InlineKeyboardButton(text='🔙 محصول',callback_data=f'product:view:{pid}')],
    ])

def summary(pid:int):
    p=row(pid); months=loads(p.get('custom_months_json'),[1]); users=loads(p.get('custom_user_counts_json'),[1])
    return (f"🎛 بسته دلخواه | {p.get('name','')}\n\n"
      f"وضعیت: {'فعال' if int(p.get('custom_enabled') or 0) else 'غیرفعال'}\n"
      f"قالب پایه: {p.get('custom_base_template_id') or 'تنظیم نشده'}\n"
      f"حجم: {p.get('custom_min_gb',1)} تا {p.get('custom_max_gb',100)} گیگ\n"
      f"ماه‌ها: {', '.join(map(str,months))}\n"
      f"قیمت عمومی هر گیگ محصول: {int(p.get('public_price_amount') or 0):,} تومان\n"
      f"قیمت پایه هر گیگ بسته دلخواه: {int(p.get('custom_base_per_gb') or 0):,} تومان (0 = استفاده از قیمت عمومی)\n"
      f"مدل قیمت ماه: {month_mode_label(p.get('custom_month_pricing_mode') or 'per_gb')}\n"
      f"مقادیر ماه: {format_month_values(p)}\n"
      f"تعداد کاربر: {'فعال' if int(p.get('custom_users_enabled') or 0) else 'غیرفعال'} ({', '.join(map(str,users))})\n"
      f"مدل کاربر: {p.get('custom_user_pricing_mode') or 'fixed'}")

def parse_map(text:str):
    out={}
    for part in text.replace('،',',').split(','):
        if not part.strip(): continue
        k,v=part.split('=',1); out[str(int(k.strip()))]=float(v.strip())
    return out

def calc_price(p:dict, gb:int, months:int, users:int)->int:
    vals=loads(p.get('custom_month_values_json'),{})
    mode=str(p.get('custom_month_pricing_mode') or 'per_gb'); base=float(p.get('custom_base_per_gb') or 0) or float(p.get('public_price_amount') or 0); mv=float(vals.get(str(months),1 if mode=='multiplier' else 0))
    if mode=='fixed': price=mv*months
    elif mode=='multiplier': price=gb*base*mv
    else: price=gb*mv
    if int(p.get('custom_users_enabled') or 0):
        uvals=loads(p.get('custom_user_values_json'),{}); uv=float(uvals.get(str(users),0)); um=str(p.get('custom_user_pricing_mode') or 'fixed')
        if um=='multiplier': price*= (uv if uv>0 else 1)
        elif um=='per_gb': price += gb*uv
        else: price += uv
    return max(0,int(round(price)))

@custom_packages_router.callback_query(F.data.startswith('custom_admin:view:'))
async def view(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); await edit_or_answer(c,summary(pid),reply_markup=admin_kb(pid)); await c.answer()
@custom_packages_router.callback_query(F.data.startswith('custom_admin:toggle:'))
async def toggle(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); p=row(pid); db.execute('UPDATE products SET custom_enabled=?,updated_at=? WHERE id=?',(0 if int(p.get('custom_enabled') or 0) else 1,db.now_iso(),pid)); await view(c)
@custom_packages_router.callback_query(F.data.startswith('custom_admin:public:'))
async def public_start(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(':')[-1]); await state.set_state(CustomAdminState.public_price); await state.update_data(pid=pid); await edit_or_answer(c,'قیمت عمومی هر ۱ گیگ این محصول را به تومان بفرستید.\nمثال: 8000\n\nبسته‌هایی که روی «پیروی از قیمت عمومی» باشند، قیمت نهایی‌شان از حجم × این مبلغ محاسبه می‌شود.\n0 یعنی غیرفعال:', reply_markup=admin_back_kb(pid)); await c.answer()
@custom_packages_router.message(CustomAdminState.public_price)
async def public_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        value=int((m.text or '0').replace(',','').strip())
        if value < 0: raise ValueError
    except Exception:
        await m.answer('قیمت هر گیگ را فقط به‌صورت عدد مثبت بفرستید. 0 یعنی غیرفعال.', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET public_price_amount=?,updated_at=? WHERE id=?',(value,db.now_iso(),pid)); await admin_saved(m,state,pid,'قیمت عمومی هر گیگ ذخیره شد ✅')
@custom_packages_router.callback_query(F.data.startswith('custom_admin:base:'))
async def base(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); metas=db.fetchall('SELECT template_id FROM package_settings WHERE product_id=? ORDER BY sort_order',(pid,)); packs={str(x.get('id')):x for x in await api_client.get_user_templates()}; rows=[]
    for x in metas:
        tid=str(x['template_id']); rows.append([InlineKeyboardButton(text=pack_title(packs.get(tid,{'id':tid})),callback_data=f'custom_admin:basepick:{pid}:{tid}')])
    rows.append([InlineKeyboardButton(text='🔙 بازگشت',callback_data=f'custom_admin:view:{pid}')]); await edit_or_answer(c,'قالب پایه را انتخاب کنید. گروه‌ها/پیشوند و تنظیمات فنی از این بسته گرفته می‌شود:',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()
@custom_packages_router.callback_query(F.data.startswith('custom_admin:basepick:'))
async def basepick(c:CallbackQuery):
    _,_,pid,tid=c.data.split(':',3); db.execute('UPDATE products SET custom_base_template_id=?,updated_at=? WHERE id=?',(tid,db.now_iso(),int(pid))); await edit_or_answer(c,summary(int(pid)),reply_markup=admin_kb(int(pid))); await c.answer('ذخیره شد')

def simple_start(state_name):
    async def fn(c:CallbackQuery,state:FSMContext):
        pid=int(c.data.split(':')[-1]); await state.set_state(state_name); await state.update_data(pid=pid)
        prompts={CustomAdminState.volume:'حداقل و حداکثر گیگ را بفرستید. مثال: 5 100',CustomAdminState.months:'ماه‌های مجاز را با کاما بفرستید. مثال: 1,2,3,6,12',CustomAdminState.base_per_gb:'قیمت پایه هر گیگ بسته دلخواه را به تومان بفرستید. 0 یعنی از قیمت عمومی هر گیگ همین محصول استفاده شود:',CustomAdminState.users:'تعداد کاربران مجاز را با کاما بفرستید. مثال: 1,2,3,5',CustomAdminState.user_values:'مقادیر را به شکل تعداد=مقدار بفرستید. مثال: 1=0,2=20000,3=35000'}
        prompt = month_values_help(row(pid).get('custom_month_pricing_mode')) if state_name == CustomAdminState.month_values else prompts[state_name]
        await edit_or_answer(c,prompt, reply_markup=admin_back_kb(pid)); await c.answer()
    return fn
custom_packages_router.callback_query(F.data.startswith('custom_admin:volume:'))(simple_start(CustomAdminState.volume))
custom_packages_router.callback_query(F.data.startswith('custom_admin:months:'))(simple_start(CustomAdminState.months))
custom_packages_router.callback_query(F.data.startswith('custom_admin:basegb:'))(simple_start(CustomAdminState.base_per_gb))
custom_packages_router.callback_query(F.data.startswith('custom_admin:monthvalues:'))(simple_start(CustomAdminState.month_values))
custom_packages_router.callback_query(F.data.startswith('custom_admin:users:'))(simple_start(CustomAdminState.users))
custom_packages_router.callback_query(F.data.startswith('custom_admin:uservalues:'))(simple_start(CustomAdminState.user_values))

@custom_packages_router.message(CustomAdminState.volume)
async def volume_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        vals=(m.text or '').replace(',',' ').split(); a,b=map(int,vals[:2])
        if len(vals) != 2 or a <= 0 or b < a: raise ValueError
    except Exception:
        await m.answer('فرمت بازه درست نیست. مثال: 5 100', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_min_gb=?,custom_max_gb=?,updated_at=? WHERE id=?',(a,b,db.now_iso(),pid)); await admin_saved(m,state,pid)
@custom_packages_router.message(CustomAdminState.months)
async def months_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        arr=sorted(set(int(x.strip()) for x in (m.text or '').replace('،',',').split(',') if x.strip()))
        if not arr or any(x <= 0 for x in arr): raise ValueError
    except Exception:
        await m.answer('ماه‌ها را با کاما و به‌صورت عدد مثبت بفرستید. مثال: 1,2,3,6,12', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_months_json=?,updated_at=? WHERE id=?',(json.dumps(arr),db.now_iso(),pid)); await admin_saved(m,state,pid)
@custom_packages_router.message(CustomAdminState.base_per_gb)
async def basegb_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        value=int((m.text or '0').replace(',','').strip())
        if value < 0: raise ValueError
    except Exception:
        await m.answer('قیمت هر گیگ را فقط به‌صورت عدد بفرستید.', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_base_per_gb=?,updated_at=? WHERE id=?',(value,db.now_iso(),pid)); await admin_saved(m,state,pid)
@custom_packages_router.message(CustomAdminState.month_values)
async def monthvals_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid']); p=row(pid); mode=str(p.get('custom_month_pricing_mode') or 'per_gb')
    try:
        values=parse_map(m.text or '')
        allowed={str(int(x)) for x in loads(p.get('custom_months_json'),[1])}
        if not values or any(k not in allowed for k in values): raise ValueError
        if any(float(v) < 0 for v in values.values()): raise ValueError
        if mode == 'multiplier' and any(float(v) <= 0 for v in values.values()): raise ValueError
    except Exception:
        await m.answer('ورودی با مدل فعلی سازگار نیست.\n\n' + month_values_help(mode), reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_month_values_json=?,updated_at=? WHERE id=?',(json.dumps(values),db.now_iso(),pid)); await admin_saved(m,state,pid,'مقادیر قیمت ماه ذخیره شد ✅')
@custom_packages_router.message(CustomAdminState.users)
async def users_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        arr=sorted(set(int(x.strip()) for x in (m.text or '').replace('،',',').split(',') if x.strip()))
        if not arr or any(x <= 0 for x in arr): raise ValueError
    except Exception:
        await m.answer('تعداد کاربران را با کاما و به‌صورت عدد مثبت بفرستید. مثال: 1,2,3,5', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_user_counts_json=?,updated_at=? WHERE id=?',(json.dumps(arr),db.now_iso(),pid)); await admin_saved(m,state,pid)
@custom_packages_router.message(CustomAdminState.user_values)
async def uservals_done(m:Message,state:FSMContext):
    d=await state.get_data(); pid=int(d['pid'])
    try:
        values=parse_map(m.text or '')
        if not values: raise ValueError
    except Exception:
        await m.answer('فرمت مقادیر درست نیست. مثال: 1=0,2=20000,3=35000', reply_markup=admin_back_kb(pid)); return
    db.execute('UPDATE products SET custom_user_values_json=?,updated_at=? WHERE id=?',(json.dumps(values),db.now_iso(),pid)); await admin_saved(m,state,pid)

@custom_packages_router.callback_query(F.data.startswith('custom_admin:cancel:'))
async def admin_cancel(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(':')[-1]); await state.clear(); await edit_or_answer(c,summary(pid),reply_markup=admin_kb(pid)); await c.answer('برگشتیم')

@custom_packages_router.callback_query(F.data.startswith('custom_admin:monthmode:'))
async def monthmode(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); p=row(pid); cur=str(p.get('custom_month_pricing_mode') or 'per_gb')
    rows=[]
    for mode in ('per_gb','fixed','multiplier'):
        mark='✅ ' if mode == cur else ''
        rows.append([InlineKeyboardButton(text=mark + month_mode_label(mode), callback_data=f'custom_admin:monthmodepick:{pid}:{mode}')])
    rows.append([InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'custom_admin:view:{pid}')])
    await edit_or_answer(c,
        '🧮 مدل محاسبه قیمت ماه را انتخاب کنید:\n\n'
        '• قیمت هر گیگ برای هر ماه: برای 1، 2، 3 ماه قیمت هر گیگ جداست.\n'
        '• مبلغ ثابت ماهانه: حجم روی قیمت ماه اثر ندارد و مبلغ ماهانه × تعداد ماه می‌شود.\n'
        '• ضریب قیمت هر ماه: حجم × قیمت پایه هر گیگ × ضریب مدت.\n\n'
        f'مدل فعلی: {month_mode_label(cur)}',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@custom_packages_router.callback_query(F.data.startswith('custom_admin:monthmodepick:'))
async def monthmodepick(c:CallbackQuery):
    _,_,pid_s,new=c.data.split(':',3); pid=int(pid_s)
    if new not in MONTH_MODE_LABELS:
        await c.answer('مدل نامعتبر است.', show_alert=True); return
    p=row(pid); old=str(p.get('custom_month_pricing_mode') or 'per_gb')
    if old != new:
        db.execute('UPDATE products SET custom_month_pricing_mode=?,custom_month_values_json=?,updated_at=? WHERE id=?',(new,'{}',db.now_iso(),pid))
        note='مقادیر مدل قبلی پاک شد تا نوع قیمت‌ها با هم قاطی نشوند.'
    else:
        note='همین مدل از قبل فعال بود.'
    await edit_or_answer(c,summary(pid) + f'\n\n✅ مدل ماه: {month_mode_label(new)}\n{note}\n\nحالا از دکمه «{month_values_button_text(new).replace("💵 ","").replace("✖️ ","").replace("💰 ","")}» مقادیر را تنظیم کنید.',reply_markup=admin_kb(pid)); await c.answer('مدل قیمت ماه تنظیم شد')
@custom_packages_router.callback_query(F.data.startswith('custom_admin:userstoggle:'))
async def usertoggle(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); p=row(pid); db.execute('UPDATE products SET custom_users_enabled=?,updated_at=? WHERE id=?',(0 if int(p.get('custom_users_enabled') or 0) else 1,db.now_iso(),pid)); await view(c)
@custom_packages_router.callback_query(F.data.startswith('custom_admin:usermode:'))
async def usermode(c:CallbackQuery):
    pid=int(c.data.split(':')[-1]); p=row(pid); modes=['fixed','per_gb','multiplier']; cur=str(p.get('custom_user_pricing_mode') or 'fixed'); new=modes[(modes.index(cur)+1)%len(modes)] if cur in modes else modes[0]; db.execute('UPDATE products SET custom_user_pricing_mode=?,updated_at=? WHERE id=?',(new,db.now_iso(),pid)); await view(c)

# customer flow
@custom_packages_router.callback_query(F.data.startswith('cust_custom:'))
async def cust_start(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(':')[-1]); p=row(pid)
    if not p or not int(p.get('custom_enabled') or 0) or not p.get('custom_base_template_id'): await c.answer('بسته دلخواه فعلا آماده نیست.',show_alert=True); return
    await state.set_state(CustomBuyState.volume); await state.update_data(custom_product_id=pid); await edit_or_answer(c,f"حجم دلخواه را وارد کنید ({p.get('custom_min_gb',1)} تا {p.get('custom_max_gb',100)} گیگ):", reply_markup=customer_back_kb(pid)); await c.answer()
@custom_packages_router.message(CustomBuyState.volume)
async def cust_volume(m:Message,state:FSMContext):
    d=await state.get_data(); p=row(int(d['custom_product_id']))
    try: gb=int((m.text or '').strip())
    except: await m.answer('حجم را فقط عدد بفرستید.', reply_markup=customer_back_kb(int(d['custom_product_id']))); return
    if gb<int(p.get('custom_min_gb') or 1) or gb>int(p.get('custom_max_gb') or 100): await m.answer('حجم خارج از بازه مجاز است.', reply_markup=customer_back_kb(int(d['custom_product_id']))); return
    await state.update_data(custom_gb=gb); months=loads(p.get('custom_months_json'),[1]); rows=[[InlineKeyboardButton(text=f'{x} ماه',callback_data=f'cust_custom_month:{x}')] for x in months]; rows.append([InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'cust_custom_back:{int(d["custom_product_id"])}')]); kb=InlineKeyboardMarkup(inline_keyboard=rows); await m.answer('مدت سرویس را انتخاب کنید:',reply_markup=kb)
@custom_packages_router.callback_query(F.data.startswith('cust_custom_month:'))
async def cust_month(c:CallbackQuery,state:FSMContext):
    months=int(c.data.split(':')[-1]); d=await state.get_data(); p=row(int(d['custom_product_id'])); await state.update_data(custom_months=months)
    if int(p.get('custom_users_enabled') or 0):
        counts=loads(p.get('custom_user_counts_json'),[1]); rows=[[InlineKeyboardButton(text=f'{x} کاربر',callback_data=f'cust_custom_user:{x}')] for x in counts]; rows.append([InlineKeyboardButton(text='⬅️ بازگشت', callback_data=f'cust_custom_months_back:{int(d["custom_product_id"])}')]); kb=InlineKeyboardMarkup(inline_keyboard=rows); await edit_or_answer(c,'تعداد کاربر همزمان را انتخاب کنید:',reply_markup=kb); await c.answer(); return
    await finish_custom(c,state,1)
@custom_packages_router.callback_query(F.data.startswith('cust_custom_user:'))
async def cust_user(c:CallbackQuery,state:FSMContext): await finish_custom(c,state,int(c.data.split(':')[-1]))

@custom_packages_router.callback_query(F.data.startswith('cust_custom_back:'))
async def cust_custom_back(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(':')[-1]); await state.clear()
    try:
        p=row(pid); packs=await api_client.get_user_templates(); items=visible_pack_rows_for_product(packs,pid)
        await edit_or_answer(c,f"🛍 {p.get('name','محصول')}\n\nبسته موردنظر را انتخاب کنید:",reply_markup=customer_packs_keyboard(items,pid))
    except Exception:
        await edit_or_answer(c,'خرید بسته لغو شد. دوباره از منوی خرید انتخاب کنید.')
    await c.answer()

@custom_packages_router.callback_query(F.data.startswith('cust_custom_months_back:'))
async def cust_custom_months_back(c:CallbackQuery,state:FSMContext):
    d=await state.get_data(); pid=int(d.get('custom_product_id') or c.data.split(':')[-1]); p=row(pid); months=loads(p.get('custom_months_json'),[1]); rows=[[InlineKeyboardButton(text=f'{x} ماه',callback_data=f'cust_custom_month:{x}')] for x in months]; rows.append([InlineKeyboardButton(text='⬅️ بازگشت',callback_data=f'cust_custom_back:{pid}')]); await edit_or_answer(c,'مدت سرویس را انتخاب کنید:',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

async def finish_custom(c:CallbackQuery,state:FSMContext,users:int):
    d=await state.get_data(); pid=int(d['custom_product_id']); gb=int(d['custom_gb']); months=int(d['custom_months']); p=row(pid); price=calc_price(p,gb,months,users)
    if price<=0: await c.answer('قیمت این ترکیب تنظیم نشده است.',show_alert=True); return
    base=str(p.get('custom_base_template_id')); pack=await api_client.get_user_template(base); meta=get_pack_meta(base); custom_id=f'custom:{base}:{gb}:{months}:{users}:{pid}'; name=f'بسته دلخواه {gb}GB / {months} ماه' + (f' / {users} کاربر' if int(p.get('custom_users_enabled') or 0) else '')
    await state.set_state(CustomerBuyState.waiting_username); await state.update_data(product_id=str(pid),product_name=str(p.get('name') or ''),package_id=custom_id,package_name=name,price_toman=price,discount_amount=0,coupon_id=None,coupon_code=None,renew_service_id=None,order_type='new',custom_volume_gb=gb,custom_months=months,custom_users=users)
    fake=dict(pack); fake['id']=custom_id; fake['name']=name; fake['data_limit']=gb*1024**3; fake['expire_duration']=months*30*86400; fake['hwid_limit']=users
    prompt,_,_=username_rules_text(fake,dict(meta,price_amount=price),c.from_user); await edit_or_answer(c,prompt,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ انصراف',callback_data='cust_cancel')]])); await c.answer()
