from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from plugin.plugins.neko_live.core.contracts import ViewerIdentity, ViewerProfile, utc_now_iso
from plugin.plugins.neko_live.core.viewer_preferences import (
    infer_viewer_preferences,
    viewer_preference_prompt_block,
    viewer_profile_projection,
)
from plugin.plugins.neko_live.modules._prompt_context_blocks import (
    viewer_preference_context_block,
)
from plugin.plugins.neko_live.stores.viewer_store import ViewerStore


class _FakePlugin:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def data_path(self, *parts):
        return self._data_dir.joinpath(*parts) if parts else self._data_dir


def test_viewer_preference_inference_builds_safe_impression_cues():
    tech = infer_viewer_preferences("AI plugin config?")
    meme = infer_viewer_preferences("hhh 233")
    music = infer_viewer_preferences("bgm sounds good")

    assert "tech_ai" in tech["favorite_topics"]
    assert "short_helper_mode" in tech["running_jokes"]
    assert tech["avoid_guidance"] == "answer before teasing; do not dodge the question"
    assert "meme_callback" in meme["running_jokes"]
    assert meme["response_preference"] == "catch the joke briefly without overexplaining"
    assert "music" in music["favorite_topics"]
    assert "music_callback" in music["running_jokes"]


def test_viewer_profile_projection_requires_repeat_evidence_for_stable_topics_and_jokes():
    profile = ViewerProfile(
        uid="1001",
        nickname="viewer",
        danmaku_count=1,
        preference_tags={"meme": 1},
        favorite_topics={"music": 1},
        running_jokes={"meme_callback": 1},
        last_interaction_summary="likes memes",
        impression_summary="likes memes; catch the joke briefly",
        last_interaction_at=utc_now_iso(),
    )

    projection = viewer_profile_projection(profile)

    assert projection["profile_confidence"] == "low"
    assert projection["profile_freshness"] == "fresh"
    assert projection["top_favorite_topics"] == []
    assert projection["top_running_jokes"] == []
    assert projection["memory_use_rule"].startswith("weak:")


def test_viewer_profile_projection_downgrades_stale_impressions():
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat(timespec="seconds")
    profile = ViewerProfile(
        uid="1001",
        nickname="viewer",
        danmaku_count=18,
        preference_tags={"tech_ai": 6, "questions": 5},
        favorite_topics={"tech_ai": 6},
        running_jokes={"short_helper_mode": 6},
        response_preference="answer first, then add one light follow-up",
        impression_summary="likes tech/AI, often asks questions",
        last_interaction_at=old,
    )

    projection = viewer_profile_projection(profile)

    assert projection["profile_freshness"] == "old"
    assert projection["profile_confidence"] == "low"
    assert projection["memory_use_rule"].startswith("old:")
    assert projection["top_favorite_topics"][0]["tag"] == "tech_ai"


def test_viewer_preference_prompt_block_marks_memory_as_private_and_cautious():
    profile = ViewerProfile(
        uid="1001",
        nickname="viewer",
        danmaku_count=4,
        preference_tags={"tech_ai": 2, "questions": 2},
        favorite_topics={"tech_ai": 2},
        running_jokes={"short_helper_mode": 2},
        interaction_style="question",
        response_preference="answer first, then add one light follow-up",
        impression_summary="likes tech/AI, often asks questions",
        avoid_guidance="answer before teasing; do not dodge the question",
        last_interaction_at=utc_now_iso(),
    )

    block = viewer_preference_prompt_block(profile)

    assert "profile_confidence: medium" in block
    assert "profile_freshness: fresh" in block
    assert "memory_use_rule: cautious:" in block
    assert "evidence_rule: treat one-off topics or jokes as weak evidence" in block
    assert "do not announce stored viewer data or say you remember the profile" in block


def test_viewer_preference_context_respects_streamer_memory_toggle():
    profile = ViewerProfile(
        uid="1001",
        nickname="viewer",
        danmaku_count=4,
        favorite_topics={"tech_ai": 2},
        last_interaction_at=utc_now_iso(),
    )
    enabled = type("Context", (), {"config": type("Config", (), {"viewer_memory_enabled": True})()})()
    disabled = type("Context", (), {"config": type("Config", (), {"viewer_memory_enabled": False})()})()

    assert "Viewer impression memory" in viewer_preference_context_block(enabled, profile)
    assert viewer_preference_context_block(disabled, profile) == ""


@pytest.mark.asyncio
async def test_viewer_profile_quality_matrix_persists_only_safe_impressions(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="1001", nickname="viewer")
    samples = [
        "AI plugin config? token=must-not-leak",
        "hhh 233",
        "bgm sounds good",
        "AI model bug?",
    ]

    for sample in samples:
        await store.record_live_danmaku(identity, sample)

    raw = (tmp_path / "viewer_profiles.json").read_text(encoding="utf-8")
    assert "must-not-leak" not in raw
    for sample in samples:
        assert sample not in raw

    stored = json.loads(raw)["1001"]
    assert stored["favorite_topics"]["tech_ai"] >= 2
    assert stored["favorite_topics"]["music"] == 1
    assert stored["running_jokes"]["short_helper_mode"] >= 2
    assert stored["running_jokes"]["music_callback"] == 1
    assert stored["impression_summary"]
    assert stored["avoid_guidance"] == "answer before teasing; do not dodge the question"

    recent = await store.recent_profiles()
    profile = recent[0]
    topics = {item["tag"]: item["count"] for item in profile["top_favorite_topics"]}
    cues = {item["tag"]: item["count"] for item in profile["top_running_jokes"]}
    assert topics["tech_ai"] >= 2
    assert cues["short_helper_mode"] >= 2
    assert profile["reply_guidance"] == "answer first, then add one light follow-up"
