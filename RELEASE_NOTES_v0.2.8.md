# v0.2.8

This release fixes updater progress recovery after service restarts and restores compatibility with legacy custom invoice variables.

## Changes

- Update progress now resumes on the same Telegram message after the bot service restarts during an update.
- The freshly started bot polls the updater worker every 2 seconds while an update is active instead of leaving the message stuck at 5%.
- Final updater status is still consumed from the independent updater worker and clears the persisted progress target normally.
- Added backward-compatible invoice aliases for custom texts created by older versions:
  - `{entity_id}` -> order ID
  - `{customer_username}` -> service username
  - `{final_status}` -> order status
- Existing custom texts, Stable IDs, Premium Emoji IDs, users, orders, products, packages and pricing are not reset or rewritten.
