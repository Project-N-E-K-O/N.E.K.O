"""Numeric v2 表现内容块的兼容读取工具。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any, Mapping


MAX_CONTENT_BLOCKS = 16


def transition_source_dialogue_policy(dialogue_policy: str) -> str:
    """来源幕只需自然收住互动；原本禁言时仍不得凭空开口。"""  # noqa: DOCSTRING_CJK

    return "forbidden" if dialogue_policy == "forbidden" else "optional"


def mixed_performance_blocks(value: Any) -> list[dict[str, str]]:
    """把“括号微动作 + 括号外对白”解析成确定性的内部内容块。"""  # noqa: DOCSTRING_CJK

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
            # performance 括号内始终是当前猫娘的可见微动作，不是环境旁白。
            # 明确分型后，Evaluator 才不会把抬手、侧耳等角色动作误当成环境目标证据。
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
                # 微动作不允许嵌套括号，避免 TTS 边界和展示边界产生歧义。
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
    """优先解析新混合正文；旧记录继续按原内容块或分离字段读取。"""  # noqa: DOCSTRING_CJK

    if "scene_narration" in container or "performance" in container:
        blocks: list[dict[str, str]] = []
        scene_narration = str(container.get("scene_narration") or "").strip()
        if scene_narration:
            blocks.append({"type": "narration", "text": scene_narration})
        blocks.extend(fixed_narration_blocks(container, "before"))
        blocks.extend(mixed_performance_blocks(container.get("performance")))
        blocks.extend(fixed_narration_blocks(container, "after"))
        # 完整场景旁白是独立字段，不占混合 performance 自身的 16 块上限。
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
    """按玩家实际看到的顺序展开普通或换场 performance。"""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if isinstance(segments, list):
        blocks: list[dict[str, str]] = []
        for segment in segments:
            if isinstance(segment, Mapping):
                blocks.extend(content_blocks(segment))
        return blocks
    return content_blocks(performance)


def performance_dialogue(performance: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        block
        for block in performance_content_blocks(performance)
        if block["type"] == "dialogue"
    ]


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
    """按当前角色状态校验对白；禁言时仍必须有可见动作，不能提交空正文。"""  # noqa: DOCSTRING_CJK

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
    """场景旁白使用独立字段，不能夹带未声明的结构。"""  # noqa: DOCSTRING_CJK

    return (
        isinstance(container.get("scene_narration"), str)
        and (allow_empty or bool(container["scene_narration"].strip()))
    )


__all__ = [
    "MAX_CONTENT_BLOCKS",
    "content_blocks",
    "mixed_performance_blocks",
    "performance_content_blocks",
    "performance_dialogue",
    "transition_source_dialogue_policy",
    "valid_mixed_performance_policy",
    "valid_ordered_content",
    "valid_scene_narration",
]
