from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.active_hook_answers import (
    is_active_hook_answer_event,
)
from plugin.plugins.neko_roast.core import live_hosting_director
from plugin.plugins.neko_roast.core.contracts import ViewerEvent
from plugin.plugins.neko_roast.core.live_hosting_beat_picker import (
    next_idle_hosting_beat,
)
from plugin.plugins.neko_roast.core.live_hosting_beat_rules import (
    idle_hosting_beat_candidates,
)
from plugin.plugins.neko_roast.core.live_hosting_loop import (
    _maybe_trigger_active_engagement,
)
from plugin.plugins.neko_roast.core.live_hosting_beat_state import (
    record_chosen_idle_hosting_beat,
)
from plugin.plugins.neko_roast.core.live_material_rules import (
    is_clean_live_material,
    is_similar_live_material_title,
)
from plugin.plugins.neko_roast.modules.active_engagement import ActiveEngagementModule
from plugin.plugins.neko_roast.modules.warmup_hosting import WarmupHostingModule


def test_hosting_modules_import_without_active_topic_slice():
    assert ActiveEngagementModule.id == "active_engagement"
    assert WarmupHostingModule.id == "warmup_hosting"
    assert live_hosting_director.LiveHostingDirector is not None
    assert idle_hosting_beat_candidates() == []


def test_live_material_safety_rejects_unsafe_or_malformed_text():
    assert is_clean_live_material({"title": "A tiny room callback"})
    assert not is_clean_live_material({"title": "nuclear reactor tutorial"})
    assert not is_clean_live_material({"title": 'broken "quote'})
    assert not is_clean_live_material({})


def test_live_material_title_similarity_handles_duplicates_and_variants():
    recent = ["Tonight's tiny question"]

    assert is_similar_live_material_title("Tonight tiny question", recent)
    assert not is_similar_live_material_title("Completely different topic", recent)


def test_empty_idle_hosting_catalog_returns_no_beat():
    runtime = SimpleNamespace(_idle_hosting_beat_candidates=lambda: [])

    assert next_idle_hosting_beat(runtime) == {}


def test_empty_active_hook_metadata_does_not_match_short_danmaku():
    recent = [{"status": "pushed", "event": {"source": "active_engagement"}}]
    event = ViewerEvent(uid="1", source="live_danmaku", danmaku_text="1")

    assert not is_active_hook_answer_event(recent, event)

    recent[0]["event"]["topic_shape"] = "tiny_answer"
    assert is_active_hook_answer_event(recent, event)


def test_active_hook_reads_stored_request_metadata():
    recent = [
        {
            "status": "pushed",
            "event": {"source": "active_engagement"},
            "request": {"metadata": {"topic_reply_affordance": "tiny_answer"}},
        }
    ]
    event = ViewerEvent(uid="1", source="live_danmaku", danmaku_text="1")

    assert is_active_hook_answer_event(recent, event)


def test_malformed_idle_hosting_fallback_returns_no_beat():
    assert record_chosen_idle_hosting_beat(
        SimpleNamespace(),
        {"title": "missing key"},
        {"title": "missing key"},
    ) == {}


@pytest.mark.asyncio
async def test_missing_active_engagement_api_degrades_to_skip():
    assert await _maybe_trigger_active_engagement(SimpleNamespace()) is None
