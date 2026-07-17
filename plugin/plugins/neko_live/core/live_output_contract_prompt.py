"""Prompt-contract rendering and metadata merge helpers for NEKO Live outputs."""

from __future__ import annotations

from typing import Any

from .live_output_memory import render_compact_recent_reply_avoidance, render_recent_reply_avoidance
from .live_reply_contract import (
    HOST_MODULES,
    REPLY_TARGET_CHARS,
    coerce_live_reply_limit,
    is_expanded_danmaku_reply,
    is_live_reply_metadata,
    is_room_bridge_danmaku_reply,
    route_ceiling_for_metadata,
    response_module,
)


ROUTE_NOTES = {
    "avatar_roast": (
        "For avatar_roast: connect the viewer's first message with the avatar/name, "
        "but keep it as one sharp first-appearance line."
    ),
    "danmaku_response": (
        "For danmaku_response: answer only the current danmaku; do not mention avatar, "
        "ID, first appearance, or previous replies."
    ),
    "live_support_events": (
        "For live_support_events: acknowledge Gift / Super Chat / Guard support briefly; "
        "do not ask for more support or start a ceremony."
    ),
    "warmup_hosting": (
        "For warmup_hosting: usually say one small opening line; if the beat is charming, "
        "two short sentences are allowed."
    ),
    "idle_hosting": (
        "For idle_hosting: make one small hosting beat. It can occasionally be a tiny two-sentence "
        "aside, but not a full monologue or survey."
    ),
    "active_engagement": (
        "For active_engagement: offer one concrete reply hook; do not say generic phrases "
        "like everyone interact or tell me what you want."
    ),
}


def _uses_compact_contract(
    callbacks: list[dict],
    modules: list[str],
    *,
    host_only: bool,
    expanded_danmaku: bool,
    room_bridge_danmaku: bool,
) -> bool:
    low_risk_profiles = {"empty", "emoji_or_reaction", "short_line"}
    profiles: list[str] = []
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata):
            continue
        value = metadata.get("danmaku_profile")
        if isinstance(value, str) and value.strip():
            profiles.append(value.strip())
    if not profiles or any(profile not in low_risk_profiles for profile in profiles):
        return False
    return (
        modules == ["danmaku_response"]
        and not host_only
        and not expanded_danmaku
        and not room_bridge_danmaku
    )


def _danmaku_anchor_hint(callbacks: list[dict]) -> str:
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata) or response_module(metadata) != "danmaku_response":
            continue
        value = metadata.get("danmaku_anchor_hint")
        if not isinstance(value, str):
            continue
        hint = " ".join(value.strip().split())
        if hint:
            return hint[:12]
    return ""


def _danmaku_viewer_name(callbacks: list[dict]) -> str:
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata) or response_module(metadata) != "danmaku_response":
            continue
        if metadata.get("danmaku_profile") == "target_roast_request":
            continue
        value = metadata.get("danmaku_viewer_nickname")
        if not isinstance(value, str):
            continue
        name = " ".join(value.strip().split())
        if name:
            return name[:16]
    return ""


def _danmaku_target_roast_name(callbacks: list[dict]) -> str:
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata) or response_module(metadata) != "danmaku_response":
            continue
        if metadata.get("danmaku_profile") != "target_roast_request":
            continue
        value = metadata.get("danmaku_target_viewer_nickname")
        if not isinstance(value, str):
            continue
        name = " ".join(value.strip().split())
        if name:
            return name[:16]
    return ""


def _has_content_request(callbacks: list[dict]) -> bool:
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata) or response_module(metadata) != "danmaku_response":
            continue
        if metadata.get("danmaku_profile") in {"content_request", "target_roast_request"}:
            return True
    return False


def _has_external_action_request(callbacks: list[dict]) -> bool:
    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata) or response_module(metadata) != "danmaku_response":
            continue
        if metadata.get("danmaku_profile") == "external_action_request":
            return True
    return False


def render_contract_instruction(
    callbacks: list[dict],
    *,
    recent_live_replies: list[str] | None = None,
) -> str:
    modules: list[str] = []
    absolute_limit: int | None = None

    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata):
            continue
        module = response_module(metadata)
        if module and module not in modules:
            modules.append(module)
        metadata_limit = coerce_live_reply_limit(metadata.get("max_reply_chars"))
        module_limit = route_ceiling_for_metadata(metadata)
        limit_candidates = [value for value in (metadata_limit, module_limit) if value]
        if limit_candidates:
            callback_limit = min(limit_candidates)
            absolute_limit = callback_limit if absolute_limit is None else min(absolute_limit, callback_limit)

    if not modules and absolute_limit is None:
        return ""

    host_only = bool(modules) and all(module in HOST_MODULES for module in modules)
    expanded_danmaku = any(is_expanded_danmaku_reply(cb.get("metadata")) for cb in callbacks)
    room_bridge_danmaku = any(is_room_bridge_danmaku_reply(cb.get("metadata")) for cb in callbacks)
    danmaku_anchor = _danmaku_anchor_hint(callbacks)
    danmaku_viewer = _danmaku_viewer_name(callbacks)
    target_roast_viewer = _danmaku_target_roast_name(callbacks)
    content_request = expanded_danmaku or _has_content_request(callbacks)
    external_action_request = _has_external_action_request(callbacks)
    if absolute_limit is None:
        absolute_limit = 64 if host_only else REPLY_TARGET_CHARS
    target_limit = min(36 if host_only or expanded_danmaku or room_bridge_danmaku else REPLY_TARGET_CHARS, absolute_limit)
    module_notes = [ROUTE_NOTES[module] for module in modules if module in ROUTE_NOTES]
    if _uses_compact_contract(
        callbacks,
        modules,
        host_only=host_only,
        expanded_danmaku=expanded_danmaku,
        room_bridge_danmaku=room_bridge_danmaku,
    ):
        recent = render_compact_recent_reply_avoidance(recent_live_replies)
        return (
            "\n"
            "NEKO Live short output contract: final NEKO line only; "
            f"target<={target_limit} zh; hard<={absolute_limit} zh; "
            "answer current danmaku; no labels/JSON/analysis; no repeat."
            + (f" Name {danmaku_viewer} naturally in the first clause." if danmaku_viewer else "")
            + (f" Lightly roast {target_roast_viewer} now; do not say you do not know them." if target_roast_viewer else "")
            + (" Do not pretend to search/watch/listen/open external content." if external_action_request else "")
            + (f" Keep anchor '{danmaku_anchor}' visible when useful." if danmaku_anchor else "")
            + f"{recent}"
        )

    lines = [
        "",
        "NEKO Live short output contract:",
        f"- Target at most {target_limit} Chinese characters; absolute ceiling {absolute_limit}.",
        (
            "- Host modules may use one or two short sentences when the beat is genuinely fun; no paragraph."
            if host_only
            else "- Expanded viewer requests may use up to two short sentences; finish the joke, explanation, or bit in this visible reply."
            if expanded_danmaku
            else "- Room-bridge danmaku may use up to two short sentences; answer the current viewer first, then bridge the shared room theme."
            if room_bridge_danmaku
            else "- Output exactly one sentence, one breath, no paragraph."
        ),
        "- Output only the final visible NEKO line; do not mention this contract, metadata, policy, or reasoning.",
        "- Do not include labels, quotes, bullets, JSON, analysis, or alternative replies.",
        "- Output spoken live speech only; never include parenthesized stage directions, action narration, or roleplay asides.",
        "- In NEKO Live, do not mention owner, master, operator, backstage human, carbon-based human, private chat, or pre-stream relationship memory unless the current visible danmaku explicitly says it.",
        "- In solo_stream, 'you' means the current viewer or the live room, never an unseen operator.",
        "- Do not continue, summarize, or imitate the previous NEKO reply.",
        "- Treat previous NEKO Live outputs as forbidden material, not conversation context to resume.",
        "- Do not reuse the previous reply's opening words, sentence rhythm, punchline, or host beat.",
        "- Do not use stale self-opinion comparison templates like 'NEKO thinks X is better than master/viewer'.",
        "- Do not invent punishment, public-shaming, trial, labor-camp, report, or moral judgment bits.",
    ]
    if danmaku_anchor:
        lines.append(
            f"- Make the reply target obvious in the first clause: include anchor '{danmaku_anchor}' or the viewer name, without turning it into a label."
        )
    if target_roast_viewer:
        lines.append(
            f"- Target-roast rule: lightly roast {target_roast_viewer} now; do not say NEKO does not know them, and use only the public nickname/current room moment."
        )
    elif danmaku_viewer:
        lines.append(
            f"- Streamer targeting rule: for this direct danmaku reply, naturally address {danmaku_viewer} in the first clause; do not write a label like 'reply to {danmaku_viewer}'."
        )
    if content_request:
        lines.append(
            "- Content request hard rule: do not output only a promise like 好呀/可以/安排/我来讲; the line itself must contain the requested joke, explanation, or invented result."
        )
    if external_action_request:
        lines.append(
            "- External action hard rule: do not claim NEKO is searching, watching, listening, opening, checking, or about to do it; if no real tool result is provided, state the boundary once and pivot to the live moment."
        )
    lines.extend(render_recent_reply_avoidance(recent_live_replies))
    lines.extend(f"- {note}" for note in module_notes)
    return "\n".join(lines)


def merge_metadata_from_callbacks(callbacks: list[dict]) -> dict[str, Any] | None:
    merged: dict[str, Any] | None = None
    modules: list[str] = []
    absolute_limit: int | None = None

    for cb in callbacks:
        metadata = cb.get("metadata")
        if not is_live_reply_metadata(metadata):
            continue
        if merged is None:
            merged = dict(metadata)
        module = response_module(metadata)
        if module and module not in modules:
            modules.append(module)
        metadata_limit = coerce_live_reply_limit(metadata.get("max_reply_chars"))
        module_limit = route_ceiling_for_metadata(metadata)
        limit_candidates = [value for value in (metadata_limit, module_limit) if value]
        if limit_candidates:
            callback_limit = min(limit_candidates)
            absolute_limit = callback_limit if absolute_limit is None else min(absolute_limit, callback_limit)

    if merged is None:
        return None
    if modules:
        merged["response_module_hint"] = modules[0] if len(modules) == 1 else "mixed"
    if absolute_limit is not None:
        merged["max_reply_chars"] = absolute_limit
    return merged
