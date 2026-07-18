"""提供临时支线合同共用的字段、Catalog 与公开文本校验原语。"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import re
from typing import Any

# Board 只有这三类公开落点；固定枚举可防止 Actor 用任意状态字符串控制前端分组。
PUBLIC_ENTITY_STATUSES = {
    "prop": frozenset({"available", "selected", "used"}),
    "clue": frozenset({"discovered"}),
}

# 只有作者提供 catalog_items 的槽位才使用 content_id；旧 Story 的声明式槽位继续兼容。
_CONTENT_CATALOG_FIELD = "content_id"

_PUBLIC_ENTITY_FIELDS = frozenset({"kind", "label", "status"})
# Planner/Actor 生成的展示文本必须短且只含自然语言，不能把合同字段或稳定引用投影到按钮与 Board。
_PUBLIC_DISPLAY_TEXT_MAX_CHARS = 80
_PUBLIC_DISPLAY_INTERNAL_FIELDS = frozenset(
    {
        "allowed_new_facts",
        "beat_id",
        "beat_outline",
        "branch_id",
        "choice_id",
        "content_id",
        "content_slot_id",
        "content_slot_ids",
        "created_revision",
        "ending_domain_id",
        "entity_id",
        "entry_callback",
        "exit_candidates",
        "exit_preparation",
        "fact_id",
        "fact_role",
        "fact_type",
        "forbidden_assumptions",
        "goal_id",
        "intent_key",
        "node_id",
        "nonprogress_turns",
        "observable_action",
        "origin_node_id",
        "player_choice_label",
        "scene_id",
        "seed_intent",
        "source_revision",
        "thread_state",
        "turn_budget",
        "turns_used",
    }
)
_PUBLIC_DISPLAY_INTERNAL_FIELD_RE = re.compile(
    r"(?i)(?<![a-z0-9_])(?:"
    + "|".join(re.escape(field) for field in sorted(_PUBLIC_DISPLAY_INTERNAL_FIELDS))
    + r")(?![a-z0-9_])"
)
# snake_case 是当前 Story/Session 稳定引用的通用形态；单连字符自然英文（如 well-made）不在此列。
_PUBLIC_DISPLAY_SNAKE_REFERENCE_RE = re.compile(
    r"(?i)(?<![a-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![a-z0-9_])"
)
# 兼容拦截 node-123、goal.archive、prop-secret-item 等非 snake_case 稳定引用，
# 同时保留 goal-oriented 这类只有一个纯英文连字符的自然表达。
_PUBLIC_DISPLAY_PREFIXED_REFERENCE_RE = re.compile(
    r"(?i)(?<![a-z0-9.:-])"
    r"(?:node|scene|choice|goal|prop|clue|branch|fact|entity|beat|slot|intent|transition|ending|domain)"
    r"(?:(?:[.:][a-z0-9]+)+|-[0-9][a-z0-9-]*|(?:-[a-z0-9]+){2,})"
    r"(?![a-z0-9.:-])"
)
_PUBLIC_DISPLAY_UUID_RE = re.compile(
    r"(?i)(?<![a-f0-9])"
    r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"
    r"(?![a-f0-9])"
)

def _validate_public_entity_candidate(
    value: Any, *, requires_slot: bool
) -> dict[str, str]:
    """校验公开实体候选，保留 entity_id 给服务端提交步骤。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        raise ValueError("Branch Fact public entity must be an object")
    if "entity_id" in value:
        raise ValueError(
            "Branch Fact public entity contains protected field: entity_id"
        )
    _require_exact_fields(value, _PUBLIC_ENTITY_FIELDS, "Branch Fact public entity")
    kind = _required_text(value.get("kind"), "public entity kind")
    if kind not in PUBLIC_ENTITY_STATUSES:
        raise ValueError("Branch Fact public entity has invalid kind")
    if kind == "prop" and not requires_slot:
        raise ValueError("Branch Fact public prop requires a content slot")
    status = _required_text(value.get("status"), "public entity status")
    if status not in PUBLIC_ENTITY_STATUSES[kind]:
        raise ValueError("Branch Fact public entity has invalid status")
    return {
        "kind": kind,
        "label": validate_public_entity_label(value.get("label")),
        "status": status,
    }


def _content_slot_index(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建立作者动态槽位索引；Story Loader 已负责完整 schema 校验。"""  # noqa: DOCSTRING_CJK
    return {
        str(item.get("slot_id") or ""): item
        for item in contract.get("dynamic_content_slots") or []
        if isinstance(item, dict) and str(item.get("slot_id") or "").strip()
    }


def _content_catalog_index(slot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建立单个槽位的作者目录索引；空目录表示旧声明式兼容模式。"""  # noqa: DOCSTRING_CJK
    return {
        str(item.get("content_id") or "").strip(): item
        for item in slot.get("catalog_items") or []
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    }


def _fact_triples(value: Any, field: str) -> list[dict[str, str]]:
    """校验并复制服务端可稳定比较的事实三元组列表。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field} contains an invalid fact")
        _require_exact_fields(
            item, frozenset({"subject", "predicate", "object"}), field
        )
        fact = {
            "subject": _required_text(item.get("subject"), "subject"),
            "predicate": _required_text(item.get("predicate"), "predicate"),
            "object": _required_text(item.get("object"), "object"),
        }
        key = (fact["subject"], fact["predicate"], fact["object"])
        if key in seen:
            raise ValueError(f"{field} contains a duplicate fact")
        seen.add(key)
        normalized.append(fact)
    return normalized


def _string_list(value: Any, field: str, *, required: bool = False) -> list[str]:
    """返回无空值、无重复的稳定字符串列表。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
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
        raise ValueError(f"{field} contains invalid stable ids")
    return normalized


def _required_text(value: Any, field: str) -> str:
    """校验模型合同中的必填短文本，不接受隐式数字或空白。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_public_display_text(value: Any, field: str) -> str:
    """校验模型生成的短展示文本，拒绝内部字段与稳定机器引用。"""  # noqa: DOCSTRING_CJK
    text = _required_text(value, field)
    if (
        len(text) > _PUBLIC_DISPLAY_TEXT_MAX_CHARS
        or "\n" in text
        or "\r" in text
        or _PUBLIC_DISPLAY_INTERNAL_FIELD_RE.search(text)
        or _PUBLIC_DISPLAY_SNAKE_REFERENCE_RE.search(text)
        or _PUBLIC_DISPLAY_PREFIXED_REFERENCE_RE.search(text)
        or _PUBLIC_DISPLAY_UUID_RE.search(text)
    ):
        raise ValueError(f"{field} is invalid for public display")
    return text


def validate_public_entity_label(value: Any) -> str:
    """统一校验作者目录与运行时 Board 共用的公开实体标签。"""  # noqa: DOCSTRING_CJK
    return _validate_public_display_text(value, "public entity label")


def _require_exact_fields(
    value: dict[str, Any],
    required: frozenset[str],
    label: str,
    *,
    optional: frozenset[str] = frozenset(),
) -> None:
    """拒绝缺字段和未知字段，避免模型输出被静默忽略后形成双重语义。"""  # noqa: DOCSTRING_CJK
    fields = set(value)
    missing = required - fields
    unknown = fields - required - optional
    if missing:
        raise ValueError(f"{label} is missing field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"{label} contains unknown field: {sorted(unknown)[0]}")
