# v0.2.2

This hotfix improves bot restart speed and makes the Stable Live Edit migration a one-time, crash-safe operation.

## Changes

- Fixed slow bot startup/restarts caused by rescanning 1,400+ editable call-sites on every launch
- Stable Live Edit migration now runs only once
- Added a persistent database completion marker after a successful migration
- The marker is written only after the full migration completes successfully
- If startup is interrupted, migration safely retries on the next launch
- Optimized legacy override migration by loading editable settings in a single SQLite read
- Cached source parsing during migration to avoid reparsing the same files hundreds of times
- Writes migrated values and the completion marker in one transaction
- Existing legacy text/sticker override keys are never deleted
- Existing customized texts remain backward-compatible through legacy fallbacks
- Subsequent restarts skip the migration immediately

## Upgrade Safety

The migration is non-destructive. Existing user data, settings, editable text overrides, sticker mappings, orders, wallets, services, and other runtime database records are preserved.

A database backup before production upgrades is still recommended.
