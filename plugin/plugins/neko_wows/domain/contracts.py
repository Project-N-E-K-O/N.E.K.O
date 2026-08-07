"""Public boundaries and runtime configuration.

The six protocols below are the seams the design fixed up front. They are
declared in their final shape even where P1 only ships a trivial
implementation -- notably `TacticsRepository`, whose real version lands later --
so adding the document layer will not change any call site.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .snapshot import WowsSnapshot

LANE_URGENT = "urgent"
LANE_NORMAL = "normal"
ALL_LANES = (LANE_URGENT, LANE_NORMAL)

CHANNEL_DUAL = "dual"
CHANNEL_SINGLE = "single"
ALL_CHANNEL_MODES = (CHANNEL_DUAL, CHANNEL_SINGLE)

# How willing the companion is to speak over an ongoing conversation. This sits
# on top of the host's own gate (which already refuses when the user spoke in the
# last ~10 seconds); the plugin-side window is the longer, user-tunable one.
INTRUSION_NO_INTERRUPT = "no_interrupt"
INTRUSION_CRITICAL_ONLY = "critical_only"
INTRUSION_ALLOW_INTERRUPT = "allow_interrupt"
ALL_INTRUSION_MODES = (
    INTRUSION_NO_INTERRUPT,
    INTRUSION_CRITICAL_ONLY,
    INTRUSION_ALLOW_INTERRUPT,
)

# Coalesce keys double as broadcast categories: they already group events the way
# a user thinks about them (survival, threat, targeting, ...).
CATEGORY_LIFECYCLE = "wows_lifecycle"
CATEGORY_SUMMARY = "wows_summary"
CATEGORY_SURVIVAL = "wows_survival"
CATEGORY_SITUATION = "wows_situation"
CATEGORY_THREAT = "wows_threat"
CATEGORY_GEOMETRY = "wows_geometry"
CATEGORY_TARGETING = "wows_targeting"
CATEGORY_PROGRESS = "wows_progress"
ALL_CATEGORIES = (
    CATEGORY_LIFECYCLE,
    CATEGORY_SUMMARY,
    CATEGORY_SURVIVAL,
    CATEGORY_SITUATION,
    CATEGORY_THREAT,
    CATEGORY_GEOMETRY,
    CATEGORY_TARGETING,
    CATEGORY_PROGRESS,
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _string_tuple(raw: Any, allowed: Sequence[str]) -> tuple[str, ...]:
    """Keep only recognised entries, so a stale name cannot disable everything."""
    if not isinstance(raw, (list, tuple)):
        return ()
    permitted = set(allowed)
    return tuple(dict.fromkeys(
        value for value in raw if isinstance(value, str) and value in permitted))


@dataclass
class WowsConfig:
    """Effective `[neko_wows]` settings, clamped so a typo cannot hurt."""

    enabled: bool = True
    dry_run: bool = True

    service_url: str = "http://127.0.0.1:8111"
    service_auto_start: bool = True
    service_source_dir: str = ""
    game_dir: str = ""
    service_startup_timeout_seconds: float = 10.0
    service_health_timeout_seconds: float = 1.5

    transport_prefer_ws: bool = True
    rest_poll_interval_seconds: float = 0.5
    ws_reconnect_min_seconds: float = 1.0
    ws_reconnect_max_seconds: float = 15.0
    http_timeout_seconds: float = 1.5

    urgent_ttl_seconds: float = 8.0
    urgent_min_gap_seconds: float = 6.0
    normal_ttl_seconds: float = 30.0
    normal_min_gap_seconds: float = 18.0
    channel_mode: str = CHANNEL_DUAL

    safety_window_seconds: float = 60.0
    safety_failure_limit: int = 5
    observability_max_events: int = 120

    low_health_ratios: tuple[float, ...] = (0.35, 0.15)
    rapid_damage_ratio: float = 0.12
    rapid_damage_window_seconds: float = 3.0
    enemy_close_range_m: float = 8000.0
    threat_scan_range_m: float = 12000.0
    multi_direction_spread_deg: float = 90.0
    isolation_ally_range_m: float = 6000.0
    isolation_enemy_range_m: float = 10000.0
    boundary_margin_m: float = 1500.0
    broadside_angle_deg: float = 25.0
    low_hp_target_ratio: float = 0.2
    damage_milestone_step: float = 50000.0
    outnumbered_margin: int = 2

    # --- tactical documents ---
    tactics_max_file_bytes: int = 8 * 1024 * 1024
    tactics_max_documents: int = 500
    tactics_max_total_bytes: int = 128 * 1024 * 1024
    tactics_chunk_chars: int = 800
    tactics_chunk_overlap: int = 100
    # Storage is uncapped up to the quotas above, but the ranked index is not:
    # past this many chunks, documents stay retrievable by tag only.
    tactics_index_chunk_cap: int = 3000
    tactics_tag_weight: float = 2.5
    tactics_min_term_hits: int = 2

    # --- broadcast preferences ---
    dialogue_intrusion_mode: str = INTRUSION_CRITICAL_ONLY
    user_chat_quiet_window_seconds: float = 60.0
    disabled_categories: tuple[str, ...] = ()
    disabled_lanes: tuple[str, ...] = ()
    prompt_revisions_kept: int = 20

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "WowsConfig":
        """Build a config from a TOML section, ignoring anything unusable.

        A broken value must never take the plugin down, and it must never
        silently enable real output: `dry_run` only turns off when the config
        says so explicitly.
        """
        data = dict(raw or {})
        cfg = cls()

        def flag(key: str, default: bool) -> bool:
            value = data.get(key, default)
            return bool(value) if isinstance(value, bool) else default

        def number(key: str, default: float, low: float, high: float) -> float:
            value = data.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return default
            return _clamp(float(value), low, high)

        def text(key: str, default: str) -> str:
            value = data.get(key, default)
            return value.strip() if isinstance(value, str) else default

        cfg.enabled = flag("enabled", cfg.enabled)
        cfg.dry_run = flag("dry_run", True)

        cfg.service_url = text("service_url", cfg.service_url).rstrip("/")
        cfg.service_auto_start = flag("service_auto_start", cfg.service_auto_start)
        cfg.service_source_dir = text("service_source_dir", "")
        cfg.game_dir = text("game_dir", "")
        cfg.service_startup_timeout_seconds = number(
            "service_startup_timeout_seconds", 10.0, 1.0, 120.0)
        cfg.service_health_timeout_seconds = number(
            "service_health_timeout_seconds", 1.5, 0.2, 30.0)

        cfg.transport_prefer_ws = flag("transport_prefer_ws", True)
        cfg.rest_poll_interval_seconds = number(
            "rest_poll_interval_seconds", 0.5, 0.05, 10.0)
        cfg.ws_reconnect_min_seconds = number("ws_reconnect_min_seconds", 1.0, 0.1, 60.0)
        cfg.ws_reconnect_max_seconds = number("ws_reconnect_max_seconds", 15.0, 1.0, 300.0)
        if cfg.ws_reconnect_max_seconds < cfg.ws_reconnect_min_seconds:
            cfg.ws_reconnect_max_seconds = cfg.ws_reconnect_min_seconds
        cfg.http_timeout_seconds = number("http_timeout_seconds", 1.5, 0.2, 30.0)

        cfg.urgent_ttl_seconds = number("urgent_ttl_seconds", 8.0, 1.0, 120.0)
        cfg.urgent_min_gap_seconds = number("urgent_min_gap_seconds", 6.0, 0.0, 600.0)
        cfg.normal_ttl_seconds = number("normal_ttl_seconds", 30.0, 1.0, 600.0)
        cfg.normal_min_gap_seconds = number("normal_min_gap_seconds", 18.0, 0.0, 3600.0)
        mode = text("channel_mode", CHANNEL_DUAL)
        cfg.channel_mode = mode if mode in ALL_CHANNEL_MODES else CHANNEL_DUAL

        cfg.safety_window_seconds = number("safety_window_seconds", 60.0, 5.0, 3600.0)
        cfg.safety_failure_limit = int(number("safety_failure_limit", 5, 1, 100))
        cfg.observability_max_events = int(number("observability_max_events", 120, 10, 2000))

        ratios = data.get("low_health_ratios")
        if isinstance(ratios, (list, tuple)):
            cleaned = sorted(
                {_clamp(float(r), 0.01, 0.99)
                 for r in ratios
                 if isinstance(r, (int, float)) and not isinstance(r, bool)},
                reverse=True,
            )
            if cleaned:
                cfg.low_health_ratios = tuple(cleaned)

        cfg.rapid_damage_ratio = number("rapid_damage_ratio", 0.12, 0.01, 0.9)
        cfg.rapid_damage_window_seconds = number(
            "rapid_damage_window_seconds", 3.0, 0.2, 60.0)
        cfg.enemy_close_range_m = number("enemy_close_range_m", 8000.0, 500.0, 50000.0)
        cfg.threat_scan_range_m = number("threat_scan_range_m", 12000.0, 500.0, 60000.0)
        cfg.multi_direction_spread_deg = number(
            "multi_direction_spread_deg", 90.0, 15.0, 180.0)
        cfg.isolation_ally_range_m = number("isolation_ally_range_m", 6000.0, 500.0, 50000.0)
        cfg.isolation_enemy_range_m = number(
            "isolation_enemy_range_m", 10000.0, 500.0, 60000.0)
        cfg.boundary_margin_m = number("boundary_margin_m", 1500.0, 100.0, 20000.0)
        cfg.broadside_angle_deg = number("broadside_angle_deg", 25.0, 5.0, 60.0)
        cfg.low_hp_target_ratio = number("low_hp_target_ratio", 0.2, 0.01, 0.9)
        cfg.damage_milestone_step = number(
            "damage_milestone_step", 50000.0, 1000.0, 1000000.0)
        cfg.outnumbered_margin = int(number("outnumbered_margin", 2, 1, 12))

        cfg.tactics_max_file_bytes = int(number(
            "tactics_max_file_bytes", 8 * 1024 * 1024, 4096, 64 * 1024 * 1024))
        cfg.tactics_max_documents = int(number("tactics_max_documents", 500, 1, 5000))
        cfg.tactics_max_total_bytes = int(number(
            "tactics_max_total_bytes", 128 * 1024 * 1024, 4096, 2 * 1024 * 1024 * 1024))
        cfg.tactics_chunk_chars = int(number("tactics_chunk_chars", 800, 200, 4000))
        cfg.tactics_chunk_overlap = int(number(
            "tactics_chunk_overlap", 100, 0, cfg.tactics_chunk_chars // 2))
        cfg.tactics_index_chunk_cap = int(number(
            "tactics_index_chunk_cap", 3000, 0, 200000))
        cfg.tactics_tag_weight = number("tactics_tag_weight", 2.5, 0.0, 100.0)
        cfg.tactics_min_term_hits = int(number("tactics_min_term_hits", 2, 1, 20))

        intrusion = text("dialogue_intrusion_mode", INTRUSION_CRITICAL_ONLY)
        cfg.dialogue_intrusion_mode = (
            intrusion if intrusion in ALL_INTRUSION_MODES else INTRUSION_CRITICAL_ONLY)
        cfg.user_chat_quiet_window_seconds = number(
            "user_chat_quiet_window_seconds", 60.0, 0.0, 1800.0)
        cfg.disabled_categories = _string_tuple(
            data.get("disabled_categories"), ALL_CATEGORIES)
        cfg.disabled_lanes = _string_tuple(data.get("disabled_lanes"), ALL_LANES)
        cfg.prompt_revisions_kept = int(number("prompt_revisions_kept", 20, 1, 200))
        return cfg

    def ttl_for(self, lane: str) -> float:
        return self.urgent_ttl_seconds if lane == LANE_URGENT else self.normal_ttl_seconds

    def min_gap_for(self, lane: str) -> float:
        if lane == LANE_URGENT:
            return self.urgent_min_gap_seconds
        return self.normal_min_gap_seconds

    def lane_enabled(self, lane: str) -> bool:
        return lane not in self.disabled_lanes

    def category_enabled(self, category: str) -> bool:
        return category not in self.disabled_categories


@dataclass(frozen=True)
class TacticExcerpt:
    """One retrieved chunk of tactical reference text."""

    doc_id: str
    title: str
    text: str
    score: float = 0.0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TacticQuery:
    """What to look for, with tags kept separate from free text.

    P1 typed this parameter as a plain string. Tag weighting cannot be expressed
    that way -- once the map name is concatenated into the query there is no way
    to score it differently from body text -- so the battle context is passed
    structured instead.
    """

    summary: str = ""
    event_id: str = ""
    map_name: str | None = None
    ship_name: str | None = None
    ship_class: str | None = None
    game_mode: str | None = None
    topics: tuple[str, ...] = ()

    def text(self) -> str:
        """Free-text side of the query, used for term matching and ranking."""
        parts = [self.summary, *self.topics]
        for value in (
            self.map_name, self.ship_name, self.ship_class, self.game_mode,
        ):
            if value:
                parts.append(value)
        return " ".join(part for part in parts if part)

    def tag_candidates(self) -> dict[str, tuple[str, ...]]:
        """Battle context mapped onto front-matter tag kinds."""
        candidates: dict[str, tuple[str, ...]] = {}
        if self.map_name:
            candidates["maps"] = (self.map_name,)
        if self.ship_name:
            candidates["ships"] = (self.ship_name,)
        if self.ship_class:
            candidates["classes"] = (self.ship_class,)
        if self.game_mode:
            candidates["modes"] = (self.game_mode,)
        if self.topics:
            candidates["topics"] = tuple(self.topics)
        return candidates


@dataclass(frozen=True)
class DeliveryRequest:
    """Everything needed to hand one call-out to the host, and nothing more."""

    event_id: str
    lane: str
    priority: int
    text: str
    coalesce_key: str
    ai_behavior: str = "respond"
    visibility: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    target_lanlan: str = ""
    expires_at: float = 0.0

    def push_kwargs(self) -> dict[str, Any]:
        return {
            "source": "neko_wows",
            "visibility": list(self.visibility),
            "ai_behavior": self.ai_behavior,
            "parts": [{"type": "text", "text": self.text}],
            "priority": self.priority,
            "coalesce_key": self.coalesce_key,
            "metadata": dict(self.metadata),
            "target_lanlan": self.target_lanlan or None,
        }


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    reason: str
    event_id: str = ""
    lane: str = ""
    at: float = 0.0
    # Number of host calls this delivery made. Asserted to be zero in dry-run.
    host_calls: int = 0


@runtime_checkable
class SchemaAdapter(Protocol):
    def parse(self, raw: Mapping[str, Any]) -> WowsSnapshot: ...


@runtime_checkable
class Detector(Protocol):
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    needs_live: bool

    def reset(self) -> None: ...

    def feed(self, previous, current, context) -> Sequence[Any]: ...


@runtime_checkable
class TacticPolicy(Protocol):
    def expand(self, events, facts) -> Sequence[Any]: ...


@runtime_checkable
class TacticsRepository(Protocol):
    def search(
        self, query: TacticQuery, *, limit: int, budget: int
    ) -> Sequence[TacticExcerpt]: ...


@runtime_checkable
class PromptRouter(Protocol):
    def build(self, candidate, profile, excerpts) -> DeliveryRequest: ...


@runtime_checkable
class OutputPort(Protocol):
    def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


class NullTacticsRepository:
    """Stands in when the document store is unavailable or empty.

    Still used as the default in tests and whenever the store fails to open, so
    the pipeline never depends on documents existing.
    """

    def search(
        self, query: TacticQuery, *, limit: int = 3, budget: int = 0
    ) -> tuple[TacticExcerpt, ...]:
        return ()
