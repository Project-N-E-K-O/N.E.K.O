"""把轻量私有状态投影成前端可以安全显示的响应。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any

from . import story_graph, story_loader


def scenario_board(story: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """生成简化 Board，只公开名称、提示和已确认线索。"""  # noqa: DOCSTRING_CJK
    props = _prop_index(story)
    clues = _clue_index(story)
    available = [props[prop_id] for prop_id in state.get("available_prop_ids") or [] if prop_id in props]
    used = [props[prop_id] for prop_id in state.get("used_prop_ids") or [] if prop_id in props]
    discovered = [clues[clue_id] for clue_id in state.get("clue_ids") or [] if clue_id in clues]
    return {
        "available_props": available,
        "used_props": used,
        "discovered_clues": discovered,
        "flags": list(state.get("flags") or []),
    }


def scenario_trace(
    *,
    progress_kind: str,
    choice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成前端实际展示的进度类型与玩家选择。"""  # noqa: DOCSTRING_CJK
    choice = choice or {}
    return {
        "progress_kind": progress_kind,
        "action_label": str(choice.get("label") or ""),
    }


def public_response(
    *,
    session: dict[str, Any],
    story: dict[str, Any],
    scene: dict[str, Any],
    narration: str,
    dialogue: str,
    trace: dict[str, Any] | None,
    ending: dict[str, Any],
    can_resume: bool,
) -> dict[str, Any]:
    """统一组装启动、回合和恢复响应。"""  # noqa: DOCSTRING_CJK
    state = session.get("story_state") if isinstance(session.get("story_state"), dict) else {}
    # 推荐对白必须使用当前 Session 的真实猫娘名，恢复页面时也不能退回作者占位符。
    options = (
        story_graph.suggestion_options(
            story,
            state,
            lanlan_name=str(session.get("lanlan_name") or "猫娘"),
        )
        if can_resume
        else []
    )
    public_options = [
        {key: option[key] for key in ("choice_id", "label", "choice_mode")}
        for option in options
    ]
    response = {
        "ok": True,
        "session_id": str(session.get("session_id") or ""),
        "story_id": str(session.get("story_id") or ""),
        "state_revision": int(session.get("state_revision") or 0),
        "phase": str(session.get("phase") or "setup"),
        "scene": story_loader.public_scene(scene),
        "narration": {"text": str(narration or "")},
        "dialogue": {"text": str(dialogue or "")},
        "scenario_board": scenario_board(story, state),
        "scenario_trace": trace,
        "suggestion_options": public_options,
        "ending": dict(ending),
        "can_resume": bool(can_resume),
        "stale": False,
    }
    return response


def _prop_index(story: dict[str, Any]) -> dict[str, dict[str, str]]:
    """索引轻量 stage_props 的公开字段。"""  # noqa: DOCSTRING_CJK
    result: dict[str, dict[str, str]] = {}
    for prop in story.get("stage_props") or []:
        if isinstance(prop, dict) and prop.get("id"):
            result[str(prop["id"])] = {
                "id": str(prop["id"]),
                "label": str(prop.get("label") or prop["id"]),
                "public_hint": str(prop.get("public_hint") or ""),
            }
    return result


def _clue_index(story: dict[str, Any]) -> dict[str, dict[str, str]]:
    """索引线索公开文字，忽略 hidden_meaning。"""  # noqa: DOCSTRING_CJK
    result: dict[str, dict[str, str]] = {}
    for clue in story.get("clues") or []:
        if isinstance(clue, dict) and clue.get("id"):
            result[str(clue["id"])] = {
                "id": str(clue["id"]),
                "title": str(clue.get("title") or clue["id"]),
                "public_text": str(clue.get("public_text") or ""),
            }
    return result
