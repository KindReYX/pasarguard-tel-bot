from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import back_keyboard, BTN_TEXTS
from permissions import deny_callback, deny_message, is_admin
from utils import get_content_stickers, set_content_stickers, message_text_with_custom_emoji_tokens

texts_router = Router()

class TextState(StatesGroup):
    wait_text = State()
    wait_extras = State()

TEXT_ITEMS = {
    'welcome_text': ('متن شروع کاربر', 'سلام {name}! به ربات خوش آمدید.', '{name} {first_name} {username} {user_id} {support}'),
    'maintenance_text': ('متن حالت تعمیر', 'ربات در حال بروزرسانی است.', '{name} {first_name} {username} {user_id} {support}'),
    'rules_text': ('متن قوانین', 'قوانین هنوز تنظیم نشده است.', '{name} {user_id} {support}'),
    'support_text': ('متن پشتیبانی', 'برای ارتباط با پشتیبانی پیام خود را ارسال کنید.', '{name} {user_id} {support}'),
    'after_purchase_text': ('متن بعد از خرید', 'سرویس شما آماده شد.', '{name} {user_id} {order_id} {package_name} {service_name} {price} {support}'),
    'customer_menu_title': ('عنوان منوی کاربر', 'منوی کاربری:', '{name} {user_id}'),
    'buy_button_text': ('متن دکمه خرید اشتراک', '🛒 خرید اشتراک', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'test_service_button_text': ('متن دکمه سرویس تست', '🧪 سرویس تست', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'services_button_text': ('متن دکمه سرویس‌های من', '👤 سرویس های من', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'wallet_button_text': ('متن دکمه کیف پول', '💳 کیف پول من', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'referral_button_text': ('متن دکمه دعوت دوستان', '👥 دعوت دوستان', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'tutorials_button_text': ('متن دکمه آموزش', '🎓 آموزش', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'support_button_text': ('متن دکمه پشتیبانی', '🎫 پشتیبانی', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'rules_button_text': ('متن دکمه قوانین', '📜 قوانین', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'test_service_text': ('متن صفحه سرویس تست', 'سرویس تست', '{name} {user_id} {support}'),
    'package_username_prompt_text': ('متن بعد از انتخاب بسته / دریافت نام سرویس', '📦 بسته انتخابی: {package_name}\n\nلطفا نام کاربری سرویس را ارسال کنید.\n\n⚠️ نام کاربری باید بدون کاراکترهای اضافه مانند @ ، فاصله ، خط تیره باشد.\n⚠️ نام کاربری باید انگلیسی باشد.\n✅ نام کاربری های صحیح: ali12 | mahdi | ws1_ksdf\n❌ نام کاربری های نادرست: ali_ | tele@ | _mahdi | محسن', '{package_name} {package_id} {package_price} {package_price_raw} {package_data_limit} {package_data_limit_raw} {package_duration} {package_duration_seconds} {username_prefix} {username_suffix} {hwid_limit} {package_status} {price} {price_raw} {data_limit} {data_limit_raw} {duration} {duration_seconds} {name} {first_name} {username} {user_id} {support}'),
    'referral_text': ('متن صفحه دعوت دوستان', 'با لینک زیر دوستان خود را دعوت کنید.', '{name} {user_id} {ref_link} {referral_link} {support}'),
    'invoice_text': ('قالب فاکتور خرید', '🧾 فاکتور خرید\n\nشماره سفارش: {order_id}\nآیدی عددی کاربر: {user_id}\nنام بسته: {package_name}\nنام سرویس: {service_name}\nمبلغ اصلی: {price}\nتخفیف: {discount}\nکیف پول مصرف شده: {wallet_used}\nمبلغ قابل پرداخت: {payable}\nپرداخت Stars: {stars} ⭐️\nوضعیت سفارش: {status}\nتاریخ: {created_at}\n{service_message}', '{order_id} {user_id} {package_id} {package_name} {service_name} {price} {price_raw} {amount} {discount} {discount_raw} {wallet_used} {wallet_used_raw} {payable} {payable_raw} {stars} {status} {created_at} {service_message} {sub_link}'),
    'service_ready_text': ('متن آماده شدن سرویس', 'سرویس آماده شد ✅', '{name} {user_id} {order_id} {package_name} {service_name} {price} {sub_link} {support}'),
    'service_detail_text': ('قالب جزئیات/وضعیت سرویس', '🔗 {sub_link}\n📊 وضعیت سرویس : {status}\n👤 نام سرویس : {service_name}\n\n🌍 موقعیت سرویس : 🌟 پلن حجمی\n🗂 نام محصول : {package_name}\n🧾 شماره سفارش : {order_id}\n🗓 تاریخ خرید : {purchase_date}\n\n🔋 ترافیک : {data_limit}\n📥 حجم مصرفی : {used_traffic}\n💢 حجم باقی مانده : {remaining_traffic} ({remaining_percent}%)\n\n📅 تاریخ اتمام : {expire_at} ({expire_remaining})\n\n📶 آخرین زمان اتصال : {last_online}\n🔄 آخرین زمان آپدیت لینک اشتراک : {subscription_updated}\n#️⃣ کلاینت متصل شده : {client}\n\n💡 برای قطع دسترسی دیگران کافیست روی گزینه «تغییر لینک» کلیک کنید.', '{sub_link} {status} {service_name} {package_name} {order_id} {purchase_date} {data_limit} {used_traffic} {remaining_traffic} {remaining_percent} {expire_at} {expire_remaining} {last_online} {subscription_updated} {client}'),
    'payment_error_text': ('متن خطای پرداخت', 'در حال حاضر امکان ساخت لینک پرداخت وجود ندارد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.', '{name} {user_id} {support}'),
    'renew_warning_text': ('متن هشدار رو به اتمام سرویس', '⏰ سرویس شما رو به اتمام است.\n\n📦 بسته: {package_name}\n👤 سرویس: {service_name}\n⏳ زمان باقی‌مانده: {remaining_time}\n📅 تاریخ اتمام: {expire_at}\n\nبرای جلوگیری از قطع شدن، از بخش سرویس‌های من تمدید کنید.', '{name} {first_name} {username} {user_id} {service_name} {package_name} {package_id} {days} {hours} {remaining_time} {expire_at} {support}'),
    'card_payment_button_text': ('متن دکمه کارت به کارت', '💳 کارت به کارت', 'ایموجی پریمیوم مستقیم یا [CUSTOM_EMOJI_ID]'),
    'card_payment_text': ('قالب پیام کارت به کارت', '💳 پرداخت کارت به کارت\n\nمبلغ: {price}\nشماره کارت: {card_number}\nبه نام: {card_holder}\nبانک: {bank}\nشماره سفارش: {order_id}\n\nبعد از واریز، تصویر فیش را همینجا ارسال کنید.', '{price} {price_raw} {card_number} {card_holder} {bank} {order_id} {user_id} {name}'),
    'card_receipt_upload_prompt_text': ('متن درخواست ارسال فیش', '📎 حالا تصویر فیش یا فایل فیش را ارسال کنید. برای انصراف /cancel را بفرستید.', '{order_id} {price} {price_raw} {payment_kind}'),
    'card_receipt_submitted_text': ('متن ثبت شدن فیش', '✅ فیش شما ثبت شد و برای بررسی ارسال شد. نتیجه تایید یا رد همینجا به شما اعلام می‌شود.', '{receipt_id} {order_id} {price} {price_raw} {payment_kind}'),
}
CONTENT_MEDIA_KEYS = {'welcome_text','maintenance_text','rules_text','support_text','after_purchase_text'}

def texts_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f'texts:edit:{key}')] for key,(label,_,__) in TEXT_ITEMS.items()])

@texts_router.message(EditableButtonFilter(BTN_TEXTS))
async def texts_home(message: Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message); return
    # Kept only for users who still have an old reply keyboard cached in Telegram.
    # The standalone text editor is no longer exposed; all editing lives in live mode.
    from modules.edit_mode import scope_menu
    await message.answer(
        'ویرایش متن‌ها و دکمه‌ها به «🛠 حالت ویرایش» منتقل شده است.\nکدام فضا را می‌خواهی ویرایش کنی؟',
        reply_markup=scope_menu(),
    )


@texts_router.callback_query(F.data.startswith('texts:edit:'))
async def text_edit_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback); return
    key=callback.data.split(':',2)[2]
    if key not in TEXT_ITEMS:
        await callback.answer('این متن قابل ویرایش نیست.', show_alert=True); return
    label,default,variables=TEXT_ITEMS[key]
    current=str(db.get_setting(key, default) or '')
    sticker_count=len(get_content_stickers(key)) if key in CONTENT_MEDIA_KEYS else 0
    await state.set_state(TextState.wait_text); await state.update_data(key=key, pending_stickers=[])
    await callback.message.answer(f'✏️ {label}\nکلید: {key}\nمتغیرهای مجاز: {variables}\nاستیکرهای فعلی: {sticker_count}\n\nپیام بعدی کل متن فعلی است؛ Copy کن، فقط بخش دلخواه را تغییر بده و متن کامل را بفرست.')
    await callback.message.answer(current or ' ')
    await callback.answer()

@texts_router.message(TextState.wait_text)
async def text_edit_receive_text(message: Message, state: FSMContext):
    uid=message.from_user.id if message.from_user else None
    if not is_admin(uid): return
    if message.text is None:
        await message.answer('متن کامل جدید را به صورت پیام متنی بفرستید.'); return
    data=await state.get_data(); key=str(data.get('key') or '')
    if key not in TEXT_ITEMS:
        await state.clear(); await message.answer('ویرایش منقضی شد.'); return
    submitted = message_text_with_custom_emoji_tokens(message)
    await state.update_data(pending_text=submitted, pending_stickers=[])
    if key not in CONTENT_MEDIA_KEYS:
        db.set_setting(key, submitted); db.add_log(uid,'bot_text_updated','setting',key,{'full_text_editor':True})
        await state.clear(); await message.answer('ذخیره و اعمال شد ✅'); return
    await state.set_state(TextState.wait_extras)
    await message.answer('متن دریافت شد ✅\nاگر استیکرهای این صفحه را هم می‌خواهی جایگزین کنی، استیکرها را بفرست و /done بزن. اگر نه، مستقیم /done بزن.')

@texts_router.message(TextState.wait_extras, F.sticker)
async def text_edit_receive_sticker(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None): return
    data=await state.get_data(); stickers=list(data.get('pending_stickers') or [])
    if message.sticker: stickers.append(message.sticker.file_id)
    await state.update_data(pending_stickers=stickers); await message.answer(f'استیکر #{len(stickers)} اضافه شد ✅\nاستیکر بعدی یا /done')

@texts_router.message(TextState.wait_extras, F.text == '/done')
async def text_edit_finish(message: Message, state: FSMContext):
    uid=message.from_user.id if message.from_user else None
    if not is_admin(uid): return
    data=await state.get_data(); key=str(data.get('key') or ''); pending=data.get('pending_text'); stickers=list(data.get('pending_stickers') or [])
    if key not in TEXT_ITEMS or pending is None:
        await state.clear(); await message.answer('اطلاعات ویرایش ناقص بود.'); return
    db.set_setting(key,str(pending)); set_content_stickers(key,stickers); db.add_log(uid,'bot_text_updated','setting',key,{'stickers':len(stickers),'full_text_editor':True})
    await state.clear(); await message.answer(f'ذخیره و اعمال شد ✅\nمتن + {len(stickers)} استیکر')

@texts_router.message(TextState.wait_extras)
async def text_edit_invalid_extra(message: Message):
    await message.answer('در این مرحله فقط استیکر بفرستید یا /done بزنید.')
