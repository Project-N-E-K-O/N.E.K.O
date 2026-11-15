"""Verify pre-save boundaries for factual evidence and two-stage assessment; mocked models do not replace semantic stress tests."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json
import pytest
from services.theater_workshop.sdk.generation.facts import (
    build_fact_sources, validate_fact_review, node_fact_context, node_fact_review_context,
    fact_review_tasks, fact_task_context,
)


def test_fact_request_preserves_chronological_reading_order_and_all_original_fields():
    # The original request sorted JSON keys, mixing endings and future targets before opening state; preserve chronological reading order at the real entry point.
    # Opening targets are already delivered; concise review instructions must distinguish them from ordinary-turn pending targets.
    from services.theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
    assert "opening对应开场已交付，turn对应普通回合待演" in FACT_REVIEW_PROMPT
    from copy import deepcopy
    import json
    from .test_numeric_v2_generation import _quality_story_context, _generation_setup
    from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    captured = []
    def capture(messages, **kwargs):
        captured.append(prompt_payload(messages[1]["content"])["story_outline"])
        raise RuntimeError("inspection_stop")
    assessor.call_llm = capture
    with pytest.raises(RuntimeError, match="inspection_stop"):
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    outline = captured[0]
    keys = list(outline)
    assert keys.index("mainline") < keys.index("branches") < keys.index("endings")
    node_keys = list(outline["mainline"][0])
    assert node_keys.index("opening_scene") < node_keys.index("character_state") < node_keys.index("goals") < node_keys.index("outgoing_routes")
    # Reordering must not delete fields or alter author text; restoring all IDs must equal the original scoring projection.
    def restore(value):
        if isinstance(value, dict):
            if set(value) == {"ref", "text"}: return value["text"]
            return {key: restore(item) for key, item in value.items()}
        if isinstance(value, list): return [restore(item) for item in value]
        return value
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    sent = restore(outline)
    source = context["mainline"][0]
    assert sent["mainline"] == [{**source, "outgoing_routes": [
        {key: route[key] for key in ("id", "target_node_id", "conditions")}
        for route in source["outgoing_routes"]]}]
    assert sent["route_entries"][0]["entry"]["opening_scene"] == context["mainline"][1]["opening_scene"]
    assert sent["entry_routes"] == []
    assert sent["background"] == context["background"]
    assert story == before
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import (
    _quality_story_context, _generation_setup, _quality_payload, _quality_wire_payload,
    _reviewed_fixture, _fact_review_payload, _quality_reply, _literary_block_payload,
)


@pytest.mark.parametrize("mode", ["fixed_entry", "legacy_scene", "legacy_ending"])
def test_fact_and_evidence_use_ingress_and_cite_the_legacy_opening_source(mode):
    from services.theater.numeric_v2 import NumericV2Compiler
    from services.theater.numeric_v2_context import scene_opening_text
    from tests.unit.test_theater_numeric_v2_fixed_entry_only import _story
    from .test_numeric_v2_evidence import review_payload
    from .test_numeric_v2_generation import _quality_reply

    fixed = mode == "fixed_entry"
    ending = mode == "legacy_ending"
    story = _story(flag=True if fixed else None, entry=fixed)
    node_id = "ending_normal" if ending else "mainline_03"
    node = next(row for row in story["nodes"] if row["id"] == node_id)
    authoring = {"mainline_node_ids": [row["id"] for row in story["nodes"] if row["id"].startswith("mainline_")]}
    opening = "玩家说：我决定把旧信交给你。"
    beat = node["story_beat"]
    field = "opening_scene" if fixed else "scene_summary" if ending else "summary"
    if fixed:
        beat["opening_scene"] = opening
    else:
        del beat["opening_scene"]
        beat["summary"] = opening + "接下来要核对另一个线索。"
    assert scene_opening_text(beat) == opening
    assert NumericV2Compiler().compile_v2_2(story).story == story
    calls = []
    fallback = _quality_reply(_quality_payload())

    def reply(messages, **options):
        request = prompt_payload(messages[1]["content"])
        operation = options["operation"]
        if operation == "numeric_v2_fact_evidence_review" or (
                operation == "numeric_v2_fact_review" and request["audit_task"]["kind"] == "node"
                and request["checked_node_ids"] == [node_id]):
            calls.append(operation)
            outline = request["story_outline"]
            assert request["start_node_id"] == story["start_node_id"]
            projected = next(row for row in outline["endings" if ending else "mainline"] if row["id"] == node_id)
            assert "fixed_entry_only=true的入边有待展示的entry旁白时，跳过普通开场" in messages[0]["content"]
            assert "原文已展示的重访沿用普通开场" in messages[0]["content"]
            if fixed:
                if operation == "numeric_v2_fact_review":
                    source = next(row for row in outline["entry_routes"] if row["source_node_id"] == "mainline_02")
                    assert source["transition_contract"]["fixed_entry_only"] is True
                else:
                    source = next(row for row in outline["mainline"] if row["id"] == "mainline_02")
                    assert source["outgoing_routes"][0]["transition_contract"]["fixed_entry_only"] is True
                assert "opening_fallback_source" not in projected
            else:
                assert projected["opening_scene"] == ""
                assert projected["opening_fallback_source"] == field
            if operation == "numeric_v2_fact_review":
                return json.dumps(_fact_review_payload(request, [{
                    "category": "ownership", "severity": "major", "target_node_ids": [node_id],
                    "problem": "开场替玩家决定交信。", "modification_plan": "去除代玩家决定。",
                    "expected_result": "由玩家决定。", "preserve": [], "repair_scope": "text",
                    "repair_targets": [{"node_id": node_id, "field": "/" + field}],
                    "evidence_refs": [projected[field]["ref"], outline["characters"]["player_identity"]["ref"]],
                }]))
            issue, = request["proposed_issues"]
            assert issue["evidence"][0]["path"].endswith("/" + field)
            assert projected[field].startswith(opening)
            return json.dumps(review_payload([issue], "unsupported" if fixed else "supported"))
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(reply).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_evidence_review"]
    assert report["issues"][0]["evidence"][0]["quote"].startswith(opening)
    assert report["issues"][0]["repairable"] is not fixed


def test_fact_and_evidence_ignore_unexported_legacy_state_cache():
    from services.theater.numeric_v2 import NumericV2Compiler
    from .test_numeric_v2_evidence import review_payload
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    node_id = node["id"]
    node["story_beat"]["opening_scene"] = "女主戴着红手套，拉开柜台抽屉。"
    stale = {"node_id": node_id, **node["story_beat"].pop("character_state"), "catgirl_state": "女主没有戴手套。"}
    authoring["character_state_arc"] = {"stages": [stale]}
    before = deepcopy((story, authoring))
    assert NumericV2Compiler().compile_v2_2(story).story == story
    fallback = _quality_reply(_quality_payload())
    calls = []

    def model(messages, **options):
        body = prompt_payload(messages[1]["content"])
        operation = options["operation"]
        assert stale["catgirl_state"] not in messages[1]["content"]
        if (operation == "numeric_v2_fact_review" and body["audit_task"]["kind"] == "node"
                and body["checked_node_ids"] == [node_id]):
            calls.append(operation)
            projected = body["story_outline"]["mainline"][0]
            assert projected["character_state"]["catgirl"] == ""
            return json.dumps(_fact_review_payload(body, [{
                "category": "state", "severity": "major", "target_node_ids": [node_id],
                "problem": "需要复核开场与摘要。", "modification_plan": "核实手套状态。", "expected_result": "两处相容。",
                "preserve": [], "repair_scope": "text", "repair_targets": [{"node_id": node_id, "field": "/opening_scene"}],
                "evidence_refs": [projected["opening_scene"]["ref"], projected["summary"]["ref"]],
            }]))
        if operation == "numeric_v2_fact_evidence_review":
            calls.append(operation)
            assert body["story_outline"]["mainline"][0]["character_state"]["catgirl"] == ""
            assert body["proposed_issues"][0]["evidence"] == [
                {"path": "/mainline/0/opening_scene"}, {"path": "/mainline/0/summary"},
            ]
            return json.dumps(review_payload(body["proposed_issues"], "unsupported"))
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_evidence_review"]
    assert report["issues"][0]["repairable"] is False
    assert (story, authoring) == before


def test_node_fact_review_covers_all_nodes_and_reuses_original_evidence():
    from .test_numeric_v2_generation import _quality_reply
    story, authoring = _quality_story_context()
    authoring["key_props"][0]["extra"] = {"ref": "作者资料定位", "text": "这不是内部证据编号对象"}
    branch = deepcopy(story["nodes"][0])
    branch.update(id="state_check_branch", type="scene", route_gates=[])
    for index, goal in enumerate(branch["story_beat"]["goals"]):
        goal["id"] = f"state_check_branch_goal_{index:02d}"
    story["nodes"].append(branch)
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    full = assessor._assessment_context(story, _generation_setup(), authoring)
    marked, sources = build_fact_sources(full)
    captured = []
    original = _quality_reply(_quality_payload())

    def capture(messages, **kwargs):
        if kwargs["operation"] == "numeric_v2_fact_review":
            request = prompt_payload(messages[1]["content"])
            focus, = request["checked_node_ids"]
            task = request["audit_task"]
            captured.append(task)
            expected, dependencies = fact_task_context(full, marked, task)
            assert request["story_outline"] == expected
            assert request["dependency_originals"] == dependencies
            assert request["terminal_node_ids"] == [node["id"] for node in full["endings"]]
            node = next(node for node in [*request["story_outline"]["mainline"], *request["story_outline"]["endings"],
                *(node for row in request["story_outline"]["branches"] for node in row["nodes"])] if node["id"] == focus)
            assert sources[node["opening_scene"]["ref"]]["quote"] == node["opening_scene"]["text"]
        return original(messages, **kwargs)

    assessor.call_llm = capture
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert captured == fact_review_tasks(full)
    assert len({task["id"] for task in captured}) == len(captured)
    assert {task["node_id"] for task in captured if task["kind"] == "node"} == {node["id"] for node in story["nodes"]}
    assert report["fact_check"]["checked_tasks"] == [task["id"] for task in captured]
    assert set(report["fact_check"]["checked_node_ids"]) == {node["id"] for node in story["nodes"]}
    assert story == before


def test_node_fact_context_retains_all_direct_neighbors_without_expanding_to_distant_nodes():
    context = {"mainline": [
        {"id": "a", "outgoing_routes": [{"target_node_id": "b"}, {"target_node_id": "x"}]},
        {"id": "b", "outgoing_routes": [{"target_node_id": "end"}]},
        {"id": "unrelated", "outgoing_routes": []}],
        "branches": [{"entry_from": "a", "nodes": [
            {"id": "x", "outgoing_routes": [{"target_node_id": "b"}, {"target_node_id": "y"}]},
            {"id": "y", "outgoing_routes": [{"target_node_id": "end"}]}]}],
        "endings": [{"id": "end", "outgoing_routes": []}], "background": "原始背景"}
    before = deepcopy(context)
    result = node_fact_context(context, "x")
    assert [node["id"] for node in result["mainline"]] == ["a", "b"]
    assert result["branches"] == context["branches"] and result["endings"] == []
    result["branches"][0]["nodes"][0]["outgoing_routes"].clear()
    assert context == before



def test_literary_prompt_does_not_repeat_generation_fact_review_tasks():
    # Drafting entry points retain the complete generation contract; literary stages retain only review duties and user-approved relational-evidence rules.
    from services.theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT
    from services.theater_workshop.sdk.generation.numeric_v2 import _SCENE_PROCESS_AUTHORING_RULE
    assert _SCENE_PROCESS_AUTHORING_RULE not in _ASSESSMENT_PROMPT
    assert "先写issues和fact_issue_ids，再写summary和score" in _ASSESSMENT_PROMPT
    # After user approval of shared literary/relational evidence, low scores may cite it explicitly, but still cannot approve without grounds.
    assert "issues、fact_issue_ids和related_issue_ids不能同时为空" in _ASSESSMENT_PROMPT
    assert "不强加赞美、升温、额外确认或抽象难度" in _ASSESSMENT_PROMPT


def _citation_fixture():
    context = {"node": {"opening": "女主已放下道具。", "rules": ["女主不得代玩家决定。"]}}
    payload = {"checked_node_ids": ["node"], "issues": [{
        "category": "state", "severity": "major", "target_node_ids": ["node"],
        "problem": "示例冲突", "modification_plan": "修正描述", "expected_result": "状态一致",
        "preserve": [], "repair_scope": "text", "evidence": [
            {"path": "/node/opening", "quote": "已放下道具"},
            {"path": "/node/rules", "quote": "不得代玩家决定"},
        ],
    }]}
    return context, payload


def test_fact_citation_normalizes_only_unique_array_match_and_preserves_input():
    context, payload = _citation_fixture()
    before = deepcopy(payload)
    report = validate_fact_review(context, payload, {"node"})
    assert report["issues"][0]["evidence"][1] == {
        "path": "/node/rules/0", "quote": "不得代玩家决定", "original_path": "/node/rules",
    }
    assert payload == before


@pytest.mark.parametrize("case", ["quote", "path", "ambiguous", "duplicate", "coverage", "target", "evidence", "scope", "index", "serialized_array"])
def test_fact_report_rejects_unverifiable_or_incomplete_evidence(case):
    context, payload = _citation_fixture()
    issue = payload["issues"][0]
    if case == "quote": issue["evidence"][0]["quote"] = "仍持有道具"
    elif case == "path": issue["evidence"][0]["path"] = "/node/absent"
    elif case == "ambiguous": context["node"]["rules"] *= 2
    elif case == "duplicate": issue["evidence"][1] = deepcopy(issue["evidence"][0])
    elif case == "coverage": payload["checked_node_ids"] = []
    elif case == "target": issue["target_node_ids"] = ["unknown"]
    elif case == "evidence": issue["evidence"] = []
    elif case == "scope": issue["repair_scope"] = "maybe"
    elif case == "index": issue["evidence"][1]["path"] = "/node/rules/-1"
    # Real stress tests treated the entire array's JSON serialization as original text; never relax this to automatic quotation rewriting.
    elif case == "serialized_array": issue["evidence"][1]["quote"] = json.dumps(context["node"]["rules"], ensure_ascii=False)
    with pytest.raises(ValueError):
        validate_fact_review(context, payload, {"node"})


def test_fact_review_allows_complete_report_without_issues():
    context, _ = _citation_fixture()
    assert validate_fact_review(context, {"checked_node_ids": ["node"], "issues": []}, {"node"})["issues"] == []


@pytest.mark.parametrize("response,valid", [
    ('```json\n{"issues": []}\n```', True),
    ('```\n{"issues": []}\n```', True),
    ('解释\n```json\n{"issues": []}\n```', False),
    ('```json\n{"issues": []}\n```\n额外正文', False),
    ('```json\n{"issues": [{"problem": "未闭合}\n```', False),
    ('```json\n{}\n```\n```json\n{}\n```', False),
])
def test_quality_parser_accepts_only_one_complete_json_wrapper(response, valid):
    if valid:
        assert NumericV2QualityAssessor._parse_response(response, "invalid_fact_review") == {"issues": []}
    else:
        with pytest.raises(QualityAssessmentError, match="invalid_fact_review"):
            NumericV2QualityAssessor._parse_response(response, "invalid_fact_review")


def test_source_refs_restore_original_fields_including_array_and_escaped_keys():
    # Identical text in different fields has distinct IDs; do not merge evidence sources by text equality.
    context, payload = _citation_fixture()
    context["node"]["a/b~c"] = context["node"]["opening"]
    before = deepcopy(context)
    marked, sources = build_fact_sources(context)
    assert context == before
    assert marked["node"]["a/b~c"]["ref"] != marked["node"]["opening"]["ref"]
    issue = payload["issues"][0]
    del issue["evidence"]
    refs = [marked["node"]["a/b~c"]["ref"], marked["node"]["rules"][0]["ref"]]
    issue["evidence_refs"] = refs
    result = validate_fact_review(context, payload, {"node"}, evidence_sources=sources)
    assert result["issues"][0]["evidence"] == [sources[ref] for ref in refs]
    assert result["issues"][0]["evidence"][0]["path"] == "/node/a~1b~0c"


def test_fact_sources_keep_locators_without_treating_them_as_conflict_evidence():
    # Real runs substituted pure goal IDs for action descriptions as second evidence; keep location data but assign it no evidence IDs.
    story, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    before = deepcopy(context)
    marked, sources = build_fact_sources(context)
    node = marked["mainline"][0]
    assert node["id"] == context["mainline"][0]["id"]
    assert node["goals"][0]["id"] == context["mainline"][0]["goals"][0]["id"]
    assert node["text_repair_fields"] == context["mainline"][0]["text_repair_fields"]
    assert node["outgoing_routes"][0]["target_node_id"] == context["mainline"][0]["outgoing_routes"][0]["target_node_id"]
    # Permissions and action text remain citable; removing ID evidence must not remove inputs needed for factual review.
    for field in ("description", "owner", "output_field"):
        ref = node["goals"][0][field]["ref"]
        assert sources[ref]["quote"] == context["mainline"][0]["goals"][0][field]
    assert not any(item["path"].endswith("/id") or "/text_repair_fields/" in item["path"] for item in sources.values())
    assert context == before


@pytest.mark.parametrize("case", ["unknown", "duplicate", "missing", "mixed", "tampered"])
def test_source_refs_reject_unknown_duplicate_or_model_supplied_quotes(case):
    # Numbering avoids transcription errors but cannot bypass two-independent-field, quotation-authenticity, or full-report validation.
    context, payload = _citation_fixture()
    marked, sources = build_fact_sources(context)
    issue = payload["issues"][0]
    del issue["evidence"]
    issue["evidence_refs"] = [marked["node"]["opening"]["ref"], marked["node"]["rules"][0]["ref"]]
    if case == "unknown": issue["evidence_refs"][0] = "E99999"
    elif case == "duplicate": issue["evidence_refs"][1] = issue["evidence_refs"][0]
    elif case == "missing": issue["evidence_refs"] = []
    elif case == "mixed": issue["evidence"] = []
    elif case == "tampered": sources[issue["evidence_refs"][0]]["quote"] = "原文不存在的句子"
    with pytest.raises(ValueError):
        validate_fact_review(context, payload, {"node"}, evidence_sources=sources)


def test_confirmed_fact_repair_receives_evidence_and_preserves_other_nodes():
    # Once the user selects nodes, original evidence accompanies suggestions into revision; scoring alone never triggers revision.
    story, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    before = deepcopy(story)
    opening = story["nodes"][0]["story_beat"]["opening_scene"].strip()
    distant_opening = next(node["story_beat"]["opening_scene"].strip() for node in story["nodes"] if node["id"] == "mainline_04")
    evidence = [{"path": "/mainline/0/opening_scene", "quote": opening},
                {"path": "/mainline/0/opening_scene", "quote": "旧信"},
                {"path": "/mainline/3/opening_scene", "quote": distant_opening}]
    assessment = {"repair_plan_version": 2, "issues": [{
        "repair_scope": "text", "repair_targets": [{"node_id": "mainline_01", "field": "/character_state/catgirl"}],
        "source": "facts", "repairable": True, "target_node_ids": ["mainline_01"],
        "evidence": evidence, "dimension": "plot", "problem": "状态描述待修正",
        "modification_plan": "仅修正女主当前状态", "expected_result": "与开场相符", "preserve": [],
    }]}
    _reviewed_fixture(assessment, context)
    # Include explicit completed-field review; legacy facts lacking review cannot execute.
    assessment["issues"][0]["evidence_review"] = {"verdict": "supported", "reason": "状态确有冲突", "target_checks": [{"node_id": "mainline_01", "field": "/character_state/catgirl", "requires_change": True, "reason": "仅状态需要改"}]}
    calls = []
    assessor = NumericV2QualityAssessor()
    def reply(messages, **kwargs):
        calls.append(kwargs["operation"])
        request = prompt_payload(messages[1]["content"])
        suggestion = request["accepted_suggestions"][0]
        assert suggestion["source"] == "fact_check"
        assert suggestion["evidence"] == [{"path": evidence[0]["path"]}, *evidence[1:]]
        assert request["story_outline"]["mainline"][0]["opening_scene"] == opening
        assert request["story_outline"]["mainline"][3]["text_omitted"] is True
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {
            "character_state": {"catgirl_state": "女主双手空着，旧信已放在桌上。"},
        }}]}, ensure_ascii=False)
    assessor.call_llm = reply
    result = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                    assessment=assessment, node_id="mainline_01")
    assert calls == ["numeric_v2_quality_single_node_optimization"]
    expected = deepcopy(before)
    expected["nodes"][0]["story_beat"]["character_state"]["catgirl_state"] = "女主双手空着，旧信已放在桌上。"
    assert result == expected
    assert story == before
    assert assessment["issues"][0]["evidence"] == evidence


@pytest.mark.parametrize("category", ["state", "ownership"])
def test_literature_cannot_erase_fact_issues_or_override_them_with_high_scores(category):
    # Real two-stage orchestration: literary review receives the fact report; final merging preserves evidence and revision scope.
    story, authoring = _quality_story_context()
    beat = story["nodes"][0]["story_beat"]
    beat["opening_scene"] = "女主把旧信放在桌上。"
    beat["character_state"]["catgirl_state"] = "女主仍握着旧信。"
    if category == "ownership":
        story["intro"]["player_name"] = "林舟"
        beat["opening_scene"] = "林舟说：“先留下这封信吧。”"
        beat["character_state"]["player_state"] = "林舟站在桌边，正在提出留下信的建议。"
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    calls = []
    _, fact = _citation_fixture()
    issue = fact["issues"][0]
    issue.update(target_node_ids=["mainline_01"], evidence=[
        {"path": "/mainline/0/opening_scene", "quote": "女主把旧信放在桌上。"},
        {"path": "/mainline/0/character_state/catgirl", "quote": "女主仍握着旧信。"},
    ])
    if category == "ownership":
        # Identifying the player and citing their new automatic dialogue is
        # sufficient evidence; compatible state text does not authorize speech.
        issue.update(category=category, problem="开场预定玩家提出留下信的新台词。",
                     modification_plan="删除玩家台词，将是否留下旧信交给玩家输入。",
                     evidence=[
                         {"path": "/mainline/0/opening_scene", "quote": beat["opening_scene"]},
                         {"path": "/characters/player_name", "quote": "林舟"},
                     ])
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    fact["checked_node_ids"] = sorted(assessor._context_node_ids(context))
    _, sources = build_fact_sources(context)
    wire_fact = deepcopy(fact)
    wire_fact["issues"][0]["evidence_refs"] = [
        ref for citation in issue["evidence"] for ref, source in sources.items() if source == citation
    ]
    del wire_fact["issues"][0]["evidence"]
    def reply(messages, **kwargs):
        calls.append(kwargs["operation"])
        if kwargs["operation"] == "numeric_v2_fact_review":
            request = prompt_payload(messages[1]["content"])
            return json.dumps(_fact_review_payload(request,
                wire_fact["issues"] if request["audit_task"]["kind"] == "node"
                and request["checked_node_ids"] == ["mainline_01"] else []), ensure_ascii=False)
        received = prompt_payload(messages[1]["content"])
        if kwargs["operation"] == "numeric_v2_fact_evidence_review":
            from .test_numeric_v2_evidence import review_payload
            return json.dumps(review_payload(received["proposed_issues"]))
        summary = received["fact_review"]["issues"][0]
        assert summary["problem"] == issue["problem"] and summary["severity"] == issue["severity"]
        assert "evidence" not in summary and "modification_plan" not in summary
        return _quality_reply(_quality_payload())(messages, **kwargs)
    assessor.call_llm = reply
    result = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    task_count = len(fact_review_tasks(context))
    block_count = len(result["literary_coverage"]["blocks"])
    assert calls == [*["numeric_v2_fact_review"] * task_count, "numeric_v2_fact_evidence_review",
                     *["numeric_v2_literary_block"] * block_count, "numeric_v2_quality_assessment"]
    assert result["overall_score"] == 82
    assert result["passed"] is False
    assert result["fact_check"]["status"] == "complete"
    assert result["issues"][0]["source"] == "facts"
    assert result["issues"][0]["evidence"] == issue["evidence"]
    assert story == before


def test_low_scores_can_share_fact_evidence_without_duplicate_repair_plans():
    # Real literary parsing: two low scores cite one fact, while independent high-score-dimension issues remain intact.
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension="prose_style", target_node_id="mainline_01")
    payload["scores"]["prose_style"]["score"] = 90
    for dimension in ["plot", "characterization"]:
        payload["scores"][dimension].update(score=60, fact_issue_ids=["fact_issue_01"])
    wire = _quality_wire_payload(payload)
    before = deepcopy(wire)
    def reply(messages, **options):
        if options["operation"] == "numeric_v2_literary_block":
            return json.dumps(_literary_block_payload(prompt_payload(messages[1]["content"])))
        return json.dumps(wire, ensure_ascii=False)
    assessor.call_llm = reply
    report = assessor._assess_literature(context, {"issues": [{"issue_id": "fact_issue_01"}]})
    assert report["scores"]["plot"]["score"] == 60
    assert report["scores"]["plot"]["fact_issue_ids"] == ["fact_issue_01"]
    assert len(report["issues"]) == 1
    assert report["issues"][0]["dimension"] == "prose_style"
    assert report["passed"] is False
    assert set(report["failed_dimensions"]) == {"plot", "characterization"}
    assert wire == before


@pytest.mark.parametrize("case", ["unknown_fact", "duplicate_fact", "missing_basis", "missing_issues", "extra_dimension", "mixed_issues"])
def test_grouped_literature_rejects_incomplete_or_fabricated_basis(case):
    # Orchestration changes must not pass reports by dropping issues, guessing dimensions, or inventing factual links.
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    wire = _quality_wire_payload(_quality_payload())
    row = wire["scores"]["plot"]
    if case == "unknown_fact": row["fact_issue_ids"] = ["fact_issue_99"]
    elif case == "duplicate_fact": row["fact_issue_ids"] = ["fact_issue_01"] * 2
    elif case == "missing_basis": row["score"] = 60
    elif case == "missing_issues": del row["issues"]
    elif case == "extra_dimension": wire["scores"]["metrics"] = deepcopy(row)
    elif case == "mixed_issues": wire["issues"] = []
    def reply(messages, **options):
        if options["operation"] == "numeric_v2_literary_block":
            return json.dumps(_literary_block_payload(prompt_payload(messages[1]["content"])))
        return json.dumps(wire, ensure_ascii=False)
    assessor.call_llm = reply
    with pytest.raises(QualityAssessmentError):
        assessor._assess_literature(context, {"issues": [{"issue_id": "fact_issue_01"}]})


@pytest.mark.parametrize("phase", ["facts", "facts_second", "evidence", "literature"])
def test_failed_stage_stops_assessment_and_preserves_story(phase):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    calls = []
    tasks = fact_review_tasks(assessor._assessment_context(story, _generation_setup(), authoring))
    failed_task_index = next(index for index, task in enumerate(tasks) if task["id"] == "node:mainline_02")
    def reply(messages, **kwargs):
        calls.append(kwargs["operation"])
        if phase == "evidence" and kwargs["operation"] == "numeric_v2_fact_review":
            from .test_numeric_v2_evidence import wire_fact
            request = prompt_payload(messages[1]["content"])
            return json.dumps(wire_fact(request) if request["audit_task"]["kind"] == "node"
                              and request["checked_node_ids"] == ["mainline_01"]
                              else _fact_review_payload(request))
        if kwargs["operation"] == "numeric_v2_fact_review":
            request = prompt_payload(messages[1]["content"])
            if phase == "literature" or phase == "facts_second" and request["audit_task"]["id"] != "node:mainline_02":
                return json.dumps(_fact_review_payload(request))
        return "{}"
    assessor.call_llm = reply
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert caught.value.phase == ("facts" if phase == "facts_second" else phase)
    assert len(calls) == (2 if phase == "facts" else failed_task_index + 2 if phase == "facts_second" else len(tasks) + 2)
    if phase == "facts_second":
        assert caught.value.provider_details["node_id"] == "mainline_02"
    assert story == before


@pytest.mark.parametrize("technical", [False, True])
@pytest.mark.parametrize("invalid_json", [False, True])
def test_only_failed_node_gets_one_format_correction_and_technical_errors_do_not_retry(technical, invalid_json):
    from services.theater_workshop.sdk.model import LLMCallFailure
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    before = deepcopy(story)
    events = []
    tasks = fact_review_tasks(NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring))
    failed_task_index = next(index for index, task in enumerate(tasks) if task["id"] == "node:mainline_02")
    original = _quality_reply(_quality_payload())

    def reply(messages, **options):
        operation = options["operation"]
        request = prompt_payload(messages[1]["content"])
        checked = request.get("checked_node_ids")
        task_id = (request.get("audit_task") or {}).get("id")
        events.append((operation, checked, task_id))
        if operation == "numeric_v2_fact_review" and task_id == "node:mainline_02":
            return LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError") if technical else '{"checked_node_ids": ["mainline_02"], "issues": "未转义"台词""}' + "补充错误答复。" * 700 if invalid_json else "{}"
        if operation == "numeric_v2_fact_review_format_repair":
            assert checked == ["mainline_02"] and [row["role"] for row in messages] == ["system", "user", "user"]
            assert ("line 1 column" if invalid_json else "incomplete_fact_review") in messages[-1]["content"]
            assert all("省略中段" not in row["content"] for row in messages)
            assert options["max_retries"] == 1
            return json.dumps(_fact_review_payload(request))
        return original(messages, **options)

    assessor = NumericV2QualityAssessor(reply)
    if technical:
        with pytest.raises(QualityAssessmentError, match="model_timeout") as caught:
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert caught.value.provider_details["node_id"] == "mainline_02"
        assert len(events) == failed_task_index + 1
    else:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert len(report["fact_check"]["checked_node_ids"]) == len(story["nodes"])
        assert events.count(("numeric_v2_fact_review", ["mainline_01"], "node:mainline_01")) == 1
        assert events.count(("numeric_v2_fact_review_format_repair", ["mainline_02"], "node:mainline_02")) == 1
    assert story == before
