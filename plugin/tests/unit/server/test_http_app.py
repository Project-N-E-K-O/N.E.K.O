from __future__ import annotations

import pytest
from starlette.requests import Request

from plugin.server import http_app


pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self.routers: list[object] = []

    def include_router(self, router: object) -> None:
        self.routers.append(router)


def _request(url: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": url.split(":", 1)[0],
            "path": "/api_key",
            "query_string": b"",
            "headers": [(b"host", url.split("//", 1)[1].encode("ascii"))],
            "server": ("testserver", 80),
        }
    )


def test_model_settings_redirect_uses_client_visible_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEKO_MAIN_SERVER_PUBLIC_ORIGIN", raising=False)

    assert http_app._model_settings_url(
        _request("http://192.168.1.25:48916"), 48911
    ) == "http://192.168.1.25:48911/api_key"


def test_model_settings_redirect_honors_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEKO_MAIN_SERVER_PUBLIC_ORIGIN", "https://neko.example.com:8443/"
    )

    assert http_app._model_settings_url(
        _request("http://127.0.0.1:48916"), 48911
    ) == "https://neko.example.com:8443/api_key"


def test_optional_router_does_not_swallow_import_attribute_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _App()

    def _import_module(_module_name: str) -> object:
        raise AttributeError("inner module bug")

    monkeypatch.setattr(http_app.importlib, "import_module", _import_module)

    with pytest.raises(AttributeError, match="inner module bug"):
        http_app._include_optional_router(
            app,
            module_name="plugin.plugins.optional_routes",
            label="optional routes",
        )

    assert app.routers == []
