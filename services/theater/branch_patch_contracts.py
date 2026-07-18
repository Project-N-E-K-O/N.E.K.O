"""校验 Planner 提交的 Runtime Branch Patch。"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .branch_contract_common import (
    _CONTENT_CATALOG_FIELD,
    _content_catalog_index,
    _content_slot_index,
    _fact_triples,
    _require_exact_fields,
    _required_text,
    _string_list,
    _validate_public_display_text,
)

# Planner 只能提出剧情候选；这些身份与版本字段必须由提交边界生成。
PROTECTED_PATCH_FIELDS = frozenset(
    {"branch_id", "created_revision", "lanlan_name", "source_revision"}
)
_PATCH_FIELDS = frozenset(
    {
        "origin_node_id",
        "seed_intent",
        "objective",
        "entry_callback",
        "turn_budget",
        "content_slot_ids",
        "allowed_new_facts",
        "forbidden_assumptions",
        "beat_outline",
        "exit_candidates",
    }
)
_FACT_RULE_FIELDS = frozenset({"fact_type", "fact_role", "content_slot_id"})
_BEAT_FIELDS = frozenset(
    {"beat_id", "objective", "observable_action", "exit_preparation"}
)
# 玩家文案是 v2.5 后补的可选展示字段；旧活动 Patch 缺失时继续兼容，但不能再公开舞台描述。
_BEAT_OPTIONAL_FIELDS = frozenset({"player_choice_label"})

def validate_runtime_branch_patch(
    value: Any,
    *,
    story: dict[str, Any],
    current_node_id: str,
    completed_goal_ids: list[str] | None = None,
) -> dict[str, Any]:
    """校验 Planner Patch 的稳定引用、预算、事实能力和可执行出口。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        raise ValueError("Runtime Branch Patch must be an object")
    protected = PROTECTED_PATCH_FIELDS & set(value)
    if protected:
        raise ValueError(
            f"Runtime Branch Patch contains protected field: {sorted(protected)[0]}"
        )
    _require_exact_fields(value, _PATCH_FIELDS, "Runtime Branch Patch")

    origin_node_id = _required_text(value.get("origin_node_id"), "origin_node_id")
    if origin_node_id != str(current_node_id or "").strip():
        raise ValueError("Runtime Branch Patch has stale origin node")
    # 当前节点还必须真实存在于作者图，防止调用方把任意 Session 字符串当成合法起点。
    authored_node_ids = {
        str(node.get("node_id") or "").strip()
        for node in story.get("narrative_nodes") or []
        if isinstance(node, dict)
    }
    if origin_node_id not in authored_node_ids:
        raise ValueError("Runtime Branch Patch references unknown origin node")

    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    turn_budget = value.get("turn_budget")
    budget = (
        contract.get("branch_turn_budget")
        if isinstance(contract.get("branch_turn_budget"), dict)
        else {}
    )
    if type(turn_budget) is not int or not int(
        budget.get("default") or 0
    ) <= turn_budget <= int(budget.get("max") or 0):
        raise ValueError("Runtime Branch Patch has invalid turn budget")

    slot_index = _content_slot_index(contract)
    content_slot_ids = _string_list(value.get("content_slot_ids"), "content_slot_ids")
    unknown_slots = set(content_slot_ids) - set(slot_index)
    if unknown_slots:
        raise ValueError(
            f"Runtime Branch Patch references unknown content slot: {sorted(unknown_slots)[0]}"
        )

    exits, permitted_roles, required_goal_roles, required_domain_roles = (
        _validate_exit_candidates(
            value.get("exit_candidates"),
            story,
        )
    )
    completed_goals = {
        str(item).strip()
        for item in completed_goal_ids or []
        if isinstance(item, str) and str(item).strip()
    }
    if any(
        exit_candidate.get("kind") == "converge"
        and str(exit_candidate.get("goal_id") or "") in completed_goals
        for exit_candidate in exits
    ):
        # Planner 不能通过新 Patch 让已经完成的作者目标再次汇流或重演。
        raise ValueError("Runtime Branch Patch references a completed goal")
    fact_rules = _validate_fact_rules(
        value.get("allowed_new_facts"),
        contract=contract,
        slot_index=slot_index,
        patch_slot_ids=set(content_slot_ids),
        permitted_roles=permitted_roles,
    )
    declared_roles = {str(item.get("fact_role") or "") for item in fact_rules}
    # 每个 Goal 的全部完成证据都必须能由 Patch 产生，否则合法出口只是不可达的装饰。
    if not required_goal_roles.issubset(declared_roles):
        raise ValueError("Runtime Branch Patch is missing goal evidence")
    if not required_domain_roles.issubset(declared_roles):
        raise ValueError("Runtime Branch Patch is missing ending domain roles")
    required_domain_types = _required_ending_domain_fact_types(exits, story)
    declared_types = {str(item.get("fact_type") or "") for item in fact_rules}
    if not required_domain_types.issubset(declared_types):
        raise ValueError("Runtime Branch Patch is missing ending domain fact types")

    patch_forbidden_assumptions = _fact_triples(
        value.get("forbidden_assumptions"), "forbidden_assumptions"
    )
    seed = story.get("seed") if isinstance(story.get("seed"), dict) else {}
    author_forbidden_assumptions = _fact_triples(
        # 旧 Story 没有 seed 或没有该可选字段时等同作者未追加禁止事实；
        # 字段一旦显式提供，_fact_triples 仍会严格拒绝非列表和坏三元组。
        seed.get("forbidden_assumptions", []),
        "seed.forbidden_assumptions",
    )
    # 作者 seed 边界由服务端强制并入每个 Patch，不能依赖 Planner 记得逐条复述。
    forbidden_assumptions: list[dict[str, str]] = []
    forbidden_keys: set[tuple[str, str, str]] = set()
    for item in [*author_forbidden_assumptions, *patch_forbidden_assumptions]:
        key = (
            str(item.get("subject") or ""),
            str(item.get("predicate") or ""),
            str(item.get("object") or ""),
        )
        if key in forbidden_keys:
            continue
        forbidden_keys.add(key)
        forbidden_assumptions.append(item)
    beat_outline = _validate_beat_outline(value.get("beat_outline"), declared_roles)
    normalized = deepcopy(value)
    normalized.update(
        {
            "origin_node_id": origin_node_id,
            "seed_intent": _required_text(value.get("seed_intent"), "seed_intent"),
            "objective": _required_text(value.get("objective"), "objective"),
            "entry_callback": _required_text(
                value.get("entry_callback"), "entry_callback"
            ),
            "turn_budget": turn_budget,
            "content_slot_ids": content_slot_ids,
            "allowed_new_facts": fact_rules,
            "forbidden_assumptions": forbidden_assumptions,
            "beat_outline": beat_outline,
            "exit_candidates": exits,
        }
    )
    return normalized


def _validate_exit_candidates(
    value: Any,
    story: dict[str, Any],
) -> tuple[list[dict[str, str]], set[str], set[str], set[str]]:
    """校验 Patch 出口，并返回可使用及必须覆盖的事实角色。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list) or not value:
        raise ValueError("Runtime Branch Patch requires at least one exit candidate")
    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    allowed_goals = set(
        _string_list(contract.get("convergence_goal_ids"), "convergence_goal_ids")
    )
    allowed_domains = set(
        _string_list(contract.get("allowed_ending_domains"), "allowed_ending_domains")
    )
    goal_index = {
        str(item.get("goal_id") or ""): item
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict)
    }
    domain_index = {
        str(item.get("ending_domain_id") or ""): item
        for item in story.get("ending_domains") or []
        if isinstance(item, dict)
    }
    normalized: list[dict[str, str]] = []
    permitted_roles: set[str] = set()
    required_goal_roles: set[str] = set()
    required_domain_roles: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Runtime Branch Patch has invalid exit candidate")
        kind = str(item.get("kind") or "").strip()
        if kind == "converge":
            _require_exact_fields(
                item, frozenset({"kind", "goal_id"}), "convergence exit"
            )
            goal_id = _required_text(item.get("goal_id"), "goal_id")
            if goal_id not in allowed_goals or goal_id not in goal_index:
                raise ValueError(
                    "Runtime Branch Patch references unknown convergence goal"
                )
            evidence = set(
                _string_list(
                    goal_index[goal_id].get("completion_evidence"),
                    "completion_evidence",
                    required=True,
                )
            )
            permitted_roles.update(evidence)
            required_goal_roles.update(evidence)
            reference_id = goal_id
            normalized_item = {"kind": kind, "goal_id": goal_id}
        elif kind == "ending_domain":
            _require_exact_fields(
                item, frozenset({"kind", "ending_domain_id"}), "ending domain exit"
            )
            domain_id = _required_text(item.get("ending_domain_id"), "ending_domain_id")
            if domain_id not in allowed_domains or domain_id not in domain_index:
                raise ValueError(
                    "Runtime Branch Patch references unknown ending domain"
                )
            domain_roles = set(
                _string_list(
                    domain_index[domain_id].get("required_fact_roles"),
                    "required_fact_roles",
                )
            )
            permitted_roles.update(domain_roles)
            required_domain_roles.update(domain_roles)
            reference_id = domain_id
            normalized_item = {"kind": kind, "ending_domain_id": domain_id}
        else:
            raise ValueError("Runtime Branch Patch has invalid exit kind")
        key = (kind, reference_id)
        if key in seen:
            raise ValueError("Runtime Branch Patch has duplicate exit candidate")
        seen.add(key)
        normalized.append(normalized_item)
    return normalized, permitted_roles, required_goal_roles, required_domain_roles


def _required_ending_domain_fact_types(
    exits: list[dict[str, str]],
    story: dict[str, Any],
) -> set[str]:
    """汇总结局出口必须由当前 Patch 可生成的事实类型，拒绝不可达的装饰性 Domain。"""  # noqa: DOCSTRING_CJK
    domain_index = {
        str(item.get("ending_domain_id") or ""): item
        for item in story.get("ending_domains") or []
        if isinstance(item, dict)
    }
    required: set[str] = set()
    for exit_candidate in exits:
        if exit_candidate.get("kind") != "ending_domain":
            continue
        domain = domain_index.get(str(exit_candidate.get("ending_domain_id") or ""))
        if isinstance(domain, dict):
            required.update(
                _string_list(domain.get("required_fact_types"), "required_fact_types")
            )
    return required


def _validate_fact_rules(
    value: Any,
    *,
    contract: dict[str, Any],
    slot_index: dict[str, dict[str, Any]],
    patch_slot_ids: set[str],
    permitted_roles: set[str],
) -> list[dict[str, str]]:
    """校验 Patch 可以产生的事实模板，禁止它扩大作者白名单。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError("Runtime Branch Patch has invalid allowed facts")
    allowed_types = set(
        _string_list(
            contract.get("allowed_dynamic_fact_types"), "allowed_dynamic_fact_types"
        )
    )
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Runtime Branch Patch has invalid fact rule")
        _require_exact_fields(
            item,
            _FACT_RULE_FIELDS,
            "Runtime Branch Patch fact rule",
            optional=frozenset({_CONTENT_CATALOG_FIELD}),
        )
        fact_type = _required_text(item.get("fact_type"), "fact_type")
        fact_role = _required_text(item.get("fact_role"), "fact_role")
        content_slot_id = str(item.get("content_slot_id") or "").strip()
        content_id = str(item.get(_CONTENT_CATALOG_FIELD) or "").strip()
        if fact_type not in allowed_types or fact_role not in permitted_roles:
            raise ValueError(
                "Runtime Branch Patch fact rule exceeds the author contract"
            )
        if content_slot_id:
            slot = slot_index.get(content_slot_id)
            if content_slot_id not in patch_slot_ids or not slot:
                raise ValueError(
                    "Runtime Branch Patch fact rule references unknown content slot"
                )
            if str(slot.get("allowed_fact_type") or "") != fact_type:
                raise ValueError(
                    "Runtime Branch Patch fact rule mismatches its content slot"
                )
            catalog = _content_catalog_index(slot)
            if catalog:
                # 严格槽位必须在 Patch 阶段绑定作者目录成员，后续 Actor 不能临时换物件。
                if not content_id or content_id not in catalog:
                    raise ValueError(
                        "Runtime Branch Patch fact rule references unknown catalog content"
                    )
            elif _CONTENT_CATALOG_FIELD in item:
                # 旧声明式槽位没有可证明的目录成员，不能接受看似权威的模型自报 content_id。
                raise ValueError(
                    "Runtime Branch Patch legacy content slot cannot declare catalog content"
                )
        elif fact_type == "ordinary_local_prop":
            raise ValueError(
                "Runtime Branch Patch local prop fact requires a content slot"
            )
        elif _CONTENT_CATALOG_FIELD in item:
            raise ValueError(
                "Runtime Branch Patch fact without a content slot cannot declare catalog content"
            )
        key = (fact_type, fact_role, content_slot_id)
        if key in seen:
            raise ValueError("Runtime Branch Patch has duplicate fact rule")
        seen.add(key)
        normalized_rule = {
            "fact_type": fact_type,
            "fact_role": fact_role,
            "content_slot_id": content_slot_id,
        }
        if content_id:
            normalized_rule[_CONTENT_CATALOG_FIELD] = content_id
        normalized.append(normalized_rule)
    return normalized


def _validate_beat_outline(
    value: Any, allowed_fact_roles: set[str]
) -> list[dict[str, Any]]:
    """校验每个轻量节拍都有可观察动作，并只准备已授权事实角色。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list) or not value:
        raise ValueError("Runtime Branch Patch requires a non-empty beat outline")
    normalized: list[dict[str, Any]] = []
    beat_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Runtime Branch Patch has invalid beat")
        _require_exact_fields(
            item,
            _BEAT_FIELDS,
            "Runtime Branch Patch beat",
            optional=_BEAT_OPTIONAL_FIELDS,
        )
        beat_id = _required_text(item.get("beat_id"), "beat_id")
        if beat_id in beat_ids:
            raise ValueError("Runtime Branch Patch has duplicate beat id")
        exit_preparation = _string_list(
            item.get("exit_preparation"), "exit_preparation"
        )
        if set(exit_preparation) - allowed_fact_roles:
            raise ValueError(
                "Runtime Branch Patch beat prepares an unauthorized fact role"
            )
        beat_ids.add(beat_id)
        normalized_beat = {
            "beat_id": beat_id,
            "objective": _required_text(item.get("objective"), "beat objective"),
            "observable_action": _required_text(
                item.get("observable_action"), "observable action"
            ),
            "exit_preparation": exit_preparation,
        }
        if "player_choice_label" in item:
            # 新 Patch 必须显式提供只由玩家控制的按钮文案；非法展示字段拒绝整个候选，不能静默降级。
            normalized_beat["player_choice_label"] = _validate_player_choice_label(
                item.get("player_choice_label")
            )
        normalized.append(normalized_beat)
    return normalized


def _validate_player_choice_label(value: Any) -> str:
    """校验动态按钮是简短玩家行动，而不是编剧旁白或对其他角色的结果控制。"""  # noqa: DOCSTRING_CJK
    label = _validate_public_display_text(value, "player choice label")
    stage_subjects = (
        "玩家",
        "双方",
        "两人",
        "彼此",
        "猫娘",
        "她会",
        "她将",
        "他会",
        "他将",
        "对方会",
        "对方将",
        "player ",
        "both ",
        "the pair",
        "catgirl ",
        "she will",
        "he will",
        "they will",
    )
    for segment in re.split(r"[，。；;.!！？!?]+", label):
        normalized = segment.strip().lower()
        if normalized and normalized.startswith(stage_subjects):
            # 每个分句都必须保持为玩家可直接实施的动作，不能预写猫娘反应或双方完成结果。
            raise ValueError("Runtime Branch Patch has invalid player choice label")
    return label
