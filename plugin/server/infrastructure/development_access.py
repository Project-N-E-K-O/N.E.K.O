"""Local-only, CSRF-resistant access to executable development directories."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        address = ip_address(host or "")
        return address.is_loopback or bool(
            getattr(address, "ipv4_mapped", None)
            and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def require_development_access(request: Request) -> None:
    # A non-simple header prevents cross-site forms. CORS alone does not stop
    # requests from executing, and the legacy require_admin is a placeholder.
    allowed = (
        request.client is not None
        and _is_loopback(request.client.host)
        and _is_loopback(request.url.hostname)
        and request.headers.get("x-neko-development") == "1"
    )
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            allowed = allowed and (
                parsed.scheme in {"http", "https"}
                and _is_loopback(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
            )
        except ValueError:
            allowed = False
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Development plugins require a local N.E.K.O page and local backend connection",
            headers={"X-Error-Code": "DEVELOPMENT_ACCESS_DENIED"},
        )
