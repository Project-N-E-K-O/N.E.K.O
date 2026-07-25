"""Runtime module assembly for NEKO Live."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from ..modules._base import ReservedModule
from .module_registry import ModuleRegistry
from .live_provider_router import LiveProviderRouter
from .pipeline import LivePipeline


def assemble_runtime_modules(runtime: Any) -> None:
    """Create owned modules, register them, and attach the pipeline."""
    runtime.registry = ModuleRegistry()
    runtime.bili_live_ingest = _create_module("bili_live_ingest", "BiliLiveIngestModule", "Bilibili live ingest")
    runtime.bili_identity = _create_module("bili_identity", "BiliIdentityModule", "Bilibili identity")
    runtime.douyin_live_ingest = _create_module("douyin_live_ingest", "DouyinLiveIngestModule", "Douyin live ingest")
    runtime.douyin_identity = _create_module("douyin_identity", "DouyinIdentityModule", "Douyin identity")
    runtime.twitch_live_ingest = _create_module("twitch_live_ingest", "TwitchLiveIngestModule", "Twitch live ingest")
    runtime.twitch_identity = _create_module("twitch_identity", "TwitchIdentityModule", "Twitch identity")
    runtime.live_provider = LiveProviderRouter(runtime)
    runtime.live_audience_session = _create_module(
        "live_audience_session",
        "LiveAudienceSessionModule",
        "Live audience session",
    )
    runtime.viewer_profile = _create_module("viewer_profile", "ViewerProfileModule", "Viewer profile")
    runtime.avatar_roast = _create_module("avatar_roast", "AvatarRoastModule", "Avatar roast")
    runtime.danmaku_response = _create_module("danmaku_response", "DanmakuResponseModule", "Danmaku response")
    runtime.live_support_events = _create_module("live_support_events", "LiveSupportEventsModule", "Live support events")
    runtime.active_engagement = _create_module("active_engagement", "ActiveEngagementModule", "Active engagement")
    runtime.warmup_hosting = _create_module("warmup_hosting", "WarmupHostingModule", "Warmup hosting")
    runtime.developer_sandbox = _create_module("developer_sandbox", "DeveloperSandboxModule", "Developer sandbox")
    runtime.live_events = _create_module("live_events", "LiveEventsModule", "Live events")
    runtime.pipeline = LivePipeline(runtime)
    runtime.plugin_dir = Path(__file__).resolve().parents[1]

    for module in registered_modules(runtime):
        runtime.registry.register(module)


def registered_modules(runtime: Any) -> tuple[Any, ...]:
    """Return the runtime module registration order."""
    return (
        runtime.bili_live_ingest,
        runtime.douyin_live_ingest,
        runtime.twitch_live_ingest,
        runtime.bili_identity,
        runtime.douyin_identity,
        runtime.twitch_identity,
        runtime.live_audience_session,
        runtime.viewer_profile,
        runtime.avatar_roast,
        runtime.danmaku_response,
        runtime.live_support_events,
        runtime.active_engagement,
        runtime.warmup_hosting,
        runtime.developer_sandbox,
        runtime.live_events,
    )


def _create_module(module_id: str, class_name: str, title: str) -> Any:
    try:
        module = import_module(f"..modules.{module_id}", package=__package__)
        cls = getattr(module, class_name)
    except (ImportError, AttributeError):
        return ReservedModule(module_id, title)
    return cls()
