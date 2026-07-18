"""验证 LLM 拆分后仍保留模型编排主干和既有测试挂点。"""  # noqa: DOCSTRING_CJK

import ast
import inspect

import pytest

from services.theater import (
    llm,
    llm_context,
    llm_fallbacks,
    llm_performance_guard,
    llm_response_contracts,
)


def _relative_imports(module) -> set[str]:
    """返回模块源码中带明确模块名的相对导入目标。"""  # noqa: DOCSTRING_CJK
    tree = ast.parse(inspect.getsource(module))
    return {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level and node.module
    }


def test_llm_model_orchestration_remains_in_original_module():
    """模型入口和统一发送器必须继续使用原模块的可替换全局变量。"""  # noqa: DOCSTRING_CJK
    public_surfaces = (
        "route_free_input_async",
        "classify_active_branch_handoff_async",
        "plan_runtime_branch_async",
        "generate_branch_entry_async",
        "generate_branch_turn_async",
        "generate_turn_async",
    )
    for name in public_surfaces:
        surface = getattr(llm, name)
        assert surface.__module__ == "services.theater.llm"
        assert surface.__globals__ is llm.__dict__
    for name in ("_invoke_model_once", "_model_config", "_record_context_incomplete"):
        assert getattr(llm, name).__module__ == "services.theater.llm"


def test_llm_reexports_moved_objects_by_identity():
    """原常量和辅助函数路径必须直接指向职责模块中的同一对象。"""  # noqa: DOCSTRING_CJK
    response_names = (
        "THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS",
        "THEATER_BRANCH_HANDOFF_MIN_CONFIDENCE",
        "THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE",
        "THEATER_BRANCH_HANDOFF_SUMMARY_MAX_CHARS",
        "THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS",
        "THEATER_BRANCH_HANDOFF_CLASSIFICATIONS",
        "THEATER_FREE_INTENT_MIN_CONFIDENCE",
        "THEATER_FREE_INTENT_RELATIONS",
        "THEATER_RESIDUAL_SUMMARY_MAX_CHARS",
        "THEATER_RESIDUAL_EVIDENCE_MAX_CHARS",
        "THEATER_RESPONSE_FOCUS_TYPES",
        "THEATER_RESPONSE_FOCUS_EVIDENCE_MAX_CHARS",
        "_FORBIDDEN_OUTPUT_TERMS",
        "_parse_planner_output",
        "_parse_branch_turn_output",
        "_parse_branch_handoff_output",
        "_parse_route_output",
        "verify_response_focus",
        "_parse_residual_intent",
        "_empty_route_result",
        "_technical_route_fallback",
        "_technical_branch_handoff_fallback",
        "_parse_output",
        "_load_unique_model_json_object",
        "_balanced_json_object_fragments",
    )
    guard_names = (
        "_INTERNAL_META_OUTPUT_PATTERNS",
        "_SOFT_PERFORMANCE_REASONS",
        "_PRIVATE_IDENTIFIER_FIELDS",
        "_performance_repair_reason",
        "_private_runtime_identifiers",
        "_exposes_internal_runtime_detail",
        "_mirrors_player_question",
        "_introduces_ungrounded_named_destination",
        "_active_story_forbidden_phrases",
        "_same_narrative_fact",
        "_story_forbidden_output_patterns",
        "_claims_uncommitted_choice_result",
        "_performance_clauses",
        "_narration_claims_player_action",
        "_dialogue_claims_player_completion",
        "_semantic_text_anchors",
        "_persona_self_name",
        "_violates_author_consent_boundary",
        "_assistant_echoes_user",
        "_repeats_recent_dialogue",
        "_dialogue_key",
    )
    fallback_names = (
        "_authored_performance_fallback",
        "_bounded_public_fallback_anchor",
        "_fallback_scene_prefix",
        "fallback_turn",
        "fallback_branch_turn",
        "fallback_branch_entry",
        "fallback_branch_handoff",
    )
    context_names = (
        "THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS",
        "THEATER_BRANCH_RECALL_MAX_FACTS",
        "THEATER_BRANCH_RECALL_MAX_HISTORIES",
        "_complete_model_text",
        "_bounded_completed_branch_recall",
        "_public_state",
        "_recent_public_turns",
        "_load_character_profile",
    )
    for name in response_names:
        assert getattr(llm, name) is getattr(llm_response_contracts, name)
    for name in guard_names:
        assert getattr(llm, name) is getattr(llm_performance_guard, name)
    for name in fallback_names:
        assert getattr(llm, name) is getattr(llm_fallbacks, name)
    for name in context_names:
        assert getattr(llm, name) is getattr(llm_context, name)


def test_llm_submodules_keep_one_way_dependencies():
    """职责模块不能反向导入模型编排主干或形成循环依赖。"""  # noqa: DOCSTRING_CJK
    modules = (
        llm_context,
        llm_response_contracts,
        llm_performance_guard,
        llm_fallbacks,
    )
    for module in modules:
        assert "llm" not in _relative_imports(module)
    assert "llm_response_contracts" in _relative_imports(llm_performance_guard)
    assert {
        "llm_response_contracts",
        "llm_performance_guard",
    }.issubset(_relative_imports(llm_fallbacks))


def test_llm_contract_results_remain_independent_and_strict():
    """技术降级结果不得共享可变对象，多对象模型输出仍必须拒绝。"""  # noqa: DOCSTRING_CJK
    first = llm._technical_route_fallback()
    second = llm._technical_route_fallback()
    first["response_focus"]["focus_type"] = "question"
    assert second["response_focus"] == {}

    with pytest.raises(ValueError, match="exactly one JSON object"):
        llm._load_unique_model_json_object('{"first": 1}\n{"second": 2}')
