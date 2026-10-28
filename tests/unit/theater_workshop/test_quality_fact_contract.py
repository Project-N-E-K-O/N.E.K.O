"""Quality review keeps author fact evidence without widening edits or provider budgets."""
from copy import deepcopy
import json

import pytest

from services.theater_workshop.host import NekoWorkshopModel
from services.theater_workshop.sdk import WorkshopError
from services.theater_workshop.sdk.generation.facts import build_fact_sources, validate_fact_review
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from services.theater_workshop.sdk.generation.repair import classify_repair
from .test_numeric_v2_generation import _generation_setup, _quality_story_context


def _story_with_npc_result():
    story, authoring = _quality_story_context()
    key = "scene:mainline_01:station_passed"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "public",
        "description": "管事已经核验文书并公开准许通行；玩家请求和同伴佐证不算放行。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    return story, authoring, key


def test_completion_requirements_cover_branches_and_endings_without_gaining_edit_permission():
    story, authoring, _ = _story_with_npc_result()
    branch = deepcopy(story["nodes"][0])
    branch.update(id="branch", type="scene", route_gates=[])
    branch["completion_contract"] = {"all": [{"fixed_narration_id": "letter"}]}
    story["nodes"].append(branch)
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, {}, authoring)
    projected = [*context["mainline"], *context["endings"],
                 *(node for row in context["branches"] for node in row["nodes"])]
    source = {node["id"]: node for node in story["nodes"]}
    for node in projected:
        assert node["completion_contract"] == source[node["id"]].get("completion_contract", {})
    issue = {"target_node_ids": ["mainline_01"], "repair_scope": "text",
             "repair_targets": [{"node_id": "mainline_01", "field": "/completion_contract"}]}
    assert classify_repair(context, issue)["repairable"] is False
    context["mainline"][0]["completion_contract"]["all"].clear()
    context["fact_contract"]["facts"].clear()
    assert story == before


def test_fact_definition_quotes_resolve_to_original_contract_fields():
    story, authoring, key = _story_with_npc_result()
    context = NumericV2QualityAssessor._assessment_context(story, {}, authoring)
    marked, sources = build_fact_sources(context)
    definition = marked["fact_contract"]["facts"][key]
    assert definition["value_type"]["text"] == "bool"
    assert definition["visibility"]["text"] == "public"
    assert marked["mainline"][0]["completion_contract"]["all"][0]["equals"] is True
    node_ids = NumericV2QualityAssessor._context_node_ids(context)
    refs = [definition["description"]["ref"], marked["mainline"][0]["opening_scene"]["ref"]]
    payload = {"checked_node_ids": sorted(node_ids), "issues": [{
        "category": "sequence", "severity": "major", "target_node_ids": ["mainline_01"],
        "problem": "开场结果与完成事实主体不一致。", "modification_plan": "对齐公开交付主体。",
        "expected_result": "按当前完成定义交付。", "preserve": [],
        "repair_scope": "text", "repair_targets": [{"node_id": "mainline_01", "field": "/opening_scene"}],
        "evidence_refs": refs,
    }]}
    review = validate_fact_review(context, payload, node_ids, evidence_sources=sources)
    assert review["issues"][0]["evidence"] == [
        {"path": f"/fact_contract/facts/{key}/description",
         "quote": story["fact_contract"]["facts"][key]["description"]},
        {"path": "/mainline/0/opening_scene", "quote": story["nodes"][0]["story_beat"]["opening_scene"]},
    ]


def test_expanded_fact_contract_is_counted_before_quality_provider_call(monkeypatch):
    from utils import llm_client
    from utils.tokenize import count_tokens

    story, authoring, key = _story_with_npc_result()
    requests = []
    def capture(messages, **options):
        requests.append(deepcopy(messages))
        raise RuntimeError("captured_quality_input")
    with pytest.raises(RuntimeError, match="captured_quality_input"):
        NumericV2QualityAssessor(capture).assess(story=story, setup=_generation_setup(), authoring=authoring)
    budget = count_tokens(json.dumps(requests[0], ensure_ascii=False)) + 1
    story["fact_contract"]["facts"][key]["description"] += "必须在可见旁白中交付管事核验的实际结果。" * 200
    before = deepcopy(story)
    def unexpected_client(**options):
        pytest.fail("over-budget fact evidence must not reach a provider")
    monkeypatch.setattr(llm_client, "create_chat_llm", unexpected_client)
    model = NekoWorkshopModel({"model": "selected-model", "api_key": "test-key",
                              "base_url": "https://example.invalid/v1", "max_input_tokens": budget})
    with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
        NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert story == before
