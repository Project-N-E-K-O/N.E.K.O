"""Developer sandbox actions for the runtime."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionResult, ViewerEvent, ViewerProfile


async def handle_sandbox_target(runtime: Any, **kwargs: Any) -> InteractionResult:
    require_developer_mode(runtime)
    event = runtime.developer_sandbox.parse_target(**kwargs)
    return await runtime.pipeline.handle_event(event)


async def lookup_bili_user(runtime: Any, **kwargs: Any) -> dict[str, Any]:
    require_developer_mode(runtime)
    event = runtime.developer_sandbox.parse_target(**kwargs, use_presets=False)
    if not event.uid:
        raise ValueError("uid or Bilibili space URL is required")
    identity = await runtime.bili_identity.resolve(event)
    profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
    identity_payload = identity.to_public_dict()
    identity_payload["avatar_preview_url"] = ""
    identity_payload["avatar_preview_data_url"] = ""
    runtime.audit.record(
        "developer_lookup",
        "bili user looked up",
        detail={"uid": identity.uid, "fetched": identity.fetched},
    )
    return {
        "event": event.to_dict(),
        "identity": identity_payload,
        "profile": profile.to_dict(),
    }


def clear_sandbox_data(runtime: Any) -> dict[str, Any]:
    cleared_records = len(runtime.recent_sandbox_results)
    runtime.recent_sandbox_results.clear()
    cleared_preview_files = 0
    preview_dir = runtime.plugin_dir / "static" / "avatar-preview"
    if preview_dir.is_dir():
        for path in preview_dir.iterdir():
            if not path.is_file():
                continue
            try:
                path.unlink()
                cleared_preview_files += 1
            except OSError:
                runtime.audit.record("sandbox_preview_clear_failed", path.name, level="warning")
    runtime.audit.record(
        "sandbox_clear",
        "sandbox runtime data cleared",
        detail={"records": cleared_records, "preview_files": cleared_preview_files},
    )
    return {"records": cleared_records, "preview_files": cleared_preview_files}


def require_developer_mode(runtime: Any) -> None:
    if not runtime.config.developer_tools_enabled:
        raise PermissionError("developer mode is disabled")


async def handle_manual_event(runtime: Any, **kwargs: Any) -> InteractionResult:
    require_developer_mode(runtime)
    event = ViewerEvent(
        uid=str(kwargs.get("uid") or "").strip(),
        nickname=str(kwargs.get("nickname") or "").strip(),
        avatar_url=str(kwargs.get("avatar_url") or "").strip(),
        danmaku_text=str(kwargs.get("danmaku_text") or "").strip(),
        target_lanlan=str(kwargs.get("target_lanlan") or kwargs.get("lanlan_name") or "").strip(),
        source="manual_live_simulation",
        live_mode=runtime.config.live_mode,
        raw=dict(kwargs),
    )
    return await runtime.pipeline.handle_event(event)
