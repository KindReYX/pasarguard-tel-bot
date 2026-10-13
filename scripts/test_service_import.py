#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# DB_PATH is read when config/db are imported.
tmp = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(tmp.name) / 'test.db')
os.environ.setdefault('API_BASE_URL', 'http://example.invalid')
os.environ.setdefault('PANEL_ADMIN_USERNAME', 'botadmin')
os.environ.setdefault('PANEL_ADMIN_PASSWORD', 'x')

import api_client  # noqa: E402
import db  # noqa: E402


def main() -> None:
    db.init_db()

    vless = 'vless://11111111-2222-3333-4444-555555555555@example.com:443?security=tls#demo'
    fps = api_client.access_fingerprints(vless)
    assert '11111111-2222-3333-4444-555555555555' in fps
    user = {'proxies': {'vless': {'id': '11111111-2222-3333-4444-555555555555'}}, 'username': 'svc_a'}
    assert fps.intersection(api_client.user_access_fingerprints(user))

    service_id, old = db.adopt_panel_service(
        1001, 'svc_a', {'username': 'svc_a', 'status': 'active', 'data_limit': 1024},
        subscription_url='https://sub.example/svc_a', links=[vless],
    )
    assert old is None
    row = db.get_user_service(service_id, 1001)
    assert row and row['telegram_id'] == 1001
    assert row['package_name'] == 'سرویس واردشده'

    # Adopting the same panel user from a second Telegram account must MOVE the
    # service instead of duplicating it, and must not carry an old order link.
    db.execute('UPDATE user_services SET order_id=77, package_name=? WHERE id=?', ('بسته قبلی', service_id))
    moved_id, old = db.adopt_panel_service(
        2002, 'svc_a', {'username': 'svc_a', 'status': 'active', 'data_limit': 2048},
        subscription_url='https://sub.example/svc_a', links=[vless],
    )
    assert moved_id == service_id
    assert old == 1001
    assert db.get_user_service(service_id, 1001) is None
    moved = db.get_user_service(service_id, 2002)
    assert moved and moved['order_id'] is None
    assert moved['package_name'] == 'بسته قبلی'
    assert int(moved['data_limit']) == 2048

    count = db.fetchone("SELECT COUNT(*) AS c FROM user_services WHERE panel_username='svc_a' AND is_active=1")
    assert int(count['c']) == 1
    integrity = db.fetchone('PRAGMA integrity_check')
    assert integrity and list(integrity.values())[0] == 'ok'
    print('service import tests: PASS')


if __name__ == '__main__':
    main()
