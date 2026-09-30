from __future__ import annotations

import json
import html
import sqlite3
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from config import settings

# --- In-memory caches for hot paths (settings, roles) ---
_SETTINGS_CACHE: dict[str, tuple[Any, float]] = {}
_SETTINGS_CACHE_TTL = 10.0  # seconds
_ROLE_CACHE: dict[int, tuple[str, float]] = {}
_ROLE_CACHE_TTL = 30.0


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds')


def now_iso_dt() -> datetime:
    return datetime.utcnow()


class _ClosingConnection(sqlite3.Connection):
    """sqlite connection whose context manager also closes the file handle.

    sqlite3.Connection.__exit__ only commits/rolls back; it does *not* close the
    connection. Most of this project intentionally uses ``with connect()`` for
    short-lived queries, so leaving the default behavior leaks one connection
    (and WAL/file descriptors) per DB operation until the process exhausts its
    open-file limit.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    db_path = Path(settings.db_path).expanduser()
    if not db_path.is_absolute():
        # Resolve relative DB_PATH once against the process working directory.
        # Production systemd uses the project directory as WorkingDirectory.
        db_path = (Path.cwd() / db_path).resolve()
    else:
        db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def execute(query: str, params: tuple = ()) -> None:
    with connect() as conn:
        conn.execute(query, params)
        conn.commit()


def fetchone(query: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def fetchall(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS bot_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                role TEXT DEFAULT 'user',
                owner_admin_id INTEGER,
                is_active INTEGER DEFAULT 1,
                is_blocked INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                telegram_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                display_name TEXT,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                seller_id INTEGER,
                package_id TEXT,
                package_name TEXT,
                amount INTEGER DEFAULT 0,
                currency TEXT DEFAULT 'TOMAN',
                status TEXT DEFAULT 'pending',
                customer_username TEXT,
                amount_toman INTEGER DEFAULT 0,
                discount_amount INTEGER DEFAULT 0,
                discount_code TEXT,
                stars_amount INTEGER DEFAULT 0,
                wallet_used INTEGER DEFAULT 0,
                created_at TEXT,
                paid_at TEXT
            );

            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_percent INTEGER DEFAULT 0,
                discount_amount INTEGER DEFAULT 0,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                package_id TEXT,
                expires_at TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER,
                amount INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS star_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER DEFAULT 0,
                currency TEXT DEFAULT 'XTR',
                payload TEXT,
                telegram_payment_charge_id TEXT UNIQUE,
                provider_payment_charge_id TEXT,
                status TEXT DEFAULT 'paid',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT,
                status TEXT DEFAULT 'open',
                assigned_admin_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS package_settings (
                template_id TEXT PRIMARY KEY,
                title TEXT,
                category TEXT,
                sort_order INTEGER DEFAULT 1000,
                is_visible INTEGER DEFAULT 1,
                price_amount INTEGER DEFAULT 0,
                price_currency TEXT DEFAULT 'TOMAN',
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                message TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS required_join_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE NOT NULL,
                title TEXT,
                invite_link TEXT,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tutorials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                download_url TEXT NOT NULL,
                content_text TEXT,
                video_file_id TEXT,
                is_recommended INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 1000,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            );



            CREATE TABLE IF NOT EXISTS ad_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ad_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                visited_at TEXT NOT NULL,
                UNIQUE(campaign_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS ad_attributions (
                user_id INTEGER PRIMARY KEY,
                campaign_id INTEGER NOT NULL,
                attributed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER NOT NULL,
                invited_id INTEGER NOT NULL UNIQUE,
                status TEXT DEFAULT 'pending',
                reward_amount INTEGER DEFAULT 0,
                reward_paid INTEGER DEFAULT 0,
                created_at TEXT,
                rewarded_at TEXT
            );

            CREATE TABLE IF NOT EXISTS wallet_charge_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_toman INTEGER NOT NULL,
                payable_toman INTEGER DEFAULT 0,
                stars_amount INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                method TEXT,
                invoice_payload TEXT,
                telegram_payment_charge_id TEXT,
                created_at TEXT,
                paid_at TEXT
            );

            CREATE TABLE IF NOT EXISTS test_service_usage (
                user_id INTEGER PRIMARY KEY,
                service_id INTEGER,
                generation INTEGER DEFAULT 1,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS user_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                order_id INTEGER,
                package_id TEXT,
                package_name TEXT,
                service_username TEXT NOT NULL,
                panel_username TEXT,
                subscription_url TEXT,
                links_json TEXT,
                raw_json TEXT,
                status TEXT,
                data_limit INTEGER DEFAULT 0,
                used_traffic INTEGER DEFAULT 0,
                lifetime_used_traffic INTEGER DEFAULT 0,
                expire INTEGER,
                online_at INTEGER,
                last_subscription_update TEXT,
                client_type TEXT,
                created_at TEXT,
                updated_at TEXT,
                transferred_from INTEGER,
                transferred_to INTEGER,
                is_active INTEGER DEFAULT 1
            );
            '''
        )
        conn.commit()
    _run_migrations()


def _column_exists(table: str, column: str) -> bool:
    with connect() as conn:
        rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
        return any(row['name'] == column for row in rows)


def _run_migrations() -> None:
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tutorials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                download_url TEXT NOT NULL,
                content_text TEXT,
                video_file_id TEXT,
                is_recommended INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 1000,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tutorials_active_sort ON tutorials(is_active, sort_order, id)')
        if not _column_exists('package_settings', 'price_amount'):
            conn.execute('ALTER TABLE package_settings ADD COLUMN price_amount INTEGER DEFAULT 0')
        if not _column_exists('package_settings', 'price_currency'):
            conn.execute("ALTER TABLE package_settings ADD COLUMN price_currency TEXT DEFAULT 'TOMAN'")
        conn.execute("UPDATE package_settings SET price_currency='TOMAN' WHERE price_currency IS NULL OR price_currency='XTR'")
        if not _column_exists('orders', 'customer_username'):
            conn.execute('ALTER TABLE orders ADD COLUMN customer_username TEXT')
        if not _column_exists('orders', 'amount_toman'):
            conn.execute('ALTER TABLE orders ADD COLUMN amount_toman INTEGER DEFAULT 0')
        if not _column_exists('orders', 'discount_amount'):
            conn.execute('ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0')
        if not _column_exists('orders', 'discount_code'):
            conn.execute('ALTER TABLE orders ADD COLUMN discount_code TEXT')
        if not _column_exists('orders', 'stars_amount'):
            conn.execute('ALTER TABLE orders ADD COLUMN stars_amount INTEGER DEFAULT 0')
        if not _column_exists('orders', 'wallet_used'):
            conn.execute('ALTER TABLE orders ADD COLUMN wallet_used INTEGER DEFAULT 0')
        if not _column_exists('orders', 'telegram_payment_charge_id'):
            conn.execute('ALTER TABLE orders ADD COLUMN telegram_payment_charge_id TEXT')
        if not _column_exists('orders', 'provider_payment_charge_id'):
            conn.execute('ALTER TABLE orders ADD COLUMN provider_payment_charge_id TEXT')
        if not _column_exists('orders', 'invoice_payload'):
            conn.execute('ALTER TABLE orders ADD COLUMN invoice_payload TEXT')
        if not _column_exists('orders', 'order_type'):
            conn.execute("ALTER TABLE orders ADD COLUMN order_type TEXT DEFAULT 'new'")
        if not _column_exists('orders', 'service_id'):
            conn.execute('ALTER TABLE orders ADD COLUMN service_id INTEGER')
        if not _column_exists('orders', 'payment_method'):
            conn.execute('ALTER TABLE orders ADD COLUMN payment_method TEXT')
        if not _column_exists('orders', 'provider_invoice_id'):
            conn.execute('ALTER TABLE orders ADD COLUMN provider_invoice_id TEXT')
        if not _column_exists('orders', 'provider_payment_url'):
            conn.execute('ALTER TABLE orders ADD COLUMN provider_payment_url TEXT')
        if not _column_exists('orders', 'provider_status'):
            conn.execute('ALTER TABLE orders ADD COLUMN provider_status TEXT')
        if not _column_exists('orders', 'provider_raw_json'):
            conn.execute('ALTER TABLE orders ADD COLUMN provider_raw_json TEXT')
        if not _column_exists('orders', 'payable_toman'):
            conn.execute('ALTER TABLE orders ADD COLUMN payable_toman INTEGER DEFAULT 0')
        if not _column_exists('orders', 'payment_effects_applied'):
            conn.execute('ALTER TABLE orders ADD COLUMN payment_effects_applied INTEGER DEFAULT 0')

        conn.execute(
            """CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER NOT NULL,
                invited_id INTEGER NOT NULL UNIQUE,
                status TEXT DEFAULT 'pending',
                reward_amount INTEGER DEFAULT 0,
                reward_paid INTEGER DEFAULT 0,
                created_at TEXT,
                rewarded_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS wallet_charge_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_toman INTEGER NOT NULL,
                payable_toman INTEGER DEFAULT 0,
                stars_amount INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                method TEXT,
                invoice_payload TEXT,
                telegram_payment_charge_id TEXT,
                created_at TEXT,
                paid_at TEXT
            )"""
        )
        if not _column_exists('wallet_charge_requests', 'provider_invoice_id'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN provider_invoice_id TEXT')
        if not _column_exists('wallet_charge_requests', 'provider_payment_url'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN provider_payment_url TEXT')
        if not _column_exists('wallet_charge_requests', 'provider_status'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN provider_status TEXT')
        if not _column_exists('wallet_charge_requests', 'provider_raw_json'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN provider_raw_json TEXT')
        if not _column_exists('wallet_charge_requests', 'campaign_id'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN campaign_id INTEGER')
        if not _column_exists('wallet_charge_requests', 'campaign_bonus_percent'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN campaign_bonus_percent REAL DEFAULT 0')
        if not _column_exists('wallet_charge_requests', 'campaign_bonus_amount'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN campaign_bonus_amount INTEGER DEFAULT 0')
        if not _column_exists('wallet_charge_requests', 'campaign_bonus_applied'):
            conn.execute('ALTER TABLE wallet_charge_requests ADD COLUMN campaign_bonus_applied INTEGER DEFAULT 0')
        if not _column_exists('wallet_charge_requests', 'campaign_timing_rule'):
            conn.execute("ALTER TABLE wallet_charge_requests ADD COLUMN campaign_timing_rule TEXT DEFAULT 'payment_time'")

        if not _column_exists('test_service_usage', 'reminder_sent_at'):
            conn.execute('ALTER TABLE test_service_usage ADD COLUMN reminder_sent_at TEXT')

        # --- Receipt tracking extensions ---
        conn.execute('''CREATE TABLE IF NOT EXISTS card_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                amount_toman INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                file_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                caption TEXT,
                submitted_at TEXT NOT NULL,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                reject_reason TEXT
            )''')
        if not _column_exists('card_receipts', 'order_type'):
            conn.execute("ALTER TABLE card_receipts ADD COLUMN order_type TEXT DEFAULT 'new'")
        if not _column_exists('card_receipts', 'service_id'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN service_id INTEGER')
        if not _column_exists('card_receipts', 'package_id'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN package_id TEXT')
        if not _column_exists('card_receipts', 'package_name'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN package_name TEXT')
        if not _column_exists('card_receipts', 'customer_username'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN customer_username TEXT')
        if not _column_exists('card_receipts', 'admin_delivery_status'):
            conn.execute("ALTER TABLE card_receipts ADD COLUMN admin_delivery_status TEXT DEFAULT 'pending'")
        if not _column_exists('card_receipts', 'notified_admin_ids'):
            conn.execute("ALTER TABLE card_receipts ADD COLUMN notified_admin_ids TEXT DEFAULT '[]'")
        if not _column_exists('card_receipts', 'admin_message_ids'):
            conn.execute("ALTER TABLE card_receipts ADD COLUMN admin_message_ids TEXT DEFAULT '{}'")
        if not _column_exists('card_receipts', 'last_delivery_error'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN last_delivery_error TEXT')
        if not _column_exists('card_receipts', 'delivery_attempts'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN delivery_attempts INTEGER DEFAULT 0')
        if not _column_exists('card_receipts', 'next_delivery_retry_at'):
            conn.execute('ALTER TABLE card_receipts ADD COLUMN next_delivery_retry_at TEXT')

        # --- System errors soft-delete/cleanup extensions ---
        conn.execute('''CREATE TABLE IF NOT EXISTS system_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                user_id INTEGER,
                entity_type TEXT,
                entity_id TEXT,
                error_type TEXT,
                message TEXT,
                details TEXT,
                is_resolved INTEGER DEFAULT 0,
                created_at TEXT
            )''')
        if not _column_exists('system_errors', 'is_deleted'):
            conn.execute('ALTER TABLE system_errors ADD COLUMN is_deleted INTEGER DEFAULT 0')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_system_errors_deleted ON system_errors(is_deleted, is_resolved)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_card_receipts_delivery ON card_receipts(admin_delivery_status, status)')
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS wallet_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                bonus_percent REAL NOT NULL DEFAULT 0,
                duration_hours INTEGER NOT NULL DEFAULT 0,
                timing_rule TEXT NOT NULL DEFAULT 'payment_time',
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER,
                started_at TEXT NOT NULL,
                ends_at TEXT,
                stopped_at TEXT,
                stopped_by INTEGER,
                created_at TEXT NOT NULL
            )'''
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS test_service_usage (
                user_id INTEGER PRIMARY KEY,
                service_id INTEGER,
                generation INTEGER DEFAULT 1,
                created_at TEXT
            )"""
        )
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS star_refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                telegram_payment_charge_id TEXT NOT NULL,
                admin_id INTEGER,
                status TEXT DEFAULT 'done',
                created_at TEXT
            )'''
        )
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS user_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                order_id INTEGER,
                package_id TEXT,
                package_name TEXT,
                service_username TEXT NOT NULL,
                panel_username TEXT,
                subscription_url TEXT,
                links_json TEXT,
                raw_json TEXT,
                status TEXT,
                data_limit INTEGER DEFAULT 0,
                used_traffic INTEGER DEFAULT 0,
                lifetime_used_traffic INTEGER DEFAULT 0,
                expire INTEGER,
                online_at INTEGER,
                last_subscription_update TEXT,
                client_type TEXT,
                created_at TEXT,
                updated_at TEXT,
                transferred_from INTEGER,
                transferred_to INTEGER,
                is_active INTEGER DEFAULT 1
            )'''
        )

        conn.execute('''CREATE TABLE IF NOT EXISTS operation_locks (
                lock_key TEXT PRIMARY KEY,
                owner TEXT,
                expires_at TEXT,
                created_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS service_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                job_type TEXT DEFAULT 'create_or_renew_service',
                status TEXT DEFAULT 'queued',
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                last_error TEXT,
                next_run_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS system_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                user_id INTEGER,
                entity_type TEXT,
                entity_id TEXT,
                error_type TEXT,
                message TEXT,
                details TEXT,
                is_resolved INTEGER DEFAULT 0,
                created_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TEXT,
                created_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS service_warning_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER,
                user_id INTEGER,
                warning_type TEXT,
                created_at TEXT,
                UNIQUE(service_id, warning_type)
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS payment_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT,
                event_id TEXT UNIQUE,
                order_id INTEGER,
                amount INTEGER,
                status TEXT,
                signature_valid INTEGER DEFAULT 0,
                raw_body TEXT,
                created_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS seller_commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER,
                order_id INTEGER UNIQUE,
                amount INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                paid_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS backup_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                status TEXT,
                message TEXT,
                created_at TEXT
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS card_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                amount_toman INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                file_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                caption TEXT,
                submitted_at TEXT NOT NULL,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                reject_reason TEXT
            )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_card_receipts_status ON card_receipts(status, submitted_at)')
        # --- Performance indexes (hot queries) ---
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bot_users_created ON bot_users(created_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bot_users_owner ON bot_users(owner_admin_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bot_users_blocked ON bot_users(is_blocked)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bot_users_active ON bot_users(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_status_paid ON orders(status, paid_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders(seller_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_wallet_user ON wallet_transactions(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_services_telegram ON user_services(telegram_id, is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_services_expire ON user_services(expire)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bot_admins_role ON bot_admins(role, is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_visits_campaign ON ad_visits(campaign_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_visits_user ON ad_visits(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_attributions_campaign ON ad_attributions(campaign_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_service_jobs_status_next ON service_jobs(status, next_run_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_system_errors_resolved ON system_errors(is_resolved)')
        # --- Ad tracking schema migration: allow total hits, not just unique ---
        # Original ad_visits had UNIQUE(campaign_id, user_id) which made total==unique and hid revisits.
        # Migrate to plain visits (every hit logged) with indexes for fast stats.
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ad_visits'").fetchone()
            sql = str(row['sql'] if row and row['sql'] else '')
            if 'UNIQUE(campaign_id, user_id)' in sql or 'UNIQUE (campaign_id, user_id)' in sql:
                # Recreate without UNIQUE constraint
                conn.execute('''CREATE TABLE IF NOT EXISTS ad_visits_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    visited_at TEXT NOT NULL
                )''')
                # Copy existing data (deduplicated already)
                existing = conn.execute('SELECT campaign_id, user_id, visited_at FROM ad_visits').fetchall()
                for r in existing:
                    try:
                        conn.execute('INSERT INTO ad_visits_new (campaign_id, user_id, visited_at) VALUES (?,?,?)',
                                     (r['campaign_id'], r['user_id'], r['visited_at']))
                    except Exception:
                        pass
                conn.execute('DROP TABLE ad_visits')
                conn.execute('ALTER TABLE ad_visits_new RENAME TO ad_visits')
        except Exception:
            pass
        # Ensure indexes exist after possible recreation
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_visits_campaign ON ad_visits(campaign_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_visits_user ON ad_visits(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ad_visits_campaign_user ON ad_visits(campaign_id, user_id)')

        # Jobs left in running state after a crash/restart must be retried.
        conn.execute("UPDATE service_jobs SET status='retry', next_run_at=?, updated_at=? WHERE status='running'", (now_iso(), now_iso()))
        conn.commit()


def seed_roles_from_env() -> None:
    ts = now_iso()
    with connect() as conn:
        for telegram_id in settings.super_admin_ids:
            conn.execute(
                '''INSERT INTO bot_admins (telegram_id, role, display_name, is_active, created_at, updated_at)
                   VALUES (?, 'super_admin', ?, 1, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET role='super_admin', is_active=1, updated_at=excluded.updated_at''',
                (telegram_id, f'Super Admin {telegram_id}', ts, ts),
            )
        for telegram_id in settings.admin_ids:
            if telegram_id in settings.super_admin_ids:
                continue
            conn.execute(
                '''INSERT INTO bot_admins (telegram_id, role, display_name, is_active, created_at, updated_at)
                   VALUES (?, 'admin', ?, 1, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET role='admin', is_active=1, updated_at=excluded.updated_at''',
                (telegram_id, f'Admin {telegram_id}', ts, ts),
            )
        for telegram_id in settings.seller_ids:
            if telegram_id in settings.super_admin_ids or telegram_id in settings.admin_ids:
                continue
            conn.execute(
                '''INSERT INTO bot_admins (telegram_id, role, display_name, is_active, created_at, updated_at)
                   VALUES (?, 'seller', ?, 1, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET role='seller', is_active=1, updated_at=excluded.updated_at''',
                (telegram_id, f'Seller {telegram_id}', ts, ts),
            )
        conn.commit()
    # Invalidate role cache after seeding
    try:
        _ROLE_CACHE.clear()
    except Exception:
        pass


def upsert_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    ts = now_iso()
    execute(
        '''INSERT INTO bot_users (telegram_id, username, first_name, role, is_active, created_at, updated_at)
           VALUES (?, ?, ?, 'user', 1, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, is_active=1, updated_at=excluded.updated_at''',
        (telegram_id, username, first_name, ts, ts),
    )


def get_setting(key: str, default: Any = None) -> Any:
    # Hot-path cache with short TTL; invalidated on set_setting.
    try:
        cached = _SETTINGS_CACHE.get(str(key))
        if cached is not None:
            val, exp = cached
            if _time.monotonic() < exp:
                return val
            # expired
            _SETTINGS_CACHE.pop(str(key), None)
    except Exception:
        pass
    row = fetchone('SELECT value FROM settings WHERE key=?', (key,))
    if not row:
        # Cache negative result briefly to avoid DB hammer on missing keys
        try:
            _SETTINGS_CACHE[str(key)] = (default, _time.monotonic() + 3.0)
        except Exception:
            pass
        return default
    try:
        parsed = json.loads(row['value'])
    except Exception:
        parsed = row['value']
    try:
        _SETTINGS_CACHE[str(key)] = (parsed, _time.monotonic() + _SETTINGS_CACHE_TTL)
    except Exception:
        pass
    return parsed


def set_setting(key: str, value: Any) -> None:
    execute(
        '''INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
        (key, json.dumps(value, ensure_ascii=False), now_iso()),
    )
    # Invalidate cache immediately
    try:
        _SETTINGS_CACHE.pop(str(key), None)
    except Exception:
        pass
    # Also clear negative-cache alias if any
    # No alias handling needed; just pop


def clear_settings_cache(key: str | None = None) -> None:
    if key is None:
        _SETTINGS_CACHE.clear()
    else:
        _SETTINGS_CACHE.pop(str(key), None)


def get_role_cached(user_id: int | None) -> str:
    if not user_id:
        return 'user'
    try:
        cached = _ROLE_CACHE.get(int(user_id))
        if cached is not None:
            role, exp = cached
            if _time.monotonic() < exp:
                return role
            _ROLE_CACHE.pop(int(user_id), None)
    except Exception:
        pass
    row = fetchone('SELECT role FROM bot_admins WHERE telegram_id=? AND is_active=1', (int(user_id),))
    role = row['role'] if row else 'user'
    try:
        _ROLE_CACHE[int(user_id)] = (role, _time.monotonic() + _ROLE_CACHE_TTL)
    except Exception:
        pass
    return role


def invalidate_role_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _ROLE_CACHE.clear()
    else:
        _ROLE_CACHE.pop(int(user_id), None)


def add_log(admin_id: int | None, action: str, entity_type: str | None = None, entity_id: str | None = None, details: Any = None) -> None:
    execute(
        'INSERT INTO admin_logs (admin_id, action, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (admin_id, action, entity_type, entity_id, json.dumps(details, ensure_ascii=False) if details is not None else None, now_iso()),
    )


def user_count(where: str = '', params: tuple = ()) -> int:
    row = fetchone(f'SELECT COUNT(*) AS c FROM bot_users {where}', params)
    return int(row['c']) if row else 0


def sales_sum(days: int | None = None) -> int:
    if days is None:
        row = fetchone("SELECT COALESCE(SUM(amount_toman),0) AS s FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready')")
    else:
        start = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec='seconds')
        row = fetchone("SELECT COALESCE(SUM(amount_toman),0) AS s FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready') AND paid_at >= ?", (start,))
    return int(row['s']) if row else 0



def wallet_balance(user_id: int) -> int:
    row = fetchone('SELECT COALESCE(SUM(amount),0) AS balance FROM wallet_transactions WHERE user_id=?', (user_id,))
    return int(row['balance']) if row else 0


def save_star_payment(
    user_id: int | None,
    amount: int,
    payload: str | None,
    telegram_payment_charge_id: str | None,
    provider_payment_charge_id: str | None = None,
    status: str = 'paid',
) -> None:
    execute(
        '''INSERT OR IGNORE INTO star_payments
           (user_id, amount, currency, payload, telegram_payment_charge_id, provider_payment_charge_id, status, created_at)
           VALUES (?, ?, 'XTR', ?, ?, ?, ?, ?)''',
        (user_id, amount, payload, telegram_payment_charge_id, provider_payment_charge_id, status, now_iso()),
    )


def star_payments_sum() -> int:
    row = fetchone("SELECT COALESCE(SUM(amount),0) AS total FROM star_payments WHERE status='paid'")
    return int(row['total']) if row else 0


def add_star_refund(user_id: int, telegram_payment_charge_id: str, admin_id: int | None) -> None:
    execute(
        'INSERT INTO star_refunds (user_id, telegram_payment_charge_id, admin_id, status, created_at) VALUES (?, ?, ?, ?, ?)',
        (user_id, telegram_payment_charge_id, admin_id, 'done', now_iso()),
    )


def paid_orders(limit: int = 20) -> list[dict[str, Any]]:
    return fetchall(
        "SELECT * FROM orders WHERE status IN ('paid','build_queued','building_service','service_ready') ORDER BY paid_at DESC, created_at DESC LIMIT ?",
        (limit,),
    )



def required_join_add(chat_id: str, title: str | None, invite_link: str | None, created_by: int | None) -> None:
    execute(
        '''INSERT INTO required_join_channels (chat_id, title, invite_link, is_active, created_by, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, invite_link=excluded.invite_link, is_active=1, updated_at=excluded.updated_at''',
        (chat_id, title, invite_link, created_by, now_iso(), now_iso()),
    )


def required_join_list(include_inactive: bool = False) -> list[dict[str, Any]]:
    if include_inactive:
        return fetchall('SELECT * FROM required_join_channels ORDER BY is_active DESC, id DESC')
    return fetchall('SELECT * FROM required_join_channels WHERE is_active=1 ORDER BY id DESC')


def required_join_get(channel_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM required_join_channels WHERE id=?', (channel_id,))


def required_join_set_active(channel_id: int, is_active: int) -> None:
    execute('UPDATE required_join_channels SET is_active=?, updated_at=? WHERE id=?', (1 if is_active else 0, now_iso(), channel_id))


def required_join_delete(channel_id: int) -> None:
    execute('DELETE FROM required_join_channels WHERE id=?', (channel_id,))



def coupon_by_code(code: str) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM coupons WHERE UPPER(code)=UPPER(?)', (code,))


def increment_coupon_usage(coupon_id: int) -> None:
    execute('UPDATE coupons SET used_count=used_count+1 WHERE id=?', (coupon_id,))


def create_customer_order(
    user_id: int,
    package_id: str,
    package_name: str,
    customer_username: str,
    amount_toman: int,
    discount_amount: int = 0,
    discount_code: str | None = None,
    stars_amount: int = 0,
    wallet_used: int = 0,
    status: str = 'pending',
    order_type: str = 'new',
    service_id: int | None = None,
) -> int:
    with connect() as conn:
        owner_row = conn.execute('SELECT owner_admin_id FROM bot_users WHERE telegram_id=?', (int(user_id),)).fetchone()
        seller_id = int(owner_row['owner_admin_id']) if owner_row and owner_row['owner_admin_id'] else None
        cur = conn.execute(
            """INSERT INTO orders (
                user_id, seller_id, package_id, package_name, amount, currency, status,
                customer_username, amount_toman, discount_amount, discount_code, stars_amount, wallet_used,
                order_type, service_id, created_at, paid_at
            ) VALUES (?, ?, ?, ?, ?, 'TOMAN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                user_id,
                seller_id,
                str(package_id),
                package_name,
                int(amount_toman),
                status,
                customer_username,
                int(amount_toman),
                int(discount_amount),
                discount_code,
                int(stars_amount),
                int(wallet_used),
                order_type,
                service_id,
                now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

def get_order(order_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM orders WHERE id=?', (order_id,))


def mark_order_paid(order_id: int, stars_amount: int, telegram_payment_charge_id: str | None, provider_payment_charge_id: str | None) -> None:
    execute(
        """UPDATE orders
           SET status='paid', paid_at=?, stars_amount=?, telegram_payment_charge_id=?, provider_payment_charge_id=?, invoice_payload=?
           WHERE id=?""",
        (now_iso(), int(stars_amount), telegram_payment_charge_id, provider_payment_charge_id, f'customer_order:{order_id}', int(order_id)),
    )



def set_order_payment_provider(order_id: int, method: str, payable_toman: int, provider_invoice_id: str | None = None, provider_payment_url: str | None = None, provider_status: str | None = None, raw: Any = None) -> None:
    execute(
        """UPDATE orders
           SET payment_method=?, payable_toman=?, provider_invoice_id=?, provider_payment_url=?, provider_status=?, provider_raw_json=?
           WHERE id=?""",
        (str(method), int(payable_toman or 0), provider_invoice_id, provider_payment_url, provider_status, json.dumps(raw, ensure_ascii=False) if raw is not None else None, int(order_id)),
    )


def mark_order_provider_paid(order_id: int, method: str, amount_toman: int, provider_payment_charge_id: str | None = None, raw: Any = None) -> None:
    execute(
        """UPDATE orders
           SET status='paid', paid_at=?, payment_method=?, payable_toman=?, provider_payment_charge_id=?, provider_status='paid', invoice_payload=?, provider_raw_json=?
           WHERE id=?""",
        (now_iso(), str(method), int(amount_toman or 0), provider_payment_charge_id, f'customer_order:{order_id}', json.dumps(raw, ensure_ascii=False) if raw is not None else None, int(order_id)),
    )


def set_wallet_charge_provider(request_id: int, method: str, provider_invoice_id: str | None = None, provider_payment_url: str | None = None, provider_status: str | None = None, raw: Any = None) -> None:
    execute(
        """UPDATE wallet_charge_requests
           SET method=?, provider_invoice_id=?, provider_payment_url=?, provider_status=?, provider_raw_json=?
           WHERE id=?""",
        (str(method), provider_invoice_id, provider_payment_url, provider_status, json.dumps(raw, ensure_ascii=False) if raw is not None else None, int(request_id)),
    )


def _campaign_bonus_for_request(req: dict[str, Any], conn: sqlite3.Connection) -> int:
    if int(req.get('campaign_bonus_applied') or 0):
        return int(req.get('campaign_bonus_amount') or 0)
    campaign_id = int(req.get('campaign_id') or 0)
    percent = float(req.get('campaign_bonus_percent') or 0)
    if not campaign_id or percent <= 0:
        return 0
    rule = str(req.get('campaign_timing_rule') or 'payment_time')
    if rule != 'payment_time':
        campaign = conn.execute('SELECT * FROM wallet_campaigns WHERE id=?', (campaign_id,)).fetchone()
        if not campaign or str(campaign['status']) != 'active':
            return 0
        ends = _parse_iso_dt(campaign['ends_at'])
        if ends and datetime.utcnow() >= ends:
            return 0
    return max(0, int(round(int(req.get('amount_toman') or 0) * percent / 100.0)))


def mark_wallet_charge_paid_provider(request_id: int, method: str, provider_payment_charge_id: str | None = None, raw: Any = None) -> bool:
    """Atomically mark a wallet charge paid and credit base+campaign bonus exactly once."""
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM wallet_charge_requests WHERE id=?', (int(request_id),)).fetchone()
        if not row or row['status'] == 'paid':
            conn.rollback()
            return False
        req = dict(row)
        cur = conn.execute(
            """UPDATE wallet_charge_requests
               SET status='paid', paid_at=?, method=?, invoice_payload=?, telegram_payment_charge_id=?, provider_status='paid', provider_raw_json=?
               WHERE id=? AND status!='paid'""",
            (now_iso(), str(method), f'wallet_charge:{request_id}', provider_payment_charge_id,
             json.dumps(raw, ensure_ascii=False, default=str) if raw is not None else None, int(request_id)),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.execute('INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, NULL, ?, ?, ?)',
                     (int(req['user_id']), int(req['amount_toman']), f'شارژ کیف پول #{request_id} با {method}', now_iso()))
        bonus = _campaign_bonus_for_request(req, conn)
        if bonus > 0:
            conn.execute('INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, NULL, ?, ?, ?)',
                         (int(req['user_id']), bonus, f'بونوس کمپین شارژ #{int(req.get("campaign_id") or 0)} برای درخواست #{request_id}', now_iso()))
        conn.execute('UPDATE wallet_charge_requests SET campaign_bonus_applied=1, campaign_bonus_amount=? WHERE id=?', (bonus, int(request_id)))
        conn.commit()
        return True

def finalize_order_payment_effects(order_id: int) -> bool:
    """Apply wallet deduction, coupon usage and referral reward exactly once per paid order."""
    referral_condition = str(get_setting('referral_condition', 'payment') or 'payment')
    fallback_reward = int(get_setting('referral_reward_toman', 0) or 0)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM orders WHERE id=?', (int(order_id),)).fetchone()
        if not row:
            conn.rollback(); return False
        order = dict(row)
        if int(order.get('payment_effects_applied') or 0):
            conn.rollback(); return False
        wallet_used = int(order.get('wallet_used') or 0)
        if wallet_used > 0:
            conn.execute('INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, NULL, ?, ?, ?)',
                         (int(order['user_id']), -wallet_used, f'پرداخت سفارش #{order_id} با کیف پول', now_iso()))
        code = str(order.get('discount_code') or '').strip()
        if code:
            coupon = conn.execute('SELECT id FROM coupons WHERE UPPER(code)=UPPER(?)', (code,)).fetchone()
            if coupon:
                conn.execute('UPDATE coupons SET used_count=used_count+1 WHERE id=?', (int(coupon['id']),))
        if referral_condition == 'payment':
            ref = conn.execute('SELECT * FROM referrals WHERE invited_id=? AND reward_paid=0', (int(order['user_id']),)).fetchone()
            if ref:
                reward = int(ref['reward_amount'] or fallback_reward or 0)
                if reward > 0:
                    conn.execute('INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, NULL, ?, ?, ?)',
                                 (int(ref['inviter_id']), reward, f'پاداش دعوت کاربر {int(order["user_id"])}', now_iso()))
                    conn.execute("UPDATE referrals SET status='rewarded', reward_paid=1, rewarded_at=? WHERE id=?", (now_iso(), int(ref['id'])))
                else:
                    conn.execute("UPDATE referrals SET status='done_no_reward', reward_paid=1, rewarded_at=? WHERE id=?", (now_iso(), int(ref['id'])))
        conn.execute('UPDATE orders SET payment_effects_applied=1 WHERE id=?', (int(order_id),))
        conn.commit()
        return True


def save_user_service(
    telegram_id: int,
    order_id: int | None,
    package_id: str | None,
    package_name: str | None,
    service_username: str,
    user_data: dict[str, Any] | None = None,
    subscription_url: str | None = None,
    links: list[str] | None = None,
) -> int:
    user_data = user_data or {}
    now = now_iso()
    status = user_data.get('status')
    data_limit = int(user_data.get('data_limit') or 0)
    used_traffic = int(user_data.get('used_traffic') or 0)
    lifetime_used = int(user_data.get('lifetime_used_traffic') or used_traffic or 0)
    expire = user_data.get('expire')
    online_at = user_data.get('online_at') or user_data.get('last_online')
    client_type = user_data.get('client_type') or user_data.get('last_client') or user_data.get('user_agent')
    with connect() as conn:
        existing = conn.execute(
            'SELECT id FROM user_services WHERE telegram_id=? AND service_username=? AND is_active=1',
            (int(telegram_id), service_username),
        ).fetchone()
        if existing:
            service_id = int(existing['id'])
            conn.execute(
                '''UPDATE user_services SET order_id=COALESCE(?, order_id), package_id=?, package_name=?, panel_username=?,
                   subscription_url=?, links_json=?, raw_json=?, status=?, data_limit=?, used_traffic=?, lifetime_used_traffic=?,
                   expire=?, online_at=?, client_type=?, updated_at=? WHERE id=?''',
                (
                    order_id, package_id, package_name, service_username, subscription_url,
                    json.dumps(links or [], ensure_ascii=False), json.dumps(user_data, ensure_ascii=False), status,
                    data_limit, used_traffic, lifetime_used, expire, online_at, client_type, now, service_id,
                ),
            )
        else:
            cur = conn.execute(
                '''INSERT INTO user_services (
                    telegram_id, order_id, package_id, package_name, service_username, panel_username,
                    subscription_url, links_json, raw_json, status, data_limit, used_traffic, lifetime_used_traffic,
                    expire, online_at, last_subscription_update, client_type, created_at, updated_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)''',
                (
                    int(telegram_id), order_id, package_id, package_name, service_username, service_username,
                    subscription_url, json.dumps(links or [], ensure_ascii=False), json.dumps(user_data, ensure_ascii=False),
                    status, data_limit, used_traffic, lifetime_used, expire, online_at, now, client_type, now, now,
                ),
            )
            service_id = int(cur.lastrowid)
        conn.commit()
        return service_id


def user_services(telegram_id: int) -> list[dict[str, Any]]:
    return fetchall(
        'SELECT * FROM user_services WHERE telegram_id=? AND is_active=1 ORDER BY updated_at DESC, id DESC',
        (int(telegram_id),),
    )


def get_user_service(service_id: int, telegram_id: int | None = None) -> dict[str, Any] | None:
    if telegram_id is None:
        return fetchone('SELECT * FROM user_services WHERE id=? AND is_active=1', (int(service_id),))
    return fetchone('SELECT * FROM user_services WHERE id=? AND telegram_id=? AND is_active=1', (int(service_id), int(telegram_id)))


def update_user_service_snapshot(service_id: int, user_data: dict[str, Any], subscription_url: str | None = None, links: list[str] | None = None) -> None:
    now = now_iso()
    execute(
        '''UPDATE user_services SET subscription_url=COALESCE(?, subscription_url), links_json=?, raw_json=?, status=?,
           data_limit=?, used_traffic=?, lifetime_used_traffic=?, expire=?, online_at=?, client_type=?, updated_at=? WHERE id=?''',
        (
            subscription_url,
            json.dumps(links or [], ensure_ascii=False),
            json.dumps(user_data, ensure_ascii=False),
            user_data.get('status'),
            int(user_data.get('data_limit') or 0),
            int(user_data.get('used_traffic') or 0),
            int(user_data.get('lifetime_used_traffic') or user_data.get('used_traffic') or 0),
            user_data.get('expire'),
            user_data.get('online_at') or user_data.get('last_online'),
            user_data.get('client_type') or user_data.get('last_client') or user_data.get('user_agent'),
            now,
            int(service_id),
        ),
    )


def transfer_user_service(service_id: int, old_owner: int, new_owner: int) -> None:
    execute(
        '''UPDATE user_services SET telegram_id=?, transferred_from=?, transferred_to=?, updated_at=?
           WHERE id=? AND telegram_id=? AND is_active=1''',
        (int(new_owner), int(old_owner), int(new_owner), now_iso(), int(service_id), int(old_owner)),
    )


def deactivate_user_service(service_id: int, owner_id: int, status: str = 'deleted_by_customer') -> None:
    execute(
        '''UPDATE user_services SET is_active=0, status=?, updated_at=?
           WHERE id=? AND telegram_id=? AND is_active=1''',
        (status, now_iso(), int(service_id), int(owner_id)),
    )



def add_wallet_transaction(user_id: int, amount: int, reason: str = '', admin_id: int | None = None) -> None:
    execute(
        'INSERT INTO wallet_transactions (user_id, admin_id, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)',
        (int(user_id), admin_id, int(amount), reason, now_iso()),
    )


def create_referral(inviter_id: int, invited_id: int) -> bool:
    if int(inviter_id) == int(invited_id):
        return False
    if fetchone('SELECT id FROM referrals WHERE invited_id=?', (int(invited_id),)):
        return False
    reward = int(get_setting('referral_reward_toman', 0) or 0)
    execute(
        'INSERT INTO referrals (inviter_id, invited_id, status, reward_amount, reward_paid, created_at) VALUES (?, ?, ?, ?, 0, ?)',
        (int(inviter_id), int(invited_id), 'pending', reward, now_iso()),
    )
    return True


def process_referral_reward(invited_id: int, reason: str = 'join') -> bool:
    row = fetchone('SELECT * FROM referrals WHERE invited_id=? AND reward_paid=0', (int(invited_id),))
    if not row:
        return False
    required = get_setting('referral_condition', 'payment')
    if required == 'payment' and reason != 'payment':
        return False
    amount = int(row.get('reward_amount') or get_setting('referral_reward_toman', 0) or 0)
    if amount <= 0:
        execute('UPDATE referrals SET status=?, reward_paid=1, rewarded_at=? WHERE id=?', ('done_no_reward', now_iso(), int(row['id'])))
        return True
    add_wallet_transaction(int(row['inviter_id']), amount, f'پاداش دعوت کاربر {invited_id}')
    execute('UPDATE referrals SET status=?, reward_paid=1, rewarded_at=? WHERE id=?', ('rewarded', now_iso(), int(row['id'])))
    return True


def referral_stats(user_id: int) -> dict[str, int]:
    total = fetchone('SELECT COUNT(*) AS c FROM referrals WHERE inviter_id=?', (int(user_id),))
    rewarded = fetchone('SELECT COUNT(*) AS c FROM referrals WHERE inviter_id=? AND reward_paid=1', (int(user_id),))
    pending = fetchone('SELECT COUNT(*) AS c FROM referrals WHERE inviter_id=? AND reward_paid=0', (int(user_id),))
    return {'total': int(total['c'] if total else 0), 'rewarded': int(rewarded['c'] if rewarded else 0), 'pending': int(pending['c'] if pending else 0)}


# Override earlier add_notification so admin can disable notifications globally.
def add_notification(type_: str, message: str) -> None:
    if not bool(get_setting('notifications_enabled', True)):
        return
    execute(
        'INSERT INTO notifications (type, message, is_read, created_at) VALUES (?, ?, 0, ?)',
        (type_, message, now_iso()),
    )



def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00').replace('+00:00', ''))
    except Exception:
        return None


def get_active_wallet_campaign(at: datetime | None = None) -> dict[str, Any] | None:
    at = at or datetime.utcnow()
    rows = fetchall("SELECT * FROM wallet_campaigns WHERE status='active' ORDER BY id DESC")
    for row in rows:
        ends = _parse_iso_dt(row.get('ends_at'))
        if ends and at >= ends:
            execute("UPDATE wallet_campaigns SET status='expired' WHERE id=? AND status='active'", (int(row['id']),))
            continue
        started = _parse_iso_dt(row.get('started_at'))
        if started and at < started:
            continue
        return row
    return None


def wallet_campaign_by_id(campaign_id: int | None) -> dict[str, Any] | None:
    if not campaign_id:
        return None
    return fetchone('SELECT * FROM wallet_campaigns WHERE id=?', (int(campaign_id),))


def create_wallet_campaign(title: str, bonus_percent: float, duration_hours: int, timing_rule: str, created_by: int) -> int:
    execute("UPDATE wallet_campaigns SET status='stopped', stopped_at=?, stopped_by=? WHERE status='active'", (now_iso(), int(created_by)))
    started = datetime.utcnow()
    ends = started + timedelta(hours=int(duration_hours)) if int(duration_hours) > 0 else None
    with connect() as conn:
        cur = conn.execute(
            '''INSERT INTO wallet_campaigns
               (title, bonus_percent, duration_hours, timing_rule, status, created_by, started_at, ends_at, created_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)''',
            (str(title or 'کمپین شارژ کیف پول'), float(bonus_percent), int(duration_hours), str(timing_rule), int(created_by),
             started.isoformat(timespec='seconds'), ends.isoformat(timespec='seconds') if ends else None, now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)


def stop_wallet_campaign(campaign_id: int, stopped_by: int) -> bool:
    row = fetchone("SELECT * FROM wallet_campaigns WHERE id=? AND status='active'", (int(campaign_id),))
    if not row:
        return False
    execute("UPDATE wallet_campaigns SET status='stopped', stopped_at=?, stopped_by=? WHERE id=?", (now_iso(), int(stopped_by), int(campaign_id)))
    return True


def _campaign_valid_for_wallet_request(req: dict[str, Any]) -> bool:
    campaign_id = int(req.get('campaign_id') or 0)
    percent = float(req.get('campaign_bonus_percent') or 0)
    if not campaign_id or percent <= 0:
        return False
    rule = str(req.get('campaign_timing_rule') or 'payment_time')
    if rule == 'payment_time':
        return True
    campaign = wallet_campaign_by_id(campaign_id)
    if not campaign or str(campaign.get('status')) != 'active':
        return False
    ends = _parse_iso_dt(campaign.get('ends_at'))
    return not ends or datetime.utcnow() < ends


def apply_wallet_campaign_bonus(request_id: int) -> int:
    req = get_wallet_charge_request(request_id)
    if not req or int(req.get('campaign_bonus_applied') or 0):
        return int(req.get('campaign_bonus_amount') or 0) if req else 0
    if not _campaign_valid_for_wallet_request(req):
        execute('UPDATE wallet_charge_requests SET campaign_bonus_applied=1, campaign_bonus_amount=0 WHERE id=?', (int(request_id),))
        return 0
    percent = float(req.get('campaign_bonus_percent') or 0)
    base = int(req.get('amount_toman') or 0)
    bonus = max(0, int(round(base * percent / 100.0)))
    if bonus > 0:
        add_wallet_transaction(int(req['user_id']), bonus, f'بونوس کمپین شارژ #{int(req.get("campaign_id") or 0)} برای درخواست #{request_id}')
    execute('UPDATE wallet_charge_requests SET campaign_bonus_applied=1, campaign_bonus_amount=? WHERE id=?', (bonus, int(request_id)))
    return bonus


def create_wallet_charge_request(user_id: int, amount_toman: int, stars_amount: int = 0, method: str | None = None) -> int:
    campaign = get_active_wallet_campaign()
    campaign_id = int(campaign['id']) if campaign else None
    campaign_percent = float(campaign.get('bonus_percent') or 0) if campaign else 0.0
    timing_rule = str(campaign.get('timing_rule') or 'payment_time') if campaign else 'payment_time'
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO wallet_charge_requests
               (user_id, amount_toman, payable_toman, stars_amount, status, method, created_at, campaign_id, campaign_bonus_percent, campaign_timing_rule)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (int(user_id), int(amount_toman), int(amount_toman), int(stars_amount), method, now_iso(), campaign_id, campaign_percent, timing_rule),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_wallet_charge_request(request_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM wallet_charge_requests WHERE id=?', (int(request_id),))


def create_or_update_card_receipt(
    user_id: int,
    kind: str,
    entity_id: int,
    amount_toman: int,
    file_type: str,
    file_id: str,
    caption: str = '',
    order_type: str = 'new',
    service_id: int | None = None,
    package_id: str | None = None,
    package_name: str | None = None,
    customer_username: str | None = None,
    receipt_id: int | None = None,
) -> int:
    ts = now_iso()
    with connect() as conn:
        if receipt_id:
            conn.execute(
                """UPDATE card_receipts
                   SET file_type=?, file_id=?, caption=?, amount_toman=?, submitted_at=?, status='pending'
                   WHERE id=?""",
                (file_type, file_id, caption or '', int(amount_toman), ts, int(receipt_id)),
            )
            return int(receipt_id)
        cur = conn.execute(
            """INSERT INTO card_receipts (
                user_id, kind, entity_id, amount_toman, status, file_type, file_id, caption,
                submitted_at, order_type, service_id, package_id, package_name, customer_username,
                admin_delivery_status, notified_admin_ids, admin_message_ids, delivery_attempts
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '[]', '{}', 0)""",
            (
                int(user_id), str(kind), int(entity_id), int(amount_toman),
                str(file_type), str(file_id), caption or '', ts,
                str(order_type or 'new'), service_id, package_id, package_name, customer_username,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_card_receipt(receipt_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM card_receipts WHERE id=?', (int(receipt_id),))


def find_active_card_receipt_for_user(user_id: int) -> dict[str, Any] | None:
    # Most recent pending receipt or waiting-receipt order
    return fetchone(
        """SELECT * FROM card_receipts
           WHERE user_id=? AND status='pending'
           ORDER BY id DESC LIMIT 1""",
        (int(user_id),),
    )


def update_card_receipt_delivery(
    receipt_id: int,
    *,
    status: str,
    notified_admin_ids: list[int] | None = None,
    admin_message_ids: dict[str, int] | None = None,
    error: str | None = None,
    increment_attempts: bool = False,
    next_retry_at: str | None = None,
) -> None:
    updates = ['admin_delivery_status=?']
    params: list[Any] = [str(status)]
    if notified_admin_ids is not None:
        updates.append('notified_admin_ids=?')
        params.append(json.dumps(notified_admin_ids))
    if admin_message_ids is not None:
        updates.append('admin_message_ids=?')
        params.append(json.dumps(admin_message_ids))
    if error is not None:
        updates.append('last_delivery_error=?')
        params.append(str(error)[:1000])
    if increment_attempts:
        updates.append('delivery_attempts=COALESCE(delivery_attempts, 0) + 1')
    if next_retry_at is not None:
        updates.append('next_delivery_retry_at=?')
        params.append(next_retry_at)
    params.append(int(receipt_id))
    execute(f"UPDATE card_receipts SET {', '.join(updates)} WHERE id=?", tuple(params))


def pending_receipt_deliveries(limit: int = 10) -> list[dict[str, Any]]:
    # Find receipts where file is present, status is pending, but admin delivery is pending or failed
    now = now_iso()
    return fetchall(
        """SELECT * FROM card_receipts
           WHERE status='pending'
             AND file_id IS NOT NULL AND file_id != ''
             AND admin_delivery_status IN ('pending', 'failed')
             AND (next_delivery_retry_at IS NULL OR next_delivery_retry_at <= ?)
             AND COALESCE(delivery_attempts, 0) < 15
           ORDER BY id ASC LIMIT ?""",
        (now, int(limit)),
    )


def mark_wallet_charge_paid(request_id: int, stars_amount: int, telegram_payment_charge_id: str | None) -> bool:
    paid = mark_wallet_charge_paid_provider(request_id, 'stars', telegram_payment_charge_id, {'stars_amount': int(stars_amount)})
    if paid:
        execute('UPDATE wallet_charge_requests SET stars_amount=? WHERE id=?', (int(stars_amount), int(request_id)))
    return paid

def current_test_generation() -> int:
    return int(get_setting('test_service_generation', 1) or 1)


def test_service_eligibility(user_id: int, cooldown_days: int | None = None) -> tuple[bool, int]:
    """Return (eligible_now, remaining_seconds). Generation reset always grants access immediately."""
    row = fetchone('SELECT generation, created_at FROM test_service_usage WHERE user_id=?', (int(user_id),))
    if not row or int(row.get('generation') or 0) < current_test_generation():
        return True, 0
    if cooldown_days is None:
        cooldown_days = int(get_setting('test_service_cooldown_days', 14) or 14)
    cooldown_days = int(cooldown_days)
    if cooldown_days <= 0:
        return False, 0
    try:
        used_at = datetime.fromisoformat(str(row.get('created_at') or ''))
    except Exception:
        return False, 0
    next_at = used_at + timedelta(days=cooldown_days)
    remaining = int((next_at - now_iso_dt()).total_seconds())
    return remaining <= 0, max(0, remaining)


def user_has_used_current_test(user_id: int) -> bool:
    eligible, _ = test_service_eligibility(user_id)
    return not eligible


def mark_test_used(user_id: int, service_id: int | None = None) -> None:
    execute(
        """INSERT INTO test_service_usage (user_id, service_id, generation, created_at, reminder_sent_at) VALUES (?, ?, ?, ?, NULL)
           ON CONFLICT(user_id) DO UPDATE SET service_id=excluded.service_id, generation=excluded.generation, created_at=excluded.created_at, reminder_sent_at=NULL""",
        (int(user_id), service_id, current_test_generation(), now_iso()),
    )


def due_test_service_reminders(cooldown_days: int, limit: int = 200) -> list[dict[str, Any]]:
    cooldown_days = int(cooldown_days)
    if cooldown_days <= 0:
        return []
    return fetchall(
        """SELECT * FROM test_service_usage
           WHERE generation=?
             AND reminder_sent_at IS NULL
             AND datetime(created_at, '+' || ? || ' days') <= datetime('now')
           ORDER BY created_at ASC LIMIT ?""",
        (current_test_generation(), cooldown_days, int(limit)),
    )


def mark_test_reminder_sent(user_id: int, created_at: str) -> None:
    execute(
        """UPDATE test_service_usage SET reminder_sent_at=?
           WHERE user_id=? AND created_at=? AND reminder_sent_at IS NULL""",
        (now_iso(), int(user_id), str(created_at)),
    )


def reset_test_usage_for_all() -> None:
    generation = current_test_generation() + 1
    set_setting('test_service_generation', generation)


# --- v14 safety, queue, cache and payment helpers ---
def record_system_error(source: str, message: str, user_id: int | None = None, entity_type: str | None = None,
                        entity_id: str | None = None, error_type: str | None = None, details: Any = None) -> None:
    execute(
        """INSERT INTO system_errors (source, user_id, entity_type, entity_id, error_type, message, details, is_resolved, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (source, user_id, entity_type, entity_id, error_type, str(message)[:1500],
         json.dumps(details, ensure_ascii=False, default=str) if details is not None else None, now_iso()),
    )


def recent_system_errors(limit: int = 30, unresolved_only: bool = False, include_deleted: bool = False) -> list[dict[str, Any]]:
    conds = []
    if not include_deleted:
        conds.append('COALESCE(is_deleted, 0) = 0')
    if unresolved_only:
        conds.append('is_resolved = 0')
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    return fetchall(f'SELECT * FROM system_errors {where} ORDER BY id DESC LIMIT ?', (int(limit),))


def resolve_system_error(error_id: int) -> None:
    execute('UPDATE system_errors SET is_resolved=1 WHERE id=?', (int(error_id),))


def delete_system_error(error_id: int) -> None:
    execute('UPDATE system_errors SET is_deleted=1 WHERE id=?', (int(error_id),))


def delete_all_resolved_system_errors() -> int:
    with connect() as conn:
        cur = conn.execute('UPDATE system_errors SET is_deleted=1 WHERE is_resolved=1 AND COALESCE(is_deleted, 0)=0')
        conn.commit()
        return int(cur.rowcount or 0)


def system_errors_counts() -> dict[str, int]:
    with connect() as conn:
        unresolved = conn.execute('SELECT COUNT(*) AS c FROM system_errors WHERE is_resolved=0 AND COALESCE(is_deleted, 0)=0').fetchone()
        resolved = conn.execute('SELECT COUNT(*) AS c FROM system_errors WHERE is_resolved=1 AND COALESCE(is_deleted, 0)=0').fetchone()
        total = conn.execute('SELECT COUNT(*) AS c FROM system_errors WHERE COALESCE(is_deleted, 0)=0').fetchone()
        return {
            'unresolved': int(unresolved['c'] if unresolved else 0),
            'resolved': int(resolved['c'] if resolved else 0),
            'total': int(total['c'] if total else 0),
        }


def get_system_error(error_id: int, include_deleted: bool = False) -> dict[str, Any] | None:
    cond = '' if include_deleted else ' AND COALESCE(is_deleted, 0) = 0'
    return fetchone(f'SELECT * FROM system_errors WHERE id=?{cond}', (int(error_id),))


def acquire_lock(lock_key: str, owner: str = 'bot', ttl_seconds: int = 120) -> bool:
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    expires = (now + timedelta(seconds=int(ttl_seconds))).isoformat(timespec='seconds')
    key = str(lock_key)
    with connect() as conn:
        conn.execute('DELETE FROM operation_locks WHERE expires_at < ?', (now.isoformat(timespec='seconds'),))
        try:
            conn.execute('INSERT INTO operation_locks (lock_key, owner, expires_at, created_at) VALUES (?, ?, ?, ?)',
                         (key, owner, expires, now.isoformat(timespec='seconds')))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def release_lock(lock_key: str) -> None:
    execute('DELETE FROM operation_locks WHERE lock_key=?', (str(lock_key),))


def enqueue_service_job(order_id: int, job_type: str = 'create_or_renew_service', delay_seconds: int = 0) -> int:
    from datetime import datetime, timedelta
    next_run = (datetime.utcnow() + timedelta(seconds=int(delay_seconds))).isoformat(timespec='seconds')
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO service_jobs (order_id, job_type, status, attempts, max_attempts, next_run_at, created_at, updated_at)
               VALUES (?, ?, 'queued', 0, 5, ?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET status=CASE WHEN status='done' THEN status ELSE 'queued' END,
                   next_run_at=excluded.next_run_at, updated_at=excluded.updated_at""",
            (int(order_id), job_type, next_run, now, now),
        )
        row = conn.execute('SELECT id FROM service_jobs WHERE order_id=?', (int(order_id),)).fetchone()
        conn.commit()
        return int(row['id']) if row else 0


def next_service_jobs(limit: int = 5) -> list[dict[str, Any]]:
    now = now_iso()
    return fetchall(
        """SELECT * FROM service_jobs WHERE status IN ('queued','retry') AND next_run_at <= ?
           ORDER BY next_run_at ASC, id ASC LIMIT ?""",
        (now, int(limit)),
    )


def mark_service_job_running(job_id: int) -> None:
    execute('UPDATE service_jobs SET status=?, attempts=attempts+1, updated_at=? WHERE id=?', ('running', now_iso(), int(job_id)))


def mark_service_job_done(job_id: int) -> None:
    execute('UPDATE service_jobs SET status=?, last_error=NULL, updated_at=? WHERE id=?', ('done', now_iso(), int(job_id)))


def mark_service_job_failed(job_id: int, error: str, retry_delay_seconds: int = 120) -> None:
    from datetime import datetime, timedelta
    row = fetchone('SELECT attempts, max_attempts FROM service_jobs WHERE id=?', (int(job_id),)) or {}
    attempts = int(row.get('attempts') or 0)
    max_attempts = int(row.get('max_attempts') or 5)
    status = 'failed' if attempts >= max_attempts else 'retry'
    next_run = (datetime.utcnow() + timedelta(seconds=int(retry_delay_seconds))).isoformat(timespec='seconds')
    execute('UPDATE service_jobs SET status=?, last_error=?, next_run_at=?, updated_at=? WHERE id=?',
            (status, str(error)[:1500], next_run, now_iso(), int(job_id)))


def service_job_stats() -> dict[str, int]:
    rows = fetchall('SELECT status, COUNT(*) AS c FROM service_jobs GROUP BY status')
    return {str(r['status']): int(r['c']) for r in rows}


def cache_get(cache_key: str) -> Any:
    row = fetchone('SELECT value, expires_at FROM api_cache WHERE cache_key=?', (str(cache_key),))
    if not row:
        return None
    if row.get('expires_at') and row['expires_at'] < now_iso():
        execute('DELETE FROM api_cache WHERE cache_key=?', (str(cache_key),))
        return None
    try:
        return json.loads(row['value'])
    except Exception:
        return row['value']


def cache_set(cache_key: str, value: Any, ttl_seconds: int = 60) -> None:
    from datetime import datetime, timedelta
    expires = (datetime.utcnow() + timedelta(seconds=int(ttl_seconds))).isoformat(timespec='seconds')
    execute(
        """INSERT INTO api_cache (cache_key, value, expires_at, created_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at, created_at=excluded.created_at""",
        (str(cache_key), json.dumps(value, ensure_ascii=False, default=str), expires, now_iso()),
    )


def cache_delete_prefix(prefix: str) -> None:
    execute('DELETE FROM api_cache WHERE cache_key LIKE ?', (str(prefix) + '%',))


def _html_code(value: Any) -> str:
    return '<code>' + html.escape(str(value if value is not None else '-')) + '</code>'


def _html_text(value: Any) -> str:
    return html.escape(str(value if value is not None else '-'))


INVOICE_TEXT_DEFAULT = (
    '🧾 فاکتور خرید\n\n'
    'شماره سفارش: {order_id}\n'
    'آیدی عددی کاربر: {user_id}\n'
    'نام بسته: {package_name}\n'
    'نام سرویس: {service_name}\n'
    'مبلغ اصلی: {price}\n'
    'تخفیف: {discount}\n'
    'کیف پول مصرف شده: {wallet_used}\n'
    'مبلغ قابل پرداخت: {payable}\n'
    'پرداخت Stars: {stars} ⭐️\n'
    'وضعیت سفارش: {status}\n'
    'تاریخ: {created_at}\n'
    '{service_message}'
)


def create_invoice_text(order: dict[str, Any], service_message: str = '') -> str:
    amount = int(order.get('amount_toman') or order.get('amount') or 0)
    discount = int(order.get('discount_amount') or 0)
    wallet_used = int(order.get('wallet_used') or 0)
    stars = int(order.get('stars_amount') or 0)
    payable = max(0, amount - wallet_used)
    service = None
    try:
        service = fetchone(
            'SELECT subscription_url FROM user_services WHERE order_id=? AND telegram_id=? AND is_active=1 ORDER BY id DESC LIMIT 1',
            (int(order.get('id') or 0), int(order.get('user_id') or 0)),
        )
    except Exception:
        service = None
    sub_link = (service or {}).get('subscription_url') or '-'
    template = str(get_setting('invoice_text', INVOICE_TEXT_DEFAULT) or INVOICE_TEXT_DEFAULT)
    values = {
        'order_id': _html_code(order.get('id')),
        'user_id': _html_code(order.get('user_id')),
        'package_id': _html_text(order.get('package_id') or '-'),
        'package_name': _html_text(order.get('package_name') or '-'),
        'service_name': _html_code(order.get('customer_username') or '-'),
        'price': f'{amount:,} تومان',
        'price_raw': str(amount),
        'amount': f'{amount:,} تومان',
        'discount': f'{discount:,} تومان',
        'discount_raw': str(discount),
        'wallet_used': f'{wallet_used:,} تومان',
        'wallet_used_raw': str(wallet_used),
        'payable': f'{payable:,} تومان',
        'payable_raw': str(payable),
        'stars': f'{stars:,}',
        'status': _html_text(order.get('status') or '-'),
        'created_at': _html_code(order.get('created_at') or '-'),
        'service_message': html.escape(str(service_message or '')),
        'sub_link': _html_code(sub_link),
    }
    text = template
    for key, value in values.items():
        text = text.replace('{' + key + '}', str(value))
    return text


def register_service_warning(service_id: int, user_id: int, warning_type: str) -> bool:
    try:
        execute('INSERT INTO service_warning_logs (service_id, user_id, warning_type, created_at) VALUES (?, ?, ?, ?)',
                (int(service_id), int(user_id), str(warning_type), now_iso()))
        return True
    except sqlite3.IntegrityError:
        return False


def record_payment_webhook(provider: str, event_id: str, order_id: int | None, amount: int | None, status: str,
                           signature_valid: bool, raw_body: str | None = None) -> bool:
    try:
        execute(
            """INSERT INTO payment_webhook_events (provider, event_id, order_id, amount, status, signature_valid, raw_body, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider, event_id, order_id, amount, status, 1 if signature_valid else 0, raw_body, now_iso()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def add_seller_commission(seller_id: int | None, order_id: int, amount: int) -> None:
    if not seller_id or int(amount) <= 0:
        return
    execute(
        """INSERT OR IGNORE INTO seller_commissions (seller_id, order_id, amount, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (int(seller_id), int(order_id), int(amount), now_iso()),
    )


def seller_commission_stats(seller_id: int) -> dict[str, int]:
    row = fetchone("SELECT COALESCE(SUM(amount),0) AS total FROM seller_commissions WHERE seller_id=?", (int(seller_id),))
    pending = fetchone("SELECT COALESCE(SUM(amount),0) AS total FROM seller_commissions WHERE seller_id=? AND status='pending'", (int(seller_id),))
    return {'total': int(row['total'] if row else 0), 'pending': int(pending['total'] if pending else 0)}


def log_backup(file_path: str | None, status: str, message: str = '') -> None:
    execute('INSERT INTO backup_logs (file_path, status, message, created_at) VALUES (?, ?, ?, ?)',
            (file_path, status, str(message)[:1000], now_iso()))


def latest_backup() -> dict[str, Any] | None:
    return fetchone('SELECT * FROM backup_logs ORDER BY id DESC LIMIT 1')


# --- Tutorials ---
def tutorial_list(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    where = '' if include_inactive else 'WHERE is_active=1'
    return fetchall(
        f'SELECT * FROM tutorials {where} ORDER BY is_recommended DESC, sort_order ASC, id ASC'
    )


def tutorial_get(tutorial_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM tutorials WHERE id=?', (int(tutorial_id),))


def tutorial_create(
    *, title: str, download_url: str, content_text: str = '', video_file_id: str = '',
    is_recommended: bool = False, created_by: int | None = None,
) -> int:
    ts = now_iso()
    with connect() as conn:
        if is_recommended:
            conn.execute('UPDATE tutorials SET is_recommended=0, updated_at=? WHERE is_recommended=1', (ts,))
        row = conn.execute('SELECT COALESCE(MAX(sort_order), 0) + 10 AS next_order FROM tutorials').fetchone()
        sort_order = int(row['next_order'] if row else 10)
        cur = conn.execute(
            '''INSERT INTO tutorials
               (title, download_url, content_text, video_file_id, is_recommended, is_active, sort_order, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)''',
            (title, download_url, content_text, video_file_id, 1 if is_recommended else 0, sort_order, created_by, ts, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def tutorial_update(tutorial_id: int, **fields: Any) -> None:
    allowed = {'title', 'download_url', 'content_text', 'video_file_id', 'is_active', 'sort_order'}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return
    clean['updated_at'] = now_iso()
    sets = ', '.join(f'{key}=?' for key in clean)
    params = tuple(clean.values()) + (int(tutorial_id),)
    execute(f'UPDATE tutorials SET {sets} WHERE id=?', params)


def tutorial_set_recommended(tutorial_id: int, value: bool) -> None:
    ts = now_iso()
    with connect() as conn:
        if value:
            conn.execute('UPDATE tutorials SET is_recommended=0, updated_at=? WHERE is_recommended=1', (ts,))
        conn.execute('UPDATE tutorials SET is_recommended=?, updated_at=? WHERE id=?', (1 if value else 0, ts, int(tutorial_id)))
        conn.commit()


def tutorial_delete(tutorial_id: int) -> None:
    execute('DELETE FROM tutorials WHERE id=?', (int(tutorial_id),))

# --- Advertising attribution -------------------------------------------------
def _normalize_slug(raw: str) -> str:
    # Normalize for case-insensitive, whitespace-tolerant lookup and storage
    return str(raw or '').strip().lower().strip('-')[:24] or 'ad'


def unique_ad_slug(base: str) -> str:
    base = _normalize_slug(base)
    candidate = base
    n = 2
    while fetchone('SELECT id FROM ad_campaigns WHERE slug=?', (candidate,)):
        candidate = f'{base[:20]}-{n}'
        n += 1
    return candidate


def create_ad_campaign(name: str, slug: str, created_by: int | None = None) -> int:
    slug = _normalize_slug(slug)
    with connect() as conn:
        cur = conn.execute(
            'INSERT INTO ad_campaigns (name, slug, is_active, created_by, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)',
            (str(name).strip(), slug, created_by, now_iso(), now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_ad_campaigns() -> list[dict[str, Any]]:
    return fetchall('SELECT * FROM ad_campaigns ORDER BY id DESC')


def get_ad_campaign(campaign_id: int) -> dict[str, Any] | None:
    return fetchone('SELECT * FROM ad_campaigns WHERE id=?', (int(campaign_id),))


def toggle_ad_campaign(campaign_id: int) -> None:
    execute('UPDATE ad_campaigns SET is_active=CASE WHEN is_active=1 THEN 0 ELSE 1 END, updated_at=? WHERE id=?', (now_iso(), int(campaign_id)))


def delete_ad_campaign(campaign_id: int) -> None:
    with connect() as conn:
        conn.execute('DELETE FROM ad_visits WHERE campaign_id=?', (int(campaign_id),))
        conn.execute('DELETE FROM ad_attributions WHERE campaign_id=?', (int(campaign_id),))
        conn.execute('DELETE FROM ad_campaigns WHERE id=?', (int(campaign_id),))
        conn.commit()


def record_ad_visit(slug: str, user_id: int) -> bool:
    # Case-insensitive slug; inactive campaigns are ignored but logged for diagnostics
    norm = _normalize_slug(slug)
    row = fetchone('SELECT id, is_active FROM ad_campaigns WHERE slug=?', (norm,))
    if not row:
        # Try case-insensitive fallback for legacy mixed-case slugs
        row = fetchone('SELECT id, is_active FROM ad_campaigns WHERE LOWER(slug)=LOWER(?)', (norm,))
    if not row:
        try:
            record_system_error('ad_tracking.record_ad_visit', f'Unknown slug: {slug} -> {norm}', entity_type='ad_campaign', entity_id=norm, error_type='unknown_slug')
        except Exception:
            pass
        return False
    if not int(row.get('is_active') or 0):
        try:
            record_system_error('ad_tracking.record_ad_visit', f'Inactive campaign hit: {norm} (id={row["id"]})', entity_type='ad_campaign', entity_id=str(row['id']), error_type='inactive')
        except Exception:
            pass
        return False
    cid = int(row['id'])
    ts = now_iso()
    with connect() as conn:
        # Total hits: every visit is a row (no UNIQUE ignore) -> visits reflects real traffic
        conn.execute('INSERT INTO ad_visits (campaign_id, user_id, visited_at) VALUES (?, ?, ?)', (cid, int(user_id), ts))
        # Attribution: last-touch wins (update on conflict) so second campaign after first correctly attributes
        # Keep history in ad_visits; ad_attributions is current owner for revenue attribution
        try:
            conn.execute('INSERT INTO ad_attributions (user_id, campaign_id, attributed_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET campaign_id=excluded.campaign_id, attributed_at=excluded.attributed_at', (int(user_id), cid, ts))
        except Exception:
            # Fallback for older SQLite without ON CONFLICT DO UPDATE on PK
            conn.execute('INSERT OR REPLACE INTO ad_attributions (user_id, campaign_id, attributed_at) VALUES (?, ?, ?)', (int(user_id), cid, ts))
        conn.commit()
    return True


def ad_campaign_stats(campaign_id: int) -> dict[str, Any]:
    cid = int(campaign_id)
    visits = fetchone('SELECT COUNT(*) AS c FROM ad_visits WHERE campaign_id=?', (cid,))
    users = fetchone('SELECT COUNT(DISTINCT user_id) AS c FROM ad_visits WHERE campaign_id=?', (cid,))
    attributed = fetchone('SELECT COUNT(*) AS c FROM ad_attributions WHERE campaign_id=?', (cid,))
    buyers = fetchone("""SELECT COUNT(DISTINCT o.user_id) AS c
                         FROM orders o JOIN ad_attributions a ON a.user_id=o.user_id
                         WHERE a.campaign_id=? AND o.status IN ('paid','build_queued','building_service','service_ready')""", (cid,))
    orders = fetchone("""SELECT COUNT(*) AS c,
                                COALESCE(SUM(CASE WHEN COALESCE(o.payable_toman,0)>0 THEN o.payable_toman ELSE o.amount_toman END),0) AS revenue
                         FROM orders o JOIN ad_attributions a ON a.user_id=o.user_id
                         WHERE a.campaign_id=? AND o.status IN ('paid','build_queued','building_service','service_ready')""", (cid,))
    v = int(visits['c'] if visits else 0)
    u = int(users['c'] if users else 0)
    b = int(buyers['c'] if buyers else 0)
    return {
        'visits': v,               # total hits (every /start ad_)
        'visits_unique': u,        # distinct users ever visited
        'users': int(attributed['c'] if attributed else 0),  # currently attributed (last-touch)
        'buyers': b,
        'orders': int(orders['c'] if orders else 0),
        'revenue': int(orders['revenue'] if orders else 0),
        'conversion': (b / u * 100.0 if u else 0.0),
        'conversion_attributed': (b / int(attributed['c'] if attributed else 0) * 100.0 if attributed and int(attributed['c']) else 0.0),
    }


def list_ad_visits(campaign_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return fetchall('SELECT * FROM ad_visits WHERE campaign_id=? ORDER BY id DESC LIMIT ?', (int(campaign_id), int(limit)))
