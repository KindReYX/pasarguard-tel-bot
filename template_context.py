from __future__ import annotations

from typing import Any


def _fmt_toman(value: Any) -> str:
    try:
        return f"{int(value or 0):,} تومان"
    except Exception:
        return ''


def _fmt_bytes(value: Any) -> str:
    try:
        n = int(value or 0)
    except Exception:
        return ''
    if n <= 0:
        return ''
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    f = float(n); i = 0
    while f >= 1024 and i < len(units)-1:
        f /= 1024; i += 1
    text = f'{f:.2f}'.rstrip('0').rstrip('.')
    return f'{text} {units[i]}'


def enrich_template_context(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one shared context for every editable template in the bot.

    Any known variable is accepted everywhere. When identifiers are present we enrich
    the context from the local DB so a caller no longer needs to manually repeat the
    same package/product/order/service values on each screen.
    """
    ctx = dict(context or {})
    try:
        from modules.variables import all_variable_defaults
        merged = all_variable_defaults()
    except Exception:
        merged = {}
    merged.update(ctx)
    ctx = merged

    alias_groups = (
        ('user_id', 'telegram_id'),
        ('order_id', 'entity_id'),
        ('service_name', 'service_username', 'customer_username', 'panel_username'),
        ('status', 'final_status'),
        ('sub_link', 'subscription_url'),
        ('ref_link', 'referral_link'),
        ('package_id', 'template_id'),
        ('volume_gb', 'traffic_gb', 'data_gb', 'gb'),
    )
    for group in alias_groups:
        value = next((ctx.get(k) for k in group if ctx.get(k) not in (None, '')), None)
        if value is not None:
            for key in group:
                if ctx.get(key) in (None, ''):
                    ctx[key] = value

    try:
        import db
        # Service -> order/package/user details.
        sid = ctx.get('service_id')
        if sid not in (None, ''):
            try:
                service = db.get_user_service(int(sid)) or {}
            except Exception:
                service = {}
            for target, source in (
                ('user_id','telegram_id'), ('order_id','order_id'), ('package_id','package_id'),
                ('service_name','service_username'), ('panel_username','panel_username'),
                ('sub_link','subscription_url'), ('package_name','package_name'),
            ):
                if ctx.get(target) in (None, '') and service.get(source) not in (None, ''):
                    ctx[target] = service.get(source)

        # Order -> package/user/payment details.
        oid = ctx.get('order_id') or ctx.get('entity_id')
        if oid not in (None, ''):
            try:
                order = db.get_order(int(oid)) or {}
            except Exception:
                order = {}
            mapping = {
                'user_id':'user_id', 'package_id':'package_id', 'package_name':'package_name',
                'amount':'amount', 'stars_amount':'stars_amount', 'status':'status', 'created_at':'created_at',
                'service_name':'service_username', 'price_raw':'amount',
            }
            for target, source in mapping.items():
                if ctx.get(target) in (None, '') and order.get(source) not in (None, ''):
                    ctx[target] = order.get(source)

        # Package meta -> canonical bot display name + product.
        package_id = str(ctx.get('package_id') or ctx.get('template_id') or '').strip()
        if package_id:
            meta = db.fetchone('SELECT * FROM package_settings WHERE template_id=?', (package_id,)) or {}
            display_name = str(meta.get('display_name') or '').strip()
            if display_name:
                ctx['package_name'] = display_name
                if not ctx.get('name'):
                    ctx['name'] = display_name
            if not ctx.get('product_id') and meta.get('product_id'):
                ctx['product_id'] = str(meta.get('product_id'))
            if ctx.get('price_raw') in (None, '') and meta.get('price_amount') is not None:
                ctx['price_raw'] = str(int(meta.get('price_amount') or 0))
                ctx['price'] = _fmt_toman(meta.get('price_amount'))
                ctx['package_price_raw'] = ctx['price_raw']
                ctx['package_price'] = ctx['price']

            if package_id.startswith('custom:'):
                parts = package_id.split(':')
                if len(parts) >= 5:
                    ctx['custom_volume_gb'] = parts[2]
                    ctx['custom_months'] = parts[3]
                    ctx['custom_users'] = parts[4]
                    if not ctx.get('volume_gb'):
                        ctx['volume_gb'] = parts[2]

        # Product values.
        pid = ctx.get('product_id')
        if pid not in (None, '', '-'):
            try:
                product = db.fetchone('SELECT * FROM products WHERE id=?', (int(pid),)) or {}
            except Exception:
                product = {}
            if product:
                if not ctx.get('product_name'):
                    ctx['product_name'] = str(product.get('name') or '')
                raw = int(product.get('public_price_amount') or 0)
                ctx['product_public_price_per_gb_raw'] = str(raw)
                ctx['product_public_price_per_gb'] = _fmt_toman(raw) if raw > 0 else ''

        # User values from local user row when only user_id is available.
        uid = ctx.get('user_id') or ctx.get('telegram_id')
        if uid not in (None, '', '-'):
            try:
                user = db.fetchone('SELECT * FROM bot_users WHERE telegram_id=?', (int(uid),)) or {}
            except Exception:
                user = {}
            if user:
                first = str(user.get('first_name') or '')
                uname = str(user.get('username') or '')
                if not ctx.get('first_name'): ctx['first_name'] = first
                if not ctx.get('username'): ctx['username'] = ('@' + uname) if uname and not uname.startswith('@') else uname
                if not ctx.get('name'): ctx['name'] = first or ctx.get('username') or str(uid)

        # Final aliases again after enrichment.
        for group in alias_groups:
            value = next((ctx.get(k) for k in group if ctx.get(k) not in (None, '')), None)
            if value is not None:
                for key in group:
                    if ctx.get(key) in (None, ''):
                        ctx[key] = value
    except Exception:
        pass

    # Friendly derived formatting where raw values exist.
    if ctx.get('price_raw') not in (None, '') and not ctx.get('price'):
        ctx['price'] = _fmt_toman(ctx.get('price_raw'))
    if ctx.get('data_limit_raw') not in (None, '') and not ctx.get('data_limit'):
        ctx['data_limit'] = _fmt_bytes(ctx.get('data_limit_raw'))
    if ctx.get('stars_amount') not in (None, '') and not ctx.get('stars'):
        ctx['stars'] = str(ctx.get('stars_amount'))
    return ctx
