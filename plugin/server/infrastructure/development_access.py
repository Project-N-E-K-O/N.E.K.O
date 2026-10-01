"""Local-only, CSRF-resistant access to executable development directories."""
import os
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


_DEFAULT_DEV_ORIGIN_PORTS = (48911, 48916, 5173)


def _read_runtime_port(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(fallback)
    try:
        port = int(raw)
    except ValueError:
        return int(fallback)
    return port if 1 <= port <= 65535 else int(fallback)


def _configured_development_origins() -> frozenset[str]:
    """Return the exact browser origins allowed to mutate development state.

    The packaged UI is served by the main server, while the development UI is
    also reachable from the embedded plugin server and the Vite dev server.
    Keep the compatibility ports explicit: accepting every loopback port makes
    an unrelated local service a CSRF origin.  Operators can replace this
    small set with ``NEKO_DEVELOPMENT_ALLOWED_ORIGINS`` when deploying a
    custom frontend, using complete ``scheme://host:port`` origins.
    """

    configured = os.getenv("NEKO_DEVELOPMENT_ALLOWED_ORIGINS", "")
    if configured.strip():
        candidates = (item.strip().rstrip("/") for item in configured.split(","))
    else:
        configured_plugin_port = 48916
        try:
            import config

            configured_plugin_port = int(config.USER_PLUGIN_SERVER_PORT)
            ports = {
                int(config.MAIN_SERVER_PORT),
                configured_plugin_port,
                *_DEFAULT_DEV_ORIGIN_PORTS,
            }
        except (AttributeError, TypeError, ValueError):
            ports = set(_DEFAULT_DEV_ORIGIN_PORTS)
        ports.add(_read_runtime_port("NEKO_USER_PLUGIN_SERVER_PORT", configured_plugin_port))
        candidates = (
            f"{scheme}://{host}:{port}"
            for scheme in ("http", "https")
            for host in ("localhost", "127.0.0.1", "[::1]")
            for port in ports
        )

    allowed: set[str] = set()
    for candidate in candidates:
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or not _is_loopback(parsed.hostname)
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.port is None
            ):
                continue
            host = parsed.hostname.lower()
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            allowed.add(f"{parsed.scheme}://{host}:{parsed.port}")
        except ValueError:
            continue
    return frozenset(allowed)


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
            normalized_origin = ""
            if parsed.hostname:
                host = parsed.hostname.lower()
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                normalized_origin = f"{parsed.scheme.lower()}://{host}:{parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)}"
            allowed = allowed and (
                parsed.scheme in {"http", "https"}
                and _is_loopback(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
                and normalized_origin in _configured_development_origins()
            )
        except ValueError:
            allowed = False
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Development plugins require a local N.E.K.O page and local backend connection",
            headers={"X-Error-Code": "DEVELOPMENT_ACCESS_DENIED"},
        )
