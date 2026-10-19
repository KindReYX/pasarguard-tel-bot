"""Match imported panel users to sellable products through active panel groups."""
from __future__ import annotations

import asyncio

import api_client
import db


async def active_groups() -> dict[int, dict]:
    simple = await api_client.get_groups()
    async def full(row):
        if 'is_disabled' in row:
            return row
        return await api_client.request('GET', f'/api/group/{int(row["id"])}')
    rows = await asyncio.gather(*(full(row) for row in simple))
    return {int(row['id']): row for row in rows if not row.get('is_disabled')}


async def eligible_products(user_data: dict) -> list[dict]:
    """Only products with enabled, priced templates sharing a live user group."""
    groups = await active_groups()
    user_groups = {int(g) for g in (user_data.get('group_ids') or [])} & groups.keys()
    if not user_groups:
        return []
    rows = db.fetchall('''SELECT ps.template_id, ps.product_id, p.name, ps.sort_order
        FROM package_settings ps JOIN products p ON p.id=ps.product_id
        WHERE p.is_active=1 AND ps.is_visible=1
        ORDER BY ps.sort_order, ps.template_id''')
    async def check(row):
        template = await api_client.get_user_template(row['template_id'])
        template_groups = {int(g) for g in (template.get('group_ids') or [])} & groups.keys()
        if template.get('is_disabled') or not template_groups.intersection(user_groups):
            return None
        from modules.packs import public_price_for_pack
        meta = db.fetchone('SELECT * FROM package_settings WHERE template_id=?', (str(row['template_id']),)) or {}
        price = public_price_for_pack(row['product_id'], template) if int(meta.get('use_product_price') or 0) else int(meta.get('price_amount') or 0)
        return dict(row) if price > 0 else None
    checked = await asyncio.gather(*(check(row) for row in rows))
    products: dict[int, dict] = {}
    for row in checked:
        if row is not None:
            products.setdefault(int(row['product_id']), {'id': int(row['product_id']), 'name': row['name'], 'packages': []})['packages'].append(str(row['template_id']))
    return list(products.values())


def assign_service_package(service_id: int, telegram_id: int, template_id: str, name: str) -> bool:
    """Update classification only; retain panel snapshot, order and usage."""
    with db.connect() as conn:
        result = conn.execute('''UPDATE user_services SET package_id=?, package_name=?, updated_at=?
            WHERE id=? AND telegram_id=? AND is_active=1''',
            (str(template_id), name, db.now_iso(), int(service_id), int(telegram_id)))
        conn.commit()
        return result.rowcount == 1
