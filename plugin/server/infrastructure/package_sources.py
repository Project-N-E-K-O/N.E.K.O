"""Package source families, user preferences and persistent route observations.

A source family is one kind of origin and the mirrors that can stand in for
it: GitHub release downloads, reached through prefix proxies, and the PyPI
simple index, which only picks the index address handed to uv. Routes of
every family share one statistics file, keyed by route id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
import math
from pathlib import Path
import threading
import time
from typing import Any, Literal
from urllib.parse import urlparse

from plugin.logging_config import get_logger
from plugin.server.infrastructure.package_download import package_http_client, run_file_operation
from plugin.utils.http_imports import ensure_httpx
from utils.file_utils import atomic_write_json, read_json_tolerating_replace

logger = get_logger("server.infrastructure.package_sources")

# The route to github.com itself; every other route is a mirror.
GITHUB_DIRECT_SOURCE_ID = "github-direct"
GITHUB_DOWNLOAD_SOURCES = (
    (GITHUB_DIRECT_SOURCE_ID, "https://github.com/"),
    ("gh-proxy-com", "https://gh-proxy.com/"),
    ("gh-proxy-org", "https://gh-proxy.org/"),
    ("cdn-gh-proxy-org", "https://cdn.gh-proxy.org/"),
    ("edgeone-gh-proxy-org", "https://edgeone.gh-proxy.org/"),
)
# The official index first, then mirrors that serve the same simple pages and
# link files by the same /packages/ paths, so uv checks the same hashes.
PYPI_DIRECT_SOURCE_ID = "pypi-direct"
PYPI_INDEX_SOURCES = (
    (PYPI_DIRECT_SOURCE_ID, "https://pypi.org/simple/"),
    ("pypi-tuna", "https://pypi.tuna.tsinghua.edu.cn/simple/"),
    ("pypi-aliyun", "https://mirrors.aliyun.com/pypi/simple/"),
    ("pypi-ustc", "https://mirrors.ustc.edu.cn/pypi/simple/"),
    ("pypi-tencent", "https://mirrors.cloud.tencent.com/pypi/simple/"),
)
DownloadMode = Literal["auto", "direct", "specified"]


def is_github_release(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https" and parsed.hostname == "github.com"
        and parsed.username is None and parsed.password is None
        and "/releases/download/" in parsed.path
    )


def is_pypi_simple(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https" and parsed.hostname == "pypi.org"
        and parsed.username is None and parsed.password is None
        and parsed.path.startswith("/simple/")
    )


@dataclass(frozen=True)
class SourceFamily:
    """One kind of origin and the mirrors that can stand in for it."""

    family_id: str
    # (route id, base URL); the first is the origin itself.
    sources: tuple[tuple[str, str], ...]
    default_mirror_id: str
    # Mirrors that were offered once and have since gone away. A preference
    # that still names one falls back to the default mirror instead of being
    # rejected, so a saved setting or an old page keeps working.
    retired_ids: frozenset[str]
    matches: Callable[[str], bool]
    # "prefix": the mirror base is put in front of the whole origin URL.
    # "rebase": the origin base is replaced by the mirror base.
    rewrite: Literal["prefix", "rebase"]

    @property
    def direct_id(self) -> str:
        return self.sources[0][0]

    @property
    def mirrors(self) -> tuple[tuple[str, str], ...]:
        return self.sources[1:]

    def mirror_url(self, source_id: str, url: str) -> str:
        """``url`` as reached through mirror ``source_id``."""
        base = dict(self.mirrors)[source_id]
        if self.rewrite == "prefix":
            return base + url
        # Compared as parsed paths, so a URL the matcher accepts with an
        # explicit default port (https://pypi.org:443/...) rewrites too.
        prefix = urlparse(self.sources[0][1]).path
        parsed = urlparse(url)
        if not (self.matches(url) and parsed.path.startswith(prefix)):
            raise ValueError(f"{url!r} is not under {self.sources[0][1]!r}")
        rewritten = base + parsed.path[len(prefix):]
        if parsed.query:
            rewritten += "?" + parsed.query
        if parsed.fragment:
            rewritten += "#" + parsed.fragment
        return rewritten

    def resolve_mirror_id(self, source_id: Any) -> str:
        """A mirror id this family offers; a retired one maps to the default."""
        if not isinstance(source_id, str):
            raise TypeError(f"{self.family_id} download source is not a string")
        if source_id in self.retired_ids:
            return self.default_mirror_id
        if source_id not in {key for key, _ in self.mirrors}:
            raise ValueError(f"Unknown {self.family_id} download source")
        return source_id


GITHUB_FAMILY = SourceFamily(
    family_id="github",
    sources=GITHUB_DOWNLOAD_SOURCES,
    default_mirror_id="gh-proxy-com",
    retired_ids=frozenset({"hk-gh-proxy-org"}),
    matches=is_github_release,
    rewrite="prefix",
)
PYPI_FAMILY = SourceFamily(
    family_id="pypi",
    sources=PYPI_INDEX_SOURCES,
    default_mirror_id="pypi-tuna",
    retired_ids=frozenset(),
    matches=is_pypi_simple,
    rewrite="rebase",
)
SOURCE_FAMILIES = (GITHUB_FAMILY, PYPI_FAMILY)
# Kept for callers of the GitHub-only table this module started with.
_MIRRORS = GITHUB_FAMILY.mirrors
_MIRROR_IDS = frozenset(source_id for source_id, _ in _MIRRORS)
_RETIRED_MIRROR_IDS = GITHUB_FAMILY.retired_ids


def _retired_route_ids() -> frozenset[str]:
    return frozenset().union(*(family.retired_ids for family in SOURCE_FAMILIES))


# Families whose files are downloaded through download_candidates. PyPI files
# are fetched by uv, so a PyPI URL there would only skew the PyPI probes.
DOWNLOAD_FAMILIES = (GITHUB_FAMILY,)


def family_for(url: str, families: tuple[SourceFamily, ...] = SOURCE_FAMILIES) -> SourceFamily | None:
    return next((family for family in families if family.matches(url)), None)


@dataclass(frozen=True)
class DownloadPreferences:
    """One mode for every family, and one specified mirror per family.

    ``specified_source_id`` is the GitHub mirror: the name predates the
    families and stays, so a file or page from before them still reads.
    """

    mode: DownloadMode = "auto"
    specified_source_id: str = GITHUB_FAMILY.default_mirror_id
    specified_pypi_source_id: str = PYPI_FAMILY.default_mirror_id

    def specified_for(self, family: SourceFamily) -> str:
        return self.specified_pypi_source_id if family is PYPI_FAMILY else self.specified_source_id


_DEFAULT_MIRROR_ID = GITHUB_FAMILY.default_mirror_id


def preferences_path() -> Path:
    from plugin.settings import get_plugin_state_root

    return get_plugin_state_root().parent / "market-download.json"


def load_preferences() -> tuple[DownloadPreferences, bool]:
    """Return the saved preferences and whether they count as configured.

    A file that exists but cannot be read still counts, so a browser seeding
    the server cannot replace a choice that is only temporarily unreadable.
    One whose content is invalid does not: that choice is already lost.
    A file from before the PyPI family has no PyPI mirror and gets the
    default one; an unusable PyPI mirror alone does not cost the GitHub one.
    """
    try:
        raw = read_json_tolerating_replace(preferences_path())
        preferences = validate_preferences(raw["mode"], raw["specified_source_id"])
        pypi_source_id = raw.get("specified_pypi_source_id")
    except FileNotFoundError:
        return DownloadPreferences(), False
    except OSError as exc:
        logger.warning("ignoring unreadable package download preferences: {}", exc)
        return DownloadPreferences(), True
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("ignoring invalid package download preferences: {}", exc)
        return DownloadPreferences(), False
    if pypi_source_id is not None:
        try:
            preferences = DownloadPreferences(
                mode=preferences.mode, specified_source_id=preferences.specified_source_id,
                specified_pypi_source_id=PYPI_FAMILY.resolve_mirror_id(pypi_source_id),
            )
        except (ValueError, TypeError) as exc:
            logger.warning("ignoring invalid PyPI index preference: {}", exc)
    return preferences, True


def validate_preferences(
    mode: str, source_id: str, pypi_source_id: str | None = None,
) -> DownloadPreferences:
    if mode not in ("auto", "direct", "specified"):
        raise ValueError("Unknown package download mode")
    github = GITHUB_FAMILY.resolve_mirror_id(source_id)
    pypi = PYPI_FAMILY.default_mirror_id if pypi_source_id is None else PYPI_FAMILY.resolve_mirror_id(pypi_source_id)
    return DownloadPreferences(mode=mode, specified_source_id=github, specified_pypi_source_id=pypi)  # type: ignore[arg-type]


def save_preferences(preferences: DownloadPreferences) -> None:
    # Versions before the PyPI family read only the first two keys and
    # ignore the third, so a file written here still reads there.
    atomic_write_json(preferences_path(), asdict(preferences))


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
# 2 ** -1000 is still a normal float; past it a decay factor is flushed to zero.
_MAX_HALVINGS = 1000.0


@dataclass
class RouteStats:
    latency: float | None = None
    throughput: float | None = None
    failures: float = 0.0
    samples: int = 0
    updated: float = 0.0
    # When a transfer or probe last measured this route; a failure only
    # moves ``updated``. Files from before this field read it as never.
    measured: float = 0.0


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
    retired = _retired_route_ids()
    for source_id, values in routes:
        source_id = str(source_id)
        if source_id in retired:
            continue
        try:
            _routes[source_id] = _parse_route(source_id, values, now)
        except (ValueError, TypeError, AttributeError, ArithmeticError) as exc:
            # One corrupt route must not cost the others their history.
            logger.warning("dropping corrupt statistics of download route {}: {}", source_id, exc)


def _parse_route(source_id: str, values: Any, now: float) -> RouteStats:
    if not isinstance(values, dict):
        raise TypeError("route statistics are not an object")
    updated = _finite(values.get("updated", 0.0), "updated")
    if updated < 0:
        raise ValueError(f"updated {updated!r} is negative")
    # A stamp past the local clock (one that runs behind, a restored VM
    # snapshot) is read as "just now": the history stays usable and decays
    # from there, instead of being lost.
    updated = min(updated, now)
    # Only decides when a PyPI probe is due: unusable means "never measured".
    # A stamp past the clock is not capped to "just now" like ``updated``,
    # which would make it look fresh after the clock was set back.
    measured = _sample(values.get("measured"), math.inf, allow_zero=True) or 0.0
    if measured > now:
        measured = 0.0
    samples = values.get("samples", 0)
    if isinstance(samples, bool) or not isinstance(samples, (int, float)):
        raise TypeError("samples is not a number")
    return RouteStats(
        latency=_loaded_sample(source_id, values, "latency", _MAX_LATENCY, allow_zero=True),
        throughput=_loaded_sample(source_id, values, "throughput", _MAX_THROUGHPUT),
        failures=min(_MAX_FAILURES, max(0.0, _finite(values.get("failures", 0.0), "failures"))),
        samples=min(_MAX_SAMPLES, max(0, int(_finite(samples, "samples")))),
        updated=updated,
        measured=measured,
    )


def _loaded_sample(
    source_id: str, values: dict[str, Any], name: str, limit: float, *, allow_zero: bool = False,
) -> float | None:
    """A persisted measurement; an unusable one is ignored, keeping the rest of the route."""
    value = values.get(name)
    sample = _sample(value, limit, allow_zero=allow_zero)
    # None and 0 mean "not measured" and are not reported.
    if sample is None and value is not None and (isinstance(value, bool) or value != 0):
        logger.warning("ignoring corrupt {} of download route {}: {!r}", name, source_id, value)
    return sample


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} is not a number")
    number = float(value)  # OverflowError for an integer beyond float range
    if not math.isfinite(number):
        raise ValueError(f"{name} is not finite")
    return number


def _sample(value: Any, limit: float, *, allow_zero: bool = False) -> float | None:
    """A usable measurement capped at ``limit``, or None.

    A latency may be 0: a fast round trip can measure 0 s on a coarse clock
    and is still a sample. A throughput must be positive.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
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
    # A route that recovers is reported again if it breaks later.
    _reported_routes.discard(source_id)
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
    # Unconditional: a new route has no failures, and a history stamped 0 or
    # carrying failures without samples still decays (to zero, via _decay).
    stats.failures *= _decay(now - stats.updated, _FAILURE_HALF_LIFE)
    latency = _sample(observation.latency, _MAX_LATENCY, allow_zero=True)
    throughput = _sample(observation.throughput, _MAX_THROUGHPUT)
    stats.latency = _blend(stats.latency, latency)
    stats.throughput = _blend(stats.throughput, throughput)
    if not observation.failed and (latency is not None or throughput is not None):
        stats.measured = now
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
    global _loaded, _generation, _last_pypi_probe
    _routes.clear()
    _last_pypi_probe = None
    _reported_routes.clear()
    _generation += 1
    _loaded = True
    await run_file_operation(_delete_stats_file, _generation)


def download_candidates(url: str, preferences: DownloadPreferences) -> list[tuple[str, str]]:
    """Routes for one package, best expected completion time first in auto mode."""
    family = family_for(url, DOWNLOAD_FAMILIES)
    if family is None:
        return [(urlparse(url).hostname or "origin", url)]
    direct = (family.direct_id, url)
    if preferences.mode == "direct":
        return [direct]
    mirrors = [(key, family.mirror_url(key, url)) for key, _ in family.mirrors]
    if preferences.mode == "specified":
        specified = preferences.specified_for(family)
        return [next(item for item in mirrors if item[0] == specified)]
    return _rank([direct, *mirrors])


def _rank(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    now = time.time()
    # Scored before sorting: estimate_route never raises, and a failure inside
    # a sort key would abort the whole ranking rather than one route.
    seconds = {key: estimate_route(key, now=now).seconds_for(TYPICAL_PACKAGE_BYTES)
               for key, _ in candidates}
    # sorted() is stable: equal estimates keep the declared order. With no
    # history that puts direct and the first mirrors into the opening race,
    # which opens unmeasured routes together and keeps the first to answer.
    return sorted(candidates, key=lambda candidate: seconds[candidate[0]])


# The PyPI family is never downloaded through here: uv fetches and checks
# every file. Its routes are measured by fetching one small simple page.
PYPI_PROBE_PROJECT = "iniconfig"
PYPI_PROBE_TIMEOUT = 5.0
_PYPI_PROBE_MAX_BYTES = 256 * 1024
# A measurement this recent is still used as it is.
PYPI_PROBE_FRESH_SECONDS = 6 * 3600.0
# After a probe, however it went, the next one waits at least this long.
PYPI_PROBE_RETRY_SECONDS = 10 * 60.0
_last_pypi_probe: float | None = None
_pypi_probe_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def pypi_index_url(source_id: str) -> str:
    """The simple index address of one PyPI route, as uv takes it."""
    return dict(PYPI_FAMILY.sources)[source_id].rstrip("/")


async def select_pypi_index_url(preferences: DownloadPreferences | None = None) -> str:
    """The PyPI simple index that should be used now, for uv's default index.

    Direct mode is the official index and specified mode the chosen mirror.
    Auto mode measures every PyPI route when the recorded measurements are
    stale and the last probe is long enough ago, then takes the route
    expected to answer first. Probing has a timeout and stops when the caller
    is cancelled. Must run on the loop that owns the route statistics.
    """
    if preferences is None:
        preferences, _ = await run_file_operation(load_preferences)
    if preferences.mode == "direct":
        return pypi_index_url(PYPI_FAMILY.direct_id)
    if preferences.mode == "specified":
        return pypi_index_url(preferences.specified_pypi_source_id)
    try:
        await refresh_pypi_routes()
    except Exception as exc:  # CancelledError is not an Exception and still propagates.
        # Choosing a mirror must not fail the install: rank on what is known.
        logger.warning("PyPI index probe could not run: {!r}", exc)
    best, _ = _rank(list(PYPI_FAMILY.sources))[0]
    return pypi_index_url(best)


def _pypi_probe_due(now: float) -> bool:
    # A clock that went backwards past the last probe does not hold it off.
    if _last_pypi_probe is not None and 0 <= now - _last_pypi_probe < PYPI_PROBE_RETRY_SECONDS:
        return False
    _ensure_loaded()
    # Only a route that answered counts: a probe that failed everywhere
    # (offline, say) is retried after the retry interval, even when older
    # measurements are still kept for ranking.
    measured = max((stats.measured for key, _ in PYPI_FAMILY.sources
                    if (stats := _routes.get(key)) is not None), default=0.0)
    # As above: a measurement stamped in the future does not stay fresh.
    return not 0 <= now - measured < PYPI_PROBE_FRESH_SECONDS


def _probe_lock() -> asyncio.Lock:
    global _pypi_probe_lock
    loop = asyncio.get_running_loop()
    if _pypi_probe_lock is None or _pypi_probe_lock[0] is not loop:
        _pypi_probe_lock = (loop, asyncio.Lock())
    return _pypi_probe_lock[1]


async def refresh_pypi_routes() -> None:
    """Probe the PyPI routes if that is due; concurrent callers share one probe."""
    global _last_pypi_probe
    async with _probe_lock():
        if not _pypi_probe_due(time.time()):
            return
        generation = route_generation()
        observations = await _probe_pypi_routes()
        # A reset during the probe drops its observations below; it must not
        # count as a probe either, or the next caller would skip probing.
        if generation == route_generation():
            _last_pypi_probe = time.time()
        await commit_route_observations(observations, generation)


async def _probe_pypi_routes() -> list[RouteObservation]:
    httpx = await ensure_httpx()
    client = await package_http_client()
    marker = f"{PYPI_PROBE_PROJECT}-".encode()

    async def probe(source_id: str, base: str) -> RouteObservation:
        url = f"{base}{PYPI_PROBE_PROJECT}/"
        started = time.monotonic()
        body = b""
        try:
            async with asyncio.timeout(PYPI_PROBE_TIMEOUT):
                async with client.stream("GET", url, headers={"Accept": "text/html"}) as response:
                    if response.status_code != 200:
                        raise ValueError(f"status {response.status_code}")
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > _PYPI_PROBE_MAX_BYTES:
                            raise ValueError("simple page too large")
            if marker not in body:
                raise ValueError("not a simple page of the probe project")
        except Exception as exc:  # CancelledError is not an Exception and still stops the probe.
            logger.info("PyPI index probe failed route={} error={}", source_id, type(exc).__name__)
            return RouteObservation(source_id, failed=True)
        return RouteObservation(source_id, latency=time.monotonic() - started)

    return list(await asyncio.gather(*(probe(key, base) for key, base in PYPI_FAMILY.sources)))


def warmup_urls(candidates: list[tuple[str, str]]) -> list[str]:
    """Every origin a download over these routes connects to before its first byte."""
    urls = [url for _, url in candidates]
    if any(source_id == GITHUB_DIRECT_SOURCE_ID for source_id, _ in candidates):
        urls.append(_GITHUB_ASSET_ORIGIN)
    return urls
