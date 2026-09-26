# Telegram Admin Bot Modular

این پروژه یک ربات ادمین ماژولار برای مدیریت کاربران، سرویس‌ها، سفارش‌ها، پرداخت‌ها، کیف پول، تیکت‌ها، اعلان‌ها و تنظیمات مدیریتی است.

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

### نصب مستقیم از GitHub

برای نصب پروژه از ریپازیتوری:

```bash
git clone https://github.com/KindReYX/pasarguard-tel-bot.git
cd pasarguard-tel-bot
```

فایل تنظیمات را بسازید:

```bash
cp .env.example .env
nano .env
```

مقادیر موردنیاز مثل Bot Token، آدرس پنل و اطلاعات API را داخل `.env` وارد کنید.

### نصب خودکار روی Linux

اگر پروژه را روی سرور Linux اجرا می‌کنید، بعد از تنظیم `.env` می‌توانید از اسکریپت نصب خودکار استفاده کنید:

```bash
sudo bash scripts/install_or_update.sh
```

این اسکریپت وابستگی‌ها، محیط Python و تنظیمات لازم برای اجرای پروژه را آماده می‌کند و در صورت پشتیبانی محیط، سرویس systemd را نیز راه‌اندازی می‌کند.

### نصب دستی

در صورت نیاز به نصب دستی:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

برای بروزرسانی نسخه نصب‌شده از GitHub:

```bash
cd pasarguard-tel-bot
git pull
sudo bash scripts/install_or_update.sh
```

## API تمپلیت‌ها و گروه‌ها

ربات روی API تمپلیت‌ها و گروه‌ها کار می‌کند:

- `GET /api/user_templates/simple?all=true`
- `GET /api/user_template/{template_id}`
- `POST /api/user_template`
- `PUT /api/user_template/{template_id}`
- `DELETE /api/user_template/{template_id}`
- `GET /api/groups/simple?all=true`

## Telegram Stars

کاربر پرداخت را با `currency=XTR` به ربات انجام می‌دهد و موجودی Stars در بالانس خود ربات قرار می‌گیرد.

در Bot API متدهای زیر برای مدیریت Stars قابل استفاده هستند:

- `getMyStarBalance`
- `getStarTransactions`
- `refundStarPayment`

برداشت درآمد Stars به حساب شخصی از داخل Bot API انجام نمی‌شود و باید از مسیرهای رسمی Telegram / Fragment یا پنل مالک ربات انجام شود.

## جوین اجباری

برای کانال عمومی:

```text
@channel_username
```

برای کانال یا گروه خصوصی:

```text
-1001234567890 | نام کانال | https://example.com/+invite
```

برای اینکه ربات بتواند عضویت کاربر را بررسی کند، ربات باید داخل کانال یا گروه موردنظر عضو باشد و بهتر است دسترسی ادمین داشته باشد.

## لینک سابسکریپشن

اگر پنل فقط مسیر یا توکن ساب را برگرداند، مقدار زیر را در `.env` تنظیم کنید تا ربات آدرس کامل لینک اشتراک را بسازد:

```env
SUBSCRIPTION_BASE_URL=https://example.com
```

اگر لینک شما قالب خاصی دارد می‌توانید از `{username}` استفاده کنید:

```env
SUBSCRIPTION_BASE_URL=https://example.com/sub/{username}
```

## HWID پیش‌فرض بسته‌ها

`hwid_limit` به‌صورت پیش‌فرض ارسال نمی‌شود تا مقدار پیش‌فرض خود سرور اعمال شود.

سوپرادمین می‌تواند از بخش تنظیمات مقدار `default_hwid_limit` را تعیین کند.

مقدار `0` یعنی استفاده از مقدار پیش‌فرض سرور.

## راه‌اندازی Callback پرداخت

ربات برای پرداخت، یک وب‌سرور داخلی روی `PAYMENT_WEBHOOK_HOST:PAYMENT_WEBHOOK_PORT` اجرا می‌کند.

پیشنهاد امن این است که پورت فقط به‌صورت داخلی در دسترس باشد:

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

بعد از اجرای ربات می‌توانید سلامت سرویس را تست کنید:

```bash
curl http://127.0.0.1:8081/health
curl https://example.com/health
```

## تنظیمات TetraPay

درخواست ساخت فاکتور از `.env` خوانده می‌شود:

```env
TETRAPAY_API_KEY=
TETRAPAY_CREATE_INVOICE_URL=https://example.com
TETRAPAY_VERIFY_URL=
TETRAPAY_DEFAULT_EMAIL=customer@example.com
TETRAPAY_DEFAULT_MOBILE=09000000000
```

اگر از مشتری ایمیل یا موبایل گرفته نشود، مقدارهای `TETRAPAY_DEFAULT_EMAIL` و `TETRAPAY_DEFAULT_MOBILE` برای ساخت فاکتور ارسال می‌شوند.

اگر سرویس پرداخت endpoint جداگانه‌ای برای تأیید پرداخت دارد، آن را داخل `TETRAPAY_VERIFY_URL` قرار دهید. اگر خالی باشد، ربات callback، `Hash_id` و مبلغ پرداخت را بررسی می‌کند.

## نصب خودکار Nginx برای Callback پرداخت

اگر وب‌سرور داخلی ربات با این دستور پاسخ می‌دهد:

```bash
curl http://127.0.0.1:8081/health
```

ولی دامنه Callback پاسخ نمی‌دهد یا خطای 404 دریافت می‌کنید، اسکریپت زیر را اجرا کنید:

```bash
sudo bash scripts/setup_payment_nginx.sh
```

این اسکریپت از `.env` مقادیر زیر را می‌خواند:

```env
PAYMENT_CALLBACK_DOMAIN=example.com
PAYMENT_WEBHOOK_HOST=127.0.0.1
PAYMENT_WEBHOOK_PORT=8081
NGINX_PAYMENT_SITE_NAME=payment-callback
NGINX_PAYMENT_DISABLE_CONFLICTS=1
NGINX_PAYMENT_ENABLE_HTTPS=0
```

اگر Cloudflare روی حالت Flexible باشد:

```env
NGINX_PAYMENT_ENABLE_HTTPS=0
```

اگر Cloudflare روی Full یا Full Strict باشد، باید certificate سمت سرور هم تنظیم شود:

```env
NGINX_PAYMENT_ENABLE_HTTPS=1
NGINX_PAYMENT_SSL_CERT=/etc/ssl/cloudflare/example.com.pem
NGINX_PAYMENT_SSL_KEY=/etc/ssl/cloudflare/example.com.key
```

برای عیب‌یابی سریع:

```bash
bash scripts/check_payment_callback.sh
```

اگر چند کانفیگ Nginx برای یک دامنه فعال باشد، با:

```env
NGINX_PAYMENT_DISABLE_CONFLICTS=1
```

اسکریپت کانفیگ‌های تکراری داخل `sites-enabled` و `conf.d` را به پوشه بکاپ منتقل می‌کند تا درخواست‌ها به vhost اشتباه نروند.

## پرداخت رمز ارز با Plisio

برای فعال کردن پرداخت رمز ارز، مقادیر زیر را در `.env` تنظیم کنید:

```env
PLISIO_API_KEY=
PLISIO_CALLBACK_URL=https://example.com/payments/plisio/callback
PLISIO_SUCCESS_URL=https://example.com/payments/plisio/success
PLISIO_FAIL_URL=https://example.com/payments/plisio/fail
PLISIO_DEFAULT_CURRENCY=USDT_TRX
PLISIO_ALLOWED_PSYS_CIDS=USDT_TRX,USDT_BSC,BTC,ETH,LTC,TRX
PLISIO_DEFAULT_EMAIL=customer@example.com
PLISIO_EXPIRE_MIN=60
PLISIO_MIN_USD_AMOUNT=1
```

قیمت بسته‌ها داخل ربات به تومان ذخیره می‌شود. هنگام پرداخت رمز ارز، نرخ دلار ذخیره‌شده خوانده می‌شود، مبلغ تومان به USD تبدیل می‌شود و فاکتور با `source_currency=USD` و `source_amount` ساخته می‌شود.

مسیرهای وبهوک لازم در Nginx همان `/payments/` هستند و با اسکریپت زیر مدیریت می‌شوند:

```bash
sudo bash scripts/setup_payment_nginx.sh
```

## متن‌های قابل ویرایش و استیکرها

برای محتوای خوش‌آمدگویی، تعمیرات، قوانین، پشتیبانی و پیام بعد از خرید، ادمین می‌تواند متن را ارسال کند و در صورت نیاز یک یا چند استیکر Telegram هم اضافه کند.

برای پایان ثبت محتوا:

```text
/done
```

شناسه فایل استیکرها جداگانه در `<setting>__stickers` ذخیره می‌شود تا تنظیمات متنی قبلی سازگار باقی بمانند.

## نصب و بروزرسانی

برای نصب یا بروزرسانی در محیط production می‌توانید از اسکریپت خودکار استفاده کنید:

```bash
sudo bash scripts/install_or_update.sh
```

این اسکریپت کارهای زیر را انجام می‌دهد:

- تهیه بکاپ امن از دیتابیس قبل از بروزرسانی
- توقف سرویس
- بروزرسانی venv و dependencyها
- compile پروژه
- اجرای migration و preflight
- بروزرسانی systemd و Nginx در صورت نیاز
- راه‌اندازی مجدد ربات

اجرای دستی preflight:

```bash
source venv/bin/activate
python scripts/preflight.py --migrate
```

بکاپ و بازیابی دیتابیس:

```bash
python scripts/db_backup.py
sudo bash scripts/restore_backup.sh /path/to/backup.db
```

برای چک‌لیست کامل راه‌اندازی و محدودیت‌های شناخته‌شده، فایل `PRE_LAUNCH_AUDIT.md` را بررسی کنید.

## امنیت Repository عمومی

این Repository نباید شامل موارد زیر باشد:

- دیتابیس runtime
- فایل‌های backup
- exportها
- فایل `.env`
- virtual environment
- API key
- Bot token
- password
- اطلاعات خصوصی deployment

فایل `.env.example` را به `.env` کپی کنید و مقادیر شخصی خودتان را فقط روی سرور یا محیط محلی وارد کنید:

```bash
cp .env.example .env
```

---

Made with ❤️ and AI
