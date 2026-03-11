from urllib.parse import urlparse


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def should_bypass_proxy_for_url(url: str) -> bool:
    """Return True when the target URL must bypass local proxy settings."""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    return host in _LOOPBACK_HOSTS


def aiohttp_session_kwargs_for_url(url: str, *, default_trust_env: bool = True) -> dict[str, object]:
    """Build ClientSession kwargs that keep loopback traffic on a direct connection."""
    if should_bypass_proxy_for_url(url):
        # aiohttp only consults proxy environment variables when trust_env=True.
        return {"trust_env": False}
    return {"trust_env": default_trust_env}
