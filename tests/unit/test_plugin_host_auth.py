from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from utils import plugin_host_auth


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "   "])
def test_require_plugin_host_token_rejects_missing_value(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    monkeypatch.setattr(plugin_host_auth, "_plugin_host_token", "")
    if value is None:
        monkeypatch.delenv(plugin_host_auth.PLUGIN_HOST_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(plugin_host_auth.PLUGIN_HOST_TOKEN_ENV, value)

    with pytest.raises(RuntimeError, match="NEKO_PLUGIN_HOST_API_TOKEN"):
        plugin_host_auth.require_plugin_host_token()


@pytest.mark.unit
def test_require_plugin_host_token_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_host_auth, "_plugin_host_token", "")
    plugin_host_auth.configure_plugin_host_token("shared-split-token")

    assert plugin_host_auth.require_plugin_host_token() == "shared-split-token"


@pytest.mark.unit
def test_require_plugin_host_access_initializes_token_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_host_auth, "_plugin_host_token", "")
    monkeypatch.setenv(
        plugin_host_auth.PLUGIN_HOST_TOKEN_ENV,
        "environment-secret",
    )
    request = Request({
        "type": "http",
        "headers": [
            (
                plugin_host_auth.PLUGIN_HOST_TOKEN_HEADER.lower().encode("ascii"),
                b"environment-secret",
            ),
        ],
    })

    plugin_host_auth.require_plugin_host_access(request)

    assert plugin_host_auth.require_plugin_host_token() == "environment-secret"
    assert plugin_host_auth.PLUGIN_HOST_TOKEN_ENV not in plugin_host_auth.os.environ


@pytest.mark.unit
def test_configure_plugin_host_token_consumes_and_scrubs_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scrubbed: list[tuple[str, str]] = []
    monkeypatch.setattr(plugin_host_auth, "_plugin_host_token", "")
    monkeypatch.setenv(plugin_host_auth.PLUGIN_HOST_TOKEN_ENV, "environment-secret")
    monkeypatch.setattr(
        plugin_host_auth,
        "_scrub_process_environment_value",
        lambda name, value: scrubbed.append((name, value)),
    )

    plugin_host_auth.configure_plugin_host_token()

    assert plugin_host_auth.require_plugin_host_token() == "environment-secret"
    assert plugin_host_auth.PLUGIN_HOST_TOKEN_ENV not in plugin_host_auth.os.environ
    assert scrubbed == [
        (plugin_host_auth.PLUGIN_HOST_TOKEN_ENV, "environment-secret")
    ]


@pytest.mark.unit
def test_require_plugin_host_access_rejects_whitespace_only_expected_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_host_auth, "_plugin_host_token", "")
    plugin_host_auth.configure_plugin_host_token("   ")
    request = Request({
        "type": "http",
        "headers": [
            (
                plugin_host_auth.PLUGIN_HOST_TOKEN_HEADER.lower().encode("ascii"),
                b"   ",
            ),
        ],
    })

    with pytest.raises(HTTPException) as exc_info:
        plugin_host_auth.require_plugin_host_access(request)

    assert exc_info.value.status_code == 403
