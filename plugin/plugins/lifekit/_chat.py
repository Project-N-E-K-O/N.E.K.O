"""LifeKit chat presentation helpers.

LifeKit uses the host's push_message v2 passthrough instead of adding a
plugin-system level rich-content API.
"""

from __future__ import annotations

from typing import Any


def _block_to_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type == "text":
        return str(block.get("text") or "").strip()
    if block_type == "image":
        url = str(block.get("url") or "").strip()
        if not url:
            return ""
        alt = str(block.get("alt") or "image").strip() or "image"
        return f"![{alt}]({url})"
    if block_type in {"link", "url"}:
        url = str(block.get("url") or "").strip()
        if not url:
            return ""
        title = str(block.get("title") or url).strip() or url
        return f"[{title}]({url})"
    if block_type == "status":
        return str(block.get("text") or "").strip()
    return ""


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = _block_to_text(block)
        if text:
            lines.append(text)
    return "\n\n".join(lines).strip()


def push_lifekit_content(
    plugin: Any,
    blocks: list[dict[str, Any]],
    *,
    target_lanlan: str | None = None,
) -> object | None:
    text = blocks_to_text(blocks)
    if not text:
        return None
    return plugin.push_message(
        source=getattr(plugin, "plugin_id", "lifekit"),
        visibility=["chat"],
        ai_behavior="blind",
        parts=[{"type": "text", "text": text}],
        metadata={"context_type": "lifekit_content"},
        target_lanlan=target_lanlan,
    )
