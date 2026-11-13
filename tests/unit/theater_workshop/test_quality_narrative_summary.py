"""Assess the plot direction used by Runtime without granting new edit permissions."""

from tests.utils.theater_prompt import prompt_payload

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError, _content_hash

from .test_numeric_v2_generation import _plan_review_reply, _quality_payload, _quality_story_context, _quality_wire_payload, _fact_review_payload, _quality_reply


DIRECTION = "  开场信件由女主持有。\n她先核对封口，再等玩家决定是否交给柜员。  "


@pytest.mark.parametrize("change_heading", [False, True])
def test_ending_optimization_respects_single_summary_permission(change_heading):
    from services.theater.numeric_v2 import NumericV2Compiler
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    ending_node = next(node for node in story["nodes"] if node["type"] == "ending")
    node_id = ending_node["id"]
    original = ending_node["story_beat"]["summary"]
    heading = "旧剧本的幕标题"
    ending_node["chapter"] = heading
    title = story["endings"][0]["title"]
    assert heading != title
    assert NumericV2Compiler().compile_v2_2(story).story == story
    reply = _quality_reply(_quality_payload(weak_dimension="plot", target_node_id=node_id))
    replacement = "更清晰地概括已经发生的结局。"

    def model(messages, **options):
        if options["operation"] != "numeric_v2_quality_single_node_optimization":
            return reply(messages, **options)
        request = prompt_payload(messages[1]["content"])
        assert request["accepted_suggestions"][0]["repair_targets"] == [{"node_id": node_id, "field": "/summary"}]
        assert "只获准一处时保留另一处" in messages[0]["content"]
        assert "summary" not in request["target_node"]["story_beat"]
        assert request["target_node"]["chapter"] == heading
        assert request["story_outline"]["endings"][0]["scene_summary"] == original
        patch = {"node_id": node_id, "ending": {"summary": replacement, "title": title}}
        if change_heading:
            patch["chapter"] = title
        return json.dumps({"node_updates": [patch]})

    assessor = NumericV2QualityAssessor(model)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    params = dict(story=story, setup={}, authoring=authoring, assessment=report, node_id=node_id)
    if change_heading:
        with pytest.raises(QualityAssessmentError, match="quality_repair_outside_plan"):
            assessor.optimize_node(**params)
        return
    revised = assessor.optimize_node(**params)
    revised_node = next(node for node in revised["nodes"] if node["id"] == node_id)
    assert revised["endings"][0]["summary"] == replacement
    assert revised["endings"][0]["title"] == title
    assert revised_node["story_beat"]["summary"] == original
    assert revised_node["chapter"] == heading
    assert NumericV2Compiler().compile_v2_2(revised).story == revised


def test_ending_title_change_can_still_update_both_titles_when_approved():
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    ending_node = next(node for node in story["nodes"] if node["type"] == "ending")
    node_id = ending_node["id"]
    ending_node["chapter"] = "旧剧本的幕标题"
    issue = _quality_payload(weak_dimension="plot", target_node_id=node_id)
    issue["issues"][0]["repair_targets"][0]["field"] = "/title"
    fallback = _quality_reply(issue)
    title = "归途"

    def model(messages, **options):
        if options["operation"] == "numeric_v2_quality_single_node_optimization":
            return json.dumps({"node_updates": [{"node_id": node_id, "ending": {"title": title}}]})
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor(model)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    revised = assessor.optimize_node(story=story, setup={}, authoring=authoring, assessment=report, node_id=node_id)
    assert next(node for node in revised["nodes"] if node["id"] == node_id)["chapter"] == title
    assert revised["endings"][0]["title"] == title


def test_state_optimization_uses_once_sent_prose_and_keeps_unapproved_situation():
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    first = story["nodes"][0]
    node_id = first["id"]
    original = "女主将钥匙放进口袋。"
    replacement = "女主把钥匙留在桌上。"
    first["story_beat"]["character_state"]["catgirl_state"] = original
    first["story_beat"]["catgirl_situation"] = original
    route = first["route_gates"][0]
    proposal = _quality_payload(weak_dimension="plot", target_node_id=node_id)
    fields = [{"node_id": node_id, "field": "/character_state/catgirl"}]
    proposal["issues"][0]["repair_targets"] = fields
    reply = _quality_reply(proposal)

    def model(messages, **options):
        if options["operation"] != "numeric_v2_quality_single_node_optimization":
            return reply(messages, **options)
        request = prompt_payload(messages[1]["content"])
        assert request["accepted_suggestions"][0]["repair_targets"] == fields
        assert "仅在 /relationship_state 也获准时同步修处境" in messages[0]["content"]
        target = request["target_node"]
        projected = request["story_outline"]["mainline"][0]
        assert "catgirl_state" not in target["story_beat"]["character_state"]
        assert projected["character_state"]["catgirl"] == original
        assert "bridge_scene_narration" not in target["route_gates"][0]["transition_contract"]
        assert projected["outgoing_routes"][0]["transition_contract"]["bridge_scene_narration"] == (
            route["transition_contract"]["bridge_scene_narration"])
        return json.dumps({"node_updates": [{"node_id": node_id, "story_beat": {
            "character_state": {"catgirl_state": replacement}}}]})

    assessor = NumericV2QualityAssessor(model)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    revised = assessor.optimize_node(story=story, setup={}, authoring=authoring, assessment=report, node_id=node_id)
    assert revised["nodes"][0]["story_beat"]["character_state"]["catgirl_state"] == replacement
    assert revised["nodes"][0]["story_beat"]["catgirl_situation"] == original


@pytest.mark.parametrize("extra_goal_field", [False, True])
def test_optimizer_reuses_goal_text_and_keeps_distinct_raw_fields(extra_goal_field):
    from services.theater.numeric_v2 import NumericV2Compiler
    from .test_numeric_v2_generation import _quality_reply

    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    node_id = node["id"]
    goals = node["story_beat"]["goals"]
    goals.append({**deepcopy(goals[0]), "id": "other_goal", "description": "女主指出旧信落款的日期。"})
    if extra_goal_field:
        goals[-1]["author_annotation"] = "这条说明没有重复发送。"
    before = deepcopy(story)
    assert NumericV2Compiler().compile_v2_2(story).story == story
    fallback = _quality_reply(_quality_payload(weak_dimension="plot", target_node_id=node_id))

    def model(messages, **options):
        if options["operation"] != "numeric_v2_quality_single_node_optimization":
            return fallback(messages, **options)
        body = prompt_payload(messages[1]["content"])
        projected = body["story_outline"]["mainline"][0]["goals"]
        assert [goal["description"] for goal in projected] == [goal["description"] for goal in goals]
        assert [goal["delivery_type"] for goal in projected] == [goal["delivery"]["type"] for goal in goals]
        beat = body["target_node"]["story_beat"]
        if extra_goal_field:
            assert beat["goals"] == [{}] * (len(goals) - 1) + [{"author_annotation": goals[-1]["author_annotation"]}]
        else:
            assert "goals" not in beat
        return json.dumps({"node_updates": [{"node_id": node_id, "story_beat": {"summary": "只修改概要。"}}]})

    assessor = NumericV2QualityAssessor(model)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    revised = assessor.optimize_node(story=story, setup={}, authoring=authoring, assessment=report, node_id=node_id)
    assert revised["nodes"][0]["story_beat"]["goals"] == goals
    assert story == before


def test_real_assessment_and_revision_preserve_direction_evidence_and_permissions():
    story, authoring = _quality_story_context()
    beat = story["nodes"][0]["story_beat"]
    beat.update(narrative_summary=DIRECTION, opening_scene="开场信件仍由玩家保管。")
    before = deepcopy(story)
    calls = []
    fallback = _quality_reply(_quality_payload())

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        body = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_literary_block":
            payload = json.loads(fallback(messages, **options))
            for note in payload["node_notes"]:
                if note["node_id"] == "mainline_01":
                    note["evidence_refs"] = [row["ref"] for row in body["original_sources"]
                                             if row["path"] == "/mainline/0/narrative_summary"]
            return json.dumps(payload)
        if operation == "numeric_v2_fact_review":
            if body["checked_node_ids"] != ["mainline_01"] or body["audit_task"]["kind"] != "node":
                return json.dumps(_fact_review_payload(body))
            node = body["story_outline"]["mainline"][0]
            assert node["narrative_summary"]["text"] == DIRECTION
            return json.dumps(_fact_review_payload(body, [{
                "category": "ownership", "severity": "major", "target_node_ids": [node["id"]],
                "problem": "两处开场写了互斥的信件持有人。",
                "modification_plan": "将详细方向中的开场持有人改回玩家。",
                "expected_result": "开场信件持有人一致。", "preserve": ["保留正确开场"],
                # A model-supplied text label must not grant permission to edit this read-only field.
                "repair_scope": "text", "repair_targets": [{"node_id": node["id"], "field": "/narrative_summary"}],
                "evidence_refs": [node["narrative_summary"]["ref"], node["opening_scene"]["ref"]],
            }]))
        if operation == "numeric_v2_fact_evidence_review":
            assert body["story_outline"]["mainline"][0]["narrative_summary"] == DIRECTION
            issue = body["proposed_issues"][0]
            assert issue["evidence"] == [{"path": "/mainline/0/narrative_summary"}, {"path": "/mainline/0/opening_scene"}]
            assert body["story_outline"]["mainline"][1]["id"] == "mainline_02"
            far_node = body["story_outline"]["mainline"][3]
            assert far_node["id"] == "mainline_04" and far_node["text_omitted"] is True
            assert "opening_scene" not in far_node
            return json.dumps({"checks": [{
                "issue_id": issue["issue_id"], "verdict": "supported", "reason": "同一开场持有人冲突。",
                "target_checks": [{**issue["repair_targets"][0], "requires_change": True, "reason": "详细方向有误。"}],
            }]})
        if operation == "numeric_v2_quality_assessment":
            from services.theater_workshop.sdk.generation.facts import _resolve_pointer
            assert any((row.get("quote") or _resolve_pointer(body,row["text_path"])) == DIRECTION
                       for row in body["original_sources"])
            fact = body["fact_review"]["issues"][0]
            assert fact["problem"] == "两处开场写了互斥的信件持有人。"
            assert "evidence" not in fact and "repair_scope" not in fact
            return json.dumps(_quality_wire_payload(_quality_payload(
                weak_dimension="prose_style", target_node_id="mainline_01",
            )))
        if operation == "numeric_v2_repair_plan_review":
            assert all(target["field"] != "/narrative_summary"
                       for plan in body["plans"] for target in plan["repair_targets"])
            return _plan_review_reply(messages)
        assert operation == "numeric_v2_quality_single_node_optimization"
        assert "narrative_summary" not in body["target_node"]["story_beat"]
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {"summary": "修订后的简要概览。"}}]})

    assessor = NumericV2QualityAssessor(reply)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    context = assessor._assessment_context(story, {}, authoring)
    assert report["content_sha256"] == _content_hash(context)
    fact = next(issue for issue in report["issues"] if issue["source"] == "facts")
    assert fact["repairable"] is False and fact["repair_scope"] == "structure"
    assert fact["evidence"][0]["quote"] == DIRECTION
    assert report["passed"] is False
    repaired = assessor.optimize_node(story=story, setup={}, authoring=authoring,
                                     assessment=report, node_id="mainline_01")
    assert repaired["nodes"][0]["story_beat"]["narrative_summary"] == DIRECTION
    expected = deepcopy(before)
    expected["nodes"][0]["story_beat"]["summary"] = "修订后的简要概览。"
    assert repaired == expected and story == before
    expected_calls = len(story["nodes"]) + 2 * sum(len(node.get("route_gates", [])) for node in story["nodes"]) + 4
    assert len(calls) == expected_calls
    readonly_report = {**report, "issues": [fact], "relationship_advice": []}
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        assessor.optimize_node(story=story, setup={}, authoring=authoring,
                               assessment=readonly_report, node_id="mainline_01")
    assert len(calls) == expected_calls
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        assessor._apply_node_updates(story, {"node_updates": [{
            "node_id": "mainline_01", "story_beat": {"narrative_summary": "未经授权的替换。"},
        }]}, ["mainline_01"])
