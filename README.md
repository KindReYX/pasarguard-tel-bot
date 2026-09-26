# Telegram Admin Bot Modular

این نسخه ماژولار برای ربات ادمین ساخته شده است.

## نقش‌ها

- `super_admin`: دسترسی کامل، لاگ‌ها، بکاپ، مدیریت ادمین‌ها
- `admin`: مدیریت بسته‌ها، کاربران، سفارش‌ها، گزارش‌ها، تخفیف، کیف پول، متن‌ها، پیام همگانی، تنظیمات
- `seller`: مشاهده بسته‌ها، مشاهده مشتری‌های خودش، ارسال پیام به مشتری‌های خودش، تیکت و اعلان‌ها

## فایل‌ها

- `bot.py`: فایل اصلی و include کردن routerها
- `config.py`: خواندن env
- `db.py`: دیتابیس SQLite و schema
- `api_client.py`: اتصال به PasarGuard API
- `permissions.py`: نقش‌ها و سطح دسترسی
- `keyboards.py`: دکمه‌های اصلی
- `modules/packs.py`: مدیریت بسته‌ها
- `modules/users.py`: مدیریت کاربران و پیام اختصاصی
- `modules/orders.py`: سفارش‌ها
- `modules/reports.py`: گزارش فروش و کاربران
- `modules/coupons.py`: کد تخفیف
- `modules/wallet.py`: کیف پول

- `modules/stars.py`: مدیریت موجودی Stars، تراکنش‌های Stars، ریفاند پرداخت Stars و راهنمای برداشت
- `modules/logs.py`: لاگ ادمین‌ها، فقط سوپرادمین
- `modules/texts.py`: متن‌های ربات
- `modules/broadcast.py`: پیام همگانی پیشرفته با فیلترهای متنوع
- `modules/tickets.py`: تیکت پشتیبانی
- `modules/backups.py`: بکاپ دیتابیس، فقط سوپرادمین
- `modules/exports.py`: خروجی اکسل کاربران و سفارش‌ها
- `modules/settings.py`: تنظیمات عمومی و حالت تعمیر
- `modules/admins.py`: مدیریت ادمین، فقط سوپرادمین
- `modules/notifications.py`: اعلان‌ها

## نصب

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## نکته

این نسخه برای شروع ماژولار است و روی همان API تمپلیت‌ها و گروه‌هایی که قبلا استخراج کردیم کار می‌کند:

- `GET /api/user_templates/simple?all=true`
- `GET /api/user_template/{template_id}`
- `POST /api/user_template`
- `PUT /api/user_template/{template_id}`
- `DELETE /api/user_template/{template_id}`
- `GET /api/groups/simple?all=true`

## Telegram Stars

کاربر پرداخت را با currency=`XTR` به ربات انجام می‌دهد. موجودی Stars در بالانس خود ربات قرار می‌گیرد. در Bot API متدهای `getMyStarBalance`، `getStarTransactions` و `refundStarPayment` برای مشاهده موجودی، تراکنش‌ها و ریفاند وجود دارد. برداشت درآمد Stars به حساب شخصی از داخل Bot API انجام نمی‌شود و باید از مسیرهای رسمی تلگرام/Fragment یا پنل مالک ربات انجام شود.

## تغییرات این نسخه

- منوی پایین در ورود به بخش‌های مدیریتی به دکمه «بازگشت» تغییر می‌کند.
- دکمه «بازگشت» به صورت سراسری به منوی اصلی ادمین برمی‌گردد و state فعلی را پاک می‌کند.
- کد تخفیف قابل لغو، فعال/غیرفعال و حذف شد.
- حالت تعمیر از دکمه اصلی «🚧 حالت تعمیر» هندل می‌شود و دیگر not handled نمی‌دهد.
- مدیریت ادمین‌ها به شکل دکمه شیشه‌ای است و سوپرادمین می‌تواند ادمین/فروشنده اضافه کند، نقش را تغییر دهد، فعال/غیرفعال کند یا حذف کند.
- ماژول Stars اضافه شد: موجودی ربات، تراکنش‌های اخیر و راهنمای برداشت.

## نکته Stars

کاربر با پرداخت Stars به ربات، موجودی Stars ربات را افزایش می‌دهد. Bot API موجودی و تراکنش‌ها را نمایش می‌دهد، اما برداشت مستقیم از داخل Bot API انجام نمی‌شود؛ برداشت باید توسط مالک ربات از مسیر رسمی Telegram/Fragment انجام شود.

## نسخه اصلاحی v3

تغییرات این نسخه:

- ادیت پیام‌های callback امن شد؛ اگر ادمین یک دکمه را دوبار بزند، خطای `message is not modified` باعث infinite loading نمی‌شود.
- برای callbackهای تکراری از پیام موقت بالای چت (`callback.answer`) استفاده می‌شود.
- دکمه و ماژول `🔐 جوین اجباری` اضافه شد.
- هر سوپرادمین یا ادمین می‌تواند کانال/گروه جوین اجباری اضافه، غیرفعال/فعال یا حذف کند.

فرمت افزودن جوین اجباری:

```text
@channel_username
```

یا برای کانال/گروه خصوصی:

```text
-1001234567890 | نام کانال | https://example.com/+invite
```

نکته: برای اینکه ربات بتواند عضویت کاربر را بررسی کند، ربات باید داخل کانال/گروه موردنظر عضو باشد و بهتر است ادمین باشد.

## تغییرات v4 بخش بسته‌ها

- برای هر بسته قیمت محلی با واحد Stars اضافه شد.
- قیمت در جدول `package_settings` ذخیره می‌شود و به API پنل وابسته نیست.
- هنگام ساخت بسته، قیمت بسته به عنوان مرحله اجباری گرفته می‌شود.
- در جزئیات هر بسته دکمه `💰 قیمت` برای تغییر قیمت همان بسته اضافه شد.
- در لیست بسته‌ها دو گزینه برای تغییر قیمت گروهی اضافه شد:
  - `📈 افزایش درصدی قیمت‌ها`
  - `📉 کاهش درصدی قیمت‌ها`
- تغییر گروهی روی همه تمپلیت‌های دریافت‌شده از پنل اعمال می‌شود. بسته‌هایی که قیمتشان صفر است، صفر باقی می‌مانند.


## تغییرات نسخه v5 مشتری و پرداخت Stars

- قیمت پایه بسته ها داخل ربات با واحد تومان ذخیره می شود.
- تبدیل تومان به Telegram Stars فقط هنگام پرداخت مشتری انجام می شود.
- نرخ تبدیل هر ۱ استار به تومان از بخش تنظیمات ادمین قابل تنظیم است.
- مسیر تنظیم نرخ: پنل ادمین -> تنظیمات -> نرخ Stars.
- اگر حالت خودکار نرخ Stars فعال باشد اما نرخ خودکار هنوز ثبت نشده باشد، سیستم از نرخ دستی استفاده می کند.
- منوی مشتری شامل دکمه خرید اشتراک است.
- بعد از انتخاب بسته، ربات نام کاربری سرویس را با قوانین زیر اعتبارسنجی می کند:
  - فقط حروف انگلیسی، عدد و آندرلاین مجاز است.
  - نام کاربری نباید با آندرلاین شروع یا تمام شود.
  - کاراکترهای @، فاصله، خط تیره و حروف فارسی مجاز نیست.
- بعد از تایید نام کاربری، پیش فاکتور با گزینه های پرداخت، اعمال کد تخفیف و انصراف نمایش داده می شود.
- در پرداخت با Stars، فاکتور Telegram Stars با currency=XTR ارسال می شود.
- بعد از پرداخت موفق، سفارش ثبت و پرداخت Stars ذخیره می شود. ربات تلاش می کند کاربر را در پنل بسازد؛ اگر ساخت سرویس خطا بدهد، پرداخت ثبت می شود و اعلان برای ادمین ساخته می شود.

## تغییرات نسخه customer_services_v6

- همه اطلاعات حساس و قابل کپی مثل آیدی عددی کاربر، شماره سفارش، نام سرویس و لینک اشتراک تا حد ممکن داخل backtick نمایش داده می شوند.
- اگر کد تخفیف ۱۰۰ درصد باشد و مبلغ نهایی ۰ تومان شود، دیگر فاکتور ۱ استاری ساخته نمی شود و کاربر با دکمه دریافت رایگان سرویس را می گیرد.
- بعد از پرداخت موفق Stars یا سفارش رایگان، ربات تلاش می کند کاربر را در پنل بسازد یا اگر تمدید باشد حجم و زمان همان سرویس را اضافه کند.
- بخش پشتیبانی سمت مشتری اصلاح شد و کاربر می تواند تیکت ایجاد کند.
- بخش سرویس های من اضافه شد: اگر کاربر سرویس نداشته باشد پیام مناسب می گیرد، اگر داشته باشد لیست سرویس ها با دکمه شیشه ای نمایش داده می شود.
- داخل هر سرویس جزئیات مصرف، تاریخ انقضا، وضعیت، لینک اشتراک، کانفیگ ها، بروزرسانی، تغییر لینک، تمدید و انتقال سرویس وجود دارد.
- برای انتقال سرویس، کاربر مقصد باید آیدی عددی خود را از بخش کیف پول من کپی کند.

## تغییرات نسخه v7

- سیستم دعوت دوستان اضافه شد.
- ادمین می تواند مبلغ پاداش دعوت را به تومان تنظیم کند.
- ادمین می تواند شرط پاداش را روی «صرفا ورود با لینک» یا «بعد از پرداخت دعوت شده» بگذارد.
- ارسال اعلان ها از پنل قابل خاموش و روشن شدن است.
- بخش تنظیمات پیشرفته برای تغییر متن صفحات و متن دکمه ها اضافه شد.
- در پرداخت سرویس، اول موجودی کیف پول کاربر کم می شود و فقط مانده مبلغ به Stars تبدیل می شود.
- اگر کیف پول کل مبلغ را پوشش بدهد، کاربر بدون پرداخت Stars سرویس را دریافت می کند.
- کیف پول مشتری کامل تر شد و قابلیت شارژ دارد.
- شارژ کیف پول فعلا با Stars فعال است و گزینه های تتراپی و رمز ارز به صورت آماده برای اتصال بعدی قرار گرفته اند.
- سرویس تست اضافه شد.
- ادمین می تواند بسته تست را مشخص کند، سرویس تست را روشن و خاموش کند و امکان دریافت مجدد تست را برای همه کاربران ریست کند.



## لینک سابسکریپشن
اگر پنل فقط مسیر یا توکن ساب را برگرداند، مقدار زیر را در `.env` تنظیم کنید تا ربات خودش آدرس کامل لینک اشتراک را بسازد:

```env
SUBSCRIPTION_BASE_URL=https://example.com
```

اگر لینک شما قالب خاص دارد می توانید از `{username}` استفاده کنید، مثلا:

```env
SUBSCRIPTION_BASE_URL=https://example.com/sub/{username}
```

## HWID پیش فرض بسته ها
از این نسخه `hwid_limit` به صورت پیش فرض ارسال نمی شود تا دیفالت خود سرور اعمال شود. سوپرادمین می تواند از بخش تنظیمات مقدار `default_hwid_limit` را تنظیم کند. مقدار 0 یعنی دیفالت سرور.


## v14_payment_ready

این نسخه برای اضافه کردن روش های پرداخت جدید آماده شده است. موارد اضافه شده:

- موتور مشترک پرداخت در `modules/payment_engine.py`
- قفل ضد دوبار پردازش سفارش و پرداخت
- صف ساخت/تمدید سرویس بعد از پرداخت با retry
- جدول خطاهای سیستم و بخش `🚨 خطاهای سیستم`
- بخش `🩺 وضعیت سیستم` برای سلامت دیتابیس، صف ساخت، خطاها و بکاپ
- ضد اسپم callback و پیام های حساس
- کش ساده API برای لیست بسته ها و گروه ها
- بکاپ خودکار دیتابیس هر ۶ ساعت و نگهداری ۷ بکاپ آخر
- هشدار خودکار نزدیک پایان سرویس و مصرف بالای ۸۰ درصد
- تمدید خودکار قابل روشن و خاموش شدن برای هر سرویس
- فاکتور خرید کامل تر
- متن های متغیردار در تنظیمات پیشرفته
- وبهوک امن آماده برای تتراپی و رمز ارز با کنترل امضا و جلوگیری از پردازش تکراری
- پنل فروشنده و کمیسیون اولیه

موارد ۱۴، ۲۱ و ۲۳ طبق درخواست اضافه نشده اند.

## راه اندازی Callback تتراپی

ربات برای پرداخت تتراپی یک وب سرور داخلی روی `PAYMENT_WEBHOOK_HOST:PAYMENT_WEBHOOK_PORT` اجرا می کند. پیشنهاد امن این است که پورت فقط داخلی باشد:

```env
PAYMENT_WEBHOOK_HOST=127.0.0.1
PAYMENT_WEBHOOK_PORT=8081
TETRAPAY_CALLBACK_URL=https://example.com/payments/tetrapay/callback
TETRAPAY_CALLBACK_SECRET=
PAYMENT_CALLBACK_DOMAIN=example.com
```

برای نصب یا تنظیم Nginx:

```bash
sudo bash scripts/setup_payment_nginx.sh
```

بعد از اجرای ربات، تست کنید:

```bash
curl http://127.0.0.1:8081/health
curl https://example.com/health
```

درخواست ساخت فاکتور تتراپی از `.env` خوانده می شود:

```env
TETRAPAY_API_KEY=...
TETRAPAY_CREATE_INVOICE_URL=https://example.com
TETRAPAY_VERIFY_URL=
TETRAPAY_DEFAULT_EMAIL=customer@example.com
TETRAPAY_DEFAULT_MOBILE=09000000000
```

اگر از مشتری ایمیل یا موبایل گرفته نشود، مقدارهای `TETRAPAY_DEFAULT_EMAIL` و `TETRAPAY_DEFAULT_MOBILE` برای ساخت فاکتور ارسال می شوند. اگر تتراپی endpoint تایید پرداخت جدا دارد، آن را داخل `TETRAPAY_VERIFY_URL` بگذارید؛ اگر خالی باشد، ربات callback، Hash_id و مبلغ پرداخت را بررسی می کند.


## نصب خودکار Nginx برای Callback پرداخت

اگر وب سرور داخلی ربات با این دستور جواب می‌دهد:

```bash
curl http://127.0.0.1:8081/health
```

ولی دامنه Callback جواب نمی‌دهد یا 404 می‌دهد، اسکریپت زیر را اجرا کنید:

```bash
sudo bash scripts/setup_payment_nginx.sh
```

این اسکریپت از `.env` این مقادیر را می‌خواند:

```env
PAYMENT_CALLBACK_DOMAIN=example.com
PAYMENT_WEBHOOK_HOST=127.0.0.1
PAYMENT_WEBHOOK_PORT=8081
NGINX_PAYMENT_SITE_NAME=payment-callback
NGINX_PAYMENT_DISABLE_CONFLICTS=1
NGINX_PAYMENT_ENABLE_HTTPS=0
```

اگر Cloudflare روی حالت Flexible باشد، مقدار `NGINX_PAYMENT_ENABLE_HTTPS=0` کافی است و فقط پورت 80 روی Nginx تنظیم می‌شود. اگر Cloudflare روی Full یا Full strict باشد، باید certificate سمت سرور هم داشته باشید و این مقادیر را پر کنید:

```env
NGINX_PAYMENT_ENABLE_HTTPS=1
NGINX_PAYMENT_SSL_CERT=/etc/ssl/cloudflare/example.com.pem
NGINX_PAYMENT_SSL_KEY=/etc/ssl/cloudflare/example.com.key
```

برای عیب یابی سریع:

```bash
bash scripts/check_payment_callback.sh
```

اگر چند کانفیگ Nginx برای همین دامنه فعال باشد، با `NGINX_PAYMENT_DISABLE_CONFLICTS=1` اسکریپت کانفیگ‌های تکراری داخل `sites-enabled` و `conf.d` را به پوشه بکاپ منتقل می‌کند تا درخواست‌ها به vhost اشتباه نروند.

## پرداخت رمز ارز با Plisio

برای فعال کردن پرداخت رمز ارز، مقدارهای زیر را در `.env` تنظیم کنید:

```env
PLISIO_API_KEY=YOUR_PLISIO_SECRET_KEY
PLISIO_CALLBACK_URL=https://example.com/payments/plisio/callback
PLISIO_SUCCESS_URL=https://example.com/payments/plisio/success
PLISIO_FAIL_URL=https://example.com/payments/plisio/fail
PLISIO_DEFAULT_CURRENCY=USDT_TRX
PLISIO_ALLOWED_PSYS_CIDS=USDT_TRX,USDT_BSC,BTC,ETH,LTC,TRX
PLISIO_DEFAULT_EMAIL=customer@example.com
PLISIO_EXPIRE_MIN=60
PLISIO_MIN_USD_AMOUNT=1
```

قیمت بسته ها داخل ربات به تومان ذخیره می شود. هنگام پرداخت رمز ارز، آخرین نرخ دلار ذخیره شده از نوسان خوانده می شود، مبلغ تومان به USD تبدیل می شود و فاکتور Plisio با `source_currency=USD` و `source_amount` ساخته می شود.

مسیرهای وبهوک لازم در Nginx همان `/payments/` هستند و با اسکریپت `scripts/setup_payment_nginx.sh` هندل می شوند.

## Editable text + stickers
For welcome, maintenance, rules, support and after-purchase content, the admin editor now uses a two-step flow: send the text, optionally send one or more Telegram stickers, then send `/done`. Sticker file IDs are stored separately in `<setting>__stickers`, so existing text settings remain backward compatible.

## v32 - رنگ جز به جز و کمپین شارژ کیف پول
- تنظیمات پیشرفته > رنگ و ظاهر دکمه ها > تغییر رنگ جز به جز
- هر override جز به جز بر تنظیم کلی اولویت دارد؛ حالت «ارث بری» دوباره از رنگ کلی استفاده می کند.
- مدیریت کمپین از دکمه «🎯 کمپین ها» در پنل ادمین انجام می شود.
- کمپین شارژ کیف پول درصد بونوس، مدت به ساعت (0 = تا توقف دستی)، و دو قانون زمانی دارد:
  - payment_time: شروع درخواست پرداخت در زمان کمپین کافی است.
  - confirm_time: تایید نهایی پرداخت هم باید قبل از پایان/توقف کمپین باشد.
- سوپرادمین می تواند ادمین های مجاز مدیریت کمپین را انتخاب کند.

## Launch-ready install/update (v33)

For production updates, prefer the automated installer instead of manually restarting after copying files:

```bash
cd /home/koskhol/tel-bot/bu/admin_bot_modular
sudo bash scripts/install_or_update.sh
```

It performs a safe pre-update database backup, stops the service, updates the venv/dependencies, compiles the project, runs migrations/preflight, updates systemd/Nginx when applicable, and restarts the bot.

Manual preflight:

```bash
source venv/bin/activate
python scripts/preflight.py --migrate
```

Safe DB backup and restore:

```bash
python scripts/db_backup.py
sudo bash scripts/restore_backup.sh /path/to/backup.db
```

See `PRE_LAUNCH_AUDIT.md` for the full launch checklist and known limitations.

## Public repository safety

This repository is distributed without runtime databases, backups, exports, `.env`, virtual environments, API keys, tokens, passwords, or private deployment data. Copy `.env.example` to `.env` locally and provide your own values. All literal public domains in this public template are intentionally set to `example.com`; replace/configure them only in your local environment as needed and do not commit secrets.
