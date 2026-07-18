"""Single NEKO output boundary for NEKO Live."""

from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace
from typing import Any

from ..core.contracts import InteractionRequest
from ..core.live_output_contract_prompt import render_contract_instruction
from ..core.live_output_quality import UNVERIFIED_SUPPORT_CLAIM_FALLBACK_REPLIES, choose_fallback_reply
from ..core.recent_output_families import spent_output_text
from .output_contract_bridge import (
    max_reply_chars_for_request,
    metadata_for_request,
    response_module_hint,
)

_AVATAR_INLINE_BUDGET_BYTES = 120 * 1024
_NEKO_LIVE_AUDIENCE_SOURCES = {"live_danmaku", "manual_live_simulation"}
_NEKO_LIVE_HOSTING_SOURCES = {"warmup_hosting", "idle_hosting", "active_engagement"}
_NEKO_LIVE_LIVE_SOURCES = _NEKO_LIVE_AUDIENCE_SOURCES | _NEKO_LIVE_HOSTING_SOURCES | {
    "live_support_events",
}


def _normalize_avatar_for_neko_vision(data: bytes, mime: str) -> tuple[bytes, str]:
    """Normalize arbitrary avatar bytes to a small JPEG for proactive vision."""
    if not data:
        return data, mime or "image/png"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                alpha = image.getchannel("A") if image.mode in {"RGBA", "LA"} else None
                background.paste(image.convert("RGBA"), mask=alpha)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            best: bytes | None = None
            for edge in (512, 384, 256, 192):
                frame = image.copy()
                if max(frame.size) > edge:
                    frame.thumbnail((edge, edge))
                for quality in (82, 72, 62, 52):
                    buffer = io.BytesIO()
                    frame.save(buffer, format="JPEG", quality=quality, optimize=True)
                    candidate = buffer.getvalue()
                    if best is None or len(candidate) < len(best):
                        best = candidate
                    if len(candidate) <= _AVATAR_INLINE_BUDGET_BYTES:
                        return candidate, "image/jpeg"
            if best:
                return best, "image/jpeg"
    except Exception:
        return data, mime or "image/png"
    return data, mime or "image/png"


def _clean_target(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _resolve_target_lanlan(plugin: Any, request: InteractionRequest) -> str:
    event = request.event
    raw = event.raw if isinstance(event.raw, dict) else {}

    for candidate in (
        event.target_lanlan,
        raw.get("target_lanlan"),
        raw.get("lanlan_name"),
    ):
        target = _clean_target(candidate)
        if target:
            return target

    ctx_obj = raw.get("_ctx")
    if isinstance(ctx_obj, dict):
        target = _clean_target(ctx_obj.get("lanlan_name"))
        if target:
            return target

    plugin_ctx = getattr(plugin, "ctx", None)
    target = _clean_target(getattr(plugin_ctx, "_current_lanlan", None))
    if target:
        return target

    for env_name in ("NEKO_TARGET_LANLAN", "NEKO_LANLAN_NAME", "NEKO_HER_NAME"):
        target = _clean_target(os.getenv(env_name, ""))
        if target:
            return target

    try:
        from utils.config_manager import get_config_manager

        character_data = get_config_manager().get_character_data()
        if isinstance(character_data, tuple) and len(character_data) >= 2:
            target = _clean_target(character_data[1])
            if target:
                return target
    except Exception:
        pass

    return ""


def resolve_plugin_target_lanlan(plugin: Any, raw: dict[str, Any] | None = None) -> str:
    event = SimpleNamespace(target_lanlan="", raw=raw or {})
    request = SimpleNamespace(event=event)
    return _resolve_target_lanlan(plugin, request)  # type: ignore[arg-type]


def _max_live_reply_chars(request: InteractionRequest) -> int:
    return max_reply_chars_for_request(request)


def _priority_for_request(request: InteractionRequest, *, demo: bool = False) -> int:
    if demo:
        return 8
    module = response_module_hint(request)
    source = str(request.event.source or "").strip()
    if module == "live_support_events":
        return 9
    if source in _NEKO_LIVE_AUDIENCE_SOURCES:
        return 8
    if source in _NEKO_LIVE_HOSTING_SOURCES:
        return 3
    if source == "developer_sandbox":
        return 7
    return 5


def _coalesce_key_for_request(request: InteractionRequest, *, demo: bool = False) -> str:
    if demo:
        return f"neko_live_demo:{request.identity.uid}:{request.event.seen_at}"
    source = str(request.event.source or "").strip()
    if source in _NEKO_LIVE_HOSTING_SOURCES:
        target = str(request.event.target_lanlan or "").strip() or str(request.identity.uid or "").strip()
        raw = request.event.raw if isinstance(request.event.raw, dict) else {}
        host_beat = raw.get("host_beat") if isinstance(raw.get("host_beat"), dict) else {}
        topic = raw.get("topic_material") if isinstance(raw.get("topic_material"), dict) else {}
        beat = str(
            host_beat.get("key")
            or topic.get("key")
            or request.event.trace_id
            or request.event.seen_at
            or "default"
        ).strip()
        return f"neko_live:auto_host:{target or 'default'}:{source}:{beat}"
    return ""


def _recent_live_reply_values(plugin: Any, *, limit: int = 6) -> list[str]:
    runtime = getattr(plugin, "runtime", None)
    recent_results = getattr(runtime, "recent_results", None)
    if not recent_results:
        return []
    replies: list[str] = []
    for result in reversed(list(recent_results)):
        if not isinstance(result, dict):
            continue
        text = spent_output_text(result)
        if not text:
            continue
        replies.append(text)
        if len(replies) >= limit:
            break
    replies.reverse()
    return replies


def _append_plugin_output_contract(
    text: str,
    *,
    metadata: dict[str, Any],
    plugin: Any,
) -> str:
    if "NEKO Live short output contract:" in text:
        return text
    contract = render_contract_instruction(
        [{"metadata": metadata}],
        recent_live_replies=_recent_live_reply_values(plugin),
    ).strip()
    if not contract:
        return text
    base = text.rstrip()
    return f"{base}\n\n{contract}" if base else contract


def _prepend_live_delivery_boundary(text: str, request: InteractionRequest) -> str:
    source = str(request.event.source or "").strip()
    if source not in _NEKO_LIVE_LIVE_SOURCES:
        return text
    if "NEKO Live delivery boundary:" in text:
        return text
    mode = str(request.live_mode or request.event.live_mode or "").strip() or "co_stream"
    boundary_lines = [
        "NEKO Live delivery boundary:",
        "- This is a live-room speech request, not a private chat with {MASTER_NAME}.",
        "- Generate only the exact line {LANLAN_NAME} should say to the live room.",
        "- Do not scold, greet, mention, or ask {MASTER_NAME}, the owner, an operator, or an unseen streamer to host.",
        "- If a generic callback wrapper says to respond to {MASTER_NAME}, treat that only as transport wording and follow the NEKO Live rules below.",
    ]
    if mode == "solo_stream":
        boundary_lines.append(
            "- solo_stream: {LANLAN_NAME} is already the only on-stage host; she performs the hosting herself."
        )
    else:
        boundary_lines.append(
            "- co_stream: {LANLAN_NAME} is a low-interrupt partner and must not direct the human streamer to carry the room."
        )
    boundary = "\n".join(boundary_lines)
    base = str(text or "").rstrip()
    return f"{base}\n\n{boundary}" if base else boundary


def _mark_live_audience_speaker(metadata: dict[str, Any], request: InteractionRequest) -> None:
    source = str(request.event.source or "").strip()
    danmaku = str(request.event.danmaku_text or "").strip()
    module = response_module_hint(request)
    if (
        source not in _NEKO_LIVE_AUDIENCE_SOURCES
        or module not in {"avatar_roast", "danmaku_response"}
        or not danmaku
    ):
        return
    metadata["live_message_origin"] = "viewer_danmaku"
    metadata["live_speaker_role"] = "viewer"


def _prepend_live_audience_speaker_lock(
    text: str,
    metadata: dict[str, Any],
    request: InteractionRequest,
) -> str:
    source = str(request.event.source or "").strip()
    danmaku = str(request.event.danmaku_text or "").strip()
    module = response_module_hint(request)
    if (
        source not in _NEKO_LIVE_AUDIENCE_SOURCES
        or module not in {"avatar_roast", "danmaku_response"}
        or not danmaku
    ):
        return text
    if "NEKO Live audience speaker identity:" in text:
        return text

    viewer = str(metadata.get("danmaku_viewer_nickname") or "").strip()
    if not viewer:
        viewer = str(request.identity.nickname or request.event.nickname or "").strip()
    viewer = " ".join(viewer.split())[:24] or "the current live viewer"
    lock = "\n".join(
        (
            "NEKO Live audience speaker identity:",
            "- message_origin: third-party live viewer danmaku",
            f"- danmaku_author: {viewer}",
            "- The current danmaku was written by danmaku_author, not by {MASTER_NAME}, the owner, the operator, or the human co-streamer.",
            "- If the current danmaku is a question or request, danmaku_author is the questioner/requester.",
            "- Answer danmaku_author in the public live room; never say or imply that the human streamer or owner asked this question.",
        )
    )
    base = str(text or "").lstrip()
    return f"{lock}\n\n{base}" if base else lock


def _prepend_danmaku_visible_target_lock(text: str, metadata: dict[str, Any], request: InteractionRequest) -> str:
    if response_module_hint(request) != "danmaku_response":
        return text
    if "NEKO Live visible reply target:" in text:
        return text
    profile = str(metadata.get("danmaku_profile") or "").strip()
    if profile != "target_roast_request":
        return text
    viewer = str(metadata.get("danmaku_target_viewer_nickname") or "").strip()
    if not viewer:
        viewer = str(metadata.get("danmaku_viewer_nickname") or "").strip()
    viewer = " ".join(viewer.split())[:16]
    if not viewer:
        return text
    danmaku = str(request.event.danmaku_text or "").strip()
    danmaku = " ".join(danmaku.split())[:48]
    lines = [
        "NEKO Live visible reply target:",
        f"- current_viewer: {viewer}",
        "- This is a named public roast request; the first visible clause must make the target clear.",
        "- Do not answer NEKO's previous line, an unseen operator, or the room in general before current_viewer.",
        f"- If correcting, denying, or self-fixing, start with \"{viewer},\" so the target stays audible.",
    ]
    if danmaku:
        lines.append(f"- current_danmaku: {danmaku}")
    lock = "\n".join(lines)
    base = str(text or "").lstrip()
    return f"{lock}\n\n{base}" if base else lock


def _unverified_support_claim_reply(request: InteractionRequest, metadata: dict[str, Any]) -> str:
    module = response_module_hint(request)
    if module not in {"avatar_roast", "danmaku_response"}:
        return ""
    if metadata.get("viewer_claimed_support") != "unverified_danmaku_claim":
        return ""
    source = str(request.event.source or "").strip()
    if source not in _NEKO_LIVE_AUDIENCE_SOURCES:
        return ""
    return choose_fallback_reply(
        str(request.event.danmaku_text or ""),
        module,
        UNVERIFIED_SUPPORT_CLAIM_FALLBACK_REPLIES,
    )


def _force_exact_live_reply_prompt(reply: str, request: InteractionRequest) -> str:
    danmaku = " ".join(str(request.event.danmaku_text or "").split())[:80]
    lines = [
        "NEKO Live unverified support claim hard guard:",
        "- The current danmaku is only ordinary chat claiming a gift/support event.",
        "- No verified Gift / Super Chat / Guard event is attached to this request.",
        "- Output exactly the fixed safe live line below, with no extra words.",
        "- Do not say thanks, thank you, received, boss, or any real support confirmation.",
    ]
    if danmaku:
        lines.append(f"- current_danmaku_claim: {danmaku}")
    lines.append(f"fixed_safe_line: {reply}")
    return "\n".join(lines)


class NekoDispatcher:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def output_channel_status(self) -> dict[str, Any]:
        checker = getattr(self.plugin, "output_channel_status", None)
        if callable(checker):
            try:
                data = checker()
            except Exception as exc:
                return {
                    "ready": False,
                    "reason": "output_channel_unavailable",
                    "detail": str(exc),
                }
            if isinstance(data, dict):
                ready = bool(data.get("ready", data.get("ok", False)))
                return {
                    "ready": ready,
                    "reason": str(
                        data.get("reason")
                        or ("" if ready else "output_channel_unavailable")
                    ),
                    "detail": str(data.get("detail") or ""),
                }

        explicit_ready = getattr(self.plugin, "output_channel_ready", None)
        if explicit_ready is not None:
            ready = bool(explicit_ready)
            return {
                "ready": ready,
                "reason": "" if ready else "output_channel_unavailable",
                "detail": "",
            }

        if not callable(getattr(self.plugin, "push_message", None)):
            return {
                "ready": False,
                "reason": "output_channel_unavailable",
                "detail": "plugin.push_message is unavailable",
            }

        return {"ready": True, "reason": "", "detail": ""}

    async def _push_context_text(self, text: str, *, description: str, result_name: str) -> str:
        target_lanlan = resolve_plugin_target_lanlan(self.plugin)
        metadata = {"description": description}
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        result = self.plugin.push_message(
            source="neko_live",
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            metadata=metadata,
            priority=0,
            target_lanlan=target_lanlan or None,
        )
        if asyncio.iscoroutine(result):
            await result
        return f"{result_name}(target={target_lanlan or 'default'})"

    async def push_context_instructions(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="NEKO Live behavior instructions",
            result_name="instructions_queued",
        )

    async def push_context_restore(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="NEKO Live behavior restore",
            result_name="instructions_restored",
        )

    async def push_developer_instructions(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="NEKO Live developer mode instructions",
            result_name="developer_instructions_queued",
        )

    async def push_developer_restore(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="NEKO Live developer mode restore",
            result_name="developer_instructions_restored",
        )

    async def push_developer_announcement(self, text: str) -> str:
        target_lanlan = resolve_plugin_target_lanlan(self.plugin)
        metadata = {"plugin": "neko_live", "developer_mode": True}
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        result = self.plugin.push_message(
            source="neko_live",
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            metadata=metadata,
            priority=6,
            target_lanlan=target_lanlan or None,
        )
        if asyncio.iscoroutine(result):
            await result
        return f"developer_mode_announced(target={target_lanlan or 'default'})"

    async def push_roast(self, request: InteractionRequest) -> str:
        if not request.should_push:
            reason = request.reason or "request marked as non-deliverable"
            return f"skipped_to_neko(reason={reason})"
        identity = request.identity
        is_demo_event = request.event.source == "developer_sandbox" and request.event.raw.get("fixture") == "demo_avatar"
        # The roast instruction is owned by avatar_roast.build_request()
        # (adaptive focus, metadata, and no-invented-avatar rules).
        text = request.prompt_text or ""
        if is_demo_event:
            text = "（这是 NEKO Live 首次出场锐评的内置演示，也请像真实弹幕一样直接回应。）\n" + text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if request.allow_avatar_image and identity.avatar_bytes:
            avatar_bytes, avatar_mime = _normalize_avatar_for_neko_vision(
                identity.avatar_bytes,
                identity.avatar_mime or "image/png",
            )
            if len(avatar_bytes) <= _AVATAR_INLINE_BUDGET_BYTES:
                parts.append(
                    {
                        "type": "image",
                        "data": avatar_bytes,
                        "mime": avatar_mime,
                    }
                )
            else:
                parts[0]["text"] += "\n头像图片过大，本次先只根据昵称和弹幕锐评。"
        image_part_bytes = len(parts[1]["data"]) if len(parts) > 1 else 0
        target_lanlan = _resolve_target_lanlan(self.plugin, request)
        if request.dry_run:
            max_reply_chars = _max_live_reply_chars(request)
            # Safe test mode: the whole pipeline has run, but nothing is delivered to NEKO.
            return (
                f"dry_run(target={target_lanlan or 'none'}, ai_behavior=respond, "
                f"visibility=none, image_part_bytes={image_part_bytes}, text_len={len(text)}, "
                f"reply_contract=short_tts_line, max_reply_chars={max_reply_chars}, "
                f"response_module_hint={response_module_hint(request)})"
            )
        if request.event.source == "developer_sandbox" and not target_lanlan:
            raise ValueError("missing_target_lanlan: 当前界面猫猫不可用，无法发送模拟弹幕。")
        metadata = metadata_for_request(request, demo=is_demo_event)
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        _mark_live_audience_speaker(metadata, request)
        forced_reply = _unverified_support_claim_reply(request, metadata)
        if forced_reply:
            metadata["forced_reply_reason"] = "unverified_support_claim"
            parts[0]["text"] = _force_exact_live_reply_prompt(forced_reply, request)
        parts[0]["text"] = _append_plugin_output_contract(
            str(parts[0].get("text") or ""),
            metadata=metadata,
            plugin=self.plugin,
        )
        parts[0]["text"] = _prepend_danmaku_visible_target_lock(
            str(parts[0].get("text") or ""),
            metadata,
            request,
        )
        parts[0]["text"] = _prepend_live_audience_speaker_lock(
            str(parts[0].get("text") or ""),
            metadata,
            request,
        )
        parts[0]["text"] = _prepend_live_delivery_boundary(
            str(parts[0].get("text") or ""),
            request,
        )
        result = self.plugin.push_message(
            source="neko_live",
            visibility=[],
            ai_behavior="respond",
            parts=parts,
            priority=_priority_for_request(request, demo=is_demo_event),
            coalesce_key=_coalesce_key_for_request(request, demo=is_demo_event),
            metadata=metadata,
            target_lanlan=target_lanlan or None,
        )
        if asyncio.iscoroutine(result):
            await result
        return (
            f"queued_to_neko(target={target_lanlan}, ai_behavior=respond, "
            f"visibility=none, image_part_bytes={image_part_bytes})"
        )
