# v0.2.94

## UUID-only service adoption fix

- Service import now matches strictly by UUID.
- UUID is extracted directly from configs or from configs fetched through a subscription URL.
- The resolver fetches full panel user details before matching, fixing panels where `/api/users` omits proxy UUIDs.
- Domain, subscription path, config title and display-name matching are no longer used.
- Ambiguous UUID matches are rejected safely.
