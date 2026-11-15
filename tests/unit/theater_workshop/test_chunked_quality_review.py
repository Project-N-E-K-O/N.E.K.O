"""Explicit task and citation coverage, not a proxy for model semantic accuracy."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _quality_reply, _fact_review_payload


def test_global_source_sharing_restores_full_quote_and_keeps_original_location():
    from services.theater_workshop.sdk.generation.quality import _shared_original_sources
    from services.theater_workshop.sdk.generation.facts import _resolve_pointer
    quote = "完整条件前半句必须保留。后半句也必须保留。"
    sources = {"E1":{"path":"/mainline/0/opening_scene","quote":quote},
               "E2":{"path":"/mainline/0/goals/0/description","quote":"图中没有的完整目标原文。"}}
    graph = {"nodes":[{"opening_scene":quote}]}
    request = {"story_graph":graph,"story_context":{},
               "original_sources":_shared_original_sources(sources,set(sources),graph,{})}
    assert request["original_sources"][0] == {"ref":"E1","path":sources["E1"]["path"],
                                             "text_path":"/story_graph/nodes/0/opening_scene"}
    for row in request["original_sources"]:
        assert (row.get("quote") or _resolve_pointer(request,row["text_path"])) == sources[row["ref"]]["quote"]


def _story(genre):
    story, authoring = _quality_story_context()
    story["intro"]["background"] = "社区电台核实点播请求。" if genre == "radio" else "馆员核实归还藏品。"
    source = story["nodes"][1]
    ending = deepcopy(story["nodes"][-1])
    ending.update(id="other_ending", ending_id="other")
    for goal in ending["story_beat"]["goals"]:
        goal["id"] = "other_" + goal["id"]
    story["nodes"].append(ending)
    story["endings"].append({"id": "other", "title": "另一结局", "summary": "保留另一种收束。", "terminal": True})
    branch = deepcopy(source)
    branch.update(id="optional_branch")
    branch.pop("completion_contract", None)
    for goal in branch["story_beat"]["goals"]:
        goal["id"] = "branch_" + goal["id"]
        goal["delivery"]["source_ids"] = ["runtime.player_input"]
    branch["route_gates"] = [deepcopy(source["route_gates"][0])]
    branch["route_gates"][0].update(id="branch_to_other", target_node_id="other_ending")
    branch["route_gates"][0]["transition_contract"]["source_ids"] = ["goal." + branch["story_beat"]["goals"][-1]["id"]]
    story["nodes"].append(branch)
    route = deepcopy(source["route_gates"][0])
    route.update(id="take_optional", target_node_id="optional_branch")
    source["route_gates"].append(route)
    # An explicit earlier author-source reference must remain visible even outside the pair.
    first = story["nodes"][0]["story_beat"]["goals"][0]
    story["nodes"][3]["story_beat"]["goals"][0]["delivery"]["source_ids"] = ["goal." + first["id"]]
    return story, authoring, first


def _local_reply(request):
    originals = request["original_sources"]
    return {"checked_node_ids": request["checked_node_ids"], "issues": [], "node_notes": [{
        "node_id": node_id, "assessment": "局部样例意见，只验证原文接线，不赋分。",
        "evidence_refs": [next(row["ref"] for row in originals if row.get("node_id") == node_id)],
    } for node_id in request["checked_node_ids"]]}


@pytest.mark.parametrize("genre", ["radio", "museum"])
def test_tasks_cover_real_edges_two_endings_and_declared_distant_originals(genre):
    story, authoring, first = _story(genre)
    before = deepcopy((story, authoring))
    tasks, local, scoring = [], [], []
    fallback = _quality_reply(_quality_payload())

    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        operation = options["operation"]
        if operation == "numeric_v2_exit_fact_binding":
            return json.dumps({"bindings": [{"route_id": route["route_id"], "claim_ref": claim["ref"],
                "kind": "other", "fact_key": None, "value": None}
                for route in request["routes"] for claim in route["claims"]]})
        if operation == "numeric_v2_fact_review":
            task = request["audit_task"]
            tasks.append(task)
            if task["kind"] == "node":
                assert all(target.get("kind") != "transition_contract" for target in request["contract_targets"])
                if task["node_id"] == story["nodes"][3]["id"]:
                    assert first["description"] in json.dumps(request["dependency_originals"], ensure_ascii=False)
            else:
                assert task["kind"] == "transition"
                assert len(request["contract_targets"]) == 1
                assert request["contract_targets"][0]["route_ids"] == [task["route_id"]]
            return json.dumps(_fact_review_payload(request), ensure_ascii=False)
        if operation == "numeric_v2_literary_block":
            local.append(request)
            assert 1 <= len(request["checked_node_ids"]) <= 2
            return json.dumps(_local_reply(request), ensure_ascii=False)
        if operation == "numeric_v2_quality_assessment":
            scoring.append(request)
            assert request["literary_blocks"]
            assert {row["id"] for row in request["story_graph"]["nodes"]} == {node["id"] for node in story["nodes"]}
            assert "局部样例意见" in json.dumps(request, ensure_ascii=False)
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert [task["node_id"] for task in tasks if task["kind"] == "node"] == [node["id"] for node in NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)["mainline"]] + [
        node["id"] for node in NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)["endings"]] + ["optional_branch"]
    expected_edges = {(node["id"], route["id"], route["target_node_id"]) for node in story["nodes"] for route in node["route_gates"]}
    assert {(task["source_node_id"], task["route_id"], task["target_node_id"]) for task in tasks if task["kind"] == "transition"} == expected_edges
    assert {node for block in local for node in block["checked_node_ids"]} == {node["id"] for node in story["nodes"]}
    assert len(scoring) == 1 and report["fact_check"]["status"] == "complete"
    assert (story, authoring) == before


def test_failed_literary_block_cannot_return_a_partial_score():
    story, authoring, _ = _story("museum")
    fallback = _quality_reply(_quality_payload())
    scoring = []
    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        if options["operation"] in {"numeric_v2_literary_block", "numeric_v2_literary_block_format_repair"}:
            return json.dumps({"checked_node_ids": [], "issues": [], "node_notes": []})
        if options["operation"] == "numeric_v2_quality_assessment":
            scoring.append(True)
        return fallback(messages, **options)
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert scoring == []


def test_internal_and_route_duties_keep_distinct_inbounds_and_citation_paths():
    from services.theater_workshop.sdk.generation.facts import build_fact_sources, fact_review_tasks, fact_task_context
    story, authoring, _ = _story("museum")
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    source, other = context["mainline"][1:3]
    target = context["endings"][0]
    other["outgoing_routes"].append({**deepcopy(source["outgoing_routes"][0]), "id": "other_entry", "target_node_id": target["id"]})
    source["outgoing_routes"][0]["target_node_id"] = target["id"]
    source["outgoing_routes"][0]["conditions"] = {"mode": "all", "requirements": [{"fact_key": "choice", "equals": "phone"}]}
    other["outgoing_routes"][-1]["conditions"] = {"mode": "all", "requirements": [{"fact_key": "choice", "equals": "visit"}]}
    marked, sources = build_fact_sources(context)
    tasks = fact_review_tasks(context)
    entries = [task for task in tasks if task["kind"] == "transition" and task["target_node_id"] == target["id"]]
    assert len(entries) >= 2
    for task in entries:
        outline, _ = fact_task_context(context, marked, task)
        inbounds = [row for row in outline["entry_routes"] if row["target_node_id"] == target["id"]]
        assert {row["route_id"] for row in inbounds} >= {source["outgoing_routes"][0]["id"], "other_entry"}
        assert len({json.dumps(row["conditions"], sort_keys=True) for row in inbounds}) >= 2
        focus = next(row for row in outline["mainline"] if row["id"] == task["source_node_id"])
        assert [row["id"] for row in focus["outgoing_routes"]] == [task["route_id"]]
        ref = focus["outgoing_routes"][0]["transition_contract"]["reason"]["ref"]
        assert sources[ref]["path"].endswith("/transition_contract/reason")


@pytest.mark.parametrize("source_id,code", [("goal.missing", "unresolved"), ("prop.unknown", "unresolved"), ("goal.cycle", "cycle")])
def test_declared_dependency_cannot_be_guessed_or_silently_omitted(source_id, code):
    from services.theater_workshop.sdk.generation.facts import build_fact_sources, fact_review_tasks, fact_task_context
    story, authoring, _ = _story("radio")
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    goal = context["mainline"][0]["goals"][0]
    goal.update(id="cycle", source_ids=[source_id])
    marked, _ = build_fact_sources(context)
    with pytest.raises(ValueError, match="audit_dependency_" + code):
        fact_task_context(context, marked, fact_review_tasks(context)[0])


@pytest.mark.parametrize("case", ["old", "missing_task", "duplicate_task", "missing_edge", "duplicate_edge", "missing_literary", "missing_block", "wrong_block_edge"])
def test_incomplete_chunk_coverage_never_authorizes_cached_repair(case):
    from .test_contract_report_freshness import _fixture
    story, authoring, assessor, report = _fixture()
    if case == "old":
        report.pop("fact_check", None)
        report.pop("literary_coverage", None)
    elif case == "missing_literary":
        report.pop("literary_coverage", None)
    elif case == "missing_task":
        report["fact_check"]["checked_tasks"].pop()
    elif case == "duplicate_task":
        report["fact_check"]["checked_tasks"].append(report["fact_check"]["checked_tasks"][0])
    elif case == "missing_edge":
        report["fact_check"]["checked_edges"].pop()
    elif case == "duplicate_edge":
        report["fact_check"]["checked_edges"].append(deepcopy(report["fact_check"]["checked_edges"][0]))
    elif case == "missing_block":
        report["literary_coverage"]["blocks"].pop()
    else:
        report["literary_coverage"]["blocks"][0]["covered_edges"][0]["route_id"] = "foreign"
    assessor.call_llm = lambda *_args, **_kwargs: pytest.fail("Incomplete chunk coverage reached repair model")
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                               assessment=report, node_id="mainline_01")
    assert caught.value.code == "quality_reassessment_required"


def test_unresolved_legacy_metadata_marks_audit_incomplete_instead_of_leaking_value_error():
    story, authoring, _ = _story("museum")
    story["nodes"][0]["story_beat"]["goals"][0]["delivery"]["source_ids"] = ["custom_legacy_id"]
    assessor = NumericV2QualityAssessor(lambda *_args, **_kwargs: pytest.fail("Unresolved metadata must fail before a model call"))
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert caught.value.phase == "facts"
    assert caught.value.provider_details["validation_error"] == "audit_dependency_unresolved:custom_legacy_id"


@pytest.mark.parametrize("genre", ["radio", "museum"])
def test_skipped_real_edge_check_fails_the_whole_audit(genre):
    story, authoring, _ = _story(genre)
    fallback = _quality_reply(_quality_payload())
    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        if options["operation"] in {"numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"}:
            response = _fact_review_payload(request)
            if request["audit_task"]["kind"] == "transition":
                response["contract_checks"] = []
            return json.dumps(response)
        assert options["operation"] not in {"numeric_v2_literary_block", "numeric_v2_quality_assessment"}
        return fallback(messages, **options)
    with pytest.raises(QualityAssessmentError) as caught:
        NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert caught.value.phase == "facts"


def test_global_score_retains_local_major_issue_and_its_exact_original_evidence():
    story, authoring, _ = _story("museum")
    payload = _quality_payload()
    fallback = _quality_reply(payload)
    emitted = []
    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_literary_block":
            response = _local_reply(request)
            if not emitted:
                node_id = request["checked_node_ids"][0]
                ref = response["node_notes"][0]["evidence_refs"][0]
                response["issues"] = [{"dimension": "plot", "severity": "major", "target_node_ids": [node_id],
                    "problem": "局部原文未承接已铺设的关键线索。", "modification_plan": "仅调整本幕小结以回收原线索。",
                    "expected_result": "关键线索获得同因回收。", "preserve": ["保留玩家的最后决定。"],
                    "repair_scope": "text", "repair_targets": [{"node_id": node_id, "field": "/summary"}],
                    "evidence_refs": [ref]}]
                emitted.append(ref)
            return json.dumps(response, ensure_ascii=False)
        if options["operation"] == "numeric_v2_quality_assessment":
            assert request["retained_local_issues"][0]["issue_id"] == "L1"
            assert request["retained_local_issues"][0]["severity"] == "major"
            from services.theater_workshop.sdk.generation.facts import _resolve_pointer
            assert any(row["ref"] == emitted[0] and (row.get("quote") or _resolve_pointer(request,row["text_path"]))
                       for row in request["original_sources"])
            assert all("scores" not in block for block in request["literary_blocks"])
        return fallback(messages, **options)
    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    local = next(issue for issue in report["issues"] if issue["problem"] == "局部原文未承接已铺设的关键线索。")
    assert local["severity"] == "major" and local["source"] == "literature"
    assert local["evidence"][0]["path"].startswith("/mainline/0/")


def test_handoff_retains_entry_delivery_metadata_without_future_turn_dependencies():
    from services.theater_workshop.sdk.generation.facts import build_fact_sources, fact_review_tasks, fact_task_context
    story, authoring, _ = _story("museum")
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    target = context["mainline"][1]
    opening = {**deepcopy(target["goals"][0]), "id": "target_opening", "timing": "opening", "source_ids": ["runtime.player_input"]}
    unknown = {**deepcopy(opening), "id": "legacy_timing"}
    unknown.pop("timing")
    future = {**deepcopy(opening), "id": "later_goal", "timing": "turn", "source_ids": ["custom_future_reference"]}
    target["goals"] = [opening, unknown, future]
    marked, _ = build_fact_sources(context)
    task = next(row for row in fact_review_tasks(context) if row["kind"] == "transition" and row["target_node_id"] == target["id"])
    outline, dependencies = fact_task_context(context, marked, task)
    selected = next(node for node in outline["mainline"] if node["id"] == target["id"])
    assert [goal["id"] for goal in selected["goals"]] == ["target_opening", "legacy_timing"]
    assert "custom_future_reference" not in json.dumps(dependencies)
