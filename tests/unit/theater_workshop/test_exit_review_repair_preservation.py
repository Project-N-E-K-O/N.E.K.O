"""Formatting recovery preserves findings, rather than malformed row counts."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.exit_review import unproven_exit_candidates
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from services.theater_workshop.sdk.generation.repair import classify_repair
from .test_exit_fact_binding import _payload, _request


def _review_case():
    request, sources, context = _request()
    candidates = unproven_exit_candidates(request, _payload(request)["bindings"], sources)
    candidate, = candidates
    issue = {
        "category": "sequence", "severity": "major", "target_node_ids": ["source"],
        "problem": "双方同意试听不保证玩家已经陈述记忆。",
        "evidence_refs": [candidate["claim_refs"][0], candidate["definition_ref"]],
        "modification_plan": "收窄未保证的既成事实。", "expected_result": "只承接已经保证的结果。",
        "preserve": ["玩家仍可自行陈述记忆。"], "repair_scope": "structure",
        "repair_targets": [{"node_id": "source", "field": "/completion_contract/all"}],
    }
    check = {**{key: candidate[key] for key in ("route_id", "fact_key", "value")},
             "status": "gap", "reason": "同意试听不蕴含陈述记忆。",
             "evidence_refs": issue["evidence_refs"], "clear_basis": None}
    return context, request, candidates, sources, issue, check


def _run(first, second):
    context, request, candidates, sources, _, _ = _review_case()
    replies = iter([json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False)])
    assessor = NumericV2QualityAssessor(lambda *_args, **_kwargs: next(replies))
    result = assessor._review_exit_candidates(context, request, candidates, {"source", "next"}, sources)
    assert list(replies) == []
    return context, result


def _report(issues, check):
    return {"checked_node_ids": ["source"], "issues": issues, "candidate_checks": [check]}


def test_twenty_incomplete_duplicate_slots_can_be_corrected_to_one_finding():
    _, _, _, _, issue, check = _review_case()
    incomplete = {key: value for key, value in issue.items() if key not in {
        "modification_plan", "expected_result", "preserve", "repair_scope", "repair_targets"}}
    context, (raw, reviewed) = _run(_report([deepcopy(incomplete) for _ in range(20)], check),
                                   _report([issue], check))
    assert raw["issues"] == [issue] and len(reviewed) == 1
    # A recovered allegation does not turn its structural plan into a text edit.
    assert classify_repair(context, reviewed[0])["repairable"] is False


@pytest.mark.parametrize("change", ["delete", "replace", "lower_severity", "flip_gap"])
def test_correction_cannot_swallow_independent_valid_findings_or_reverse_a_gap(change):
    _, _, _, _, issue, check = _review_case()
    other = deepcopy(issue)
    other["problem"] = "后幕还把玩家未说过的话固定为既成陈述。"
    malformed = {"problem": "不完整占位"}
    second_issues, second_check = [deepcopy(issue), deepcopy(other)], deepcopy(check)
    if change == "delete":
        second_issues.pop()
    elif change == "replace":
        second_issues[1]["problem"] = "另一条新问题，数量相同仍不能替换原问题。"
    elif change == "lower_severity":
        second_issues[1]["severity"] = "minor"
    else:
        second_check.update(status="clear", clear_basis="source_opening")
        second_issues = []
    if change != "flip_gap":
        while len(second_issues) < 3:
            new = deepcopy(issue)
            new["problem"] = f"纠正时新增的问题{len(second_issues)}不能顶替原有效问题。"
            second_issues.append(new)
    with pytest.raises(ValueError, match="exit_format_repair_changed"):
        _run(_report([issue, other, malformed], check), _report(second_issues, second_check))


def test_corrected_plan_with_nonexistent_path_remains_non_executable():
    _, _, _, _, issue, check = _review_case()
    incomplete = {key: value for key, value in issue.items() if key != "preserve"}
    issue["repair_targets"][0]["field"] = "/outgoing_routes/0/completion_contract/all"
    context, (_, reviewed) = _run(_report([incomplete], check), _report([issue], check))
    reviewed[0]["evidence_review"] = {"verdict": "supported", "target_checks": [
        {**issue["repair_targets"][0], "requires_change": True}]}
    result = classify_repair(context, reviewed[0])
    assert result["repairable"] is False
    assert "未能在当前故事中核实" in result["repair_reason"]
    assert result["repair_targets"] == issue["repair_targets"]


def test_missing_claim_citation_can_be_added_without_rewriting_a_valid_finding():
    _, request, candidates, _, issue, check = _review_case()
    original = deepcopy(issue)
    original["evidence_refs"] = [candidates[0]["definition_ref"],
                                  request["routes"][0]["source_context"]["opening_scene"]["ref"]]
    corrected = deepcopy(original)
    corrected["evidence_refs"].append(candidates[0]["claim_refs"][0])
    _, (raw, reviewed) = _run(_report([original], check), _report([corrected], check))
    assert raw["issues"] == [corrected]
    assert reviewed[0]["problem"] == original["problem"]
    assert len(reviewed[0]["evidence"]) == 3


def test_citation_recovery_cannot_remove_previously_valid_evidence():
    _, request, _, _, issue, check = _review_case()
    issue["evidence_refs"].append(request["routes"][0]["source_context"]["opening_scene"]["ref"])
    corrected = deepcopy(issue)
    corrected["evidence_refs"].pop()
    with pytest.raises(ValueError, match="exit_format_repair_changed"):
        _run(_report([issue, {"problem": "未完成的占位"}], check), _report([corrected], check))


@pytest.mark.parametrize('location',['valid','unresolved','drop'])
def test_exit_bad_location_reselects_once_and_cannot_erase_the_finding(location):
    context,request,candidates,sources,issue,check=_review_case()
    issue['repair_targets'][0]['field']='/outgoing_routes/0/completion_contract/all'
    first=_report([deepcopy(issue)],check)
    calls=[]
    def reply(messages,**options):
        calls.append(options['operation'])
        if len(calls)==1:return json.dumps(first,ensure_ascii=False)
        detail=prompt_payload(messages[-1]['content'])
        assert 'invalid_fact_repair_target' in detail['validation_error']
        assert '/completion_contract' in detail['repair_target_catalog']['source']
        corrected=deepcopy(first)
        if location=='valid':corrected['issues'][0]['repair_targets'][0]['field']='/completion_contract'
        elif location=='drop':corrected['issues']=[]
        return json.dumps(corrected,ensure_ascii=False)
    assessor=NumericV2QualityAssessor(reply)
    if location=='drop':
        with pytest.raises(ValueError,match='exit_format_repair_changed_findings'):
            assessor._review_exit_candidates(context,request,candidates,{'source','next'},sources)
    else:
        raw,reviewed=assessor._review_exit_candidates(context,request,candidates,{'source','next'},sources)
        assert raw['issues'][0]['problem']==first['issues'][0]['problem']
        assert raw['issues'][0]['evidence_refs']==first['issues'][0]['evidence_refs']
        if location=='unresolved':
            reviewed[0]['evidence_review']={'verdict':'supported','target_checks':[
                {**issue['repair_targets'][0],'requires_change':True}]}
            classified=classify_repair(context,reviewed[0])
            assert classified['repairable'] is False
            assert classified['repair_targets']==issue['repair_targets']
    assert calls==['numeric_v2_exit_fact_review','numeric_v2_exit_fact_review_format_repair']


@pytest.mark.parametrize('change',['valid','delete_evidence','replace_problem','fake_ref','unrelated_ref','change_claim_set'])
def test_one_exit_correction_reports_location_and_claim_errors_and_only_adds_related_claim(change):
    context,request,candidates,sources,issue,check=_review_case()
    candidate=candidates[0]
    source_opening=request['routes'][0]['source_context']['opening_scene']['ref']
    target_opening=request['routes'][0]['target_entry']['opening_scene']['ref']
    issue['evidence_refs']=[candidate['definition_ref'],source_opening]
    issue['repair_targets'][0]['field']='/outgoing_routes/0/completion_contract/all'
    first=_report([deepcopy(issue)],check);calls=[]
    def reply(messages,**options):
        calls.append(options['operation'])
        if len(calls)==1:return json.dumps(first,ensure_ascii=False)
        detail=prompt_payload(messages[-1]['content'])
        assert 'invalid_fact_repair_target' in detail['validation_error']
        assert 'exit_gap_requires_issue_claim' in detail['validation_error']
        needed=detail['required_issue_claims'][0]
        assert needed['route_id']==candidate['route_id'] and needed['fact_key']==candidate['fact_key']
        assert needed['required_claim_refs']==candidate['claim_refs']
        assert needed['related_issue_indexes']==[0]
        corrected=deepcopy(first)
        fixed=corrected['issues'][0]
        fixed['repair_targets'][0]['field']='/completion_contract'
        fixed['evidence_refs'].append(candidate['claim_refs'][0])
        if change=='delete_evidence':fixed['evidence_refs'].remove(source_opening)
        elif change=='replace_problem':fixed['problem']='换成无关的问题。'
        elif change=='fake_ref':fixed['evidence_refs'][-1]='E999999'
        elif change=='unrelated_ref':fixed['evidence_refs'].append(target_opening)
        elif change=='change_claim_set':corrected['candidate_checks'][0]['claim_refs']=['E999999']
        return json.dumps(corrected,ensure_ascii=False)
    assessor=NumericV2QualityAssessor(reply)
    if change=='valid':
        raw,reviewed=assessor._review_exit_candidates(context,request,candidates,{'source','next'},sources)
        assert raw['issues'][0]['evidence_refs']==[*issue['evidence_refs'],candidate['claim_refs'][0]]
        assert reviewed[0]['problem']==issue['problem']
    else:
        with pytest.raises(ValueError):
            assessor._review_exit_candidates(context,request,candidates,{'source','next'},sources)
    assert calls==['numeric_v2_exit_fact_review','numeric_v2_exit_fact_review_format_repair']
