import importlib

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    ("legacy_name", "implementation_name"),
    [
        ("utils.internal_http_client", "utils.http.internal_client"),
        ("utils.external_http_client", "utils.http.external_client"),
        ("utils.aiohttp_proxy_utils", "utils.http.aiohttp_proxy"),
        ("utils.url_utils", "utils.http.url"),
        ("utils.ssl_env_diagnostics", "utils.http.ssl_diagnostics"),
    ],
)
def test_legacy_http_module_is_implementation_module(legacy_name, implementation_name):
    legacy = importlib.import_module(legacy_name)
    implementation = importlib.import_module(implementation_name)

    assert legacy is implementation


@pytest.mark.unit
def test_legacy_internal_client_private_seam_is_shared(monkeypatch):
    legacy = importlib.import_module("utils.internal_http_client")
    implementation = importlib.import_module("utils.http.internal_client")
    sentinel = object()

    monkeypatch.setattr(legacy, "_fallback_client", sentinel)

    assert implementation._fallback_client is sentinel


@pytest.mark.unit
def test_legacy_external_client_singleton_seam_is_shared(monkeypatch):
    legacy = importlib.import_module("utils.external_http_client")
    implementation = importlib.import_module("utils.http.external_client")
    sentinel = object()

    monkeypatch.setattr(legacy, "_client", sentinel)

    assert implementation._client is sentinel
