"""Market origins shared by the application and plugin server."""

import os

from utils.social_base import validate_http_url


def get_market_api_url() -> str:
    return validate_http_url(
        os.getenv("NEKO_MARKET_API_URL", os.getenv("NEKO_MARKET_URL", "https://market.project-neko.cn")),
        name="NEKO_MARKET_API_URL", allow_empty=True,
    )


def get_market_web_url() -> str:
    configured = os.getenv("NEKO_MARKET_WEB_URL")
    if configured is not None:
        return validate_http_url(configured, name="NEKO_MARKET_WEB_URL", allow_empty=True)
    api = get_market_api_url()
    if api.rstrip("/") in {"http://localhost:8000", "http://127.0.0.1:8000"}:
        return "http://localhost:5173"
    return api
