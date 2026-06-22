"""Shared contracts for the Neko Roast viewer-interaction pipeline."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

LiveMode = Literal["co_stream", "solo_stream"]
RoastStrength = Literal["gentle", "normal", "sharp"]
TriggerSource = Literal["live_danmaku", "developer_sandbox", "manual_live_simulation"]
SafetyStatus = Literal["running", "paused", "degraded", "tripped", "disconnected"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_LIVE_ROOM_URL_RE = re.compile(r"live\.bilibili\.com/(?:h5/|blanc/)?(\d+)", re.IGNORECASE)


def parse_room_id(value: Any) -> int:
    """房号 / 纯数字串 / B站直播间链接 → 数字房号；解析不出返回 0。

    接受 int、纯数字串、含 ``live.bilibili.com/<id>`` 的 URL（可带 ``/h5/``、``/blanc/``、
    query、路径）。让面板/动作能直接粘直播间链接，而不必手动找房号。
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = _LIVE_ROOM_URL_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return 0
    return 0


@dataclass
class RoastConfig:
    live_room_id: int = 0
    live_mode: LiveMode = "co_stream"
    live_enabled: bool = False
    developer_tools_enabled: bool = False
    dry_run: bool = True  # 安全测试态：跑完整 pipeline 但不真的投给猫猫
    roast_once_per_uid: bool = True
    roast_strength: RoastStrength = "normal"
    co_stream_output_policy: str = "auto_low_interrupt"
    solo_output_policy: str = "auto_rate_limited"
    avatar_fetch_timeout_seconds: float = 8.0
    recent_limit: int = 30
    rate_limit_seconds: int = 20
    queue_limit: int = 5
    safety_auto_stop_enabled: bool = True
    safety_window_seconds: int = 60
    safety_pipeline_failure_limit: int = 3
    safety_output_failure_limit: int = 2
    safety_queue_overflow_limit: int = 3
    viewer_store_dir: str = ""  # 观众档案 JSON 存储目录；留空=插件数据目录。主播可在「设置」改。

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RoastConfig":
        raw = dict(data or {})
        live_mode = str(raw.get("live_mode") or "co_stream")
        if live_mode == "solo":
            live_mode = "solo_stream"
        if live_mode not in {"co_stream", "solo_stream"}:
            live_mode = "co_stream"
        roast_strength = str(raw.get("roast_strength") or "normal")
        if roast_strength not in {"gentle", "normal", "sharp"}:
            roast_strength = "normal"
        return cls(
            live_room_id=parse_room_id(raw.get("live_room_id")),
            live_mode=live_mode,  # type: ignore[arg-type]
            live_enabled=bool(raw.get("live_enabled", False)),
            developer_tools_enabled=bool(raw.get("developer_tools_enabled", False)),
            dry_run=bool(raw.get("dry_run", True)),
            roast_once_per_uid=bool(raw.get("roast_once_per_uid", True)),
            roast_strength=roast_strength,  # type: ignore[arg-type]
            co_stream_output_policy=str(raw.get("co_stream_output_policy") or "auto_low_interrupt"),
            solo_output_policy=str(raw.get("solo_output_policy") or "auto_rate_limited"),
            avatar_fetch_timeout_seconds=float(
                raw.get("avatar_fetch_timeout_seconds")
                if raw.get("avatar_fetch_timeout_seconds") is not None
                else 8
            ),
            recent_limit=max(1, min(int(raw.get("recent_limit") or 30), 200)),
            rate_limit_seconds=max(0, min(int(raw.get("rate_limit_seconds") if raw.get("rate_limit_seconds") is not None else 20), 3600)),
            queue_limit=max(1, min(int(raw.get("queue_limit") or 5), 100)),
            safety_auto_stop_enabled=bool(raw.get("safety_auto_stop_enabled", True)),
            safety_window_seconds=max(5, min(int(raw.get("safety_window_seconds") or 60), 3600)),
            safety_pipeline_failure_limit=max(1, min(int(raw.get("safety_pipeline_failure_limit") or 3), 100)),
            safety_output_failure_limit=max(1, min(int(raw.get("safety_output_failure_limit") or 2), 100)),
            safety_queue_overflow_limit=max(1, min(int(raw.get("safety_queue_overflow_limit") or 3), 100)),
            viewer_store_dir=str(raw.get("viewer_store_dir") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ViewerEvent:
    uid: str
    nickname: str = ""
    avatar_url: str = ""
    danmaku_text: str = ""
    target_lanlan: str = ""
    source: TriggerSource = "developer_sandbox"
    live_mode: LiveMode = "co_stream"
    seen_at: str = field(default_factory=utc_now_iso)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class LiveEvent:
    """直播事件统一信封：把各类直播事件（弹幕 / 礼物 / SC / 上舰 / 进场…）包成同一形状，
    经 ``EventBus`` 按 ``type`` 路由给订阅了该类型的 handler 模块。这是「分发给其他开发者
    各写各事件 handler」的核心数据契约。

    - ``type``：路由键（``"danmaku"`` / ``"gift"`` / ``"super_chat"`` / ``"guard"`` /
      ``"entry"`` / …）。接入侧由命令名映射而来。
    - ``uid``：触发用户 UID（str；空串=无主体事件）。
    - ``payload``：该类型专属字段的轻量 dict（handler 自解）。**各类型的精确 payload schema
      随对应 handler 落地时敲定**（见 roadmap §7-2，由首个非弹幕事件决定形状）。
    - ``source``：事件来源（``"live"`` / ``"developer_sandbox"`` / …）。
    - ``ts``：事件时间戳（epoch 秒；由调用方注入，便于测试确定性）。
    - ``schema_version``：信封版本，演进时供兼容判断。
    - ``raw``：原始富模型对象（如 ``LiveDanmaku``）。需要完整字段（如 ``get_score()``）的
      handler 走 ``raw``；信封本身不约束其形状。
    """

    type: str
    uid: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "live"
    ts: float = 0.0
    schema_version: int = 1
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        # 不含 raw（富模型可能很大 / 不可序列化）；轻量调试视图。
        return {
            "type": self.type,
            "uid": self.uid,
            "payload": dict(self.payload),
            "source": self.source,
            "ts": self.ts,
            "schema_version": self.schema_version,
        }


@dataclass
class ViewerIdentity:
    uid: str
    nickname: str
    name: str = ""
    email: str = ""
    avatar_url: str = ""
    avatar_bytes: bytes | None = None
    avatar_mime: str = ""
    source_url: str = ""
    fetched: bool = False
    error: str = ""
    # 头像形态 META：用于自适应锐评焦点 + 头像识别不了时的兜底素材。
    # avatar_bytes 是否可用走 has_avatar；这里只描述"这个人头像的配置"。
    is_default_avatar: bool = False  # B 站默认头像（noface），无可锐评内容
    is_animated_avatar: bool = False  # 动图头像（大会员），只取了代表帧
    pendant: str = ""                 # 头像挂件/装扮名（出框效果来源），无则空

    @property
    def avatar_vision_ok(self) -> bool:
        """是否拿到了可喂给视觉的真实头像帧。看不到就别脑补，走 META/ID。"""
        return bool(self.avatar_bytes)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "nickname": self.nickname,
            "name": self.name or self.nickname,
            "avatar_url": self.avatar_url,
            "avatar_mime": self.avatar_mime,
            "has_avatar": bool(self.avatar_bytes),
            "is_default_avatar": self.is_default_avatar,
            "is_animated_avatar": self.is_animated_avatar,
            "pendant": self.pendant,
            "source_url": self.source_url,
            "fetched": self.fetched,
            "error": self.error,
        }


@dataclass
class ViewerProfile:
    uid: str
    nickname: str
    avatar_url: str = ""
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_seen_at: str = field(default_factory=utc_now_iso)
    roast_count: int = 0
    last_roast_at: str = ""
    last_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    reason: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "identity": self.identity.to_public_dict(),
            "profile": self.profile.to_dict(),
            "prompt_text": self.prompt_text,
            "live_mode": self.live_mode,
            "strength": self.strength,
            "should_push": self.should_push,
            "dry_run": self.dry_run,
            "reason": self.reason,
        }


@dataclass
class PipelineStep:
    id: str
    status: Literal["ok", "skipped", "failed"]
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class InteractionResult:
    accepted: bool
    status: Literal["queued", "pushed", "skipped", "failed"]
    event: ViewerEvent
    identity: ViewerIdentity | None = None
    profile: ViewerProfile | None = None
    request: InteractionRequest | None = None
    output: str = ""
    reason: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "event": self.event.to_dict(),
            "identity": self.identity.to_public_dict() if self.identity else None,
            "profile": self.profile.to_dict() if self.profile else None,
            "request": self.request.to_public_dict() if self.request else None,
            "output": self.output,
            "reason": self.reason,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
        }

    def to_sandbox_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "uid": self.event.uid,
            "nickname": self.event.nickname,
            "output": self.output,
            "reason": self.reason,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
        }


@dataclass
class SafetyDecision:
    allowed: bool
    status: SafetyStatus = "running"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveRoomStatus:
    room_id: int
    ok: bool
    title: str = ""
    anchor_name: str = ""
    live_status: str = "unknown"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
