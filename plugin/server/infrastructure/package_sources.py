"""GitHub package routes, user preferences and persistent route observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
import threading
import time
from typing import Any, Literal
from urllib.parse import urlparse

from plugin.logging_config import get_logger
from plugin.server.infrastructure.package_download import run_file_operation
from utils.file_utils import atomic_write_json, read_json_tolerating_replace

logger = get_logger("server.infrastructure.package_sources")

GITHUB_DOWNLOAD_SOURCES = (
    ("github-direct", "https://github.com/"),
    ("gh-proxy-com", "https://gh-proxy.com/"),
    ("gh-proxy-org", "https://gh-proxy.org/"),
    ("hk-gh-proxy-org", "https://hk.gh-proxy.org/"),
    ("cdn-gh-proxy-org", "https://cdn.gh-proxy.org/"),
    ("edgeone-gh-proxy-org", "https://edgeone.gh-proxy.org/"),
)
_MIRRORS = GITHUB_DOWNLOAD_SOURCES[1:]
_MIRROR_IDS = frozenset(source_id for source_id, _ in _MIRRORS)
DownloadMode = Literal["auto", "direct", "specified"]


@dataclass(frozen=True)
class DownloadPreferences:
    mode: DownloadMode = "auto"
    specified_source_id: str = "gh-proxy-com"


def preferences_path() -> Path:
    from plugin.settings import get_plugin_state_root

    return get_plugin_state_root().parent / "market-download.json"


def load_preferences() -> tuple[DownloadPreferences, bool]:
    """Return the saved preferences and whether they count as configured.

    A file that exists but cannot be read still counts, so a browser seeding
    the server cannot replace a choice that is only temporarily unreadable.
    One whose content is invalid does not: that choice is already lost.
    """
    try:
        raw = read_json_tolerating_replace(preferences_path())
        return validate_preferences(raw["mode"], raw["specified_source_id"]), True
    except FileNotFoundError:
        return DownloadPreferences(), False
    except OSError as exc:
        logger.warning("ignoring unreadable package download preferences: {}", exc)
        return DownloadPreferences(), True
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("ignoring invalid package download preferences: {}", exc)
        return DownloadPreferences(), False


def validate_preferences(mode: str, source_id: str) -> DownloadPreferences:
    if mode not in ("auto", "direct", "specified"):
        raise ValueError("Unknown package download mode")
    if source_id not in _MIRROR_IDS:
        raise ValueError("Unknown GitHub download source")
    return DownloadPreferences(mode=mode, specified_source_id=source_id)  # type: ignore[arg-type]


def save_preferences(preferences: DownloadPreferences) -> None:
    atomic_write_json(preferences_path(), asdict(preferences))


def is_github_release(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https" and parsed.hostname == "github.com"
        and parsed.username is None and parsed.password is None
        and "/releases/download/" in parsed.path
    )


# Observations are kept per route rather than per package, so every package
# teaches the next one. They persist across restarts and decay towards a
# neutral prior: a stale winner never pins later transfers, and a network
# change is relearned within a few downloads.
TYPICAL_PACKAGE_BYTES = 1024 * 1024
_PRIOR_LATENCY = 0.6
_PRIOR_THROUGHPUT = 4 * 1024 * 1024
# Where github.com redirects release downloads. Pre-connecting it is best
# effort: if GitHub moves its assets, only that warm-up request is wasted.
_GITHUB_ASSET_ORIGIN = "https://release-assets.githubusercontent.com/"
_HALF_LIFE = 24 * 3600.0
_FAILURE_HALF_LIFE = 1800.0
_FAILURE_PENALTY = 2.0
_SMOOTHING = 0.4


@dataclass
class RouteStats:
    latency: float | None = None
    throughput: float | None = None
    failures: float = 0.0
    samples: int = 0
    updated: float = 0.0


@dataclass(frozen=True)
class RouteEstimate:
    latency: float
    throughput: float
    confidence: float

    def seconds_for(self, size: int) -> float:
        return self.latency + size / self.throughput


@dataclass(frozen=True)
class RouteObservation:
    """What one transfer learned about one route."""

    source_id: str
    latency: float | None = None
    throughput: float | None = None
    failed: bool = False


@dataclass(frozen=True)
class _Snapshot:
    generation: int
    sequence: int
    routes: dict[str, dict[str, Any]]


# The statistics belong to the event loop: it reads, records and resets them.
# Worker threads only write immutable snapshots, under _file_lock, so a write
# can neither iterate a dictionary the loop is changing nor land after a reset
# or after a newer snapshot.
_routes: dict[str, RouteStats] = {}
_loaded = False
_generation = 0
_sequence = 0
_written_sequence = 0
_written_generation = 0
_file_lock = threading.Lock()


def route_stats_path() -> Path:
    return preferences_path().with_name("market-download-routes.json")


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        raw = read_json_tolerating_replace(route_stats_path())
        for source_id, values in raw["routes"].items():
            _routes[str(source_id)] = RouteStats(
                latency=_positive(values.get("latency")),
                throughput=_positive(values.get("throughput")),
                failures=max(0.0, float(values.get("failures", 0.0))),
                samples=max(0, int(values.get("samples", 0))),
                updated=float(values.get("updated", 0.0)),
            )
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("ignoring unreadable package route statistics: {}", exc)
        _routes.clear()


def _positive(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _blend(previous: float | None, value: float | None) -> float | None:
    if value is None:
        return previous
    return value if previous is None else previous + (value - previous) * _SMOOTHING


def estimate_route(source_id: str, *, now: float | None = None) -> RouteEstimate:
    _ensure_loaded()
    stats = _routes.get(source_id)
    if stats is None or not stats.samples:
        return RouteEstimate(_PRIOR_LATENCY, _PRIOR_THROUGHPUT, 0.0)
    age = max(0.0, (now if now is not None else time.time()) - stats.updated)
    confidence = 0.5 ** (age / _HALF_LIFE) * min(1.0, stats.samples / 3)
    failures = stats.failures * 0.5 ** (age / _FAILURE_HALF_LIFE)

    def toward_prior(value: float | None, prior: float) -> float:
        return prior if value is None else prior + (value - prior) * confidence

    latency = toward_prior(stats.latency, _PRIOR_LATENCY) + failures * _FAILURE_PENALTY
    throughput = toward_prior(stats.throughput, _PRIOR_THROUGHPUT)
    return RouteEstimate(latency, max(throughput, 1.0), confidence)


def route_generation() -> int:
    """Identifies the current history; a transfer passes it back when it commits."""
    return _generation


def _record(observation: RouteObservation, now: float) -> None:
    stats = _routes.setdefault(observation.source_id, RouteStats())
    if stats.updated:
        stats.failures *= 0.5 ** (max(0.0, now - stats.updated) / _FAILURE_HALF_LIFE)
    stats.latency = _blend(stats.latency, observation.latency)
    stats.throughput = _blend(stats.throughput, observation.throughput)
    stats.failures += 1.0 if observation.failed else 0.0
    stats.samples += 1
    stats.updated = now


def _write_snapshot(snapshot: _Snapshot) -> None:
    global _written_sequence, _written_generation
    with _file_lock:
        if snapshot.generation != _generation or snapshot.sequence <= _written_sequence:
            return
        atomic_write_json(route_stats_path(), {"routes": snapshot.routes})
        _written_sequence = snapshot.sequence
        _written_generation = snapshot.generation


async def commit_route_observations(
    observations: Iterable[RouteObservation], generation: int, *, now: float | None = None,
) -> None:
    """Fold one finished transfer into the statistics and persist them.

    A transfer that began before a history reset is dropped, so it cannot
    restore what the user cleared. Persisting is best effort: the in-memory
    estimates stay valid if the write fails.
    """
    global _sequence
    observations = list(observations)
    if not observations or generation != _generation:
        return
    _ensure_loaded()
    now = now if now is not None else time.time()
    for observation in observations:
        _record(observation, now)
    _sequence += 1
    snapshot = _Snapshot(_generation, _sequence, {
        source_id: asdict(stats) for source_id, stats in _routes.items()
    })
    try:
        await run_file_operation(_write_snapshot, snapshot)
    except OSError as exc:
        logger.warning("failed to save package route statistics: {}", exc)


def _delete_stats_file(generation: int) -> None:
    with _file_lock:
        # A transfer that began after the reset may already have written the
        # file, holding only observations made since.
        if _written_generation >= generation:
            return
        route_stats_path().unlink(missing_ok=True)


async def reset_transfer_history() -> None:
    """Forget every route observation, including those of transfers in progress."""
    global _loaded, _generation
    _routes.clear()
    _generation += 1
    _loaded = True
    await run_file_operation(_delete_stats_file, _generation)


def download_candidates(url: str, preferences: DownloadPreferences) -> list[tuple[str, str]]:
    """Routes for one package, best expected completion time first in auto mode."""
    if not is_github_release(url):
        return [(urlparse(url).hostname or "origin", url)]
    direct = ("github-direct", url)
    if preferences.mode == "direct":
        return [direct]
    mirrors = [(key, prefix + url) for key, prefix in _MIRRORS]
    if preferences.mode == "specified":
        return [next(item for item in mirrors if item[0] == preferences.specified_source_id)]
    now = time.time()
    # sorted() is stable: equal estimates keep the declared order. With no
    # history that puts direct and the first mirrors into the opening race,
    # which opens unmeasured routes together and keeps the first to answer.
    return sorted([direct, *mirrors], key=lambda candidate: estimate_route(
        candidate[0], now=now).seconds_for(TYPICAL_PACKAGE_BYTES))


def warmup_urls(candidates: list[tuple[str, str]]) -> list[str]:
    """Every origin a download over these routes connects to before its first byte."""
    urls = [url for _, url in candidates]
    if any(source_id == "github-direct" for source_id, _ in candidates):
        urls.append(_GITHUB_ASSET_ORIGIN)
    return urls
