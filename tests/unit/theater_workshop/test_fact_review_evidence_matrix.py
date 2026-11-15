"""Entry and handoff conclusions retain the action and guarantee evidence behind them."""
from copy import deepcopy

import pytest

from services.theater_workshop.sdk.generation.facts import build_contract_targets, build_fact_sources, validate_fact_review
from .test_completion_contract_review import _context, _source_ref


def _case(opening="林舟检查设备接口。小岚展示登记信息。"):
    context = _context()
    source, target = context["mainline"][:2]
    context["start_node_id"] = source["id"]
    target["opening_scene"] = opening
    source["outgoing_routes"][0].update(target_node_id=target["id"], conditions={"all": []}, transition_contract={
        "reason": "玩家可先打电话、直接去管理处，或两者都做。",
        "accept_input": "好，我先打电话核实。", "bridge_scene_narration": "林舟已经到了管理处。",
        "must_deliver": [], "must_preserve": [],
    })
    context["endings"] = []
    context["branches"] = []
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {source["id"], target["id"]})
    checks = [{"target_id": row["id"], "status": "clear", "subject": "实际主体", "completion_evidence": "按原文交付。",
               "timing": "依实际阶段。", "refusal": "允许暂缓。", "alternatives": [],
               "evidence_refs": [_source_ref(sources, row["definition_path"])]} for row in targets]
    payload = {"checked_node_ids": [source["id"], target["id"]], "issues": [], "contract_checks": checks}
    return context, sources, targets, payload


def _validate(context, sources, payload):
    return validate_fact_review(context, payload, {row["id"] for row in context["mainline"]}, evidence_sources=sources)


def _matrices(targets, payload, sources):
    for target, check in zip(targets, payload["contract_checks"], strict=True):
        check["evidence_refs"] = [_source_ref(sources, target["definition_path"])]
        if target.get("kind") == "entry_ownership":
            check["ownership_checks"] = [{"unit_id": unit["id"], "quote": unit["text"], "subject": "",
                "owner": "environment", "kind": "passive", "authorization_refs": [], "status": "clear"}
                for unit in target["ownership_units"]]
        elif target.get("kind") == "transition_contract":
            check["choice_checks"] = [{"choice_quote": sources[_source_ref(sources, target["definition_path"])]["quote"],
                "guarantee_ids": [], "downstream_refs": [ref for ref, source in sources.items()
                    if any(source["path"] == path or source["path"].startswith(path + "/")
                           for path in target["downstream_paths"])], "compatible": True, "counterexample": ""}]


@pytest.mark.parametrize("reason", [
    "玩家可先打电话，也可直接去管理处。",
    "玩家可当面签收，也可委托寄回。",
])
def test_a_route_check_cannot_omit_the_other_explicit_arrangement(reason):
    context, _, _, payload = _case()
    context["mainline"][0]["outgoing_routes"][0]["transition_contract"]["reason"] = reason
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {row["id"] for row in context["mainline"]})
    _matrices(targets, payload, sources)
    check = next(c for t, c in zip(targets, payload["contract_checks"]) if t.get("kind") == "transition_contract")
    check["choice_checks"][0]["choice_quote"] = reason.split("，")[0]
    with pytest.raises(ValueError, match="incomplete_choice_reason"):
        _validate(context, sources, payload)


@pytest.mark.parametrize("omitted", ["bridge_scene_narration", "must_deliver", "must_preserve", "opening_scene"])
def test_a_route_check_cannot_clear_only_the_safe_downstream_fields(omitted):
    context, _, _, payload = _case()
    contract = context["mainline"][0]["outgoing_routes"][0]["transition_contract"]
    contract.update(must_deliver=["林舟已在柜台签收。"], must_preserve=["林舟已亲自核实。"])
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {row["id"] for row in context["mainline"]})
    _matrices(targets, payload, sources)
    check = next(c for t, c in zip(targets, payload["contract_checks"]) if t.get("kind") == "transition_contract")
    check["choice_checks"][0]["downstream_refs"] = [ref for ref in check["choice_checks"][0]["downstream_refs"]
        if omitted not in sources[ref]["path"].split("/")]
    with pytest.raises(ValueError, match="incomplete_choice_downstream"):
        _validate(context, sources, payload)


def test_each_explicit_arrangement_is_checked_against_the_complete_downstream():
    context, sources, targets, payload = _case()
    _matrices(targets, payload, sources)
    check = next(c for t, c in zip(targets, payload["contract_checks"]) if t.get("kind") == "transition_contract")
    row = check["choice_checks"][0]
    check["choice_checks"] = [{**deepcopy(row), "choice_quote": quote}
        for quote in ["玩家可先打电话", "直接去管理处", "或两者都做"]]
    before = deepcopy((context, sources, payload))
    _validate(context, sources, payload)
    assert (context, sources, payload) == before
    check["choice_checks"][1]["downstream_refs"].pop()
    with pytest.raises(ValueError, match="incomplete_choice_downstream"):
        _validate(context, sources, payload)


@pytest.mark.parametrize("fixed_only,has_entry,opening_expected", [(True, True, False), (True, False, True), (False, True, True)])
def test_route_downstream_coverage_uses_only_the_material_actually_shown_at_entry(fixed_only, has_entry, opening_expected):
    from services.theater_workshop.sdk.generation.facts import bind_contract_targets
    context, _, _, _ = _case()
    context["mainline"][0]["outgoing_routes"][0]["transition_contract"]["fixed_entry_only"] = fixed_only
    target = context["mainline"][1]
    target["fixed_narrations"] = [
        {"id": "later", "text": "玩家日后签收才会显示。", "trigger": {"type": "fact", "key": "choice", "equals": True}},
        *([{"id": "entry", "text": "小岚当场展示收据。", "trigger": {"type": "entry"}}] if has_entry else []),
    ]
    _, sources = build_fact_sources(context)
    targets = bind_contract_targets(build_contract_targets(context, {"main"}), sources)
    transition = next(row for row in targets if row.get("kind") == "transition_contract")
    paths = {sources[ref]["path"] for ref in transition["downstream_refs"]}
    assert ("/mainline/1/opening_scene" in paths) is opening_expected
    assert "/mainline/1/fixed_narrations/0/text" not in paths
    assert ("/mainline/1/fixed_narrations/1/text" in paths) is has_entry


def test_free_text_clear_is_not_an_entry_or_route_evidence_check():
    context, sources, targets, payload = _case()
    with pytest.raises(ValueError, match="matrix|ownership|choice|contract_check_fields"):
        _validate(context, sources, payload)


@pytest.mark.parametrize("opening", ["林舟检查设备接口。小岚展示登记信息。", "林舟签署借展文书。小岚展示展品目录。"])
@pytest.mark.parametrize("mutation", ["missing_unit", "wrong_owner", "role_permission", "violation_without_issue"])
def test_player_action_cannot_use_actor_permission_or_an_unchecked_unit(opening, mutation):
    context, sources, targets, payload = _case(opening)
    _matrices(targets, payload, sources)
    index = next(i for i, target in enumerate(targets) if target["id"] == "free:entry:opening")
    row = payload["contract_checks"][index]["ownership_checks"][0]
    row.update(subject="林舟", owner="player", kind="new_action")
    if mutation == "missing_unit": payload["contract_checks"][index]["ownership_checks"].pop()
    elif mutation == "wrong_owner": row["owner"] = "catgirl"
    elif mutation == "role_permission": row["authorization_refs"] = [_source_ref(sources, "/characters/catgirl_name")]
    else:
        row["status"] = "violation"
        payload["contract_checks"][index]["status"] = "uncertain"
    with pytest.raises(ValueError): _validate(context, sources, payload)


def test_explicit_same_action_authorization_is_distinct_from_role_permissions():
    context, sources, targets, payload = _case()
    context["mainline"][0]["outgoing_routes"][0]["transition_contract"]["accept_input"] = "我来检查设备接口。"
    _, sources = build_fact_sources(context)
    _matrices(targets, payload, sources)
    index = next(i for i, target in enumerate(targets) if target["id"] == "free:entry:opening")
    payload["contract_checks"][index]["ownership_checks"][0].update(subject="林舟", owner="player", kind="new_action",
        authorization_refs=[_source_ref(sources, "/mainline/0/outgoing_routes/0/transition_contract/accept_input")])
    before = deepcopy(payload)
    _validate(context, sources, payload)
    assert payload == before


def test_route_counterexample_cannot_be_hidden_by_clear_or_empty_issues():
    context, sources, targets, payload = _case()
    _matrices(targets, payload, sources)
    index = next(i for i, target in enumerate(targets) if target.get("kind") == "transition_contract")
    check = payload["contract_checks"][index]
    check["choice_checks"][0].update(compatible=False, counterexample="玩家只打电话仍合法离幕，却被写成已经到场。")
    check["status"] = "uncertain"
    with pytest.raises(ValueError, match="issue|matrix|counterexample"):
        _validate(context, sources, payload)


def _player_row(targets, payload):
    index = next(i for i, target in enumerate(targets) if target["id"] == "free:entry:opening")
    return payload["contract_checks"][index], payload["contract_checks"][index]["ownership_checks"][0]


@pytest.mark.parametrize("kind", ["history", "passive", "new_action"])
def test_known_history_passive_player_and_independent_role_act_do_not_need_player_authorization(kind):
    context, sources, targets, payload = _case()
    context["background"] = "林舟昨天已检查设备接口。"
    _, sources = build_fact_sources(context)
    _matrices(targets, payload, sources)
    _, row = _player_row(targets, payload)
    row.update(subject="小岚" if kind == "new_action" else "林舟", owner="catgirl" if kind == "new_action" else "player", kind=kind)
    if kind == "history":
        row["authorization_refs"] = [_source_ref(sources,"/background")]
    if kind == "new_action":
        row.update(unit_id="u1", quote=targets[next(i for i,t in enumerate(targets) if t["id"] == "free:entry:opening")]["ownership_units"][1]["text"])
        # Retain a separate first-unit row; one role action cannot cover another subject's unit.
        check, _ = _player_row(targets, payload)
        check["ownership_checks"].append({"unit_id":"u0","quote":"林舟检查设备接口。","subject":"林舟",
            "owner":"player","kind":"history","authorization_refs":[_source_ref(sources,"/background")],"status":"clear"})
    _validate(context, sources, payload)


def test_auto_route_cannot_borrow_its_unused_manual_acceptance_as_player_authorization():
    context, _, _, _ = _case()
    contract = context["mainline"][0]["outgoing_routes"][0]["transition_contract"]
    contract.update(advance_on_completion=True, accept_input="我来检查设备接口。")
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {row["id"] for row in context["mainline"]})
    _, _, _, payload = _case()
    _matrices(targets, payload, sources)
    _, row = _player_row(targets, payload)
    row.update(subject="林舟",owner="player",kind="new_action",authorization_refs=[_source_ref(sources,
        "/mainline/0/outgoing_routes/0/transition_contract/accept_input")])
    with pytest.raises(ValueError, match="authorization"):
        _validate(context, sources, payload)


@pytest.mark.parametrize("both_routes", [False, True])
def test_shared_entry_authorization_requires_every_actual_inbound_not_union_of_guarantees(both_routes):
    context, _, _, payload = _case()
    source = context["mainline"][0]
    source["outgoing_routes"][0]["transition_contract"]["accept_input"] = "我来检查设备接口。"
    other = deepcopy(source)
    other["id"] = "other"
    other["outgoing_routes"][0]["id"] = "other_leave"
    context["branches"] = [{"id":"other_branch","nodes":[other]}]
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {row["id"] for row in context["mainline"]})
    _matrices(targets, payload, sources)
    _, row = _player_row(targets, payload)
    row.update(subject="林舟",owner="player",kind="new_action",authorization_refs=[_source_ref(sources,
        "/mainline/0/outgoing_routes/0/transition_contract/accept_input")])
    if both_routes:
        row["authorization_refs"].append(_source_ref(sources,
            "/branches/0/nodes/0/outgoing_routes/0/transition_contract/accept_input"))
        _validate(context, sources, payload)
    else:
        with pytest.raises(ValueError, match="authorization"):
            _validate(context, sources, payload)


def test_unknown_player_action_remains_uncertain_without_inventing_a_repair_issue():
    context, sources, targets, payload = _case()
    _matrices(targets, payload, sources)
    check, row = _player_row(targets, payload)
    row.update(subject="林舟",owner="player",kind="new_action",status="uncertain")
    check["status"] = "uncertain"
    review = _validate(context, sources, payload)
    assert review["issues"] == []


def test_concrete_route_counterexample_with_matching_evidence_is_retained():
    context, sources, targets, payload = _case()
    _matrices(targets, payload, sources)
    index = next(i for i,t in enumerate(targets) if t.get("kind") == "transition_contract")
    check = payload["contract_checks"][index]
    check["status"] = "uncertain"
    check["choice_checks"][0].update(compatible=False,counterexample="电话核实满足条件但没有亲赴管理处。")
    payload["issues"] = [{"category":"sequence","severity":"major","target_node_ids":["main"],
        "problem":"电话路线被写成亲赴管理处。","modification_plan":"收回到场断言，保留两种核实方式。",
        "expected_result":"承接实际选择。","preserve":["保留原选择。"],"repair_scope":"text",
        "repair_targets":[{"node_id":"main","field":"/outgoing_routes/0/transition_contract/bridge_scene_narration"}],
        "evidence_refs":[check["evidence_refs"][0],check["choice_checks"][0]["downstream_refs"][0]]}]
    assert _validate(context, sources, payload)["issues"][0]["problem"] == payload["issues"][0]["problem"]


def test_goals_are_not_invented_as_route_guarantees():
    context, sources, targets, payload = _case()
    _matrices(targets, payload, sources)
    check = next(c for t,c in zip(targets,payload["contract_checks"]) if t.get("kind") == "transition_contract")
    check["choice_checks"][0]["guarantee_ids"] = ["goal.some_future_action"]
    with pytest.raises(ValueError,match="choice"):
        _validate(context,sources,payload)


def test_request_units_locate_complete_original_without_duplicating_it():
    from services.theater_workshop.sdk.generation.facts import bind_contract_targets
    text = "\n 林舟站在门边，小岚打开台灯。\n"
    context,sources,targets,_ = _case(text)
    bind_contract_targets(targets,sources)
    target = next(t for t in targets if t["id"] == "free:entry:opening")
    assert all(set(unit) == {"id","start","end"} for unit in target["ownership_units"])
    assert "".join(text[u["start"]:u["end"]] for u in target["ownership_units"]) == text


def test_punctuation_only_entry_units_stay_with_adjacent_sentence_without_losing_text():
    import unicodedata
    text = "老人说：‘先核实。再回答。’\n录音停止。"
    context,_,_,_ = _case(text)
    target=next(t for t in build_contract_targets(context,{'free'}) if t['kind']=='entry_ownership')
    units=target['ownership_units']
    assert ''.join(unit['text'] for unit in units)==text
    assert all(any(not c.isspace() and not unicodedata.category(c).startswith('P') for c in unit['text']) for unit in units)
    assert len(units)==3


def test_current_player_state_cannot_establish_history_for_the_same_entry_action():
    context,_,_,payload=_case()
    context['mainline'][1]['character_state']={'player':'林舟已检查设备接口。'}
    _,sources=build_fact_sources(context)
    targets=build_contract_targets(context,{row['id'] for row in context['mainline']})
    _matrices(targets,payload,sources)
    _,row=_player_row(targets,payload)
    row.update(subject='林舟',owner='player',kind='history',authorization_refs=[_source_ref(sources,'/mainline/1/character_state/player')])
    with pytest.raises(ValueError,match='history|authorization'):
        _validate(context,sources,payload)


def test_explicit_background_history_supports_player_history_without_acceptance():
    context,_,_,payload=_case()
    context['background']='林舟昨晚已检查过这台设备接口。'
    _,sources=build_fact_sources(context)
    targets=build_contract_targets(context,{row['id'] for row in context['mainline']})
    _matrices(targets,payload,sources)
    _,row=_player_row(targets,payload)
    row.update(subject='林舟',owner='player',kind='history',authorization_refs=[_source_ref(sources,'/background')])
    _validate(context,sources,payload)
