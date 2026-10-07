from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from utils.llm_client import AIMessage, SQLChatMessageHistory

# The insights route asks where memory lives before it will analyse a
# CONFIGURED character: that branch skips the existence check, so a linked
# memory/<name> would otherwise be followed straight out of the root by
# the read-only lookup. These doubles predate the route reading the
# storage layout at all, because the configured branch never reached it.
#
# Never touched on disk: a default path is a direct child by
# construction, and none of these tests are about the layout.
_ANY_CONTAINED_MEMORY_ROOT = str(
    Path(tempfile.gettempdir()) / "neko-test-memory-root"
)


def _storage_aware(namespace):
    """Give a config-manager double the storage attributes the route reads."""
    namespace.memory_dir = _ANY_CONTAINED_MEMORY_ROOT
    namespace.get_character_data = lambda: (None,) * 6 + ({},) + (None, None)
    return namespace


def _empty_effects(days: int = 30) -> dict:
    return {
        "schema_version": "anti-repeat-effects/v1",
        "source_available": False,
        "period_days": days,
        "totals": {},
        "reason_counts": {},
        "bm25": {},
        "patterns": [],
    }

def test_sql_history_preserves_anti_repeat_link_metadata():
    history = SQLChatMessageHistory.__new__(SQLChatMessageHistory)

    serialized = history._serialize(
        AIMessage(
            content="synthetic reply",
            additional_kwargs={
                "anti_repeat_response_id": "response-1",
                "anti_repeat_visible_text_length": "15",
            },
        )
    )

    assert json.loads(serialized) == {
        "type": "ai",
        "data": {
            "content": "synthetic reply",
            "additional_kwargs": {
                "anti_repeat_response_id": "response-1",
                "anti_repeat_visible_text_length": "15",
            },
        },
    }

def test_sql_history_discards_unapproved_and_non_string_metadata():
    history = SQLChatMessageHistory.__new__(SQLChatMessageHistory)

    serialized = history._serialize(
        AIMessage(
            content="synthetic reply",
            additional_kwargs={
                "anti_repeat_response_id": object(),
                "provider_payload": object(),
                "private_note": "must not persist",
            },
        )
    )

    assert json.loads(serialized) == {
        "type": "ai",
        "data": {"content": "synthetic reply"},
    }

@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_repetition_insights_returns_review_only_candidates():
    from app.memory_server import routes

    history = SimpleNamespace(
        messages=["quiet lantern", "quiet lantern", "quiet lantern"],
        source_available=True,
        skipped_row_count=2,
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            "test_char",
            routes.RepetitionInsightsRequest(
                language="en",
                assistant_message_limit=25,
            ),
        )

    assert result["success"] is True
    assert result["artifact_type"] == "user_review_candidates"
    assert result["summary"] == {
        "assistant_message_count": 3,
        "analyzed_message_count": 3,
        "analyzed_source_lines": [1, 2, 3],
        "messages_truncated": False,
        "content_truncated": False,
        "candidate_count": 1,
        "returned_candidate_count": 1,
        "candidates_truncated": False,
        "source_available": True,
    }
    assert result["parameters"]["assistant_message_limit"] == 25
    assert result["parameters"]["message_count_threshold"] == 3
    assert result["candidates"][0]["phrase"] == "quiet lantern"
    assert "context" not in result["candidates"][0]
    # No persisted response IDs -> the key must be OMITTED, not sent as an empty
    # list. An empty list still selects the message-scoped branch in
    # main_routers.memory_router, which then reports "no linked records" forever
    # instead of falling back to the day-scoped aggregate that does work.
    assert "_anti_repeat_response_ids" not in result
    time_manager.aretrieve_latest_assistant_texts.assert_awaited_once_with(
        "test_char",
        25,
    )

@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_repetition_insights_forwards_present_response_ids():
    """The join key still travels when the persisted history actually carries it."""
    from app.memory_server import routes

    history = SimpleNamespace(
        messages=["quiet lantern", "quiet lantern", "quiet lantern"],
        source_available=True,
        skipped_row_count=0,
        # Positionally aligned with `messages`: one entry per reply.
        response_ids=["turn-a", "turn-b", "turn-c"],
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            "test_char",
            routes.RepetitionInsightsRequest(
                language="en",
                assistant_message_limit=25,
            ),
        )

    assert result["_anti_repeat_response_ids"] == ["turn-a", "turn-b", "turn-c"]

@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("character_name", ["a" * 60, "chat"])
async def test_internal_repetition_insights_accepts_existing_query_names(character_name):
    from app.memory_server import routes

    history = SimpleNamespace(
        messages=[],
        source_available=True,
        skipped_row_count=0,
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            character_name,
            routes.RepetitionInsightsRequest(language="en"),
        )

    assert result["success"] is True
    assert result["character_name"] == character_name
    time_manager.aretrieve_latest_assistant_texts.assert_awaited_once_with(
        character_name,
        100,
    )

@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_repetition_insights_requires_initialized_time_manager():
    from app.memory_server import routes

    with patch.object(routes.runtime, "time_manager", None):
        with pytest.raises(HTTPException) as exc_info:
            await routes.repetition_insights(
                "test_char",
                routes.RepetitionInsightsRequest(language="en"),
            )

    assert exc_info.value.status_code == 503

@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("character_name", ["legacy.name", "chat"])
async def test_public_repetition_insights_validates_and_forwards_local_request(
    character_name,
):
    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    response_payload = {
        "success": True,
        "schema_version": "natural-expression-candidates/v1",
        "artifact_type": "user_review_candidates",
        "candidates": [],
    }
    response = SimpleNamespace(
        status_code=200,
        json=lambda: dict(response_payload),
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    effect_store = SimpleNamespace(
        query_effects=MagicMock(return_value=_empty_effects())
    )
    config = _storage_aware(
        SimpleNamespace(
            aload_characters=AsyncMock(
                return_value={"猫娘": {character_name: {}}}
            )
        )
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client,
            "get_internal_http_client",
            return_value=client,
        ),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
    ):
        result = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name=character_name,
                language="zh-CN",
                assistant_message_limit=50,
            )
        )

    assert result["success"] is True
    assert result["artifact_type"] == "user_review_candidates"
    assert result["effectiveness"] == _empty_effects()
    assert result["associations"] == []
    call = client.post.await_args
    assert call.kwargs["json"] == {
        "language": "zh-CN",
        "assistant_message_limit": 50,
    }
    assert call.kwargs["timeout"] == 30.0
    assert call.args[0].startswith("http://127.0.0.1:")
    assert call.args[0].endswith(f"/{character_name}/repetition_insights")
    effect_store.query_effects.assert_called_once_with(character_name, 30)

@pytest.mark.unit
def test_repetition_insight_effect_days_are_limited_to_supported_windows():
    from main_routers import memory_router

    with pytest.raises(ValidationError):
        memory_router.RepetitionInsightsRequest(
            character_name="test_char",
            language="en",
            effect_days=14,
        )

@pytest.mark.unit
def test_repetition_effect_associations_are_exact_or_safe_containment_only():
    from main_routers import memory_router

    candidates = [
        {
            "normalized_phrase": "quiet lantern",
            "language": "en",
            "occurrence_count": 4,
            "message_count": 3,
        },
        {
            "normalized_phrase": "好久不见",
            "language": "zh-CN",
            "occurrence_count": 3,
            "message_count": 3,
        },
        {
            "normalized_phrase": "hello there",
            "language": "en",
            "occurrence_count": 3,
            "message_count": 3,
        },
    ]
    patterns = [
        {
            "normalized_phrase": "quiet lantern",
            "language": "en",
            "detected_count": 6,
            "regen_triggered_count": 4,
            "regen_guard_passed_count": 3,
            "blocked_count": 1,
        },
        {
            "normalized_phrase": "今天好久不见呀",
            "language": "zh",
            "detected_count": 2,
        },
        {
            "normalized_phrase": "好久不见",
            "language": "zh-TW",
            "detected_count": 100,
        },
        {
            "normalized_phrase": "hello their",
            "language": "en",
            "detected_count": 99,
        },
    ]

    result = memory_router._associate_repetition_effects(candidates, patterns)

    assert [item["association_type"] for item in result] == ["exact", "contained"]
    assert result[0] == {
        "normalized_phrase": "quiet lantern",
        "language": "en",
        "effect_normalized_phrase": "quiet lantern",
        "association_type": "exact",
        "detected_count": 6,
        "regen_triggered_count": 4,
        "regen_guard_passed_count": 3,
        "blocked_count": 1,
        "residual_occurrence_count": 4,
        "residual_message_count": 3,
    }

@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "candidate_phrase", "rejected_phrase", "contained_phrase"),
    [
        ("en", "he said", "she said", "well he said today"),
        ("es", "la casa", "mala casa", "visité la casa hoy"),
        ("pt", "a casa", "na casa", "vi a casa hoje"),
        ("ru", "он сказал", "слон сказал", "вчера он сказал правду"),
    ],
)
def test_word_language_associations_require_contiguous_token_boundaries(
    language,
    candidate_phrase,
    rejected_phrase,
    contained_phrase,
):
    from main_routers import memory_router

    candidates = [
        {
            "normalized_phrase": candidate_phrase,
            "language": language,
            "occurrence_count": 3,
            "message_count": 3,
        }
    ]
    patterns = [
        {
            "normalized_phrase": rejected_phrase,
            "language": language,
            "detected_count": 99,
        },
        {
            "normalized_phrase": contained_phrase,
            "language": language,
            "detected_count": 2,
        },
    ]

    result = memory_router._associate_repetition_effects(candidates, patterns)

    assert len(result) == 1
    assert result[0]["effect_normalized_phrase"] == contained_phrase
    assert result[0]["association_type"] == "contained"

@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "candidate_phrase", "effect_phrase"),
    [
        ("en", "quiet lantern", "quiet"),
        ("zh-CN", "一直陪着", "陪着"),
    ],
)
def test_associations_accept_actual_runtime_detector_signature_sizes(
    language,
    candidate_phrase,
    effect_phrase,
):
    from main_routers import memory_router

    candidates = [
        {
            "normalized_phrase": candidate_phrase,
            "language": language,
            "occurrence_count": 3,
            "message_count": 3,
        }
    ]
    patterns = [
        {
            "normalized_phrase": effect_phrase,
            "language": language,
            "reasons": {"bm25": 1},
            "detected_count": 1,
        }
    ]

    result = memory_router._associate_repetition_effects(candidates, patterns)

    assert len(result) == 1
    assert result[0]["effect_normalized_phrase"] == effect_phrase
    assert result[0]["association_type"] == "contained"

@pytest.mark.unit
def test_korean_associations_use_word_boundaries_without_losing_character_ngrams():
    from main_routers import memory_router

    candidates = [
        {
            "normalized_phrase": "나는 정말",
            "language": "ko",
            "occurrence_count": 3,
            "message_count": 3,
        },
        {
            "normalized_phrase": "두근두근",
            "language": "ko",
            "occurrence_count": 4,
            "message_count": 3,
        },
    ]
    patterns = [
        {
            "normalized_phrase": "신나는 정말",
            "language": "ko",
            "detected_count": 99,
        },
        {
            "normalized_phrase": "어제 나는 정말 웃었어",
            "language": "ko",
            "detected_count": 2,
        },
        {
            "normalized_phrase": "오늘도 두근두근 설레",
            "language": "ko",
            "detected_count": 3,
        },
    ]

    result = memory_router._associate_repetition_effects(candidates, patterns)

    assert [item["effect_normalized_phrase"] for item in result] == [
        "어제 나는 정말 웃었어",
        "오늘도 두근두근 설레",
    ]

@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_repetition_insights_keeps_residuals_when_effect_query_fails():
    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    client = SimpleNamespace(
        post=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"success": True, "candidates": []},
            )
        )
    )
    config = _storage_aware(
        SimpleNamespace(aload_characters=AsyncMock(return_value={}))
    )
    effect_store = SimpleNamespace(
        query_effects=MagicMock(side_effect=OSError("private path"))
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client,
            "get_internal_http_client",
            return_value=client,
        ),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
        patch.object(memory_router, "character_memory_exists", return_value=True),
    ):
        result = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name="test_char",
                language="en",
                effect_days=7,
            )
        )

    assert result["success"] is True
    assert result["effectiveness"]["source_available"] is False
    assert result["effectiveness"]["period_days"] == 7
    assert "private path" not in json.dumps(result)

@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("character_name", ["legacy.name", "chat"])
async def test_reset_repetition_effects_clears_only_selected_character(character_name):
    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager

    config = _storage_aware(
        SimpleNamespace(aload_characters=AsyncMock(return_value={}))
    )
    effect_store = SimpleNamespace(clear_effects=MagicMock())

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
        patch.object(memory_router, "character_memory_exists", return_value=True),
    ):
        result = await memory_router.reset_repetition_effects(
            memory_router.RepetitionEffectsResetRequest(character_name=character_name)
        )

    assert result == {
        "success": True,
        "character_name": character_name,
        "cleared": True,
    }
    effect_store.clear_effects.assert_called_once_with(character_name)

@pytest.mark.unit
@pytest.mark.asyncio
async def test_repetition_endpoints_still_reject_dot_traversal_names():
    from main_routers import memory_router

    insights = await memory_router.repetition_insights(
        memory_router.RepetitionInsightsRequest(
            character_name="../escape",
            language="en",
        )
    )
    reset = await memory_router.reset_repetition_effects(
        memory_router.RepetitionEffectsResetRequest(character_name="../escape")
    )

    assert insights.status_code == 422
    assert reset.status_code == 422

@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_repetition_insights_returns_sanitized_unavailable_error():
    from main_routers import memory_router
    from utils import config_manager, internal_http_client

    client = SimpleNamespace(
        post=AsyncMock(side_effect=RuntimeError("private upstream detail"))
    )
    config = _storage_aware(
        SimpleNamespace(
            aload_characters=AsyncMock(
                return_value={"猫娘": {"test_char": {}}}
            )
        )
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client,
            "get_internal_http_client",
            return_value=client,
        ),
    ):
        response = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name="test_char",
                language="en",
            )
        )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {
        "success": False,
        "error": "local memory analysis unavailable",
    }
    assert "private upstream detail" not in response.body.decode("utf-8")

@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_ids, expected",
    [
        # Every analyzed reply linkable -> message scope is honest.
        (["turn-a", "turn-b", "turn-c"], ["turn-a", "turn-b", "turn-c"]),
        # A legacy row without the key mixed in -> the aggregate would cover
        # only part of the window, so fall back to day scope.
        (["turn-a", None, "turn-c"], None),
        (["turn-a", None, None], None),
        ([None, None, None], None),
    ],
)
async def test_partial_response_id_coverage_falls_back_to_day_scope(
    response_ids, expected
):
    from app.memory_server import routes

    history = SimpleNamespace(
        messages=["quiet lantern", "quiet lantern", "quiet lantern"],
        source_available=True,
        skipped_row_count=0,
        response_ids=response_ids,
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            "test_char",
            routes.RepetitionInsightsRequest(language="en", assistant_message_limit=25),
        )

    assert result.get("_anti_repeat_response_ids") == expected

@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_ids_are_sliced_to_the_analyzed_window(monkeypatch):
    """The budget narrows the window newest-first; the IDs must follow it.

    Emitting IDs for replies the report never analyzed would let the panel label
    an out-of-window aggregate as handling for the latest requested replies.
    """
    from app.memory_server import routes
    from utils import natural_expression_candidates as candidate_core

    # One such reply mines to 145 occurrences, so a 200 budget keeps exactly one.
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_OCCURRENCES", 200)
    unbroken = "今天天气真好我们一起去散步你觉得怎么样我觉得非常开心因为可以和你聊天"
    history = SimpleNamespace(
        messages=[unbroken] * 4,
        source_available=True,
        skipped_row_count=0,
        response_ids=["oldest", "older", "newer", "newest"],
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            "test_char",
            routes.RepetitionInsightsRequest(
                language="zh-CN", assistant_message_limit=25
            ),
        )

    analyzed = result["summary"]["analyzed_message_count"]
    assert result["summary"]["messages_truncated"] is True
    assert 0 < analyzed < 4
    # Exactly the newest `analyzed` ids, never the dropped older ones.
    assert result["_anti_repeat_response_ids"] == ["oldest", "older", "newer", "newest"][-analyzed:]

@pytest.mark.unit
@pytest.mark.asyncio
async def test_effect_scope_uses_the_analyzed_count_not_the_request(monkeypatch):
    """A narrowed window must relabel the effect scope.

    Otherwise the panel says "the latest 100 replies" over an aggregate that
    covers however few the budget actually allowed.
    """
    from main_routers import memory_router

    captured = {}

    class _Store:
        def query_effects_for_responses(self, name, ids, limit, **_kwargs):
            captured["limit"] = limit
            return {
                "schema_version": "anti-repeat-effects/v1",
                "source_available": True,
                "started_at": 0.0,
                "scope_type": "assistant_messages",
                "assistant_message_limit": limit,
                "linked_message_count": len(list(ids)),
                "totals": {},
                "reason_counts": {},
                "bm25": {},
                "patterns": [],
            }

    monkeypatch.setattr(
        "memory.anti_repeat_effects.get_anti_repeat_effect_store", lambda: _Store()
    )

    inner = {
        "success": True,
        "summary": {"assistant_message_count": 100, "analyzed_message_count": 10},
        "candidates": [],
        "parameters": {},
        "_anti_repeat_response_ids": ["a", "b"],
    }

    class _Response:
        status_code = 200

        def json(self):
            return dict(inner)

    monkeypatch.setattr(
        memory_router,
        "character_memory_exists",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: SimpleNamespace(post=AsyncMock(return_value=_Response())),
    )
    monkeypatch.setattr(
        "utils.config_manager.get_config_manager",
        lambda: _storage_aware(
            SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {}}))
        ),
    )

    result = await memory_router.repetition_insights(
        memory_router.RepetitionInsightsRequest(
            character_name="测试猫娘", language="en", assistant_message_limit=100
        )
    )

    assert captured["limit"] == 10
    assert result["effectiveness"]["assistant_message_limit"] == 10

@pytest.mark.asyncio
async def test_insight_characters_lists_history_without_a_recent_file(tmp_path):
    """The selector must offer every identity the analysis route accepts.

    Built from the recent-memory file list, it omitted a character that has
    time-indexed history but no ``recent.json`` -- the shape a cloud-save import
    produces when the profile ships no recent file -- even though the analysis
    route explicitly supports it.
    """
    from main_routers import memory_router
    from utils import config_manager

    memory_dir = tmp_path / "memory"
    (memory_dir / "OnlyHistory").mkdir(parents=True)
    (memory_dir / "OnlyHistory" / "time_indexed.db").write_bytes(b"")
    (memory_dir / "HasRecent").mkdir()
    (memory_dir / "HasRecent" / "recent.json").write_text("[]", encoding="utf-8")
    (memory_dir / "Empty").mkdir()

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"Configured": {}}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        result = await memory_router.get_insight_characters()

    assert "OnlyHistory" in result["characters"], (
        "a character with time-indexed history but no recent.json was omitted"
    )
    assert "HasRecent" in result["characters"]
    assert "Configured" in result["characters"]
    assert "Absent" not in result["characters"]

    # Anti-drift: membership must be exactly the analysis route's admission
    # rule, so the panel can never offer a name the route rejects nor hide one
    # it accepts. A bare directory qualifies under that rule -- the route serves
    # it and reports an empty analysis -- so it belongs in the list too.
    from utils.character_memory import character_memory_exists

    with patch.object(config_manager, "get_config_manager", return_value=config):
        for name in ("OnlyHistory", "HasRecent", "Empty", "Absent"):
            admitted = name == "Configured" or character_memory_exists(
                config, name
            )
            assert (name in result["characters"]) is admitted, name

@pytest.mark.asyncio
async def test_a_flat_legacy_orphan_is_migrated_then_offered_normally(tmp_path):
    """The selector decodes nothing; the migration removes the shape instead.

    A pre-layout install stored memory as ``<kind>_<name>`` at the root. The
    selector used to decode an owner out of those names, which made the READ
    path carry a second layout -- and got it wrong: "time_indexed_Carol.db-wal"
    decoded to a character called "Carol.db-wal", which the existence check then
    confirmed, so the panel offered it.

    The flat layout was retired in 2026-03 together with the startup migration
    that replaces it. That migration now covers owners absent from
    characters.json as well, so by the time the selector runs a legacy root file
    has already become ``memory/<name>/`` and the ordinary directory branch sees
    it.
    """
    from main_routers import memory_router
    from utils import config_manager
    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "time_indexed_Carol.db").write_bytes(b"")
    (memory_dir / "time_indexed_Carol.db-wal").write_bytes(b"")
    (memory_dir / "recent_Bob.json").write_text("[]", encoding="utf-8")
    (memory_dir / "Dave").mkdir()

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )

    # BEFORE the migration this selector decodes nothing, so the flat
    # time-indexed database names nobody. "Bob" IS listed, through a
    # different and older path: the memory browser lists recent files by a
    # logical "recent_<name>.json" name and reads both layouts, which is
    # its own UI contract rather than legacy decoding in this route.
    with patch.object(config_manager, "get_config_manager", return_value=config):
        before = (await memory_router.get_insight_characters())["characters"]
    assert "Carol" not in before, before
    assert before == ["Bob", "Dave"], before

    # The startup migration, with NO configured names -- the case that used to
    # leave these files flat forever.
    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])

    with patch.object(config_manager, "get_config_manager", return_value=config):
        after = (await memory_router.get_insight_characters())["characters"]

    assert "Carol" in after, "the migrated owner is not offered as a directory"
    assert "Bob" in after
    assert "Dave" in after
    # The decoder's old mistakes cannot come back, because nothing decodes.
    assert "Carol.db" not in after
    assert "Carol.db-wal" not in after
    # And nothing flat is left for anything to decode.
    assert not [
        entry for entry in memory_dir.iterdir()
        if entry.name.startswith(("time_indexed_", "recent_"))
    ]

def test_the_migration_never_moves_a_directory(tmp_path):
    """A real character can be named like a legacy store, and one is.

    "semantic_memory_Alice" is a legal character name and its ordinary
    per-character directory has exactly the shape a pre-layout vector store has.
    Nothing on disk can tell the two apart, so the migration moves FILES only --
    a vector store holds no assistant history either way, so nothing the panel
    can read is lost by leaving those alone.
    """
    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "semantic_memory_Alice").mkdir()
    (memory_dir / "semantic_memory_Alice" / "facts.json").write_text(
        "[1]", encoding="utf-8"
    )

    for configured in ([], ["semantic_memory_Alice"]):
        memory_pkg.migrate_to_character_dirs(str(memory_dir), configured)
        assert (
            memory_dir / "semantic_memory_Alice" / "facts.json"
        ).read_text(encoding="utf-8") == "[1]", configured
        assert not (memory_dir / "Alice").exists(), (
            "a directory was decoded as a legacy store and moved out from "
            "under the character that owns it (configured=%r)" % (configured,)
        )

    # A directory named like a legacy FILE is the sharper case: the
    # extension-less "time_indexed_{name}" pattern matches
    # "time_indexed_Ghost", so without the file check discovery invents a
    # character called Ghost and the move takes the whole directory as if
    # it were her database.
    ghost = memory_dir / "time_indexed_Ghost"
    ghost.mkdir()
    (ghost / "inner.txt").write_text("x", encoding="utf-8")
    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])
    assert (ghost / "inner.txt").exists(), (
        "a directory shaped like a legacy database was moved away"
    )
    assert not (memory_dir / "Ghost").exists(), (
        "the migration invented a character out of a directory name"
    )

def test_the_migration_decoder_boundaries():
    """The decoder's own contract, now a migration-private helper.

    It decides which owners get a directory created for them, so a pattern that
    matched its own bare prefix -- or a SQLite sidecar -- would materialise a
    character out of nothing.
    """
    from memory import _legacy_root_entry_owner as owner

    assert owner("time_indexed_Carol.db") == "Carol"
    assert owner("time_indexed_Carol") == "Carol"
    assert owner("recent_Bob.json") == "Bob"
    # A bare prefix names nobody.
    assert owner("semantic_memory_") is None
    assert owner("time_indexed_.db") is None
    # Not a legacy shape at all.
    assert owner("Dave") is None
    assert owner("recent.json") is None
    # The extension-less pattern matches these, which is exactly why discovery
    # skips them rather than trusting the decoder alone.
    assert owner("time_indexed_Carol.db-wal") == "Carol.db-wal"
    assert owner("time_indexed_Carol.db-shm") == "Carol.db-shm"

def _association_pair(phrase, effect_phrase, association_type, **counts):
    row = {
        "normalized_phrase": phrase,
        "language": "zh-CN",
        "effect_normalized_phrase": effect_phrase,
        "association_type": association_type,
        "detected_count": 0,
        "regen_triggered_count": 0,
        "regen_guard_passed_count": 0,
        "blocked_count": 0,
        "residual_occurrence_count": 3,
        "residual_message_count": 2,
    }
    row.update(counts)
    return row

def test_associations_fold_to_one_row_per_candidate_without_changing_totals():
    """The payload is bounded by the candidate count, not by the product.

    One row per (candidate, pattern) pair made the response the PRODUCT of two
    capped lists -- 200 candidates against up to 1920 window patterns. The
    panel only ever reduces them to four totals plus an "any at all?" test, so
    folding keeps every displayed number identical while dropping the row
    count by more than an order of magnitude.

    Capping instead would have been the wrong fix: a truncated array turns
    those totals into silently wrong numbers on the card.
    """
    from main_routers.memory_router import _aggregate_repetition_associations

    pairs = [
        _association_pair("一起去吃饭吧", "一起去吃饭", "contained", detected_count=4),
        _association_pair("一起去吃饭吧", "一起去吃饭吧", "exact", detected_count=6,
                          regen_triggered_count=2),
        _association_pair("一起去吃饭吧", "去吃饭吧", "contained", blocked_count=1,
                          regen_guard_passed_count=3),
        _association_pair("今天也辛苦了", "今天也辛苦", "contained", detected_count=5),
    ]

    folded = _aggregate_repetition_associations(pairs)

    assert [row["normalized_phrase"] for row in folded] == [
        "一起去吃饭吧",
        "今天也辛苦了",
    ]
    first = folded[0]
    # Exactly what the card displays, summed the way the browser sums it.
    assert first["detected_count"] == 10
    assert first["regen_triggered_count"] == 2
    assert first["regen_guard_passed_count"] == 3
    assert first["blocked_count"] == 1
    # An exact hit anywhere in the group is the stronger claim and must win.
    assert first["association_type"] == "exact"
    assert first["effect_pattern_count"] == 3
    assert first["residual_occurrence_count"] == 3
    assert first["residual_message_count"] == 2
    assert folded[1]["association_type"] == "contained"
    assert folded[1]["effect_pattern_count"] == 1

def test_folded_associations_preserve_the_totals_of_the_pair_list():
    """Differential check: the four sums must survive folding exactly."""
    from main_routers.memory_router import (
        _aggregate_repetition_associations,
        _associate_repetition_effects,
    )

    phrases = ["一起去吃饭吧", "今天也辛苦了呢", "晚安啦做个好梦"]
    grams = [
        phrase[start : start + size]
        for phrase in phrases
        for size in range(2, len(phrase) + 1)
        for start in range(len(phrase) - size + 1)
    ]
    candidates = [
        {
            "language": "zh-CN",
            "normalized_phrase": gram,
            "occurrence_count": 3,
            "message_count": 2,
        }
        for gram in dict.fromkeys(grams)
    ]
    patterns = [
        {
            "language": "zh-CN",
            "normalized_phrase": gram,
            "detected_count": 2,
            "regen_triggered_count": 1,
            "regen_guard_passed_count": 1,
            "blocked_count": 1,
            "reasons": {"bm25": 1},
        }
        for gram in dict.fromkeys(grams)
    ]

    pairs = _associate_repetition_effects(candidates, patterns)
    folded = _aggregate_repetition_associations(pairs)

    fields = (
        "detected_count",
        "regen_triggered_count",
        "regen_guard_passed_count",
        "blocked_count",
    )
    assert len(pairs) > len(folded) * 5, "the fixture must actually exercise folding"
    assert len(folded) <= len(candidates)
    for field in fields:
        assert sum(row[field] for row in pairs) == sum(row[field] for row in folded), field

@pytest.mark.unit
@pytest.mark.asyncio
async def test_repetition_insights_route_ships_folded_associations():
    """The ROUTE has to fold, not just the helper.

    Testing `_aggregate_repetition_associations` alone left the wiring
    unpinned: replacing the call at the payload site with the raw pair list
    kept every helper assertion green while shipping the product-sized array
    again.
    """
    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    phrase = "一起去吃饭吧"
    candidates = [
        {
            "language": "zh-CN",
            "phrase": phrase,
            "normalized_phrase": phrase,
            "occurrence_count": 3,
            "message_count": 3,
            "status": "pending",
        }
    ]
    # Three window patterns all associate with that single candidate, so an
    # unfolded payload carries three rows and a folded one carries exactly one.
    patterns = [
        {
            "language": "zh-CN",
            "phrase": text,
            "normalized_phrase": text,
            "detected_count": count,
            "regen_triggered_count": 0,
            "regen_guard_passed_count": 0,
            "blocked_count": 0,
            "reasons": {"bm25": 1},
        }
        for text, count in (
            (phrase, 4),
            ("一起去吃饭", 5),
            ("去吃饭吧", 6),
        )
    ]

    client = SimpleNamespace(
        post=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"success": True, "candidates": candidates},
            )
        )
    )
    config = _storage_aware(
        SimpleNamespace(aload_characters=AsyncMock(return_value={}))
    )
    effect_store = SimpleNamespace(
        query_effects=MagicMock(
            return_value={"source_available": True, "patterns": patterns}
        )
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client,
            "get_internal_http_client",
            return_value=client,
        ),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
        patch.object(memory_router, "character_memory_exists", return_value=True),
    ):
        result = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name="test_char",
                language="zh-CN",
                effect_days=7,
            )
        )

    associations = result["associations"]
    assert len(associations) == 1, "the route shipped one row per pattern again"
    assert associations[0]["effect_pattern_count"] == 3
    # The card's number is the sum across all three patterns, unchanged.
    assert associations[0]["detected_count"] == 15
    assert associations[0]["association_type"] == "exact"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_insight_characters_applies_the_analysis_routes_admission_rule():
    """The selector must never offer a name the route answers 422 to.

    A historical unsafe name such as "." can still sit in characters.json --
    the delete route keeps a rescue path for exactly that state -- and the
    selector admitted any non-empty configured string. The reserved-route
    exception is deliberate and shared with the route, so "chat" stays.
    """
    from main_routers import memory_router
    from utils import config_manager

    memory_dir = Path(tempfile.mkdtemp()) / "memory"
    memory_dir.mkdir()
    # A disk-derived candidate the rule NORMALIZES rather than rejects: the
    # route would ask about "Bob", which has no memory, so offering the
    # trailing-space spelling means offering a name the route cannot serve.
    (memory_dir / "recent_Bob .json").write_text("[]", encoding="utf-8")
    config = SimpleNamespace(
        aload_characters=AsyncMock(
            return_value={"猫娘": {"Alice": {}, ".": {}, "..": {}, "chat": {}}}
        ),
        memory_dir=str(memory_dir),
        project_memory_dir=str(Path(tempfile.mkdtemp()) / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        result = await memory_router.get_insight_characters()

    characters = result["characters"]
    assert "Alice" in characters
    assert "chat" in characters, "the reserved-route exception is intentional"
    assert "." not in characters, "the route rejects this with unsafe_dot"
    assert ".." not in characters
    assert "Bob " not in characters, "the disk side skipped the same rule"
    assert "Bob" not in characters

    # Anti-drift, stated as the route states it.
    from utils.character_name import validate_character_name

    for name in ("Alice", "chat", ".", ".."):
        validation = validate_character_name(name, allow_dots=True)
        admitted = validation.ok or validation.code == "reserved_route_name"
        assert (name in characters) is admitted, name

@pytest.mark.unit
@pytest.mark.asyncio
async def test_insight_selector_and_route_share_the_name_length_cap():
    """The public route and the panel must use the INTERNAL route's cap.

    Without it an over-long name was offered, accepted by the public route,
    rejected by the internal one with 400, and remapped to
    "local memory analysis unavailable" -- a 503 that sends the user hunting a
    memory-server fault that does not exist.

    The constant-equality line is deliberate: deriving the fixture from the cap
    alone would let a change to the cap silently re-derive the test.
    """
    from main_routers import memory_router
    from utils import config_manager
    from utils.character_name import PROFILE_NAME_MAX_UNITS

    assert PROFILE_NAME_MAX_UNITS == 60
    too_long = "L" * (PROFILE_NAME_MAX_UNITS + 2)
    at_cap = "S" * PROFILE_NAME_MAX_UNITS

    config = SimpleNamespace(
        aload_characters=AsyncMock(
            return_value={"猫娘": {too_long: {}, at_cap: {}}}
        ),
        memory_dir=str(Path(tempfile.mkdtemp()) / "absent"),
        project_memory_dir=str(Path(tempfile.mkdtemp()) / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        listed = (await memory_router.get_insight_characters())["characters"]

    assert too_long not in listed, "selector offered a name the analysis route 400s"
    assert at_cap in listed, "the cap itself must still be analyzable"

    # The route half: fixing only the selector leaves a direct POST, or a stale
    # panel, answering 503 instead of naming the real problem.
    response = await memory_router.repetition_insights(
        memory_router.RepetitionInsightsRequest(
            character_name=too_long, language="en", effect_days=7
        )
    )
    assert response.status_code == 422

@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_padded_configured_key_is_served_not_confused_with_an_orphan(
    tmp_path,
):
    """The selector offers a normalized name; the route has to mean the same one.

    ``_insight_selectable_name`` returns the NORMALIZED name and the panel trims
    it again before posting, but characters.json keys are not normalized. A key
    carrying padding was therefore offered as its trimmed form and then failed
    the raw membership test -- a 404 on a name the panel had just listed, which
    is the drift the selector docstring says cannot happen.

    The 404 is the mild half. An unrelated ``memory/<trimmed>/`` left behind by
    a delete satisfies the existence arm instead, so the panel reads that orphan
    and the reset button clears ITS aggregates rather than the configured
    character's.

    Nothing in this repo writes a padded key -- every characters.json writer
    strips first -- so this needs a hand-edited config or one from an older
    build. Both routes are new here, and the invariant is theirs.
    """
    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    padded = "\u3000Bob"           # ideographic space, which str.strip removes
    memory_dir = tmp_path / "memory"
    (memory_dir / padded).mkdir(parents=True)
    (memory_dir / padded / "time_indexed.db").write_bytes(b"")
    # The orphan a previous delete could leave behind, under the TRIMMED name.
    (memory_dir / "Bob").mkdir()
    (memory_dir / "Bob" / "time_indexed.db").write_bytes(b"")

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {padded: {}}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )

    with patch.object(config_manager, "get_config_manager", return_value=config):
        listed = await memory_router.get_insight_characters()
    assert "Bob" in listed["characters"], (
        "the selector stopped offering the padded key at all"
    )

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "success": True,
            "schema_version": "natural-expression-candidates/v1",
            "artifact_type": "user_review_candidates",
            "candidates": [],
        },
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    effect_store = SimpleNamespace(
        query_effects=MagicMock(return_value=_empty_effects()),
        clear_effects=MagicMock(return_value={"cleared": True}),
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client, "get_internal_http_client", return_value=client
        ),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
    ):
        result = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name="Bob", language="zh-CN",
            )
        )

    assert getattr(result, "status_code", 200) != 404, (
        "the route rejected a name its own selector had just offered"
    )
    url = client.post.await_args.args[0]
    assert url.endswith("/%E3%80%80Bob/repetition_insights"), (
        "the public route posted the trimmed name: %r" % url
    )

    # The URL alone is a weaker claim than the one this test makes. The
    # internal route validated and then re-stripped, so the padded key was
    # undone on arrival and the analysis read memory/Bob/ anyway -- the
    # orphan. Drive that route with the segment the public one just posted
    # and assert which identity actually reaches the history read.
    from urllib.parse import unquote

    from app.memory_server import routes as memory_server_routes

    asked = []

    from memory.timeindex import LatestAssistantTexts

    async def _retrieve(name, limit):
        asked.append(name)
        return LatestAssistantTexts([], True)

    segment = unquote(url.rsplit("/", 2)[-2])
    with patch.object(
        memory_server_routes,
        "runtime",
        SimpleNamespace(
            time_manager=SimpleNamespace(
                aretrieve_latest_assistant_texts=_retrieve
            )
        ),
    ):
        await memory_server_routes.repetition_insights(
            segment,
            memory_server_routes.RepetitionInsightsRequest(language="zh-CN"),
        )
    assert asked == [padded], (
        "the internal route read the orphan memory/Bob/ instead of the "
        "configured character: %r" % asked
    )
    # The aggregates half of the same response reads the configured key too.
    effect_store.query_effects.assert_called_once_with(padded, 30)

    # The reset button shares the shape, and there the wrong target is
    # destructive rather than merely wrong.
    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
    ):
        await memory_router.reset_repetition_effects(
            memory_router.RepetitionEffectsResetRequest(character_name="Bob")
        )

    effect_store.clear_effects.assert_called_once_with(padded)

@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_ids_follow_the_survivors_not_the_last_n(monkeypatch):
    """The survivors stopped being a contiguous suffix, and the ids followed them.

    The budget drops the oldest message that is over its FAIR SHARE, which can be
    an interior one -- that is what keeps a short old reply from being thrown
    away for a heavy new one. Reconstructing the window as "the last N ids" then
    silently shifts every id: a reply that was mined goes unattributed while one
    that was dropped gets credited, and the panel labels the wrong turns.

    Two short replies, four budget-sized ones, one short: the victim is the
    first heavy reply, at position 3, so the survivors are 1,2,4,5,6,7.
    """
    from app.memory_server import routes
    from utils import natural_expression_candidates as candidate_core

    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    phrase = "我们一起去公园散步吧"
    fillers = "\u554a\u55ef\u597d\u5462\u5440\u54e6\u5427"
    shapes = "SSHHHHS"
    messages = [
        (fillers[index % len(fillers)] + " " + phrase + " "
         + fillers[(index + 1) % len(fillers)])
        if shape == "S"
        else ("\u5c0f" * budget)
        for index, shape in enumerate(shapes)
    ]
    ids = ["id-%d" % (index + 1) for index in range(len(shapes))]

    history = SimpleNamespace(
        messages=messages,
        source_available=True,
        skipped_row_count=0,
        response_ids=list(ids),
    )
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(return_value=history)
    )

    with patch.object(routes.runtime, "time_manager", time_manager):
        result = await routes.repetition_insights(
            "test_char",
            routes.RepetitionInsightsRequest(
                language="zh-CN", assistant_message_limit=25
            ),
        )

    summary = result["summary"]
    survivors = summary["analyzed_source_lines"]
    assert summary["messages_truncated"] is True
    assert survivors != list(range(1, len(shapes) + 1))[-len(survivors):], (
        "the fixture no longer produces a non-contiguous window, so it cannot "
        "tell the two reconstructions apart"
    )

    expected = [ids[position - 1] for position in survivors]
    assert result["_anti_repeat_response_ids"] == expected, (
        "the ids were reconstructed from the count instead of the survivors"
    )
    # Named explicitly, so a future change to the eviction cannot quietly make
    # this test agree with the wrong answer.
    assert expected == ["id-1", "id-2", "id-4", "id-5", "id-6", "id-7"]

def test_an_interrupted_database_migration_can_be_retried(tmp_path, monkeypatch):
    """The ORDER is what makes an interrupted run recoverable.

    An uncheckpointed WAL holds committed rows, so moving the database without
    it loses them. Moving the database FIRST makes that unrecoverable: the
    database is gone from the root afterwards, so the guard that starts the step
    is false on every later run and the WAL is stranded for good.

    Sidecars first and the database last means a crash always leaves the
    database still flat, and the whole step simply runs again.
    """
    import shutil

    import memory as memory_pkg

    def fresh():
        root = tmp_path / ("memory%d" % fresh.count)
        fresh.count += 1
        root.mkdir(parents=True)
        (root / "time_indexed_Carol.db").write_text("db", encoding="utf-8")
        (root / "time_indexed_Carol.db-wal").write_text("wal", encoding="utf-8")
        return root

    fresh.count = 0

    # Ordinary: both move, nothing left behind.
    root = fresh()
    memory_pkg.migrate_to_character_dirs(str(root), [])
    assert (root / "Carol" / "time_indexed.db").exists()
    assert (root / "Carol" / "time_indexed.db-wal").read_text(
        encoding="utf-8"
    ) == "wal"
    assert [entry.name for entry in root.iterdir()] == ["Carol"]

    # A sidecar that cannot move leaves the DATABASE where it is, so the next
    # run retries the whole set rather than stranding what did not move.
    root = fresh()
    real_move = shutil.move

    def refuse_the_sidecar(source, target):
        if str(source).endswith("-wal"):
            raise OSError("disk full")
        return real_move(source, target)

    monkeypatch.setattr(memory_pkg.shutil, "move", refuse_the_sidecar)
    memory_pkg.migrate_to_character_dirs(str(root), [])
    assert (root / "time_indexed_Carol.db").exists(), (
        "the database moved without its WAL, which strands the WAL for good"
    )

    monkeypatch.setattr(memory_pkg.shutil, "move", real_move)
    memory_pkg.migrate_to_character_dirs(str(root), [])
    assert (root / "Carol" / "time_indexed.db").exists()
    assert (root / "Carol" / "time_indexed.db-wal").exists()

    # And the state a crash between the two moves leaves -- WAL across,
    # database still flat -- is NOT finished by the next run, which is a
    # deliberate reversal of what this test used to assert.
    #
    # A destination sidecar with no destination database is ambiguous: it is
    # either our own half-finished move, or a stale WAL left over from a
    # database that no longer exists. Nothing on disk tells the two apart,
    # and SQLite will replay a foreign WAL of the same page size into
    # whatever database it finds beside it -- so finishing the move can
    # silently REPLACE the rows of the very history this step exists to
    # preserve.
    #
    # Between "recovers automatically, and sometimes corrupts" and "never
    # corrupts, and needs a hand", the second is the one to keep: the
    # database stays flat and intact, and the log says why.
    root = fresh()
    (root / "Carol").mkdir()
    real_move(
        str(root / "time_indexed_Carol.db-wal"),
        str(root / "Carol" / "time_indexed.db-wal"),
    )
    memory_pkg.migrate_to_character_dirs(str(root), [])
    assert not (root / "Carol" / "time_indexed.db").exists(), (
        "the database was paired with a WAL nothing proves belongs to it"
    )
    assert (root / "time_indexed_Carol.db").read_text(encoding="utf-8") == "db", (
        "the source database was not left intact where it was"
    )
    assert (root / "Carol" / "time_indexed.db-wal").read_text(
        encoding="utf-8"
    ) == "wal", "the sidecar that had already crossed was touched"

def test_a_decoded_owner_must_be_a_name_this_project_would_accept(tmp_path):
    """A legacy filename is not a validated identity.

    The decoded string goes straight into a path, and on Windows "Bob." and
    "Bob " both resolve to "Bob" -- so "time_indexed_Bob..db" migrated an
    unrelated orphan INTO the real Bob's directory as his time_indexed.db.
    Measured on the platform this ships on, not reasoned about.

    The equality check is what catches the whitespace form: validation STRIPS
    before it judges, so "Bob " passes as "Bob" unless the result is required to
    be the name that was decoded.
    """
    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "Bob").mkdir()
    (memory_dir / "Bob" / "facts.json").write_text("REAL BOB", encoding="utf-8")
    (memory_dir / "time_indexed_Bob..db").write_text("ORPHAN", encoding="utf-8")
    (memory_dir / "time_indexed_Bob .db").write_text("ORPHAN", encoding="utf-8")
    (memory_dir / "time_indexed_Alice.db").write_text("ALICE", encoding="utf-8")

    memory_pkg.migrate_to_character_dirs(str(memory_dir), ["Bob"])

    assert not (memory_dir / "Bob" / "time_indexed.db").exists(), (
        "an unrelated orphan was attached to a different character as its "
        "own history"
    )
    assert (memory_dir / "time_indexed_Bob..db").exists()
    assert (memory_dir / "time_indexed_Bob .db").exists()
    # The dual, so this is not "refuse everything": a safe owner still migrates.
    assert (memory_dir / "Alice" / "time_indexed.db").read_text(
        encoding="utf-8"
    ) == "ALICE"

    # The predicate's own contract, pinned directly. The trailing DOT is what
    # corrupts today -- makedirs creates "Bob", the move succeeds -- and the
    # validator catches it. The trailing SPACE currently fails loudly on this
    # platform instead, so asserting it through an end-to-end effect would
    # assert nothing; the rule "the decoded name must be usable as-is" is the
    # thing worth holding, and it is what keeps a filesystem that resolves
    # the two silently from turning the space form into the dot form.
    assert not memory_pkg._decoded_owner_is_safe("Bob.")
    assert not memory_pkg._decoded_owner_is_safe("Bob ")
    assert not memory_pkg._decoded_owner_is_safe("CON")
    assert not memory_pkg._decoded_owner_is_safe("../Bob")
    assert memory_pkg._decoded_owner_is_safe("Bob")
    assert memory_pkg._decoded_owner_is_safe("Bob.Smith"), (
        "a legitimately dotted name stopped migrating"
    )

def test_a_partial_sidecar_move_is_rolled_back(tmp_path, monkeypatch):
    """All-or-nothing beats an ordering argument about who opens what when.

    Keeping the database while one of its sidecars has already gone leaves a
    source that no longer carries its own WAL, so anything opening it before the
    retry reads a database missing committed rows.
    """
    import shutil

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "time_indexed_Carol.db").write_text("db", encoding="utf-8")
    (memory_dir / "time_indexed_Carol.db-wal").write_text("wal", encoding="utf-8")
    (memory_dir / "time_indexed_Carol.db-shm").write_text("shm", encoding="utf-8")

    real_move = shutil.move

    def refuse_the_shm(source, target):
        if str(source).endswith("-shm"):
            raise OSError("disk full")
        return real_move(source, target)

    monkeypatch.setattr(memory_pkg.shutil, "move", refuse_the_shm)
    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])

    assert (memory_dir / "time_indexed_Carol.db").exists()
    assert (memory_dir / "time_indexed_Carol.db-wal").read_text(
        encoding="utf-8"
    ) == "wal", "the WAL that had already moved was not put back"

    # And the retry, once the failure clears, completes the whole set.
    monkeypatch.setattr(memory_pkg.shutil, "move", real_move)
    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])
    for name in ("time_indexed.db", "time_indexed.db-wal", "time_indexed.db-shm"):
        assert (memory_dir / "Carol" / name).exists(), name

@pytest.mark.asyncio
async def test_a_symlinked_character_directory_is_not_offered(tmp_path):
    """``Path.is_dir()`` follows links, so the root could name anything.

    A symlink in the memory root was offered as a character and
    ``character_memory_exists`` confirmed it, so the panel would read, render
    and export assistant-shaped rows from whatever database it points at --
    outside the memory root entirely.

    Fixed in the enumeration rather than in the shared reader on purpose:
    ``_resolve_expected_db_path`` honours ``time_store``, which exists so a
    character CAN register a database outside memory_dir, and a blanket
    containment check there would break it.
    """
    import os

    from main_routers import memory_router
    from utils import config_manager

    memory_dir = tmp_path / "memory"
    outside = tmp_path / "elsewhere"
    memory_dir.mkdir()
    outside.mkdir()
    (outside / "time_indexed.db").write_bytes(b"")
    (memory_dir / "Real").mkdir()
    (memory_dir / "Real" / "time_indexed.db").write_bytes(b"")
    try:
        os.symlink(str(outside), str(memory_dir / "Ghost"), target_is_directory=True)
    except OSError:
        pytest.skip("this environment does not permit symlinks")

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        characters = (await memory_router.get_insight_characters())["characters"]

    assert "Ghost" not in characters, (
        "a symlink out of the memory root was offered as a character"
    )
    # The dual: a real directory beside it is still offered.
    assert "Real" in characters



@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_recent_file_listing_does_not_readmit_symlinks(tmp_path):
    """Filtering only the directory scan left this path to let them back in.

    ``iter_recent_memory_files`` is what the insights selector AND the memory
    browser both enumerate from, and both of its branches follow links:
    ``is_dir()`` on a linked character directory, and ``is_file()`` on a linked
    ``recent_<name>.json``. Either shape put a name back in the selector whose
    contents live outside the memory root.
    """
    import os

    from main_routers import memory_router
    from utils import config_manager

    memory_dir = tmp_path / "memory"
    outside = tmp_path / "elsewhere"
    memory_dir.mkdir()
    outside.mkdir()
    (outside / "recent.json").write_text("[]", encoding="utf-8")
    (tmp_path / "flat.json").write_text("[]", encoding="utf-8")
    try:
        os.symlink(str(outside), str(memory_dir / "Ghost"), target_is_directory=True)
        os.symlink(str(tmp_path / "flat.json"), str(memory_dir / "recent_Phantom.json"))
    except OSError:
        pytest.skip("this environment does not permit symlinks")

    # Real entries of BOTH shapes, so this cannot pass by listing nothing.
    (memory_dir / "Real").mkdir()
    (memory_dir / "Real" / "recent.json").write_text("[]", encoding="utf-8")
    (memory_dir / "recent_Legacy.json").write_text("[]", encoding="utf-8")

    listing = memory_router.iter_recent_memory_files(memory_dir)
    assert "recent_Ghost.json" not in listing
    assert "recent_Phantom.json" not in listing
    assert "recent_Real.json" in listing
    assert "recent_Legacy.json" in listing

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        characters = (await memory_router.get_insight_characters())["characters"]

    assert "Ghost" not in characters, "a linked directory came back via recent files"
    assert "Phantom" not in characters, "a linked recent file named a character"
    assert "Real" in characters
    assert "Legacy" in characters


def test_a_symlinked_legacy_file_is_never_migrated(tmp_path):
    """Moving a link puts it INSIDE the character namespace.

    ``shutil.move`` recreates the link at the destination, so
    ``memory/<name>/time_indexed.db`` becomes a link out of the memory root and
    every reader follows it from then on. Left flat it is read by nothing, so
    migrating it is strictly worse than not.
    """
    import os

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "outside.db"
    outside.write_text("EXTERNAL", encoding="utf-8")
    try:
        os.symlink(str(outside), str(memory_dir / "time_indexed_Carol.db"))
    except OSError:
        pytest.skip("this environment does not permit symlinks")
    (memory_dir / "time_indexed_Real.db").write_text("REAL", encoding="utf-8")

    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])

    assert (memory_dir / "time_indexed_Carol.db").is_symlink(), (
        "the link was migrated into the character namespace"
    )
    assert not (memory_dir / "Carol").exists()
    # The dual: a real legacy file still migrates, so this is not "refuse all".
    assert (memory_dir / "Real" / "time_indexed.db").read_text(
        encoding="utf-8"
    ) == "REAL"


@pytest.mark.unit
def test_a_symlinked_legacy_file_is_not_migrated_for_a_configured_name(tmp_path):
    """Every name in ``names`` bypasses owner discovery.

    The link check was added to ``_legacy_root_file_owners``, which only ever
    sees names the root DISCOVERS. A character listed in characters.json goes
    straight into the migration loop, so a linked ``time_indexed_Carol.db``
    still satisfied ``exists()`` and was recreated as
    ``memory/Carol/time_indexed.db`` -- inside the namespace, where the
    read-only time-indexed reader follows it and the insights panel can render
    and export rows from an arbitrary external database.
    """
    import os

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "outside.db"
    outside.write_text("EXTERNAL", encoding="utf-8")
    try:
        os.symlink(str(outside), str(memory_dir / "time_indexed_Carol.db"))
    except OSError:
        pytest.skip("this environment does not permit symlinks")
    (memory_dir / "time_indexed_Real.db").write_text("REAL", encoding="utf-8")

    # CONFIGURED, unlike the discovery case: the name is handed in.
    memory_pkg.migrate_to_character_dirs(str(memory_dir), ["Carol", "Real"])

    assert not (memory_dir / "Carol" / "time_indexed.db").exists(), (
        "a configured character's link was migrated into the namespace"
    )
    assert (memory_dir / "time_indexed_Carol.db").is_symlink(), (
        "the link was moved rather than left where nothing reads it"
    )
    # The dual: a real legacy file for a configured name still migrates.
    assert (memory_dir / "Real" / "time_indexed.db").read_text(
        encoding="utf-8"
    ) == "REAL"


@pytest.mark.unit
def test_a_symlinked_sidecar_is_never_migrated(tmp_path):
    """shutil.move recreates a link at the destination, sidecars included.

    The database itself is refused when it is a link, but a linked -wal, -shm
    or -journal beside an ordinary database went straight through and
    installed itself as memory/<name>/time_indexed.db-wal. Every later SQLite
    open then follows it out of the namespace -- to read AND to write.

    The whole entry is refused rather than that one sidecar: moving the
    database without its WAL is exactly the split state the move ORDER exists
    to avoid.
    """
    import os

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "outside.db-wal"
    outside.write_bytes(b"EXTERNAL")
    (memory_dir / "time_indexed_Carol.db").write_bytes(b"CAROL")
    try:
        os.symlink(str(outside), str(memory_dir / "time_indexed_Carol.db-wal"))
    except OSError:
        pytest.skip("this environment does not permit symlinks")
    (memory_dir / "time_indexed_Real.db").write_bytes(b"REAL")
    (memory_dir / "time_indexed_Real.db-wal").write_bytes(b"REALWAL")

    memory_pkg.migrate_to_character_dirs(str(memory_dir), ["Carol", "Real"])

    assert not (memory_dir / "Carol" / "time_indexed.db-wal").exists(), (
        "a linked sidecar was installed inside the character namespace"
    )
    assert not (memory_dir / "Carol" / "time_indexed.db").exists(), (
        "the database moved without its linked sidecar, which is the split "
        "state the ordering exists to prevent"
    )
    assert (memory_dir / "time_indexed_Carol.db").read_bytes() == b"CAROL"
    # The dual: an ordinary database with an ordinary sidecar still migrates.
    assert (memory_dir / "Real" / "time_indexed.db").read_bytes() == b"REAL"
    assert (memory_dir / "Real" / "time_indexed.db-wal").read_bytes() == b"REALWAL"


@pytest.mark.unit
def test_a_failed_database_move_puts_its_sidecars_back(tmp_path):
    """Unlike a crash, this returns and startup carries on.

    So the runtime looks in the character directory and finds sidecars with
    no database, while the authoritative one sits stranded in the root -- and
    a writable open then creates a fresh database beside a WAL that does not
    belong to it. The sidecar-failure branch already rolled back; the
    database-failure branch simply never did.
    """
    import shutil

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "time_indexed_Carol.db").write_bytes(b"CAROL")
    (memory_dir / "time_indexed_Carol.db-wal").write_bytes(b"WAL")

    real_move = shutil.move

    def _the_database_will_not_move(source, destination, *args, **kwargs):
        if str(source).endswith("time_indexed_Carol.db"):
            raise OSError(13, "The process cannot access the file")
        return real_move(source, destination, *args, **kwargs)

    with patch.object(memory_pkg.shutil, "move", _the_database_will_not_move):
        memory_pkg.migrate_to_character_dirs(str(memory_dir), ["Carol"])

    assert (memory_dir / "time_indexed_Carol.db").read_bytes() == b"CAROL"
    assert (memory_dir / "time_indexed_Carol.db-wal").read_bytes() == b"WAL", (
        "the sidecar was left in the character directory with no database "
        "beside it"
    )
    assert not (memory_dir / "Carol" / "time_indexed.db-wal").exists(), (
        "a sidecar stayed migrated while its database did not"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_configured_character_with_a_linked_directory_is_refused(
    tmp_path,
):
    """The configured branch skips the only filesystem check there was.

    Configured names are added to the selector before directory enumeration,
    so the symlink filter there never sees them. The read-only lookup then
    builds memory_dir/<name>/time_indexed.db and follows the link, and the
    panel renders and exports assistant-shaped rows from a database outside
    the memory root. The sidecar STORES are not reachable this way because
    they already ask the same containment question.

    An explicitly registered time_store path is the deliberate case and stays
    permitted -- that is what the field is for.
    """
    import os

    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "time_indexed.db").write_bytes(b"EXTERNAL")
    try:
        os.symlink(str(outside), str(memory_dir / "Carol"), target_is_directory=True)
    except OSError:
        pytest.skip("this environment does not permit symlinks")

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "success": True,
            "schema_version": "natural-expression-candidates/v1",
            "artifact_type": "user_review_candidates",
            "candidates": [],
        },
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    effect_store = SimpleNamespace(
        query_effects=MagicMock(return_value=_empty_effects())
    )

    # The shape get_character_data really produces: a DEFAULT entry for
    # every configured character. Supplying an empty store here is what let
    # the first version of this guard pass while doing nothing at all.
    default_store = {
        "Carol": os.path.join(str(memory_dir), "Carol", "time_indexed.db")
    }

    async def _ask(time_store):
        config = SimpleNamespace(
            aload_characters=AsyncMock(return_value={"猫娘": {"Carol": {}}}),
            memory_dir=str(memory_dir),
            get_character_data=lambda: (None,) * 6 + (time_store,) + (None, None),
        )
        with (
            patch.object(
                config_manager, "get_config_manager", return_value=config
            ),
            patch.object(
                internal_http_client,
                "get_internal_http_client",
                return_value=client,
            ),
            patch.object(
                anti_repeat_effects,
                "get_anti_repeat_effect_store",
                return_value=effect_store,
            ),
        ):
            return await memory_router.repetition_insights(
                memory_router.RepetitionInsightsRequest(
                    character_name="Carol", language="en"
                )
            )

    client.post.reset_mock()
    refused = await _ask(default_store)
    assert getattr(refused, "status_code", 200) == 404, (
        "a configured character whose directory is a link was analysed"
    )
    assert client.post.await_count == 0, (
        "the analysis ran before the refusal"
    )

    # And with no entry at all, which is the same situation.
    client.post.reset_mock()
    assert getattr(await _ask({}), "status_code", 200) == 404

    # The dual: pointed somewhere ELSE explicitly, it is a choice rather
    # than a leak. A different path, not merely a present key.
    client.post.reset_mock()
    allowed = await _ask({"Carol": str(outside / "time_indexed.db")})
    assert getattr(allowed, "status_code", 200) != 404, (
        "an explicitly registered time_store path was refused"
    )
    assert client.post.await_count == 1


@pytest.mark.unit
def test_time_store_carries_the_default_path_for_a_configured_character(tmp_path):
    """The premise the containment carve-out rests on.

    ``_default_memory_dir_escapes_root`` treats a ``time_store`` entry as a
    deliberate override only when it DIFFERS from the default path. That is
    only correct because ``get_character_data`` auto-populates a default
    entry for every configured character.

    Treating mere MEMBERSHIP as the override -- the first version of this --
    made the check return False for exactly the case it exists to catch, so
    it never ran in production at all, while its own test passed because the
    double supplied an empty store. The premise is pinned here rather than
    assumed at the call site: if it ever stops holding, "present" becomes
    meaningful again and the carve-out needs rewriting, not silently
    changing meaning.
    """
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    with patch.object(
        ConfigManager,
        "_get_documents_directory",
        return_value=tmp_path / "runtime-parent",
    ), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[standard_root],
    ):
        manager = ConfigManager("N.E.K.O")
    manager._get_standard_data_directory_candidates = lambda: [standard_root]

    characters_path = Path(manager.config_dir) / "characters.json"
    characters_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        characters = json.loads(characters_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        characters = {}
    characters["猫娘"] = {"Carol": characters.get("猫娘", {}).get("Carol", {})}
    characters_path.write_text(
        json.dumps(characters, ensure_ascii=False), encoding="utf-8"
    )
    manager._characters_cache = None
    manager._characters_cache_mtime = None

    time_store = manager.get_character_data()[6]

    assert time_store.get("Carol") == os.path.join(
        str(manager.memory_dir), "Carol", "time_indexed.db"
    ), (
        "time_store no longer carries the DEFAULT path for a configured "
        "character. 'Present in time_store' has become meaningful again, so "
        "_default_memory_dir_escapes_root must be rewritten rather than "
        "quietly changing meaning: %r" % (time_store,)
    )


@pytest.mark.unit
def test_a_symlinked_destination_directory_is_never_written_through(tmp_path):
    """ensure_character_dir accepts an existing memory/<name> that is a link.

    Every shutil.move below it then writes THROUGH the link, so an orphan's
    authoritative database and its sidecars leave the memory root at startup.
    Refusing a linked SOURCE was only half of the same move.
    """
    import os

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (memory_dir / "time_indexed_Ghost.db").write_bytes(b"GHOST")
    try:
        os.symlink(str(outside), str(memory_dir / "Ghost"), target_is_directory=True)
    except OSError:
        pytest.skip("this environment does not permit symlinks")
    (memory_dir / "time_indexed_Real.db").write_bytes(b"REAL")

    memory_pkg.migrate_to_character_dirs(str(memory_dir), [])

    assert not (outside / "time_indexed.db").exists(), (
        "the database was moved outside the memory root through a linked "
        "destination directory"
    )
    assert (memory_dir / "time_indexed_Ghost.db").read_bytes() == b"GHOST"
    # The dual: an ordinary discovered owner still migrates.
    assert (memory_dir / "Real" / "time_indexed.db").read_bytes() == b"REAL"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_symlinked_default_database_is_refused(tmp_path):
    """The directory can be real while the database under it is a link.

    Containment on memory/<name> alone passes then, and the read-only
    SQLAlchemy path follows a FILE link exactly as it would a directory one
    -- so the panel could still render and export rows from an external
    database.
    """
    import os

    from main_routers import memory_router
    from memory import anti_repeat_effects
    from utils import config_manager, internal_http_client

    memory_dir = tmp_path / "memory"
    (memory_dir / "Carol").mkdir(parents=True)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"EXTERNAL")
    try:
        os.symlink(str(outside), str(memory_dir / "Carol" / "time_indexed.db"))
    except OSError:
        pytest.skip("this environment does not permit symlinks")

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "success": True,
            "schema_version": "natural-expression-candidates/v1",
            "artifact_type": "user_review_candidates",
            "candidates": [],
        },
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    effect_store = SimpleNamespace(
        query_effects=MagicMock(return_value=_empty_effects())
    )
    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"Carol": {}}}),
        memory_dir=str(memory_dir),
        get_character_data=lambda: (None,) * 6
        + ({"Carol": os.path.join(str(memory_dir), "Carol", "time_indexed.db")},)
        + (None, None),
    )

    with (
        patch.object(config_manager, "get_config_manager", return_value=config),
        patch.object(
            internal_http_client,
            "get_internal_http_client",
            return_value=client,
        ),
        patch.object(
            anti_repeat_effects,
            "get_anti_repeat_effect_store",
            return_value=effect_store,
        ),
    ):
        refused = await memory_router.repetition_insights(
            memory_router.RepetitionInsightsRequest(
                character_name="Carol", language="en"
            )
        )

    assert getattr(refused, "status_code", 200) == 404, (
        "a linked default database was analysed"
    )
    assert client.post.await_count == 0, "the analysis ran before the refusal"


@pytest.mark.unit
def test_a_symlinked_effect_sidecar_is_never_read(tmp_path):
    """The write path has enforced containment since it was written.

    The read path asked nothing and simply joined the components, so a real
    memory/<name>/ holding "anti_repeat_effects.json" as a LINK was followed
    by open() -- and a link to another character's sidecar has the panel
    render and associate one identity's private repeated phrases under
    another's name.
    """
    import json as json_module
    import os

    from memory import anti_repeat_effects

    memory_dir = tmp_path / "memory"
    (memory_dir / "Carol").mkdir(parents=True)
    (memory_dir / "Dave").mkdir(parents=True)
    private = {
        "version": 1,
        "phrases": {"dave only": {"count": 3}},
    }
    (memory_dir / "Dave" / "anti_repeat_effects.json").write_text(
        json_module.dumps(private), encoding="utf-8"
    )
    try:
        os.symlink(
            str(memory_dir / "Dave" / "anti_repeat_effects.json"),
            str(memory_dir / "Carol" / "anti_repeat_effects.json"),
        )
    except OSError:
        pytest.skip("this environment does not permit symlinks")

    with patch.object(
        anti_repeat_effects,
        "get_config_manager",
        lambda: SimpleNamespace(memory_dir=str(memory_dir)),
    ):
        store = anti_repeat_effects.AntiRepeatEffectStore()

    assert store._existing_file_path("Carol") is None, (
        "a linked sidecar was offered to the reader"
    )
    # The dual: the character that really owns the file still reads it.
    assert store._existing_file_path("Dave") == os.path.join(
        str(memory_dir), "Dave", "anti_repeat_effects.json"
    )


@pytest.mark.unit
def test_a_dangling_destination_sidecar_also_stops_the_move(tmp_path):
    """exists() answers False for a broken link, and it is still there.

    So a dangling "time_indexed.db-wal" at the destination slipped past the
    ambiguity check and the database moved in beside it -- exactly the
    pairing that check exists to refuse, and a link SQLite would then follow
    or replace.
    """
    import os

    import memory as memory_pkg

    memory_dir = tmp_path / "memory"
    (memory_dir / "Carol").mkdir(parents=True)
    (memory_dir / "time_indexed_Carol.db").write_bytes(b"CAROL")
    try:
        os.symlink(
            str(tmp_path / "gone.db-wal"),
            str(memory_dir / "Carol" / "time_indexed.db-wal"),
        )
    except OSError:
        pytest.skip("this environment does not permit symlinks")
    assert not os.path.exists(memory_dir / "Carol" / "time_indexed.db-wal"), (
        "the fixture link is not dangling, so nothing here is tested"
    )

    memory_pkg.migrate_to_character_dirs(str(memory_dir), ["Carol"])

    assert not (memory_dir / "Carol" / "time_indexed.db").exists(), (
        "the database was moved in beside a dangling sidecar link"
    )
    assert (memory_dir / "time_indexed_Carol.db").read_bytes() == b"CAROL", (
        "the source database was not left intact"
    )


def test_a_malformed_history_row_is_skipped_not_fatal(tmp_path):
    """One damaged row must cost that row, not the whole request.

    The row filter used to read ``json_valid(x) AND json_extract(x, ...)``.
    Short-circuit evaluation of AND is not something SQLite promises -- it may
    reorder the terms of a WHERE clause -- so a plan that evaluates the extract
    first would raise "malformed JSON" and take a 503 with it.
    """
    import sqlite3

    from memory.timeindex import _ASSISTANT_ROW_FILTER

    con = sqlite3.connect(":memory:")
    con.execute("create table t(id integer primary key, message text)")
    con.executemany(
        "insert into t(message) values(?)",
        [
            ('{"type": "ai", "data": {"content": "hi"}}',),
            ("not json at all",),
            ("{broken",),
            ('{"type": "human"}',),
        ],
    )

    rows = con.execute(
        "select id from t where " + _ASSISTANT_ROW_FILTER + " order by id"
    ).fetchall()
    assert rows == [(1,)]

    # The filter must not merely be total -- it must still select. A mutation
    # that makes it match nothing would pass the "does not raise" half alone.
    assert con.execute(
        "select count(*) from t where " + _ASSISTANT_ROW_FILTER
    ).fetchone() == (1,)

    # And the SHAPE, which is unusual to assert and is the point here: no
    # query I could construct on this SQLite build evaluates the AND form
    # eagerly, so behaviour cannot pin the choice. Reverting to
    # "json_valid(x) AND json_extract(x, ...)" is invisible to every
    # assertion above while reinstating a form SQLite does not promise to
    # short-circuit.
    assert "CASE WHEN" in _ASSISTANT_ROW_FILTER
    assert " AND json_extract" not in _ASSISTANT_ROW_FILTER


def test_a_legacy_vector_store_is_not_offered_as_a_character(tmp_path):
    """``semantic_memory_Alice/`` confirms itself, so a name is not enough.

    ``character_memory_exists("semantic_memory_Alice")`` returns True because
    that very directory is one of the paths it checks for a character of that
    name. The selector offered it, and analysing it read
    ``memory/semantic_memory_Alice/time_indexed.db`` -- a vector store, not
    Alice's history -- so the panel reported no history for a character who
    has plenty.
    """
    from utils.character_memory import is_legacy_vector_store_dir

    (tmp_path / "Alice").mkdir()
    (tmp_path / "Alice" / "time_indexed.db").write_bytes(b"")
    (tmp_path / "semantic_memory_Alice").mkdir()
    assert is_legacy_vector_store_dir(tmp_path, "semantic_memory_Alice")

    # The legacy FLAT owner counts as an owner too -- her files have not been
    # migrated into a directory yet, and her vector store is still hers.
    (tmp_path / "recent_Bob.json").write_text("[]", encoding="utf-8")
    (tmp_path / "semantic_memory_Bob").mkdir()
    assert is_legacy_vector_store_dir(tmp_path, "semantic_memory_Bob")


def test_the_vector_store_rule_needs_an_owner_not_just_the_prefix(tmp_path):
    """A character really called "semantic_memory_x" must not be hidden.

    Excluding on the prefix alone is the defect that a prefix-based exemption
    already caused once elsewhere: a legal name starting with the marker was
    treated as infrastructure and silently dropped. The owner has to exist.
    """
    from utils.character_memory import is_legacy_vector_store_dir

    (tmp_path / "semantic_memory_Nobody").mkdir()
    (tmp_path / "semantic_memory_Nobody" / "time_indexed.db").write_bytes(b"")
    assert not is_legacy_vector_store_dir(tmp_path, "semantic_memory_Nobody")

    # Ordinary names are never touched by the rule, nor is a bare prefix.
    assert not is_legacy_vector_store_dir(tmp_path, "Alice")
    assert not is_legacy_vector_store_dir(tmp_path, "semantic_memory_")

    # A name LONGER than the prefix but not carrying it. Dropping the prefix
    # check reads its tail as an owner, and a character really called that
    # tail then makes an unrelated name look like a vector store. "Alice"
    # alone could never catch that: it is shorter than the prefix, so the
    # strip yields an empty owner and the rule declines either way.
    prefix_length = len("semantic_memory_")
    impostor = "TheCharacterCalledZoe"
    assert len(impostor) > prefix_length
    (tmp_path / impostor[prefix_length:]).mkdir()
    assert not is_legacy_vector_store_dir(tmp_path, impostor)


@pytest.mark.asyncio
async def test_the_directory_door_excludes_a_vector_store_on_its_own(tmp_path):
    """Door 1, with door 2 unable to help.

    Both existing tests went through ``iter_recent_memory_files``. A vector
    store holding no ``recent.json`` is invisible to that door, so only the
    selector's own directory branch can reject it -- and removing that branch
    left every test green.
    """
    from main_routers import memory_router
    from utils import config_manager

    memory_dir = tmp_path / "memory"
    (memory_dir / "Alice").mkdir(parents=True)
    (memory_dir / "Alice" / "time_indexed.db").write_bytes(b"")
    # No recent.json anywhere in it: door 2 cannot see this at all.
    (memory_dir / "semantic_memory_Alice").mkdir()
    (memory_dir / "semantic_memory_Alice" / "index.faiss").write_bytes(b"x")

    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {}}),
        memory_dir=str(memory_dir),
        project_memory_dir=str(tmp_path / "absent"),
    )
    with patch.object(config_manager, "get_config_manager", return_value=config):
        result = await memory_router.get_insight_characters()

    assert "Alice" in result["characters"]
    assert "semantic_memory_Alice" not in result["characters"]


def test_the_vector_store_cannot_come_back_through_the_recent_file_door(tmp_path):
    """Two doors lead into the candidate set, and only one was shut.

    ``iter_recent_memory_files`` builds a logical ``recent_<name>.json`` from
    every directory holding a ``recent.json``, so
    ``semantic_memory_Alice/recent.json`` re-entered as
    ``recent_semantic_memory_Alice.json`` after the directory scan had already
    rejected it. That function's own comment warns about exactly this
    re-admission for the symlink case.
    """
    from main_routers.memory_router import (
        extract_catgirl_name_from_recent_filename,
        iter_recent_memory_files,
    )

    (tmp_path / "Alice").mkdir()
    (tmp_path / "Alice" / "recent.json").write_text("[]", encoding="utf-8")
    (tmp_path / "semantic_memory_Alice").mkdir()
    (tmp_path / "semantic_memory_Alice" / "recent.json").write_text(
        "[]", encoding="utf-8"
    )

    names = {
        name
        for name in (
            extract_catgirl_name_from_recent_filename(logical)
            for logical in iter_recent_memory_files(tmp_path)
        )
        if name
    }
    assert "Alice" in names, "the real owner must still come through this door"
    assert "semantic_memory_Alice" not in names


def test_an_unowned_vector_store_name_still_comes_through(tmp_path):
    """The ownership rule applies at this door too, not a prefix ban."""
    from main_routers.memory_router import (
        extract_catgirl_name_from_recent_filename,
        iter_recent_memory_files,
    )

    (tmp_path / "semantic_memory_Nobody").mkdir()
    (tmp_path / "semantic_memory_Nobody" / "recent.json").write_text(
        "[]", encoding="utf-8"
    )
    names = {
        name
        for name in (
            extract_catgirl_name_from_recent_filename(logical)
            for logical in iter_recent_memory_files(tmp_path)
        )
        if name
    }
    assert "semantic_memory_Nobody" in names


@pytest.mark.asyncio
async def test_insight_reads_are_refused_while_a_cloud_apply_runs(monkeypatch):
    """Read-only is not harmless during an import.

    The overwrite releases this character's SQLite handles and replaces
    ``memory/<name>/`` wholesale. A read arriving in that window reopens the
    database and CACHES the engine, and on Windows a pooled handle is enough
    to make the replacement fail. Nothing on this path took the writability
    assertion, because nothing on it writes.
    """
    from app.memory_server import routes
    from utils.cloudsave_runtime import fence

    monkeypatch.setattr(routes.runtime, "time_manager", object(), raising=False)
    monkeypatch.setattr(fence, "is_write_fence_active", lambda cm: True)

    with pytest.raises(HTTPException) as excinfo:
        await routes.repetition_insights(
            "Neko", routes.RepetitionInsightsRequest(language="zh-CN")
        )
    assert excinfo.value.status_code == 503
    assert "restored" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_an_unreadable_fence_does_not_fail_the_analysis(monkeypatch):
    """The dual: a fence we cannot read must not become a new failure mode.

    Without this the guard turns any root-state read error into a 503 on a
    path that used to work, which is a worse trade than the narrow window it
    closes.
    """
    from app.memory_server import routes
    from utils.cloudsave_runtime import fence

    def _boom(_cm):
        raise OSError("root state unavailable")

    monkeypatch.setattr(fence, "is_write_fence_active", _boom)
    # time_manager PRESENT, so the analysis really runs. Leaving it None made
    # this pass on the earlier "not fully initialized" 503, which a mutation
    # that treats an unreadable fence as active returns too -- the assertion
    # could not tell the two apart.
    manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(
            return_value=SimpleNamespace(
                messages=[],
                source_available=True,
                response_ids=[],
                skipped_row_count=0,
            )
        )
    )
    monkeypatch.setattr(routes.runtime, "time_manager", manager, raising=False)

    report = await routes.repetition_insights(
        "Neko", routes.RepetitionInsightsRequest(language="zh-CN")
    )
    assert report is not None
    manager.aretrieve_latest_assistant_texts.assert_awaited()
