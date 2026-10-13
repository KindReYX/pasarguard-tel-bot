# v0.2.97

- Fixed duplicate subscription links in service/test delivery when the customized delivery template already contains `{sub_link}`.
- Strengthened import ownership protection: bot-created and test services are now protected by proxy UUID as well as panel username.
- Import checks run before any panel owner change, so a service already registered to another Telegram user cannot be taken over.
- No destructive database migration.
