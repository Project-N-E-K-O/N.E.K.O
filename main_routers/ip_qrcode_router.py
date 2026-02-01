from __future__ import annotations

import ipaddress
import logging
import socket
import os
import subprocess
import sys
import re
from io import BytesIO
from typing import Any, cast

try:
    import qrcode as _qrcode
    from qrcode.constants import ERROR_CORRECT_M

    _ERROR_CORRECT_M: Any | None = ERROR_CORRECT_M
    _QRCODE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _qrcode = None
    _ERROR_CORRECT_M = None
    _QRCODE_AVAILABLE = False

try:
    import ifaddr as _ifaddr  # pyright: ignore[reportMissingImports]

    _IFADDR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ifaddr = None
    _IFADDR_AVAILABLE = False

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from config import MAIN_SERVER_PORT
from config import MAIN_SERVER_HOST

router = APIRouter(tags=["ip_qrcode"])
logger = logging.getLogger(__name__)


_IFCONFIG_INET_RE = re.compile(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})\b")
_IPCONFIG_IPV4_RE = re.compile(r"\bIPv4[^:\n]*:\s*(\d{1,3}(?:\.\d{1,3}){3})\b", re.IGNORECASE)
_DOCKER_LIKE_IFACE_RE = re.compile(
    r"(?:^|[\s._-])(?:docker|docker0|br-|veth|wsl|vmnet|vbox|virtualbox|utun|tun|tap)(?:$|[\s._-])",
    re.IGNORECASE,
)

_DOCKER_LIKE_IPV4_NETS = [
    # Docker default bridges (Linux)
    ipaddress.ip_network("172.17.0.0/16"),
    ipaddress.ip_network("172.18.0.0/16"),
    ipaddress.ip_network("172.19.0.0/16"),
    # Common docker/custom bridge pools (still within 172.16/12)
    ipaddress.ip_network("172.20.0.0/14"),  # 172.20-23
    ipaddress.ip_network("172.24.0.0/14"),  # 172.24-27
    ipaddress.ip_network("172.28.0.0/14"),  # 172.28-31
    # Docker Desktop internal networks (macOS/Windows)
    ipaddress.ip_network("192.168.65.0/24"),
    ipaddress.ip_network("192.168.99.0/24"),
]


def _get_ipv4_candidates_from_env() -> set[ipaddress.IPv4Address]:
    """Allow users to override LAN IP detection.

    Env:
    - NEKO_LAN_IP / NEKO_QR_LAN_IP: explicit IPv4 used to build the QR URL.
    """
    candidates: set[ipaddress.IPv4Address] = set()
    for key in ("NEKO_QR_LAN_IP", "NEKO_LAN_IP"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw)
            if isinstance(addr, ipaddress.IPv4Address):
                candidates.add(addr)
        except Exception:
            logger.warning("Invalid %s=%r, ignored", key, raw)
    return candidates


def _get_ipv4_candidates_from_ifaddr() -> set[ipaddress.IPv4Address]:
    """Cross-platform enumeration of interface IPv4 addresses (best effort)."""
    candidates: set[ipaddress.IPv4Address] = set()
    if not _IFADDR_AVAILABLE:
        return candidates
    try:
        assert _ifaddr is not None
        for adapter in _ifaddr.get_adapters():
            for ip in adapter.ips:
                # ifaddr uses str for IPv4, tuple for IPv6
                if isinstance(ip.ip, str):
                    try:
                        addr = ipaddress.ip_address(ip.ip)
                        if isinstance(addr, ipaddress.IPv4Address):
                            candidates.add(addr)
                    except Exception:
                        continue
    except Exception:
        logger.debug("Failed to enumerate IPs via ifaddr", exc_info=True)
    return candidates


def _get_ipv4_candidates_from_ifaddr_with_iface_flags() -> tuple[set[ipaddress.IPv4Address], set[ipaddress.IPv4Address]]:
    """Return (all_candidates, docker_like_candidates) with interface-name heuristics."""
    all_candidates: set[ipaddress.IPv4Address] = set()
    docker_like: set[ipaddress.IPv4Address] = set()
    if not _IFADDR_AVAILABLE:
        return all_candidates, docker_like
    try:
        assert _ifaddr is not None
        for adapter in _ifaddr.get_adapters():
            raw_iface_name = getattr(adapter, "nice_name", None) or getattr(adapter, "name", None)
            iface_name = (str(raw_iface_name) if raw_iface_name is not None else str(adapter)).strip()
            iface_is_docker_like = bool(iface_name and _DOCKER_LIKE_IFACE_RE.search(iface_name))
            for ip in adapter.ips:
                if isinstance(ip.ip, str):
                    try:
                        addr = ipaddress.ip_address(ip.ip)
                        if isinstance(addr, ipaddress.IPv4Address):
                            all_candidates.add(addr)
                            if iface_is_docker_like:
                                docker_like.add(addr)
                    except Exception:
                        continue
    except Exception:
        logger.debug("Failed to enumerate IPs via ifaddr", exc_info=True)
    return all_candidates, docker_like


def _get_ipv4_candidates_from_ifconfig() -> set[ipaddress.IPv4Address]:
    """Best-effort enumerate IPv4 addresses from system networking tools.

    This is a fallback for environments where:
    - UDP "probe connect" cannot determine the outbound interface (no default route),
    - hostname resolution returns only loopback (common on macOS).
    """
    candidates: set[ipaddress.IPv4Address] = set()
    if sys.platform == "win32":
        return candidates

    try:
        # `ifconfig` exists on macOS and most Unix-like environments.
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
        for m in _IFCONFIG_INET_RE.finditer(out):
            try:
                addr = ipaddress.ip_address(m.group(1))
                if isinstance(addr, ipaddress.IPv4Address):
                    candidates.add(addr)
            except Exception:
                continue
    except Exception:
        # Don't log as warning to avoid spamming logs on platforms without ifconfig.
        return candidates

    return candidates


def _get_ipv4_candidates_from_ipconfig() -> set[ipaddress.IPv4Address]:
    """Windows fallback when ifaddr is not available."""
    candidates: set[ipaddress.IPv4Address] = set()
    if sys.platform != "win32":
        return candidates
    try:
        out = subprocess.check_output(["ipconfig"], text=True, stderr=subprocess.DEVNULL, encoding="utf-8", errors="ignore")
        for m in _IPCONFIG_IPV4_RE.finditer(out):
            try:
                addr = ipaddress.ip_address(m.group(1))
                if isinstance(addr, ipaddress.IPv4Address):
                    candidates.add(addr)
            except Exception:
                continue
    except Exception:
        return candidates
    return candidates


def _get_preferred_lan_ip() -> str | None:
    """Best-effort pick an IP that other devices on the LAN can reach.

    Strategy:
    - Collect candidate IPv4 addresses from multiple sources.
    - Prefer RFC1918 addresses (192.168/10/172.16-31).
    - Avoid proxy/virtual adapter ranges like 198.18.0.0/15.
    """

    def is_rfc1918(addr: ipaddress.IPv4Address) -> bool:
        return (
            addr in ipaddress.ip_network("192.168.0.0/16")
            or addr in ipaddress.ip_network("10.0.0.0/8")
            or addr in ipaddress.ip_network("172.16.0.0/12")
        )

    def is_docker_like_subnet(addr: ipaddress.IPv4Address) -> bool:
        for net in _DOCKER_LIKE_IPV4_NETS:
            if addr in net:
                return True
        return False

    def is_disallowed(addr: ipaddress.IPv4Address) -> bool:
        if addr in ipaddress.ip_network("198.18.0.0/15"):
            return True
        if addr.is_loopback or addr.is_link_local:
            return True
        if addr.is_multicast or addr.is_unspecified:
            return True
        return False

    def score(addr: ipaddress.IPv4Address, docker_like: bool) -> tuple[int, int]:
        # De-prioritize docker/vm/tunnel networks: they are often not reachable
        # from other LAN devices even though they are "private".
        if docker_like or is_docker_like_subnet(addr):
            return (50, int(addr))
        if addr in ipaddress.ip_network("192.168.0.0/16"):
            return (0, int(addr))
        if addr in ipaddress.ip_network("10.0.0.0/8"):
            return (1, int(addr))
        if addr in ipaddress.ip_network("172.16.0.0/12"):
            return (2, int(addr))
        # RFC6598 (CGNAT) - not RFC1918, but often used for "LAN-like" networks.
        if addr in ipaddress.ip_network("100.64.0.0/10"):
            return (3, int(addr))
        if addr.is_private:
            return (10, int(addr))
        if addr.is_global:
            return (50, int(addr))
        return (100, int(addr))

    candidates: set[ipaddress.IPv4Address] = set()
    docker_like_candidates: set[ipaddress.IPv4Address] = set()

    # Highest priority: user override
    candidates |= _get_ipv4_candidates_from_env()

    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((probe, 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
            addr = ipaddress.ip_address(ip)
            if isinstance(addr, ipaddress.IPv4Address):
                candidates.add(addr)
        except Exception:
            logger.warning("Failed to probe LAN IP via %s", probe, exc_info=True)

    try:
        host = socket.gethostname()
        _h, _aliases, ips = socket.gethostbyname_ex(host)
        for ip in ips:
            try:
                addr = ipaddress.ip_address(ip)
                if isinstance(addr, ipaddress.IPv4Address):
                    candidates.add(addr)
            except Exception:
                logger.warning("Failed to parse hostname IP %s", ip, exc_info=True)
                continue
    except Exception:
        logger.warning("Failed to resolve hostname IPs", exc_info=True)

    # Cross-platform interface enumeration (preferred fallback).
    ifaddr_all, ifaddr_docker_like = _get_ipv4_candidates_from_ifaddr_with_iface_flags()
    candidates |= ifaddr_all
    docker_like_candidates |= ifaddr_docker_like

    # Last-ditch fallbacks using OS tools (when ifaddr isn't installed).
    candidates |= _get_ipv4_candidates_from_ipconfig()
    candidates |= _get_ipv4_candidates_from_ifconfig()

    valid_non_docker: list[ipaddress.IPv4Address] = []
    valid_docker_like: list[ipaddress.IPv4Address] = []
    for addr in candidates:
        if is_disallowed(addr):
            continue
        # Prefer non-docker addresses when possible.
        if addr in docker_like_candidates or is_docker_like_subnet(addr):
            valid_docker_like.append(addr)
        else:
            valid_non_docker.append(addr)

    valid = valid_non_docker if valid_non_docker else valid_docker_like
    if not valid:
        return None

    valid.sort(key=lambda a: score(a, docker_like=(a in docker_like_candidates)))
    best = valid[0]
    # If we have *any* non-disallowed IPv4, it's usable for building a URL.
    # (RFC1918 is preferred; see `score()`.)
    if is_disallowed(best):
        return None
    return str(best)


def _build_access_url(ip: str) -> str:
    return f"http://{ip}:{MAIN_SERVER_PORT}"


@router.get("/getipqrcode")
async def get_ip_qrcode():
    """Return a QR code (PNG) for opening the web UI on another device."""

    # If server is bound to localhost only, the LAN QR will not work anyway.
    # Provide a clearer message so users know what to change.
    if MAIN_SERVER_HOST in ("127.0.0.1", "localhost"):
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": "server_localhost_only",
                "message": "服务器仅监听 127.0.0.1，局域网设备无法访问。请设置 NEKO_DEV_MODE=1（或将监听地址改为 0.0.0.0）后重启。",
            },
        )

    if not _QRCODE_AVAILABLE:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": "qrcode_unavailable",
                "message": "QR 码生成库未安装",
            },
        )

    ip = _get_preferred_lan_ip()
    if not ip:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": "no_lan_ip",
                "message": "无法获取本机局域网 IP，请检查网络连接/网卡配置。",
            },
        )

    url = _build_access_url(ip)

    try:
        assert _qrcode is not None
        assert _ERROR_CORRECT_M is not None
        qr = _qrcode.QRCode(
            version=None,
            error_correction=cast(Any, _ERROR_CORRECT_M),
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf)
        png_bytes = buf.getvalue()
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                # Let frontend show the URL under the QR code.
                "X-Neko-Access-Url": url,
                # Allow reading this header in cross-origin fetch.
                "Access-Control-Expose-Headers": "X-Neko-Access-Url",
            },
        )
    except Exception as e:
        logger.exception("QR code generation failed: %s", e)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": "qrcode_generate_failed",
                "message": str(e),
            },
        )
