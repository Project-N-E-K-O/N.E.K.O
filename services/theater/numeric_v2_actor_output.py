"""Numeric v2 Actor 的纯文本比较与输出合同校验。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from difflib import SequenceMatcher
import json
from typing import Any, Mapping

from .numeric_v2_performance import mixed_performance_blocks


def _strip_inline_markdown(value: str) -> str:
    """可见演绎是纯文本协议，移除模型偶发输出的行内 Markdown 标记。"""  # noqa: DOCSTRING_CJK

    return value.replace("**", "").replace("__", "").replace("`", "")


class NumericV2ActorError(RuntimeError):
    """Actor 无法提供可提交的演绎正文。"""  # noqa: DOCSTRING_CJK


class NumericV2ActorUnavailableError(NumericV2ActorError):
    """Actor 的模型配置当前不可用。"""  # noqa: DOCSTRING_CJK


class NumericV2ActorOutputError(NumericV2ActorError):
    """Actor 返回内容没有通过确定性输出合同。"""  # noqa: DOCSTRING_CJK


def _sentence_units(value: Any) -> list[str]:
    """按完整句拆分表现文本，避免工作记忆留下诱导模型补全的半句话。"""  # noqa: DOCSTRING_CJK

    text = str(value or "").strip()
    if not text:
        return []
    units: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in "。！？!?\n":
            unit = text[start:index + 1].strip()
            if unit:
                units.append(unit)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return units


def _normalized_comparison_text(value: Any) -> str:
    """去掉不影响语义比较的空白和标点，保留否定词与正文。"""  # noqa: DOCSTRING_CJK

    return "".join(
        character
        for character in str(value or "")
        if character.isalnum() or character == "_"
    ).casefold()


def _text_is_covered(
    value: Any,
    references: list[str],
    *,
    similarity: float,
    common_span: int = 0,
    strong_common_span: int = 0,
) -> bool:
    """判断一条文本是否已经被任一完整参考句覆盖。"""  # noqa: DOCSTRING_CJK

    candidate = _normalized_comparison_text(value)
    if not candidate:
        return False
    for reference in references:
        normalized_reference = _normalized_comparison_text(reference)
        if not normalized_reference:
            continue
        if candidate in normalized_reference or normalized_reference in candidate:
            return True
        matcher = SequenceMatcher(None, candidate, normalized_reference)
        ratio = matcher.ratio()
        if ratio >= similarity:
            return True
        if strong_common_span > 0 and matcher.find_longest_match().size >= strong_common_span:
            return True
        if (
            common_span > 0
            and ratio >= 0.32
            and matcher.find_longest_match().size >= common_span
        ):
            return True
    return False


def _require_block_types(
    blocks: list[dict[str, str]],
    *,
    narration: bool = False,
    dialogue: bool = False,
) -> None:
    """校验混合正文是否包含当前阶段要求的动作与对白。"""  # noqa: DOCSTRING_CJK

    types = {block["type"] for block in blocks}
    if narration and "action" not in types:
        raise NumericV2ActorOutputError("numeric_v2_actor_narration_required")
    if dialogue and "dialogue" not in types:
        raise NumericV2ActorOutputError("numeric_v2_actor_dialogue_required")


def _parse_mixed_performance(
    value: Any,
    *,
    require_narration: bool,
    dialogue_policy: str = "required",
) -> str:
    """校验模型的单字段混合正文；Session 只保存校验后的原始顺序。"""  # noqa: DOCSTRING_CJK

    if not isinstance(value, str) or not value.strip():
        raise NumericV2ActorOutputError("numeric_v2_actor_performance_invalid")
    text = _strip_inline_markdown(value.strip())
    blocks = mixed_performance_blocks(text)
    if not blocks:
        raise NumericV2ActorOutputError("numeric_v2_actor_performance_invalid")
    if dialogue_policy not in {"required", "optional", "forbidden"}:
        raise NumericV2ActorOutputError("numeric_v2_actor_dialogue_policy_invalid")
    _require_block_types(
        blocks,
        narration=require_narration,
        dialogue=dialogue_policy == "required",
    )
    if (
        dialogue_policy == "forbidden"
        and any(block["type"] == "dialogue" for block in blocks)
    ):
        raise NumericV2ActorOutputError("numeric_v2_actor_dialogue_forbidden")
    return text


def _parse_transition_performance(
    value: Any,
    *,
    dialogue_policy: str = "required",
) -> str:
    """兼容模型在换场分段中偶发返回的旧式 action/dialogue 对象。"""  # noqa: DOCSTRING_CJK

    if isinstance(value, Mapping) and set(value) == {"action", "dialogue"}:
        action = value.get("action")
        dialogue = value.get("dialogue")
        if not isinstance(action, str) or not isinstance(dialogue, str):
            raise NumericV2ActorOutputError("numeric_v2_actor_performance_invalid")
        # 这里只做确定性的旧形状归一化，不补写正文，也不增加第二次 Actor 调用。
        normalized_action = action.strip()
        if normalized_action.startswith("(") and normalized_action.endswith(")"):
            normalized_action = "（" + normalized_action[1:-1] + "）"
        value = normalized_action + dialogue.strip()
    return _parse_mixed_performance(
        value,
        require_narration=False,
        dialogue_policy=dialogue_policy,
    )


def _parse_scene_narration(value: Any) -> str:
    """场景旁白必须是非空字符串，空桥段只允许在去重完成后形成。"""  # noqa: DOCSTRING_CJK

    if not isinstance(value, str) or not value.strip():
        raise NumericV2ActorOutputError("numeric_v2_actor_scene_narration_invalid")
    return _strip_inline_markdown(value.strip())


def _parse_scene_update(value: Any) -> str:
    """普通回合的场景更新沿用旁白存储；空字符串等价于省略可选字段。"""  # noqa: DOCSTRING_CJK

    if not isinstance(value, str):
        raise NumericV2ActorOutputError("numeric_v2_actor_scene_narration_invalid")
    return _strip_inline_markdown(value.strip())


def _deduplicate_transition_bridge(
    value: Any,
    target_opening: str,
) -> str:
    """移除与 Runtime 目标开场重复的桥接句，保留来源场景的收束过程。"""  # noqa: DOCSTRING_CJK

    bridge = _parse_scene_narration(value)
    opening = str(target_opening or "").strip()
    if not opening:
        return bridge
    opening_units = _sentence_units(opening)
    kept = [
        unit
        for unit in _sentence_units(bridge)
        if not _text_is_covered(
            unit,
            opening_units,
            similarity=0.55,
            common_span=3,
            strong_common_span=5,
        )
    ]
    # 模型只复述目标开场时不制造系统式占位旁白；前端会直接进入确定性目标开场。
    return "".join(kept).strip()


def _normalize_suggestion_quotes(value: str) -> str:
    """把成对英文双引号规范成中文引号，避免推荐输入视觉格式漂移。"""  # noqa: DOCSTRING_CJK

    if not value or value.count('"') == 0 or value.count('"') % 2:
        return value
    opening = True
    normalized: list[str] = []
    for char in value:
        if char != '"':
            normalized.append(char)
            continue
        normalized.append("“" if opening else "”")
        opening = not opening
    return "".join(normalized)


def _parse_actor_suggestions(
    value: Any,
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[str]:
    """解析 Actor 同次生成的玩家输入；格式异常时只降级推荐，不丢弃合法正文。"""  # noqa: DOCSTRING_CJK

    parse_counts = {
        "container_invalid": 0,
        "too_many_items": 0,
        "empty_item": 0,
        "too_long_item": 0,
        "duplicate_item": 0,
        "placeholder_item": 0,
        "mixed_shape_invalid": 0,
        "action_owner_invalid": 0,
        "accepted_items": 0,
        "insufficient_valid_items": 0,
    }
    if not isinstance(value, list):
        parse_counts["container_invalid"] = 1
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(parse_counts)
        return []
    if len(value) > 3:
        # 推荐合同是 2—3 条；超出上限不能静默截断，交给唯一一次轻量补推荐处理。
        parse_counts["too_many_items"] = 1
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(parse_counts)
        return []
    parsed: list[str] = []
    for item in value:
        text = _normalize_suggestion_quotes(str(item or "").strip())
        # 推荐会直接显示并可一键发送，模板占位符不能泄漏给玩家；正文仍由调用方保留。
        if not text:
            parse_counts["empty_item"] += 1
            continue
        if len(text) > 120:
            parse_counts["too_long_item"] += 1
            continue
        if text in parsed:
            parse_counts["duplicate_item"] += 1
            continue
        if "[" in text or "]" in text:
            parse_counts["placeholder_item"] += 1
            continue
        blocks = mixed_performance_blocks(text)
        if [block.get("type") for block in blocks] != ["action", "dialogue"]:
            parse_counts["mixed_shape_invalid"] += 1
            continue
        action = str(blocks[0].get("text") or "").strip()
        dialogue = str(blocks[1].get("text") or "").strip()
        # suggested_inputs 后续会由程序作为 player_input/input_text 存储，
        # 中文动作可以自然省略“我”；只拒绝明确把第三方写成动作主体。
        explicit_nonplayer_prefixes = (
            "你",
            "您",
            "她",
            "他",
            "猫娘",
            "女主",
            "男主",
            "环境",
        )
        if action.startswith(explicit_nonplayer_prefixes) or not dialogue:
            parse_counts["action_owner_invalid"] += 1
            continue
        parsed.append(text)
        parse_counts["accepted_items"] += 1
    # 少于两条时由 Actor 的一次轻量补推荐决定是否重试；这里保留合法正文。
    if len(parsed) not in {2, 3}:
        parse_counts["insufficient_valid_items"] = 1
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(parse_counts)
    return parsed if len(parsed) in {2, 3} else []


def _parse_transition_offered(value: Any) -> bool:
    """校验 Actor 是否在可见正文中声明了具体下一步；不在此处猜测自然语言。"""  # noqa: DOCSTRING_CJK

    if not isinstance(value, bool):
        raise NumericV2ActorOutputError("numeric_v2_actor_transition_offered_invalid")
    return value


def _parse_output(
    content: Any,
    *,
    opening_required: bool = False,
    transition_required: bool = False,
    deterministic_transition: bool = False,
    target_opening: str = "",
    dialogue_policy: str = "required",
    source_dialogue_policy: str = "required",
    target_dialogue_policy: str = "required",
    suggestions_only: bool = False,
    transition_suggestions_only: bool = False,
    suggestion_diagnostics: dict[str, int] | None = None,
) -> dict[str, Any]:
    """把单次 Actor 返回解析为唯一合法的开场、普通回合或换场形状。"""  # noqa: DOCSTRING_CJK

    if not isinstance(content, str) or not content.strip():
        raise NumericV2ActorOutputError("numeric_v2_actor_empty_output")
    try:
        payload = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise NumericV2ActorOutputError("numeric_v2_actor_invalid_json") from exc
    if not isinstance(payload, dict):
        raise NumericV2ActorOutputError("numeric_v2_actor_fields_invalid")
    if transition_suggestions_only:
        # 转场补推荐把接受路径独立成字段，代码再确定性放到第一位；
        # 这样模型不能把“先核对/再看看”等暂缓选项误排为默认接受路径。
        if set(payload) != {"accept_input", "alternative_inputs"}:
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        alternatives = payload.get("alternative_inputs")
        if not isinstance(alternatives, list) or len(alternatives) not in {1, 2}:
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        suggestions = _parse_actor_suggestions(
            [payload.get("accept_input"), *alternatives],
            diagnostics=suggestion_diagnostics,
        )
        return {"suggested_inputs": suggestions}
    if suggestions_only:
        # 补推荐调用只允许返回推荐字段，任何正文都不会覆盖前一次合法结果。
        if set(payload) != {"suggested_inputs"}:
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        return {
            "suggested_inputs": _parse_actor_suggestions(
                payload.get("suggested_inputs"),
                diagnostics=suggestion_diagnostics,
            )
        }
    if opening_required:
        expected_fields = {"scene_narration", "performance"}
        # 推荐或转场字段缺失时交给一次轻量补推荐；已合法的正文不会被丢弃。
        tolerated_fields = {*expected_fields, "suggested_inputs", "transition_offered"}
        optional_legacy_fields = {
            frozenset(expected_fields),
            frozenset({*expected_fields, "suggested_inputs"}),
            frozenset(tolerated_fields),
        }
        if set(payload) not in optional_legacy_fields:
            raise NumericV2ActorOutputError("numeric_v2_actor_fields_invalid")
        result = {
            "scene_narration": _parse_scene_narration(payload.get("scene_narration")),
            "performance": _parse_mixed_performance(
                payload.get("performance"),
                require_narration=False,
                dialogue_policy=dialogue_policy,
            ),
        }
        result["suggested_inputs"] = _parse_actor_suggestions(
            payload.get("suggested_inputs"),
            diagnostics=suggestion_diagnostics,
        )
        result["transition_offered"] = _parse_transition_offered(
            payload.get("transition_offered", False)
        )
        return result
    if transition_required and deterministic_transition:
        # Runtime 仍确定段位和顺序；旁白按真实历史生成，缺字段不能退回会复演的作者原文。
        expected_fields = {"source_performance", "target_performance",
                           "bridge_scene_narration", "target_scene_narration"}
        tolerated_fields = {*expected_fields, "suggested_inputs"}
        if set(payload) not in {frozenset(expected_fields), frozenset(tolerated_fields)}:
            raise NumericV2ActorOutputError("numeric_v2_actor_transition_required")
        result = {
            "bridge_scene_narration": _parse_scene_narration(payload.get("bridge_scene_narration")),
            "target_scene_narration": _parse_scene_narration(payload.get("target_scene_narration")),
            "source_performance": _parse_transition_performance(
                payload.get("source_performance"),
                dialogue_policy=source_dialogue_policy,
            ),
            "target_performance": _parse_transition_performance(
                payload.get("target_performance"),
                dialogue_policy=target_dialogue_policy,
            ),
        }
        result["suggested_inputs"] = _parse_actor_suggestions(
            payload.get("suggested_inputs"),
            diagnostics=suggestion_diagnostics,
        )
        return result
    if transition_required:
        expected_fields = {"segments"}
        tolerated_fields = {*expected_fields, "suggested_inputs"}
        if set(payload) not in {frozenset(expected_fields), frozenset(tolerated_fields)}:
            raise NumericV2ActorOutputError("numeric_v2_actor_transition_required")
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list) or len(raw_segments) != 3:
            raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
        expected_phases = ("source_response", "transition_bridge", "target_opening")
        segments = []
        for index, raw_segment in enumerate(raw_segments):
            if not isinstance(raw_segment, Mapping):
                raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
            # phase 是模型标签，不作为权限依据；固定位置和互斥字段形状共同确定真实段位。
            if not isinstance(raw_segment.get("phase"), str) or not raw_segment["phase"].strip():
                raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
            if index == 0:
                if set(raw_segment) != {"phase", "performance"}:
                    raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
                segments.append({
                    "phase": expected_phases[index],
                    "performance": _parse_transition_performance(
                        raw_segment.get("performance"),
                        dialogue_policy=source_dialogue_policy,
                    ),
                })
                continue
            if index == 1:
                if set(raw_segment) != {"phase", "scene_narration"}:
                    raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
                segments.append({
                    "phase": expected_phases[index],
                    "scene_narration": _deduplicate_transition_bridge(
                        raw_segment.get("scene_narration"),
                        target_opening,
                    ),
                })
                continue
            # 目标段即使附带 scene_narration 也不采信；目标开场只能由 Runtime 提供。
            if set(raw_segment) not in (
                {"phase", "performance"},
                {"phase", "scene_narration", "performance"},
            ):
                raise NumericV2ActorOutputError("numeric_v2_actor_transition_segments_invalid")
            segments.append({
                "phase": expected_phases[index],
                "performance": _parse_transition_performance(
                    raw_segment.get("performance"),
                    dialogue_policy=target_dialogue_policy,
                ),
            })
        return {
            "suggested_inputs": _parse_actor_suggestions(
                payload.get("suggested_inputs"),
                diagnostics=suggestion_diagnostics,
            ),
            "segments": segments,
        }
    allowed_fields = {"performance"}
    if "scene_update" in payload:
        allowed_fields.add("scene_update")
    tolerated_fields = {*allowed_fields, "suggested_inputs", "transition_offered"}
    optional_legacy_fields = {
        frozenset(allowed_fields),
        frozenset({*allowed_fields, "suggested_inputs"}),
        frozenset(tolerated_fields),
    }
    if set(payload) not in optional_legacy_fields:
        raise NumericV2ActorOutputError("numeric_v2_actor_fields_invalid")
    result = {
        "performance": _parse_mixed_performance(
            payload.get("performance"),
            # 普通回合允许纯对白；只有对白是必需项，括号微动作不应被格式层强制生成。
            require_narration=False,
            dialogue_policy=dialogue_policy,
        ),
        "suggested_inputs": _parse_actor_suggestions(
            payload.get("suggested_inputs"),
            diagnostics=suggestion_diagnostics,
        ),
        "transition_offered": _parse_transition_offered(
            payload.get("transition_offered", False)
        ),
    }
    if "scene_update" in payload:
        # Runtime 继续使用既有 scene_narration 字段保存，前端与旧 Session 无需新增协议分支。
        scene_update = _parse_scene_update(payload.get("scene_update"))
        if scene_update:
            result["scene_narration"] = scene_update
    return result


__all__ = [
    "NumericV2ActorError",
    "NumericV2ActorOutputError",
    "NumericV2ActorUnavailableError",
]
