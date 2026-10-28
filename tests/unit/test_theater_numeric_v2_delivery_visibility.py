"""Name-presence checks read the same visible blocks as playback, not templates."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_context import missing_contract_names
from services.theater.numeric_v2_performance import performance_content_blocks


def _node():
    return {"route_gates": [{"target_node_id": "target", "transition_contract": {
        "must_deliver": ["关键道具“登记册”[register_book]：本轮展示。"],
        "must_preserve": [],
    }}]}


def _session():
    return SimpleNamespace(current_node_id="source", revision=0,
                           opening_performance={}, performance_history=())


def _check(performance, expected):
    node, session = _node(), _session()
    before = deepcopy((node, session, performance))
    assert missing_contract_names(node, "target", performance, session) == expected
    assert (node, session, performance) == before


@pytest.mark.parametrize("phase,channel", [
    pytest.param("source_response", "performance", id="performance-source_response"),
    pytest.param("source_response", "scene_narration", id="scene_narration-source_response"),
    pytest.param("transition_bridge", "scene_narration", id="scene_narration-transition_bridge"),
    pytest.param("target_opening", "fixed_before", id="fixed_before-target_opening"),
    pytest.param("target_opening", "fixed_after", id="fixed_after-target_opening"),
])
def test_each_actual_visible_channel_satisfies_name_presence(phase, channel):
    part = {"phase": phase, "performance": "我们接着核对吧。"}
    if channel.startswith("fixed_"):
        part["fixed_narrations"] = [{
            "id": "displayed-entry", "position": channel.removeprefix("fixed_"),
            "text": "登记册摊在桌上。",
        }]
    else:
        part[channel] = "登记册摊在桌上。"
    performance = {"segments": [part]}
    assert any("登记册" in block["text"] for block in performance_content_blocks(performance))
    _check(performance, ())


@pytest.mark.parametrize("position", ["before"])
def test_unsegmented_fixed_original_is_visible_without_generated_dialogue(position):
    _check({"performance": "", "fixed_narrations": [
        {"id": "displayed", "position": position, "text": "登记册摊在桌上。"},
    ]}, ())


@pytest.mark.parametrize("hidden", [pytest.param({
    "performance": "登记册已经在桌上。", "scene_narration": "登记册已经在桌上。",
    "fixed_narrations": [{"id": "ignored", "position": "before", "text": "登记册已经在桌上。"}],
}, id="hidden0")])
def test_segmented_playback_does_not_count_ignored_top_level_copies(hidden):
    performance = {**hidden, "segments": [
        {"phase": "target_opening", "performance": "先看看窗外。"},
    ]}
    assert all("登记册" not in block["text"] for block in performance_content_blocks(performance))
    _check(performance, ("登记册",))


@pytest.mark.parametrize("hidden", [pytest.param({
    "suggested_inputs": ["（拿起登记册）我来看。"],
    "narrative_events": [{"object": "登记册", "value": "放在桌上"}],
    "pending_fixed_narrations": [{"text": "登记册摊在桌上。"}],
}, id="hidden0")])
def test_nonplayed_metadata_and_choices_cannot_satisfy_delivery(hidden):
    _check({"performance": "先等等吧。", **hidden}, ("登记册",))


def test_uninserted_author_fixed_template_does_not_count_as_playback():
    node = _node()
    node["story_beat"] = {"fixed_narrations": [{
        "id": "conditional", "text": "登记册摊在桌上。", "timing": "after_condition",
    }]}
    before = deepcopy(node)
    assert missing_contract_names(node, "target", {"performance": "先等等吧。"}, _session()) == ("登记册",)
    assert node == before


@pytest.mark.parametrize("performance", [
    {"content": [{"type": "narration", "text": "登记册摊在桌上。"}]},
    {"content": [{"type": "dialogue", "speaker_id": "active_catgirl", "text": "登记册在这里。"}]},
    {"narration": "登记册摊在桌上。"},
    {"dialogue": [{"speaker_id": "active_catgirl", "text": "登记册在这里。"}]},
])
def test_supported_legacy_visible_content_counts(performance):
    _check(performance, ())


def test_hidden_legacy_speaker_does_not_count_as_catgirl_dialogue():
    _check({"dialogue": [{"speaker_id": "someone_else", "text": "登记册在这里。"}]}, ("登记册",))


def test_name_presence_does_not_claim_to_prove_handover_or_authorization():
    # Merely asking about an object still mentions its name. Existing independent
    # semantic review owns whether a handover happened or was authorized.
    _check({"performance": "登记册什么时候能拿来？"}, ())
    _check({"performance": "登记簿摊在桌上。"}, ("登记册",))


def test_preservation_without_explicit_delivery_does_not_require_repetition():
    node = _node()
    contract = node["route_gates"][0]["transition_contract"]
    contract["must_preserve"] = contract.pop("must_deliver")
    assert missing_contract_names(node, "target", {"performance": "先等等吧。"}, _session()) == ()


@pytest.mark.parametrize("inserted", [False, True])
def test_unplayable_body_cannot_substitute_for_separate_visible_original(inserted):
    performance = {"performance": "（登记册", "scene_narration": "窗外下起小雨。"}
    if inserted:
        performance["fixed_narrations"] = [{
            "id": "visible", "position": "before", "text": "登记册摊在桌上。",
        }]
    _check(performance, () if inserted else ("登记册",))


def test_unsupported_tuple_segments_are_not_read_as_playable_transition():
    # Runtime requires a list for transitions; this helper must not grant a
    # second, looser protocol to an otherwise unplayed tuple.
    _check({"performance": "", "segments": (
        {"phase": "target_opening", "performance": "登记册摊在桌上。"},
    )}, ("登记册",))


def test_empty_segment_list_does_not_fall_back_to_hidden_aggregate():
    _check({"segments": [], "performance": "登记册摊在桌上。"}, ("登记册",))


def test_modern_fields_do_not_count_unused_legacy_content():
    _check({"performance": "先等等吧。", "content": [
        {"type": "narration", "text": "登记册摊在桌上。"},
    ]}, ("登记册",))
