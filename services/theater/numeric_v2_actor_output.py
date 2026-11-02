"""Numeric v2 Actor plain-text comparison and output-contract validation."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from difflib import SequenceMatcher
import json
from typing import Any, Mapping

from .numeric_v2_json import strip_single_json_fence
from .numeric_v2_performance import mixed_performance_blocks


def _strip_inline_markdown(value: str) -> str:
    """Visible performance is a plain-text protocol; remove incidental inline Markdown markers from model output."""  # noqa: DOCSTRING_CJK

    return value.replace("**", "").replace("__", "").replace("`", "")


class NumericV2ActorError(RuntimeError):
    """The Actor could not provide performance text that can be submitted."""  # noqa: DOCSTRING_CJK


class NumericV2ActorUnavailableError(NumericV2ActorError):
    """The Actor's model configuration is currently unavailable."""  # noqa: DOCSTRING_CJK


class NumericV2ActorOutputError(NumericV2ActorError):
    """The Actor's response failed deterministic output-contract validation."""  # noqa: DOCSTRING_CJK


def _sentence_units(value: Any) -> list[str]:
    """Split performance text into complete sentences so working memory does not leave a half-sentence that could prompt the model to continue it."""  # noqa: DOCSTRING_CJK

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
    """Remove whitespace and punctuation that do not affect semantic comparison; preserve negation and body text."""  # noqa: DOCSTRING_CJK

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
    """Check whether any complete reference sentence already covers a text."""  # noqa: DOCSTRING_CJK

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
    """Check whether mixed body text includes the action and dialogue required by the current stage."""  # noqa: DOCSTRING_CJK

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
    """Validate the model's single mixed-body field; Session stores only the validated original order."""  # noqa: DOCSTRING_CJK

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
    """Support the legacy action/dialogue object that the model occasionally returns for a split scene transition."""  # noqa: DOCSTRING_CJK

    if isinstance(value, Mapping) and set(value) == {"action", "dialogue"}:
        action = value.get("action")
        dialogue = value.get("dialogue")
        if not isinstance(action, str) or not isinstance(dialogue, str):
            raise NumericV2ActorOutputError("numeric_v2_actor_performance_invalid")
        # Normalize only deterministic legacy shapes here; do not rewrite body text or make a second Actor call.
        normalized_action = action.strip()
        if normalized_action.startswith("(") and normalized_action.endswith(")"):
            normalized_action = "（" + normalized_action[1:-1] + "）"
        elif normalized_action and not (normalized_action.startswith("（") and normalized_action.endswith("）")):
            normalized_action = "（" + normalized_action + "）"
        value = normalized_action + dialogue.strip()
    return _parse_mixed_performance(
        value,
        require_narration=False,
        dialogue_policy=dialogue_policy,
    )


def _parse_scene_narration(value: Any, *, allow_empty: bool = False) -> str:
    """Only beats explicitly permitted by the runtime contract may be empty; openings and other narration must remain non-empty."""  # noqa: DOCSTRING_CJK

    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise NumericV2ActorOutputError("numeric_v2_actor_scene_narration_invalid")
    return _strip_inline_markdown(value.strip())


def _parse_scene_update(value: Any) -> str:
    """Ordinary-turn scene updates continue to use narration storage; an empty string is equivalent to omitting the optional field."""  # noqa: DOCSTRING_CJK

    if not isinstance(value, str):
        raise NumericV2ActorOutputError("numeric_v2_actor_scene_narration_invalid")
    return _strip_inline_markdown(value.strip())


def _normalize_suggestion_quotes(value: str) -> str:
    """Normalize paired English double quotes to Chinese quotation marks to prevent visual drift in suggested input formatting."""  # noqa: DOCSTRING_CJK

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
    """Parse player inputs generated in the same Actor response; on malformed formatting, fall back only for recommendations and preserve valid body text."""  # noqa: DOCSTRING_CJK

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
        # The recommended contract is 2–3 items. If there are more, keep the first three instead of discarding the entire result: these are valid buttons written by the actor,
        # and discarding them would needlessly trigger another recommendation call (or leave all buttons empty). Only trim deterministically;
        # do not invent content for the model.
        parse_counts["too_many_items"] = 1
        value = value[:3]
    parsed: list[str] = []
    for item in value:
        text = _normalize_suggestion_quotes(str(item or "").strip())
        # Recommendations are shown directly and can be sent with one click, so template placeholders must not leak to the player; the caller retains the body text.
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
        if [block.get("type") for block in blocks] not in (["action"], ["action", "dialogue"]):
            parse_counts["mixed_shape_invalid"] += 1
            continue
        action = str(blocks[0].get("text") or "").strip()
        if action in {"玩家动作", "玩家对白", "动作", "对白"}:
            parse_counts["placeholder_item"] += 1
            continue
        # suggested_inputs will later be stored by the program as player_input/input_text,
        # so Chinese actions may naturally omit "I"; reject only cases that explicitly make a third party the action's subject.
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
        if action.startswith(explicit_nonplayer_prefixes):
            parse_counts["action_owner_invalid"] += 1
            continue
        parsed.append(text)
        parse_counts["accepted_items"] += 1
    # If there are fewer than two items, keep recommendations that pass format and subject validation; the Actor layer decides whether to fill the rest.
    # Otherwise, when opening without additional recommendations, the model's only valid option would be incorrectly cleared.
    if len(parsed) not in {2, 3}:
        parse_counts["insufficient_valid_items"] = 1
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(parse_counts)
    return parsed


def _parse_transition_offered(value: Any) -> bool:
    """Validate whether the Actor declares a concrete next step in visible body text; do not infer natural language here."""  # noqa: DOCSTRING_CJK

    if not isinstance(value, bool):
        raise NumericV2ActorOutputError("numeric_v2_actor_transition_offered_invalid")
    return value


def _parse_actor_fact_candidates(
    value: Any,
    *,
    expected: bool = False,
    diagnostics: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Preserve Actor candidates; missing or malformed values only trigger fallback and increment a counter, without rerunning the body for auxiliary fields."""  # noqa: DOCSTRING_CJK

    parse_counts = {
        "expected": int(expected),
        "missing": 0,
        "invalid": 0,
        "accepted_items": 0,
    }
    if value is None:
        parse_counts["missing"] = int(expected)
        parsed: list[dict[str, Any]] = []
    elif not isinstance(value, list) or len(value) > 16:
        parse_counts["invalid"] = 1
        parsed = []
    elif any(not isinstance(item, Mapping) for item in value):
        parse_counts["invalid"] = 1
        parsed = []
    else:
        parsed = [dict(item) for item in value]
        parse_counts["accepted_items"] = len(parsed)
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(parse_counts)
    return parsed


def _parse_output(
    content: Any,
    *,
    opening_required: bool = False,
    transition_required: bool = False,
    bridge_required: bool = True,
    dialogue_policy: str = "required",
    source_dialogue_policy: str = "required",
    target_dialogue_policy: str = "required",
    suggestions_only: bool = False,
    transition_suggestions_only: bool = False,
    suggestion_diagnostics: dict[str, int] | None = None,
    fact_candidates_expected: bool = False,
    fact_candidate_diagnostics: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Parse one Actor response into the only valid opening, ordinary-turn, or scene-transition shape."""  # noqa: DOCSTRING_CJK

    if not isinstance(content, str) or not content.strip():
        raise NumericV2ActorOutputError("numeric_v2_actor_empty_output")
    try:
        # As with the Evaluator, only unwrap one complete JSON fence; do not extract or repair fragments mixed with explanatory prose.
        payload = json.loads(strip_single_json_fence(content))
    except (TypeError, ValueError) as exc:
        raise NumericV2ActorOutputError("numeric_v2_actor_invalid_json") from exc
    if not isinstance(payload, dict):
        raise NumericV2ActorOutputError("numeric_v2_actor_fields_invalid")
    # Accept the legacy narrative_events field shape for body text, but do not parse or use it; the strict shape for supplemental recommendations is unchanged.
    if transition_suggestions_only:
        # Supplemental transition recommendations return an accepted path in its own field, and code deterministically places it first;
        # this prevents the model from sorting a deferment such as "check first / look again" as the default accepted path.
        if set(payload) != {"accept_input", "alternative_inputs"}:
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        alternatives = payload.get("alternative_inputs")
        if not isinstance(alternatives, list) or len(alternatives) not in {1, 2}:
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        acceptance = payload.get("accept_input")
        if not isinstance(acceptance, str) or not _parse_actor_suggestions([acceptance]):
            raise NumericV2ActorOutputError("numeric_v2_actor_suggestions_invalid")
        suggestions = _parse_actor_suggestions(
            [acceptance, *alternatives],
            diagnostics=suggestion_diagnostics,
        )
        return {"suggested_inputs": suggestions}
    if suggestions_only:
        # The supplemental recommendation call may return only recommendation fields; body text never overwrites the previous valid result.
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
        # If recommendation or transition fields are missing, make one lightweight supplemental recommendation call; keep any valid body text.
        tolerated_fields = {*expected_fields, "suggested_inputs", "transition_offered", "narrative_events"}
        # Optional fields degrade independently (missing suggestions == an invalid
        # container, missing transition_offered == False), so any subset is safe.
        if not expected_fields <= set(payload) <= tolerated_fields:
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
    if transition_required:
        # Runtime still determines stages and order; narration is generated from real history, and a missing field must not fall back to author text that could replay a scene.
        expected_fields = {"source_performance", "target_performance",
                           "bridge_scene_narration", "target_scene_narration"}
        tolerated_fields = {*expected_fields, "suggested_inputs", "source_scene_narration", "narrative_events"}
        if not expected_fields.issubset(payload) or set(payload) - tolerated_fields:
            raise NumericV2ActorOutputError("numeric_v2_actor_transition_required")
        result = {
            "bridge_scene_narration": _parse_scene_narration(
                payload.get("bridge_scene_narration"), allow_empty=not bridge_required,
            ),
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
        # Source narration, like ordinary scene_update, may be omitted; preserve NPC wording rather than turning it into catgirl dialogue.
        if "source_scene_narration" in payload:
            narration = _parse_scene_update(payload["source_scene_narration"])
            if narration:
                result["source_scene_narration"] = narration
        result["suggested_inputs"] = _parse_actor_suggestions(
            payload.get("suggested_inputs"),
            diagnostics=suggestion_diagnostics,
        )
        return result
    required_fields = {"performance"}
    tolerated_fields = {
        *required_fields,
        "scene_update",
        "suggested_inputs",
        "transition_offered",
        "fact_candidates",
        "narrative_events",
    }
    # Every optional field is parsed independently below and degrades on its own
    # when absent, so any combination of them is a valid ordinary-turn shape.
    if not required_fields <= set(payload) <= tolerated_fields:
        raise NumericV2ActorOutputError("numeric_v2_actor_fields_invalid")
    result = {
        "performance": _parse_mixed_performance(
            payload.get("performance"),
            # Ordinary turns allow dialogue only; dialogue is required, and the formatting layer should not force generation of parenthetical micro-actions.
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
        # Runtime continues to use the existing scene_narration field, so the frontend and older Sessions need no new protocol branch.
        scene_update = _parse_scene_update(payload.get("scene_update"))
        if scene_update:
            result["scene_narration"] = scene_update
    result["fact_candidates"] = _parse_actor_fact_candidates(
        payload.get("fact_candidates"),
        expected=fact_candidates_expected,
        diagnostics=fact_candidate_diagnostics,
    )
    return result


__all__ = [
    "NumericV2ActorError",
    "NumericV2ActorOutputError",
    "NumericV2ActorUnavailableError",
]
