"""提供不提交权威状态的小剧场确定性安全回退。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any

from .llm_performance_guard import _exposes_internal_runtime_detail
from .llm_response_contracts import _FORBIDDEN_OUTPUT_TERMS


def _authored_performance_fallback(
    fallback: dict[str, Any],
    node: dict[str, Any],
    progress_kind: str,
) -> dict[str, Any]:
    """纠错失败时恢复作者台词，避免场景笔记把必要的剧情交接替换成泛化回应。"""  # noqa: DOCSTRING_CJK
    if progress_kind not in {"opening", "graph_progress"}:
        return fallback
    author_fallback = dict(fallback)
    scripted_dialogue = str(node.get("scripted_dialogue") or "").strip()
    if scripted_dialogue:
        author_fallback["dialogue"] = scripted_dialogue
    return author_fallback


def _bounded_public_fallback_anchor(value: Any, *, max_chars: int = 96) -> str:
    """清洗公开短锚点；只做边界保护，不按题材、情绪或关键词推断剧情。"""  # noqa: DOCSTRING_CJK
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return ""
    lowered = normalized.lower()
    if any(term.lower() in lowered for term in _FORBIDDEN_OUTPUT_TERMS):
        return ""
    # 公开回退不应复述看起来像服务端稳定引用的值，即使它来自被篡改的 Story 文本。
    if re.search(r"(?i)\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", normalized):
        return ""
    return normalized[:max_chars]


def _fallback_scene_prefix(
    scene: dict[str, Any] | None = None, *, scene_title: str = ""
) -> str:
    """把当前公开 Scene 标题转成确定性对白前缀；没有安全标题时返回空串。"""  # noqa: DOCSTRING_CJK
    title = _bounded_public_fallback_anchor(
        scene_title or (scene.get("title") if isinstance(scene, dict) else ""),
        max_chars=48,
    )
    return f"我们还在「{title}」这里。" if title else ""


def fallback_turn(
    *,
    lanlan_name: str,
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    has_scene_notes: bool = False,
    recent_turns: list[dict[str, Any]] | None = None,
    choice_options: list[dict[str, Any]] | None = None,
    completed_branch_recall: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """使用作者文本生成离线演绎，确保模型故障时仍能继续游戏。"""  # noqa: DOCSTRING_CJK
    name = str(lanlan_name or "Lan").strip() or "Lan"
    if progress_kind == "roleplay_response":
        message = str(user_message or "").strip()
        if not message:
            dialogue = f"{name}还在这里喵。"
        else:
            scene_prefix = _fallback_scene_prefix(scene)
            if completed_branch_recall:
                continuity = "刚才已经发生的事我记得，不会把它抹掉；"
            elif choice_options:
                continuity = "眼前的下一步还没有替你决定；"
            elif recent_turns or has_scene_notes:
                continuity = "我没有忘记我们刚才说到哪里；"
            else:
                continuity = "我听见了；"
            dialogue = scene_prefix + continuity + "先让我理清楚，再好好回应你喵。"
        # 模型不可用时不得猜测玩家是否完成 Choice；保守停留是自然语言推进的安全底线。
        return {
            "narration": "",
            "dialogue": dialogue,
            "choice_rewrites": [],
        }
    narration = str(callback or node.get("summary") or scene.get("text") or "").strip()
    # scripted_dialogue 是作者可播放正文；runtime_generation_guide 只是内部约束，
    # 即使模型故障也不能把第三人称演绎指令或框架固定口癖冒充角色台词。
    dialogue = str(node.get("scripted_dialogue") or "").strip()
    return {
        "narration": narration,
        "dialogue": dialogue,
        "choice_rewrites": [],
    }


def fallback_branch_turn(
    *,
    lanlan_name: str,
    scene: dict[str, Any],
    user_message: str,
    activity_summary: str = "",
    has_committed_progress: bool = False,
    private_identifiers: set[str] | None = None,
) -> dict[str, Any]:
    """返回不提交事实的技术降级回应；内部标记只供事务层选择无预算路径。"""  # noqa: DOCSTRING_CJK
    scene_prefix = _fallback_scene_prefix(scene)
    activity = _bounded_public_fallback_anchor(activity_summary)
    if _exposes_internal_runtime_detail(activity, private_identifiers or set()):
        activity = ""
    activity_prefix = f"关于“{activity}”这件事，" if activity else ""
    continuity = (
        "刚才已经发生的进展都还算数，下一步也没有替你决定；"
        if has_committed_progress
        else "下一步还没有替你完成；"
    )
    # 模型或合同失败时绝不猜测动作结果；技术故障不能冒充玩家没有推进并消耗作者预算。
    return {
        "narration": "",
        "dialogue": scene_prefix
        + activity_prefix
        + continuity
        + "先让我理清楚，再继续回应你喵。",
        "fact_candidates": [],
        "turn_delivery": "technical_degraded",
    }


def fallback_branch_entry(
    *,
    scene_title: str = "",
    activity_summary: str = "",
    private_identifiers: set[str] | None = None,
) -> dict[str, Any]:
    """只用公开场景与行动方向返回无新增事实的通用支线安全演出。"""  # noqa: DOCSTRING_CJK
    scene_prefix = _fallback_scene_prefix(scene_title=scene_title)
    activity = _bounded_public_fallback_anchor(activity_summary)
    if _exposes_internal_runtime_detail(activity, private_identifiers or set()):
        activity = ""
    activity_prefix = f"你想做的“{activity}”已经确认从这里开始；" if activity else ""
    # 固定对白只确认入口方向，不代做动作、不补充物件，也不依赖任何当前剧本题材。
    return {
        "narration": "",
        "dialogue": scene_prefix
        + activity_prefix
        + "后面的事还没有发生，我们只从眼前这一步继续喵。",
        "choice_rewrites": [],
    }


def fallback_branch_handoff(
    *,
    scene_title: str = "",
    activity_summary: str = "",
    private_identifiers: set[str] | None = None,
) -> dict[str, Any]:
    """为显式转交回合返回不抢跑新行动的固定安全演出。"""  # noqa: DOCSTRING_CJK
    scene_prefix = _fallback_scene_prefix(scene_title=scene_title)
    activity = _bounded_public_fallback_anchor(activity_summary)
    if _exposes_internal_runtime_detail(activity, private_identifiers or set()):
        activity = ""
    activity_text = (
        f"“{activity}”先停在这里。" if activity else "刚才那件事先停在这里。"
    )
    return {
        "narration": "",
        "dialogue": scene_prefix
        + activity_text
        + "你的新想法还没有开始，我会先和你确认清楚再行动喵。",
        "choice_rewrites": [],
    }
