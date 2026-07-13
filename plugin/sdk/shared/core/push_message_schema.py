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

# Canonical migration metadata shared by the runtime translator and the
# ``neko-plugin check`` source scanner.  Keep the complete v1 keyword set here
# so adding or removing a compatibility field cannot silently leave one of the
# two deprecation paths behind.
LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS = {
    "message_type": "parts=[...] plus visibility=[...] / ai_behavior=...",
    "description": "metadata={'description': ...}",
    "content": "parts=[{'type': 'text', 'text': ...}]",
    "binary_data": "parts=[{'type': 'image|audio|video', 'data': ..., 'mime': ...}]",
    "binary_url": "parts=[{'type': 'image|audio|video', 'url': ..., 'mime': ...}]",
    "mime": "parts=[{..., 'mime': ...}]",
    "unsafe": "drop the field",
    "fast_mode": "drop the field; v2 uses the standard host delivery path",
    "delivery": "visibility=[...] + ai_behavior=...",
    "reply": "visibility=[...] + ai_behavior=...",
}

# These bool flags retain their default/non-active value without warning. The
# static checker ignores literal ``False``/``None`` but reports dynamic values,
# because it cannot prove that they stay inactive at runtime.
LEGACY_PUSH_MESSAGE_TRUTHY_ONLY_FIELDS = frozenset({"unsafe", "fast_mode"})

# Legacy positional order on the host ``PluginContext.push_message`` method.
# ``None`` marks common/non-legacy slots. The public SDK is keyword-only, but
# the source checker still diagnoses older direct-context calls accurately.
LEGACY_PUSH_MESSAGE_POSITIONAL_FIELDS: tuple[str | None, ...] = (
    None,  # source
    "message_type",
    "description",
    None,  # priority
    "content",
    "binary_data",
    "binary_url",
    None,  # metadata
    "unsafe",
    "fast_mode",
    None,  # target_lanlan
)

# Legacy ``message_type`` literals; we still accept them on the wire for
# the deprecation window.  Translation rules live in ``translate_push_message``.
LEGACY_MESSAGE_TYPES = (
    "proactive_notification",
    "music_play_url",
    "music_allowlist_add",
)


def format_push_message_v1_static_diagnostic(field: str) -> str:
    """Return the trilingual source-check warning for one legacy keyword."""
    migration = LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS[field]
    return (
        f"push_message v1 keyword '{field}' is deprecated; migrate to "
        f"{migration} before removal in {DEPRECATION_REMOVAL_VERSION} / "
        f"push_message v1 参数 '{field}' 已弃用；请在 {DEPRECATION_REMOVAL_VERSION} "
        f"移除前迁移为 {migration} / "
        f"push_message v1 キーワード '{field}' は非推奨です。"
        f"{DEPRECATION_REMOVAL_VERSION} で削除される前に {migration} へ移行してください"
    )


def _warn(field: str, replacement: str | None = None) -> None:
    if replacement is None:
        replacement = LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS[field]
    warnings.warn(
        f"push_message: '{field}' is deprecated; use {replacement}. "
        f"Removed in {DEPRECATION_REMOVAL_VERSION}. / "
        f"push_message 参数 '{field}' 已弃用，请改用 {replacement}；"
        f"将在 {DEPRECATION_REMOVAL_VERSION} 移除。 / "
        f"push_message キーワード '{field}' は非推奨です。{replacement} を使用してください。"
        f"{DEPRECATION_REMOVAL_VERSION} で削除されます。",
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
    unknown types here so downstream consumers stay forward-compatible.
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
    fast_mode: bool | None = None,
    # ---- common ----
    source: str = "",
    metadata: dict[str, Any] | None = None,
    target_lanlan: str | None = None,
    priority: int = 0,
    coalesce_key: str | None = None,
) -> dict[str, Any]:
    """Translate (v2 + legacy) kwargs to the canonical wire payload.

    DeprecationWarning is emitted once per legacy field that the caller
    passed.  Returns a dict suitable for embedding under a message_plane
    item ``payload``; the caller is responsible for adding transport
    metadata (``message_id``, ``plugin_id``, ``time``, …).
    """
    active_legacy_fields = {
        "message_type": message_type is not None,
        "description": description is not None,
        "content": content is not None,
        "binary_data": binary_data is not None,
        "binary_url": binary_url is not None,
        "mime": mime is not None,
        "delivery": delivery is not None,
        "reply": reply is not None,
        "unsafe": bool(unsafe),
        "fast_mode": bool(fast_mode),
    }
    legacy_used = any(active_legacy_fields.values())

    # Warn for every explicitly active legacy field, even when a canonical v2
    # field takes precedence during translation. Static and runtime diagnostics
    # must not disagree merely because a legacy value is shadowed.
    if message_type is not None:
        if message_type == "proactive_notification":
            _warn(
                "message_type='proactive_notification'",
                "drop the field (default behaviour) and pass parts=[...]",
            )
        elif message_type == "music_play_url":
            _warn(
                "message_type='music_play_url'",
                "parts=[{'type': 'ui_action', 'action': 'media_play_url', "
                "'url': ..., 'media_type': 'audio'}] with visibility=['chat']",
            )
        elif message_type == "music_allowlist_add":
            _warn(
                "message_type='music_allowlist_add'",
                "parts=[{'type': 'ui_action', 'action': 'media_allowlist_add', "
                "'domains': [...]}]",
            )
        else:
            _warn(
                f"message_type={message_type!r}",
                "drop message_type and use parts + visibility/ai_behavior",
            )
    for field in (
        "description",
        "content",
        "binary_data",
        "binary_url",
        "mime",
        "delivery",
        "reply",
        "unsafe",
        "fast_mode",
    ):
        if active_legacy_fields[field]:
            _warn(field)

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
        final_visibility, final_ai_behavior = _delivery_to_axes(delivery)
    elif reply is not None:
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
            final_parts.append({"type": "text", "text": str(content)})
        if binary_data is not None:
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
            final_parts.append(
                {
                    "type": _mime_to_part_type(mime),
                    "url": str(binary_url),
                    "mime": mime,
                }
            )
    # ---- legacy message_type ----
    md = dict(metadata) if isinstance(metadata, dict) else None

    if message_type is not None:
        if message_type == "proactive_notification":
            # No part synthesis — visibility/ai_behavior already derived
            # from delivery/reply above (or defaulted).
            pass
        elif message_type == "music_play_url":
            if parts is None:
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
            if visibility is None:
                final_visibility = ["chat"]
            if ai_behavior is None:
                final_ai_behavior = "blind"
        elif message_type == "music_allowlist_add":
            if parts is None:
                md_local = md or {}
                final_parts.append(
                    {
                        "type": "ui_action",
                        "action": "media_allowlist_add",
                        "domains": list(md_local.get("domains") or []),
                    }
                )
            if visibility is None:
                final_visibility = []
            if ai_behavior is None:
                final_ai_behavior = "blind"

    if description is not None:
        if md is None:
            md = {}
        md.setdefault("description", description)

    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "source": source,
        "priority": priority,
        # Optional coalescing key for ProactiveDeliveryManager (OPT-IN):
        # queued proactive cues sharing the SAME explicit key collapse to the
        # newest. Empty → never coalesce (unique per cue). Set distinct keys
        # per cue CATEGORY so distinct important cues don't drop each other.
        "coalesce_key": coalesce_key if isinstance(coalesce_key, str) else "",
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
    "LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS",
    "LEGACY_PUSH_MESSAGE_POSITIONAL_FIELDS",
    "LEGACY_PUSH_MESSAGE_TRUTHY_ONLY_FIELDS",
    "LEGACY_MESSAGE_TYPES",
    "format_push_message_v1_static_diagnostic",
    "translate_push_message",
]
