import pytest

from main_logic.core import _build_avatar_interaction_memory_meta
from main_logic.cross_server import _should_persist_avatar_interaction_memory


@pytest.mark.unit
def test_avatar_interaction_memory_meta_promotes_fist_and_hammer_summaries():
    fist_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
    })
    fist_rapid = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "rapid",
    })
    hammer_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "normal",
    })
    hammer_burst = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "burst",
    })

    assert fist_normal["memory_note"] == "[主人摸了摸你的头]"
    assert fist_rapid["memory_note"] == "[主人连续摸了摸你的头]"
    assert fist_rapid["memory_dedupe_rank"] > fist_normal["memory_dedupe_rank"]
    assert fist_rapid["memory_dedupe_key"] == fist_normal["memory_dedupe_key"] == "fist_touch"

    assert hammer_normal["memory_note"] == "[主人用锤子敲了敲你的头]"
    assert hammer_burst["memory_note"] == "[主人连续敲了你好几下]"
    assert hammer_burst["memory_dedupe_rank"] > hammer_normal["memory_dedupe_rank"]
    assert hammer_burst["memory_dedupe_key"] == hammer_normal["memory_dedupe_key"] == "hammer_bonk"


@pytest.mark.unit
def test_avatar_interaction_memory_window_allows_rank_upgrade_within_window():
    cache: dict[str, dict[str, int | str]] = {}

    first_persisted = _should_persist_avatar_interaction_memory(
        cache,
        "[主人摸了摸你的头]",
        "fist_touch",
        1,
    )
    upgraded_persisted = _should_persist_avatar_interaction_memory(
        cache,
        "[主人连续摸了摸你的头]",
        "fist_touch",
        2,
    )
    duplicate_summary_persisted = _should_persist_avatar_interaction_memory(
        cache,
        "[主人连续摸了摸你的头]",
        "fist_touch",
        2,
    )

    assert first_persisted is True
    assert upgraded_persisted is True
    assert duplicate_summary_persisted is False


@pytest.mark.unit
def test_avatar_interaction_memory_meta_falls_back_for_invalid_intensity_combos():
    lollipop_invalid_rapid = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "lollipop",
        "action_id": "offer",
        "intensity": "rapid",
    })
    fist_invalid_burst = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "burst",
    })

    assert lollipop_invalid_rapid["memory_note"] == "[主人喂了你一口棒棒糖]"
    assert lollipop_invalid_rapid["memory_dedupe_rank"] == 1
    assert fist_invalid_burst["memory_note"] == "[主人摸了摸你的头]"
    assert fist_invalid_burst["memory_dedupe_rank"] == 1
