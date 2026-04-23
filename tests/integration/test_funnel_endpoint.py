# -*- coding: utf-8 -*-
"""
Integration tests for the `/api/memory/funnel/{lanlan_name}` endpoint
introduced in PR-4 (memory-evidence-rfc §3.10).

Why these tests exist alongside the unit tests on `funnel_counts`:
  - The unit tests cover the scan + aggregate algorithm directly on the
    pure function.
  - These integration tests prove the HTTP wrapper (query-param parsing,
    default window, `asyncio.to_thread` hop, HTTPException on bad input)
    works end-to-end against a minimal isolated FastAPI instance. They
    DO NOT boot the full `memory_server` app (which would drag in the
    startup hook's bootstrap + component init chain); instead they build
    a tiny FastAPI app that mounts only the funnel route, which is the
    lowest-cost way to exercise the wrapper logic.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))


_NAME = "小天"


def _write_events(tmpdir: str, events: list[dict], name: str = _NAME) -> None:
    char_dir = os.path.join(tmpdir, name)
    os.makedirs(char_dir, exist_ok=True)
    path = os.path.join(char_dir, "events.ndjson")
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            rec = {
                "event_id": str(uuid.uuid4()),
                "type": evt["type"],
                "ts": evt["ts"],
                "payload": evt.get("payload", {}),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _build_app(tmpdir: str) -> FastAPI:
    """Build a standalone FastAPI app mounting only the funnel endpoint.

    We re-implement the handler here inline rather than importing
    `memory_server.api_memory_funnel` because loading `memory_server`
    at import time triggers its module-level ConfigManager and component
    globals — heavy, and pollutes state. The handler logic is tiny
    (dispatches to `funnel_counts`) so duplicating it here keeps the test
    hermetic.  If the memory_server handler's behavior diverges from
    this test fixture, update both.
    """
    import asyncio

    from memory.evidence_analytics import funnel_counts, to_naive_local

    app = FastAPI()
    cm = MagicMock()
    cm.memory_dir = tmpdir

    def _patched_get_cm():
        return cm

    @app.get("/api/memory/funnel/{lanlan_name}")
    async def funnel(lanlan_name: str, since: str | None = None, until: str | None = None):
        # Duplicates memory_server.api_memory_funnel for test hermeticity.
        from utils.character_name import validate_character_name
        result = validate_character_name(lanlan_name, allow_dots=True, max_length=50)
        if result.code is not None:
            raise HTTPException(status_code=400, detail="invalid lanlan_name")
        lanlan_name = result.normalized

        now = datetime.now()
        try:
            since_dt = datetime.fromisoformat(since) if since else now - timedelta(days=7)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid `since` ISO8601: {since!r}")
        try:
            until_dt = datetime.fromisoformat(until) if until else now
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid `until` ISO8601: {until!r}")
        # Normalize tz BEFORE the inequality check — mirrors the
        # production handler's round-2 fix for coderabbitai PR #937.
        since_dt = to_naive_local(since_dt)
        until_dt = to_naive_local(until_dt)
        if since_dt > until_dt:
            raise HTTPException(status_code=400, detail="`since` must be <= `until`")

        with patch("memory.evidence_analytics.get_config_manager", _patched_get_cm):
            counts = await asyncio.to_thread(funnel_counts, lanlan_name, since_dt, until_dt)
        return {
            "lanlan_name": lanlan_name,
            "since": since_dt.isoformat(),
            "until": until_dt.isoformat(),
            "counts": counts,
        }

    return app


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_returns_expected_counts(tmp_path):
    now = datetime(2026, 4, 22, 12, 0, 0)
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": now.isoformat(), "payload": {"fact_id": "f1"}},
        {"type": "fact.added", "ts": now.isoformat(), "payload": {"fact_id": "f2"}},
        {"type": "reflection.synthesized", "ts": now.isoformat(), "payload": {"reflection_id": "r1"}},
        {
            "type": "reflection.state_changed",
            "ts": now.isoformat(),
            "payload": {"reflection_id": "r1", "from": "pending", "to": "confirmed"},
        },
    ])
    app = _build_app(str(tmp_path))
    since = (now - timedelta(days=1)).isoformat()
    until = (now + timedelta(days=1)).isoformat()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={"since": since, "until": until},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["lanlan_name"] == _NAME
    assert body["counts"]["facts_added"] == 2
    assert body["counts"]["reflections_synthesized"] == 1
    assert body["counts"]["reflections_confirmed"] == 1
    # Remaining buckets should be present and zero.
    for key in (
        "reflections_promoted", "reflections_merged", "reflections_denied",
        "reflections_archived", "persona_entries_added",
        "persona_entries_rewritten", "persona_entries_archived",
    ):
        assert body["counts"][key] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_rejects_bad_iso8601(tmp_path):
    _write_events(str(tmp_path), [])
    app = _build_app(str(tmp_path))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={"since": "garbage-not-iso"},
        )
    assert resp.status_code == 400
    assert "since" in resp.json()["detail"].lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_rejects_since_after_until(tmp_path):
    _write_events(str(tmp_path), [])
    app = _build_app(str(tmp_path))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={
                "since": "2026-04-23T00:00:00",
                "until": "2026-04-22T00:00:00",
            },
        )
    assert resp.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_accepts_aware_iso8601_bounds(tmp_path):
    """Regression: codex P1 — `datetime.fromisoformat("...Z")` returns aware
    in Py3.11+; event-log `ts` is naive local. The endpoint must tolerate
    aware bounds without raising
    `TypeError: can't compare offset-naive and offset-aware datetimes`
    (which would surface to the client as 500).

    We use a window deliberately wide enough that, regardless of the
    local UTC offset on the runner, the lone event falls inside.
    """
    now = datetime(2026, 4, 22, 12, 0, 0)
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": now.isoformat(), "payload": {"fact_id": "f1"}},
    ])
    app = _build_app(str(tmp_path))
    # Aware-UTC bounds spanning ±30 days — wide enough that astimezone()
    # still keeps `now` inside even with extreme UTC offsets.
    since = (now - timedelta(days=30)).isoformat() + "Z"
    until = (now + timedelta(days=30)).isoformat() + "+00:00"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={"since": since, "until": until},
        )
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    assert resp.json()["counts"]["facts_added"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_default_window_covers_recent_events(tmp_path):
    """No since/until → default window = now - 7 days .. now."""
    now = datetime.now()
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": (now - timedelta(hours=1)).isoformat(), "payload": {}},
    ])
    app = _build_app(str(tmp_path))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/funnel/{_NAME}")
    assert resp.status_code == 200
    assert resp.json()["counts"]["facts_added"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_aware_since_naive_default_until(tmp_path):
    """Regression: coderabbitai PR #937 round-2 — only `since` aware (e.g.
    `...Z`) and `until` defaulted to naive `datetime.now()`. The endpoint
    must NOT raise TypeError on the `since_dt > until_dt` comparison;
    must return 200 with valid JSON.

    Picks an old `since` (>30 days ago) so the default `until = now()`
    is unambiguously after it regardless of the local UTC offset that
    `astimezone()` resolves to.
    """
    now = datetime.now()
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": (now - timedelta(hours=1)).isoformat(), "payload": {}},
    ])
    app = _build_app(str(tmp_path))
    since = (now - timedelta(days=30)).isoformat() + "Z"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={"since": since},
        )
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["counts"]["facts_added"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funnel_endpoint_naive_default_since_aware_until(tmp_path):
    """Reverse mix: `since` defaulted (naive `now() - 7d`), `until` aware
    (`...Z`). Symmetric regression for coderabbitai PR #937 round-2.

    Pins the event into the default 7-day window and chooses `until` far
    enough in the future that it's unambiguously > the naive default
    `since` after `astimezone()` normalization on any UTC offset.
    """
    now = datetime.now()
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": (now - timedelta(hours=1)).isoformat(), "payload": {}},
    ])
    app = _build_app(str(tmp_path))
    until = (now + timedelta(days=30)).isoformat() + "Z"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/memory/funnel/{_NAME}",
            params={"until": until},
        )
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["counts"]["facts_added"] == 1
