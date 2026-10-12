# v0.2.4 — System Error Diagnostics Hotfix

Based strictly on v0.2.3.

- Added a destructive **Reset system errors** action for super admins. It hard-deletes all system error rows and resets the SQLite sequence; the next error starts at #1.
- Telegram `bot was blocked by the user` delivery failures are ignored by the system-error registry.
- Improved system-error diagnostics with human-readable categories for timeout, DNS, connection, rate-limit, upstream 502/503/504, authentication, permissions, not-found, validation, and remote 500 errors.
- Service creation/renewal errors now preserve the real root exception, operation stage, HTTP status when available, order/package context, retry attempt, and retry state.
- Transient service-build failures (timeout/network/DNS/429/502/503/504) remain queued and automatically retry instead of immediately being treated as a final admin-review failure.
- Removed the duplicate generic `card_transfer.approve_order / ServiceBuildError` when the detailed service-job error has already been recorded.
- Empty exception messages are no longer stored as blank errors.
