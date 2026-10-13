# v0.2.99

- Fixed package-template placeholders being rendered as `-` inside the admin template editor.
- Package template management now bypasses stale Live Edit overrides and always shows literal `{package_*}` variables.
- Added Premium Emoji support for package display names without polluting the panel-side package name.
- Package list, package detail and customer package buttons use the local Premium-Emoji-aware display name.
- Global and per-package button templates continue to support Premium Emoji button icons.
- Added validation for the global package button template so unknown variables cannot be saved accidentally.
- Added a safe `display_name` column migration to `package_settings`; existing package data is preserved.
