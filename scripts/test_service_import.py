#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(tmp.name) / 'test.db')
os.environ.setdefault('API_BASE_URL', 'http://example.invalid')
os.environ.setdefault('PANEL_ADMIN_USERNAME', 'botadmin')
os.environ.setdefault('PANEL_ADMIN_PASSWORD', 'x')

import api_client  # noqa: E402
import db  # noqa: E402

UUID = '11111111-2222-3333-4444-555555555555'


async def test_panel_uuid_lookup_and_owner_transfer() -> None:
    calls = []

    async def fake_request(method, path, params=None, payload=None, retries=3):
        calls.append((method, path, params, payload))
        if method == 'GET' and path == '/api/users':
            assert params.get('proxy_id') == UUID
            return {
                'users': [{
                    'id': 9,
                    'username': 'svc_a',
                    'status': 'active',
                    'admin': {'username': 'oldadmin'},
                    'proxy_settings': {'vless': {'id': UUID}},
                }],
                'total': 1,
            }
        if method == 'PUT' and path == '/api/user/svc_a/set_owner':
            assert params == {'admin_username': 'botadmin'}
            return {
                'id': 9,
                'username': 'svc_a',
                'status': 'active',
                'admin': {'username': 'botadmin'},
                'proxy_settings': {'vless': {'id': UUID}},
            }
        raise AssertionError(f'unexpected request: {method} {path} {params}')

    old_request = api_client.request
    api_client.request = fake_request
    try:
        got = await api_client.adopt_panel_user_for_bot(
            f'vless://{UUID}@example.com:443?security=tls#demo'
        )
    finally:
        api_client.request = old_request

    assert got['username'] == 'svc_a'
    assert got['admin']['username'] == 'botadmin'
    assert calls[0][0:2] == ('GET', '/api/users')
    assert calls[1][0:2] == ('PUT', '/api/user/svc_a/set_owner')


def test_db_adoption() -> None:
    db.init_db()
    vless = f'vless://{UUID}@example.com:443?security=tls#demo'
    assert UUID in api_client.extract_access_uuids(vless)

    service_id, old = db.adopt_panel_service(
        1001,
        'svc_a',
        {'username': 'svc_a', 'status': 'active', 'data_limit': 1024},
        subscription_url='https://sub.example/svc_a',
        links=[vless],
    )
    assert old is None
    row = db.get_user_service(service_id, 1001)
    assert row and row['telegram_id'] == 1001

    db.execute('UPDATE user_services SET order_id=77 WHERE id=?', (service_id,))
    try:
        db.adopt_panel_service(
            2002,
            'svc_a',
            {'username': 'svc_a', 'status': 'active', 'data_limit': 2048},
            subscription_url='https://sub.example/svc_a',
            links=[vless],
        )
    except LookupError as exc:
        assert str(exc) == 'service_registered_to_other_user'
    else:
        raise AssertionError('cross-user adoption must be rejected')
    original = db.get_user_service(service_id, 1001)
    assert original and original['order_id'] == 77
    assert db.get_user_service(service_id, 2002) is None

    # Even if the panel username changed, the same proxy UUID still belongs to
    # the original Telegram user and must not be adoptable by another user.
    try:
        db.adopt_panel_service(
            2002,
            'renamed_service',
            {'username': 'renamed_service', 'status': 'active'},
            subscription_url='https://sub.example/renamed_service',
            links=[vless],
        )
    except LookupError as exc:
        assert str(exc) == 'service_registered_to_other_user'
    else:
        raise AssertionError('same UUID under a different username must be rejected')


def main() -> None:
    asyncio.run(test_panel_uuid_lookup_and_owner_transfer())
    test_db_adoption()
    print('service import tests: PASS')


if __name__ == '__main__':
    main()
