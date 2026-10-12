# v0.2.7

## Public Price Visibility Fix

- Fixed packages disappearing from the customer purchase list after enabling product public per-GB pricing.
- Public-priced packages now hydrate missing template details from the panel detail cache/API when the simple template list omits `data_limit`.
- Prevented enabling public pricing when the product public per-GB rate is zero.
- Prevented enabling public pricing for templates with zero or invalid traffic volume.
- Existing package, product, user, order, text override, and pricing data are preserved.
