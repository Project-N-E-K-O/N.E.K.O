from __future__ import annotations

import ipaddress
import socket
from io import BytesIO

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from config import MAIN_SERVER_PORT

router = APIRouter(tags=["ip_qrcode"])


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

    def is_disallowed(addr: ipaddress.IPv4Address) -> bool:
        if addr in ipaddress.ip_network("198.18.0.0/15"):
            return True
        if addr.is_loopback or addr.is_link_local:
            return True
        return False

    def score(addr: ipaddress.IPv4Address) -> tuple[int, int]:
        if addr in ipaddress.ip_network("192.168.0.0/16"):
            return (0, int(addr))
        if addr in ipaddress.ip_network("10.0.0.0/8"):
            return (1, int(addr))
        if addr in ipaddress.ip_network("172.16.0.0/12"):
            return (2, int(addr))
        if addr.is_private:
            return (10, int(addr))
        return (100, int(addr))

    candidates: set[ipaddress.IPv4Address] = set()

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
            pass

    try:
        host = socket.gethostname()
        _h, _aliases, ips = socket.gethostbyname_ex(host)
        for ip in ips:
            try:
                addr = ipaddress.ip_address(ip)
                if isinstance(addr, ipaddress.IPv4Address):
                    candidates.add(addr)
            except Exception:
                continue
    except Exception:
        pass

    valid: list[ipaddress.IPv4Address] = []
    for addr in candidates:
        if not addr.is_private:
            continue
        if is_disallowed(addr):
            continue
        valid.append(addr)

    if not valid:
        return None

    valid.sort(key=score)
    best = valid[0]
    if is_rfc1918(best) or best.is_private:
        return str(best)
    return None


def _build_access_url(ip: str) -> str:
    return f"http://{ip}:{MAIN_SERVER_PORT}"


@router.get("/getipqrcode")
@router.post("/getipqrcode")
async def get_ip_qrcode():
    """Return a QR code (PNG) for opening the web UI on another device."""

    ip = _get_preferred_lan_ip()
    if not ip:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "no_lan_ip",
                "message": "无法获取本机局域网 IP，请检查网络连接/网卡配置。",
            },
        )

    url = _build_access_url(ip)

    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf)
        png_bytes = buf.getvalue()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "qrcode_generate_failed",
                "message": str(e),
                "url": url,
            },
        )
