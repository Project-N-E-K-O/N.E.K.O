"""Single NEKO output boundary for Neko Roast."""

from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace
from typing import Any

from ..core.contracts import InteractionRequest

_AVATAR_INLINE_BUDGET_BYTES = 120 * 1024


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
            source="neko_roast",
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
            description="Neko Roast behavior instructions",
            result_name="instructions_queued",
        )

    async def push_context_restore(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="Neko Roast behavior restore",
            result_name="instructions_restored",
        )

    async def push_developer_instructions(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="Neko Roast developer mode instructions",
            result_name="developer_instructions_queued",
        )

    async def push_developer_restore(self, text: str) -> str:
        return await self._push_context_text(
            text,
            description="Neko Roast developer mode restore",
            result_name="developer_instructions_restored",
        )

    async def push_developer_announcement(self, text: str) -> str:
        target_lanlan = resolve_plugin_target_lanlan(self.plugin)
        metadata = {"plugin": "neko_roast", "developer_mode": True}
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        result = self.plugin.push_message(
            source="neko_roast",
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
        # 锐评指令由 avatar_roast.build_request 集中构造（自适应焦点 / META / 禁脑补）。
        text = request.prompt_text or ""
        if is_demo_event:
            text = "（这是猫娘锐评插件的内置演示，也请像真实弹幕一样直接回应。）\n" + text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if identity.avatar_bytes:
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
            # 安全测试态：整条 pipeline 已跑完（身份/锐评/头像/安全门），但不真的投给猫猫。
            return (
                f"dry_run(target={target_lanlan or 'none'}, ai_behavior=respond, "
                f"visibility=none, image_part_bytes={image_part_bytes}, text_len={len(text)})"
            )
        if request.event.source == "developer_sandbox" and not target_lanlan:
            raise ValueError("missing_target_lanlan: 当前界面猫猫不可用，无法发送模拟弹幕。")
        metadata = {
            "plugin": "neko_roast",
            "uid": identity.uid,
            "live_mode": request.live_mode,
            "demo": is_demo_event,
        }
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        result = self.plugin.push_message(
            source="neko_roast",
            visibility=[],
            ai_behavior="respond",
            parts=parts,
            priority=8 if is_demo_event else 5,
            coalesce_key=f"neko_roast_demo:{identity.uid}:{request.event.seen_at}" if is_demo_event else "",
            metadata=metadata,
            target_lanlan=target_lanlan or None,
        )
        if asyncio.iscoroutine(result):
            await result
        return (
            f"queued_to_neko(target={target_lanlan}, ai_behavior=respond, "
            f"visibility=none, image_part_bytes={image_part_bytes})"
        )
