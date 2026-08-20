from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from utils.llm_client import AIMessage, SQLChatMessageHistory


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
        "candidate_count": 1,
        "returned_candidate_count": 1,
        "candidates_truncated": False,
        "source_available": True,
    }
    assert result["parameters"]["assistant_message_limit"] == 25
    assert result["parameters"]["message_count_threshold"] == 3
    assert result["candidates"][0]["phrase"] == "quiet lantern"
    assert "context" not in result["candidates"][0]
    time_manager.aretrieve_latest_assistant_texts.assert_awaited_once_with(
        "test_char",
        25,
    )


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
    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {character_name: {}}})
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
    config = SimpleNamespace(aload_characters=AsyncMock(return_value={}))
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

    config = SimpleNamespace(aload_characters=AsyncMock(return_value={}))
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
    config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}})
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
