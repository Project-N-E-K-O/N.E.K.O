"""Viewer identity/profile preparation for the interaction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import PipelineStep, ViewerEvent, ViewerIdentity, ViewerProfile
from .live_provider_router import identity_provider_for
from .runtime_live_session import is_current_live_session_event
from .runtime_timeline import record_timeline


@dataclass(frozen=True)
class PipelineViewerContext:
    identity: ViewerIdentity
    profile: ViewerProfile
    is_transient_event: bool
    session_current: bool = True


async def resolve_viewer_context(
    ctx: Any,
    event: ViewerEvent,
    steps: list[PipelineStep],
    *,
    is_transient_event: bool,
) -> PipelineViewerContext:
    provider = identity_provider_for(ctx)
    identity = await provider.resolve_identity(event)
    identity_step_id = provider.identity_step_id()
    record_timeline(
        ctx,
        event,
        stage=identity_step_id,
        status="ok" if not identity.error else "failed",
        reason=identity.error,
    )
    steps.append(
        PipelineStep(
            identity_step_id,
            "ok" if not identity.error else "failed",
            identity.error,
        )
    )

    if not is_current_live_session_event(ctx, event):
        steps.append(PipelineStep("live_session", "skipped", "live_session.stale"))
        record_timeline(
            ctx,
            event,
            stage="live_session",
            status="skipped",
            reason="live_session.stale",
        )
        return PipelineViewerContext(
            identity=identity,
            profile=ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname,
                avatar_url=identity.avatar_url,
            ),
            is_transient_event=is_transient_event,
            session_current=False,
        )

    if is_transient_event:
        profile = ViewerProfile(
            uid=identity.uid,
            nickname=identity.nickname,
            avatar_url=identity.avatar_url,
        )
        steps.append(
            PipelineStep(
                "viewer_profile",
                "skipped",
                f"{event.source} uses transient profile",
            )
        )
        record_timeline(
            ctx,
            event,
            stage="viewer_profile",
            status="skipped",
            reason=f"{event.source} uses transient profile",
        )
    else:
        profile = await ctx.viewer_profile.upsert(identity)
        if _should_record_live_danmaku(event):
            recorder = getattr(ctx.viewer_profile, "record_live_danmaku", None)
            if callable(recorder):
                try:
                    recorded_profile = await recorder(identity, event.danmaku_text)
                except Exception as exc:
                    message = f"record_live_danmaku_failed: {type(exc).__name__}"
                    steps.append(
                        PipelineStep(
                            "viewer_profile.record_live_danmaku",
                            "failed",
                            message,
                        )
                    )
                    record_timeline(
                        ctx,
                        event,
                        stage="viewer_profile.record_live_danmaku",
                        status="failed",
                        reason=message,
                    )
                else:
                    if recorded_profile is not None:
                        profile = recorded_profile
        steps.append(PipelineStep("viewer_profile", "ok"))
        record_timeline(ctx, event, stage="viewer_profile", status="ok")

    return PipelineViewerContext(
        identity=identity,
        profile=profile,
        is_transient_event=is_transient_event,
    )


def _should_record_live_danmaku(event: ViewerEvent) -> bool:
    return bool(
        event.source == "live_danmaku"
        and event.danmaku_text
        and not event.uid.startswith("__neko_")
    )
