"""构建小剧场静态图与演绎层共用的只读权威事实视图。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from typing import Any


_FACT_FIELDS = ("subject", "predicate", "object")


def authoritative_facts(story: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """合并静态事实与已完成 Goal 的作者投影，不读取模型生成的支线事实。"""  # noqa: DOCSTRING_CJK
    result: list[dict[str, Any]] = []
    known: set[str] = set()

    # narrative_facts 仍是真实存储；视图只复制读取，不能反向修改 Session。
    for item in state.get("narrative_facts") or []:
        _append_unique_fact(result, known, item)

    completed_goal_ids = {
        item.strip()
        for item in state.get("completed_goal_ids") or []
        if isinstance(item, str) and item.strip()
    }
    for goal in story.get("narrative_goals") or []:
        if not isinstance(goal, dict) or str(goal.get("goal_id") or "").strip() not in completed_goal_ids:
            continue
        # completion_fact_projections 由作者在 Story 中声明；原始 branch_facts 的三元组
        # 由模型生成，只能服务当前动态支线，绝不能借此解锁静态节点或结局。
        for item in goal.get("completion_fact_projections") or []:
            projection = _author_projection(item)
            if projection:
                _append_unique_fact(result, known, projection)

    return result


def _append_unique_fact(result: list[dict[str, Any]], known: set[str], value: Any) -> None:
    """复制并稳定去重一个结构化事实；非法值不进入权威视图。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict):
        return
    key = _fact_key(value)
    if not key or key in known:
        return
    result.append(dict(value))
    known.add(key)


def _fact_key(value: dict[str, Any]) -> str:
    """按事实三元组比较语义，同时兼容旧静态事实上的展示字段。"""  # noqa: DOCSTRING_CJK
    triple = {key: value.get(key) for key in _FACT_FIELDS if key in value}
    comparable = triple or value
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _author_projection(value: Any) -> dict[str, str]:
    """防御性收窄作者投影，避免绕过 Loader 时携带支线身份或坏三元组。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict) or set(value) != set(_FACT_FIELDS):
        return {}
    projection = {key: value.get(key) for key in _FACT_FIELDS}
    if any(not isinstance(item, str) or not item.strip() for item in projection.values()):
        return {}
    return {key: value[key] for key in _FACT_FIELDS}
