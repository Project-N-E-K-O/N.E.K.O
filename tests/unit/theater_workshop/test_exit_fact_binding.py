"""Typed gate proofs and source bindings, independent of model semantic accuracy."""
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.exit_review import (
    build_exit_binding_request, fact_is_guaranteed, unproven_exit_candidates,
    validate_exit_bindings, validate_exit_candidate_checks,
)
from services.theater_workshop.sdk.generation.facts import build_fact_sources, node_fact_context
from services.theater_workshop.sdk.generation.quality import _pack_shared_branches


def _context():
    history = "玩家已经亲口陈述自己记得的经过。"
    route = {"id": "advance", "target_node_id": "next", "conditions": {"all": []},
             "transition_contract": {"advance_on_completion": True, "must_preserve": [history],
                                     "bridge_scene_narration": "女主打开播放器。"}}
    return {
        "fact_contract": {"facts": {
            "agreement": {"value_type": "bool", "description": "双方同意先试听。"},
            "history": {"value_type": "bool", "description": history},
            "count": {"value_type": "int", "description": "实际交还的物件数量。"},
            "classification": {"value_type": "string", "description": "玩家选择的分类原值。"},
        }},
        "mainline": [{
            "id": "source", "summary": "理想顺序：先陈述经过，再同意试听。",
            "goals": [{"description": history}], "opening_scene": "女主等待玩家回答。",
            "must_not_happen": ["不能替玩家说出记忆。"],
            "completion_contract": {"all": [{"key": "agreement", "equals": True}]},
            "outgoing_routes": [route],
        }, {
            "id": "next", "opening_scene": "女主按下播放键，录音开始响起。",
            "character_state": {"continuity": [history], "catgirl": "女主站在播放器旁。"},
            "outgoing_routes": [],
        }], "branches": [], "endings": [],
    }


def _request(context=None, *, shared=False, terminal_node_ids=None):
    context = context or _context()
    if shared:
        nodes = context["mainline"]
        context["mainline"] = []
        context["branches"] = [{"id": name, "nodes": deepcopy(nodes)} for name in ("left", "right")]
    marked, sources = build_fact_sources(context)
    scoped, _ = _pack_shared_branches(node_fact_context(marked, "source"))
    before = deepcopy((context, scoped, sources))
    request = build_exit_binding_request(context, scoped, "source", terminal_node_ids)
    assert (context, scoped, sources) == before
    return request, sources, context


def _payload(request, *, kind="source_history", key="history", value=True):
    return {"bindings": [{"route_id": route["route_id"], "claim_ref": claim["ref"],
                          "kind": kind, "fact_key": key, "value": value}
                         for route in request["routes"] for claim in route["claims"]]}


@pytest.mark.parametrize("shared", [False, True])
def test_catalog_uses_original_claims_and_definitions_through_shared_aliases(shared):
    request, sources, _ = _request(shared=shared)
    route, = request["routes"]
    prefix = "/branches/0/nodes" if shared else "/mainline"
    assert [sources[claim["ref"]]["path"] for claim in route["claims"]] == [
        prefix + "/0/outgoing_routes/0/transition_contract/must_preserve/0",
        prefix + "/1/character_state/continuity/0",
    ]
    assert all(claim["text"] == sources[claim["ref"]]["quote"] for claim in route["claims"])
    assert {row["key"] for row in request["fact_definitions"]} == {
        "agreement", "history", "count", "classification"}
    for definition in request["fact_definitions"]:
        assert definition["description"]["text"] == sources[definition["description"]["ref"]]["quote"]
    assert route["completion_contract"] == {"all": [{"key": "agreement", "equals": True}]}
    assert route["conditions"] == {"all": []}
    assert "summary" not in route["source_context"] and "goals" not in route["source_context"]
    assert route["source_context"]["must_not_happen"][0]["text"] == "不能替玩家说出记忆。"
    assert route["target_entry"]["opening_scene"]["text"] == "女主按下播放键，录音开始响起。"


def test_binding_definition_scope_matches_current_scene_and_global_write_permissions():
    context = _context()
    context["fact_contract"]["facts"].update({
        "scene:source:current": {"value_type": "bool", "description": "本幕可登记的完成结果。"},
        "scene:previous:history": {"value_type": "bool", "description": "前幕已经登记的历史。"},
        "scene:source_extra:history": {"value_type": "bool", "description": "另一个幕的历史。"},
    })
    before = deepcopy(context)
    request, sources, _ = _request(context)
    definitions = {row["key"]: row for row in request["fact_definitions"]}
    assert set(definitions) == {"agreement", "history", "count", "classification", "scene:source:current"}
    current = definitions["scene:source:current"]["description"]
    assert sources[current["ref"]] == {
        "path": "/fact_contract/facts/scene:source:current/description", "quote": current["text"]}
    assert context == before
    assert "scene:previous:history" in context["fact_contract"]["facts"]


def test_binding_request_accepts_unreferenced_fact_without_description():
    context = _context()
    context["fact_contract"]["facts"]["unused"] = {"value_type": "bool", "visibility": "public"}
    request, _, _ = _request(context)
    definitions = {row["key"]: row for row in request["fact_definitions"]}
    assert definitions["unused"] == {"key": "unused", "value_type": "bool", "description": ""}
    assert definitions["history"]["description"]["text"] == "玩家已经亲口陈述自己记得的经过。"


@pytest.mark.parametrize("shared", [False, True])
def test_catalog_preserves_prior_history_and_original_repair_locations(shared):
    context = _context()
    context.update(background="开场前玩家已归还门卡。", author_intent="保留前情中已完成的归还，不要让玩家重复操作。")
    source, target = context["mainline"]
    source["text_repair_fields"] = ["/opening_scene", "/outgoing_routes/1/transition_contract/must_preserve"]
    target["text_repair_fields"] = ["/character_state/catgirl", "/opening_scene"]
    target["summary"] = "后幕规划不能当历史。"
    invitation = deepcopy(source["outgoing_routes"][0])
    invitation["id"] = "invitation"
    invitation["transition_contract"]["advance_on_completion"] = False
    source["outgoing_routes"].insert(0, invitation)
    if shared:
        context["mainline"] = []
        context["branches"] = [{"id": name, "nodes": deepcopy([source, target])} for name in ("left", "right")]
    before = deepcopy(context)
    request, sources, _ = _request(context)
    for field in ("background", "author_intent"):
        original = request[field]
        assert sources[original["ref"]] == {"path": "/" + field, "quote": context[field]}
        assert original["text"] == context[field]
    route, = request["routes"]
    assert route["route_id"] == "advance" and route["route_field"] == "/outgoing_routes/1"
    prefix = "/branches/0/nodes" if shared else "/mainline"
    assert sources[route["claims"][0]["ref"]]["path"] == (
        prefix + "/0" + route["route_field"] + "/transition_contract/must_preserve/0")
    assert list(request["text_repair_fields"]) == ["next", "source"]
    assert request["text_repair_fields"] == {
        "next": target["text_repair_fields"], "source": source["text_repair_fields"]}
    rendered = json.dumps(request, ensure_ascii=False)
    assert "理想顺序" not in rendered and "后幕规划不能当历史" not in rendered
    assert all("goals" not in route[field] and "summary" not in route[field]
               for field in ("source_context", "target_entry"))
    request["background"]["text"] = "仅修改请求副本。"
    request["text_repair_fields"]["source"].clear()
    assert context == before


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign_ref", "foreign_route", "extra_field"])
def test_binding_requires_exact_claim_coverage(mutation):
    request, _, _ = _request()
    payload = _payload(request)
    if mutation == "missing":
        payload["bindings"].pop()
    elif mutation == "duplicate":
        payload["bindings"].append(deepcopy(payload["bindings"][0]))
    elif mutation == "foreign_ref":
        payload["bindings"][0]["claim_ref"] = "not-an-original-ref"
    elif mutation == "foreign_route":
        payload["bindings"][0]["route_id"] = "another-route"
    else:
        payload["bindings"][0]["severity"] = "major"
    with pytest.raises(ValueError):
        validate_exit_bindings(request, payload)


@pytest.mark.parametrize("key,value,valid", [
    ("history", True, True), ("history", False, True), ("history", 0, False),
    ("count", 0, True), ("count", False, False), ("count", 0.0, False),
    ("classification", "0", True), ("classification", 0, False), ("invented", True, False),
])
def test_binding_only_accepts_declared_keys_and_exact_json_value_types(key, value, valid):
    request, _, _ = _request()
    payload = _payload(request, key=key, value=value)
    before = deepcopy(payload)
    if valid:
        bindings = validate_exit_bindings(request, payload)
        assert bindings == payload["bindings"] and bindings is not payload["bindings"]
        assert type(bindings[0]["value"]) is type(value)
    else:
        with pytest.raises(ValueError):
            validate_exit_bindings(request, payload)
    assert payload == before


@pytest.mark.parametrize("mode,rows,expected", [
    ("all", [], False),
    ("all", [{"type": "fact_equals", "key": "history", "value": True}], True),
    ("any", [{"type": "fact_equals", "key": "history", "value": True}], True),
    ("any", [{"type": "fact_equals", "key": "history", "value": True},
             {"type": "fact_equals", "key": "agreement", "value": True}], False),
    ("any", [{"type": "fact_equals", "key": "history", "value": True}] * 2, True),
    ("any", [{"type": "fact_equals", "key": "history", "value": True},
             {"type": "metric_gte", "metric": "trust", "value": 20}], False),
    ("all", [{"type": "fact_equals", "key": "history", "value": 1}], False),
])
def test_route_fact_sufficiency_respects_all_any_and_does_not_require_completion_membership(mode, rows, expected):
    request, _, _ = _request()
    route = request["routes"][0]
    route["conditions"] = {mode: rows}
    assert not any(row["key"] == "history" for row in route["completion_contract"]["all"])
    assert fact_is_guaranteed(route, "history", True) is expected


@pytest.mark.parametrize("automatic,value,expected", [(True, True, True), (True, 1, False), (False, True, False)])
def test_completion_only_proves_the_same_typed_result_on_automatic_departure(automatic, value, expected):
    route = {"advance_on_completion": automatic, "completion_contract": {
        "all": [{"fixed_narration_id": "original"}, {"key": "history", "equals": value}]},
        "conditions": {"all": []}}
    assert fact_is_guaranteed(route, "history", True) is expected


@pytest.mark.parametrize("kind", ["entry_delivery", "other"])
def test_new_entry_delivery_and_unmapped_claims_do_not_create_history_candidates(kind):
    request, sources, _ = _request()
    payload = _payload(request, kind=kind, key=None, value=None)
    bindings = validate_exit_bindings(request, payload)
    assert unproven_exit_candidates(request, bindings, sources) == []
    payload["bindings"][0]["fact_key"] = "history"
    with pytest.raises(ValueError):
        validate_exit_bindings(request, payload)


def test_unproven_candidates_group_original_citations_without_issuing_verdict_or_repair():
    request, sources, _ = _request()
    bindings = validate_exit_bindings(request, _payload(request))
    before = deepcopy((request, bindings, sources))
    candidate, = unproven_exit_candidates(request, bindings, sources)
    assert candidate["claim_refs"] == [row["claim_ref"] for row in bindings]
    assert candidate["fact_key"] == "history" and candidate["value"] is True
    assert candidate["guaranteed"] is False
    assert {"issue_id", "severity", "verdict", "repair_scope", "repair_targets"}.isdisjoint(candidate)
    for citation in candidate["evidence"]:
        assert {key: citation[key] for key in ("path", "quote")} == sources[citation["ref"]]
    assert (request, bindings, sources) == before
    request["routes"][0]["conditions"] = {"all": [{"type": "fact_equals", "key": "history", "value": True}]}
    assert unproven_exit_candidates(request, bindings, sources) == []


@pytest.mark.parametrize("excluded", ["invitation", "ending", "terminal", "no_claims"])
def test_catalog_excludes_departures_with_additional_authorization_or_ending_rules(excluded):
    context = _context()
    route = context["mainline"][0]["outgoing_routes"][0]
    terminal_ids = None
    if excluded == "invitation":
        route["transition_contract"].pop("advance_on_completion")
    elif excluded == "ending":
        context["endings"] = [context["mainline"].pop()]
    elif excluded == "terminal":
        terminal_ids = {"next"}
    else:
        route["transition_contract"]["must_preserve"] = []
        context["mainline"][1]["character_state"]["continuity"] = []
    request, _, _ = _request(context, terminal_node_ids=terminal_ids)
    assert request["routes"] == []
    assert validate_exit_bindings(request, {"bindings": []}) == []


def _candidate_review():
    context = _context()
    context.update(background="故事开始前已陈述经过。", author_intent="保留已经发生的前情。")
    source = context["mainline"][0]
    source["fixed_narrations"] = [
        {"id": "prior", "text": "必需原文的前置交付。"},
        {"id": "required", "text": "离幕前必须展示的原文。", "after": ["prior"]},
        {"id": "optional", "text": "可选原文，可能从未展示。"},
    ]
    source["completion_contract"]["all"].append({"fixed_narration_id": "required"})
    request, sources, _ = _request(context)
    bindings = validate_exit_bindings(request, _payload(request))
    candidates = unproven_exit_candidates(request, bindings, sources)
    candidate, = candidates
    check = {"route_id": candidate["route_id"], "fact_key": candidate["fact_key"],
             "value": candidate["value"], "status": "uncertain", "reason": "语义判断仍由模型负责。",
             "evidence_refs": [], "clear_basis": None}
    return request, candidates, sources, {"checked_node_ids": ["source"], "issues": [], "candidate_checks": [check]}


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign_route", "foreign_key", "wrong_type"])
def test_candidate_checks_cover_each_original_typed_identity_exactly_once(mutation):
    request, candidates, sources, payload = _candidate_review()
    if mutation == "missing":
        payload["candidate_checks"].clear()
    elif mutation == "duplicate":
        payload["candidate_checks"].append(deepcopy(payload["candidate_checks"][0]))
    elif mutation == "foreign_route":
        payload["candidate_checks"][0]["route_id"] = "other-route"
    elif mutation == "foreign_key":
        payload["candidate_checks"][0]["fact_key"] = "count"
    else:
        payload["candidate_checks"][0]["value"] = 1
    with pytest.raises(ValueError):
        validate_exit_candidate_checks(request, candidates, payload, sources)


def test_gap_check_requires_an_issue_citing_the_same_original_claim():
    request, candidates, sources, payload = _candidate_review()
    candidate, = candidates
    check = payload["candidate_checks"][0]
    check.update(status="gap", evidence_refs=[candidate["claim_refs"][0], candidate["definition_ref"]])
    with pytest.raises(ValueError):
        validate_exit_candidate_checks(request, candidates, payload, sources)
    payload["issues"] = [{"evidence_refs": [candidate["definition_ref"], request["background"]["ref"]]}]
    with pytest.raises(ValueError):
        validate_exit_candidate_checks(request, candidates, payload, sources)
    payload["issues"][0]["evidence_refs"] = list(check["evidence_refs"])
    assert validate_exit_candidate_checks(request, candidates, payload, sources) == [check]


def test_gap_check_can_cite_gate_and_definition_when_its_issue_cites_original_claim():
    request, candidates, sources, payload = _candidate_review()
    candidate, = candidates
    gate_ref = next(row["description"]["ref"] for row in request["fact_definitions"] if row["key"] == "agreement")
    check = payload["candidate_checks"][0]
    check.update(status="gap", evidence_refs=[candidate["definition_ref"], gate_ref])
    payload["issues"] = [{"evidence_refs": [candidate["claim_refs"][0], candidate["definition_ref"]]}]
    assert set(check["evidence_refs"]).isdisjoint(candidate["claim_refs"])
    assert validate_exit_candidate_checks(request, candidates, payload, sources) == [check]


@pytest.mark.parametrize("basis,path", [
    ("source_opening", "/mainline/0/opening_scene"),
    ("source_original", "/mainline/0/fixed_narrations/1/text"),
    ("source_original", "/mainline/0/fixed_narrations/0/text"),
    ("background", "/background"),
    ("background", "/author_intent"),
    ("entry_delivery", "/mainline/1/opening_scene"),
    ("entry_delivery", "/mainline/0/outgoing_routes/0/transition_contract/bridge_scene_narration"),
    ("binding_mismatch", None),
])
def test_clear_checks_require_positive_original_material_for_the_selected_basis(basis, path):
    request, candidates, sources, payload = _candidate_review()
    candidate, = candidates
    refs = ([candidate["claim_refs"][0], candidate["definition_ref"]] if path is None else
            [next(ref for ref, item in sources.items() if item["path"] == path)])
    check = payload["candidate_checks"][0]
    check.update(status="clear", clear_basis=basis, evidence_refs=refs)
    before = deepcopy((request, candidates, payload, sources))
    assert validate_exit_candidate_checks(request, candidates, payload, sources) == [check]
    assert (request, candidates, payload, sources) == before


def test_claims_definitions_and_optional_originals_do_not_prove_history_complete():
    request, candidates, sources, payload = _candidate_review()
    candidate, = candidates
    check = payload["candidate_checks"][0]
    optional = next(ref for ref, item in sources.items() if item["path"] == "/mainline/0/fixed_narrations/2/text")
    for basis, refs in [
        ("source_opening", candidate["claim_refs"]),
        ("background", [candidate["definition_ref"]]),
        ("source_original", [optional]),
        ("entry_delivery", [request["background"]["ref"]]),
    ]:
        check.update(status="clear", clear_basis=basis, evidence_refs=refs)
        with pytest.raises(ValueError):
            validate_exit_candidate_checks(request, candidates, payload, sources)


def test_uncertain_candidate_remains_an_opinion_without_generated_issue_or_semantic_guess():
    request, candidates, sources, payload = _candidate_review()
    checks = validate_exit_candidate_checks(request, candidates, payload, sources)
    assert checks == payload["candidate_checks"] and checks is not payload["candidate_checks"]
    assert checks[0]["status"] == "uncertain" and payload["issues"] == []
    assert "severity" not in checks[0] and "verdict" not in checks[0]
