# v0.2.1

## Stable live-edit IDs and updater reliability

- Live-edit text IDs are now independent of the absolute install path.
- Live-edit text IDs no longer depend on surrounding source lines or line numbers.
- Existing legacy text overrides and stickers are migrated non-destructively to stable v2 IDs.
- Legacy keys are intentionally preserved as fallback/audit data.
- Startup performs an eager migration so pages do not need to be opened before a later update.
- Lazy read-through migration remains available for legacy targets discovered at runtime.
- Fixed a false updater failure/success race when the systemd update worker had already started.
- No runtime database, .env, backups, or user data are included in this release archive.
