from __future__ import annotations

import asyncio
import html
import json
import os
import re
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import settings
from permissions import deny_callback, is_super_admin
from version import __version__

updater_router = Router()
GITHUB_REPO = "KindReYX/pasarguard-tel-bot"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
LOOP_INTERVAL_SECONDS = 60
UPDATE_HELPER = "/usr/local/sbin/pasarguard-bot-update"
STATUS_FILE = Path(__file__).resolve().parents[1] / ".update_status.json"
PROGRESS_FILE = Path(__file__).resolve().parents[1] / ".update_progress.json"
_SEMVER_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def normalize_version(value: str) -> str:
    value = str(value or "").strip()
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return ".".join(match.groups())


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = normalize_version(value).split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def _super_admin_ids() -> list[int]:
    ids = set(int(x) for x in settings.super_admin_ids)
    try:
        rows = db.fetchall("SELECT telegram_id FROM bot_admins WHERE role='super_admin' AND is_active=1")
        ids.update(int(row["telegram_id"]) for row in rows)
    except Exception:
        pass
    return sorted(ids)


def _release_keyboard(tag: str, html_url: str = "") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"⬆️ بروزرسانی به {tag}", callback_data=f"ghupd:install:{tag}")]]
    if html_url.startswith("https://github.com/"):
        rows.append([InlineKeyboardButton(text="📝 مشاهده Release", url=html_url)])
    rows.append([InlineKeyboardButton(text="🔄 بررسی دوباره", callback_data="ghupd:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _safe_release_body(body: Any, limit: int = 1800) -> str:
    text = str(body or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return html.escape(text)


_PROGRESS_STEPS = {
    "starting": (5, "شروع کردیم؛ دارم همه‌چی رو آماده می‌کنم…"),
    "backup": (15, "اول یه بکاپ امن از دیتابیس می‌گیرم…"),
    "fetch": (30, "دارم نسخه جدید رو از GitHub می‌گیرم…"),
    "checkout": (45, "کد نسخه جدید رسید؛ دارم جاش می‌ذارم…"),
    "verify": (55, "نسخه رو چک می‌کنم که همون Release درست باشه…"),
    "dependencies": (70, "دارم وابستگی‌ها رو آپدیت می‌کنم…"),
    "compile": (82, "کدها رو یه دور بررسی می‌کنم که خطای واضح نداشته باشن…"),
    "migrate": (90, "دارم دیتابیس و تنظیمات لازم رو هماهنگ می‌کنم…"),
    "restart": (96, "تقریباً تمومه؛ ربات داره ری‌استارت می‌شه…"),
    "success": (100, "تموم شد؛ آپدیت با موفقیت نصب شد ✅"),
    "rollback": (92, "یه مشکلی پیش اومد؛ دارم نسخه قبلی رو برمی‌گردونم…"),
    "failed": (100, "آپدیت کامل نشد و نسخه قبلی برگردونده شد ❌"),
}


def _progress_text(version: str, step: str, percent: int | None = None, detail: str = "") -> str:
    default_percent, message = _PROGRESS_STEPS.get(step, (percent or 0, "دارم آپدیت رو انجام می‌دم…"))
    pct = max(0, min(100, int(percent if percent is not None else default_percent)))
    filled = min(10, pct // 10)
    bar = "█" * filled + "░" * (10 - filled)
    text = (
        f"⬆️ <b>آپدیت ربات به نسخه {html.escape(version)}</b>\n\n"
        f"{message}\n\n"
        f"<code>{bar}</code>  <b>{pct}%</b>"
    )
    if detail:
        text += f"\n\n<code>{html.escape(detail[:600])}</code>"
    return text


def _read_progress_file() -> dict[str, Any] | None:
    try:
        if not PROGRESS_FILE.exists():
            return None
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def _edit_progress_message(bot: Bot, chat_id: int, message_id: int, version: str, step: str, percent: int | None = None, detail: str = "") -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=_progress_text(version, step, percent, detail),
            parse_mode="HTML",
        )
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            pass


async def monitor_update_progress(bot: Bot, chat_id: int, message_id: int, version: str) -> None:
    last_key = ""
    for _ in range(300):
        data = _read_progress_file()
        if data and normalize_version(str(data.get("tag") or version)) == normalize_version(version):
            step = str(data.get("step") or "starting")
            percent = int(data.get("percent") or _PROGRESS_STEPS.get(step, (0, ""))[0])
            detail = str(data.get("detail") or "")
            key = f"{step}:{percent}:{detail}"
            if key != last_key:
                await _edit_progress_message(bot, chat_id, message_id, version, step, percent, detail)
                last_key = key
            if step in {"success", "failed"}:
                return
        await asyncio.sleep(1)

async def fetch_latest_release() -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"pasarguard-tel-bot/{__version__}",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(GITHUB_API_URL) as response:
            if response.status == 404:
                raise RuntimeError("GitHub Release پیدا نشد. Repository باید عمومی باشد و حداقل یک Release منتشرشده داشته باشد.")
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"GitHub API error {response.status}: {text[:300]}")
            data = await response.json()
    normalize_version(str(data.get("tag_name") or ""))
    return data


async def notify_release(bot: Bot, release: dict[str, Any], *, force: bool = False) -> None:
    tag = str(release.get("tag_name") or "").strip()
    if not is_newer_version(tag):
        return
    normalized = normalize_version(tag)
    if not force and str(db.get_setting("github_update_last_notified_version", "") or "") == normalized:
        return
    name = html.escape(str(release.get("name") or tag))
    body = _safe_release_body(release.get("body"))
    url = str(release.get("html_url") or "")
    text = (
        "🚀 <b>نسخه جدید ربات منتشر شده</b>\n\n"
        f"نسخه فعلی: <code>{html.escape(__version__)}</code>\n"
        f"نسخه جدید: <code>{html.escape(normalized)}</code>\n"
        f"Release: <b>{name}</b>"
    )
    if body:
        text += f"\n\n<b>تغییرات:</b>\n{body}"
    sent = False
    for admin_id in _super_admin_ids():
        try:
            await bot.send_message(admin_id, text, reply_markup=_release_keyboard(tag, url), parse_mode="HTML")
            sent = True
        except Exception as exc:
            db.record_system_error("updater.notify_release", str(exc), user_id=admin_id, error_type=type(exc).__name__)
    if sent:
        db.set_setting("github_update_last_notified_version", normalized)


def _read_status_file() -> dict[str, Any] | None:
    try:
        if not STATUS_FILE.exists():
            return None
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        STATUS_FILE.unlink(missing_ok=True)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def announce_update_result(bot: Bot) -> None:
    status = _read_status_file()
    if not status:
        return
    state = str(status.get("status") or "")
    tag = str(status.get("tag") or "")
    detail = str(status.get("detail") or "").strip()
    db.set_setting("github_update_in_progress_version", "")
    normalized_tag = normalize_version(tag) if tag else __version__
    target_chat = int(db.get_setting("github_update_progress_chat_id", 0) or 0)
    target_message = int(db.get_setting("github_update_progress_message_id", 0) or 0)
    target_version = str(db.get_setting("github_update_progress_version", "") or "")
    if state == "success":
        text = _progress_text(normalized_tag, "success", 100)
    else:
        text = _progress_text(normalized_tag, "failed", 100, detail)
    edited_target = False
    if target_chat and target_message and (not target_version or target_version == normalized_tag):
        try:
            await bot.edit_message_text(chat_id=target_chat, message_id=target_message, text=text, parse_mode="HTML")
            edited_target = True
        except Exception:
            pass
    for admin_id in _super_admin_ids():
        if edited_target and admin_id == target_chat:
            continue
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass
    db.set_setting("github_update_progress_chat_id", 0)
    db.set_setting("github_update_progress_message_id", 0)
    db.set_setting("github_update_progress_version", "")


async def trigger_update(tag: str, *, actor_id: int | None = None, automatic: bool = False) -> None:
    normalized = normalize_version(tag)
    if not is_newer_version(normalized):
        raise RuntimeError("این نسخه جدیدتر از نسخه نصب‌شده نیست.")
    pending = str(db.get_setting("github_update_in_progress_version", "") or "")
    if pending == normalized:
        raise RuntimeError("بروزرسانی این نسخه قبلا شروع شده است.")
    db.set_setting("github_update_in_progress_version", normalized)
    db.set_setting("github_update_started_at", db.now_iso())
    db.set_setting("github_update_started_by", "auto" if automatic else int(actor_id or 0))
    tag_arg = f"v{normalized}"
    cmd = [UPDATE_HELPER, tag_arg] if os.geteuid() == 0 else ["sudo", "-n", UPDATE_HELPER, tag_arg]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except Exception:
        db.set_setting("github_update_in_progress_version", "")
        raise
    if proc.returncode != 0:
        db.set_setting("github_update_in_progress_version", "")
        detail = (stderr or stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(detail or "اجرای helper بروزرسانی ناموفق بود.")


async def check_for_updates(bot: Bot, *, force_notify: bool = False, allow_auto: bool = True) -> dict[str, Any]:
    release = await fetch_latest_release()
    tag = str(release.get("tag_name") or "")
    normalized = normalize_version(tag)
    db.set_setting("github_update_last_check_at", db.now_iso())
    db.set_setting("github_update_latest_version", normalized)
    db.set_setting("github_update_latest_url", str(release.get("html_url") or ""))
    if is_newer_version(normalized):
        if allow_auto and bool(db.get_setting("github_auto_update_enabled", False)):
            await notify_release(bot, release, force=force_notify)
            try:
                await trigger_update(normalized, automatic=True)
            except RuntimeError as exc:
                if "قبلا شروع" in str(exc):
                    return release
                raise
            for admin_id in _super_admin_ids():
                try:
                    await bot.send_message(
                        admin_id,
                        f"🤖 آپدیت خودکار به <code>{html.escape(normalized)}</code> شروع شد.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        else:
            await notify_release(bot, release, force=force_notify)
    return release


def _check_due() -> bool:
    raw = str(db.get_setting("github_update_last_check_at", "") or "")
    if not raw:
        return True
    try:
        from datetime import datetime, timezone

        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() >= CHECK_INTERVAL_SECONDS
    except Exception:
        return True


async def github_update_loop(bot: Bot) -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await announce_update_result(bot)
            if bool(db.get_setting("github_auto_check_enabled", True)) and _check_due():
                await check_for_updates(bot)
        except Exception as exc:
            db.set_setting("github_update_last_error", str(exc))
            db.record_system_error("tasks.github_update_loop", str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(LOOP_INTERVAL_SECONDS)


@updater_router.callback_query(F.data == "ghupd:check")
async def manual_check(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    await callback.answer("در حال بررسی…")
    try:
        release = await fetch_latest_release()
        tag = str(release.get("tag_name") or "")
        normalized = normalize_version(tag)
        db.set_setting("github_update_last_check_at", db.now_iso())
        db.set_setting("github_update_latest_version", normalized)
        if is_newer_version(normalized):
            await callback.message.answer(
                f"🚀 نسخه جدید موجود است.\nنسخه فعلی: <code>{html.escape(__version__)}</code>\nنسخه جدید: <code>{html.escape(normalized)}</code>",
                reply_markup=_release_keyboard(tag, str(release.get("html_url") or "")),
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(
                f"✅ آخرین نسخه نصب است: <code>{html.escape(__version__)}</code>",
                parse_mode="HTML",
            )
    except Exception as exc:
        await callback.message.answer(
            f"❌ بررسی آپدیت ناموفق بود:\n<code>{html.escape(str(exc)[:1200])}</code>",
            parse_mode="HTML",
        )


@updater_router.callback_query(F.data.startswith("ghupd:install:"))
async def manual_install(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await deny_callback(callback)
        return
    tag = callback.data.split(":", 2)[2]
    try:
        normalized = normalize_version(tag)
    except ValueError:
        await callback.answer("نسخه نامعتبر است.", show_alert=True)
        return
    await callback.answer("شروع شد 👌", show_alert=False)
    progress_message = await callback.message.answer(
        _progress_text(normalized, "starting", 5),
        parse_mode="HTML",
    )
    db.set_setting("github_update_progress_chat_id", int(progress_message.chat.id))
    db.set_setting("github_update_progress_message_id", int(progress_message.message_id))
    db.set_setting("github_update_progress_version", normalized)
    try:
        await trigger_update(normalized, actor_id=callback.from_user.id)
        asyncio.create_task(
            monitor_update_progress(callback.bot, progress_message.chat.id, progress_message.message_id, normalized)
        )
    except Exception as exc:
        db.record_system_error(
            "updater.manual_install",
            str(exc),
            user_id=callback.from_user.id,
            error_type=type(exc).__name__,
        )
        db.set_setting("github_update_progress_chat_id", 0)
        db.set_setting("github_update_progress_message_id", 0)
        db.set_setting("github_update_progress_version", "")
        await _edit_progress_message(
            callback.bot, progress_message.chat.id, progress_message.message_id, normalized, "failed", 100,
            "شروع آپدیت نشد؛ اگر خطا مربوط به sudo/helper است، یک بار scripts/install_or_update.sh را با sudo اجرا کنید. " + str(exc)[:500],
        )
