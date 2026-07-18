"""校验小剧场各模型职责的结构化输出与稳定降级对象。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import math
from typing import Any

from config import THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS
from utils.file_utils import robust_json_loads


THEATER_BRANCH_HANDOFF_MIN_CONFIDENCE = 0.85
THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE = 0.65
THEATER_BRANCH_HANDOFF_SUMMARY_MAX_CHARS = 160
THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS = 240
THEATER_BRANCH_HANDOFF_CLASSIFICATIONS = frozenset(
    {"continue_branch", "intent_handoff", "uncertain"}
)
# 置信度只用于拒绝含糊的自由意图；连续两次阈值仍由 intent_tracker 独立判断。
THEATER_FREE_INTENT_MIN_CONFIDENCE = 0.65
THEATER_FREE_INTENT_RELATIONS = frozenset({"new", "continue", "refine", "replace"})
# 复合输入的剩余意图只保存短语义与原话摘录，不能放大 Session 和 Router 上下文。
THEATER_RESIDUAL_SUMMARY_MAX_CHARS = 160
THEATER_RESIDUAL_EVIDENCE_MAX_CHARS = 240
# 回应焦点只描述本轮应先承接的公开语义，不承担路由或事实提交权。
THEATER_RESPONSE_FOCUS_TYPES = frozenset(
    {"question", "object", "action", "attitude"}
)
THEATER_RESPONSE_FOCUS_EVIDENCE_MAX_CHARS = 240
_FORBIDDEN_OUTPUT_TERMS = (
    "scene_id",
    "node_id",
    "choice_id",
    "goal_id",
    "fact_type",
    "fact_role",
    "fact_object",
    "content_id",
    "content_slot_id",
    "beat_id",
    "branch_id",
    "ending_domain_id",
    "turns_used",
    "nonprogress_turns",
    "turn_delivery",
    "回合预算",
)


def _parse_planner_output(raw: Any) -> dict[str, Any] | None:
    """只解析 Planner 的 JSON 对象；字段合同与稳定引用由 branch_contracts 统一裁决。"""  # noqa: DOCSTRING_CJK
    try:
        payload = _load_unique_model_json_object(raw)
    except Exception:
        return None
    # 列表、字符串或 null 都不是 Patch；服务层只会继续处理独立对象。
    return payload if isinstance(payload, dict) else None


def _parse_branch_turn_output(raw: Any) -> dict[str, Any] | None:
    """解析活动支线 Actor 的公开演出和无权威事实候选，合同校验留给 branch_runtime。"""  # noqa: DOCSTRING_CJK
    try:
        payload = _load_unique_model_json_object(raw)
    except Exception:
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "narration",
        "dialogue",
        "fact_candidates",
    }:
        return None
    narration = str(payload.get("narration") or "").strip()
    dialogue = str(payload.get("dialogue") or "").strip()
    combined = narration + dialogue
    if not dialogue or any(
        term.lower() in combined.lower() for term in _FORBIDDEN_OUTPUT_TERMS
    ):
        return None
    fact_candidates = payload.get("fact_candidates")
    if (
        not isinstance(fact_candidates, list)
        or len(fact_candidates) > THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS
        or any(not isinstance(item, dict) for item in fact_candidates)
    ):
        return None
    # 候选只做隔离复制，不在模型解析层补字段、改 ID 或静默修正合同错误。
    return {
        "narration": narration,
        "dialogue": dialogue,
        "fact_candidates": [dict(item) for item in fact_candidates],
    }


def _parse_branch_handoff_output(
    raw: Any, *, user_message: str
) -> dict[str, Any] | None:
    """解析无权威转交候选，并用本轮玩家原话复核两段证据。"""  # noqa: DOCSTRING_CJK
    try:
        payload = _load_unique_model_json_object(raw)
    except Exception:
        return None
    required_fields = {
        "classification",
        "intent_summary",
        "exit_evidence_excerpt",
        "next_evidence_excerpt",
        "confidence",
        "response_focus",
    }
    if not isinstance(payload, dict) or set(payload) != required_fields:
        return None
    classification = str(payload.get("classification") or "").strip()
    intent_summary = " ".join(str(payload.get("intent_summary") or "").strip().split())
    exit_excerpt = " ".join(
        str(payload.get("exit_evidence_excerpt") or "").strip().split()
    )
    next_excerpt = " ".join(
        str(payload.get("next_evidence_excerpt") or "").strip().split()
    )
    confidence = payload.get("confidence")
    raw_response_focus = payload.get("response_focus")
    if (
        classification not in THEATER_BRANCH_HANDOFF_CLASSIFICATIONS
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        return None

    if raw_response_focus == {}:
        response_focus: dict[str, Any] = {}
    else:
        response_focus = verify_response_focus(
            raw_response_focus,
            user_message=user_message,
        )
        # 非空焦点若不能由玩家本轮原话证明，则拒绝整份分类结果，不能静默降级后继续采用。
        if not response_focus:
            return None
    # 只有继续当前支线时才会调用当前支线 Actor；转交或不确定分类不得夹带回应义务。
    if classification != "continue_branch" and response_focus:
        return None

    if classification != "intent_handoff":
        # 普通继续与语义不确定都不能夹带下一意图，避免调用方误把附加文本当成状态候选。
        if (
            intent_summary
            or exit_excerpt
            or next_excerpt
            or (
                classification == "continue_branch"
                and float(confidence) < THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE
            )
        ):
            return None
        return {
            "classification": classification,
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": float(confidence),
            "response_focus": response_focus,
        }

    normalized_message = " ".join(str(user_message or "").strip().split())
    if (
        float(confidence) < THEATER_BRANCH_HANDOFF_MIN_CONFIDENCE
        or not 2 <= len(intent_summary) <= THEATER_BRANCH_HANDOFF_SUMMARY_MAX_CHARS
        or not 1 <= len(exit_excerpt) <= THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS
        or not 1 <= len(next_excerpt) <= THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS
        or exit_excerpt not in normalized_message
        or next_excerpt not in normalized_message
        or exit_excerpt == next_excerpt
        or exit_excerpt in next_excerpt
        or next_excerpt in exit_excerpt
        or any(
            term.lower() in intent_summary.lower() for term in _FORBIDDEN_OUTPUT_TERMS
        )
    ):
        return None
    return {
        "classification": "intent_handoff",
        "intent_summary": intent_summary,
        "exit_evidence_excerpt": exit_excerpt,
        "next_evidence_excerpt": next_excerpt,
        "confidence": float(confidence),
        "response_focus": {},
    }


def _parse_route_output(
    raw: Any,
    *,
    allowed_choice_ids: set[str],
    allowed_intent_ids: set[str],
    user_message: str = "",
) -> dict[str, Any] | None:
    """解析作者白名单 ID 或自由意图语义；模型永远不能提交身份与次数。"""  # noqa: DOCSTRING_CJK
    try:
        payload = _load_unique_model_json_object(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    candidate_choice = str(payload.get("matched_choice_id") or "").strip()
    matched_choice_id = (
        candidate_choice if candidate_choice in allowed_choice_ids else ""
    )
    # v2.5 对外字段改名为 authored_intent_id；旧名只为平滑读取过渡期模型输出。
    candidate_intent = str(
        payload.get("authored_intent_id") or payload.get("observed_intent_id") or ""
    ).strip()
    authored_intent_id = (
        candidate_intent
        if not matched_choice_id and candidate_intent in allowed_intent_ids
        else ""
    )
    residual_intent = _parse_residual_intent(payload.get("residual_intent"))
    response_focus = verify_response_focus(
        payload.get("response_focus"),
        user_message=user_message,
    )
    if matched_choice_id:
        # 推荐 Choice 是最高优先级；只有严格合法的后半句摘要可以随该 Choice 进入待重验状态。
        return {
            "route_kind": "authored_choice",
            "matched_choice_id": matched_choice_id,
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": residual_intent,
            "response_focus": response_focus,
        }
    if authored_intent_id:
        return {
            "route_kind": "authored_intent",
            "matched_choice_id": "",
            "authored_intent_id": authored_intent_id,
            "free_intent": {},
            "residual_intent": {},
            "response_focus": response_focus,
        }

    fallback = _empty_route_result()
    if str(payload.get("route_kind") or "").strip() != "free_intent":
        # 普通同节点互动本来就落在 idle；合法焦点仍可独立帮助 Actor 回应，但不获得任何状态权威。
        fallback["response_focus"] = response_focus
        return fallback
    free_intent = payload.get("free_intent")
    # 精确字段集会直接拒绝 intent_key、streak 等模型越权字段，也避免未来字段被静默当成状态。
    if not isinstance(free_intent, dict) or set(free_intent) != {
        "summary",
        "relation",
        "confidence",
    }:
        return fallback
    summary = " ".join(str(free_intent.get("summary") or "").strip().split())
    relation = str(free_intent.get("relation") or "").strip()
    confidence = free_intent.get("confidence")
    if (
        not 2 <= len(summary) <= 160
        or relation not in THEATER_FREE_INTENT_RELATIONS
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not THEATER_FREE_INTENT_MIN_CONFIDENCE <= float(confidence) <= 1.0
    ):
        return fallback
    return {
        "route_kind": "free_intent",
        "matched_choice_id": "",
        "authored_intent_id": "",
        "free_intent": {
            "summary": summary,
            "relation": relation,
            "confidence": float(confidence),
        },
        "residual_intent": {},
        "response_focus": response_focus,
    }


def verify_response_focus(value: Any, *, user_message: Any) -> dict[str, Any]:
    """只接受能由本轮完整玩家原话证明的有界回应焦点。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict) or set(value) != {
        "focus_type",
        "evidence_excerpt",
        "requires_state_change",
    }:
        return {}
    focus_type = str(value.get("focus_type") or "").strip()
    evidence_excerpt = " ".join(
        str(value.get("evidence_excerpt") or "").strip().split()
    )
    normalized_message = " ".join(str(user_message or "").strip().split())
    requires_state_change = value.get("requires_state_change")
    if (
        focus_type not in THEATER_RESPONSE_FOCUS_TYPES
        or not 1
        <= len(evidence_excerpt)
        <= THEATER_RESPONSE_FOCUS_EVIDENCE_MAX_CHARS
        or evidence_excerpt not in normalized_message
        or not isinstance(requires_state_change, bool)
    ):
        return {}
    return {
        "focus_type": focus_type,
        "evidence_excerpt": evidence_excerpt,
        "requires_state_change": requires_state_change,
    }


def _parse_residual_intent(value: Any) -> dict[str, str]:
    """只接受 Choice 后可分离的短语义，不允许模型附带任何状态权威。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, dict) or set(value) != {"summary", "evidence_excerpt"}:
        return {}
    summary = " ".join(str(value.get("summary") or "").strip().split())
    evidence_excerpt = " ".join(
        str(value.get("evidence_excerpt") or "").strip().split()
    )
    if (
        not 2 <= len(summary) <= THEATER_RESIDUAL_SUMMARY_MAX_CHARS
        or not 1 <= len(evidence_excerpt) <= THEATER_RESIDUAL_EVIDENCE_MAX_CHARS
    ):
        return {}
    return {"summary": summary, "evidence_excerpt": evidence_excerpt}


def _empty_route_result() -> dict[str, Any]:
    """返回新的保守路由结果，避免调用方共享可变意图字典。"""  # noqa: DOCSTRING_CJK
    return {
        "route_kind": "idle",
        "matched_choice_id": "",
        "authored_intent_id": "",
        "free_intent": {},
        "residual_intent": {},
        "response_focus": {},
    }


def _technical_route_fallback() -> dict[str, Any]:
    """标记 Router 基础设施降级，避免调用方把技术故障误算成玩家语义 idle。"""  # noqa: DOCSTRING_CJK
    result = _empty_route_result()
    # 该字段不属于模型合同，只能由服务端失败路径生成，并在回合事务内消费。
    result["route_delivery"] = "technical_degraded"
    return result


def _technical_branch_handoff_fallback() -> dict[str, Any]:
    """返回不改变支线状态的技术降级分类，且每次创建独立字典。"""  # noqa: DOCSTRING_CJK
    return {
        "classification": "uncertain",
        "intent_summary": "",
        "exit_evidence_excerpt": "",
        "next_evidence_excerpt": "",
        "confidence": 0.0,
        "response_focus": {},
        "route_delivery": "technical_degraded",
    }


def _parse_output(
    raw: Any,
    *,
    progress_kind: str,
) -> dict[str, Any] | None:
    """解析演绎模型 JSON；该阶段不再拥有任何剧情路由字段。"""  # noqa: DOCSTRING_CJK
    try:
        payload = _load_unique_model_json_object(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    narration = str(payload.get("narration") or "").strip()
    dialogue = str(payload.get("dialogue") or "").strip()
    combined = narration + dialogue
    if not dialogue or any(
        term.lower() in combined.lower() for term in _FORBIDDEN_OUTPUT_TERMS
    ):
        return None
    if progress_kind not in {"roleplay_response", "branch_entry"} and not narration:
        return None
    return {
        "narration": narration,
        "dialogue": dialogue,
        # 字段仅保留旧模型 JSON 形状兼容；静态 Choice 文案始终由 Story 作者控制。
        "choice_rewrites": [],
    }


def _load_unique_model_json_object(raw: Any) -> dict[str, Any]:
    """读取唯一 JSON 对象；允许外围说明，但拒绝零个或多个竞争对象。"""  # noqa: DOCSTRING_CJK
    text = str(raw or "").strip()
    if text.startswith("```"):
        # 标准 JSON 围栏仍走最快路径；非标准围栏会由下方唯一对象扫描处理。
        text = text.strip("`").removeprefix("json").strip()
    try:
        direct = robust_json_loads(text)
    except Exception:
        direct = None
    if isinstance(direct, dict):
        return direct

    candidates: list[dict[str, Any]] = []
    for fragment in _balanced_json_object_fragments(text):
        try:
            candidate = robust_json_loads(fragment)
        except Exception:
            continue
        if isinstance(candidate, dict):
            candidates.append(candidate)
    if len(candidates) != 1:
        # 多对象输出可能表达互相竞争的路由或事实，不能静默挑第一个。
        raise ValueError("model output must contain exactly one JSON object")
    return candidates[0]


def _balanced_json_object_fragments(text: str) -> list[str]:
    """在不查看字段内容语义的前提下扫描字符串外的平衡花括号片段。"""  # noqa: DOCSTRING_CJK
    fragments: list[str] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(str(text or "")):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                fragments.append(text[start : index + 1])
                start = -1
    return fragments
