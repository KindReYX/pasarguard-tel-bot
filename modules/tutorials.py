from __future__ import annotations

from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import EditableButtonFilter
from keyboards import BTN_TUTORIALS, BTN_TUTORIALS_ADMIN, CustomerButtonFilter, back_keyboard
from permissions import deny_callback, deny_message, is_admin
from utils import answer_callback, edit_or_answer, message_text_with_custom_emoji_tokens, truncate_custom_emoji_text


tutorials_router = Router()

ENTRY_STICKER_KEY = 'tutorial_entry_sticker_file_id'


class TutorialState(StatesGroup):
    create_title = State()
    create_url = State()
    create_content = State()
    create_recommended = State()
    edit_title = State()
    edit_url = State()
    edit_text = State()
    edit_video = State()
    entry_sticker = State()


def _normalize_url(raw: str) -> str:
    value = str(raw or '').strip()
    if value.startswith('www.'):
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('لینک دانلود باید با http:// یا https:// شروع شود.')
    return value


def _short(value: str, length: int = 42) -> str:
    return truncate_custom_emoji_text(str(value or '').strip(), length)


def _content_label(row: dict) -> str:
    has_video = bool(str(row.get('video_file_id') or '').strip())
    has_text = bool(str(row.get('content_text') or '').strip())
    if has_video and has_text:
        return '🎬 فیلم + کپشن'
    if has_video:
        return '🎬 فیلم'
    if has_text:
        return '📝 توضیحات متنی'
    return '⚠️ بدون محتوا'


def admin_home_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text='➕ تعریف آموزش جدید', callback_data='tutorial_admin:create')],
        [InlineKeyboardButton(text='🎟 استیکر ورود بخش آموزش', callback_data='tutorial_admin:sticker')],
    ]
    tutorials = db.tutorial_list(include_inactive=True)
    for row in tutorials:
        status = '✅' if int(row.get('is_active') or 0) else '🚫'
        star = '🌟 ' if int(row.get('is_recommended') or 0) else ''
        rows.append([
            InlineKeyboardButton(
                text=f"{status} {star}{_short(row.get('title') or 'بدون نام')}",
                callback_data=f"tutorial_admin:detail:{row['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text='🔄 بروزرسانی', callback_data='tutorial_admin:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_detail_text(row: dict) -> str:
    text = str(row.get('content_text') or '').strip()
    preview = truncate_custom_emoji_text(text, 600, suffix='...')
    return (
        f"🎓 آموزش #{row['id']}\n\n"
        f"نام: {row.get('title') or '-'}\n"
        f"لینک دانلود: {row.get('download_url') or '-'}\n"
        f"محتوا: {_content_label(row)}\n"
        f"وضعیت: {'فعال ✅' if int(row.get('is_active') or 0) else 'غیرفعال 🚫'}\n"
        f"پیشنهادی: {'بله 🌟' if int(row.get('is_recommended') or 0) else 'خیر'}\n\n"
        f"متن/کپشن:\n{preview or 'ثبت نشده'}"
    )


def admin_detail_keyboard(row: dict) -> InlineKeyboardMarkup:
    tutorial_id = int(row['id'])
    active_text = '🚫 غیرفعال کردن' if int(row.get('is_active') or 0) else '✅ فعال کردن'
    rec_text = '☆ حذف از پیشنهادی' if int(row.get('is_recommended') or 0) else '🌟 انتخاب به عنوان پیشنهادی'
    rows = [
        [
            InlineKeyboardButton(text='✏️ نام', callback_data=f'tutorial_admin:edit_title:{tutorial_id}'),
            InlineKeyboardButton(text='🔗 لینک دانلود', callback_data=f'tutorial_admin:edit_url:{tutorial_id}'),
        ],
        [
            InlineKeyboardButton(text='📝 متن/کپشن', callback_data=f'tutorial_admin:edit_text:{tutorial_id}'),
            InlineKeyboardButton(text='🎬 فیلم', callback_data=f'tutorial_admin:edit_video:{tutorial_id}'),
        ],
        [InlineKeyboardButton(text=rec_text, callback_data=f'tutorial_admin:recommended:{tutorial_id}')],
        [InlineKeyboardButton(text=active_text, callback_data=f'tutorial_admin:toggle:{tutorial_id}')],
        [InlineKeyboardButton(text='🗑 حذف آموزش', callback_data=f'tutorial_admin:delete_ask:{tutorial_id}')],
        [InlineKeyboardButton(text='🔙 برگشت به آموزش‌ها', callback_data='tutorial_admin:home')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_tutorials_keyboard() -> InlineKeyboardMarkup | None:
    tutorials = db.tutorial_list(include_inactive=False)
    if not tutorials:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    recommended = next((row for row in tutorials if int(row.get('is_recommended') or 0)), None)
    if recommended:
        rows.append([
            InlineKeyboardButton(
                text=f"🌟 برنامه پیشنهادی: {_short(recommended.get('title') or 'آموزش', 50)} 🌟",
                callback_data=f"tutorial_user:open:{recommended['id']}",
                style='danger',
            )
        ])

    for index, row in enumerate(tutorials):
        tutorial_id = int(row['id'])
        title = _short(row.get('title') or 'آموزش')
        style = 'primary' if index % 2 == 0 else 'success'
        tutorial_label = f'🎓 آموزش {title}'
        if int(row.get('is_recommended') or 0):
            tutorial_label = f'⭐ آموزش {title} ⭐'
        # Telegram renders the first item on the left and the second on the right.
        # Therefore the tutorial action is intentionally the second button.
        rows.append([
            InlineKeyboardButton(text=f'⬇️ دانلود {title}', url=str(row.get('download_url') or ''), style=style),
            InlineKeyboardButton(text=tutorial_label, callback_data=f'tutorial_user:open:{tutorial_id}', style=style),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tutorial_open_keyboard(row: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⬇️ لینک دانلود {_short(row.get('title') or '', 30)}", url=str(row.get('download_url') or ''), style='success')],
        [InlineKeyboardButton(text='🔙 لیست آموزش‌ها', callback_data='tutorial_user:list')],
    ])


async def send_user_tutorial_list(message: Message, *, include_sticker: bool = True) -> None:
    tutorials = db.tutorial_list(include_inactive=False)
    if not tutorials:
        await message.answer('🎓 در حال حاضر آموزشی برای نمایش ثبت نشده است.')
        return

    sticker_file_id = str(db.get_setting(ENTRY_STICKER_KEY, '') or '').strip()
    if include_sticker and sticker_file_id:
        try:
            await message.answer_sticker(sticker=sticker_file_id)
        except Exception as exc:
            db.record_system_error(
                'tutorials.entry_sticker', str(exc), entity_type='setting', entity_id=ENTRY_STICKER_KEY,
                error_type=type(exc).__name__,
            )

    await message.answer(
        '🎓 بخش آموزش\n\nآموزش موردنظر را انتخاب کنید یا از دکمه دانلود همان ردیف استفاده کنید.',
        reply_markup=user_tutorials_keyboard(),
    )


@tutorials_router.message(CustomerButtonFilter('tutorials'))
async def tutorials_user_home(message: Message):
    await send_user_tutorial_list(message)


@tutorials_router.callback_query(F.data == 'tutorial_user:list')
async def tutorials_user_list_callback(callback: CallbackQuery):
    await answer_callback(callback)
    await send_user_tutorial_list(callback.message, include_sticker=False)


@tutorials_router.callback_query(F.data.startswith('tutorial_user:open:'))
async def tutorials_user_open(callback: CallbackQuery):
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row or not int(row.get('is_active') or 0):
        await answer_callback(callback, 'این آموزش در دسترس نیست.', show_alert=True)
        return

    text = str(row.get('content_text') or '').strip()
    video_file_id = str(row.get('video_file_id') or '').strip()
    markup = tutorial_open_keyboard(row)
    await answer_callback(callback)

    if video_file_id:
        caption = text or f"🎓 آموزش {row.get('title') or ''}"
        # Telegram video captions are limited. Admin flows already enforce this;
        # this fallback protects old/manual database values too.
        if len(caption) > 1024:
            caption = caption[:1021] + '...'
        try:
            await callback.message.answer_video(video=video_file_id, caption=caption, reply_markup=markup)
            return
        except Exception as exc:
            db.record_system_error(
                'tutorials.user_video', str(exc), user_id=callback.from_user.id,
                entity_type='tutorial', entity_id=str(tutorial_id), error_type=type(exc).__name__,
            )
            if text:
                await callback.message.answer(text, reply_markup=markup)
                return
            await callback.message.answer('ارسال فیلم این آموزش موقتاً ممکن نیست.', reply_markup=markup)
            return

    await callback.message.answer(text or 'محتوای این آموزش هنوز ثبت نشده است.', reply_markup=markup)


@tutorials_router.message(EditableButtonFilter(BTN_TUTORIALS_ADMIN))
async def tutorials_admin_home(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        await deny_message(message)
        return
    await state.clear()
    await message.answer('🎓 مدیریت آموزش‌ها', reply_markup=back_keyboard())
    await message.answer('از اینجا آموزش جدید تعریف کنید یا آموزش‌های فعلی را ویرایش کنید.', reply_markup=admin_home_keyboard())


@tutorials_router.callback_query(F.data == 'tutorial_admin:home')
async def tutorials_admin_home_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    await edit_or_answer(callback, '🎓 مدیریت آموزش‌ها', reply_markup=admin_home_keyboard())
    await answer_callback(callback)


@tutorials_router.callback_query(F.data == 'tutorial_admin:create')
async def tutorials_admin_create_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    await state.set_state(TutorialState.create_title)
    await edit_or_answer(
        callback,
        '➕ تعریف آموزش جدید\n\nمرحله ۱ از ۴: نام آموزش را ارسال کنید.\nمثال: Android / Hiddify',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='❌ لغو', callback_data='tutorial_admin:home')
        ]]),
    )
    await answer_callback(callback)


@tutorials_router.message(TutorialState.create_title)
async def tutorials_admin_create_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    title = message_text_with_custom_emoji_tokens(message).strip()
    if not title:
        await message.answer('نام آموزش نمی‌تواند خالی باشد.')
        return
    if len(title) > 80:
        await message.answer('نام آموزش حداکثر ۸۰ کاراکتر باشد.')
        return
    await state.update_data(tutorial_title=title)
    await state.set_state(TutorialState.create_url)
    await message.answer('مرحله ۲ از ۴: لینک دانلود را ارسال کنید.\nمثال: https://example.com/app')


@tutorials_router.message(TutorialState.create_url)
async def tutorials_admin_create_url(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    try:
        url = _normalize_url(message.text or '')
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(tutorial_url=url)
    await state.set_state(TutorialState.create_content)
    await message.answer(
        'مرحله ۳ از ۴: محتوای آموزش را بفرستید.\n\n'
        '• اگر آموزش متنی است، متن را ارسال کنید.\n'
        '• اگر فیلم آموزشی است، خود ویدیو را ارسال کنید؛ متن کپشن ویدیو هم به عنوان توضیحات ذخیره می‌شود.'
    )


@tutorials_router.message(TutorialState.create_content, F.video)
async def tutorials_admin_create_video(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    caption = message_text_with_custom_emoji_tokens(message).strip()
    if len(caption) > 1024:
        await message.answer('کپشن فیلم حداکثر ۱۰۲۴ کاراکتر باشد.')
        return
    await state.update_data(
        tutorial_video_file_id=message.video.file_id if message.video else '',
        tutorial_content_text=caption,
    )
    await state.set_state(TutorialState.create_recommended)
    await message.answer(
        'مرحله ۴ از ۴: این آموزش به عنوان «برنامه پیشنهادی» نمایش داده شود؟\n\n'
        'در هر لحظه فقط یک آموزش می‌تواند پیشنهادی باشد.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text='🌟 بله، پیشنهادی', callback_data='tutorial_admin:create_rec:yes', style='success'),
            InlineKeyboardButton(text='خیر', callback_data='tutorial_admin:create_rec:no'),
        ]]),
    )


@tutorials_router.message(TutorialState.create_content, F.text)
async def tutorials_admin_create_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    text = message_text_with_custom_emoji_tokens(message).strip()
    if not text:
        await message.answer('متن آموزش نمی‌تواند خالی باشد.')
        return
    if len(text) > 4096:
        await message.answer('متن آموزش حداکثر ۴۰۹۶ کاراکتر باشد.')
        return
    await state.update_data(tutorial_video_file_id='', tutorial_content_text=text)
    await state.set_state(TutorialState.create_recommended)
    await message.answer(
        'مرحله ۴ از ۴: این آموزش به عنوان «برنامه پیشنهادی» نمایش داده شود؟\n\n'
        'در هر لحظه فقط یک آموزش می‌تواند پیشنهادی باشد.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text='🌟 بله، پیشنهادی', callback_data='tutorial_admin:create_rec:yes', style='success'),
            InlineKeyboardButton(text='خیر', callback_data='tutorial_admin:create_rec:no'),
        ]]),
    )


@tutorials_router.message(TutorialState.create_content)
async def tutorials_admin_create_content_invalid(message: Message):
    await message.answer('در این مرحله فقط متن یا فایل ویدیویی تلگرام ارسال کنید.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:create_rec:'))
async def tutorials_admin_create_finish(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    data = await state.get_data()
    recommended = callback.data.endswith(':yes')
    title = str(data.get('tutorial_title') or '').strip()
    url = str(data.get('tutorial_url') or '').strip()
    if not title or not url:
        await state.clear()
        await answer_callback(callback, 'اطلاعات ساخت ناقص شد؛ دوباره ایجاد کنید.', show_alert=True)
        return
    tutorial_id = db.tutorial_create(
        title=title,
        download_url=url,
        content_text=str(data.get('tutorial_content_text') or ''),
        video_file_id=str(data.get('tutorial_video_file_id') or ''),
        is_recommended=recommended,
        created_by=callback.from_user.id,
    )
    db.add_log(callback.from_user.id, 'tutorial_created', 'tutorial', str(tutorial_id), {'recommended': recommended, 'title': title})
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await edit_or_answer(callback, '✅ آموزش ساخته شد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))
    await answer_callback(callback, 'آموزش ذخیره شد ✅')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:detail:'))
async def tutorials_admin_detail(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await state.clear()
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=admin_detail_keyboard(row))
    await answer_callback(callback)


async def _start_edit(callback: CallbackQuery, state: FSMContext, tutorial_id: int, field: str, prompt: str, target_state: State) -> None:
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    await state.set_state(target_state)
    await state.update_data(tutorial_edit_id=tutorial_id, tutorial_edit_field=field)
    await edit_or_answer(
        callback,
        prompt,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='❌ لغو', callback_data=f'tutorial_admin:detail:{tutorial_id}')
        ]]),
    )
    await answer_callback(callback)


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:edit_title:'))
async def tutorials_admin_edit_title_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    await _start_edit(callback, state, tutorial_id, 'title', '✏️ نام جدید آموزش را ارسال کنید.', TutorialState.edit_title)


@tutorials_router.message(TutorialState.edit_title)
async def tutorials_admin_edit_title_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    title = message_text_with_custom_emoji_tokens(message).strip()
    if not title or len(title) > 80:
        await message.answer('نام باید بین ۱ تا ۸۰ کاراکتر باشد.')
        return
    data = await state.get_data()
    tutorial_id = int(data.get('tutorial_edit_id') or 0)
    db.tutorial_update(tutorial_id, title=title)
    db.add_log(message.from_user.id, 'tutorial_title_updated', 'tutorial', str(tutorial_id), {'title': title})
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await message.answer('✅ نام آموزش تغییر کرد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:edit_url:'))
async def tutorials_admin_edit_url_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    await _start_edit(callback, state, tutorial_id, 'download_url', '🔗 لینک دانلود جدید را ارسال کنید.', TutorialState.edit_url)


@tutorials_router.message(TutorialState.edit_url)
async def tutorials_admin_edit_url_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    try:
        url = _normalize_url(message.text or '')
    except ValueError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    tutorial_id = int(data.get('tutorial_edit_id') or 0)
    db.tutorial_update(tutorial_id, download_url=url)
    db.add_log(message.from_user.id, 'tutorial_url_updated', 'tutorial', str(tutorial_id))
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await message.answer('✅ لینک دانلود تغییر کرد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:edit_text:'))
async def tutorials_admin_edit_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    limit_note = 'چون این آموزش فیلم دارد، کپشن حداکثر ۱۰۲۴ کاراکتر باشد.' if row and row.get('video_file_id') else 'حداکثر ۴۰۹۶ کاراکتر.'
    await _start_edit(
        callback, state, tutorial_id, 'content_text',
        f'📝 متن/کپشن جدید را ارسال کنید.\n{limit_note}\n\nبرای پاک کردن متن، فقط - بفرستید.',
        TutorialState.edit_text,
    )


@tutorials_router.message(TutorialState.edit_text, F.text)
async def tutorials_admin_edit_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    tutorial_id = int(data.get('tutorial_edit_id') or 0)
    row = db.tutorial_get(tutorial_id)
    if not row:
        await state.clear()
        await message.answer('آموزش پیدا نشد.')
        return
    text = message_text_with_custom_emoji_tokens(message).strip()
    if text == '-':
        text = ''
    if not text and not str(row.get('video_file_id') or '').strip():
        await message.answer('این آموزش فیلم ندارد؛ نمی‌توان متن را هم خالی کرد. اول فیلم اضافه کنید.')
        return
    max_len = 1024 if str(row.get('video_file_id') or '').strip() else 4096
    if len(text) > max_len:
        await message.answer(f'متن برای این آموزش حداکثر {max_len} کاراکتر باشد.')
        return
    db.tutorial_update(tutorial_id, content_text=text)
    db.add_log(message.from_user.id, 'tutorial_text_updated', 'tutorial', str(tutorial_id), {'length': len(text)})
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await message.answer('✅ متن/کپشن تغییر کرد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))


@tutorials_router.message(TutorialState.edit_text)
async def tutorials_admin_edit_text_invalid(message: Message):
    await message.answer('در این مرحله متن جدید را به صورت پیام متنی بفرستید.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:edit_video:'))
async def tutorials_admin_edit_video_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    await state.set_state(TutorialState.edit_video)
    await state.update_data(tutorial_edit_id=tutorial_id)
    buttons = []
    if str(row.get('video_file_id') or '').strip():
        buttons.append([InlineKeyboardButton(text='🗑 حذف فیلم فعلی', callback_data=f'tutorial_admin:remove_video:{tutorial_id}')])
    buttons.append([InlineKeyboardButton(text='❌ لغو', callback_data=f'tutorial_admin:detail:{tutorial_id}')])
    await edit_or_answer(
        callback,
        '🎬 ویدیوی جدید را ارسال کنید.\n\nمتن/کپشن فعلی دست‌نخورده می‌ماند و از گزینه «متن/کپشن» جداگانه قابل ویرایش است.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await answer_callback(callback)


@tutorials_router.message(TutorialState.edit_video, F.video)
async def tutorials_admin_edit_video_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    tutorial_id = int(data.get('tutorial_edit_id') or 0)
    row = db.tutorial_get(tutorial_id)
    if not row:
        await state.clear()
        await message.answer('آموزش پیدا نشد.')
        return
    existing_text = str(row.get('content_text') or '')
    if len(existing_text) > 1024:
        await message.answer('متن فعلی بیشتر از ۱۰۲۴ کاراکتر است و نمی‌تواند کپشن ویدیو شود. اول متن/کپشن را کوتاه کنید.')
        return
    db.tutorial_update(tutorial_id, video_file_id=message.video.file_id if message.video else '')
    db.add_log(message.from_user.id, 'tutorial_video_updated', 'tutorial', str(tutorial_id))
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await message.answer('✅ فیلم آموزش تغییر کرد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))


@tutorials_router.message(TutorialState.edit_video)
async def tutorials_admin_edit_video_invalid(message: Message):
    await message.answer('فقط فایل ویدیویی تلگرام را ارسال کنید.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:remove_video:'))
async def tutorials_admin_remove_video(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    if not str(row.get('content_text') or '').strip():
        await answer_callback(callback, 'این آموزش متن ندارد؛ اول متن اضافه کنید و بعد فیلم را حذف کنید.', show_alert=True)
        return
    db.tutorial_update(tutorial_id, video_file_id='')
    db.add_log(callback.from_user.id, 'tutorial_video_removed', 'tutorial', str(tutorial_id))
    await state.clear()
    row = db.tutorial_get(tutorial_id)
    await edit_or_answer(callback, '✅ فیلم حذف شد.\n\n' + admin_detail_text(row), reply_markup=admin_detail_keyboard(row))
    await answer_callback(callback, 'فیلم حذف شد.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:recommended:'))
async def tutorials_admin_recommended(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    new_value = not bool(int(row.get('is_recommended') or 0))
    db.tutorial_set_recommended(tutorial_id, new_value)
    db.add_log(callback.from_user.id, 'tutorial_recommended_changed', 'tutorial', str(tutorial_id), {'value': new_value})
    row = db.tutorial_get(tutorial_id)
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=admin_detail_keyboard(row))
    await answer_callback(callback, 'برنامه پیشنهادی تنظیم شد 🌟' if new_value else 'از حالت پیشنهادی خارج شد.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:toggle:'))
async def tutorials_admin_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    new_value = 0 if int(row.get('is_active') or 0) else 1
    db.tutorial_update(tutorial_id, is_active=new_value)
    db.add_log(callback.from_user.id, 'tutorial_active_changed', 'tutorial', str(tutorial_id), {'value': bool(new_value)})
    row = db.tutorial_get(tutorial_id)
    await edit_or_answer(callback, admin_detail_text(row), reply_markup=admin_detail_keyboard(row))
    await answer_callback(callback, 'وضعیت تغییر کرد.')


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:delete_ask:'))
async def tutorials_admin_delete_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    await edit_or_answer(
        callback,
        f"⚠️ آموزش «{row.get('title') or tutorial_id}» حذف شود؟\nاین کار قابل بازگشت نیست.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🗑 بله، حذف شود', callback_data=f'tutorial_admin:delete:{tutorial_id}', style='danger')],
            [InlineKeyboardButton(text='🔙 انصراف', callback_data=f'tutorial_admin:detail:{tutorial_id}')],
        ]),
    )
    await answer_callback(callback)


@tutorials_router.callback_query(F.data.startswith('tutorial_admin:delete:'))
async def tutorials_admin_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tutorial_id = int(callback.data.rsplit(':', 1)[1])
    row = db.tutorial_get(tutorial_id)
    if not row:
        await answer_callback(callback, 'آموزش پیدا نشد.', show_alert=True)
        return
    db.tutorial_delete(tutorial_id)
    db.add_log(callback.from_user.id, 'tutorial_deleted', 'tutorial', str(tutorial_id), {'title': row.get('title')})
    await edit_or_answer(callback, '✅ آموزش حذف شد.\n\n🎓 مدیریت آموزش‌ها', reply_markup=admin_home_keyboard())
    await answer_callback(callback, 'حذف شد.')


@tutorials_router.callback_query(F.data == 'tutorial_admin:sticker')
async def tutorials_admin_sticker_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    current = str(db.get_setting(ENTRY_STICKER_KEY, '') or '').strip()
    await state.set_state(TutorialState.entry_sticker)
    rows = []
    if current:
        rows.append([InlineKeyboardButton(text='🗑 حذف استیکر فعلی', callback_data='tutorial_admin:sticker_clear')])
    rows.append([InlineKeyboardButton(text='🔙 برگشت', callback_data='tutorial_admin:home')])
    await edit_or_answer(
        callback,
        '🎟 استیکر ورود بخش آموزش\n\n'
        f"وضعیت فعلی: {'ثبت شده ✅' if current else 'بدون استیکر'}\n\n"
        'استیکری که می‌خواهید هنگام ورود کاربر به بخش آموزش نمایش داده شود را همین حالا ارسال کنید.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await answer_callback(callback)


@tutorials_router.message(TutorialState.entry_sticker, F.sticker)
async def tutorials_admin_sticker_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    file_id = message.sticker.file_id if message.sticker else ''
    db.set_setting(ENTRY_STICKER_KEY, file_id)
    db.add_log(message.from_user.id, 'tutorial_entry_sticker_updated', 'setting', ENTRY_STICKER_KEY)
    await state.clear()
    await message.answer('✅ استیکر ورود بخش آموزش ذخیره شد.', reply_markup=back_keyboard())
    await message.answer('🎓 مدیریت آموزش‌ها', reply_markup=admin_home_keyboard())


@tutorials_router.message(TutorialState.entry_sticker)
async def tutorials_admin_sticker_invalid(message: Message):
    await message.answer('در این مرحله فقط استیکر تلگرام ارسال کنید.')


@tutorials_router.callback_query(F.data == 'tutorial_admin:sticker_clear')
async def tutorials_admin_sticker_clear(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    db.set_setting(ENTRY_STICKER_KEY, '')
    db.add_log(callback.from_user.id, 'tutorial_entry_sticker_cleared', 'setting', ENTRY_STICKER_KEY)
    await state.clear()
    await edit_or_answer(callback, '✅ استیکر ورود حذف شد.\n\n🎓 مدیریت آموزش‌ها', reply_markup=admin_home_keyboard())
    await answer_callback(callback, 'استیکر حذف شد.')
