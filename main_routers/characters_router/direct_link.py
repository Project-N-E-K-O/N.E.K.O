# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SSRF-safe direct-link audio download (pinned DNS, redirect
validation) used by /voice_clone_direct.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

import re
import io
import asyncio
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin
import aiohttp


_DIRECT_LINK_MAX_REDIRECTS = 10


_DIRECT_LINK_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class DirectLinkSecurityError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DirectLinkValidatedTarget:
    url: str
    hostname: str
    port: int
    addr_info: list


class _DirectLinkPinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, target: DirectLinkValidatedTarget):
        self._hostname = target.hostname.casefold()
        self._addr_info = list(target.addr_info)

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if (host or "").casefold() != self._hostname:
            raise OSError(f"unexpected direct_link DNS host: {host}")

        records = []
        for addr_family, socktype, proto, _canonname, sockaddr in self._addr_info:
            ip = sockaddr[0]
            resolved_port = sockaddr[1] if len(sockaddr) > 1 else port
            records.append({
                "hostname": host,
                "host": ip,
                "port": resolved_port,
                "family": addr_family,
                "proto": proto,
                "flags": 0,
            })
        return records

    async def close(self):
        return None


class _DirectLinkProbeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    async def aclose(self) -> None:
        return None


def _direct_link_hostname(target_url: str) -> str:
    parsed_url = urlparse(target_url)
    if parsed_url.scheme not in ("http", "https"):
        raise DirectLinkSecurityError("direct_link 必须是有效的HTTP/HTTPS链接", "INVALID_DIRECT_LINK")

    hostname = parsed_url.hostname
    if not hostname:
        raise DirectLinkSecurityError("direct_link 缺少主机名", "INVALID_DIRECT_LINK")
    if hostname.lower() == "localhost":
        raise DirectLinkSecurityError("direct_link 不能指向 localhost", "PRIVATE_IP_NOT_ALLOWED")
    return hostname


def _direct_link_port(target_url: str) -> int:
    parsed_url = urlparse(target_url)
    try:
        explicit_port = parsed_url.port
    except ValueError as exc:
        raise DirectLinkSecurityError("direct_link 端口无效", "INVALID_DIRECT_LINK") from exc
    if explicit_port is not None:
        return explicit_port
    return 443 if parsed_url.scheme == "https" else 80


def _assert_direct_link_addresses_safe(addr_info) -> None:
    import ipaddress

    for _, _, _, _, sockaddr in addr_info:
        ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
            or ip_obj.is_reserved
        ):
            raise DirectLinkSecurityError("direct_link 指向受限地址，已拒绝", "PRIVATE_IP_NOT_ALLOWED")


async def _validate_direct_link_target(target_url: str) -> DirectLinkValidatedTarget:
    hostname = _direct_link_hostname(target_url)
    port = _direct_link_port(target_url)

    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise DirectLinkSecurityError(
            f"direct_link 主机无法解析: {hostname}",
            "DIRECT_LINK_DNS_FAILED",
        ) from exc

    _assert_direct_link_addresses_safe(addr_info)
    return DirectLinkValidatedTarget(
        url=target_url,
        hostname=hostname,
        port=port,
        addr_info=addr_info,
    )


async def _redirect_target_from_response(response) -> DirectLinkValidatedTarget:
    location = response.headers.get("location")
    if not location:
        raise DirectLinkSecurityError("直链重定向响应缺少 Location 头", "DIRECT_LINK_REDIRECT_INVALID")

    next_url = urljoin(str(response.url), location)
    return await _validate_direct_link_target(next_url)


def _open_pinned_direct_link_session(target: DirectLinkValidatedTarget, *, timeout: float):
    resolver = _DirectLinkPinnedResolver(target)
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        ttl_dns_cache=0,
    )
    return aiohttp.ClientSession(
        connector=connector,
        connector_owner=True,
        timeout=aiohttp.ClientTimeout(total=timeout),
        trust_env=False,
    )


async def _request_direct_link_follow_redirects(
    method: str,
    direct_link: str,
    *,
    stream: bool = False,
    headers: dict[str, str] | None = None,
) -> _DirectLinkProbeResponse:
    target = await _validate_direct_link_target(direct_link)
    for _ in range(_DIRECT_LINK_MAX_REDIRECTS + 1):
        async with _open_pinned_direct_link_session(target, timeout=30) as session:
            async with session.request(
                method,
                target.url,
                headers=headers,
                allow_redirects=False,
            ) as response:
                status_code = response.status
                if stream:
                    response.release()
                else:
                    await response.read()
                if status_code in _DIRECT_LINK_REDIRECT_STATUSES:
                    target = await _redirect_target_from_response(response)
                    continue
                return _DirectLinkProbeResponse(status_code)
    raise DirectLinkSecurityError("直链重定向次数过多", "TOO_MANY_REDIRECTS")


async def _download_direct_link_audio(
    direct_link: str,
    *,
    max_file_size: int,
) -> tuple[str, bytes]:
    target = await _validate_direct_link_target(direct_link)
    for _ in range(_DIRECT_LINK_MAX_REDIRECTS + 1):
        async with _open_pinned_direct_link_session(target, timeout=60) as session:
            async with session.get(target.url, allow_redirects=False) as download_resp:
                if download_resp.status in _DIRECT_LINK_REDIRECT_STATUSES:
                    target = await _redirect_target_from_response(download_resp)
                    download_resp.release()
                    continue

                if download_resp.status != 200:
                    raise DirectLinkSecurityError(
                        f"直链下载失败，状态码: {download_resp.status}",
                        "DOWNLOAD_FAILED",
                    )

                filename = "audio.wav"
                content_disposition = download_resp.headers.get("content-disposition", "")
                if "filename=" in content_disposition:
                    match = re.search(r'filename=["\']?([^"\';]+)', content_disposition)
                    if match:
                        filename = match.group(1)
                else:
                    parsed = urlparse(str(download_resp.url))
                    path_filename = parsed.path.split("/")[-1]
                    if path_filename and "." in path_filename:
                        filename = path_filename

                audio_buffer = io.BytesIO()
                total_size = 0
                async for chunk in download_resp.content.iter_chunked(8192):
                    total_size += len(chunk)
                    if total_size > max_file_size:
                        raise DirectLinkSecurityError("音频文件超过100MB限制", "FILE_TOO_LARGE")
                    audio_buffer.write(chunk)

                return filename, audio_buffer.getvalue()

    raise DirectLinkSecurityError("直链重定向次数过多", "TOO_MANY_REDIRECTS")
