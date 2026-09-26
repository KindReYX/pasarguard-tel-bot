from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_ADVANCED_SETTINGS, back_keyboard
from permissions import deny_callback, deny_message, is_super_admin
from utils import answer_callback, edit_or_answer, get_content_stickers, set_content_stickers, BUTTON_DETAIL_GROUPS, get_button_detail_override, set_button_detail_style, message_text_with_custom_emoji_tokens, truncate_custom_emoji_text
from modules.qr_tools import make_qr_file, background_available

advanced_router = Router()


class AdvancedState(StatesGroup):
    edit_value = State()
    content_text = State()
    content_extras = State()
    qr_size_input = State()
    qr_size_confirm = State()


CONTENT_MEDIA_KEYS = {'welcome_text', 'maintenance_text', 'rules_text', 'support_text', 'after_purchase_text'}


@dataclass(frozen=True)
class SettingItem:
    key: str
    label: str
    group: str
    value_type: str = 'text'  # text, int, float, bool, secret
    default: Any = ''
    hint: str = ''
    sensitive: bool = False


GROUPS: dict[str, str] = {
    'general_texts': '📝 متن‌های کلی ربات',
    'public_buttons': '🔘 دکمه‌های عمومی',
    'payments': '💳 روشن/خاموش درگاه ها',
    'stars': '⭐ تنظیمات Stars',
    'tetrapay': '💳 تنظیمات تتراپی',
    'plisio': '₿ تنظیمات Plisio',
    'card': '💳 تنظیمات کارت به کارت',
    'navasan': '💵 تنظیمات نوسان و دلار',
    'wallet': '💰 تنظیمات کیف پول',
    'referral': '👥 تنظیمات دعوت دوستان',
    'test': '🧪 تنظیمات سرویس تست',
    'renewal': '🔁 تنظیمات تمدید',
    'updates': '⬆️ تنظیمات بروزرسانی',
    'qr': '🖼 تنظیمات QR',
    'system': '⚙️ تنظیمات سیستمی قابل تغییر',
}

SETTINGS: list[SettingItem] = [
    # Texts and buttons
    SettingItem('welcome_text', 'متن شروع کاربر', 'general_texts', hint='متغیرها: {name} {first_name} {username} {user_id} {support} | ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('maintenance_text', 'متن حالت تعمیر', 'general_texts', 'text', 'ربات در حال بروزرسانی است.', 'متغیرها: {name} {first_name} {username} {user_id} {support} | ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('rules_text', 'متن قوانین', 'general_texts', hint='متغیرها: {name} {user_id} {support} | ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('support_text', 'متن پشتیبانی', 'general_texts', hint='متغیرها: {name} {user_id} {support} | ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('support_username', 'آیدی/لینک پشتیبانی برای {support}', 'general_texts', 'text', ''),
    SettingItem('after_purchase_text', 'متن بعد از خرید', 'general_texts', hint='متغیرها: {name} {user_id} {order_id} {package_name} {service_name} {price} {support} | ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('customer_menu_title', 'عنوان منوی کاربر', 'general_texts', 'text', 'منوی کاربری:', 'ایموجی کاستوم: [CUSTOM_EMOJI_ID]'),
    SettingItem('buy_button_text', 'متن دکمه خرید اشتراک', 'texts', 'text', '🛒 خرید اشتراک', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('wallet_button_text', 'متن دکمه کیف پول', 'texts', 'text', '💳 کیف پول من', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('services_button_text', 'متن دکمه سرویس های من', 'texts', 'text', '👤 سرویس های من', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('test_service_button_text', 'متن دکمه سرویس تست', 'texts', 'text', '🧪 سرویس تست', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('referral_button_text', 'متن دکمه دعوت دوستان', 'texts', 'text', '👥 دعوت دوستان', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('tutorials_button_text', 'متن دکمه آموزش', 'texts', 'text', '🎓 آموزش', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('support_button_text', 'متن دکمه پشتیبانی', 'texts', 'text', '🎫 پشتیبانی', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('rules_button_text', 'متن دکمه قوانین', 'texts', 'text', '📜 قوانین', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('test_service_text', 'متن صفحه سرویس تست', 'general_texts', hint='متغیرها: {name} {user_id} {support}'),
    SettingItem('package_username_prompt_text', 'متن بعد از انتخاب بسته / دریافت نام سرویس', 'general_texts', 'text', '📦 بسته انتخابی: {package_name}\n\nلطفا نام کاربری سرویس را ارسال کنید.\n\n⚠️ نام کاربری باید بدون کاراکترهای اضافه مانند @ ، فاصله ، خط تیره باشد.\n⚠️ نام کاربری باید انگلیسی باشد.\n✅ نام کاربری های صحیح: ali12 | mahdi | ws1_ksdf\n❌ نام کاربری های نادرست: ali_ | tele@ | _mahdi | محسن', 'متغیرها: {package_name} {package_id} {package_price} {package_price_raw} {package_data_limit} {package_data_limit_raw} {package_duration} {package_duration_seconds} {username_prefix} {username_suffix} {hwid_limit} {package_status} {price} {price_raw} {data_limit} {data_limit_raw} {duration} {duration_seconds} {name} {first_name} {username} {user_id} {support}'),
    SettingItem('referral_text', 'متن صفحه دعوت دوستان', 'general_texts', hint='متغیرها: {name} {user_id} {ref_link} {referral_link} {support}'),
    SettingItem('invoice_text', 'قالب فاکتور خرید', 'general_texts', 'text', '🧾 فاکتور خرید\n\nشماره سفارش: {order_id}\nآیدی عددی کاربر: {user_id}\nنام بسته: {package_name}\nنام سرویس: {service_name}\nمبلغ اصلی: {price}\nتخفیف: {discount}\nکیف پول مصرف شده: {wallet_used}\nمبلغ قابل پرداخت: {payable}\nپرداخت Stars: {stars} ⭐️\nوضعیت سفارش: {status}\nتاریخ: {created_at}\n{service_message}', 'متغیرها: {order_id} {user_id} {package_id} {package_name} {service_name} {price} {price_raw} {amount} {discount} {discount_raw} {wallet_used} {wallet_used_raw} {payable} {payable_raw} {stars} {status} {created_at} {service_message} {sub_link}'),
    SettingItem('service_ready_text', 'متن آماده شدن سرویس', 'general_texts', hint='متغیرها: {name} {user_id} {order_id} {package_name} {service_name} {price} {sub_link} {support}'),
    SettingItem('service_detail_text', 'قالب جزئیات/وضعیت سرویس', 'general_texts', 'text', '🔗 {sub_link}\n📊 وضعیت سرویس : {status}\n👤 نام سرویس : {service_name}\n\n🌍 موقعیت سرویس : 🌟 پلن حجمی\n🗂 نام محصول : {package_name}\n🧾 شماره سفارش : {order_id}\n🗓 تاریخ خرید : {purchase_date}\n\n🔋 ترافیک : {data_limit}\n📥 حجم مصرفی : {used_traffic}\n💢 حجم باقی مانده : {remaining_traffic} ({remaining_percent}%)\n\n📅 تاریخ اتمام : {expire_at} ({expire_remaining})\n\n📶 آخرین زمان اتصال : {last_online}\n🔄 آخرین زمان آپدیت لینک اشتراک : {subscription_updated}\n#️⃣ کلاینت متصل شده : {client}', 'متغیرها: {sub_link} {status} {service_name} {package_name} {order_id} {purchase_date} {data_limit} {used_traffic} {remaining_traffic} {remaining_percent} {expire_at} {expire_remaining} {last_online} {subscription_updated} {client}'),
    SettingItem('payment_error_text', 'متن عمومی خطای پرداخت برای مشتری', 'general_texts', default='در حال حاضر امکان ساخت لینک پرداخت وجود ندارد. لطفا کمی بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'),
    SettingItem('renew_warning_text', 'متن هشدار رو به اتمام سرویس', 'general_texts', 'text', '⏰ سرویس شما رو به اتمام است.\n\n📦 بسته: {package_name}\n👤 سرویس: {service_name}\n⏳ زمان باقی‌مانده: {remaining_time}\n📅 تاریخ اتمام: {expire_at}\n\nبرای جلوگیری از قطع شدن، از بخش سرویس‌های من تمدید کنید.', 'متغیرها: {name} {first_name} {username} {user_id} {service_name} {package_name} {package_id} {days} {hours} {remaining_time} {expire_at} {support} | ایموجی پریمیوم: مستقیم یا [CUSTOM_EMOJI_ID]'),
    # Public service-page buttons (text + style).
    SettingItem('service_btn_refresh_text', 'بروزرسانی', 'public_buttons', 'text', '🔄 بروزرسانی', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_configs_text', 'دریافت کانفیگ ها', 'public_buttons', 'text', '📋 دریافت کانفیگ ها', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_revoke_text', 'تغییر لینک', 'public_buttons', 'text', '♻️ تغییر لینک', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_renew_text', 'تمدید سرویس', 'public_buttons', 'text', '🔁 تمدید سرویس', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_auto_renew_text', 'تمدید خودکار', 'public_buttons', 'text', '🤖 تمدید خودکار', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_transfer_text', 'انتقال سرویس', 'public_buttons', 'text', '👤 انتقال سرویس', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_delete_text', 'حذف سرویس', 'public_buttons', 'text', '🗑 حذف سرویس', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),
    SettingItem('service_btn_back_text', 'بازگشت به سرویس های من', 'public_buttons', 'text', '🔙 بازگشت به سرویس های من', 'Premium Emoji: مستقیم یا [CUSTOM_EMOJI_ID]'),


    # Telegram Bot API button styles
    SettingItem('button_style_primary', 'رنگ دکمه های اصلی', 'buttons', 'text', 'primary', 'default / primary / success / danger'),
    SettingItem('button_style_success', 'رنگ دکمه های تایید و موفق', 'buttons', 'text', 'success', 'default / primary / success / danger'),
    SettingItem('button_style_danger', 'رنگ دکمه های حذف و رد', 'buttons', 'text', 'danger', 'default / primary / success / danger'),
    SettingItem('button_style_neutral', 'رنگ دکمه های برگشت و خنثی', 'buttons', 'text', 'default', 'default / primary / success / danger'),

    # Payment toggles
    SettingItem('payment_stars_enabled', 'فعال بودن پرداخت Stars', 'payments', 'bool', True),
    SettingItem('payment_tetrapay_enabled', 'فعال بودن پرداخت تتراپی', 'payments', 'bool', True),
    SettingItem('payment_plisio_enabled', 'فعال بودن پرداخت رمز ارز Plisio', 'payments', 'bool', True),
    SettingItem('payment_card_enabled', 'فعال بودن پرداخت کارت به کارت', 'payments', 'bool', False),
    SettingItem('wallet_charge_enabled', 'فعال بودن شارژ کیف پول', 'payments', 'bool', True),

    # Stars
    SettingItem('star_toman_rate', 'نرخ هر ۱ استار به تومان', 'stars', 'int', 0, 'مثال: 3000'),
    SettingItem('stars_rate_mode', 'حالت نرخ Stars', 'stars', 'text', 'manual', 'manual یا auto'),
    SettingItem('auto_star_toman_rate', 'نرخ خودکار ذخیره شده Stars', 'stars', 'int', 0),

    # TetraPay
    SettingItem('tetrapay_api_key', 'API Key تتراپی', 'tetrapay', 'secret', '', sensitive=True),
    SettingItem('tetrapay_create_invoice_url', 'آدرس ساخت سفارش تتراپی', 'tetrapay', 'text', 'https://example.com/api/create_order'),
    SettingItem('tetrapay_verify_url', 'آدرس verify تتراپی', 'tetrapay', 'text', 'https://example.com/api/verify'),
    SettingItem('tetrapay_callback_url', 'CallbackURL تتراپی', 'tetrapay', 'text', 'https://example.com/payments/tetrapay/callback'),
    SettingItem('tetrapay_callback_secret', 'Secret اختیاری Callback تتراپی', 'tetrapay', 'secret', '', sensitive=True, hint='اگر تتراپی query string را قبول نمی کند، خالی بگذارید.'),
    SettingItem('tetrapay_default_email', 'ایمیل پیش فرض تتراپی', 'tetrapay', 'text', 'customer@example.com'),
    SettingItem('tetrapay_default_mobile', 'موبایل پیش فرض تتراپی', 'tetrapay', 'text', '09000000000'),


    # Card to card
    SettingItem('card_number', 'شماره کارت مقصد', 'card', 'text', ''),
    SettingItem('card_holder', 'نام صاحب کارت', 'card', 'text', ''),
    SettingItem('card_bank', 'نام بانک', 'card', 'text', ''),
    SettingItem('card_payment_button_text', 'متن دکمه کارت به کارت', 'card', 'text', '💳 کارت به کارت', 'ایموجی پریمیوم: خود ایموجی را مستقیم بفرستید یا [CUSTOM_EMOJI_ID] متن دکمه'),
    SettingItem('card_payment_text', 'قالب پیام کارت به کارت', 'card', 'text', '💳 پرداخت کارت به کارت\n\nمبلغ: {price}\nشماره کارت: {card_number}\nبه نام: {card_holder}\nبانک: {bank}\nشماره سفارش: {order_id}\n\nبعد از واریز، تصویر فیش را همینجا ارسال کنید.', hint='متغیرها: {price} {price_raw} {card_number} {card_holder} {bank} {order_id} {user_id} {name}'),
    SettingItem('card_receipt_upload_prompt_text', 'متن درخواست ارسال فیش', 'card', 'text', '📎 حالا تصویر فیش یا فایل فیش را ارسال کنید. برای انصراف /cancel را بفرستید.', hint='متغیرها: {order_id} {price} {price_raw} {payment_kind}'),
    SettingItem('card_receipt_submitted_text', 'متن ثبت شدن فیش', 'card', 'text', '✅ فیش شما ثبت شد و برای بررسی ارسال شد. نتیجه تایید یا رد همینجا به شما اعلام می‌شود.', hint='متغیرها: {receipt_id} {order_id} {price} {price_raw} {payment_kind}'),

    # Plisio
    SettingItem('plisio_api_key', 'API Key / Secret Key Plisio', 'plisio', 'secret', '', sensitive=True),
    SettingItem('plisio_api_base_url', 'Base URL Plisio', 'plisio', 'text', 'https://example.com/api/v1'),
    SettingItem('plisio_callback_url', 'CallbackURL Plisio', 'plisio', 'text', 'https://example.com/payments/plisio/callback'),
    SettingItem('plisio_success_url', 'Success URL Plisio', 'plisio', 'text', 'https://example.com/payments/plisio/success'),
    SettingItem('plisio_fail_url', 'Fail URL Plisio', 'plisio', 'text', 'https://example.com/payments/plisio/fail'),
    SettingItem('plisio_default_currency', 'ارز پیش فرض Plisio', 'plisio', 'text', 'USDT_TRX', 'مثال: USDT_TRX'),
    SettingItem('plisio_allowed_psys_cids', 'لیست ارزهای مجاز Plisio', 'plisio', 'text', 'USDT_TRX,USDT_BSC,BTC,ETH,LTC,TRX'),
    SettingItem('plisio_default_email', 'ایمیل پیش فرض Plisio', 'plisio', 'text', 'customer@example.com'),
    SettingItem('plisio_expire_min', 'مدت اعتبار فاکتور Plisio به دقیقه', 'plisio', 'int', 60),
    SettingItem('plisio_min_usd_amount', 'حداقل مبلغ دلاری Plisio', 'plisio', 'float', 5.01, 'برای USDT_TRX فعلا حداقل 5.01 بگذارید.'),

    # Navasan / dollar
    SettingItem('navasan_api_key', 'API Key نوسان', 'navasan', 'secret', '', sensitive=True),
    SettingItem('navasan_base_url', 'Base URL نوسان', 'navasan', 'text', 'http://example.com'),
    SettingItem('navasan_usd_item', 'آیتم نرخ دلار نوسان', 'navasan', 'text', 'usd_sell'),
    SettingItem('navasan_auto_enabled', 'دریافت خودکار نرخ نوسان', 'navasan', 'bool', True),
    SettingItem('navasan_fetch_on_startup', 'دریافت نرخ هنگام شروع ربات', 'navasan', 'bool', True),
    SettingItem('crypto_usd_margin_percent', 'درصد حاشیه امنیت پرداخت کریپتو', 'navasan', 'float', 3),
    SettingItem('crypto_usd_toman_rate', 'نرخ دستی/ذخیره دلار به تومان', 'navasan', 'int', 0),

    # Wallet/referral/test
    SettingItem('wallet_min_charge_toman', 'حداقل شارژ کیف پول به تومان', 'wallet', 'int', 10000),
    SettingItem('referral_reward_amount', 'مبلغ پاداش دعوت به تومان', 'referral', 'int', 0),
    SettingItem('referral_condition', 'شرط پاداش دعوت', 'referral', 'text', 'payment', 'join یا payment'),
    SettingItem('test_service_enabled', 'فعال بودن سرویس تست', 'test', 'bool', True),
    SettingItem('test_template_id', 'Template ID سرویس تست', 'test', 'text', ''),
    SettingItem('test_service_generation', 'نسل سرویس تست برای ریست کلی', 'test', 'int', 1),
    SettingItem('test_service_cooldown_days', 'فاصله دریافت مجدد سرویس تست (روز)', 'test', 'int', 14, 'مثال: 14 یعنی کاربر 14 روز بعد دوباره می تواند تست بگیرد.'),
    SettingItem('test_service_reminder_text', 'متن یادآوری امکان دریافت مجدد تست', 'test', 'text', '🧪 دوباره می‌توانید سرویس تست بگیرید.\n\nاز منوی ربات وارد بخش «سرویس تست» شوید و سرویس جدیدتان را دریافت کنید.', 'متغیرها: {name} {first_name} {username} {user_id} {cooldown_days} {support}'),
    SettingItem('renewal_mode', 'نحوه اعمال تمدید', 'renewal', 'text', 'add', 'add = افزودن زمان/حجم باقی‌مانده | replace = جایگزینی و سوزاندن باقی‌مانده'),
    SettingItem('github_auto_check_enabled', 'چک خودکار آپدیت هر 24 ساعت', 'updates', 'bool', True, 'اگر خاموش باشد، فقط با دکمه «بررسی آپدیت همین الان» GitHub بررسی می‌شود.'),
    SettingItem('github_auto_update_enabled', 'نصب خودکار آپدیت', 'updates', 'bool', False, 'اگر روشن باشد و چک خودکار هم روشن باشد، Release جدید بعد از شناسایی به صورت خودکار نصب می‌شود.'),

    # QR delivery
    SettingItem('qr_size_percent', 'اندازه QR روی تصویر (درصد)', 'qr', 'int', 42, 'عدد 1 تا 100؛ 100 یعنی QR تمام ضلع کوتاه تصویر را می گیرد.'),

    # System-level values. Some require restart/nginx reload to take effect.
    SettingItem('subscription_base_url', 'آدرس پایه لینک ساب', 'system', 'text', ''),
    SettingItem('default_hwid_limit', 'تعداد کاربر همزمان پیش فرض', 'system', 'int', 0, '0 یعنی دیفالت سرور'),
    SettingItem('maintenance_mode', 'حالت تعمیر', 'system', 'bool', False),
    SettingItem('notifications_enabled', 'فعال بودن ثبت اعلان ها', 'system', 'bool', True),
    SettingItem('payment_webhook_secret', 'Secret عمومی Webhook پرداخت', 'system', 'secret', '', sensitive=True),
]

SETTINGS_BY_KEY = {item.key: item for item in SETTINGS}

BUTTON_TEXT_SETTING_KEYS = {
    'buy_button_text', 'test_service_button_text', 'services_button_text', 'wallet_button_text',
    'referral_button_text', 'tutorials_button_text', 'support_button_text', 'rules_button_text',
    'card_payment_button_text',
    'service_btn_refresh_text','service_btn_configs_text','service_btn_revoke_text','service_btn_renew_text',
    'service_btn_auto_renew_text','service_btn_transfer_text','service_btn_delete_text','service_btn_back_text',
}


def _raw_value(item: SettingItem) -> Any:
    return db.get_setting(item.key, item.default)


def _value_text(item: SettingItem, *, full: bool = False) -> str:
    value = _raw_value(item)
    if item.value_type == 'bool':
        return 'روشن' if bool(value) else 'خاموش'
    if item.sensitive:
        s = str(value or '')
        if not s:
            return 'تنظیم نشده'
        if full:
            return s
        if len(s) <= 8:
            return '••••'
        return f'{s[:4]}••••{s[-4:]} ({len(s)} کاراکتر)'
    s = str(value if value is not None else '')
    if not s:
        return 'تنظیم نشده'
    if not full and len(s) > 70:
        return truncate_custom_emoji_text(s, 70, suffix='...')
    return s


def _parse_value(item: SettingItem, raw: str) -> Any:
    raw = (raw or '').strip()
    if raw == '-' and item.value_type in {'text', 'secret'}:
        return ''
    if item.value_type == 'int':
        cleaned = raw.replace(',', '').replace('٬', '')
        if cleaned.startswith('-'):
            raise ValueError('عدد منفی مجاز نیست.')
        if not cleaned.isdigit():
            raise ValueError('لطفا عدد صحیح وارد کنید.')
        return int(cleaned)
    if item.value_type == 'float':
        cleaned = raw.replace(',', '.')
        try:
            value = float(cleaned)
        except ValueError as exc:
            raise ValueError('لطفا عدد وارد کنید. مثال: 5.01') from exc
        if value < 0:
            raise ValueError('عدد منفی مجاز نیست.')
        return value
    if item.value_type == 'bool':
        lowered = raw.lower()
        if lowered in {'1', 'on', 'true', 'yes', 'روشن', 'فعال'}:
            return True
        if lowered in {'0', 'off', 'false', 'no', 'خاموش', 'غیرفعال'}:
            return False
        raise ValueError('برای مقدار بولین روشن یا خاموش بفرستید.')
    return raw


def groups_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for group_id, title in GROUPS.items():
        rows.append([InlineKeyboardButton(text=title, callback_data=f'adv:cat:{group_id}')])
    rows.append([InlineKeyboardButton(text='📋 همه تنظیمات در یک نگاه', callback_data='adv:list')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(group: str) -> InlineKeyboardMarkup:
    if group == 'public_buttons':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📱 دکمه‌های صفحه سرویس', callback_data='adv:public:service')],
            [InlineKeyboardButton(text='🔙 برگشت به گروه ها', callback_data='adv:home')],
        ])
    rows = []
    if group == 'updates':
        rows.append([InlineKeyboardButton(text='🔄 بررسی آپدیت همین الان', callback_data='ghupd:check')])
    for item in SETTINGS:
        if item.group != group:
            continue
        if item.value_type == 'bool':
            rows.append([InlineKeyboardButton(text=f'{item.label}: {_value_text(item)}', callback_data=f'adv:toggle:{item.key}')])
        else:
            rows.append([InlineKeyboardButton(text=item.label, callback_data=f'adv:edit:{item.key}')])
    if group == 'buttons':
        style_labels = {'default': '⚪ پیش فرض', 'primary': '🔵 آبی', 'success': '🟢 سبز', 'danger': '🔴 قرمز'}
        for key, label in [('button_style_primary','اصلی'),('button_style_success','تایید'),('button_style_danger','حذف/رد'),('button_style_neutral','خنثی/برگشت')]:
            current = str(db.get_setting(key, SETTINGS_BY_KEY[key].default) or 'default')
            rows.append([InlineKeyboardButton(text=f'{label}: {style_labels.get(current, current)}', callback_data=f'adv:stylemenu:{key}')])
        rows.append([InlineKeyboardButton(text='🧩 تغییر رنگ جز به جز', callback_data='adv:detailstyles')])
    if group == 'navasan':
        rows.append([InlineKeyboardButton(text='🔄 دریافت نرخ دلار از نوسان', callback_data='settings:navasan:fetch_now')])
    if group == 'card':
        rows.append([InlineKeyboardButton(text='👥 انتخاب ادمین های بررسی فیش', callback_data='cardadmin:approvers')])
        rows.append([InlineKeyboardButton(text='🧾 فیش های در انتظار', callback_data='cardadmin:pending')])
    rows.append([InlineKeyboardButton(text='🔙 برگشت به گروه ها', callback_data='adv:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def item_detail_text(item: SettingItem) -> str:
    text = f'{item.label}\n\nکلید: {item.key}\nمقدار فعلی: {_value_text(item)}\n'
    if item.hint:
        text += f'\nراهنما: {item.hint}\n'
    if item.sensitive:
        text += '\nبرای حذف مقدار، فقط - بفرستید. مقدار کامل به خاطر امنیت نمایش داده نمی شود.\n'
    if item.key in {'payment_webhook_secret'}:
        text += '\nنکته: بعضی تنظیمات سیستمی بعد از تغییر نیاز به ریستارت ربات یا reload nginx دارند.\n'
    text += '\nمقدار جدید را ارسال کنید.'
    return text


def home_text() -> str:
    return (
        '🧩 تنظیمات پیشرفته\n\n'
        'از این بخش می توانید تنظیمات QR، درگاه ها، کلیدهای API، نوسان، Plisio، تتراپی، کیف پول، دعوت دوستان و سرویس تست را از داخل خود ربات تغییر دهید.\n\n'
        'برای ویرایش سریع متن‌های اصلی مثل ولکام، قوانین و پشتیبانی از «📝 متن‌های کلی ربات» استفاده کنید.\n'
        'برای ویرایش زنده هر پیام و دکمه همچنان «🛠 حالت ویرایش» در منوی ادمین در دسترس است.\n\n'
        'موارد حساس فقط برای سوپرادمین قابل تغییر هستند.'
    )


@advanced_router.message(EditableButtonFilter(BTN_ADVANCED_SETTINGS))
async def advanced_home(message: Message):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await message.answer('🧩 تنظیمات پیشرفته', reply_markup=back_keyboard())
    await message.answer(home_text(), reply_markup=groups_keyboard())


@advanced_router.callback_query(F.data == 'adv:home')
async def advanced_home_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await edit_or_answer(callback, home_text(), reply_markup=groups_keyboard())
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:cat:'))
async def advanced_category(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    group = callback.data.split(':', 2)[2]
    if group not in GROUPS:
        await callback.answer('گروه پیدا نشد.', show_alert=True)
        return
    if group == 'public_buttons':
        await edit_or_answer(callback, '🔘 دکمه‌های عمومی\n\nبخش موردنظر را انتخاب کنید:', reply_markup=category_keyboard(group))
        await answer_callback(callback)
        return
    lines = [GROUPS[group], '']
    if group == 'updates':
        from version import __version__
        latest = str(db.get_setting('github_update_latest_version', '') or 'هنوز بررسی نشده')
        last_check = str(db.get_setting('github_update_last_check_at', '') or 'هنوز بررسی نشده')
        lines.extend([f'نسخه نصب شده: {__version__}', f'آخرین نسخه دیده شده: {latest}', f'آخرین بررسی: {last_check}', ''])
    for item in SETTINGS:
        if item.group == group:
            lines.append(f'{item.label}: {_value_text(item)}')
    await edit_or_answer(callback, '\n'.join(lines), reply_markup=category_keyboard(group))
    await answer_callback(callback)


SERVICE_BUTTON_KEYS = [
    ('service_btn_refresh_text','بروزرسانی'), ('service_btn_configs_text','دریافت کانفیگ ها'),
    ('service_btn_revoke_text','تغییر لینک'), ('service_btn_renew_text','تمدید سرویس'),
    ('service_btn_auto_renew_text','تمدید خودکار'), ('service_btn_transfer_text','انتقال سرویس'),
    ('service_btn_delete_text','حذف سرویس'), ('service_btn_back_text','بازگشت به سرویس های من'),
]


def service_buttons_admin_keyboard() -> InlineKeyboardMarkup:
    rows=[]
    labels={'default':'⚪','primary':'🔵','success':'🟢','danger':'🔴'}
    for key,label in SERVICE_BUTTON_KEYS:
        style_key=key.replace('_text','_style')
        style=str(db.get_setting(style_key,'default') or 'default')
        rows.append([
            InlineKeyboardButton(text=f'✏️ {label}', callback_data=f'adv:edit:{key}'),
            InlineKeyboardButton(text=f'{labels.get(style,"⚪")} رنگ', callback_data=f'adv:svcstyle:{key}'),
        ])
    rows.append([InlineKeyboardButton(text='🔙 دکمه‌های عمومی', callback_data='adv:cat:public_buttons')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@advanced_router.callback_query(F.data == 'adv:public:service')
async def advanced_public_service_buttons(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    await edit_or_answer(callback, '📱 دکمه‌های صفحه سرویس\n\nبرای هر دکمه می‌توانید متن/Premium Emoji و رنگ را تغییر دهید.', reply_markup=service_buttons_admin_keyboard())
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:svcstyle:'))
async def advanced_service_button_style(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    key=callback.data.split(':',2)[2]
    if key not in dict(SERVICE_BUTTON_KEYS):
        await callback.answer('دکمه معتبر نیست.', show_alert=True); return
    skey=key.replace('_text','_style')
    current=str(db.get_setting(skey,'default') or 'default')
    opts=[('default','⚪ پیش‌فرض'),('primary','🔵 آبی'),('success','🟢 سبز'),('danger','🔴 قرمز')]
    rows=[[InlineKeyboardButton(text=('✅ ' if v==current else '')+label, callback_data=f'adv:svcstyleset:{key}:{v}', style=(None if v=='default' else v))] for v,label in opts]
    rows.append([InlineKeyboardButton(text='🔙 صفحه سرویس', callback_data='adv:public:service')])
    await edit_or_answer(callback, f'🎨 رنگ دکمه {dict(SERVICE_BUTTON_KEYS)[key]}', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:svcstyleset:'))
async def advanced_service_button_style_set(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    _,_,key,value=callback.data.split(':',3)
    if key not in dict(SERVICE_BUTTON_KEYS) or value not in {'default','primary','success','danger'}:
        await callback.answer('مقدار نامعتبر است.', show_alert=True); return
    db.set_setting(key.replace('_text','_style'), value)
    db.add_log(callback.from_user.id, 'service_button_style_changed', 'settings', key, {'style':value})
    await callback.answer('ذخیره شد ✅')
    await advanced_service_button_style(callback)


@advanced_router.callback_query(F.data == 'adv:list')
async def advanced_list(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    lines = ['📋 خلاصه تنظیمات پیشرفته', '']
    current_group = None
    for item in SETTINGS:
        if item.group not in GROUPS:
            continue
        if current_group != item.group:
            current_group = item.group
            lines.extend(['', GROUPS.get(item.group, item.group)])
        lines.append(f'{item.label}: {_value_text(item)}')
    text = '\n'.join(lines)
    if len(text) > 3800:
        text = truncate_custom_emoji_text(text, 3700, suffix='') + '\n\nمتن کوتاه شد. برای تغییر هر بخش از دکمه های گروهی استفاده کنید.'
    await edit_or_answer(callback, text, reply_markup=groups_keyboard())
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:toggle:'))
async def advanced_toggle(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    key = callback.data.split(':', 2)[2]
    item = SETTINGS_BY_KEY.get(key)
    if not item or item.value_type != 'bool':
        await callback.answer('این گزینه قابل روشن/خاموش کردن نیست.', show_alert=True)
        return
    current = bool(_raw_value(item))
    db.set_setting(key, not current)
    db.add_log(callback.from_user.id, 'advanced_setting_toggled', 'settings', key, {'value': not current})
    await callback.answer('ذخیره شد.')
    await advanced_category(callback)


@advanced_router.callback_query(F.data.startswith('adv:stylemenu:'))
async def advanced_style_menu(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    key = callback.data.split(':', 2)[2]
    if key not in {'button_style_primary','button_style_success','button_style_danger','button_style_neutral'}:
        await callback.answer('گزینه معتبر نیست.', show_alert=True); return
    current = str(db.get_setting(key, SETTINGS_BY_KEY[key].default) or 'default')
    opts=[('default','⚪ پیش فرض'),('primary','🔵 آبی'),('success','🟢 سبز'),('danger','🔴 قرمز')]
    rows=[]
    for value,label in opts:
        mark='✅ ' if value==current else ''
        rows.append([InlineKeyboardButton(text=mark+label, callback_data=f'adv:styleset:{key}:{value}', style=(None if value=='default' else value))])
    rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='adv:cat:buttons')])
    await edit_or_answer(callback, f'🎨 انتخاب رنگ برای {SETTINGS_BY_KEY[key].label}\n\nرنگ ها توسط خود تلگرام نمایش داده می شوند.', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await answer_callback(callback)

@advanced_router.callback_query(F.data.startswith('adv:styleset:'))
async def advanced_style_set(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    _,_,key,value = callback.data.split(':',3)
    if key not in {'button_style_primary','button_style_success','button_style_danger','button_style_neutral'} or value not in {'default','primary','success','danger'}:
        await callback.answer('مقدار معتبر نیست.', show_alert=True); return
    db.set_setting(key, value)
    db.add_log(callback.from_user.id, 'button_style_changed', 'settings', key, {'value': value})
    await callback.answer('رنگ ذخیره شد.')
    # Update the same message immediately.
    await advanced_style_menu(callback)



@advanced_router.callback_query(F.data == 'adv:detailstyles')
async def detailed_styles_home(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    rows=[]
    for group_id, (title, items) in BUTTON_DETAIL_GROUPS.items():
        rows.append([InlineKeyboardButton(text=f'{title} ({len(items)})', callback_data=f'adv:detailgroup:{group_id}')])
    rows.append([InlineKeyboardButton(text='🔙 برگشت به تنظیم رنگ', callback_data='adv:cat:buttons')])
    await edit_or_answer(callback, '🧩 تغییر رنگ جز به جز\n\nهر گزینه فقط همان دکمه یا خانواده دکمه را تغییر می دهد. اگر روی «ارث بری از حالت کلی» باشد، رنگ از تنظیم کلی گرفته می شود.', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:detailgroup:'))
async def detailed_styles_group(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    group_id=callback.data.split(':',2)[2]
    group=BUTTON_DETAIL_GROUPS.get(group_id)
    if not group:
        await callback.answer('گروه پیدا نشد.', show_alert=True); return
    title, items=group
    labels={'__inherit__':'↩️ کلی','default':'⚪ پیش فرض','primary':'🔵 آبی','success':'🟢 سبز','danger':'🔴 قرمز'}
    rows=[]
    for detail_id,label in items:
        try:
            raw=str(db.get_setting(f'button_detail_style:{detail_id}','__inherit__') or '__inherit__')
        except Exception:
            raw='__inherit__'
        rows.append([InlineKeyboardButton(text=f'{label}: {labels.get(raw, raw)}', callback_data=f'adv:detailmenu:{group_id}:{detail_id}')])
    rows.append([InlineKeyboardButton(text='🔙 گروه های جز به جز', callback_data='adv:detailstyles')])
    await edit_or_answer(callback, f'{title}\n\nدکمه موردنظر را انتخاب کنید:', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:detailmenu:'))
async def detailed_style_menu(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    parts=callback.data.split(':',3)
    if len(parts)!=4:
        await callback.answer('داده نامعتبر است.',show_alert=True); return
    group_id,detail_id=parts[2],parts[3]
    group=BUTTON_DETAIL_GROUPS.get(group_id)
    if not group or detail_id not in {x[0] for x in group[1]}:
        await callback.answer('دکمه پیدا نشد.',show_alert=True); return
    label=next(x[1] for x in group[1] if x[0]==detail_id)
    current=str(db.get_setting(f'button_detail_style:{detail_id}','__inherit__') or '__inherit__')
    options=[('__inherit__','↩️ ارث بری از حالت کلی'),('default','⚪ پیش فرض'),('primary','🔵 آبی'),('success','🟢 سبز'),('danger','🔴 قرمز')]
    rows=[]
    for value,title in options:
        mark='✅ ' if value==current else ''
        rows.append([InlineKeyboardButton(text=mark+title, callback_data=f'adv:detailset:{group_id}:{detail_id}:{value}', style=(None if value in {'__inherit__','default'} else value))])
    rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data=f'adv:detailgroup:{group_id}')])
    await edit_or_answer(callback, f'🎨 {label}\n\nرنگ فعلی: {current}', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await answer_callback(callback)


@advanced_router.callback_query(F.data.startswith('adv:detailset:'))
async def detailed_style_set(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback); return
    parts=callback.data.split(':',4)
    if len(parts)!=5:
        await callback.answer('داده نامعتبر است.',show_alert=True); return
    group_id,detail_id,value=parts[2],parts[3],parts[4]
    if value not in {'__inherit__','default','primary','success','danger'}:
        await callback.answer('رنگ نامعتبر است.',show_alert=True); return
    group=BUTTON_DETAIL_GROUPS.get(group_id)
    if not group or detail_id not in {x[0] for x in group[1]}:
        await callback.answer('دکمه نامعتبر است.',show_alert=True); return
    set_button_detail_style(detail_id,value)
    db.add_log(callback.from_user.id,'button_detail_style_changed','settings',detail_id,{'value':value})
    await callback.answer('رنگ ذخیره شد ✅')
    label=next(x[1] for x in group[1] if x[0]==detail_id)
    options=[('__inherit__','↩️ ارث بری از حالت کلی'),('default','⚪ پیش فرض'),('primary','🔵 آبی'),('success','🟢 سبز'),('danger','🔴 قرمز')]
    rows=[]
    for opt_value,title in options:
        mark='✅ ' if opt_value==value else ''
        rows.append([InlineKeyboardButton(text=mark+title, callback_data=f'adv:detailset:{group_id}:{detail_id}:{opt_value}', style=(None if opt_value in {'__inherit__','default'} else opt_value))])
    rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data=f'adv:detailgroup:{group_id}')])
    await edit_or_answer(callback, f'🎨 {label}\n\nرنگ فعلی: {value}', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@advanced_router.callback_query(F.data.startswith('adv:edit:'))
async def advanced_edit_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    key = callback.data.split(':', 2)[2]
    item = SETTINGS_BY_KEY.get(key)
    if not item:
        await callback.answer('کلید معتبر نیست.', show_alert=True)
        return
    if item.value_type == 'bool':
        await callback.answer('این گزینه با دکمه روشن/خاموش تغییر می کند.', show_alert=True)
        return
    if key == 'qr_size_percent':
        await state.set_state(AdvancedState.qr_size_input)
        await state.update_data(advanced_key=key)
        current = int(db.get_setting('qr_size_percent', 42) or 42)
        await edit_or_answer(
            callback,
            f'🖼 اندازه QR روی تصویر\n\nمقدار فعلی: {current}%\n\nیک عدد از 1 تا 100 ارسال کنید.\n100 = بیشترین اندازه و 1 = کمترین اندازه.\nبعد از ارسال، پیش نمایش می بینید و تا قبل از تایید چیزی ذخیره نمی شود.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data='adv:qrcancel')]]),
        )
        await answer_callback(callback)
        return
    if key in CONTENT_MEDIA_KEYS:
        await state.set_state(AdvancedState.content_text)
        await state.update_data(advanced_key=key, pending_stickers=[])
        sticker_count = len(get_content_stickers(key))
        intro = f'✏️ {item.label}\n\n'
        if item.hint:
            intro += f'{item.hint}\n\n'
        intro += (
            f'استیکرهای فعلی: {sticker_count}\n'
            'پیام بعدی کل متن فعلی است. آن را Copy کنید، فقط بخش دلخواه را تغییر دهید و متن کامل جدید را بفرستید.\n'
            'بعد از متن می توانید استیکر بفرستید و در پایان /done بزنید.'
        )
        await edit_or_answer(
            callback, intro,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data=f'adv:cat:{item.group}')]]),
        )
        current = str(db.get_setting(item.key, item.default) or '')
        await callback.message.answer(current or ' ')
        await answer_callback(callback)
        return
    await state.set_state(AdvancedState.edit_value)
    await state.update_data(advanced_key=key)
    if item.group == 'general_texts':
        intro = f'✏️ {item.label}\n\n'
        if item.hint:
            intro += f'{item.hint}\n\n'
        intro += 'پیام بعدی کل متن فعلی است. Copy کنید، فقط بخش دلخواه را تغییر دهید و متن کامل جدید را بفرستید.'
        await edit_or_answer(
            callback, intro,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data=f'adv:cat:{item.group}')]]),
        )
        current = str(db.get_setting(item.key, item.default) or '')
        await callback.message.answer(current or ' ')
    else:
        await edit_or_answer(
            callback,
            item_detail_text(item),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ لغو', callback_data=f'adv:cat:{item.group}')]]),
        )
    await answer_callback(callback)


@advanced_router.message(AdvancedState.qr_size_input)
async def advanced_qr_size_preview(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or '').strip().replace('٪', '').replace('%', '')
    if not raw.isdigit():
        await message.answer('لطفا فقط یک عدد از 1 تا 100 ارسال کنید.')
        return
    value = int(raw)
    if value < 1 or value > 100:
        await message.answer('مقدار باید بین 1 تا 100 باشد.')
        return

    await state.update_data(pending_qr_size_percent=value)
    await state.set_state(AdvancedState.qr_size_confirm)
    try:
        preview = make_qr_file(
            'https://example.com/subscription/qr-preview',
            filename=f'qr_preview_{value}.png',
            qr_size_percent=value,
        )
        background_note = '' if background_available() else '\n\n⚠️ back.jpg پیدا نشد؛ پیش نمایش فعلا QR ساده است.'
        await message.answer_photo(
            preview,
            caption=(
                f'🖼 پیش نمایش اندازه QR: {value}%'
                f'{background_note}\n\n'
                'اگر اندازه مناسب است تایید کنید؛ در غیر این صورت «تغییر مقدار» را بزنید.'
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='✅ تایید و ذخیره', callback_data='adv:qrconfirm')],
                [InlineKeyboardButton(text='✏️ تغییر مقدار', callback_data='adv:qrretry')],
                [InlineKeyboardButton(text='❌ لغو', callback_data='adv:qrcancel')],
            ]),
        )
    except Exception as exc:
        await state.set_state(AdvancedState.qr_size_input)
        await message.answer(f'ساخت پیش نمایش ممکن نشد: {type(exc).__name__}\nمقدار دیگری وارد کنید.')


@advanced_router.callback_query(F.data == 'adv:qrconfirm')
async def advanced_qr_size_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    data = await state.get_data()
    value = int(data.get('pending_qr_size_percent') or 0)
    if value < 1 or value > 100:
        await callback.answer('مقدار پیش نمایش پیدا نشد؛ دوباره تنظیم کنید.', show_alert=True)
        await state.set_state(AdvancedState.qr_size_input)
        return
    db.set_setting('qr_size_percent', value)
    db.add_log(callback.from_user.id, 'qr_size_percent_changed', 'settings', 'qr_size_percent', {'value': value})
    await state.clear()
    try:
        await callback.message.edit_caption(
            caption=f'✅ اندازه QR روی {value}% ذخیره شد و از این به بعد روی تصاویر اعمال می شود.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔙 تنظیمات QR', callback_data='adv:cat:qr')]
            ]),
        )
    except Exception:
        pass
    await callback.answer('ذخیره شد ✅')


@advanced_router.callback_query(F.data == 'adv:qrretry')
async def advanced_qr_size_retry(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.set_state(AdvancedState.qr_size_input)
    await state.update_data(pending_qr_size_percent=None)
    try:
        await callback.message.edit_caption(
            caption='✏️ مقدار جدید را از 1 تا 100 ارسال کنید.\nتا وقتی تایید نکنید، تنظیم قبلی تغییر نمی کند.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='❌ لغو', callback_data='adv:qrcancel')]
            ]),
        )
    except Exception:
        await callback.message.answer('مقدار جدید را از 1 تا 100 ارسال کنید.')
    await callback.answer()


@advanced_router.callback_query(F.data == 'adv:qrcancel')
async def advanced_qr_size_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    try:
        await callback.message.answer(GROUPS['qr'], reply_markup=category_keyboard('qr'))
        await callback.message.delete()
    except Exception:
        await edit_or_answer(callback, GROUPS['qr'], reply_markup=category_keyboard('qr'))
    await answer_callback(callback, 'لغو شد؛ تنظیم قبلی حفظ شد.')


@advanced_router.message(AdvancedState.content_text)
async def advanced_content_receive_text(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    if not message.text or message.text.strip().lower() == '/done':
        await message.answer('اول متن جدید را بفرستید.')
        return
    submitted = message_text_with_custom_emoji_tokens(message)
    await state.update_data(pending_text=submitted, pending_stickers=[])
    await state.set_state(AdvancedState.content_extras)
    await message.answer(
        'متن دریافت شد ✅\n\n'
        'حالا استیکرهای بزرگ را یکی یکی بفرستید. وقتی تمام شد /done بزنید.\n'
        'اگر استیکر نمی خواهید، همین الان /done بزنید.'
    )


@advanced_router.message(AdvancedState.content_extras, F.sticker)
async def advanced_content_receive_sticker(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    stickers = list(data.get('pending_stickers') or [])
    if message.sticker:
        stickers.append(message.sticker.file_id)
        await state.update_data(pending_stickers=stickers)
    await message.answer(f'استیکر #{len(stickers)} اضافه شد ✅\nاستیکر بعدی را بفرستید یا /done بزنید.')


@advanced_router.message(AdvancedState.content_extras, F.text == '/done')
async def advanced_content_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    key = str(data.get('advanced_key') or '')
    item = SETTINGS_BY_KEY.get(key)
    pending_text = data.get('pending_text')
    stickers = list(data.get('pending_stickers') or [])
    if not item or pending_text is None:
        await state.clear()
        await message.answer('اطلاعات ویرایش ناقص بود. دوباره تلاش کنید.', reply_markup=back_keyboard())
        return
    db.set_setting(key, str(pending_text))
    set_content_stickers(key, stickers)
    db.add_log(message.from_user.id, 'advanced_content_changed', 'settings', key, {'stickers': len(stickers)})
    await state.clear()
    await message.answer(f'ذخیره شد ✅\n{item.label} + {len(stickers)} استیکر', reply_markup=back_keyboard())
    await message.answer(GROUPS[item.group], reply_markup=category_keyboard(item.group))


@advanced_router.message(AdvancedState.content_extras)
async def advanced_content_invalid_extra(message: Message):
    await message.answer('در این مرحله فقط استیکر بفرستید یا /done بزنید.')


@advanced_router.message(AdvancedState.edit_value)
async def advanced_edit_finish(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    key = data.get('advanced_key')
    item = SETTINGS_BY_KEY.get(str(key))
    if not item:
        await state.clear()
        await message.answer('کلید پیدا نشد.', reply_markup=back_keyboard())
        return
    try:
        raw_input = message_text_with_custom_emoji_tokens(message) if item.value_type == 'text' else (message.text or '')
        value = _parse_value(item, raw_input)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if item.key in BUTTON_TEXT_SETTING_KEYS:
        previous = str(db.get_setting(item.key, item.default) or '').strip()
        new_text = str(value or '').strip()
        if previous and previous != new_text:
            alias_key = f'button_text_aliases:{item.key}'
            aliases = db.get_setting(alias_key, [])
            if not isinstance(aliases, list):
                aliases = []
            aliases = [str(x).strip() for x in aliases if str(x).strip() and str(x).strip() != new_text]
            if previous not in aliases:
                aliases.append(previous)
            db.set_setting(alias_key, aliases[-10:])
    db.set_setting(item.key, value)
    log_details = {'value': '***' if item.sensitive else value}
    db.add_log(message.from_user.id, 'advanced_setting_changed', 'settings', item.key, log_details)
    await state.clear()
    await message.answer(f'ذخیره شد ✅\n{item.label}: {_value_text(item)}', reply_markup=back_keyboard())
    await message.answer(GROUPS[item.group], reply_markup=category_keyboard(item.group))
