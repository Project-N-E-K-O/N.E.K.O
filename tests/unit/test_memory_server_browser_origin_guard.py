# -*- coding: utf-8 -*-
"""Browser-origin and payload-validation tests for memory_server routes (F0084, F0085)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit


@pytest.fixture
def memory_client(monkeypatch):
    from app.memory_server import locale_state, post_turn, review, routes, runtime

    monkeypatch.setattr(routes, "validate_lanlan_name", lambda n: n)
    monkeypatch.setattr(post_turn, "_track_character_post_turn_task", lambda *_a, **_kw: None)
    monkeypatch.setattr(runtime, "_spawn_background_task", lambda coro: coro.close() if hasattr(coro, "close") else None)
    monkeypatch.setattr(runtime, "_open_summary_preemption_window", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        locale_state,
        "run_with_character_prompt_locale",
        AsyncMock(return_value="ok"),
    )
    monkeypatch.setattr(
        runtime,
        "reflection_engine",
        SimpleNamespace(
            reflect=AsyncMock(return_value="ok"),
            arecord_surfaced=AsyncMock(),
        ),
    )
    monkeypatch.setattr(
        runtime,
        "pinned_fact_store",
        SimpleNamespace(amark_surfaced=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr(review, "correction_tasks", {})
    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    # Construct TestClient without entering its lifespan context so startup
    # hooks (e.g. token_tracker.install_hooks) do not leak across xdist tests.
    client = TestClient(
        runtime.app,
        base_url="http://127.0.0.1:48915",
        client=("127.0.0.1", 50000),
    )
    try:
        yield client, runtime, locale_state
    finally:
        client.close()


@pytest.mark.parametrize(
    "path",
    [
        "/reflect/Neko",
        "/cancel_correction/Neko",
        "/preempt_summary/Neko",
        "/record_surfaced/Neko",
    ],
)
@pytest.mark.parametrize(
    "browser_headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:48911"},
        {"Referer": "http://127.0.0.1:48911/index.html"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
)
def test_memory_internal_posts_refuse_browser_headers(memory_client, path, browser_headers):
    client, runtime, locale_state = memory_client
    headers = dict(browser_headers)
    if path.startswith("/record_surfaced"):
        headers["Content-Type"] = "application/json"
        resp = client.post(path, headers=headers, json={"reflection_ids": ["r1"]})
    else:
        resp = client.post(path, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "untrusted_origin"
    assert locale_state.run_with_character_prompt_locale.await_count == 0
    assert runtime.reflection_engine.arecord_surfaced.await_count == 0


def test_memory_bodyless_internal_posts_allow_headerless_backend_calls(memory_client):
    client, _runtime, _locale_state = memory_client
    assert client.post("/reflect/Neko").status_code == 200
    assert client.post("/cancel_correction/Neko").status_code == 200
    assert client.post("/preempt_summary/Neko").status_code == 200


def test_record_surfaced_rejects_text_plain_and_missing_content_type(memory_client):
    client, runtime, _ = memory_client
    resp_text = client.post(
        "/record_surfaced/Neko",
        content='{"reflection_ids": ["r1"]}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp_text.status_code == 415
    assert resp_text.json()["detail"]["reason"] == "unsupported_media_type"

    resp_missing = client.post("/record_surfaced/Neko", content='{"reflection_ids": ["r1"]}')
    assert resp_missing.status_code == 415
    assert runtime.reflection_engine.arecord_surfaced.await_count == 0


def test_record_surfaced_handles_malformed_and_invalid_json_without_500(memory_client):
    client, runtime, _ = memory_client
    resp_bad_json = client.post(
        "/record_surfaced/Neko",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert resp_bad_json.status_code == 400
    assert resp_bad_json.json()["detail"]["reason"] == "invalid_json"

    resp_list = client.post(
        "/record_surfaced/Neko",
        json=["not-a-dict"],
    )
    assert resp_list.status_code == 422
    assert resp_list.json()["detail"]["reason"] == "invalid_payload"

    resp_bad_field = client.post(
        "/record_surfaced/Neko",
        json={"commitment_ids": 123},
    )
    assert resp_bad_field.status_code == 422
    assert resp_bad_field.json()["detail"]["reason"] == "invalid_payload"
    assert runtime.reflection_engine.arecord_surfaced.await_count == 0


def test_record_surfaced_accepts_valid_internal_json(memory_client):
    client, runtime, _ = memory_client
    resp = client.post(
        "/record_surfaced/Neko",
        json={"commitment_ids": ["c1"], "reflection_ids": ["r1"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "commitments_recorded": True}
    runtime.reflection_engine.arecord_surfaced.assert_awaited_once_with("Neko", ["r1"])


_MEMORY_BROWSER_GET_PATHS = [
    "/internal/memory/browser/overview/Neko",
    "/internal/memory/browser/star_map/Neko",
    "/internal/memory/browser/lineage/Neko?kind=fact&entry_id=f1",
    "/internal/memory/browser/snapshots/Neko",
    "/internal/memory/browser/export/Neko",
    "/internal/memory/browser/lineage_graph/Neko",
]

_MEMORY_BROWSER_POST_PATHS = [
    "/internal/memory/browser/imprint_note/Neko",
    "/internal/memory/browser/search/Neko",
    "/internal/memory/browser/persona/edit",
    "/internal/memory/browser/persona/flags",
    "/internal/memory/browser/persona/archive",
    "/internal/memory/browser/persona/restore_version",
    "/internal/memory/browser/correction/resolve",
    "/internal/memory/browser/reflection/review",
    "/internal/memory/browser/fact/edit",
    "/internal/memory/browser/fact/archive",
    "/internal/memory/browser/duplicate/merge",
    "/internal/memory/browser/directive/add",
    "/internal/memory/browser/directive/remove",
    "/internal/memory/browser/star_map/label_clusters/Neko",
    "/internal/memory/browser/related/Neko",
    "/internal/memory/browser/snapshots/create/Neko",
    "/internal/memory/browser/snapshots/restore/Neko",
    "/internal/memory/browser/deep_conflicts/Neko",
]


@pytest.mark.parametrize("path", _MEMORY_BROWSER_GET_PATHS)
@pytest.mark.parametrize(
    "browser_headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:48911"},
        {"Referer": "http://127.0.0.1:48911/index.html"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
)
def test_memory_browser_gets_refuse_browser_headers(memory_client, path, browser_headers):
    client, _runtime, _locale_state = memory_client
    resp = client.get(path, headers=browser_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "untrusted_origin"


@pytest.mark.parametrize("path", _MEMORY_BROWSER_POST_PATHS)
@pytest.mark.parametrize(
    "browser_headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:48911"},
        {"Referer": "http://127.0.0.1:48911/index.html"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
)
def test_memory_browser_posts_refuse_browser_headers(memory_client, path, browser_headers):
    client, _runtime, _locale_state = memory_client
    headers = dict(browser_headers)
    headers["Content-Type"] = "application/json"
    resp = client.post(path, headers=headers, json={})
    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "untrusted_origin"


@pytest.mark.parametrize("path", _MEMORY_BROWSER_POST_PATHS)
def test_memory_browser_posts_reject_non_json_content_type(memory_client, path):
    client, _runtime, _locale_state = memory_client
    resp_text = client.post(
        path,
        content=b"{}",
        headers={"Content-Type": "text/plain"},
    )
    assert resp_text.status_code == 415
    assert resp_text.json()["detail"]["reason"] == "unsupported_media_type"

    resp_missing = client.post(path, content=b"{}")
    assert resp_missing.status_code == 415
    assert resp_missing.json()["detail"]["reason"] == "unsupported_media_type"
