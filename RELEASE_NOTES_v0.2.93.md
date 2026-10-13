# v0.2.93

## Existing Service Import / Adoption

- Added `➕ اضافه کردن کانفیگ` to **My Services**.
- Customers can submit a subscription URL or one existing config (VLESS, VMess, Trojan, Shadowsocks, etc.).
- The bot resolves the matching panel user without logging the submitted secret/config.
- If the panel service is owned by another panel admin, ownership is transferred to the bot's configured full-access panel admin (`PANEL_ADMIN_USERNAME`).
- The service is then attached to the submitting Telegram customer and appears in **My Services**.
- If the same service was locally attached to another Telegram customer, local ownership is moved instead of creating a duplicate active service.
- Old financial/order history is not transferred to the new Telegram owner; a moved service has its old `order_id` detached.
- Imported services can use the normal service actions (refresh, configs, revoke link, renew, transfer, delete, auto-renew toggle).
- Renewal does not require knowing the original package: the customer can choose any currently visible renewal package.
- Ambiguous matches are rejected rather than transferring the wrong service.
- Added a service-import regression test and SQLite integrity check.

## Data Safety

- No destructive database migration.
- Existing users, orders, wallets, packages, prices, coupons, UI texts and Premium Emoji settings are preserved.
