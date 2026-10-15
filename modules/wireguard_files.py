"""Resolve a selected WireGuard config into its importable .conf contents."""

from __future__ import annotations

import base64
import io
import re
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

import aiohttp

import api_client

MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_CONFIG_BYTES = 512 * 1024


def is_wireguard_link(link: str) -> bool:
    return str(link or '').lower().startswith(('wireguard://', 'wg://'))


def _valid_config(body: bytes) -> bool:
    if len(body) > MAX_CONFIG_BYTES:
        return False
    try:
        text = body.decode('utf-8-sig')
    except UnicodeDecodeError:
        return False
    return all(re.search(pattern, text, re.I | re.M) for pattern in (
        r'^\s*\[Interface\]\s*$', r'^\s*\[Peer\]\s*$',
        r'^\s*PrivateKey\s*=', r'^\s*PublicKey\s*=',
    ))


def config_from_link(link: str) -> bytes | None:
    """Some panels embed the full wg-quick config in the URI authority."""
    if not is_wireguard_link(link):
        return None
    payload = str(link).split('://', 1)[1].split('#', 1)[0].split('?', 1)[0]
    payload = unquote(payload)
    candidates = [payload.encode('utf-8')]
    compact = payload.strip('/')
    if re.fullmatch(r'[A-Za-z0-9+/=_-]+', compact or '') and len(compact) < MAX_CONFIG_BYTES:
        try:
            candidates.append(base64.b64decode(compact.replace('-', '+').replace('_', '/') + '=' * (-len(compact) % 4), validate=True))
        except (ValueError, base64.binascii.Error):
            pass
    return next((body for body in candidates if _valid_config(body)), None)


def _slug(value: str) -> str:
    return re.sub(r'[^\w]+', '', str(value or '').casefold(), flags=re.UNICODE)


def config_from_archive(archive: bytes, link: str) -> bytes | None:
    """Choose the matching .conf, or the only config in the panel ZIP."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            configs = []
            for item in bundle.infolist():
                if item.is_dir() or not item.filename.lower().endswith('.conf') or item.file_size > MAX_CONFIG_BYTES:
                    continue
                body = bundle.read(item)
                if _valid_config(body):
                    configs.append((PurePosixPath(item.filename).stem, body))
    except (OSError, ValueError, zipfile.BadZipFile):
        return None
    if len(configs) == 1:
        return configs[0][1]
    if not configs:
        return None
    # Never guess by list position when a subscription contains several peers.
    name = _slug(unquote(urlparse(link).fragment))
    matches = [body for stem, body in configs if name and _slug(stem) == name]
    return matches[0] if len(matches) == 1 else None


async def get_wireguard_config(link: str, subscription_url: str | None) -> bytes | None:
    embedded = config_from_link(link)
    if embedded:
        return embedded
    url = urlsplit(str(subscription_url or '').strip())
    if not is_wireguard_link(link) or url.scheme not in {'http', 'https'} or not url.netloc:
        return None
    wireguard_url = urlunsplit((url.scheme, url.netloc, url.path.rstrip('/') + '/wireguard', url.query, ''))
    session = await api_client.get_shared_session()
    async with session.get(wireguard_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
        if response.status != 200:
            return None
        if response.content_length is not None and response.content_length > MAX_ARCHIVE_BYTES:
            return None
        archive = await response.content.read(MAX_ARCHIVE_BYTES + 1)
    return config_from_archive(archive, link) if len(archive) <= MAX_ARCHIVE_BYTES else None
