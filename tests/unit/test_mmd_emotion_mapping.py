"""The existing API must preserve explicit empty lists and filename-based sharing."""

import json
from unittest.mock import AsyncMock

import pytest

from main_routers import mmd_router


@pytest.mark.asyncio
async def test_emotion_mapping_roundtrip_keeps_disabled_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(mmd_router, "_ensure_mmd_directory", lambda _: tmp_path)
    mapping = {emotion: [] for emotion in ["neutral", "happy", "sad", "angry", "surprised", "relaxed", "fear"]}
    mapping["happy"] = ["瞳小"]
    request = AsyncMock()
    request.json.return_value = {"model": "花火3.0", "mapping": mapping}

    response = await mmd_router.update_emotion_mapping(request)
    assert response.status_code == 200
    assert json.loads(mmd_router.get_emotion_mapping("花火3.0").body)["mapping"] == mapping
    assert json.loads((tmp_path / "emotion_config/花火3.0.json").read_text(encoding="utf-8")) == mapping
    assert json.loads(mmd_router.get_emotion_mapping("花火").body)["mapping"] == {}
    assert not (tmp_path / "emotion_config/花火.json").exists()

    # The same stem still addresses the same file, independent of source or character.
    mapping["happy"] = []
    response = await mmd_router.update_emotion_mapping(request)
    assert response.status_code == 200
    assert json.loads(mmd_router.get_emotion_mapping("花火3.0").body)["mapping"] == mapping
    assert len(list((tmp_path / "emotion_config").glob("*.json"))) == 1
