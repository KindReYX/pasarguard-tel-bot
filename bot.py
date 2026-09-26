from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import db
from config import settings, validate_settings
from keyboards import EditableButtonFilter
from keyboards import BTN_BACK, main_admin_keyboard, customer_keyboard
from permissions import ROLE_LABELS, get_role, is_staff
from utils import answer_template, user_template_context, install_button_style_patch, install_edit_mode_output_patch

from modules.dashboard import dashboard_router
from modules.users import users_router
from modules.orders import orders_router
from modules.reports import reports_router
from modules.packs import packs_router
from modules.coupons import coupons_router
from modules.wallet import wallet_router
from modules.stars import stars_router
from modules.logs import logs_router
from modules.texts import texts_router
from modules.broadcast import broadcast_router
from modules.tickets import tickets_router
from modules.backups import backups_router
from modules.exports import exports_router
from modules.settings import settings_router
from modules.admins import admins_router
from modules.notifications import notifications_router
from modules.join import RequiredJoinMiddleware, ensure_force_join, join_router
from modules.customer import customer_router
from modules.referral import referral_router
from modules.advanced import advanced_router
from modules.test_service import test_service_router
from modules.system import system_router
from modules.card_transfer import card_transfer_router
from modules.campaigns import campaigns_router
from modules.security import ThrottleMiddleware, BlockedUserMiddleware
from modules.tutorials import tutorials_router
from modules.ad_tracking import ad_tracking_router
from modules.edit_mode import edit_router
from modules import edit_runtime
from modules.updater import updater_router


async def send_admin_home(message: Message) -> None:
    role = get_role(message.from_user.id if message.from_user else None)
    await message.answer(
        f'پنل مدیریت\nنقش شما: {ROLE_LABELS.get(role, role)}',
        reply_markup=main_admin_keyboard(role),
    )


async def start_handler(message: Message, bot: Bot):
    user = message.from_user
    if user:
        db.upsert_user(user.id, user.username, user.first_name)
        parts = (message.text or '').split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith('seller_'):
            seller_raw = parts[1].replace('seller_', '', 1).strip()
            if seller_raw.isdigit() and int(seller_raw) != user.id:
                seller_id = int(seller_raw)
                seller = db.fetchone("SELECT telegram_id FROM bot_admins WHERE telegram_id=? AND role='seller' AND is_active=1", (seller_id,))
                current = db.fetchone('SELECT owner_admin_id FROM bot_users WHERE telegram_id=?', (user.id,))
                if seller and current and not current.get('owner_admin_id'):
                    db.execute('UPDATE bot_users SET owner_admin_id=?, updated_at=? WHERE telegram_id=?', (seller_id, db.now_iso(), user.id))
        if len(parts) > 1 and parts[1].startswith('ref_'):
            ref_id = parts[1].replace('ref_', '', 1).strip()
            if ref_id.isdigit():
                created = db.create_referral(int(ref_id), user.id)
                if created and db.get_setting('referral_condition', 'payment') == 'join':
                    db.process_referral_reward(user.id, reason='join')
        if len(parts) > 1 and parts[1].lower().startswith('ad_'):
            # Case-insensitive ad attribution; keep original suffix case then normalize in db
            payload = parts[1].strip()
            # Remove prefix case-insensitively
            if payload[:3].lower() == 'ad_':
                slug = payload[3:].strip()
            else:
                slug = payload.replace('ad_', '', 1).replace('AD_', '', 1).strip()
            if slug:
                try:
                    db.record_ad_visit(slug, user.id)
                except Exception:
                    # Never let ad tracking break /start flow
                    try:
                        db.record_system_error('bot.start_ad_visit', f'slug={slug}', user_id=user.id, error_type='ad_visit_error')
                    except Exception:
                        pass
    if db.get_setting('maintenance_mode', False) and not is_staff(user.id if user else None):
        support = db.get_setting('support_username', settings.support_username)
        await answer_template(message, db.get_setting('maintenance_text', 'ربات در حال بروزرسانی است.'), user_template_context(user, support=support), content_key='maintenance_text')
        return
    if is_staff(user.id if user else None):
        await send_admin_home(message)
        return
    if not await ensure_force_join(message, bot):
        return
    support = db.get_setting('support_username', settings.support_username)
    await answer_template(
        message,
        db.get_setting('welcome_text', 'سلام {name}! به ربات خوش آمدید.'),
        user_template_context(user, support=support),
        reply_markup=customer_keyboard(),
        content_key='welcome_text',
    )


async def admin_handler(message: Message):
    if not is_staff(message.from_user.id if message.from_user else None):
        await message.answer('شما دسترسی پنل مدیریت ندارید.')
        return
    await send_admin_home(message)


async def back_handler(message: Message, state: FSMContext):
    uid = message.from_user.id if message.from_user else None
    if not is_staff(uid):
        return
    # Root-level back handler is registered before child routers. In live edit mode
    # it must behave like every other selected button instead of escaping the editor.
    if edit_runtime.is_active(uid):
        replaying = bool(uid and edit_runtime.consume_message_bypass(int(uid), int(message.message_id)))
        if not replaying:
            try:
                from modules.edit_mode import resolve_reply_target, show_button_editor
                session = edit_runtime.get_session(uid)
                target = resolve_reply_target(message, session.scope) if session else None
                if target:
                    await show_button_editor(message, target)
                    return
            except Exception:
                pass
    if state is not None:
        try:
            await state.clear()
        except Exception:
            pass
    await send_admin_home(message)


async def global_error_handler(event) -> bool:
    exception = event.exception
    update = getattr(event, 'update', None)
    callback: CallbackQuery | None = getattr(update, 'callback_query', None) if update else None
    message = str(exception).lower()

    if isinstance(exception, TelegramBadRequest) and 'message is not modified' in message:
        if callback:
            try:
                await callback.answer('همین صفحه الان نمایش داده شده است.')
            except Exception:
                pass
        return True

    logging.error('Unhandled update error', exc_info=(type(exception), exception, exception.__traceback__))
    try:
        db.record_system_error('bot.global_error_handler', str(exception), error_type=type(exception).__name__)
    except Exception:
        pass

    if callback:
        try:
            await callback.answer('خطای موقت. دوباره تلاش کنید.', show_alert=False)
        except Exception:
            pass
    return True


async def main() -> None:
    validate_settings()
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    db.seed_roles_from_env()
    install_button_style_patch()
    install_edit_mode_output_patch()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    edit_runtime.set_dispatcher(dp)

    dp.errors.register(global_error_handler)
    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())
    dp.message.middleware(edit_runtime.EditModeRenderMiddleware())
    dp.callback_query.middleware(edit_runtime.EditModeRenderMiddleware())
    # Enforce customer blacklist before join checks and business handlers.
    dp.message.middleware(BlockedUserMiddleware())
    dp.callback_query.middleware(BlockedUserMiddleware())
    dp.message.middleware(RequiredJoinMiddleware())
    dp.callback_query.middleware(RequiredJoinMiddleware())

    dp.message.register(start_handler, CommandStart())
    dp.message.register(admin_handler, Command('admin'))
    dp.message.register(back_handler, EditableButtonFilter(BTN_BACK))

    for router in [
        # Must be first: while edit mode is active it intercepts real buttons,
        # then re-feeds the original update only when admin chooses "perform action".
        edit_router,
        dashboard_router,
        users_router,
        orders_router,
        reports_router,
        packs_router,
        coupons_router,
        wallet_router,
        customer_router,
        card_transfer_router,
        campaigns_router,
        referral_router,
        tutorials_router,
        ad_tracking_router,
        advanced_router,
        updater_router,
        test_service_router,
        stars_router,
        logs_router,
        texts_router,
        broadcast_router,
        tickets_router,
        backups_router,
        exports_router,
        settings_router,
        admins_router,
        notifications_router,
        system_router,
        join_router,
    ]:
        dp.include_router(router)

    try:
        from modules.tasks import start_background_tasks
        start_background_tasks(bot)
    except Exception:
        logging.exception('Could not start background tasks')

    try:
        from modules.payment_webhook import start_payment_webhook_server
        await start_payment_webhook_server(bot)
    except Exception:
        logging.exception('Could not start payment webhook server')
        raise

    try:
        await dp.start_polling(bot)
    finally:
        # Cleanup shared keep-alive session to free sockets / reduce RAM on restart
        try:
            from api_client import close_shared_session
            await close_shared_session()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == '__main__':
    asyncio.run(main())
