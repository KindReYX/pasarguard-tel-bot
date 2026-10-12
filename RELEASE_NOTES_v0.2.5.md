# v0.2.5

## Card receipt delivery retry

- Added an admin **Retry order delivery** button when a card-transfer payment is approved but service creation/delivery fails.
- Retry runs only the service build/delivery stage; it does not repeat payment registration, wallet deductions, coupon consumption, or other payment effects.
- Manual retry is idempotent and protected against repeated clicks.
- If the order is already `service_ready`, stale retry clicks do not rebuild the service or resend the customer delivery.
- If a service was already saved but the previous delivery step failed, the existing service is recovered and delivered instead of being created again.
- On successful retry, the customer receives the normal subscription delivery and after-purchase message, exactly like a successful initial approval.
- Related unresolved order errors are marked resolved after successful manual delivery.
- Failed retries keep the retry button available and preserve the already-approved payment state.
