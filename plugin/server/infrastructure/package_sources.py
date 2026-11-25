"""GitHub package routes, user preferences and persistent route observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
import math
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
    ("cdn-gh-proxy-org", "https://cdn.gh-proxy.org/"),
    ("edgeone-gh-proxy-org", "https://edgeone.gh-proxy.org/"),
)
_MIRRORS = GITHUB_DOWNLOAD_SOURCES[1:]
_MIRROR_IDS = frozenset(source_id for source_id, _ in _MIRRORS)
# Mirrors that were offered once and have since gone away. A preference that
# still names one falls back to the default mirror instead of being rejected,
# so a saved setting or an old page keeps working.
_RETIRED_MIRROR_IDS = frozenset({"hk-gh-proxy-org"})
DownloadMode = Literal["auto", "direct", "specified"]


@dataclass(frozen=True)
class DownloadPreferences:
    mode: DownloadMode = "auto"
    specified_source_id: str = "gh-proxy-com"


_DEFAULT_MIRROR_ID = DownloadPreferences.specified_source_id


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
    if source_id in _RETIRED_MIRROR_IDS:
        source_id = _DEFAULT_MIRROR_ID
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
# Bounds for persisted and observed values. Anything beyond them is either a
# corrupt file or a measurement glitch, and would only distort the ranking.
_MAX_LATENCY = 600.0
_MAX_THROUGHPUT = 100.0 * _PRIOR_THROUGHPUT
_MAX_FAILURES = 1000.0
_MAX_SAMPLES = 1_000_000
# Statistics stamped this far past the local clock are treated as corrupt.
_FUTURE_TOLERANCE = 300.0
# 2 ** -1000 is still a normal float; past it a decay factor is flushed to zero.
_MAX_HALVINGS = 1000.0


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
# Routes whose unusable history has already been reported.
_reported_routes: set[str] = set()


def route_stats_path() -> Path:
    return preferences_path().with_name("market-download-routes.json")


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        raw = read_json_tolerating_replace(route_stats_path())
        routes = raw["routes"].items()
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        logger.warning("ignoring unreadable package route statistics: {}", exc)
        return
    now = time.time()
    for source_id, values in routes:
        source_id = str(source_id)
        if source_id in _RETIRED_MIRROR_IDS:
            continue
        try:
            _routes[source_id] = _parse_route(values, now)
        except (ValueError, TypeError, AttributeError, ArithmeticError) as exc:
            # One corrupt route must not cost the others their history.
            logger.warning("dropping corrupt statistics of download route {}: {}", source_id, exc)


def _parse_route(values: Any, now: float) -> RouteStats:
    if not isinstance(values, dict):
        raise TypeError("route statistics are not an object")
    updated = _finite(values.get("updated", 0.0), "updated")
    if updated < 0 or updated > now + _FUTURE_TOLERANCE:
        raise ValueError(f"updated {updated!r} is outside the valid range")
    samples = values.get("samples", 0)
    if isinstance(samples, bool) or not isinstance(samples, (int, float)):
        raise TypeError("samples is not a number")
    return RouteStats(
        latency=_sample(_optional(values.get("latency"), "latency"), _MAX_LATENCY),
        throughput=_sample(_optional(values.get("throughput"), "throughput"), _MAX_THROUGHPUT),
        failures=min(_MAX_FAILURES, max(0.0, _finite(values.get("failures", 0.0), "failures"))),
        samples=min(_MAX_SAMPLES, max(0, int(_finite(samples, "samples")))),
        updated=updated,
    )


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} is not a number")
    number = float(value)  # OverflowError for an integer beyond float range
    if not math.isfinite(number):
        raise ValueError(f"{name} is not finite")
    return number


def _optional(value: Any, name: str) -> float | None:
    return None if value is None else _finite(value, name)


def _sample(value: Any, limit: float) -> float | None:
    """A usable positive measurement capped at ``limit``, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return min(number, limit)


def _blend(previous: float | None, value: float | None) -> float | None:
    if value is None:
        return previous
    if previous is None or not math.isfinite(previous):
        return value
    return previous + (value - previous) * _SMOOTHING


def _decay(age: float, half_life: float) -> float:
    """``0.5 ** (age / half_life)``, flushed to zero before it turns subnormal.

    Windows' C runtime reports a subnormal pow() result as a range error,
    which CPython raises as OverflowError: a route last updated about 21 days
    earlier made the failure decay raise and aborted the whole install.
    """
    halvings = max(0.0, age) / half_life
    return 0.0 if halvings >= _MAX_HALVINGS else 0.5 ** halvings


_PRIOR_ESTIMATE = RouteEstimate(_PRIOR_LATENCY, _PRIOR_THROUGHPUT, 0.0)


def estimate_route(source_id: str, *, now: float | None = None) -> RouteEstimate:
    """The expected behaviour of one route; the prior when its history is unusable."""
    _ensure_loaded()
    stats = _routes.get(source_id)
    if stats is None or not stats.samples:
        return _PRIOR_ESTIMATE
    try:
        estimate = _estimate_from(stats, now if now is not None else time.time())
        if not (math.isfinite(estimate.confidence)
                and math.isfinite(estimate.seconds_for(TYPICAL_PACKAGE_BYTES))):
            raise ValueError(f"non-finite estimate {estimate!r}")
    except Exception as exc:
        # One bad history must not stop the other routes from being ranked.
        # Every install estimates each route several times; report it once.
        if source_id not in _reported_routes:
            _reported_routes.add(source_id)
            logger.warning("ignoring statistics of download route {}: {!r}", source_id, exc)
        return _PRIOR_ESTIMATE
    return estimate


def _estimate_from(stats: RouteStats, now: float) -> RouteEstimate:
    age = now - stats.updated
    confidence = _decay(age, _HALF_LIFE) * min(1.0, stats.samples / 3)
    failures = stats.failures * _decay(age, _FAILURE_HALF_LIFE)

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
        stats.failures *= _decay(now - stats.updated, _FAILURE_HALF_LIFE)
    stats.latency = _blend(stats.latency, _sample(observation.latency, _MAX_LATENCY))
    stats.throughput = _blend(stats.throughput, _sample(observation.throughput, _MAX_THROUGHPUT))
    stats.failures = min(_MAX_FAILURES, stats.failures + (1.0 if observation.failed else 0.0))
    stats.samples = min(_MAX_SAMPLES, stats.samples + 1)
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
    _reported_routes.clear()
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
    candidates = [direct, *mirrors]
    # Scored before sorting: estimate_route never raises, and a failure inside
    # a sort key would abort the whole ranking rather than one route.
    seconds = {key: estimate_route(key, now=now).seconds_for(TYPICAL_PACKAGE_BYTES)
               for key, _ in candidates}
    # sorted() is stable: equal estimates keep the declared order. With no
    # history that puts direct and the first mirrors into the opening race,
    # which opens unmeasured routes together and keeps the first to answer.
    return sorted(candidates, key=lambda candidate: seconds[candidate[0]])


def warmup_urls(candidates: list[tuple[str, str]]) -> list[str]:
    """Every origin a download over these routes connects to before its first byte."""
    urls = [url for _, url in candidates]
    if any(source_id == "github-direct" for source_id, _ in candidates):
        urls.append(_GITHUB_ASSET_ORIGIN)
    return urls
