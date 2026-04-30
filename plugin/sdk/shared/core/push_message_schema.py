"""Canonical schema + translation helpers for ``ctx.push_message`` v2.

Two orthogonal axes:

* ``visibility``  – list of channels the user sees the plugin's parts on,
  rendered **verbatim**.  Empty list means the user does not see the parts
  themselves; if AI also responds (``ai_behavior="respond"``) the user only
  sees AI's reply.  Allowed values: ``"chat"``, ``"hud"``.
* ``ai_behavior`` – how the LLM treats the parts:

    - ``"respond"`` – feed parts into LLM context AND trigger an immediate
      AI turn (the AI generates a reply that goes to chat as the AI's
      bubble).  Equivalent to the legacy ``delivery="proactive"`` /
      ``reply=True`` default.
    - ``"read"``    – feed parts into LLM context but do NOT trigger a
      turn.  AI sees them and may bring them up naturally on the next user
      turn.  Equivalent to the legacy ``delivery="passive"``.
    - ``"blind"``   – do NOT feed parts to the LLM at all.  The parts
      either render verbatim (``visibility`` includes ``"chat"`` or
      ``"hud"``) or trigger pure infrastructure actions (e.g. media
      allowlist updates).  Equivalent to the legacy ``delivery="silent"``
      / ``reply=False`` for HUD-only notifications, and to the legacy
      ``message_type="music_play_url"`` / ``"music_allowlist_add"`` for
      UI side effects.

Parts are an ordered list of dicts.  Each part has a ``type`` discriminator:

* ``{"type": "text",  "text": str}``
* ``{"type": "image", "data": bytes, "mime": str}``  (inline)
* ``{"type": "image", "url":  str,   "mime": str}``  (remote)
* ``{"type": "audio", "data": bytes, "mime": str}``
* ``{"type": "audio", "url":  str,   "mime": str}``
* ``{"type": "video", "url":  str,   "mime": str}``
* ``{"type": "ui_action", "action": str, ...}``       – frontend-only side
  effects (media playback, allowlist updates, …).  ``action`` values today:
  ``"media_play_url"`` (with ``url`` / ``media_type`` / ``name`` /
  ``artist``) and ``"media_allowlist_add"`` (with ``domains``).

The wire format encodes inline ``data: bytes`` as base64 in
``binary_base64`` so the message_plane PUB JSON serialiser can carry it.
``translate_push_message`` performs the encoding; downstream consumers
should treat ``binary_base64`` as the source-of-truth field.

This module is the **single source of truth** for the schema; both the
plugin-side adapter (``plugin/core/context.py``) and the host-side bridge
(``plugin/server/messaging/proactive_bridge.py``) call into it.
"""

from __future__ import annotations

import base64
import warnings
from typing import Any


SCHEMA_VERSION = "push_message.v2"
DEPRECATION_REMOVAL_VERSION = "v0.9"  # see docs/changelog

VISIBILITY_VALUES = ("chat", "hud")
AI_BEHAVIOR_VALUES = ("respond", "read", "blind")
DEFAULT_VISIBILITY: list[str] = []
DEFAULT_AI_BEHAVIOR = "respond"

# Legacy ``message_type`` literals; we still accept them on the wire for
# the deprecation window.  Translation rules live in ``translate_push_message``.
LEGACY_MESSAGE_TYPES = (
    "proactive_notification",
    "music_play_url",
    "music_allowlist_add",
)


def _warn(field: str, replacement: str) -> None:
    warnings.warn(
        f"push_message: '{field}' is deprecated; use {replacement}. "
        f"Removed in {DEPRECATION_REMOVAL_VERSION}.",
        DeprecationWarning,
        stacklevel=4,
    )


def _mime_to_part_type(mime: str | None) -> str:
    if not isinstance(mime, str):
        return "image"
    m = mime.lower()
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("video/"):
        return "video"
    return "image"


def _normalize_part(p: dict[str, Any]) -> dict[str, Any]:
    """Encode a single part for the wire.

    ``data: bytes`` is replaced by ``binary_base64: str``.  Everything else
    is shallow-copied through unchanged.  Caller is responsible for
    ensuring ``type`` is one of the documented values; we don't reject
    unknown types here so downstream extensions stay forward-compatible.
    """
    if not isinstance(p, dict):
        raise TypeError(f"part must be a dict, got {type(p).__name__}")
    out = dict(p)
    raw = out.pop("data", None)
    if raw is not None:
        if not isinstance(raw, (bytes, bytearray)):
            raise TypeError("part 'data' must be bytes/bytearray")
        out["binary_base64"] = base64.b64encode(bytes(raw)).decode("ascii")
    return out


def _delivery_to_axes(delivery: str | bool) -> tuple[list[str], str]:
    """Map legacy ``delivery`` value to (visibility, ai_behavior)."""
    if isinstance(delivery, bool):
        return ([], "respond") if delivery else (["hud"], "blind")
    if delivery == "proactive":
        return ([], "respond")
    if delivery == "passive":
        return ([], "read")
    if delivery == "silent":
        return (["hud"], "blind")
    # Unknown delivery string: fall back to default
    return ([], "respond")


def translate_push_message(
    *,
    # ---- v2 (canonical) ----
    visibility: list[str] | None = None,
    ai_behavior: str | None = None,
    parts: list[dict[str, Any]] | None = None,
    # ---- legacy (each emits DeprecationWarning) ----
    message_type: str | None = None,
    description: str | None = None,
    content: str | None = None,
    binary_data: bytes | bytearray | None = None,
    binary_url: str | None = None,
    mime: str | None = None,
    delivery: str | bool | None = None,
    reply: bool | None = None,
    unsafe: bool | None = None,
    # ---- common ----
    source: str = "",
    metadata: dict[str, Any] | None = None,
    target_lanlan: str | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Translate (v2 + legacy) kwargs to the canonical wire payload.

    DeprecationWarning is emitted once per legacy field that the caller
    passed.  Returns a dict suitable for embedding under a message_plane
    item ``payload``; the caller is responsible for adding transport
    metadata (``message_id``, ``plugin_id``, ``time``, …).
    """
    legacy_used = False

    # ---- visibility / ai_behavior ----
    final_visibility: list[str] = list(DEFAULT_VISIBILITY)
    final_ai_behavior: str = DEFAULT_AI_BEHAVIOR

    if visibility is not None or ai_behavior is not None:
        if visibility is not None:
            if not isinstance(visibility, (list, tuple)):
                raise TypeError("visibility must be a list of strings")
            seen: set[str] = set()
            cleaned: list[str] = []
            for v in visibility:
                if not isinstance(v, str):
                    raise TypeError(f"visibility entries must be str, got {type(v).__name__}")
                if v not in VISIBILITY_VALUES:
                    raise ValueError(
                        f"visibility entry {v!r} not in {VISIBILITY_VALUES}"
                    )
                if v not in seen:
                    cleaned.append(v)
                    seen.add(v)
            final_visibility = cleaned
        if ai_behavior is not None:
            if ai_behavior not in AI_BEHAVIOR_VALUES:
                raise ValueError(
                    f"ai_behavior must be one of {AI_BEHAVIOR_VALUES}, got {ai_behavior!r}"
                )
            final_ai_behavior = ai_behavior
    elif delivery is not None:
        legacy_used = True
        _warn("delivery", "visibility=[...] + ai_behavior=...")
        final_visibility, final_ai_behavior = _delivery_to_axes(delivery)
    elif reply is not None:
        legacy_used = True
        _warn("reply", "visibility=[...] + ai_behavior=...")
        final_visibility, final_ai_behavior = _delivery_to_axes(bool(reply))

    # ---- parts ----
    final_parts: list[dict[str, Any]] = []

    if parts is not None:
        if not isinstance(parts, (list, tuple)):
            raise TypeError("parts must be a list of dicts")
        for p in parts:
            final_parts.append(_normalize_part(p))
    else:
        if content is not None:
            legacy_used = True
            _warn("content", "parts=[{'type': 'text', 'text': ...}]")
            final_parts.append({"type": "text", "text": str(content)})
        if binary_data is not None:
            legacy_used = True
            _warn(
                "binary_data",
                "parts=[{'type': 'image|audio|video', 'data': ..., 'mime': ...}]",
            )
            if not isinstance(binary_data, (bytes, bytearray)):
                raise TypeError("binary_data must be bytes/bytearray")
            final_parts.append(
                {
                    "type": _mime_to_part_type(mime),
                    "binary_base64": base64.b64encode(bytes(binary_data)).decode("ascii"),
                    "mime": mime or "application/octet-stream",
                }
            )
        elif binary_url is not None:
            legacy_used = True
            _warn(
                "binary_url",
                "parts=[{'type': 'image|audio|video', 'url': ..., 'mime': ...}]",
            )
            final_parts.append(
                {
                    "type": _mime_to_part_type(mime),
                    "url": str(binary_url),
                    "mime": mime,
                }
            )
        # ``mime`` alone is meaningless; only warn when caller supplied it
        # without binary_data / binary_url.
        if mime is not None and binary_data is None and binary_url is None:
            legacy_used = True
            _warn("mime", "parts=[{..., 'mime': ...}]")

    # ---- legacy message_type ----
    md = dict(metadata) if isinstance(metadata, dict) else None

    if message_type is not None:
        legacy_used = True
        if message_type == "proactive_notification":
            _warn(
                "message_type='proactive_notification'",
                "drop the field (default behaviour) and pass parts=[...]",
            )
            # No part synthesis — visibility/ai_behavior already derived
            # from delivery/reply above (or defaulted).
        elif message_type == "music_play_url":
            _warn(
                "message_type='music_play_url'",
                "parts=[{'type': 'ui_action', 'action': 'media_play_url', "
                "'url': ..., 'media_type': 'audio'}] with visibility=['chat']",
            )
            md_local = md or {}
            ui_part: dict[str, Any] = {
                "type": "ui_action",
                "action": "media_play_url",
            }
            for k in ("url", "name", "artist"):
                if k in md_local:
                    ui_part[k] = md_local[k]
            ui_part.setdefault("media_type", md_local.get("media_type", "audio"))
            final_parts.append(ui_part)
            # music_play_url renders a chat card; no AI involvement.
            final_visibility = ["chat"]
            final_ai_behavior = "blind"
        elif message_type == "music_allowlist_add":
            _warn(
                "message_type='music_allowlist_add'",
                "parts=[{'type': 'ui_action', 'action': 'media_allowlist_add', "
                "'domains': [...]}]",
            )
            md_local = md or {}
            final_parts.append(
                {
                    "type": "ui_action",
                    "action": "media_allowlist_add",
                    "domains": list(md_local.get("domains") or []),
                }
            )
            final_visibility = []
            final_ai_behavior = "blind"
        else:
            _warn(
                f"message_type={message_type!r}",
                "drop message_type and use parts + visibility/ai_behavior",
            )

    if description is not None:
        legacy_used = True
        _warn("description", "metadata={'description': ...}")
        if md is None:
            md = {}
        md.setdefault("description", description)

    if unsafe:
        legacy_used = True
        _warn("unsafe", "drop the field")

    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "source": source,
        "priority": priority,
        "visibility": final_visibility,
        "ai_behavior": final_ai_behavior,
        "parts": final_parts,
        "metadata": md if md is not None else {},
        "target_lanlan": target_lanlan,
        "_legacy_call": legacy_used,
    }
    return payload


__all__ = [
    "SCHEMA_VERSION",
    "DEPRECATION_REMOVAL_VERSION",
    "VISIBILITY_VALUES",
    "AI_BEHAVIOR_VALUES",
    "DEFAULT_VISIBILITY",
    "DEFAULT_AI_BEHAVIOR",
    "LEGACY_MESSAGE_TYPES",
    "translate_push_message",
]
