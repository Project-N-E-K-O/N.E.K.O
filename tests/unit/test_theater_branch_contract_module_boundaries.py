"""验证 Branch Contracts 拆分后的兼容门面、依赖方向和副本边界。"""  # noqa: DOCSTRING_CJK

import ast
import inspect

from services.theater import (
    branch_contract_common,
    branch_contracts,
    branch_fact_contracts,
    branch_patch_contracts,
)


def _relative_imports(module) -> set[str]:
    """读取模块源码中的相对导入目标，不执行额外导入。"""  # noqa: DOCSTRING_CJK
    tree = ast.parse(inspect.getsource(module))
    return {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
    }


def test_branch_contract_facade_reexports_same_objects():
    """原模块路径必须继续暴露实现模块中的同一个对象。"""  # noqa: DOCSTRING_CJK
    assert (
        branch_contracts.PROTECTED_PATCH_FIELDS
        is branch_patch_contracts.PROTECTED_PATCH_FIELDS
    )
    assert (
        branch_contracts.PROTECTED_FACT_FIELDS
        is branch_fact_contracts.PROTECTED_FACT_FIELDS
    )
    assert (
        branch_contracts.PUBLIC_ENTITY_STATUSES
        is branch_contract_common.PUBLIC_ENTITY_STATUSES
    )
    assert (
        branch_contracts.BRANCH_HISTORY_EXIT_KINDS
        is branch_fact_contracts.BRANCH_HISTORY_EXIT_KINDS
    )
    assert (
        branch_contracts.validate_runtime_branch_patch
        is branch_patch_contracts.validate_runtime_branch_patch
    )
    assert (
        branch_contracts.validate_branch_fact_candidate
        is branch_fact_contracts.validate_branch_fact_candidate
    )
    assert (
        branch_contracts.build_committed_branch_fact
        is branch_fact_contracts.build_committed_branch_fact
    )
    assert (
        branch_contracts.validate_committed_branch_fact_structure
        is branch_fact_contracts.validate_committed_branch_fact_structure
    )
    assert (
        branch_contracts.validate_committed_branch_fact_against_patch
        is branch_fact_contracts.validate_committed_branch_fact_against_patch
    )
    assert (
        branch_contracts.validate_branch_history_entry
        is branch_fact_contracts.validate_branch_history_entry
    )
    assert (
        branch_contracts.validate_public_entity_label
        is branch_contract_common.validate_public_entity_label
    )


def test_branch_contract_implementation_dependency_direction():
    """Common 不反向依赖业务合同，Patch 与 Fact 也不能互相导入。"""  # noqa: DOCSTRING_CJK
    common_imports = _relative_imports(branch_contract_common)
    patch_imports = _relative_imports(branch_patch_contracts)
    fact_imports = _relative_imports(branch_fact_contracts)

    assert "branch_patch_contracts" not in common_imports
    assert "branch_fact_contracts" not in common_imports
    assert "branch_fact_contracts" not in patch_imports
    assert "branch_patch_contracts" not in fact_imports
    assert patch_imports == {"branch_contract_common"}
    assert fact_imports == {"branch_contract_common"}


def test_branch_history_validation_returns_deep_copy():
    """成功校验不能让返回的 History 与调用方输入共享可变对象。"""  # noqa: DOCSTRING_CJK
    value = {
        "branch_id": "branch_test",
        "completed_goal_ids": [],
        "key_fact_ids": [],
        "exit_kind": "user_exit",
        "ended_revision": 1,
        "recap": "保留原始回顾",
    }
    validated = branch_contracts.validate_branch_history_entry(
        value,
        story={"narrative_goals": []},
        branch_facts=[],
    )

    validated["recap"] = "修改后的回顾"
    assert value["recap"] == "保留原始回顾"
