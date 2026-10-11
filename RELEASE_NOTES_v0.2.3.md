# v0.2.3

This hotfix fixes contradictory GitHub updater status messages and keeps the installed updater helper synchronized with the checked-out release.

## Fixes

- Fixed false "update failed to start" messages followed by a successful update message.
- The systemd update launcher now uses non-blocking submission so stopping the bot service cannot kill the launcher before it returns.
- Clears stale update status/progress files before starting a new update.
- Adds a short compatibility grace period for older installed helpers.
- The update worker now refreshes the root-owned updater launcher and worker files after checkout, preventing old helper code from surviving future bot updates.
- Keeps the v0.2.2 one-time Stable Live Edit migration optimization and existing data-preservation behavior.

## Upgrade note

For the cleanest first upgrade to v0.2.3, update the server manually once with `sudo bash scripts/install_or_update.sh`. After that, future in-bot GitHub updates use the refreshed non-blocking helper automatically.
