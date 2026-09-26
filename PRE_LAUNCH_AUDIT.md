# Pre-launch audit - v33

This release was reviewed as a launch candidate on 2026-08-14.

## Blocking issues fixed

- SQLite backups now use the SQLite Backup API and are verified with `PRAGMA integrity_check`.
- Pre-update backups are created automatically before install/update.
- A safe restore script validates a backup before replacing the live database.
- SQLite uses WAL mode, a 30s busy timeout, and normal synchronous mode to reduce lock failures under concurrent bot/webhook/background activity.
- Service jobs left in `running` after a crash/restart are recovered to `retry` during database initialization.
- Wallet top-ups are idempotent and the base credit plus campaign bonus are committed in one transaction.
- Card-to-card receipt approval is claimed atomically so two admins cannot approve the same receipt concurrently.
- Paid-order side effects (wallet deduction, coupon consumption, referral reward) are applied atomically only once.
- Telegram Stars pre-checkout now validates payload ownership, currency, and the expected amount before approving the checkout.
- Plisio only treats a verified final payment state as paid; callback signatures are required and the operation is re-checked before crediting an order/wallet.
- Generic Plisio API success is no longer confused with a completed payment.
- Duplicate Tetrapay callback route registration was removed.
- Payment webhook startup is fail-fast instead of silently launching polling without callbacks when callback hosting is expected.
- Disabled Stars no longer blocks the other gateways because a Stars conversion rate is not required when Stars is off.
- 100% discount/free orders correctly show the free-completion path before wallet payment logic.
- Sales/report/buyer filters include paid orders that progressed to build/service-ready states.
- Seller order ownership is populated from the customer's owner seller.
- Sellers are denied global dashboard/notifications and sensitive order status changes; seller tickets are scoped to their customers.
- Seller `/start seller_<id>` attribution is supported for assigning a new customer once.
- Service expiry warning parsing accepts ISO-8601 timestamps such as `2026-09-22T11:54:19Z`.
- Duplicate handler/function definitions found during review were removed.
- Advanced-setting toggle refresh behavior was corrected.
- Subscription base URL configured from the bot is honored instead of being gated by a stale `.env` check.
- Broadcast buyer/non-buyer filters now use all successful paid-order lifecycle states.

## Automatic install/update

Run as root from the project directory:

```bash
sudo bash scripts/install_or_update.sh
```

The installer:

1. Creates an integrity-checked SQLite pre-update backup.
2. Stops `pasarguard-admin-bot.service` if it is running.
3. Creates/updates the virtual environment and installs requirements.
4. Compiles Python files with syntax warnings treated as errors.
5. Runs migrations and preflight checks.
6. Installs/updates the systemd service.
7. Configures Nginx callback proxying when a callback domain/URL is configured.
8. Restarts the service and verifies it is active.

## Manual preflight

```bash
source venv/bin/activate
python scripts/preflight.py --migrate
```

Review every `[WARN]` before public launch, especially enabled payment gateways with incomplete keys/callback URLs.

## Backups

The running bot creates a database backup immediately when background tasks start and then every 6 hours while `auto_backup_enabled` is enabled. The seven newest automatic bot backups are retained.

Create a safe backup manually:

```bash
python scripts/db_backup.py
```

Restore a backup safely:

```bash
sudo bash scripts/restore_backup.sh /path/to/backup.db
```

The restore script validates the backup first and preserves the current database as a `before-restore` copy.

## Useful launch checks

```bash
sudo systemctl status pasarguard-admin-bot.service
sudo journalctl -u pasarguard-admin-bot.service -f
bash scripts/check_payment_callback.sh
```

For the current deployment where Xray owns server port 443 and Cloudflare terminates HTTPS, keep Nginx on port 80 and `NGINX_PAYMENT_ENABLE_HTTPS=0`.

## Known non-blocking limitation

The project contains seller commission storage/reporting helpers, but no explicit configurable commission percentage/business rule is defined yet. This release does not invent a commission percentage or automatically credit seller commission. Seller customer ownership, order visibility, and access scoping work independently of commission accounting.

## External end-to-end checks still required

Static, database, migration, backup, and local logic tests can be run without production credentials. Before public traffic, make one real low-value test for every gateway you intend to enable (Stars, Tetrapay, Plisio, card-to-card), verify the external callback reaches the server, and verify service creation on the production panel.
