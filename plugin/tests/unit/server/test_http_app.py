from __future__ import annotations

import pytest

from plugin.server import http_app


pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self.routers: list[object] = []

    def include_router(self, router: object) -> None:
        self.routers.append(router)


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
