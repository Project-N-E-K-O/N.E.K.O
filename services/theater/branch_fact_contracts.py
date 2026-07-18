"""校验动态 Branch Fact、已提交事实和结束支线索引。"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .branch_contract_common import (
    _CONTENT_CATALOG_FIELD,
    _PUBLIC_ENTITY_FIELDS,
    _content_catalog_index,
    _content_slot_index,
    _fact_triples,
    _require_exact_fields,
    _required_text,
    _string_list,
    _validate_public_entity_candidate,
)

PROTECTED_FACT_FIELDS = frozenset({"fact_id", "branch_id", "source_revision"})
# History 的退出原因是服务端状态机输出，不允许用任意自然语言替代。
BRANCH_HISTORY_EXIT_KINDS = frozenset(
    {
        "goal_converged",
        "ending_domain",
        "author_choice",
        "budget_exhausted",
        "nonprogress_exhausted",
        "user_exit",
        "intent_handoff",
        "restore_invalid",
    }
)
_FACT_CANDIDATE_REQUIRED_FIELDS = frozenset(
    {
        "goal_id",
        "fact_type",
        "fact_role",
        "subject",
        "predicate",
        "object",
        "content_slot_id",
    }
)
_COMMITTED_FACT_REQUIRED_FIELDS = (
    _FACT_CANDIDATE_REQUIRED_FIELDS | PROTECTED_FACT_FIELDS
)
_HISTORY_REQUIRED_FIELDS = frozenset(
    {"branch_id", "completed_goal_ids", "key_fact_ids", "exit_kind", "ended_revision"}
)

def validate_branch_fact_candidate(
    value: Any,
    *,
    story: dict[str, Any],
    patch: dict[str, Any],
    publicly_observed: bool,
) -> dict[str, Any]:
    """校验本轮候选事实是否已公开发生并被已验证 Patch 预授权。"""  # noqa: DOCSTRING_CJK
    if publicly_observed is not True:
        raise ValueError("Branch Fact candidate was not publicly observed")
    if not isinstance(value, dict):
        raise ValueError("Branch Fact candidate must be an object")
    protected = PROTECTED_FACT_FIELDS & set(value)
    if protected:
        raise ValueError(
            f"Branch Fact candidate contains protected field: {sorted(protected)[0]}"
        )
    _require_exact_fields(
        value,
        _FACT_CANDIDATE_REQUIRED_FIELDS,
        "Branch Fact candidate",
        optional=frozenset({"public_entity", _CONTENT_CATALOG_FIELD}),
    )

    goal_id = str(value.get("goal_id") or "").strip()
    fact_type = _required_text(value.get("fact_type"), "fact_type")
    fact_role = _required_text(value.get("fact_role"), "fact_role")
    content_slot_id = str(value.get("content_slot_id") or "").strip()
    content_id = str(value.get(_CONTENT_CATALOG_FIELD) or "").strip()
    allowed_rules = {
        (
            str(item.get("fact_type") or ""),
            str(item.get("fact_role") or ""),
            str(item.get("content_slot_id") or ""),
            str(item.get(_CONTENT_CATALOG_FIELD) or ""),
        )
        for item in patch.get("allowed_new_facts") or []
        if isinstance(item, dict)
    }
    if (fact_type, fact_role, content_slot_id, content_id) not in allowed_rules:
        raise ValueError("Branch Fact candidate is not allowed by the active Patch")

    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    slot = _content_slot_index(contract).get(content_slot_id)
    if content_slot_id and not isinstance(slot, dict):
        raise ValueError("Branch Fact candidate references an unknown content slot")
    if (
        isinstance(slot, dict)
        and str(slot.get("allowed_fact_type") or "").strip() != fact_type
    ):
        raise ValueError("Branch Fact candidate mismatches its content slot")
    catalog = _content_catalog_index(slot or {})
    catalog_item = catalog.get(content_id)
    if catalog:
        if not isinstance(catalog_item, dict):
            raise ValueError("Branch Fact candidate references unknown catalog content")
    elif _CONTENT_CATALOG_FIELD in value:
        # 没有作者目录时保持旧合同，不把模型自行提供的 ID 当作 traits 证明。
        raise ValueError("Branch Fact candidate cannot self-declare catalog content")

    goal_ids = {
        str(item.get("goal_id") or "")
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict)
    }
    patch_goal_ids = {
        str(item.get("goal_id") or "")
        for item in patch.get("exit_candidates") or []
        if isinstance(item, dict) and item.get("kind") == "converge"
    }
    if goal_id and (goal_id not in goal_ids or goal_id not in patch_goal_ids):
        raise ValueError("Branch Fact candidate references an unauthorized goal")

    normalized = deepcopy(value)
    normalized.update(
        {
            "goal_id": goal_id,
            "fact_type": fact_type,
            "fact_role": fact_role,
            "subject": _required_text(value.get("subject"), "subject"),
            "predicate": _required_text(value.get("predicate"), "predicate"),
            "object": _required_text(value.get("object"), "object"),
            "content_slot_id": content_slot_id,
        }
    )
    candidate_key = (
        normalized["subject"],
        normalized["predicate"],
        normalized["object"],
    )
    seed = story.get("seed") if isinstance(story.get("seed"), dict) else {}
    forbidden_assumptions = [
        *_fact_triples(
            seed.get("forbidden_assumptions", []),
            "seed.forbidden_assumptions",
        ),
        *_fact_triples(
            patch.get("forbidden_assumptions", []),
            "forbidden_assumptions",
        ),
    ]
    if candidate_key in {
        (item["subject"], item["predicate"], item["object"])
        for item in forbidden_assumptions
    }:
        # Prompt 只负责提醒模型；真正的作者禁止事实必须在提交门再次确定性拒绝。
        raise ValueError("Branch Fact candidate matches a forbidden assumption")

    immutable_facts = _fact_triples(
        contract.get("immutable_facts", []),
        "world_contract.immutable_facts",
    )
    for immutable in immutable_facts:
        if (
            normalized["subject"] == immutable["subject"]
            and normalized["predicate"] == immutable["predicate"]
            and normalized["object"] != immutable["object"]
        ):
            # 相同主语与谓词的另一对象会改写作者不可变事实；完全相同的重复事实仍可通过去重层处理。
            raise ValueError("Branch Fact candidate conflicts with an immutable fact")
    if catalog_item is not None:
        # 目录成员的事实对象和 Board 身份全部来自作者合同；模型只能精确引用，不能改写。
        if normalized["object"] != str(catalog_item.get("fact_object") or "").strip():
            raise ValueError("Branch Fact candidate mismatches catalog fact object")
        normalized[_CONTENT_CATALOG_FIELD] = content_id
        if "public_entity" not in value:
            raise ValueError("Branch Fact catalog content requires a public entity")
    if "public_entity" in value:
        normalized["public_entity"] = _validate_public_entity_candidate(
            value.get("public_entity"),
            requires_slot=bool(content_slot_id),
        )
        if catalog_item is not None and (
            normalized["public_entity"]["kind"]
            != str(catalog_item.get("entity_kind") or "").strip()
            or normalized["public_entity"]["label"]
            != str(catalog_item.get("label") or "").strip()
        ):
            raise ValueError("Branch Fact public entity mismatches catalog content")
    return normalized


def build_committed_branch_fact(
    candidate: dict[str, Any],
    *,
    branch_id: str,
    fact_id: str,
    source_revision: int,
    public_entity_id: str = "",
) -> dict[str, Any]:
    """由服务端为已校验候选补齐权威身份；本函数不替代前置作者合同校验。"""  # noqa: DOCSTRING_CJK
    if not isinstance(candidate, dict):
        raise ValueError("Validated Branch Fact candidate must be an object")
    protected = PROTECTED_FACT_FIELDS & set(candidate)
    if protected:
        raise ValueError(
            f"Branch Fact candidate contains protected field: {sorted(protected)[0]}"
        )
    _require_exact_fields(
        candidate,
        _FACT_CANDIDATE_REQUIRED_FIELDS,
        "Validated Branch Fact candidate",
        optional=frozenset({"public_entity", _CONTENT_CATALOG_FIELD}),
    )
    normalized_branch_id = _required_text(branch_id, "branch_id")
    normalized_fact_id = _required_text(fact_id, "fact_id")
    if type(source_revision) is not int or source_revision < 0:
        raise ValueError("Branch Fact has invalid source revision")

    committed = deepcopy(candidate)
    committed["branch_id"] = normalized_branch_id
    committed["fact_id"] = normalized_fact_id
    committed["source_revision"] = source_revision
    public_entity = committed.get("public_entity")
    if isinstance(public_entity, dict):
        # 公开实体与事实共享提交边界，不能让前端或模型自行发明 entity_id。
        public_entity["entity_id"] = _required_text(
            public_entity_id, "public_entity_id"
        )
    elif public_entity_id:
        raise ValueError(
            "Branch Fact without public entity cannot receive an entity id"
        )
    return committed


def validate_committed_branch_fact_structure(
    value: Any,
    *,
    story: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """恢复时重验服务端事实结构，并可结合当前 Story 重验作者目录绑定。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        raise ValueError("Committed Branch Fact must be an object")
    _require_exact_fields(
        value,
        _COMMITTED_FACT_REQUIRED_FIELDS,
        "Committed Branch Fact",
        optional=frozenset({"public_entity", _CONTENT_CATALOG_FIELD}),
    )
    source_revision = value.get("source_revision")
    if type(source_revision) is not int or source_revision < 0:
        raise ValueError("Committed Branch Fact has invalid source revision")
    normalized = deepcopy(value)
    normalized.update(
        {
            "fact_id": _required_text(value.get("fact_id"), "fact_id"),
            "branch_id": _required_text(value.get("branch_id"), "branch_id"),
            "source_revision": source_revision,
            "goal_id": str(value.get("goal_id") or "").strip(),
            "fact_type": _required_text(value.get("fact_type"), "fact_type"),
            "fact_role": _required_text(value.get("fact_role"), "fact_role"),
            "subject": _required_text(value.get("subject"), "subject"),
            "predicate": _required_text(value.get("predicate"), "predicate"),
            "object": _required_text(value.get("object"), "object"),
            "content_slot_id": str(value.get("content_slot_id") or "").strip(),
        }
    )
    public_entity = value.get("public_entity")
    if isinstance(public_entity, dict):
        if set(public_entity) != _PUBLIC_ENTITY_FIELDS | {"entity_id"}:
            raise ValueError("Committed Branch Fact has invalid public entity fields")
        candidate_entity = {key: public_entity[key] for key in _PUBLIC_ENTITY_FIELDS}
        normalized_entity = _validate_public_entity_candidate(
            candidate_entity,
            requires_slot=bool(normalized["content_slot_id"]),
        )
        normalized_entity["entity_id"] = _required_text(
            public_entity.get("entity_id"), "entity_id"
        )
        normalized["public_entity"] = normalized_entity
    if _CONTENT_CATALOG_FIELD in value:
        normalized[_CONTENT_CATALOG_FIELD] = _required_text(
            value.get(_CONTENT_CATALOG_FIELD), _CONTENT_CATALOG_FIELD
        )
    if story is not None:
        _validate_committed_catalog_binding(normalized, story=story)
    return normalized


def validate_committed_branch_fact_against_patch(
    value: Any,
    *,
    story: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """恢复活动支线时复核已提交事实仍绑定原 Patch，而非同槽其他成员。"""  # noqa: DOCSTRING_CJK
    committed = validate_committed_branch_fact_structure(value, story=story)
    candidate = {key: committed[key] for key in _FACT_CANDIDATE_REQUIRED_FIELDS}
    if _CONTENT_CATALOG_FIELD in committed:
        candidate[_CONTENT_CATALOG_FIELD] = committed[_CONTENT_CATALOG_FIELD]
    public_entity = committed.get("public_entity")
    if isinstance(public_entity, dict):
        # entity_id 是服务端提交身份，不属于 Actor 候选；其余展示字段必须重新走原合同。
        candidate["public_entity"] = {
            key: public_entity[key] for key in _PUBLIC_ENTITY_FIELDS
        }
    validate_branch_fact_candidate(
        candidate,
        story=story,
        patch=patch,
        publicly_observed=True,
    )
    return committed


def _validate_committed_catalog_binding(
    fact: dict[str, Any],
    *,
    story: dict[str, Any],
) -> None:
    """用当前 Story 复核已提交目录事实仍是本槽作者允许的成员。"""  # noqa: DOCSTRING_CJK
    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    content_slot_id = str(fact.get("content_slot_id") or "").strip()
    content_id = str(fact.get(_CONTENT_CATALOG_FIELD) or "").strip()
    slot = _content_slot_index(contract).get(content_slot_id)
    if content_slot_id and not isinstance(slot, dict):
        raise ValueError("Committed Branch Fact references unknown content slot")
    catalog = _content_catalog_index(slot or {})
    if not catalog:
        if content_id:
            raise ValueError(
                "Committed Branch Fact cannot self-declare catalog content"
            )
        return
    item = catalog.get(content_id)
    if not isinstance(item, dict):
        raise ValueError("Committed Branch Fact references unknown catalog content")
    if (
        str(fact.get("fact_type") or "").strip()
        != str(slot.get("allowed_fact_type") or "").strip()
        or str(fact.get("object") or "").strip()
        != str(item.get("fact_object") or "").strip()
    ):
        raise ValueError("Committed Branch Fact mismatches catalog content")
    entity = fact.get("public_entity")
    if not isinstance(entity, dict) or (
        str(entity.get("kind") or "").strip()
        != str(item.get("entity_kind") or "").strip()
        or str(entity.get("label") or "").strip()
        != str(item.get("label") or "").strip()
    ):
        raise ValueError(
            "Committed Branch Fact public entity mismatches catalog content"
        )


def validate_branch_history_entry(
    value: Any,
    *,
    story: dict[str, Any],
    branch_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """校验已结束支线的结构化索引只引用同支线权威事实。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        raise ValueError("Branch History entry must be an object")
    _require_exact_fields(
        value,
        _HISTORY_REQUIRED_FIELDS,
        "Branch History entry",
        optional=frozenset({"recap"}),
    )
    branch_id = _required_text(value.get("branch_id"), "branch_id")
    completed_goal_ids = _string_list(
        value.get("completed_goal_ids"), "completed_goal_ids"
    )
    authored_goal_ids = {
        str(item.get("goal_id") or "")
        for item in story.get("narrative_goals") or []
        if isinstance(item, dict)
    }
    unknown_goals = set(completed_goal_ids) - authored_goal_ids
    if unknown_goals:
        raise ValueError(
            f"Branch History references unknown goal: {sorted(unknown_goals)[0]}"
        )

    key_fact_ids = _string_list(value.get("key_fact_ids"), "key_fact_ids")
    fact_branch_by_id = {
        str(item.get("fact_id") or ""): str(item.get("branch_id") or "")
        for item in branch_facts
        if isinstance(item, dict) and str(item.get("fact_id") or "").strip()
    }
    for fact_id in key_fact_ids:
        if fact_id not in fact_branch_by_id or fact_branch_by_id[fact_id] != branch_id:
            raise ValueError(
                f"Branch History references unknown branch fact: {fact_id}"
            )

    exit_kind = str(value.get("exit_kind") or "").strip()
    if exit_kind not in BRANCH_HISTORY_EXIT_KINDS:
        raise ValueError("Branch History has invalid exit kind")
    ended_revision = value.get("ended_revision")
    if type(ended_revision) is not int or ended_revision < 0:
        raise ValueError("Branch History has invalid ended revision")
    recap = value.get("recap", "")
    if not isinstance(recap, str) or len(recap) > 600:
        raise ValueError("Branch History has invalid recap")

    normalized = deepcopy(value)
    normalized.update(
        {
            "branch_id": branch_id,
            "completed_goal_ids": completed_goal_ids,
            "key_fact_ids": key_fact_ids,
            "exit_kind": exit_kind,
            "ended_revision": ended_revision,
            "recap": recap.strip(),
        }
    )
    return normalized
