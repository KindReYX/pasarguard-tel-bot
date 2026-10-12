# v0.2.6

## Live edit recursion / duplicate prompt fix

- Fixed recursive live-edit controls appearing on the editor's own instruction/current-value messages.
- Removed the old dependency on an installation directory named `admin_bot_modular`; editor-internal detection is now project-relative and works under any deployment path.
- Added explicit output passthrough around critical editor UI messages as a second safety layer.
- Added per-admin edit-session de-duplication: repeated taps on the same text/button edit action no longer spawn duplicate prompts.
- Existing stable IDs, text overrides, button overrides, sticker mappings and premium emoji tokens are preserved; no destructive migration is performed.
