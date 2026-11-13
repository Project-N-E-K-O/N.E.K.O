"""Measure the native optimizer request and protect scoped author evidence."""

from tests.utils.theater_prompt import prompt_payload

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.host import NekoWorkshopModel
from services.theater_workshop.sdk import WorkshopError
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from services.theater_workshop.sdk.generation.facts import build_fact_sources, validate_fact_review
from services.theater_workshop.sdk.generation.evidence import validate_evidence_review
from services.theater_workshop.sdk.numeric_v2_project_store import _normalize_authoring
from utils import llm_client
from utils.tokenize import count_tokens

from .test_numeric_v2_evidence import review_payload
from .test_numeric_v2_generation import _generation_setup, _quality_payload, _fact_review_payload, _reviewed_fixture


@pytest.mark.parametrize("case", [1, 2, 3, 4, 5, 6, 7, "cited_entry", "uncited_entry", "late_state", "oversized"])
def test_neon_optimizer_scope_and_real_host_budget(monkeypatch, case):
    story_path = Path(__file__).resolve().parents[3] / "assets/theater/story_neon_rebuild_ea2e45dab7.json"
    story = json.loads(story_path.read_text(encoding="utf-8"))
    mainline = [f"mainline_{index:02d}" for index in range(1, 8)]
    entry = case in ("cited_entry", "uncited_entry")
    target = f"mainline_{(3 if entry else 7 if case in ('late_state', 'oversized') else case):02d}"
    node = next(row for row in story["nodes"] if row["id"] == target)
    if entry:
        source_node = next(row for row in story["nodes"] if row["id"] == "mainline_02")
        required_piece, = source_node["story_beat"]["fixed_narrations"]
        required_piece.update(text="档案 FX-170 已展示。锈红色通行卡放在北墙第三格。", required_before_exit=False,
                              after=["opening_register"])
        dependency = {**required_piece, "id": "opening_register", "after": [], "text": "登记员先翻开收件簿。"}
        optional_piece = {**dependency, "id": "optional_register", "text": "当日桌上的其他资料只记了室温。"}
        source_node["story_beat"]["fixed_narrations"] = [dependency, optional_piece, required_piece]
    if case == "oversized":
        pieces = node["story_beat"]["fixed_narrations"]
        pieces.append({"id": "extra_archive", "trigger": {"type": "entry"}, "after": [], "required_before_exit": False,
                       "text": "【档案】" + "监测终端上保留一行可读序列号，连续显示公开的供电与连接状态。" * 48})
        assert sum(count_tokens(piece["text"]) for piece in pieces) < 2_000
    NumericV2Compiler().compile_v2_2(story)
    setup = _generation_setup()
    authoring = _normalize_authoring({"mainline_node_ids": mainline}, story)
    before = deepcopy((story, authoring))
    payload = (_quality_payload() if case == "cited_entry"
               else _quality_payload(weak_dimension="plot", target_node_id=target))
    if case == "uncited_entry":
        payload["issues"][0].update(repair_targets=[{"node_id": target, "field": "/opening_scene"}],
                                    preserve=["保留前幕固定档案中的通行卡状态。"])
    if case == "late_state":
        payload["issues"][0]["repair_targets"] = [{"node_id": target, "field": "/character_state/catgirl"}]
    sent = []
    patched_field = "opening_scene" if entry else "summary"
    replacement = "街巷深处传来低沉的设备声。"
    if case == "late_state":
        patched_field = "character_state"
        replacement = {"catgirl_state": "女主在灯下复核已经公开的记录。"}

    class Client:
        def invoke(self, messages, **options):
            sent.append(messages)
            return SimpleNamespace(content=json.dumps({"node_updates": [{"node_id": target,
                "story_beat": {patched_field: replacement}}]}, ensure_ascii=False), response_metadata={})

        def close(self):
            pass

    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **kwargs: Client())
    host = NekoWorkshopModel({"model": "fixture", "base_url": "https://example.invalid/v1", "max_input_tokens": 10_000})

    def fake(messages, **options):
        operation = options["operation"]
        body = prompt_payload(messages[1]["content"])
        assert operation == "numeric_v2_quality_single_node_optimization"
        outline = body["story_outline"]
        selected = next(row for row in outline["mainline"] if row["id"] == target)
        assert selected.get("fixed_narrations") == node["story_beat"].get("fixed_narrations")
        assert selected["acting_contract"] == node["story_beat"]["acting_contract"]
        assert "chapter" not in body["target_node"] and "completion_contract" not in body["target_node"]
        assert selected["title"] == node["chapter"] and selected["completion_contract"] == node.get("completion_contract", {})
        for raw, route, original in zip(body["target_node"]["route_gates"], selected["outgoing_routes"], node["route_gates"], strict=True):
            assert "conditions" not in raw and "target_node_id" not in raw
            assert route["conditions"] == original["conditions"] and route["target_node_id"] == original["target_node_id"]
        if entry:
            source = outline["mainline"][1]
            assert source["edit_context_only"]
            assert source["fixed_narrations"] == [dependency, {"id": optional_piece["id"], "text_omitted": True}, required_piece]
            assert messages[1]["content"].count(required_piece["text"]) == 1
            edge = next(route for route in source["outgoing_routes"] if route["target_node_id"] == target)
            assert edge["transition_contract"]["accept_input"]
            assert "character_state" in source and "completion_contract" in source
            evidence = body["accepted_suggestions"][0]["evidence"]
            if case == "cited_entry":
                assert evidence == [{"path": "/mainline/2/opening_scene"},
                                    {"path": "/mainline/1/fixed_narrations/1/text", "quote": optional_piece["text"]}]
            else:
                assert evidence == [] and optional_piece["text"] not in messages[1]["content"]
        else:
            assert all("goals" not in row for row in outline["mainline"] if row["id"] != target)
        size = count_tokens(json.dumps(messages, ensure_ascii=False))
        assert (size > 10_000) == (case == "oversized")
        return host(messages, **options)

    assessor = NumericV2QualityAssessor(fake)
    # This tests optimizer packing, independently of semantic audit responses.
    # The legacy asset has an unresolved author-source ID; do not silently repair
    # that metadata or claim a full audit passed merely to reach the optimizer.
    context = assessor._assessment_context(story, setup, authoring)
    report = assessor._validated_assessment(context, payload)
    if case == "cited_entry":
        marked, sources = build_fact_sources(context)
        source, selected = marked["mainline"][1:3]
        request = {"checked_node_ids": [target], "contract_targets": []}
        review = validate_fact_review(context, _fact_review_payload(request, [{
            "category": "state", "severity": "major", "target_node_ids": [target], "preserve": [],
            "repair_scope": "text", "problem": "需要让开场承接前一幕的结果。", "modification_plan": "按原文修正开场。",
            "expected_result": "两幕相容。", "repair_targets": [{"node_id": target, "field": "/opening_scene"}],
            "evidence_refs": [selected["opening_scene"]["ref"], source["fixed_narrations"][1]["text"]["ref"]],
        }]), set(mainline), evidence_sources=sources, checked_node_ids={target}, expected_target_ids=[])
        report["issues"] = validate_evidence_review(review["issues"], review_payload(review["issues"]))
    _reviewed_fixture(report, context)
    params = dict(story=story, setup=setup, authoring=authoring, assessment=report, node_id=target)
    if case == "oversized":
        with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
            assessor.optimize_node(**params)
        assert sent == []
    else:
        revised = assessor.optimize_node(**params)
        updated_node = next(row for row in revised["nodes"] if row["id"] == target)
        changed = updated_node["story_beat"][patched_field]
        if case == "late_state":
            assert changed["catgirl_state"] == replacement["catgirl_state"]
        else:
            assert changed == replacement
        assert updated_node["route_gates"] == node["route_gates"]
        assert NumericV2Compiler().compile_v2_2(revised).story == revised
        assert len(sent) == 1
    assert (story, authoring) == before
