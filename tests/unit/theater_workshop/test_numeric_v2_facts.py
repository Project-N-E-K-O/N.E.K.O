"""事实证据及两阶段评分的保存前边界；不以伪造模型测试代替语义压测。"""
from copy import deepcopy
import json
import pytest
from theater_workshop.sdk.generation.facts import build_fact_sources, validate_fact_review


def test_fact_request_preserves_chronological_reading_order_and_all_original_fields():
    # 原请求按JSON字段名排序，把结局和后续目标混到开场前；真实入口必须保留时序阅读顺序。
    # 开场目标已交付，不能在精简检查指令时把它与普通回合待演目标混为一类。
    from theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
    assert "opening对应开场已交付，turn对应普通回合待演" in FACT_REVIEW_PROMPT
    from copy import deepcopy
    import json
    from .test_numeric_v2_generation import _quality_story_context, _generation_setup
    from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    captured = []
    def capture(messages, **kwargs):
        captured.append(json.loads(messages[1]["content"])["story_outline"])
        raise RuntimeError("inspection_stop")
    assessor.call_llm = capture
    with pytest.raises(RuntimeError, match="inspection_stop"):
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    outline = captured[0]
    keys = list(outline)
    assert keys.index("mainline") < keys.index("branches") < keys.index("endings")
    node_keys = list(outline["mainline"][0])
    assert node_keys.index("opening_scene") < node_keys.index("character_state") < node_keys.index("goals") < node_keys.index("outgoing_routes")
    # 重排不是删字段或改作者文本；还原所有编号后必须与原评分投影相等。
    def restore(value):
        if isinstance(value, dict):
            if set(value) == {"ref", "text"}: return value["text"]
            return {key: restore(item) for key, item in value.items()}
        if isinstance(value, list): return [restore(item) for item in value]
        return value
    assert restore(outline) == assessor._assessment_context(story, _generation_setup(), authoring)
    assert story == before
from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _quality_wire_payload, _reviewed_fixture


def test_fact_opening_comparisons_cover_branches_and_reuse_original_evidence():
    # 同一请求的对照视图不能遗漏支线、结局，或重写原文及证据路径来制造可修问题。
    story, authoring = _quality_story_context()
    branch = deepcopy(story["nodes"][0])
    branch.update(id="state_check_branch", type="scene", route_gates=[])
    story["nodes"].append(branch)
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    captured = []

    def capture(messages, **kwargs):
        captured.append(json.loads(messages[1]["content"]))
        raise RuntimeError("inspection_stop")

    assessor.call_llm = capture
    with pytest.raises(RuntimeError, match="inspection_stop"):
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    payload = captured[0]
    outline = payload["story_outline"]
    nodes = [*outline["mainline"], *(n for b in outline["branches"] for n in b["nodes"]), *outline["endings"]]
    comparisons = payload["opening_end_state_comparisons"]
    assert len(comparisons) == len(nodes)
    assert {row["node_id"] for row in comparisons} == {node["id"] for node in nodes}
    for row, node in zip(comparisons, nodes):
        assert row["opening_performance"] == node["opening_scene"]
        assert row["declared_picture_after_performance"] == node["character_state"]
        assert row["same_moment_context"] == node["relationship_state"]
    # 额外视图只存在于事实请求，不能变成剧本字段或污染原评分投影。
    assert "opening_end_state_comparisons" not in outline
    assert story == before
    assert len(captured) == 1


def test_literary_prompt_does_not_repeat_generation_fact_review_tasks():
    # 写稿入口仍使用完整生成合同，文学阶段只保留评审职责及用户确认的关联依据规则。
    from theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT
    from theater_workshop.sdk.generation.numeric_v2 import _SCENE_PROCESS_AUTHORING_RULE
    assert _SCENE_PROCESS_AUTHORING_RULE not in _ASSESSMENT_PROMPT
    assert "先写issues和fact_issue_ids，再写summary和score" in _ASSESSMENT_PROMPT
    # 用户批准共享文学/关系依据后，低分也可显式引用；仍不得无依据放行。
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
    # 真实压测曾把整个数组的JSON序列化结果当原文，不能放宽为自动改写引文。
    elif case == "serialized_array": issue["evidence"][1]["quote"] = json.dumps(context["node"]["rules"], ensure_ascii=False)
    with pytest.raises(ValueError):
        validate_fact_review(context, payload, {"node"})


def test_fact_review_allows_complete_report_without_issues():
    context, _ = _citation_fixture()
    assert validate_fact_review(context, {"checked_node_ids": ["node"], "issues": []}, {"node"})["issues"] == []


def test_source_refs_restore_original_fields_including_array_and_escaped_keys():
    # 同样的文字在不同字段拥有不同编号，不能凭文字相同合并证据来源。
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
    # 实测曾用纯goal ID代替行动描述作为第二份证据；定位数据保留，但不能获得证据编号。
    story, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    before = deepcopy(context)
    marked, sources = build_fact_sources(context)
    node = marked["mainline"][0]
    assert node["id"] == context["mainline"][0]["id"]
    assert node["goals"][0]["id"] == context["mainline"][0]["goals"][0]["id"]
    assert node["text_repair_fields"] == context["mainline"][0]["text_repair_fields"]
    assert node["outgoing_routes"][0]["target_node_id"] == context["mainline"][0]["outgoing_routes"][0]["target_node_id"]
    # 权限与行动正文仍可引用；不能为了去掉ID证据而丢失事实检查所需的输入。
    for field in ("description", "owner", "output_field"):
        ref = node["goals"][0][field]["ref"]
        assert sources[ref]["quote"] == context["mainline"][0]["goals"][0][field]
    assert not any(item["path"].endswith("/id") or "/text_repair_fields/" in item["path"] for item in sources.values())
    assert context == before


@pytest.mark.parametrize("case", ["unknown", "duplicate", "missing", "mixed", "tampered"])
def test_source_refs_reject_unknown_duplicate_or_model_supplied_quotes(case):
    # 编号避免转录错误，但不能绕过两个独立字段、原文真实性和完整报告校验。
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
    # 用户选择节点后，原文证据必须随建议进入修订；评分本身不会触发该步骤。
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    evidence = [{"path": "/mainline/0/opening_scene", "quote": "旧信"}]
    assessment = {"repair_plan_version": 2, "issues": [{
        "repair_scope": "text", "repair_targets": [{"node_id": "mainline_01", "field": "/character_state/catgirl"}],
        "source": "facts", "repairable": True, "target_node_ids": ["mainline_01"],
        "evidence": evidence, "dimension": "plot", "problem": "状态描述待修正",
        "modification_plan": "仅修正女主当前状态", "expected_result": "与开场相符", "preserve": [],
    }]}
    _reviewed_fixture(assessment)
    # 显式加入已完成的字段复核，旧无复核事实不能执行。
    assessment["issues"][0]["evidence_review"] = {"verdict": "supported", "reason": "状态确有冲突", "target_checks": [{"node_id": "mainline_01", "field": "/character_state/catgirl", "requires_change": True, "reason": "仅状态需要改"}]}
    calls = []
    assessor = NumericV2QualityAssessor()
    def reply(messages, **kwargs):
        calls.append(kwargs["operation"])
        suggestion = json.loads(messages[1]["content"])["accepted_suggestions"][0]
        assert suggestion["source"] == "fact_check"
        assert suggestion["evidence"] == evidence
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


def test_literature_cannot_erase_fact_issues_or_override_them_with_high_scores():
    # 真实两阶段编排：文学阶段收到事实报告，最终合并保持证据与修订范围。
    story, authoring = _quality_story_context()
    beat = story["nodes"][0]["story_beat"]
    beat["opening_scene"] = "女主把旧信放在桌上。"
    beat["character_state"]["catgirl_state"] = "女主仍握着旧信。"
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    calls = []
    _, fact = _citation_fixture()
    issue = fact["issues"][0]
    issue.update(target_node_ids=["mainline_01"], evidence=[
        {"path": "/mainline/0/opening_scene", "quote": "女主把旧信放在桌上。"},
        {"path": "/mainline/0/character_state/catgirl", "quote": "女主仍握着旧信。"},
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
        if len(calls) == 1:
            return json.dumps(wire_fact, ensure_ascii=False)
        received = json.loads(messages[1]["content"])
        if kwargs["operation"] == "numeric_v2_fact_evidence_review":
            from .test_numeric_v2_evidence import review_payload
            return json.dumps(review_payload(received["proposed_issues"]))
        assert received["fact_review"]["issues"][0]["evidence"] == issue["evidence"]
        return json.dumps(_quality_wire_payload(_quality_payload()), ensure_ascii=False)
    assessor.call_llm = reply
    result = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_evidence_review", "numeric_v2_quality_assessment"]
    assert result["overall_score"] == 82
    assert result["passed"] is False
    assert result["fact_check"]["status"] == "complete"
    assert result["issues"][0]["source"] == "facts"
    assert result["issues"][0]["evidence"] == issue["evidence"]
    assert story == before


def test_low_scores_can_share_fact_evidence_without_duplicate_repair_plans():
    # 真实文学解析链路：两个低分引用同一事实，高分维度的独立问题仍完整保留。
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension="prose_style", target_node_id="mainline_01")
    payload["scores"]["prose_style"]["score"] = 90
    for dimension in ["plot", "characterization"]:
        payload["scores"][dimension].update(score=60, fact_issue_ids=["fact_issue_01"])
    wire = _quality_wire_payload(payload)
    before = deepcopy(wire)
    assessor.call_llm = lambda *args, **kwargs: json.dumps(wire, ensure_ascii=False)
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
    # 编排变化不能通过丢弃问题、猜维度或伪造事实关联来让报告通过。
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
    assessor.call_llm = lambda *args, **kwargs: json.dumps(wire, ensure_ascii=False)
    with pytest.raises(QualityAssessmentError):
        assessor._assess_literature(context, {"issues": [{"issue_id": "fact_issue_01"}]})


@pytest.mark.parametrize("phase", ["facts", "literature"])
def test_failed_stage_stops_assessment_and_preserves_story(phase):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    calls = []
    def reply(messages, **kwargs):
        calls.append(kwargs["operation"])
        if phase == "literature" and len(calls) == 1:
            context = json.loads(messages[1]["content"])
            return json.dumps({"checked_node_ids": context["checked_node_ids"], "issues": []})
        return "{}"
    assessor.call_llm = reply
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert caught.value.phase == phase
    assert len(calls) == (1 if phase == "facts" else 2)
    assert story == before
