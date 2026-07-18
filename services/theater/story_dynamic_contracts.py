"""校验小剧场可选 v2.5 动态作者合同。"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from . import branch_contracts


# v2.5 字段必须成组出现；旧 Story 可以继续走 v2.4，不能加载半套动态支线协议。
V25_STORY_CONTRACT_FIELDS = (
    "story_revision",
    "world_contract",
    "narrative_goals",
    "ending_domains",
)
# 目录内部引用必须与公开自然语言明显区分，才能被演绎泄漏门禁稳定识别。
_CONTENT_CATALOG_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
# 目录会完整进入 Planner，字段必须保持在可回传且不会放大 Prompt 的作者合同边界内。
_CONTENT_CATALOG_MAX_ITEMS = 16
_CONTENT_CATALOG_MAX_TOTAL_ITEMS = 32
_CONTENT_CATALOG_REFERENCE_MAX_CHARS = 64
_CONTENT_CATALOG_MAX_TRAITS = 12
_CONTENT_CATALOG_TRAIT_MAX_CHARS = 48
_CONTENT_CATALOG_PROMPT_MAX_CHARS = 12_000


def validate_dynamic_story_contract(
    story: dict[str, Any], path: Path, nodes: list[dict[str, Any]]
) -> None:
    """校验成组声明的 v2.5 Goal、World、Catalog 和 Ending 合同。"""  # noqa: DOCSTRING_CJK
    declared = [field for field in V25_STORY_CONTRACT_FIELDS if field in story]
    if not declared:
        return
    missing = [field for field in V25_STORY_CONTRACT_FIELDS if field not in story]
    if missing:
        raise ValueError(
            f"Theater story {path} has incomplete v2.5 contract: {', '.join(missing)}"
        )
    revision = story.get("story_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError(f"Theater story {path} has invalid story revision")

    # Narrative Goal 先建立稳定 ID 集合，World Contract 和 Ending Domain 只能引用该白名单。
    goal_ids = _validate_narrative_goals(story.get("narrative_goals"), path, nodes)
    _validate_node_goal_completions(nodes, path, goal_ids)
    _validate_recommended_edge_goal_bindings(story.get("edges"), path, goal_ids)
    contract = story.get("world_contract")
    if not isinstance(contract, dict):
        raise ValueError(f"Theater story {path} has invalid world contract")
    allowed_fact_types = _validate_world_contract(contract, path, goal_ids)
    domain_ids = _validate_ending_domains(
        story.get("ending_domains"),
        path,
        story=story,
        goal_ids=goal_ids,
        allowed_fact_types=allowed_fact_types,
    )
    allowed_domains = _validated_string_list(
        contract.get("allowed_ending_domains"),
        path,
        "world_contract.allowed_ending_domains",
    )
    unknown_domains = set(allowed_domains) - domain_ids
    if unknown_domains:
        raise ValueError(
            f"Theater story {path} references unknown ending domain: {sorted(unknown_domains)[0]}"
        )


def _validate_node_goal_completions(
    nodes: list[dict[str, Any]],
    path: Path,
    goal_ids: set[str],
) -> None:
    """校验静态节点显式完成的 Narrative Goal，不从节点标题或事实文本推断。"""  # noqa: DOCSTRING_CJK
    for node in nodes:
        if "completes_goal_ids" not in node:
            continue
        node_id = str(node.get("node_id") or "")
        completed = set(
            _validated_string_list(
                node.get("completes_goal_ids"),
                path,
                f"narrative_node.{node_id}.completes_goal_ids",
                required=True,
            )
        )
        unknown = completed - goal_ids
        if unknown:
            raise ValueError(
                f"Theater story {path} node references unknown completed goal: {sorted(unknown)[0]}"
            )


def _validate_recommended_edge_goal_bindings(
    value: Any,
    path: Path,
    goal_ids: set[str],
) -> None:
    """校验推荐边可选的 Goal 绑定；旧 latent goal 继续保留其 v2.4 路由语义。"""  # noqa: DOCSTRING_CJK
    for edge in value or []:
        if (
            not isinstance(edge, dict)
            or str(edge.get("visibility") or "recommended") == "latent"
        ):
            continue
        if "goal_id" not in edge:
            continue
        goal_id = str(edge.get("goal_id") or "").strip()
        if not goal_id or goal_id not in goal_ids:
            raise ValueError(
                f"Theater story {path} recommended edge references unknown goal"
            )


def _validate_narrative_goals(
    value: Any, path: Path, nodes: list[dict[str, Any]]
) -> set[str]:
    """校验 Narrative Goal 的完成证据、汇流出口和作者回退。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"Theater story {path} has invalid narrative goals")
    node_ids = {str(node.get("node_id") or "") for node in nodes}
    goal_ids: set[str] = set()
    for goal in value:
        if not isinstance(goal, dict):
            raise ValueError(f"Theater story {path} has invalid narrative goal")
        goal_id = str(goal.get("goal_id") or "").strip()
        summary = str(goal.get("summary") or "").strip()
        converge_to = str(goal.get("converge_to_node_id") or "").strip()
        fallback = str(goal.get("fallback_convergence_callback") or "").strip()
        if not goal_id or not summary or not converge_to or not fallback:
            raise ValueError(f"Theater story {path} has incomplete narrative goal")
        if goal_id in goal_ids:
            raise ValueError(
                f"Theater story {path} has duplicate narrative goal: {goal_id}"
            )
        if converge_to not in node_ids:
            raise ValueError(
                f"Theater story {path} narrative goal references unknown convergence node"
            )
        evidence = set(
            _validated_string_list(
                goal.get("completion_evidence"),
                path,
                f"narrative_goal.{goal_id}.completion_evidence",
                required=True,
            )
        )
        fact_roles = set(
            _validated_string_list(
                goal.get("convergence_fact_roles"),
                path,
                f"narrative_goal.{goal_id}.convergence_fact_roles",
                required=True,
            )
        )
        # Goal 完成只能由作者声明的证据角色证明，不能让 Planner 临时创造新角色名。
        if not fact_roles.issubset(evidence):
            raise ValueError(
                f"Theater story {path} narrative goal has unknown convergence fact role"
            )
        projections = goal.get("completion_fact_projections", [])
        if not isinstance(projections, list):
            raise ValueError(
                f"Theater story {path} narrative goal has invalid completion fact projections"
            )
        projection_keys: set[tuple[str, str, str]] = set()
        for projection in projections:
            # 投影会进入静态图和通用演绎上下文，因此只允许作者声明完整、无附加身份字段的三元组。
            if not _is_fact_triple(projection) or set(projection) != {
                "subject",
                "predicate",
                "object",
            }:
                raise ValueError(
                    f"Theater story {path} narrative goal has invalid completion fact projection"
                )
            projection_key = tuple(
                str(projection[key]).strip()
                for key in ("subject", "predicate", "object")
            )
            if projection_key in projection_keys:
                raise ValueError(
                    f"Theater story {path} narrative goal has duplicate completion fact projection"
                )
            projection_keys.add(projection_key)
        goal_ids.add(goal_id)
    return goal_ids


def _validate_world_contract(
    contract: dict[str, Any], path: Path, goal_ids: set[str]
) -> set[str]:
    """校验双主角边界、动态内容槽位、预算和安全退出策略。"""  # noqa: DOCSTRING_CJK
    required_fields = {
        "speaking_roles",
        "immutable_facts",
        "allowed_dynamic_fact_types",
        "dynamic_content_slots",
        "forbidden_changes",
        "branch_turn_budget",
        "branch_abort_policy",
        "allowed_ending_domains",
        "convergence_goal_ids",
    }
    missing = sorted(required_fields - set(contract))
    if missing:
        raise ValueError(
            f"Theater story {path} world contract missing fields: {', '.join(missing)}"
        )
    speaking_roles = _validated_string_list(
        contract.get("speaking_roles"),
        path,
        "world_contract.speaking_roles",
        required=True,
    )
    # 小剧场运行时只允许玩家与当前猫娘发言，顺序不影响协议语义。
    if set(speaking_roles) != {"player", "active_catgirl"} or len(speaking_roles) != 2:
        raise ValueError(
            f"Theater story {path} world contract has invalid speaking roles"
        )
    immutable_facts = contract.get("immutable_facts")
    if not isinstance(immutable_facts, list) or any(
        not _is_fact_triple(item) for item in immutable_facts
    ):
        raise ValueError(
            f"Theater story {path} world contract has invalid immutable facts"
        )
    allowed_fact_types = set(
        _validated_string_list(
            contract.get("allowed_dynamic_fact_types"),
            path,
            "world_contract.allowed_dynamic_fact_types",
            required=True,
        )
    )
    _validate_dynamic_content_slots(
        contract.get("dynamic_content_slots"), path, allowed_fact_types
    )
    _validated_string_list(
        contract.get("forbidden_changes"),
        path,
        "world_contract.forbidden_changes",
        required=True,
    )

    budget = contract.get("branch_turn_budget")
    if not isinstance(budget, dict):
        raise ValueError(
            f"Theater story {path} world contract has invalid branch budget"
        )
    default_turns = budget.get("default")
    max_turns = budget.get("max")
    max_nonprogress = budget.get("max_nonprogress_turns")
    # bool 是 int 的子类，必须显式排除，避免 true 被误当成一个回合。
    if any(
        type(item) is not int for item in (default_turns, max_turns, max_nonprogress)
    ):
        raise ValueError(
            f"Theater story {path} world contract has invalid branch budget"
        )
    if (
        default_turns < 1
        or max_turns < default_turns
        or not 0 <= max_nonprogress <= max_turns
    ):
        raise ValueError(
            f"Theater story {path} world contract has invalid branch budget"
        )

    abort_policy = contract.get("branch_abort_policy")
    if (
        not isinstance(abort_policy, dict)
        or str(abort_policy.get("mode") or "") != "return_to_anchor"
    ):
        raise ValueError(
            f"Theater story {path} world contract has invalid abort policy"
        )
    if not str(abort_policy.get("neutral_callback") or "").strip():
        raise ValueError(
            f"Theater story {path} world contract is missing abort callback"
        )
    convergence_goals = set(
        _validated_string_list(
            contract.get("convergence_goal_ids"),
            path,
            "world_contract.convergence_goal_ids",
        )
    )
    unknown_goals = convergence_goals - goal_ids
    if unknown_goals:
        raise ValueError(
            f"Theater story {path} references unknown convergence goal: {sorted(unknown_goals)[0]}"
        )
    return allowed_fact_types


def _validate_dynamic_content_slots(
    value: Any, path: Path, allowed_fact_types: set[str]
) -> None:
    """校验动态内容只能使用作者声明的唯一槽位与枚举特征。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"Theater story {path} has invalid dynamic content slots")
    slot_ids: set[str] = set()
    catalog_item_count = 0
    catalog_prompt_chars = 0
    for slot in value:
        if not isinstance(slot, dict):
            raise ValueError(f"Theater story {path} has invalid dynamic content slot")
        slot_id = str(slot.get("slot_id") or "").strip()
        fact_type = str(slot.get("allowed_fact_type") or "").strip()
        if not slot_id or slot_id in slot_ids or fact_type not in allowed_fact_types:
            raise ValueError(f"Theater story {path} has invalid dynamic content slot")
        allowed_traits = set(
            _validated_string_list(
                slot.get("allowed_traits"),
                path,
                f"dynamic_content_slot.{slot_id}.allowed_traits",
                required=True,
            )
        )
        forbidden_traits = set(
            _validated_string_list(
                slot.get("forbidden_traits"),
                path,
                f"dynamic_content_slot.{slot_id}.forbidden_traits",
            )
        )
        if allowed_traits & forbidden_traits:
            raise ValueError(
                f"Theater story {path} dynamic content slot has conflicting traits"
            )
        if "catalog_items" in slot:
            # 严格槽位会完整进入 Planner；旧声明式槽位不受这组新增预算影响。
            if any(
                len(reference) > _CONTENT_CATALOG_REFERENCE_MAX_CHARS
                or not _CONTENT_CATALOG_REFERENCE_RE.fullmatch(reference)
                for reference in (slot_id, fact_type)
            ) or any(
                len(traits) > _CONTENT_CATALOG_MAX_TRAITS
                or any(
                    len(trait) > _CONTENT_CATALOG_TRAIT_MAX_CHARS for trait in traits
                )
                for traits in (allowed_traits, forbidden_traits)
            ):
                raise ValueError(
                    f"Theater story {path} has oversized catalog slot authority: {slot_id}"
                )
            catalog_item_count += _validate_content_catalog(
                slot.get("catalog_items"),
                path,
                slot_id=slot_id,
                required_traits=allowed_traits,
                forbidden_traits=forbidden_traits,
            )
            if catalog_item_count > _CONTENT_CATALOG_MAX_TOTAL_ITEMS:
                raise ValueError(f"Theater story {path} has too many catalog items")
            catalog_prompt_chars += len(
                json.dumps(slot, ensure_ascii=False, separators=(",", ":"))
            )
            if catalog_prompt_chars > _CONTENT_CATALOG_PROMPT_MAX_CHARS:
                raise ValueError(f"Theater story {path} catalog exceeds prompt budget")
        slot_ids.add(slot_id)


def _validate_content_catalog(
    value: Any,
    path: Path,
    *,
    slot_id: str,
    required_traits: set[str],
    forbidden_traits: set[str],
) -> int:
    """校验可选作者目录；成员身份而不是名称关键词提供确定性授权。"""  # noqa: DOCSTRING_CJK
    if (
        not isinstance(value, list)
        or not value
        or len(value) > _CONTENT_CATALOG_MAX_ITEMS
    ):
        raise ValueError(
            f"Theater story {path} content catalog must not be empty: {slot_id}"
        )
    required_fields = {"content_id", "entity_kind", "label", "fact_object", "traits"}
    content_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ValueError(
                f"Theater story {path} has invalid catalog item: {slot_id}"
            )
        content_id = str(item.get("content_id") or "").strip()
        entity_kind = str(item.get("entity_kind") or "").strip()
        label = item.get("label")
        fact_object_value = item.get("fact_object")
        fact_object = (
            fact_object_value.strip() if isinstance(fact_object_value, str) else ""
        )
        if (
            not content_id
            or content_id in content_ids
            or len(content_id) > _CONTENT_CATALOG_REFERENCE_MAX_CHARS
            or not _CONTENT_CATALOG_REFERENCE_RE.fullmatch(content_id)
            or entity_kind not in {"prop", "clue"}
            or not isinstance(fact_object_value, str)
            or len(fact_object) > _CONTENT_CATALOG_REFERENCE_MAX_CHARS
            or not _CONTENT_CATALOG_REFERENCE_RE.fullmatch(fact_object)
        ):
            raise ValueError(
                f"Theater story {path} has invalid catalog item: {slot_id}"
            )
        try:
            # Loader 与事实提交必须共享同一展示规则，避免“校验通过但运行时永远不可执行”。
            branch_contracts.validate_public_entity_label(label)
        except ValueError as exc:
            raise ValueError(
                f"Theater story {path} has invalid catalog label: {slot_id}"
            ) from exc
        trait_list = _validated_string_list(
            item.get("traits"),
            path,
            f"dynamic_content_slot.{slot_id}.catalog_item.{content_id}.traits",
            required=True,
        )
        if len(trait_list) > _CONTENT_CATALOG_MAX_TRAITS or any(
            len(trait) > _CONTENT_CATALOG_TRAIT_MAX_CHARS for trait in trait_list
        ):
            raise ValueError(
                f"Theater story {path} catalog item has oversized traits: {slot_id}"
            )
        traits = set(trait_list)
        # 现有 allowed_traits 的作者语义是全部必需的正向特征，而不是可任选的闭集。
        if not required_traits.issubset(traits) or traits & forbidden_traits:
            raise ValueError(
                f"Theater story {path} catalog item violates slot traits: {slot_id}"
            )
        content_ids.add(content_id)
    return len(value)


def _validate_ending_domains(
    value: Any,
    path: Path,
    *,
    story: dict[str, Any],
    goal_ids: set[str],
    allowed_fact_types: set[str],
) -> set[str]:
    """校验 Ending Domain 只能落到作者结局，并具备确定性公开证据。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"Theater story {path} has invalid ending domains")
    authored_endings = {
        str(item.get("id") or "").strip()
        for item in story.get("ending_attractors") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for node in story.get("narrative_nodes") or []:
        if not isinstance(node, dict) or str(node.get("node_type") or "") != "ending":
            continue
        # 静态合同已要求 ending_id；Ending Domain 只能引用作者显式结局，不能再拿 Node ID 代替。
        authored_endings.add(str(node.get("ending_id") or "").strip())
    domain_ids: set[str] = set()
    for domain in value:
        if not isinstance(domain, dict):
            raise ValueError(f"Theater story {path} has invalid ending domain")
        domain_id = str(domain.get("ending_domain_id") or "").strip()
        ending_id = str(domain.get("ending_id") or "").strip()
        if (
            not domain_id
            or domain_id in domain_ids
            or ending_id not in authored_endings
        ):
            raise ValueError(
                f"Theater story {path} has invalid ending domain reference"
            )
        required_goals = set(
            _validated_string_list(
                domain.get("required_goal_ids"),
                path,
                f"ending_domain.{domain_id}.required_goal_ids",
            )
        )
        required_types = set(
            _validated_string_list(
                domain.get("required_fact_types"),
                path,
                f"ending_domain.{domain_id}.required_fact_types",
            )
        )
        required_roles = set(
            _validated_string_list(
                domain.get("required_fact_roles"),
                path,
                f"ending_domain.{domain_id}.required_fact_roles",
            )
        )
        forbidden_roles = set(
            _validated_string_list(
                domain.get("forbidden_fact_roles"),
                path,
                f"ending_domain.{domain_id}.forbidden_fact_roles",
            )
        )
        if required_goals - goal_ids or required_types - allowed_fact_types:
            raise ValueError(
                f"Theater story {path} ending domain has unknown evidence reference"
            )
        if required_roles & forbidden_roles or not (
            required_goals or required_types or required_roles
        ):
            raise ValueError(
                f"Theater story {path} ending domain has invalid evidence rules"
            )
        domain_ids.add(domain_id)
    return domain_ids


def _validated_string_list(
    value: Any,
    path: Path,
    field: str,
    *,
    required: bool = False,
) -> list[str]:
    """返回无空值、无重复的稳定字符串列表。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"Theater story {path} has invalid {field}")
    normalized = [
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ]
    if (
        len(normalized) != len(value)
        or len(normalized) != len(set(normalized))
        or (required and not normalized)
    ):
        raise ValueError(f"Theater story {path} has invalid {field}")
    return normalized


def _is_fact_triple(value: Any) -> bool:
    """只接受可由现有 Rules 稳定比较的事实三元组。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(value.get(key), str) and str(value.get(key)).strip()
        for key in ("subject", "predicate", "object")
    )
