"""Regression coverage for automatic AnySearch selection in the host gateway."""

from __future__ import annotations

from collections import OrderedDict

import pytest

from utils.web_scraper import search_gateway


def _reset_gateway_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_gateway, "_cache", OrderedDict())
    monkeypatch.setattr(search_gateway, "_inflight", {})
    monkeypatch.setattr(search_gateway, "_waiters", {})
    monkeypatch.setattr(search_gateway, "_backend_locks", {})
    monkeypatch.setattr(search_gateway, "_failure_cooldown_until", {})
    monkeypatch.setattr(search_gateway, "_next_run_at", {})
    monkeypatch.setattr(search_gateway, "_runtime_loop", None)


@pytest.mark.asyncio
async def test_gateway_auto_reserves_anysearch_and_keeps_plugin_backend_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gateway_state(monkeypatch)
    calls: list[tuple[str, str | None, str | None]] = []

    async def invoke(
        _query: str,
        _limit: int,
        reservation_backend: str,
        _deadline: float,
        *,
        entry_backend: str | None = None,
        preferred_backend: str | None = None,
    ) -> dict[str, object]:
        calls.append((reservation_backend, entry_backend, preferred_backend))
        return {
            "success": True,
            "results": [{"title": "result", "url": "https://example.com", "abstract": ""}],
        }

    monkeypatch.setattr(search_gateway, "_invoke_in_backend_slot", invoke)

    result = await search_gateway.search_via_plugin("neko", limit=3)

    assert result["success"] is True
    assert calls == [("anysearch", None, None)]


@pytest.mark.asyncio
async def test_gateway_explicit_anysearch_remains_an_explicit_plugin_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gateway_state(monkeypatch)
    calls: list[tuple[str, str | None]] = []

    async def invoke(
        _query: str,
        _limit: int,
        reservation_backend: str,
        _deadline: float,
        *,
        entry_backend: str | None = None,
        preferred_backend: str | None = None,
    ) -> dict[str, object]:
        calls.append((reservation_backend, entry_backend))
        return {
            "success": True,
            "results": [{"title": "result", "url": "https://example.com", "abstract": ""}],
        }

    monkeypatch.setattr(search_gateway, "_invoke_in_backend_slot", invoke)

    result = await search_gateway.search_via_plugin("neko", limit=3, backend="anysearch")

    assert result["success"] is True
    assert calls == [("anysearch", "anysearch")]
