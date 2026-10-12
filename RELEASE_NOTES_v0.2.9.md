# v0.2.9

## Fixes

- Fixed public-priced packages being missing on the first visit to a product/category.
- The initial fresh `/api/user_templates/simple` response is now hydrated before rendering, not only cached responses.
- Existing users, products, packages, prices, custom texts, and UI overrides are not modified.
