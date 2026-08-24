"""Pipeline request/result contracts for NEKO Live interactions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .contracts_events import ViewerEvent
from .contracts_public import (
    TOPIC_PRIVACY_PUBLIC,
    public_bool,
    public_dict,
    public_text,
    topic_privacy_classification,
)
from .contracts_types import LiveMode, RoastStrength, _response_latency_ms, utc_now_iso
from .contracts_viewer import ViewerIdentity, ViewerProfile


@dataclass
class InteractionRequest:
    event: ViewerEvent
    identity: ViewerIdentity
    profile: ViewerProfile
    prompt_text: str
    live_mode: LiveMode
    strength: RoastStrength
    should_push: bool = True
    dry_run: bool = False
    allow_avatar_image: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        metadata = public_dict(self.metadata)
        topic = (
            self.event.raw.get("topic_material")
            if isinstance(self.event.raw, dict)
            else None
        )
        if topic_privacy_classification(topic) != TOPIC_PRIVACY_PUBLIC:
            for key in ("topic_title", "topic_key", "topic_hook", "topic_evidence"):
                metadata.pop(key, None)
        return {
            "event": self.event.to_dict(),
            "identity": self.identity.to_public_dict(),
            "profile": self.profile.to_dict(),
            "live_mode": _safe_live_mode(self.live_mode),
            "strength": _safe_strength(self.strength),
            "should_push": public_bool(self.should_push),
            "dry_run": public_bool(self.dry_run),
            "allow_avatar_image": public_bool(self.allow_avatar_image),
            "reason": public_text(self.reason),
            "metadata": metadata,
        }


@dataclass
class PipelineStep:
    id: str
    status: Literal["ok", "dry_run", "skipped", "failed"]
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": public_text(self.id),
            "status": _safe_step_status(self.status),
            "message": public_text(self.message),
        }


@dataclass
class InteractionResult:
    accepted: bool
    status: Literal["queued", "dry_run", "pushed", "skipped", "failed"]
    event: ViewerEvent
    identity: ViewerIdentity | None = None
    profile: ViewerProfile | None = None
    request: InteractionRequest | None = None
    output: str = ""
    reason: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    dispatcher_latency_ms: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        response_latency_ms = _response_latency_ms(self.event.seen_at, self.created_at)
        pipeline_latency_ms = response_latency_ms
        return {
            "accepted": public_bool(self.accepted),
            "status": _safe_result_status(self.status),
            "event": self.event.to_dict(),
            "identity": self.identity.to_public_dict() if self.identity else None,
            "profile": self.profile.to_dict() if self.profile else None,
            "request": self.request.to_public_dict() if self.request else None,
            "output": public_text(self.output),
            "reason": public_text(self.reason),
            "steps": [step.to_dict() for step in self.steps],
            "created_at": public_text(self.created_at),
            "response_latency_ms": response_latency_ms,
            "pipeline_latency_ms": pipeline_latency_ms,
            "dispatcher_latency_ms": _safe_latency_ms(self.dispatcher_latency_ms),
        }

    def to_sandbox_dict(self) -> dict[str, Any]:
        response_latency_ms = _response_latency_ms(self.event.seen_at, self.created_at)
        pipeline_latency_ms = response_latency_ms
        return {
            "accepted": public_bool(self.accepted),
            "status": _safe_result_status(self.status),
            "uid": public_text(self.event.uid),
            "nickname": public_text(self.event.nickname),
            "output": public_text(self.output),
            "reason": public_text(self.reason),
            "steps": [step.to_dict() for step in self.steps],
            "created_at": public_text(self.created_at),
            "response_latency_ms": response_latency_ms,
            "pipeline_latency_ms": pipeline_latency_ms,
            "dispatcher_latency_ms": _safe_latency_ms(self.dispatcher_latency_ms),
        }


def _safe_live_mode(value: Any) -> str:
    return value if isinstance(value, str) and value in {"co_stream", "solo_stream"} else "co_stream"


def _safe_strength(value: Any) -> str:
    return value if isinstance(value, str) and value in {"gentle", "normal", "sharp"} else "normal"


def _safe_step_status(value: Any) -> str:
    return value if isinstance(value, str) and value in {"ok", "dry_run", "skipped", "failed"} else "failed"


def _safe_result_status(value: Any) -> str:
    if isinstance(value, str) and value in {"queued", "dry_run", "pushed", "skipped", "failed"}:
        return value
    return "failed"


def _safe_latency_ms(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        latency = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, latency)
