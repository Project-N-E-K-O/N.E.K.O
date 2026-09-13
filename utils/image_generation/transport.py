"""Bounded provider response handling shared by both image protocols."""
import asyncio
import base64
import binascii
import ipaddress
import json
from urllib.parse import urlsplit

import httpx

from .types import GeneratedImage, ImageGenerationError

MAX_RESPONSE_BYTES = 32 * 1024 * 1024


async def request_json(client, method, url, *, key, **kwargs):
    headers = {"Authorization": f"Bearer {key}", **kwargs.pop("headers", {}), "Accept-Encoding": "identity"}
    # generate_image owns the total deadline, including every polling request.
    async with client.stream(method, url, headers=headers, follow_redirects=False, timeout=None, **kwargs) as response:
        if response.status_code < 200 or response.status_code >= 300:
            raise ImageGenerationError("provider_http_error")
        # Reject before aiter_bytes can allocate decompressed transport chunks.
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise ImageGenerationError("unsupported_content_encoding")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ImageGenerationError("response_too_large")
            body.extend(chunk)
    try:
        result = await asyncio.to_thread(json.loads, body)
    except (ValueError, UnicodeError, RecursionError):
        raise ImageGenerationError("invalid_response") from None
    if not isinstance(result, dict):
        raise ImageGenerationError("invalid_response")
    return result


def parse_image(item):
    if not isinstance(item, dict):
        raise ImageGenerationError("invalid_response")
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ImageGenerationError("invalid_image_data") from None
        if not data:
            raise ImageGenerationError("invalid_image_data")
        return GeneratedImage(data=data)
    url = item.get("url")
    try:
        p = urlsplit(url) if isinstance(url, str) and len(url) <= 8192 else None
        if not p or p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
            raise ValueError()
        httpx.URL(url)
        p.port
        host = p.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        if any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise ImageGenerationError("invalid_image_url") from None
    return GeneratedImage(url=url)
