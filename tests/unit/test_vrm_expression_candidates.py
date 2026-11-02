"""Fallback candidates retain common names and supplement saved bindings."""

import json
from unittest.mock import AsyncMock

import pytest

from main_routers import vrm_router


COMMON_EXPRESSIONS = [
    "neutral", "happy", "joy", "fun", "smile", "joy_01",
    "relaxed", "content", "sad", "sorrow", "grief", "angry", "anger",
    "surprised", "surprise", "shock", "blink", "blink_l", "blink_r",
    "aa", "ih", "ou", "ee", "oh", "lookUp", "lookDown", "lookLeft", "lookRight",
]


@pytest.fixture
def emotion_config(tmp_path, monkeypatch):
    path = tmp_path / "model_emotion.json"
    monkeypatch.setattr(vrm_router, "_get_emotion_config_path", lambda _: path)
    return path


@pytest.mark.asyncio
async def test_saved_names_supplement_common_candidates_in_order(emotion_config):
    emotion_config.write_text(json.dumps({
        "happy": ["happy", "big_smile", "big_smile", "sad", " wink ", "", "  ", None, 3],
        "relaxed": "big_smile",
        "surprised": ["wink", "目笑"],
    }, ensure_ascii=False), encoding="utf-8")

    response = await vrm_router.get_model_expressions("model")

    assert response["expressions"] == COMMON_EXPRESSIONS + ["big_smile", "wink", "目笑"]
    assert response["source"] == "common_with_emotion_mapping"


@pytest.mark.asyncio
async def test_removing_a_saved_binding_does_not_shrink_common_candidates(emotion_config):
    emotion_config.write_text(json.dumps({"happy": ["happy", "big_smile"]}), encoding="utf-8")
    before = await vrm_router.get_model_expressions("model")
    emotion_config.write_text(json.dumps({"happy": ["happy"]}), encoding="utf-8")
    after = await vrm_router.get_model_expressions("model")

    assert before["expressions"] == COMMON_EXPRESSIONS + ["big_smile"]
    assert after["expressions"] == COMMON_EXPRESSIONS
    assert after["source"] == "common"


@pytest.mark.parametrize("content", [None, "{}", "[]", '"not a mapping"',
                                     '{"happy": [null, 7, "", "  "]}', "{broken"])
@pytest.mark.asyncio
async def test_missing_empty_or_damaged_mapping_keeps_common_candidates(emotion_config, content):
    if content is not None:
        emotion_config.write_text(content, encoding="utf-8")

    response = await vrm_router.get_model_expressions("model")

    assert response["success"] is True
    assert response["expressions"] == COMMON_EXPRESSIONS
    assert response["source"] == "common"


@pytest.mark.asyncio
async def test_mapping_using_only_common_names_does_not_change_candidates(emotion_config):
    emotion_config.write_text(json.dumps({"neutral": ["neutral"], "happy": ["happy"]}), encoding="utf-8")

    response = await vrm_router.get_model_expressions("model")

    assert response["expressions"] == COMMON_EXPRESSIONS
    assert response["source"] == "common"


@pytest.mark.asyncio
async def test_undecodable_mapping_keeps_common_candidates(emotion_config, monkeypatch):
    emotion_config.write_text("{}", encoding="utf-8")
    reader = AsyncMock(side_effect=UnicodeDecodeError(
        "utf-8", b"\xff", 0, 1, "invalid start byte",
    ))
    monkeypatch.setattr(vrm_router, "read_json_async", reader)

    response = await vrm_router.get_model_expressions("model")

    reader.assert_awaited_once_with(emotion_config)
    assert response["success"] is True
    assert response["expressions"] == COMMON_EXPRESSIONS
    assert response["source"] == "common"


@pytest.mark.asyncio
async def test_unreadable_mapping_still_reports_failure(emotion_config, monkeypatch):
    emotion_config.write_text("{}", encoding="utf-8")
    reader = AsyncMock(side_effect=PermissionError("mapping is not readable"))
    monkeypatch.setattr(vrm_router, "read_json_async", reader)

    response = await vrm_router.get_model_expressions("model")

    reader.assert_awaited_once_with(emotion_config)
    assert response.status_code == 500
    assert json.loads(response.body)["success"] is False
