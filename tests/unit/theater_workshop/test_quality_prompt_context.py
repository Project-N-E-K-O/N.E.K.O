"""Verify assessment inputs and output limits without widening node repair permissions."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.facts import build_fact_sources, fact_review_tasks
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from services.theater_workshop.sdk.generation.repair import classify_repair
from .test_numeric_v2_evidence import review_payload
from .test_numeric_v2_generation import (
    _generation_setup, _quality_payload, _quality_reply, _quality_story_context, _quality_wire_payload,
    _fact_review_payload, _literary_block_payload,
)
from .test_numeric_v2_plan_review import ready_payload


def test_assessment_reads_temporal_plan_without_adding_text_repair_permission():
    story, authoring = _quality_story_context()
    temporal = {"anchor": "午后", "relation_to_previous": "same_day"}
    story["nodes"][0]["story_beat"].setdefault("character_state", {})["temporal_context"] = temporal
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    node = context["mainline"][0]
    assert node["character_state"]["temporal_context"] == temporal
    assert all("temporal_context" not in field for field in node["text_repair_fields"])
    assert story == before


def test_exact_marked_original_is_local_and_other_author_instructions_remain_citable():
    from services.theater_workshop.sdk.generation.quality import _content_hash

    story, authoring = _quality_story_context()
    first, _, _, fourth = authoring["mainline_node_ids"]
    text = "值班员登记夜间的灯号与时间。"
    missing = "这段没有对应的固定旁白，仍须检查。"
    setup = {**_generation_setup(),
             "brief": f"首幕应先打开前门。第四幕逐字展示[原文起]{text}[原文止]。"
                      f"结局另有原文[原文起]{missing}[原文止]。"}
    node = next(node for node in story["nodes"] if node["id"] == fourth)
    node["story_beat"]["fixed_narrations"] = [{
        "id": "author_archive", "text": text, "trigger": {"type": "entry"}, "after": [],
        "required_before_exit": False,
    }]
    original = deepcopy((story, setup))
    context = NumericV2QualityAssessor._assessment_context(story, setup, authoring)
    calls = {}
    reply = _quality_reply(_quality_payload())

    def model(messages, **options):
        operation = options["operation"]
        request = prompt_payload(messages[1]["content"])
        intent = request["story_context" if operation == "numeric_v2_quality_assessment" else "story_outline"]["author_intent"]
        assert intent[1] == {"fixed_original_refs": [{"node_id": fourth, "fixed_narration_id": "author_archive"}]}
        assert missing in messages[1]["content"]
        if operation == "numeric_v2_fact_review":
            focus, = request["checked_node_ids"]
            task = request["audit_task"]
            calls[task["id"]] = messages[1]["content"].count(text)
            if task["kind"] != "node" or focus != first:
                return reply(messages, **options)
            assert calls[task["id"]] == 0
            assert intent[0]["text"] == "首幕应先打开前门。第四幕逐字展示[原文起]"
            opening = request["story_outline"]["mainline"][0]["opening_scene"]
            return json.dumps(_fact_review_payload(request, [{
                "category": "state", "severity": "minor", "target_node_ids": [first],
                "problem": "首幕没有承接作者规定的前门状态。", "modification_plan": "调整首幕开场。",
                "expected_result": "符合作者意图。", "preserve": [], "repair_scope": "text",
                "repair_targets": [{"node_id": first, "field": "/opening_scene"}],
                "evidence_refs": [intent[0]["ref"], opening["ref"]],
            }]))
        if operation == "numeric_v2_fact_evidence_review":
            assert request["proposed_issues"][0]["evidence"][0] == {"path": "/author_intent/0"}
            assert intent[0] == "首幕应先打开前门。第四幕逐字展示[原文起]"
            assert text not in messages[1]["content"]
            return json.dumps(review_payload(request["proposed_issues"]))
        if operation == "numeric_v2_literary_block":
            block = _literary_block_payload(request)
            if fourth in request["checked_node_ids"]:
                ref = next(source["ref"] for source in request["original_sources"]
                           if source["node_id"] == fourth and source["path"].endswith("/fixed_narrations/0/text"))
                next(note for note in block["node_notes"] if note["node_id"] == fourth)["evidence_refs"] = [ref]
            return json.dumps(block, ensure_ascii=False)
        if operation == "numeric_v2_quality_assessment":
            assert messages[1]["content"].count(text) == 1
        return reply(messages, **options)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=setup, authoring=authoring)
    assert calls[f"node:{fourth}"] == 1
    assert report["issues"][0]["evidence"][0] == {
        "path": "/author_intent", "quote": "首幕应先打开前门。第四幕逐字展示[原文起]",
    }
    assert report["content_sha256"] == _content_hash(context)
    assert (story, setup) == original


def test_single_node_optimization_merges_identical_commands_after_local_scope():
    from .test_numeric_v2_generation import _reviewed_fixture

    story, authoring = _quality_story_context()
    first, second, third = authoring["mainline_node_ids"][:3]
    assessor = NumericV2QualityAssessor()
    setup = _generation_setup()
    context = assessor._assessment_context(story, setup, authoring)
    payload = _quality_payload(weak_dimension="plot", target_node_id=first)
    issue = payload["issues"][0]
    issue.update(target_node_ids=[first, second], repair_targets=[
        {"node_id": first, "field": "/summary"}, {"node_id": second, "field": "/summary"},
    ])
    payload["relationship_advice"] = [{
        "target_node_ids": [first, third],
        "repair_targets": [{"node_id": first, "field": "/summary"}, {"node_id": third, "field": "/summary"}],
        "problem": "还需要明确角色为何在意本幕选择。",
        "suggestion": issue["modification_plan"], "expected_result": "更清晰的关系动机。", "repair_scope": "text",
    }]
    report = _reviewed_fixture(assessor._validated_assessment(context, payload), context)
    original = deepcopy((report, story))

    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        accepted, = request["accepted_suggestions"]
        assert accepted["repair_targets"] == [{"node_id": first, "field": "/summary"}]
        impact, = accepted["related_impacts"]
        assert impact["problem"] == "还需要明确角色为何在意本幕选择。"
        assert request["story_outline"]["mainline"][2]["opening_scene"] == context["mainline"][2]["opening_scene"]
        return json.dumps({"node_updates": [{"node_id": first, "story_beat": {"summary": "本幕补足角色动机。"}}]})

    assessor.call_llm = model
    repaired = assessor.optimize_node(story=story, setup=setup, authoring=authoring, assessment=report, node_id=first)
    assert repaired["nodes"][1:] == story["nodes"][1:]
    assert (report, story) == original


@pytest.mark.parametrize("effect", ["none", "positive", "negative", None])
def test_assessment_receives_actual_relationship_effect_and_existing_missing_default(effect):
    story, authoring = _quality_story_context()
    metric = story["metric_schema"]["trust"]
    if effect is None:
        metric.pop("relationship_effect", None)
    else:
        metric["relationship_effect"] = effect
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    received = []
    original = _quality_reply(_quality_payload())

    def reply(messages, **options):
        request = prompt_payload(messages[1]["content"])
        received.append((options["operation"], request.get("story_outline", request.get("story_context"))))
        return original(messages, **options)

    assessor.call_llm = reply
    assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    task_count = len(fact_review_tasks(assessor._assessment_context(story, _generation_setup(), authoring)))
    block_count = len({(node["id"], route["target_node_id"]) for node in story["nodes"] for route in node.get("route_gates", [])})
    assert [operation for operation, _ in received] == [*["numeric_v2_fact_review"] * task_count,
        *["numeric_v2_literary_block"] * block_count, "numeric_v2_quality_assessment"]
    assert received[0][1]["metrics"][0]["relationship_effect"]["text"] == (effect or "none")
    assert received[-1][1]["metrics"][0]["relationship_effect"] == (effect or "none")
    assert story == before


def test_formal_background_is_citable_and_reaches_all_assessment_and_repair_stages():
    story, authoring = _quality_story_context()
    background = '花店的前门整日开放。\n门口贴着“访客可直接进入”的告示。'
    opening = "女主仍称前门关闭，要求访客从后门进入。"
    corrected = "女主指向敞开的前门，让访客看到入口的告示。"
    story["intro"]["background"] = background
    story["nodes"][0]["story_beat"]["opening_scene"] = opening
    # Unexported world rules must not become runtime facts merely to complete the canonical background.
    authoring["world"] = {"rules": ["HIDDEN_AUTHOR_RULE_DO_NOT_PROJECT"]}
    before, authoring_before = deepcopy(story), deepcopy(authoring)
    assessor = NumericV2QualityAssessor()
    calls = []
    target = {"node_id": "mainline_01", "field": "/opening_scene"}

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        request = prompt_payload(messages[1]["content"])
        assert "HIDDEN_AUTHOR_RULE_DO_NOT_PROJECT" not in messages[1]["content"]
        if operation == "numeric_v2_quality_assessment":
            assert request["story_context"]["background"] == background
            assert "/background" not in request["allowed_target_node_ids"]
            return json.dumps(_quality_wire_payload(_quality_payload()), ensure_ascii=False)
        outline = request["story_outline"]
        if operation == "numeric_v2_fact_review":
            if request["audit_task"]["kind"] != "node" or request["checked_node_ids"] != ["mainline_01"]:
                return json.dumps(_fact_review_payload(request))
            assert outline["background"]["text"] == background
            assert list(outline).index("background") < list(outline).index("mainline")
            return json.dumps(_fact_review_payload(request, [{
                "category": "state", "severity": "major", "target_node_ids": ["mainline_01"],
                "problem": "开场与正式背景的前门状态冲突。", "modification_plan": "仅把开场的入口说明改成前门开放。",
                "expected_result": "开场承接正式背景。", "preserve": ["保留正式背景原文。"],
                "repair_scope": "text", "repair_targets": [target],
                "evidence_refs": [outline["background"]["ref"], outline["mainline"][0]["opening_scene"]["ref"]],
            }]), ensure_ascii=False)
        assert (outline["background"]["text"] if operation == "numeric_v2_literary_block" else outline["background"]) == background
        for node in outline["mainline"]:
            if operation == "numeric_v2_quality_single_node_optimization" and node["id"] != target["node_id"]:
                assert "text_repair_fields" not in node
                continue
            fields = (outline["repair_field_sets"][node["repair_field_set"]]
                      if operation == "numeric_v2_literary_block" else node["text_repair_fields"])
            assert "/background" not in fields
        if operation == "numeric_v2_fact_evidence_review":
            # Review has not occurred; do not give the judge a negative conclusion inferred from the absence of review.
            assert "repairable" not in request["proposed_issues"][0]
            assert "repair_reason" not in request["proposed_issues"][0]
            assert set(request["proposed_issues"][0]) == {
                "issue_id", "problem", "evidence", "target_node_ids", "repair_targets",
            }
            return json.dumps(review_payload(request["proposed_issues"]), ensure_ascii=False)
        if operation == "numeric_v2_literary_block":
            return json.dumps(_literary_block_payload(request), ensure_ascii=False)
        if operation == "numeric_v2_repair_plan_review":
            return json.dumps(ready_payload(request["plans"]), ensure_ascii=False)
        assert operation == "numeric_v2_quality_single_node_optimization"
        assert request["accepted_suggestions"][0]["repair_targets"] == [target]
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {
            "opening_scene": corrected,
        }}]}, ensure_ascii=False)

    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert report["issues"][0]["evidence"] == [
        {"path": "/background", "quote": background},
        {"path": "/mainline/0/opening_scene", "quote": opening},
    ]
    assert report["issues"][0]["repairable"] is True
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                      assessment=report, node_id="mainline_01")
    expected = deepcopy(before)
    expected["nodes"][0]["story_beat"]["opening_scene"] = corrected
    assert repaired == expected
    assert story == before and authoring == authoring_before
    task_count = len(report["fact_check"]["checked_tasks"])
    block_count = len(report["literary_coverage"]["blocks"])
    assert calls == [*["numeric_v2_fact_review"] * task_count, "numeric_v2_fact_evidence_review",
                     *["numeric_v2_literary_block"] * block_count, "numeric_v2_quality_assessment",
                     "numeric_v2_repair_plan_review", "numeric_v2_quality_single_node_optimization"]


def test_different_formal_backgrounds_change_assessment_and_citation_inputs():
    story, authoring = _quality_story_context()
    inputs, sources = [], []
    for background in ("花店前门开放。", "花店前门关闭，访客须走后门。"):
        story["intro"]["background"] = background
        context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
        inputs.append(context)
        _, evidence = build_fact_sources(context)
        sources.append(evidence)
    assert inputs[0] != inputs[1]
    assert sources[0] != sources[1]


@pytest.mark.parametrize("update", [
    {"background": "重写全局背景。"},
    {"story_beat": {"background": "把背景伪装成节点文本。"}},
])
def test_formal_background_does_not_gain_node_repair_permission(update):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    issue = {"target_node_ids": ["mainline_01"], "repair_scope": "text",
             "repair_targets": [{"node_id": "mainline_01", "field": "/background"}]}
    assert classify_repair(context, issue)["repairable"] is False
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        NumericV2QualityAssessor._apply_node_updates(story, {"node_updates": [
            {"node_id": "mainline_01", **update},
        ]}, ["mainline_01"])
    assert story == before


def test_actual_literature_request_declares_metric_count_limit():
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    original = _quality_reply(_quality_payload())
    seen = []

    def reply(messages, **options):
        if options["operation"] == "numeric_v2_quality_assessment":
            seen.append(messages[0]["content"])
            request = prompt_payload(messages[1]["content"])
            assert request["allowed_target_node_ids"] == sorted(node["id"] for node in story["nodes"])
            assert "metrics 不是节点" in messages[0]["content"]
            assert "positive、negative、none" in messages[0]["content"]
        return original(messages, **options)

    assessor.call_llm = reply
    assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(seen) == 1
    assert "recommended_count 必须为 0—4 的整数" in seen[0]


@pytest.mark.parametrize("shared_branch", [False, True])
def test_literary_request_for_builtin_neon_fits_32000_without_cutting_scene_prose(shared_branch):
    from pathlib import Path
    from utils.tokenize import count_tokens

    path = Path(__file__).resolve().parents[3] / "assets/theater/story_neon_rebuild_ea2e45dab7.json"
    story = json.loads(path.read_text(encoding="utf-8"))
    if shared_branch:
        from services.theater.numeric_v2 import NumericV2Compiler

        source_routes = story["nodes"][4]["route_gates"]
        ordinary, branch = story["nodes"][5]["route_gates"]
        source_routes[0]["conditions"] = deepcopy(ordinary["conditions"])
        source_routes.append({**deepcopy(branch), "id": "audit_shared_branch_route"})
        NumericV2Compiler().compile_v2_2(story)
    authoring = {"mainline_node_ids": [node["id"] for node in story["nodes"]
                 if node["id"].startswith("mainline_") and node["type"] != "ending"]}
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    reply = _quality_reply(_quality_payload())
    calls = [];covered_nodes = set();covered_edges = set();shared_refs = []
    source_nodes = {node["id"]: node for node in [*context["mainline"], *context["endings"],
                    *(node for branch in context["branches"] for node in branch["nodes"])]}

    def restore(value):
        if isinstance(value, dict):
            return value["text"] if set(value) == {"ref", "text"} else {key: restore(item) for key, item in value.items()}
        return [restore(item) for item in value] if isinstance(value, list) else value

    def model(messages, **options):
        calls.append(options["operation"])
        assert count_tokens(json.dumps(messages, ensure_ascii=False)) < 32000
        request = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_quality_assessment":
            assert "story_outline" not in request
            assert {node["id"] for node in request["story_graph"]["nodes"]} == set(source_nodes)
            for node in request["story_graph"]["nodes"]:
                assert node["opening_scene"] == source_nodes[node["id"]]["opening_scene"]
                assert node["summary"] == source_nodes[node["id"]]["summary"]
            return reply(messages, **options)
        assert options["operation"] == "numeric_v2_literary_block"
        outline = restore(request["story_outline"])
        covered_nodes.update(request["checked_node_ids"])
        covered_edges.update((edge["source_node_id"], edge["route_id"], edge["target_node_id"])
                             for edge in request["covered_edges"])
        for rows in [outline["mainline"], outline["endings"], *(branch["nodes"] for branch in outline["branches"])]:
            for sent in rows:
                if "shared_node_ref" in sent:
                    canonical = outline
                    for key in sent["shared_node_ref"].lstrip("/").split("/"):
                        canonical = canonical[int(key)] if isinstance(canonical, list) else canonical[key]
                    assert canonical["id"] == sent["id"]
                    shared_refs.append(sent["shared_node_ref"])
                    continue
                source = source_nodes[sent["id"]]
                for field in ("id", "opening_scene", "summary", "fixed_narrations", "outgoing_routes"):
                    assert sent.get(field) == source.get(field)
                assert outline["repair_field_sets"][sent["repair_field_set"]] == source["text_repair_fields"]
        return reply(messages, **options)

    NumericV2QualityAssessor(model)._assess_literature(context, {"issues": []})
    assert calls[-1] == "numeric_v2_quality_assessment" and calls.count("numeric_v2_quality_assessment") == 1
    assert covered_nodes == set(source_nodes)
    assert covered_edges == {(node["id"], route["id"], route["target_node_id"])
                             for node in story["nodes"] for route in node.get("route_gates", [])}
    assert bool(shared_refs) is shared_branch


@pytest.mark.parametrize("count,accepted", [(0, True), (4, True), (5, False), (-1, False), (1.5, False), (True, False)])
def test_metric_advice_count_remains_strict_and_is_never_clamped(count, accepted):
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload()
    payload["metric_advice"]["recommended_count"] = count
    assessor.call_llm = _quality_reply(payload)
    if accepted:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert report["metric_advice"]["recommended_count"] == count
    else:
        with pytest.raises(QualityAssessmentError, match="invalid_quality_assessment") as error:
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert error.value.phase == "literature"


@pytest.mark.parametrize("destination", ["观测舱", "山间石桥"])
def test_silent_choice_is_visible_to_assessment_without_expanding_repair(destination):
    from services.theater_workshop.sdk.generation.repair import TRANSITION_CONTRACT_FIELDS
    story, authoring = _quality_story_context()
    contract = story["nodes"][0]["route_gates"][0]["transition_contract"]
    contract.update({"silent_choice": {"destination": destination, "arrangement": "查看记录"},
                     "accept_input": "按刚才的安排。", "fallback_offer": ""})
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    projected = context["mainline"][0]["outgoing_routes"][0]["transition_contract"]
    for field in ("silent_choice", "accept_input", "fallback_offer"):
        assert projected[field] == contract[field]
        assert field not in TRANSITION_CONTRACT_FIELDS
    projected["silent_choice"]["destination"] = "别处"
    assert contract["silent_choice"]["destination"] == destination
