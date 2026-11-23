"""Bounded provider response handling shared by both image protocols."""
import asyncio
import base64
import binascii
import ipaddress
import json
import math
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import aiohttp
import httpx
from yarl import URL

from .types import GeneratedImage, ImageGenerationError

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_PROVIDER_STATUS_ERRORS = {
    400: "provider_request_rejected",
    401: "provider_access_denied",
    402: "provider_quota_exceeded",
    403: "provider_access_denied",
    408: "provider_timeout",
    429: "provider_rate_limited",
    504: "provider_timeout",
}
_BILLING_ERROR_CODES = {"billing_hard_limit_reached", "Arrearage"}


def _retry_after_seconds(value: str) -> int | None:
    value = value.strip()
    if len(value) <= 6 and value.isascii() and value.isdecimal():
        return int(value)
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is not None:
            return max(0, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, OverflowError):
        pass
    return None


async def _provider_error(response) -> ImageGenerationError:
    """Classify explicit billing failures without retaining provider text."""
    status = response.status_code
    if status in {400, 403, 429} and response.headers.get("content-encoding", "identity").strip().lower() == "identity":
        body = bytearray()
        try:
            async with asyncio.timeout(2):
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if len(body) + len(chunk) > 8192:
                        break
                    body.extend(chunk)
                else:
                    payload = json.loads(body)
                    if isinstance(payload, dict):
                        error = payload.get("error")
                        details = error if isinstance(error, dict) else payload
                        code, message = details.get("code"), details.get("message")
                        if isinstance(code, str) and (
                            code in _BILLING_ERROR_CODES
                            or (
                                code in {"insufficient_quota", "Throttling.AllocationQuota"}
                                and isinstance(message, str)
                                and any(phrase in message.lower() for phrase in
                                        ("quota has been exhausted", "free allocated quota exceeded"))
                            )
                        ):
                            return ImageGenerationError("provider_quota_exceeded")
        except (ValueError, RecursionError, TimeoutError, httpx.HTTPError):
            pass
    fallback = "provider_unavailable" if status >= 500 else "provider_http_error"
    retry_after = _retry_after_seconds(response.headers.get("Retry-After", "")) if status == 429 else None
    return ImageGenerationError(_PROVIDER_STATUS_ERRORS.get(status, fallback), retry_after=retry_after)


async def request_json(client, method, url, *, key, **kwargs):
    headers = {"Authorization": f"Bearer {key}", **kwargs.pop("headers", {}), "Accept-Encoding": "identity"}
    # generate_image owns the total deadline, including every polling request.
    async with client.stream(method, url, headers=headers, follow_redirects=False, timeout=None, **kwargs) as response:
        if response.status_code < 200 or response.status_code >= 300:
            raise await _provider_error(response)
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


class _ImageResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, hostname, records):
        self.hostname = hostname
        self.records = records

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host.casefold() != self.hostname.casefold():
            raise OSError("Unexpected image host")
        return [{"hostname": host, "host": addr[0], "port": addr[1], "family": af,
                 "proto": proto, "flags": socket.AI_NUMERICHOST}
                for af, _, proto, _, addr in self.records]

    async def close(self):
        pass


async def read_generated_image(image: GeneratedImage, *, limit: int) -> bytes:
    if image.data is not None:
        if not image.data or len(image.data) > limit:
            raise ImageGenerationError("invalid_image_data")
        return image.data
    url = parse_image({"url": image.url}).url
    parsed = urlsplit(url)
    try:
        async with asyncio.timeout(20):
            records = await asyncio.get_running_loop().getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM,
            )
            addresses = [ipaddress.ip_address(record[4][0]) for record in records]
            addresses = [getattr(address, "ipv4_mapped", None) or address for address in addresses]
            if not addresses or any(not ip.is_global or ip.is_multicast for ip in addresses):
                raise ImageGenerationError("invalid_image_url")
            # Use the checked addresses for the connection and keep TLS/Host
            # verification against the provider hostname. No redirects or key.
            resolver = _ImageResolver(parsed.hostname, records)
            connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
            async with aiohttp.ClientSession(connector=connector, trust_env=False, auto_decompress=False) as client:
                async with client.get(URL(url, encoded=True), allow_redirects=False,
                                      headers={"Accept-Encoding": "identity"}) as response:
                    if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise ImageGenerationError("image_download_failed")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ImageGenerationError("response_too_large")
                    return bytes(data)
    except (TimeoutError, OSError, ValueError, aiohttp.ClientError):
        raise ImageGenerationError("image_download_failed") from None
