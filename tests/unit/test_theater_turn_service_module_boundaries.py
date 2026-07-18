"""验证 Turn Service 拆分后仍保留唯一事务主干和既有测试挂点。"""  # noqa: DOCSTRING_CJK

import ast
from copy import deepcopy
import inspect

from services.theater import (
    turn_branch_flow,
    turn_history,
    turn_request_contracts,
    turn_service,
)


def _relative_imports(module) -> set[str]:
    """返回模块源码中带明确模块名的相对导入目标。"""  # noqa: DOCSTRING_CJK
    tree = ast.parse(inspect.getsource(module))
    return {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level and node.module
    }


def test_turn_transaction_spine_remains_in_original_module():
    """锁内事务和总状态机不能被替换成兼容包装函数。"""  # noqa: DOCSTRING_CJK
    assert turn_service.submit.__module__ == "services.theater.turn_service"
    assert turn_service._submit_impl.__module__ == "services.theater.turn_service"
    assert turn_service._apply_turn.__module__ == "services.theater.turn_service"
    assert (
        turn_service._current_catgirl_name.__module__
        == "services.theater.turn_service"
    )


def test_turn_service_reexports_moved_objects_by_identity():
    """原常量和辅助函数路径继续指向实现模块中的同一个对象。"""  # noqa: DOCSTRING_CJK
    request_names = (
        "MAX_IDEMPOTENT_RESULTS",
        "MAX_FREE_INPUT_CHARS",
        "_turn_submit_outcome",
        "_turn_execution_surface",
        "_verified_residual_evidence_excerpt",
        "_normalize_request",
        "_cached_result",
        "_revision_conflict",
    )
    history_names = (
        "MAX_RECENT_TURN_MESSAGES",
        "_compose_graph_progress_dialogue",
        "_append_turns",
        "_now_ms",
    )
    branch_names = (
        "_active_branch_handoff_is_verified",
        "_active_branch_continue_is_verified",
        "_prepare_active_branch_handoff",
        "_prepare_technical_degraded_active_branch_turn",
        "_prepare_active_runtime_branch_turn",
        "_finish_runtime_branch",
        "_record_committed_branch_outcomes",
        "_narrative_goal",
        "_ending_domain",
        "_prepare_runtime_branch_entry",
        "_revalidatable_pending_intent",
        "_apply_exit",
    )
    for name in request_names:
        assert getattr(turn_service, name) is getattr(turn_request_contracts, name)
    for name in history_names:
        assert getattr(turn_service, name) is getattr(turn_history, name)
    for name in branch_names:
        assert getattr(turn_service, name) is getattr(turn_branch_flow, name)


def test_turn_submodules_preserve_dependency_and_monkeypatch_objects():
    """新模块不能反向导入事务主干，模型与 Store 仍共享模块对象。"""  # noqa: DOCSTRING_CJK
    assert "turn_service" not in _relative_imports(turn_request_contracts)
    assert "turn_service" not in _relative_imports(turn_history)
    assert "turn_service" not in _relative_imports(turn_branch_flow)
    assert turn_service.llm is turn_branch_flow.llm
    assert turn_service.session_store is turn_branch_flow.session_store


def test_cached_turn_result_remains_a_deep_copy():
    """幂等回放结果不能与 Session 中的已保存结果共享可变对象。"""  # noqa: DOCSTRING_CJK
    stored = {
        "ok": True,
        "dialogue": "原始结果",
        "scenario_trace": {"progress_kind": "roleplay_response"},
    }
    session = {"turn_results_by_client_id": {"turn_test": deepcopy(stored)}}
    cached = turn_service._cached_result(session, "turn_test")

    cached["scenario_trace"]["progress_kind"] = "changed"
    assert session["turn_results_by_client_id"]["turn_test"] == stored
