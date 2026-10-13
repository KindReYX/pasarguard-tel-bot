# v0.2.95

## UUID service adoption API fix

- Uses PasarGuard 5.4.1 native `GET /api/users?proxy_id=<uuid>` lookup.
- Fixes owner transfer endpoint to `PUT /api/user/{username}/set_owner?admin_username=...`.
- Service import now matches only by UUID; domains, subscription paths, display names, and usernames are not used for identification.
- Subscription URLs are still supported by extracting UUIDs from the returned configs first.
- Keeps duplicate/ambiguous-match protection and existing database ownership-transfer behavior.
