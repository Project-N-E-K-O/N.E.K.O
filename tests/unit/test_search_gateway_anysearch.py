"""Regression coverage for automatic AnySearch selection in the host gateway."""

from __future__ import annotations

import asyncio
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
async def test_gateway_auto_uses_neutral_scope_and_keeps_plugin_backend_auto(
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
    assert calls == [("auto", None, None)]


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


@pytest.mark.asyncio
async def test_gateway_slot_throttles_using_its_reservation_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gateway_state(monkeypatch)
    calls: list[tuple[str | None, str | None]] = []

    async def invoke(
        _query: str,
        _limit: int,
        backend: str | None,
        preferred_backend: str | None,
        *,
        run_timeout: float,
    ) -> dict[str, object]:
        calls.append((backend, preferred_backend))
        return {
            "success": True,
            "results": [{"title": "result", "url": "https://example.com", "abstract": ""}],
        }

    monkeypatch.setattr(search_gateway, "_invoke_plugin", invoke)
    deadline = asyncio.get_running_loop().time() + 1

    result = await search_gateway._invoke_in_backend_slot(
        "neko",
        3,
        "auto",
        deadline,
    )

    assert result["success"] is True
    assert calls == [(None, None)]
    assert "auto" in search_gateway._next_run_at
    assert "anysearch" not in search_gateway._next_run_at
