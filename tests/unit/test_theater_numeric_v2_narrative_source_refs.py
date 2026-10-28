"""Provider source IDs bind one visible block without changing canonical event semantics."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_narrative as narrative
from services.theater import numeric_v2_narrative_proposer as proposer
from services.theater import numeric_v2_structured_output as structured
from services.theater.numeric_v2_performance import performance_content_blocks
from tests.unit.test_theater_numeric_v2_fixed_narrative_evidence import _piece
from tests.unit.test_theater_numeric_v2_narrative_proposer import _client, _session


TEXT = "我还不确定。"
_NATIVE_PROPOSE = proposer.NumericV2NarrativeProposer.propose


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch):
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda _: None)


def _catalog(performance, message="先等。", *, opening=False):
    return proposer._source_catalog(performance, message, opening=opening)


def _decode(events, catalog):
    return proposer._decode_source_events(events, catalog, {"player": "player", "character": "character"})


def _wire(source_id, quote=TEXT, **changes):
    return {"source_id": source_id, "subject": {"ref": "character"}, "kind": "speech",
            "changes": [{"entity": {"ref": "character"}, "attribute": "unresolved", "value": "不确定"}],
            "quote": quote, "known_to": ["character"], **changes}


@pytest.mark.parametrize("opening", [False, True])
def test_catalog_keeps_actual_fixed_order_and_duplicate_blocks_separate(opening):
    performance = {"scene_narration": "窗外安静了。", "performance": TEXT,
                   "fixed_narrations": [_piece(TEXT, "before", "first"), _piece(TEXT, "after", "second")]}
    before = deepcopy(performance)
    catalog = _catalog(performance, opening=opening)
    phase = "opening" if opening else "turn"
    expected = [] if opening else [("player_input", "player_input", "先等。")]
    expected += [(phase, "scene_narration", "窗外安静了。"), (phase, "scene_narration", TEXT),
                 (phase, "performance", TEXT), (phase, "scene_narration", TEXT)]
    assert catalog == tuple({"id": f"s{index}", "phase": p, "source": s, "text": text}
                            for index, (p, s, text) in enumerate(expected))
    assert performance == before
    duplicate_rows = [row for row in catalog if row["text"] == TEXT]
    assert len({row["id"] for row in duplicate_rows}) == 3
    decoded = [_decode([_wire(row["id"])], catalog)[0] for row in duplicate_rows]
    assert [row["source"] for row in decoded] == ["scene_narration", "performance", "scene_narration"]


@pytest.mark.parametrize("shape", ["segments", "compact"])
def test_formal_catalog_uses_selected_phase_fields_and_excludes_hidden_top_level(shape):
    compact = {"source_scene_narration": "来源的门仍开着。", "source_performance": "我先回应。",
               "bridge_scene_narration": "两人经过走廊。", "target_scene_narration": "目标的窗开着。",
               "target_performance": "我们到这里了。"}
    if shape == "compact":
        performance = {**compact, "performance": "隐藏顶层表演", "scene_narration": "隐藏顶层旁白"}
    else:
        performance = {**compact, "performance": "隐藏顶层表演", "scene_narration": "隐藏顶层旁白",
            "segments": [{"phase": "source_response", "scene_narration": compact["source_scene_narration"],
                          "performance": compact["source_performance"]},
                         {"phase": "transition_bridge", "scene_narration": compact["bridge_scene_narration"],
                          "fixed_narrations": [_piece("桥段不准展示的固定原文。", "after")]},
                         {"phase": "target_opening", "scene_narration": compact["target_scene_narration"],
                          "performance": compact["target_performance"]}]}
    before = deepcopy(performance)
    catalog = _catalog(performance)
    assert [(row["phase"], row["source"], row["text"]) for row in catalog] == [
        ("player_input", "player_input", "先等。"),
        ("source_response", "scene_narration", compact["source_scene_narration"]),
        ("source_response", "performance", compact["source_performance"]),
        ("transition_bridge", "scene_narration", compact["bridge_scene_narration"]),
        ("target_opening", "scene_narration", compact["target_scene_narration"]),
        ("target_opening", "performance", compact["target_performance"])]
    assert performance == before


@pytest.mark.parametrize("opening,segments", [
    (False, []), (True, [{"phase": "source_response", "performance": "来源段内可见。"}]),
])
def test_segment_list_priority_never_exposes_hidden_top_level_sources(opening, segments):
    performance = {"performance": "隐藏顶层表演。", "scene_narration": "隐藏顶层旁白。", "segments": segments}
    # Renderer isolation applies even to an empty list; an opening request cannot relabel a source response.
    assert all("隐藏顶层" not in block["text"] for block in performance_content_blocks(performance))
    assert _catalog(performance, "", opening=opening) == ()


def test_catalog_does_not_upgrade_legacy_or_undelivered_fields_to_visible_sources():
    performance = {"performance": TEXT, "scene_update": "未播放的旧别名。", "narration": "旧旁白。",
                   "dialogue": [{"text": "旧对白。"}], "content": [{"type": "narration", "text": "旧块。"}],
                   "author_fixed_definitions": [_piece("尚未触发的作者原文。")],
                   "suggested_inputs": ["未选择的推荐。"]}
    catalog = _catalog(performance, "")
    assert catalog == ({"id": "s0", "phase": "turn", "source": "performance", "text": TEXT},)
    assert _catalog({key: value for key, value in performance.items() if key != "performance"}, "") == ()


@pytest.mark.parametrize("mutation", ["unknown_id", "non_string_id", "missing_id", "wrong_block",
                                      "cross_block", "explicit_phase", "explicit_source", "bad_knower"])
def test_decoder_rejects_bad_references_and_bypasses_as_an_entire_group(mutation):
    performance = {"performance": TEXT, "fixed_narrations": [
        _piece("盒子在", "before", "first"), _piece("柜台。", "after", "second")]}
    catalog = _catalog(performance, "")
    source_id = next(row["id"] for row in catalog if row["source"] == "performance")
    good = _wire(source_id)
    bad = deepcopy(good)
    if mutation == "unknown_id": bad["source_id"] = "s999"
    elif mutation == "non_string_id": bad["source_id"] = True
    elif mutation == "missing_id": bad.pop("source_id")
    elif mutation == "wrong_block": bad["source_id"] = catalog[0]["id"]
    elif mutation == "cross_block": bad["quote"] = "盒子在\n柜台。"
    elif mutation == "explicit_phase": bad["phase"] = "turn"
    elif mutation == "explicit_source": bad["source"] = "performance"
    else: bad["known_to"] = []
    before = deepcopy((good, bad, catalog))
    assert _decode([good, bad], catalog) is None
    assert (good, bad, catalog) == before


def test_decoder_only_restores_source_fields_without_rewriting_subject_or_knowers():
    catalog = _catalog({"performance": TEXT}, "")
    event = _wire("s0", subject={"ref": "player"}, known_to=["player"])
    event["changes"][0]["entity"] = {"ref": "player"}
    before = deepcopy(event)
    decoded = _decode([event], catalog)
    assert decoded == [{**{key: value for key, value in event.items() if key != "source_id"},
                        "subject": "player", "changes": [{"entity": "player", "attribute": "unresolved", "value": "不确定"}],
                        "phase": "turn", "source": "performance"}]
    assert narrative.parse_narrative_events(decoded) == decoded
    assert event == before


def test_wire_schema_has_only_catalog_ids_while_canonical_schema_stays_unchanged():
    baseline = structured.narrative_output_schema()
    original = deepcopy(baseline)
    wire = structured.narrative_output_schema(source_ids=("s0", "s2"))
    event = wire["properties"]["narrative_events"]["items"]
    assert event["properties"]["source_id"]["enum"] == ["s0", "s2"]
    assert "source_id" in event["required"] and not {"phase", "source"} & set(event["properties"])
    assert event["additionalProperties"] is False
    old = baseline["properties"]["narrative_events"]["items"]
    for key in ("known_to", "authorization_quote"):
        assert event["properties"][key] == old["properties"][key]
    assert event["properties"]["subject"]["anyOf"][0]["properties"]["ref"]["enum"] == ["player", "character"]
    assert structured.narrative_output_schema() == original


def test_source_reference_messages_replace_only_the_provider_source_shape():
    performance, session = {"performance": TEXT}, _session()
    original = deepcopy(performance)
    normal = proposer._messages(performance, "先等。", session, False)
    wire = proposer._messages(performance, "先等。", session, False, source_refs=True)
    old_data, wire_data = (json.loads(messages[1].content) for messages in (normal, wire))
    assert wire_data["source_texts"] == list(_catalog(performance))
    assert not {"player_input", "phase_texts"} & set(wire_data)
    assert "source_texts" not in old_data and old_data["player_input"] == "先等。"
    assert performance == original


@pytest.mark.asyncio
async def test_native_proposal_decodes_wire_ids_then_review_keeps_the_canonical_table(monkeypatch):
    session, performance = _session(), {"performance": TEXT}
    catalog = _catalog(performance)
    source_id = next(row["id"] for row in catalog if row["source"] == "performance")
    worker, seen = _client(monkeypatch, {"narrative_events": [_wire(source_id)]})
    events = await _NATIVE_PROPOSE(worker, performance=performance, player_input="先等。", session=session)
    assert "source_id" not in events[0] and events[0]["phase"] == "turn" and events[0]["source"] == "performance"
    assert events[0]["subject"] == "character" and events[0]["known_to"] == ["character"]
    request = json.loads(seen["requests"][0][0][1].content)
    assert request["source_texts"] == list(catalog) and "phase_texts" not in request
    assert len(seen["requests"]) == 1
    candidate = {**performance, "narrative_events": events}
    before = deepcopy(candidate)
    reviewer, reviewed = _client(monkeypatch, {"approved_narrative_event_indexes": [], "narrative_review_complete": False})
    review = await reviewer.review(performance=candidate, player_input="先等。", session=session)
    data = json.loads(reviewed["requests"][0][0][1].content)
    assert "source_texts" not in data and "phase_texts" in data and data["player_input"] == "先等。"
    assert data["narrative_events"] == [{"index": 0, **events[0]}]
    assert review.reviewed_narrative_events == tuple(events) and not review.approved_narrative_event_indexes
    assert candidate == before


@pytest.mark.asyncio
async def test_source_reference_request_is_budgeted_before_config_or_transport(monkeypatch):
    session = _session()
    session.opening_performance, session.performance_history = {}, ()
    performance = {"performance": TEXT}
    messages = proposer._messages(performance, "先等。", session, False, source_refs=True)
    assert not json.loads(messages[1].content)["history_evidence"]
    limit = sum(proposer.count_tokens(message.content) for message in messages) - 1
    monkeypatch.setattr(proposer, "NUMERIC_V2_NARRATIVE_INPUT_MAX_TOKENS", limit)
    worker, seen = _client(monkeypatch, {"narrative_events": []})
    with pytest.raises(proposer.NumericV2ActorError, match="narrative_input_over_budget"):
        await _NATIVE_PROPOSE(worker, performance=performance, player_input="先等。", session=session)
    assert not seen["config"] and not seen["factory"] and not seen["requests"]


def test_entity_reference_catalog_exposes_only_projected_identities_and_public_protagonists(monkeypatch):
    session = _session()
    session.catgirl_binding = {"catgirl_name": "新演员", "player_address": "未告知称呼"}
    session.player_address_known = False
    projection = {"current": {"item:画板": {"holder": {"value": "person:管理员"}}},
                  "last_known": {"environment:旧雨势": {"condition": {"value": "雨停"}}},
                  "recent_events": [{"subject": "person:不在投影的姓名"}]}
    monkeypatch.setattr(proposer, "project_narrative_state", lambda *a, **k: deepcopy(projection))
    messages = proposer._messages({"performance": TEXT}, "先等。", session, False, source_refs=True)
    data = json.loads(messages[1].content)
    assert data["entity_refs"] == {"player": "player", "character": "character",
                                  "e0": "environment:旧雨势", "e1": "item:画板", "e2": "person:管理员"}
    assert data["protagonists"] == {"player": {"role": "用户"},
                                   "character": {"role": "当前演出角色", "name": "新演员"}}
    assert "未告知称呼" not in messages[1].content
    assert "不在投影的姓名" not in data["entity_refs"].values()
    assert data["narrative_state"] == projection


def test_typed_entity_wire_decodes_without_aliases_or_closed_world_guessing():
    quote = "管理员接过风筝。"
    catalog = _catalog({"scene_narration": quote}, "")
    refs = {"player": "player", "character": "character", "e0": "person:管理员"}
    event = {"source_id": "s0", "subject": {"ref": "e0"}, "kind": "action",
             "changes": [{"entity": {"type": "item", "name": "风筝"},
                          "attribute": "holder", "value": {"ref": "e0"}}],
             "quote": quote, "known_to": ["player", "character"]}
    decoded = proposer._decode_source_events([event], catalog, refs)
    assert decoded[0]["subject"] == "person:管理员"
    assert decoded[0]["changes"] == [{"entity": "item:风筝", "attribute": "holder", "value": "person:管理员"}]
    for reference in ({"ref": "character:新演员"}, {"type": "character", "name": "新演员"},
                      {"ref": "e404"}, {"type": "item", "name": "没出现的风筝"}):
        invalid = deepcopy(event)
        invalid["subject"] = reference
        assert proposer._decode_source_events([event, invalid], catalog, refs) is None


def test_typed_entity_schema_keeps_stored_protocol_and_holder_recipient_limits():
    from jsonschema import Draft202012Validator
    refs = {"player": "player", "character": "character", "e0": "item:包裹", "e1": "person:门卫"}
    canonical = structured.narrative_output_schema()
    schema = structured.narrative_output_schema(source_ids=("s0",), entity_refs=refs)
    event = {"source_id": "s0", "subject": {"ref": "character"}, "kind": "action",
             "changes": [{"entity": {"ref": "e0"}, "attribute": "holder", "value": {"ref": "e1"}}],
             "quote": "递过包裹。", "known_to": ["player", "character"]}
    validator = Draft202012Validator(schema)
    validator.validate({"narrative_events": [event]})
    event["changes"][0]["value"] = {"ref": "e0"}
    assert not validator.is_valid({"narrative_events": [event]})
    assert structured.narrative_output_schema() == canonical


@pytest.mark.parametrize("field,reference", [
    ("subject", "character:未出现的演员"), ("subject", {"ref": "none"}),
    ("subject", {"ref": "character", "name": "额外别名"}),
    ("subject", {"type": "item", "name": " "}),
    ("subject", {"type": "person", "name": "未出现的人"}),
    ("entity", {"type": "character", "name": "管理员"}),
    ("holder", {"type": "item", "name": "风筝"}),
    ("holder", {"ref": "e1"}),
])
def test_typed_decoder_rejects_reference_bypasses_before_canonical_review(field, reference):
    quote = "管理员接过风筝。"
    sources = _catalog({"scene_narration": quote}, "")
    refs = {"player": "player", "character": "character", "e0": "person:管理员", "e1": "item:风筝"}
    event = {"source_id": "s0", "subject": {"ref": "e0"}, "kind": "action",
             "changes": [{"entity": {"ref": "e1"}, "attribute": "holder", "value": {"ref": "e0"}}],
             "quote": quote, "known_to": ["player", "character"]}
    invalid = deepcopy(event)
    if field == "subject":
        invalid[field] = reference
    else:
        invalid["changes"][0]["value" if field == "holder" else "entity"] = reference
    before = deepcopy((event, invalid, sources, refs))
    assert proposer._decode_source_events([event, invalid], sources, refs) is None
    assert (event, invalid, sources, refs) == before


def test_known_identity_can_continue_without_asserting_its_last_known_state():
    quote = "它留在原处。"
    refs = {"player": "player", "character": "character", "e0": "item:长方木箱"}
    event = {"source_id": "s0", "subject": {"ref": "character"}, "kind": "speech",
             "changes": [{"entity": {"ref": "e0"}, "attribute": "position", "value": "原处"}],
             "quote": quote, "known_to": ["player", "character"]}
    sources = _catalog({"performance": quote}, "")
    decoded = proposer._decode_source_events([event], sources, refs)
    # 身份可延用，但解码只提供未批准候选，不从旧状态填补持有人、地点或完成结果。
    assert decoded[0]["changes"] == [{"entity": "item:长方木箱", "attribute": "position", "value": "原处"}]
    assert not {"approved_narrative_event_indexes", "narrative_review_complete"} & set(decoded[0])


def test_new_person_holder_and_no_holder_use_explicit_reference_shapes():
    quote = "门卫把包裹放在地上。"
    sources = _catalog({"scene_narration": quote}, "")
    refs = {"player": "player", "character": "character"}
    event = {"source_id": "s0", "subject": {"type": "person", "name": "门卫"}, "kind": "action",
             "changes": [{"entity": {"type": "item", "name": "包裹"}, "attribute": "holder",
                          "value": {"type": "person", "name": "门卫"}}],
             "quote": quote, "known_to": ["player", "character"]}
    assert proposer._decode_source_events([event], sources, refs)[0]["changes"][0]["value"] == "person:门卫"
    event["changes"][0]["value"] = {"ref": "none"}
    assert proposer._decode_source_events([event], sources, refs)[0]["changes"][0]["value"] == "none"
