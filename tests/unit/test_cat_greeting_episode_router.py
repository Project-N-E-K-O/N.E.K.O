"""Boundary tests for the one-shot cat return episode transport."""

import asyncio
import json
import math

import pytest

import main_routers.websocket_router as websocket_router
from fastapi import WebSocketDisconnect

from main_routers.websocket_router import _normalize_cat_greeting_check


def test_cat_greeting_router_uses_canonical_top_level_values_only():
    duration, tier, was_auto, episode = _normalize_cat_greeting_check({
        "cat_duration_seconds": 181.5,
        "tier": "  CAT2 ",
        "was_auto": True,
        "cat_memory_summary": {
            "duration_seconds": 999999,
            "entry": "manual",
            "final_tier": "cat3",
            "episode": {
                "kind": "rest_after_activity",
                "highlight": "played_yarn",
                "untrusted_text": "do not transport",
            },
        },
    })

    assert duration == 181.5
    assert tier == "cat2"
    assert was_auto is True
    assert episode == {"kind": "rest_after_activity", "highlight": "played_yarn"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (180, 180.0),
        (0, 0.0),
        (-1, 0.0),
        (7 * 24 * 3600 + 1, float(7 * 24 * 3600)),
        (True, 0.0),
        (False, 0.0),
        ("180", 0.0),
        (None, 0.0),
        (math.nan, 0.0),
        (math.inf, 0.0),
        (-math.inf, 0.0),
    ],
)
def test_cat_greeting_router_duration_accepts_only_finite_numbers(raw, expected):
    duration, _, _, _ = _normalize_cat_greeting_check({"cat_duration_seconds": raw})
    assert duration == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cat1", "cat1"),
        (" CAT2 ", "cat2"),
        ("Cat3", "cat3"),
        ("cat4", ""),
        ("", ""),
        (True, ""),
        (["cat1"], ""),
        ({"tier": "cat1"}, ""),
    ],
)
def test_cat_greeting_router_tier_is_allowlisted(raw, expected):
    _, tier, _, _ = _normalize_cat_greeting_check({"tier": raw})
    assert tier == expected


@pytest.mark.parametrize("raw", [False, "false", "true", "1", 1, 0, [], {}, None])
def test_cat_greeting_router_only_literal_boolean_true_means_auto(raw):
    _, _, was_auto, _ = _normalize_cat_greeting_check({"was_auto": raw})
    assert was_auto is False


@pytest.mark.parametrize(
    ("raw_episode", "expected"),
    [
        ({"kind": "activity"}, {"kind": "activity"}),
        ({"kind": "activity", "highlight": "ate_snack"}, {"kind": "activity", "highlight": "ate_snack"}),
        ({"kind": "rest_after_activity"}, {"kind": "rest_after_activity"}),
        ({"kind": "rest_after_activity", "highlight": "small_move"}, {"kind": "rest_after_activity", "highlight": "small_move"}),
        ({"kind": "rested"}, {"kind": "rested"}),
    ],
)
def test_cat_greeting_router_accepts_only_valid_episode_combinations(raw_episode, expected):
    _, _, _, episode = _normalize_cat_greeting_check({
        "cat_memory_summary": {"episode": raw_episode},
    })
    assert episode == expected


@pytest.mark.parametrize(
    "raw_episode",
    [
        None,
        [],
        "activity",
        {"kind": "unknown"},
        {"kind": ["activity"]},
        {"kind": "activity", "highlight": "free text"},
        {"kind": "activity", "highlight": None},
        {"kind": "rested", "highlight": "social_ping"},
    ],
)
def test_cat_greeting_router_drops_invalid_episode_without_rejecting_the_check(raw_episode):
    duration, tier, was_auto, episode = _normalize_cat_greeting_check({
        "cat_duration_seconds": 240,
        "tier": "cat1",
        "was_auto": True,
        "cat_memory_summary": {"episode": raw_episode},
    })
    assert (duration, tier, was_auto) == (240.0, "cat1", True)
    assert episode is None


def test_cat_greeting_router_ignores_unrecognized_summary_fields_and_top_level_episode():
    _, _, _, episode = _normalize_cat_greeting_check({
        "episode": {"kind": "activity", "highlight": "played_yarn"},
        "cat_memory_summary": {
            "events": ["open", "text"],
            "scores": {"appetite": 100},
            "episode": {
                "kind": "activity",
                "highlight": "played_yarn",
                "coordinates": [1, 2],
            },
        },
    })
    assert episode == {"kind": "activity", "highlight": "played_yarn"}


def test_cat_greeting_router_ignores_non_object_summary():
    for summary in (None, [], "summary", 1):
        _, _, _, episode = _normalize_cat_greeting_check({"cat_memory_summary": summary})
        assert episode is None


def test_cat_greeting_router_passes_only_canonical_episode_to_manager(monkeypatch):
    calls = []
    session_ids = {}

    class _Manager:
        pending_agent_callbacks = []
        websocket = None

        def set_user_language(self, _value):
            pass

        def trigger_cat_greeting(self, duration, tier, was_auto, *, episode=None):
            calls.append((duration, tier, was_auto, episode))

            async def _done():
                return None

            return _done()

        async def cleanup(self, **_kwargs):
            return None

    class _WebSocket:
        client = "cat-greeting-router-test"

        def __init__(self):
            self._messages = [json.dumps({
                "action": "cat_greeting_check",
                "cat_duration_seconds": 9 * 24 * 3600,
                "tier": "cat4",
                "was_auto": "false",
                "cat_memory_summary": {
                    "duration_seconds": 1,
                    "entry": "auto",
                    "final_tier": "cat1",
                    "episode": {
                        "kind": "activity",
                        "highlight": "ate_snack",
                        "raw_text": "must not reach the manager",
                    },
                },
            })]

        async def accept(self):
            return None

        async def receive_text(self):
            if self._messages:
                return self._messages.pop(0)
            raise WebSocketDisconnect()

    manager = _Manager()

    def _capture_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(websocket_router, "get_session_manager", lambda: {"Test": manager})
    monkeypatch.setattr(websocket_router, "get_session_id", lambda: session_ids)
    monkeypatch.setattr(websocket_router, "_fire_task", _capture_task)

    asyncio.run(websocket_router.websocket_endpoint(_WebSocket(), "Test"))

    assert calls == [(
        float(7 * 24 * 3600),
        "",
        False,
        {"kind": "activity", "highlight": "ate_snack"},
    )]
