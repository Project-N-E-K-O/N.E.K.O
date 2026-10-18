"""Compatibility readers for Numeric v2 performance content blocks."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any, Mapping


MAX_CONTENT_BLOCKS = 16


def transition_source_dialogue_policy(dialogue_policy: str) -> str:
    """The source scene need only naturally conclude interaction; an existing speech prohibition still applies."""  # noqa: DOCSTRING_CJK

    return "forbidden" if dialogue_policy == "forbidden" else "optional"


def mixed_performance_blocks(value: Any) -> list[dict[str, str]]:
    """Parse parenthesized micro-actions plus external dialogue into deterministic internal blocks."""  # noqa: DOCSTRING_CJK

    text = str(value or "").strip()
    if not text:
        return []
    pairs = {"（": "）", "(": ")"}
    openers = frozenset(pairs)
    closers = frozenset(pairs.values())
    blocks: list[dict[str, str]] = []
    buffer: list[str] = []
    expected_close = ""

    def flush(block_type: str) -> bool:
        segment = "".join(buffer).strip()
        buffer.clear()
        if not segment:
            return block_type == "dialogue"
        if block_type == "action":
            # performance parentheses always contain the current catgirl's visible micro-actions, not environmental narration.
            # Explicit typing prevents Evaluator from treating character actions such as raised hands or tilted ears as environment evidence.
            blocks.append({"type": "action", "text": segment})
        else:
            blocks.append({
                "type": "dialogue",
                "speaker_id": "active_catgirl",
                "text": segment,
            })
        return len(blocks) <= MAX_CONTENT_BLOCKS

    for char in text:
        if expected_close:
            if char in openers:
                # Micro-actions forbid nested parentheses to keep TTS and display boundaries unambiguous.
                return []
            if char in closers:
                if char != expected_close or not flush("action"):
                    return []
                expected_close = ""
                continue
            buffer.append(char)
            continue
        if char in openers:
            if not flush("dialogue"):
                return []
            expected_close = pairs[char]
            continue
        if char in closers:
            return []
        buffer.append(char)

    if expected_close or not flush("dialogue"):
        return []
    return blocks


def fixed_narration_blocks(container: Mapping[str, Any], position: str) -> list[dict[str, str]]:
    """Read server-owned text verbatim; it never becomes catgirl dialogue."""
    return [{"type": "narration", "text": item["text"]}
            for item in container.get("fixed_narrations", []) if item["position"] == position]


def content_blocks(container: Mapping[str, Any]) -> list[dict[str, str]]:
    """Prefer new mixed narrative; read legacy records using existing blocks or separated fields."""  # noqa: DOCSTRING_CJK

    if "scene_narration" in container or "performance" in container:
        blocks: list[dict[str, str]] = []
        scene_narration = str(container.get("scene_narration") or "").strip()
        if scene_narration:
            blocks.append({"type": "narration", "text": scene_narration})
        blocks.extend(fixed_narration_blocks(container, "before"))
        blocks.extend(mixed_performance_blocks(container.get("performance")))
        blocks.extend(fixed_narration_blocks(container, "after"))
        # Full scene narration uses a separate field, outside mixed performance's 16-block limit.
        return blocks

    raw_content = container.get("content")
    if isinstance(raw_content, list):
        blocks: list[dict[str, str]] = []
        for raw in raw_content:
            if not isinstance(raw, Mapping):
                continue
            block_type = str(raw.get("type") or "")
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if block_type == "narration":
                blocks.append({"type": "narration", "text": text})
            elif block_type == "action":
                blocks.append({"type": "action", "text": text})
            elif block_type == "dialogue" and raw.get("speaker_id") == "active_catgirl":
                blocks.append({
                    "type": "dialogue",
                    "speaker_id": "active_catgirl",
                    "text": text,
                })
        return blocks[:MAX_CONTENT_BLOCKS]

    blocks = []
    narration = str(container.get("narration") or "").strip()
    if narration:
        blocks.append({"type": "narration", "text": narration})
    for raw in container.get("dialogue") or []:
        if not isinstance(raw, Mapping) or raw.get("speaker_id") != "active_catgirl":
            continue
        text = str(raw.get("text") or "").strip()
        if text:
            blocks.append({
                "type": "dialogue",
                "speaker_id": "active_catgirl",
                "text": text,
            })
    return blocks[:MAX_CONTENT_BLOCKS]


def performance_content_blocks(performance: Mapping[str, Any]) -> list[dict[str, str]]:
    """Expand ordinary or transition performance in the player's actual display order."""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if isinstance(segments, list):
        blocks: list[dict[str, str]] = []
        for segment in segments:
            if isinstance(segment, Mapping):
                segment_blocks = content_blocks(segment)
                if segment.get("phase") == "transition_bridge":
                    segment_blocks = [block for block in segment_blocks if not (
                        block["type"] == "narration" and block["text"] == "时间向前流转，现场随之转换。")]
                blocks.extend(segment_blocks)
        return blocks
    blocks = content_blocks(performance)
    if performance.get("phase") == "transition_bridge":
        blocks = [block for block in blocks if not (
            block["type"] == "narration" and block["text"] == "时间向前流转，现场随之转换。")]
    return blocks


def valid_ordered_content(
    container: Mapping[str, Any],
    *,
    require_narration: bool = False,
    require_dialogue: bool = False,
) -> bool:
    raw_content = container.get("content")
    if not isinstance(raw_content, list) or not 1 <= len(raw_content) <= MAX_CONTENT_BLOCKS:
        return False
    block_types: set[str] = set()
    for raw in raw_content:
        if not isinstance(raw, Mapping) or not str(raw.get("text") or "").strip():
            return False
        if raw.get("type") in {"narration", "action"} and set(raw) == {"type", "text"}:
            block_types.add(str(raw.get("type")))
            continue
        if (
            raw.get("type") == "dialogue"
            and set(raw) == {"type", "speaker_id", "text"}
            and raw.get("speaker_id") == "active_catgirl"
        ):
            block_types.add("dialogue")
            continue
        return False
    return (
        (not require_narration or bool({"narration", "action"} & block_types))
        and (not require_dialogue or "dialogue" in block_types)
    )


def valid_mixed_performance_policy(
    container: Mapping[str, Any],
    dialogue_policy: str,
) -> bool:
    """Validate dialogue against current character state; even when speech is forbidden, visible actions are required, never empty narrative."""  # noqa: DOCSTRING_CJK

    if dialogue_policy not in {"required", "optional", "forbidden"}:
        return False
    raw = container.get("performance")
    if not isinstance(raw, str) or not raw.strip():
        return False
    blocks = mixed_performance_blocks(raw)
    if not blocks:
        return False
    has_dialogue = any(block["type"] == "dialogue" for block in blocks)
    if dialogue_policy == "required":
        return has_dialogue
    if dialogue_policy == "forbidden":
        return not has_dialogue and any(block["type"] == "action" for block in blocks)
    return True


def valid_scene_narration(
    container: Mapping[str, Any],
    *,
    allow_empty: bool = False,
) -> bool:
    """Scene narration uses its own field and must not embed undeclared structure."""  # noqa: DOCSTRING_CJK

    return (
        isinstance(container.get("scene_narration"), str)
        and (allow_empty or bool(container["scene_narration"].strip()))
    )


def is_silent_invitation_control(event: Mapping[str, Any], performance: Mapping[str, Any]) -> bool:
    """Allow an empty body only for a program-issued button-only control turn."""
    receipt = event.get("program_invitation")
    return bool(
        event.get("input_source") == "reinvite"
        and event.get("from_node_id") == event.get("to_node_id")
        and isinstance(receipt, Mapping) and receipt.get("silent") is True
        and receipt.get("offer") == "" and receipt.get("performance") == ""
        and receipt.get("revision") == event.get("result_revision")
        and isinstance(receipt.get("route_id"), str) and receipt["route_id"]
        and isinstance(receipt.get("accept_input"), str) and receipt["accept_input"].strip()
        and receipt.get("visible_blocks") == []
        and performance.get("performance") == ""
        and not any(key in performance for key in ("scene_narration", "scene_update", "segments", "content", "fixed_narrations"))
        and performance.get("suggested_inputs") == [receipt["accept_input"]]
        and performance.get("transition_offered") is True
        and event.get("transition_offered") is True
        and performance.get("transition_offer_presented") is True
        and event.get("transition_offer_presented") is True
    )


__all__ = [
    "MAX_CONTENT_BLOCKS",
    "content_blocks",
    "is_silent_invitation_control",
    "mixed_performance_blocks",
    "performance_content_blocks",
    "transition_source_dialogue_policy",
    "valid_mixed_performance_policy",
    "valid_ordered_content",
    "valid_scene_narration",
]
