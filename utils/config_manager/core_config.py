# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core config mixin.

get_core_config / get_model_api_config snapshot assembly, geo (mainland vs
non-mainland) region resolution, free-route URL adjustment and the agent/voice
free-tier predicates.

The geo caches themselves are class attributes on the assembled
``ConfigManager`` (single owner); methods below resolve them late through
the package facade.

Region resolution: one background probe, everyone else reads its verdict
------------------------------------------------------------------------
A single daemon thread (``_ip_probe_loop``) retries the ip-api.com lookup with
backoff until it lands a verdict, then exits. It is the ONLY writer of
``_ip_check_cache``. Everything else — ``get_core_config``, session setup,
startup — only reads that value (and idempotently makes sure the probe is
running). This shape is deliberate: an earlier version kicked a fresh probe
from each call site, which forced generation tickets, wedged-thread caps and
stale-replacement logic to keep concurrent probes from clobbering each other.
Collapsing to one owner deletes that entire class of bug. When you touch this,
keep it one-writer; a fix that spawns a second concurrent probe brings the
whole mess back.

Invariants, each learned by breaking it:

1. The probe never does network IO on the caller's thread. ``get_core_config``
   fans out to ~40 sync callers living inside ``async def``, so a blocking probe
   freezes the shared event loop and stalls every WebSocket handshake. Callers
   read the cache; only ``_ip_probe_loop`` blocks, and it is off to the side.
2. The probe is started only for the free ``lanlan.tech`` route. Users on their
   own API keys never have their public IP handed to a third-party geolocation
   service. The gate lives at the callers of ``_check_non_mainland`` (free-route
   code only) — ``get_core_config`` first checks a ``lanlan.tech`` URL exists.
   Re-deriving eligibility elsewhere is how this regressed three times.
3. ``_region_cache`` is written only from the IP verdict. Steam is a fallback
   vote that must never latch: latching it on a cold-boot timeout would let it
   rule permanently, and IP (which bypasses the proxy) must still take over.
4. The probe never gives up permanently. Connectivity can arrive tens of minutes
   in (WiFi after boot); the loop backs off but keeps retrying for the life of
   the process. A DNS-wedged iteration just holds the one thread until the OS
   resolver times out — no leak, because there is only ever one thread.
5. Every path that freezes a base URL into a session settles the region first
   (main session, hot-swap prepare, game session pool). Tests assert this
   structurally, because the real risk is a *new* path added later.
"""
import asyncio
import json
import math
import sys
import threading
import time
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

from config import DEFAULT_CONFIG_DATA, GEOIP_FORCE_NON_MAINLAND
from utils.gptsovits_config import normalize_gsv_api_url
from utils.steam_state import get_steamworks

from ._shared import _as_bool, logger
from .json_update import update_json_config


# Startup migration write retries: Windows os.replace can encounter transient antivirus sharing locks and raise
# PermissionError(WinError 5); abandoning after one attempt leaves old ports until the next startup.
_OPENCLAW_MIGRATION_ATTEMPTS = 3
_OPENCLAW_MIGRATION_RETRY_DELAY_S = 0.1


from utils.http.url import same_endpoint

class CoreConfigMixin:
    """Core config snapshot, geo checks and model API resolution."""

    # Background probe failure backoff: autostart often precedes network-stack readiness, guaranteeing an initial timeout.
    # On machines without Steam, IP is the only criterion; permanent abandonment would lock the machine to domestic routing even if networking
    # recovers minutes later (e.g. user connects WiFi after boot). Use unlimited retries with capped exponential backoff.
    _IP_CHECK_RETRY_BASE_S = 30.0
    _IP_CHECK_RETRY_MAX_S = 600.0
    # Cap the exponent before multiplication: after prolonged offline failures, float * (2**huge integer) raises
    # OverflowError rather than becoming inf. 2**32 * base already exceeds the cap, so truncating here loses nothing.
    _IP_CHECK_MAX_EXPONENT = 32

    @classmethod
    def _ip_check_backoff_s(cls, failures: int) -> float:
        """Seconds to wait before the next probe after `failures` consecutive failures."""
        if failures <= 0:
            return 0.0
        exponent = min(failures - 1, cls._IP_CHECK_MAX_EXPONENT)
        return min(cls._IP_CHECK_RETRY_BASE_S * (2 ** exponent), cls._IP_CHECK_RETRY_MAX_S)

    async def aget_core_config(self):
        """Async wrapper for get_core_config: internally open()+json.load() reads core_config.json;
        async endpoints must offload it to avoid blocking the event loop."""
        return await asyncio.to_thread(self.get_core_config)

    async def aget_model_api_config(self, model_type: str, *, core_config: dict | None = None) -> dict:
        """Async wrapper for get_model_api_config, dual of aget_core_config.

        get_model_api_config resolves everything on top of get_core_config, so it inherits
        the same open()+json.load() on core_config.json; async callers must offload it to
        avoid blocking the event loop.

        Pass ``core_config`` to reuse a snapshot the caller already read (e.g. right after
        aget_core_config): resolution is then pure dict work and needs no thread hop.
        """
        if core_config is not None:
            return self.get_model_api_config(model_type, _core_config=core_config)
        return await asyncio.to_thread(self.get_model_api_config, model_type)

    # --- Core config helpers ---

    # Only lanlan.tech in original configuration is region-rewritten; lanlan.app counts only when the loop rechecks
    # its own rewritten snapshot. Do not conflate these: treating .app as eligible original configuration makes users explicitly configuring
    # lanlan.app look like free-route users and needlessly probes their IP (invariant #2).
    _REGION_HOSTS_RAW = ('lanlan.tech',)
    _REGION_HOSTS_ADJUSTED = ('lanlan.tech', 'lanlan.app')

    # Text slots map to corresponding tier model fields in provider profiles. omni/tts are excluded because their models
    # are not chat tiers; core API / TTS paths choose them independently.
    _SLOT_PROFILE_MODEL_KEYS = {
        'conversation': 'CONVERSATION_MODEL',
        'summary': 'SUMMARY_MODEL',
        'gameMain': 'CONVERSATION_MODEL',
        'gameSummary': 'SUMMARY_MODEL',
        'correction': 'CORRECTION_MODEL',
        'emotion': 'EMOTION_MODEL',
        'vision': 'VISION_MODEL',
        'agent': 'AGENT_MODEL',
    }

    @staticmethod
    def _migrated_openclaw_url(raw_url: object) -> str:
        """Return the 8088 equivalent of a legacy 8089 openclawUrl, or '' if not applicable.

        Pure: no file access, no side effects. Both the read path (in-memory only) and the
        startup migration (which persists) derive the new URL from here.
        """
        if not isinstance(raw_url, str) or not raw_url.strip():
            return ''
        normalized = raw_url.strip().rstrip('/')
        try:
            parsed = urlparse(normalized)
        except Exception:
            return ''
        if not parsed.netloc:
            return ''
        try:
            if parsed.port != 8089:
                return ''
        except ValueError:
            return ''
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        return urlunparse(parsed._replace(netloc=f"{userinfo}{host}:8088"))

    def migrate_openclaw_url_port(self) -> bool:
        """Persist the legacy openclawUrl 8089 -> 8088 rewrite, once, at startup.

        This used to live inside get_core_config, which made that method a
        read-modify-write. Every async caller now reaches it through asyncio.to_thread,
        so the write could interleave with a /core_api save on the loop and replace the
        user's just-saved keys with the worker's older snapshot.

        The read-modify-write goes through ``update_json_config``, the same per-file
        lock every other core_config.json writer takes, so neither a concurrent
        migration nor a /core_api save or memory toggle can be lost in between.
        That guarantee is in-process only: a second entry point (app/monitor.py has
        its own __main__) running as a separate process is not covered.

        A config that exists but cannot be parsed is left untouched (no retry: the
        retry budget is for transient Windows write failures, not for bad content).

        get_core_config still normalizes in memory (see _migrated_openclaw_url) so a
        config that somehow arrives with 8089 later is routed correctly regardless.
        """
        def _mutate(core_cfg: dict):
            migrated = self._migrated_openclaw_url(core_cfg.get('openclawUrl'))
            if migrated:
                core_cfg['openclawUrl'] = migrated
            return migrated

        for attempt in range(_OPENCLAW_MIGRATION_ATTEMPTS):
            try:
                migrated = update_json_config(self, 'core_config.json', _mutate)
            except OSError as exc:
                last = attempt == _OPENCLAW_MIGRATION_ATTEMPTS - 1
                logger.warning(
                    "自动迁移 openclawUrl 到 8088 失败（第 %d/%d 次）: %s",
                    attempt + 1, _OPENCLAW_MIGRATION_ATTEMPTS, exc,
                )
                if last:
                    return False
                # Windows: antivirus/Explorer sharing locks can make os.replace raise PermissionError(WinError 5).
                # Scans typically finish within tens of milliseconds, so short backoff/retry succeeds. POSIX rename(2) has no such restriction;
                # these retries are harmless there.
                time.sleep(_OPENCLAW_MIGRATION_RETRY_DELAY_S * (attempt + 1))
                continue
            except Exception as exc:
                # Corrupt files / non-object top-level values / write-fence rejection will not change on retry; preserve files unchanged.
                from utils.cloudsave_runtime import MaintenanceModeError

                if isinstance(exc, MaintenanceModeError):
                    # Startup during maintenance mode (cloudsave restoration) is expected; readers still normalize in memory.
                    logger.debug("维护模式下跳过 openclawUrl 8088 迁移: %s", exc)
                    return False
                logger.warning("跳过 openclawUrl 8088 迁移（core_config.json 不可改写）: %s", exc)
                return False
            if not migrated:
                return False
            logger.info("已自动将 openclawUrl 从 8089 迁移到 8088: %s", migrated)
            return True
        return False

    @staticmethod
    def _any_free_provider(config) -> bool:
        """Whether any provider slot selects the free route.

        Region rewriting runs over *every* ``*_URL`` key, so a paid core with
        ``assistApi='free'`` still has a lanlan.tech assist URL that depends on the
        verdict. Looking at the core slot alone would stop the probe for those users
        and leave that URL on the mainland endpoint.

        These fields are not touched by the rewrite, which is what makes them usable
        for telling "free URL rewritten to .app" apart from "custom endpoint the user
        happened to host at .app".
        """
        cfg = config or {}
        return 'free' in {
            str(cfg.get('CORE_API_TYPE') or ''),
            str(cfg.get('coreApi') or ''),
            str(cfg.get('assistApi') or ''),
        }

    @classmethod
    def _config_needs_region(cls, config, hosts=None) -> bool:
        """Whether this config actually has a URL whose route depends on the region.

        The single source of truth for probe eligibility (invariant #2). Deriving it
        separately at each call site is exactly how the privacy gate regressed three
        times, so both ``get_core_config`` and the probe loop go through here.

        ``hosts`` says which lanlan hosts count. Callers must pick deliberately:
        ``_REGION_HOSTS_RAW`` for a config as the user wrote it (only ``lanlan.tech``
        is ever rewritten, so an explicitly configured ``lanlan.app`` endpoint is a
        custom route and must not trigger a probe), and ``_REGION_HOSTS_ADJUSTED``
        for the retry loop, which re-reads a snapshot whose free URLs may already
        have been rewritten to ``lanlan.app``.

        Livestream is excluded for the paths it takes over: ``_adjust_free_api_url``
        derives those from the livestream prefix *before* consulting the region, so a
        livestream user needs no verdict for them and must not be probed on their
        account — but only when the derivation can actually succeed.
        """
        from utils.config_manager import get_livestream_config, is_livestream_active

        hosts = tuple(hosts) if hosts else cls._REGION_HOSTS_RAW

        try:
            livestream = is_livestream_active()
            livestream_prefix = get_livestream_config()['server_prefix'] if livestream else ''
        except Exception:
            # Treat unreadable livestream configuration as disabled, making eligibility more conservative
            # (exclude no URLs), never missing routes needing region selection.
            livestream, livestream_prefix = False, ''

        for key, value in (config or {}).items():
            if not (key.endswith('_URL') and isinstance(value, str)):
                continue
            # AGENT_MODEL_URL never undergoes region rewriting (free-agent-model uses a fixed CN endpoint;
            # see _normalize_agent_url and the rewrite-loop exemption), so region selection has no effect on it.
            # Including it as needing region would start probing an unrewritten URL when neither core nor assist is free,
            # exposing IP pointlessly (invariant #2).
            if key == 'AGENT_MODEL_URL':
                continue
            try:
                parsed = urlparse(value)
                host = (parsed.hostname or '').lower()
                path = parsed.path or ''
            except Exception:
                # Unparseable URLs cannot be either official host; skip as not needing region.
                # Correct conservative direction: avoid probing rather than exposing IP for malformed URLs.
                continue
            # Match hostname, not substrings: a custom URL such as
            # https://custom.example/v1/lanlan.tech would otherwise be mistaken for an official free
            # route and trigger entirely needless IP exposure.
            #
            # Include both hosts: this function handles original get_core_config configuration (lanlan.tech)
            # and rewritten snapshots read by probe-loop eligibility checks (lanlan.app after overseas
            # classification). Recognizing only lanlan.tech would stop probing after the first failure for users
            # classified overseas by Steam while IP remains undecided, incorrectly deciding region selection is no longer needed.
            if not any(host == h or host.endswith('.' + h) for h in hosts):
                continue
            # URLs overridden by a livestream prefix need no region selection, but confirm derivation **actually succeeds**
            # before excluding them: a nonempty malformed server_prefix (e.g. missing scheme in
            # "localhost:8080/tok") makes _derive_livestream_url return empty and fall back to region
            # rewriting; incorrectly declaring no need for region would pin overseas users to lanlan.tech.
            if livestream and path in cls._LIVESTREAM_DERIVE_PATHS:
                if cls._derive_livestream_url(value, livestream_prefix):
                    continue
            return True
        return False

    @staticmethod
    def _ip_probe_once():
        """One ip-api.com lookup. Returns True/False on a verdict, None on failure."""
        import urllib.request
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=countryCode",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # Explicitly disable proxies so probing detects the user's real IP location, not the proxy server's country.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
        country = (data.get("countryCode") or "").upper()
        return (country != "CN") if country else None

    @staticmethod
    def _ip_probe_loop():
        """The single background probe: retry with backoff until a verdict lands.

        One long-lived daemon thread owns the whole retry schedule, so there is
        exactly one writer of ``_ip_check_cache`` and no per-call thread churn.
        That is what lets this stay simple where the earlier per-call design could
        not: no generation tickets, no wedged-thread bookkeeping, no replacement cap.
        A DNS-wedged iteration just holds this one thread until the OS resolver times
        out; the next iteration then retries. Never gives up — connectivity can
        arrive tens of minutes in (the user connects WiFi after boot).
        """
        from utils.config_manager import ConfigManager

        failures = 0
        try:
            while ConfigManager._ip_check_cache is None:
                # Recheck eligibility **before** the request: users may leave free routing for custom APIs during backoff.
                # Checking afterward would make the loop wake and needlessly contact ip-api.com once
                # before stopping, exposing precisely the IP it should protect. Skip the first recheck: the caller just
                # authorized via needs_region, so rereading configuration is unnecessary. Use shared
                # _config_needs_region instead of defining another criterion here (invariant #2).
                if failures and not ConfigManager._free_route_still_needs_region():
                    print("[GeoIP] free route no longer selected, stopping probe", file=sys.stderr)
                    return
                try:
                    ConfigManager._ip_probe_in_flight.set()
                    result = ConfigManager._ip_probe_once()
                    if result is not None:
                        ConfigManager._ip_check_cache = result
                        print(f"[GeoIP] HTTP IP check: non_mainland={result}", file=sys.stderr)
                        return
                except Exception as e:
                    print(f"[GeoIP] HTTP IP check failed: {e}", file=sys.stderr)
                finally:
                    # Backoff is not in-flight: no result can arrive then, so waiters skip join,
                    # avoiding a needless join timeout for each session (see join_ip_probe).
                    # Exception: retain the flag when wake is already set (kick arrived during this attempt): the loop
                    # immediately consumes the wake and retries, but first performs eligibility rechecking (disk read) before
                    # setting the flag again. Clearing it during this rolling window makes session joins mistake deliberately awakened
                    # retries for ordinary backoff and give up. If eligibility checks finish probing or stopping exits,
                    # outer finally clears the flag, leaving no stale state.
                    # Checking wake and clearing the flag occur under _geo_probe_lock, excluding _kick_ip_probe's
                    # two writes. Without locking, this finally could intervene between kick's in_flight preset and wake set,
                    # observe unset wake, and wrongly clear the newly preset flag.
                    with ConfigManager._geo_probe_lock:
                        if not ConfigManager._ip_probe_wake.is_set():
                            ConfigManager._ip_probe_in_flight.clear()

                failures += 1
                wait = ConfigManager._ip_check_backoff_s(failures)
                print(f"[GeoIP] HTTP IP check will retry in {wait:.0f}s", file=sys.stderr)
                # Interruptible backoff. _ip_probe_stopping distinguishes two wake meanings:
                #   stopping=True -> shutdown / test cleanup; exit cleanly;
                #   stopping=False -> retry now without waiting (startup warmup; see
                #                     _kick_ip_probe), skipping remaining backoff for the next iteration.
                # Neither occurs in normal production operation, matching time.sleep(wait).
                if ConfigManager._ip_probe_wake.wait(wait):
                    if ConfigManager._ip_probe_stopping:
                        return
                    ConfigManager._ip_probe_wake.clear()
        finally:
            ConfigManager._ip_probe_in_flight.clear()

    @staticmethod
    def _free_route_still_needs_region() -> bool:
        """Re-check probe eligibility from the current config, for the retry loop."""
        from utils.config_manager import ConfigManager, get_config_manager

        try:
            cfg = get_config_manager().get_core_config() or {}
            # Check **route selection** first: these fields are never region-rewritten, distinguishing free routing rewritten to
            # lanlan.app from users explicitly choosing lanlan.app for custom endpoints. Host-only checks
            # misclassify the latter as free routing and keep contacting ip-api.com after users switch away.
            if not ConfigManager._any_free_provider(cfg):
                return False
            # Still free routing: confirm some URL actually needs region selection (livestream may override them all).
            # This snapshot is **already rewritten**, so use the ADJUSTED set.
            return ConfigManager._config_needs_region(
                cfg, ConfigManager._REGION_HOSTS_ADJUSTED,
            )
        except Exception:
            return True    # Conservatively continue on unreadable configuration rather than abandoning probing after one read failure.

    @staticmethod
    def _ensure_ip_probe_started():
        """Idempotently start the background probe. Cheap to call from anywhere.

        The free-route gate lives in the *callers* (only ``_check_non_mainland``
        reaches here, and only free-route code reaches that), so this itself makes
        no policy decision — it just guarantees at most one probe thread is running.
        """
        from utils.config_manager import ConfigManager

        if ConfigManager._ip_check_cache is not None:
            return
        with ConfigManager._geo_probe_lock:
            if ConfigManager._ip_check_cache is not None:
                return
            thread = ConfigManager._ip_probe_thread
            if thread is not None and thread.is_alive():
                # Alive means the retry plan is running, so returning here is correct and needs no replacement probe.
                # This thread runs _ip_probe_loop, not a single probe: an iteration blocked in getaddrinfo
                # raises after the OS resolver timeout, then the loop catches, backs off, and retries.
                # A replacement thread would call the same getaddrinfo and block identically, only introducing
                # multiple writers and thread leaks, precisely what this design eliminates (see module docstring).
                return
            thread = threading.Thread(
                target=ConfigManager._ip_probe_loop,
                name="geoip-probe",
                daemon=True,
            )
            # Clear the stop signal before a new loop (previously set when leaving free routing),
            # and preset in-flight: even before the thread enters lookup, treat it as active,
            # preventing an immediately following join from mistaking it for backoff and skipping.
            # Reset _ip_probe_stopping too: this shutdown/test-cleanup flag, if left set,
            # makes restarted loops exit on their first wake and _kick_ip_probe refuse to wake them;
            # a stop flag should affect only the loop for which it was set.
            ConfigManager._ip_probe_stopping = False
            ConfigManager._ip_probe_wake.clear()
            ConfigManager._ip_probe_in_flight.set()
            ConfigManager._ip_probe_thread = thread
            thread.start()

    @staticmethod
    def _kick_ip_probe() -> None:
        """Ask a backing-off probe to retry now instead of waiting out its backoff.

        Startup needs this: the first attempt often fails before the network stack is
        up, and the resulting 30s backoff is far longer than the warmup is willing to
        wait — so without a kick the warmup would admit sessions with no verdict.
        """
        from utils.config_manager import ConfigManager

        # The two writes (preset in-flight + set wake) must exclude the probe loop finally's wake-dependent
        # clearing through shared _geo_probe_lock. Without it, finally can observe wake unset and
        # erase the new preset flag, nullifying it and making join mistake awakened
        # retries for backoff in the rolling window. Either critical-section order works:
        # kick first lets finally see wake set and retain the flag; finally first lets kick subsequently
        # set both, retaining the flag until the next iteration takes over.
        with ConfigManager._geo_probe_lock:
            thread = ConfigManager._ip_probe_thread
            if thread is not None and thread.is_alive() and not ConfigManager._ip_probe_stopping:
                # Preset in-flight before waking, following _ensure_ip_probe_started:
                # the awakened loop only sets in_flight after OS scheduling. If callers (typically starting a session
                # immediately after returning to free routing) reach join earlier, they would mistake this
                # **deliberately awakened** attempt for ordinary backoff and stop waiting. If eligibility rechecks
                # end the awakened loop, outer finally clears the preset flag without leaving stale state.
                ConfigManager._ip_probe_in_flight.set()
                ConfigManager._ip_probe_wake.set()

    @staticmethod
    def join_ip_probe(timeout: float = 5.0, through_backoff: bool = False) -> bool:
        """Block until the in-flight GeoIP probe finishes. Returns whether a verdict landed.

        Only for startup and session setup, and only from a worker thread (see
        ``awarmup_region_check``): request paths must never wait on the probe.
        Waiting before a session starts is what keeps it off the transient mainland
        fallback — the route is frozen into each session at start_session time.

        Skipped entirely once Steam has answered. The wait exists to avoid routing on
        *no* information; Steam's answer is information, and it is already enough to
        pick a route. IP still outranks it — the Steam verdict is never latched, so
        the probe takes over for later sessions once it lands. Waiting anyway would
        tax exactly the users who already have an answer in hand.
        """
        from utils.config_manager import ConfigManager

        # A forced-region debug override is immediately resolved (_check_non_mainland returns it directly
        # without caching or probing). Cache-only checks would keep free routes unresolved under override,
        # wasting the full warmup timeout every startup.
        if GEOIP_FORCE_NON_MAINLAND is not None:
            return True
        if ConfigManager._steam_check_cache is not None:
            return True
        # Wait only while a probe is **actually making a request**. A loop sleeping in backoff is alive,
        # but no result can arrive; waiting would charge every session a needless join timeout.
        # When GeoIP is blocked, backoff reaches ten minutes, adding a fixed 1.5s to every session.
        # Wait for **this request**, not the thread. join(thread) waits until the entire loop ends, but
        # failures enter 30–600s backoff while remaining alive, with no possible result,
        # needlessly consuming timeout. Watch in_flight instead: clearing it means this attempt finished.
        # through_backoff serves startup warmup, whose goal is resolving region before admitting sessions.
        # Initial probes often fail before network readiness and enter backoff; returning immediately on backoff as sessions do
        # would nullify warmup, exposed when probing moved earlier into startup cleanup.
        # Session paths retain False: their 1.5s prevents wrong route pinning, not worthwhile for empty backoff waiting.
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            if ConfigManager._ip_check_cache is not None:
                return True
            if not ConfigManager._ip_probe_in_flight.is_set():
                # through_backoff means wait across backoff, not always consume the full timeout.
                # If no probe runs (custom APIs / all livestream-derived URLs mean the free-route gate never
                # started one, or the loop ended after leaving free routing), no amount of waiting yields a result.
                # Startup warmup is the sole through_backoff=True caller; waiting would make every
                # non-Steam custom-API user wait five needless seconds before session admission.
                # Filter using in_flight first: _ensure_ip_probe_started presets it before start(),
                # preventing newly created but not yet executing threads from being mistaken for absent.
                thread = ConfigManager._ip_probe_thread
                if thread is None or not thread.is_alive():
                    return False
                if not through_backoff:
                    return False    # Backoff entered: this attempt finished, so no further waiting can yield its result.
            if time.monotonic() >= deadline:
                return ConfigManager._ip_check_cache is not None
            time.sleep(0.02)

    async def aensure_region_resolved(self, timeout: float = 1.5) -> bool:
        """Last chance for an in-flight probe before a session freezes its route.

        A session pins its base URL at start_session and never revisits it, so a
        verdict that lands one second too late costs that whole session. Startup
        already waits, but its join can expire while the probe sits in DNS
        resolution (the 3s socket timeout does not cover ``getaddrinfo``).

        Costs nothing on the normal path: returns immediately unless the region is
        still unknown *and* a probe is actually in flight. The wait itself is
        offloaded, so the event loop keeps running.
        """
        from utils.config_manager import ConfigManager

        # Debug-forced region means resolved (_check_non_mainland always returns the forced value),
        # without caching or probing; waiting is pointless.
        if GEOIP_FORCE_NON_MAINLAND is not None:
            return True
        if ConfigManager._region_cache is not None or ConfigManager._ip_check_cache is not None:
            return True
        # Steam already resolved enough to choose a route, avoiding IP wait. IP still takes over when available
        # (Steam results never finalize resolution), saving latency without sacrificing correctness.
        if self._check_steam_non_mainland() is not None:
            return True

        # Read configuration once to ensure background probing starts idempotently. Deliberately use aget_core_config,
        # not _ensure_ip_probe_started directly: the free-route gate lives in get_core_config
        # (needs_region + _check_non_mainland). Direct calls would probe custom-API / livestream
        # users and send IPs to ip-api.com despite their routes never undergoing regional rewriting.
        cfg = await self.aget_core_config()
        if ConfigManager._ip_check_cache is not None:
            return True
        # Resolve vacuous truth before checking probe liveness: configuration without region-sensitive URLs (custom APIs /
        # entirely livestream-derived routes) is resolved regardless of old probes stuck in DNS. After leaving
        # free routing, residual probes may remain alive+in_flight for a long time because getaddrinfo ignores
        # socket timeouts. Waiting for thread death would charge every later session/plugin request a full timeout
        # for region-independent routes.
        if not (
            ConfigManager._any_free_provider(cfg)
            and ConfigManager._config_needs_region(
                cfg, ConfigManager._REGION_HOSTS_ADJUSTED,
            )
        ):
            return True
        thread = ConfigManager._ip_probe_thread
        if ((thread is None or not thread.is_alive())
                and not ConfigManager._ip_probe_in_flight.is_set()):
            # Free routing but no running probe (the loop exited just after the configuration read above or already finished).
            # Check in_flight too: _ensure_ip_probe_started presets it before start(),
            # so assigned-but-not-started threads must not appear absent. Follow join_ip_probe's
            # same guard, sending that window to join below.
            # Recheck resolution: the thread may have died just after writing cache, leaving a valid result.
            return ConfigManager._ip_check_cache is not None
        resolved = await asyncio.to_thread(self.join_ip_probe, timeout)
        if not resolved:
            # Still unresolved after waiting: this session pins the mainland fallback for its entire lifetime. Waiting forever
            # would hang sessions, so record the failure for diagnosis; otherwise occasional whole-session slowness
            # for overseas users leaves no log evidence.
            logger.warning(
                "[GeoIP] 区域判定在会话开始前仍未落地（等待 %.1fs），本场会话按大陆线路启动",
                timeout,
            )
        return resolved

    async def awarmup_region_check(self, timeout: float = 5.0) -> bool:
        """Resolve the region before the server starts accepting sessions.

        Reads the config (which kicks the probe only on the free ``lanlan.tech``
        route, so users on their own API keys never hand their IP to a third-party
        geolocation service), then waits for that probe off the event loop.

        Returns ``True`` when a verdict is in hand *or* nothing in the config
        depends on one; ``False`` means a region-dependent route is still waiting
        on the probe — the only case the startup log should mention.
        """
        from utils.config_manager import ConfigManager

        cfg = await self.aget_core_config()
        # Resolve vacuous truth early regardless of probe liveness: custom-API / all-livestream configuration needs no
        # regional result (privacy gate). Returning False would log a nonexistent subsequent backoff retry;
        # old probes left after switching away from free routing may remain alive in DNS
        # for ages, wasting startup time if awaited. Match aensure_region_resolved
        # symmetrically, using the same _any_free_provider conjunction (explicit custom endpoints
        # on lanlan.app are not free routes).
        if not (
            ConfigManager._any_free_provider(cfg)
            and ConfigManager._config_needs_region(
                cfg, ConfigManager._REGION_HOSTS_ADJUSTED,
            )
        ):
            return True
        self._kick_ip_probe()
        return await asyncio.to_thread(self.join_ip_probe, timeout, True)

    @staticmethod
    def _check_ip_non_mainland_http():
        """Read the IP geolocation verdict. Pure read — never does network IO here.

        The verdict is produced by the background probe thread (``_ip_probe_loop``);
        this only reports what has landed so far (True/False, or None if the probe
        has not answered yet). Blocking here is not an option: ``get_core_config``
        fans out to ~40 sync callers inside ``async def`` (``get_model_api_config``
        in ``_start_session_prepare_runtime`` among them), so a 3s connect timeout
        would freeze the shared event loop and stall every WebSocket handshake.
        """
        from utils.config_manager import ConfigManager

        return ConfigManager._ip_check_cache

    @staticmethod
    def _check_steam_non_mainland():
        """Steam-based IP country check via Steamworks SDK.

        Fallback source only — see _check_non_mainland for why the HTTP probe outranks it.
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        if ConfigManager._steam_check_cache is not None:
            return ConfigManager._steam_check_cache
        try:
            steamworks = get_steamworks()
            if steamworks is None:
                return None
            ip_country = steamworks.Utils.GetIPCountry()
            if isinstance(ip_country, bytes):
                ip_country = ip_country.decode('utf-8')
            if ip_country:
                result = ip_country.upper() != "CN"
                ConfigManager._steam_check_cache = result
                print(f"[GeoIP] Steam IP check: country={ip_country}, non_mainland={result}", file=sys.stderr)
                return result
        except ImportError:
            pass
        except Exception as e:
            print(f"[GeoIP] Steam IP check failed: {e}", file=sys.stderr)
        return None

    def _check_non_mainland(self) -> bool:
        """Region check: HTTP IP geo first, Steam geo only as a fallback.

        This used to require both to say non-mainland. Steamworks stays silent
        forever on non-Steam builds and on Steam builds started without the Steam
        client running — most users only auto-start N.E.K.O., not Steam — so that
        second yes vote never arrived and pinned those overseas users to the
        mainland route.

        IP now decides whenever it has an answer, because it is the more accurate
        of the two: the probe disables proxies explicitly, so a user behind a plain
        system HTTP proxy still geolocates to their real country, while
        ``Utils.GetIPCountry()`` reports whatever exit IP Steam's servers saw — the
        proxy's. Steam only breaks the tie when the probe has no answer at all.
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        # Debug override: return non-None config.GEOIP_FORCE_NON_MAINLAND directly, bypassing actual detection.
        # Keep None in production to use detection below. Change that constant in config/__init__.py, not here.
        if GEOIP_FORCE_NON_MAINLAND is not None:
            print(
                f"[GeoIP] override active: forcing non-mainland={GEOIP_FORCE_NON_MAINLAND} "
                "(config.GEOIP_FORCE_NON_MAINLAND)",
                file=sys.stderr,
            )
            return GEOIP_FORCE_NON_MAINLAND

        if ConfigManager._region_cache is not None:
            return ConfigManager._region_cache

        # Ensure background probing is running idempotently; callers enforce the free-route gate (see _ensure_ip_probe_started).
        self._ensure_ip_probe_started()

        # IP is authoritative: finalize its result once available. The probe is the sole writer, so every observed value is current;
        # no stale-result overwrite exists, since the single-threaded loop exits on success.
        ip_result = self._check_ip_non_mainland_http()
        if ip_result is not None:
            ConfigManager._region_cache = ip_result
            ConfigManager._geo_indeterminate_logged = False
            print(f"[GeoIP] IP decides: non_mainland={ip_result}", file=sys.stderr)
            return ip_result

        steam_result = self._check_steam_non_mainland()
        # Background probing may resolve during the Steam query above. Without rechecking, a fallback result
        # would override newly available authority; get_core_config checks each URL separately, potentially mixing
        # IP and Steam decisions in one snapshot (opposite directions under a proxy).
        ip_result = self._check_ip_non_mainland_http()
        if ip_result is not None:
            ConfigManager._region_cache = ip_result
            ConfigManager._geo_indeterminate_logged = False
            print(f"[GeoIP] IP decides (landed during Steam check): non_mainland={ip_result}", file=sys.stderr)
            return ip_result

        if steam_result is not None:
            # Consult Steam only while IP probing is unresolved. It reflects the outbound IP Steam's server sees,
            # following proxies too, so it is only fallback evidence and **never finalizes** _region_cache:
            #  - Steam often has a result when the first cold-start probe times out; finalizing it would make that result permanent,
            #    preventing IP retries from ever taking over;
            #  - Prolonged probe failure does not make Steam correct: overseas-proxied Steam can disagree with temporarily
            #    unavailable direct GeoIP, which must take over after network recovery.
            if not ConfigManager._geo_steam_fallback_logged:
                ConfigManager._geo_steam_fallback_logged = True
                print(
                    f"[GeoIP] Steam fallback: non_mainland={steam_result} "
                    "(IP has no verdict yet, still retrying)",
                    file=sys.stderr,
                )
            return steam_result

        # No verdict from either source (ip-api.com unreachable AND Steam not yet
        # initialised).  Do NOT write to _region_cache: either may become definitive
        # shortly after this call, and caching False here would permanently suppress
        # re-evaluation.
        # Callers that iterate get_core_config() will simply retry the geo check on the
        # next invocation until at least one source becomes definitive.
        if not ConfigManager._geo_indeterminate_logged:
            ConfigManager._geo_indeterminate_logged = True
            print("[GeoIP] Both sources indeterminate, transient mainland default", file=sys.stderr)
        return False

    # Livestream derivation overrides only these three known free endpoints, avoiding hijacking other lanlan.tech paths
    # such as future non-data /docs or /metrics endpoints.
    _LIVESTREAM_DERIVE_PATHS = frozenset({'/core', '/text/v1', '/tts'})

    def _adjust_free_api_url(self, url: str, is_free: bool, non_mainland=None) -> str:
        """Internal URL adjustment for free API users.

        Priority: livestream prefix derivation > overseas lanlan.tech→lanlan.app switch > return as-is.
        When livestream is enabled it only takes over whitelisted free-path endpoints under
        the lanlan.tech domain (/core /text/v1 /tts); other paths go through the original region switch.

        ``non_mainland`` lets a caller rewriting several URLs pass one region verdict
        for all of them. Resolving per URL is not safe: the verdict is not cached
        while it is still provisional, so Steam initialising midway through the loop
        would leave earlier URLs on lanlan.tech and later ones on lanlan.app — one
        snapshot pointing at two regions.
        """
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites.
        from utils.config_manager import get_livestream_config, is_livestream_active

        if not url:
            return url
        # Match hostname rather than substrings, as _config_needs_region does: if a custom
        # URL's path contains "lanlan.tech" (e.g. https://custom.example/v1/lanlan.tech),
        # substring gating passes and replacement below rewrites its path, corrupting the custom endpoint.
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or '').lower()
        except Exception:
            return url
        if not (host == 'lanlan.tech' or host.endswith('.lanlan.tech')):
            return url

        try:
            if is_livestream_active():
                orig_path = parsed.path or ''
                if orig_path in self._LIVESTREAM_DERIVE_PATHS:
                    derived = self._derive_livestream_url(
                        url, get_livestream_config()['server_prefix']
                    )
                    if derived:
                        return derived
        except Exception as e:
            logger.warning(f"Livestream URL 派生失败，回退到原始路径: {e}")

        try:
            overseas = non_mainland if non_mainland is not None else self._check_non_mainland()
            if overseas:
                # Overseas free routing uniformly uses www.lanlan.app, including /tts. That node passes the client's
                # voice field through to Gemini, supporting all Gemini voices + yui. The old .replace downgrading /tts
                # to bare lanlan.app, an older endpoint forcing Leda, has been removed.
                # Replace only netloc after confirming the official host; preserve path/query unchanged.
                # Lowercase before replacement: the host gate uses normalized lowercase hostname, but case-sensitive netloc
                # replacement would silently miss variants like WWW.LANLAN.TECH, leaving overseas
                # users on mainland endpoints. DNS ignores netloc case, so lowercasing loses nothing.
                return urlunparse(parsed._replace(
                    netloc=parsed.netloc.lower().replace('lanlan.tech', 'lanlan.app')))
        except Exception:
            pass

        return url

    def _normalize_agent_url(self, url: str) -> str:
        """Temporarily do not rewrite the Agent URL.

        free-agent-model must use the CN ``lanlan.tech`` text entry from the config; keep
        AGENT_MODEL_URL as-is here to avoid normalizing it to ``lanlan.app``.
        """
        return url

    @staticmethod
    def _derive_livestream_url(original_url: str, prefix: str) -> str:
        """Derive the equivalent address of a lanlan.tech URL from the livestream server_prefix.

        - keeps the original URL's path (``/core`` / ``/tts`` / ``/text/v1``) appended after the prefix path
        - scheme family is unchanged (ws/wss in → ws/wss out; http/https in → http/https out)
        - encryption (the ``s`` suffix) follows the prefix's scheme (https/wss prefix → encrypted output)

        Examples:
        - ``wss://www.lanlan.tech/core`` + ``http://host:port/tok`` → ``ws://host:port/tok/core``
        - ``https://www.lanlan.tech/text/v1`` + ``http://host:port/tok`` → ``http://host:port/tok/text/v1``
        - ``wss://www.lanlan.tech/tts`` + ``https://host/tok`` → ``wss://host/tok/tts``
        """
        if not original_url or not prefix:
            return ''
        try:
            orig = urlparse(original_url)
            pref = urlparse(prefix)
        except Exception:
            return ''
        if not pref.scheme or not pref.netloc:
            return ''

        is_ws_family = orig.scheme in ('ws', 'wss')
        is_secure = pref.scheme in ('https', 'wss')
        if is_ws_family:
            out_scheme = 'wss' if is_secure else 'ws'
        else:
            out_scheme = 'https' if is_secure else 'http'

        base_path = pref.path.rstrip('/')
        return f"{out_scheme}://{pref.netloc}{base_path}{orig.path}"

    @staticmethod
    def _provider_url_candidates(profile: dict, url_key: str, list_key: str) -> list[str]:
        """Read the provider's primary URL and candidate URLs, deduped and order-preserving."""
        raw_candidates = [profile.get(url_key)]
        configured_candidates = profile.get(list_key)
        if isinstance(configured_candidates, list):
            raw_candidates.extend(configured_candidates)
        elif isinstance(configured_candidates, str):
            raw_candidates.append(configured_candidates)

        result = []
        seen = set()
        for raw_url in raw_candidates:
            url = str(raw_url or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def _get_saved_provider_url(
        self,
        core_cfg: dict,
        scope: str,
        provider_key: str,
        profile: dict,
        url_key: str,
        list_key: str,
    ) -> str:
        """Return the URL saved by the connectivity test that still belongs to the current provider candidate set."""
        resolved_urls = core_cfg.get('resolvedProviderUrls')
        if not isinstance(resolved_urls, dict):
            return ''
        saved_url = str(resolved_urls.get(f'{scope}:{provider_key}') or '').strip()
        if not saved_url:
            return ''
        candidates = set(self._provider_url_candidates(profile, url_key, list_key))
        return saved_url if saved_url in candidates else ''

    @staticmethod
    def _normalize_realtime_provider(provider: str) -> str:
        """Drop a named provider from the omni slot.

        The omni dropdown used to be filled from the assist (text-LLM) provider
        table, so it offered a per-slot realtime provider. Picking one was then
        honoured downstream: the frontend auto-fills the slot URL,
        ``get_model_api_config`` sees a complete custom triple and stamps an
        ``api_type`` that becomes the process-wide core api type — which also
        drives TTS worker selection, native-voice routing and the audio
        credential. Those follow the core provider, so a divergent pick tore
        the audio stack in half (see ``_resolved_realtime_api_type``).

        The audio stack carries exactly one provider identity, so this slot
        cannot express a second one. A named pick is therefore treated as "not
        selected" and falls back to the core API — which is what the user
        effectively got before, minus the broken realtime session and the
        cross-vendor credential.

        ``follow_*``, ``custom`` and the empty legacy value pass through:
        neither claims a provider identity of its own.
        """
        candidate = (provider or '').strip()
        # follow_assist has no independent meaning in this slot: realtime follows core API. The frontend already
        # uses the **core** provider's url/key for omni follow_assist;
        # only backend key derivation still follows assist, leaving an assist key paired with a core
        # endpoint in snapshots. It cannot currently reach downstream because follow_* leaves REALTIME_MODEL_URL empty,
        # preventing a full custom triple, but one change could activate this latent trap. Normalize it.
        if candidate == 'follow_assist':
            return 'follow_core'
        if not candidate or candidate == 'custom' or candidate.startswith('follow_'):
            return candidate
        # Fall back to follow_core, not empty: an empty string lets URL/model/key override branches below write
        # leftover frontend-autofilled values. follow_core is the existing equivalent-to-unselected path
        # (skip URL, model falls back to CORE_MODEL, key comes from CORE_API_KEY).
        return 'follow_core'

    def get_core_config(self):
        """Read core config dynamically"""
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites.
        from utils.config_manager import (
            get_assist_api_key_fields,
            get_assist_api_profiles,
            get_core_api_profiles,
        )

        # Import all default configuration values from config.
        from config import (
            DEFAULT_CORE_API_KEY,
            DEFAULT_AUDIO_API_KEY,
            DEFAULT_OPENROUTER_API_KEY,
            DEFAULT_MCP_ROUTER_API_KEY,
            DEFAULT_CORE_URL,
            DEFAULT_CORE_MODEL,
            DEFAULT_OPENROUTER_URL,
            DEFAULT_CONVERSATION_MODEL,
            DEFAULT_SUMMARY_MODEL,
            DEFAULT_CORRECTION_MODEL,
            DEFAULT_EMOTION_MODEL,
            DEFAULT_VISION_MODEL,
            DEFAULT_REALTIME_MODEL,
            DEFAULT_TTS_MODEL,
            DEFAULT_AGENT_MODEL,
            DEFAULT_CONVERSATION_MODEL_URL,
            DEFAULT_CONVERSATION_MODEL_API_KEY,
            DEFAULT_SUMMARY_MODEL_URL,
            DEFAULT_SUMMARY_MODEL_API_KEY,
            DEFAULT_CORRECTION_MODEL_URL,
            DEFAULT_CORRECTION_MODEL_API_KEY,
            DEFAULT_EMOTION_MODEL_URL,
            DEFAULT_EMOTION_MODEL_API_KEY,
            DEFAULT_VISION_MODEL_URL,
            DEFAULT_VISION_MODEL_API_KEY,
            DEFAULT_AGENT_MODEL_URL,
            DEFAULT_AGENT_MODEL_API_KEY,
            DEFAULT_REALTIME_MODEL_URL,
            DEFAULT_REALTIME_MODEL_API_KEY,
            DEFAULT_TTS_MODEL_URL,
            DEFAULT_TTS_MODEL_API_KEY,
        )

        config = {
            'CORE_API_KEY': DEFAULT_CORE_API_KEY,
            'AUDIO_API_KEY': DEFAULT_AUDIO_API_KEY,
            'OPENROUTER_API_KEY': DEFAULT_OPENROUTER_API_KEY,
            'MCP_ROUTER_API_KEY': DEFAULT_MCP_ROUTER_API_KEY,
            'CORE_URL': DEFAULT_CORE_URL,
            'CORE_MODEL': DEFAULT_CORE_MODEL,
            'CORE_API_TYPE': 'qwen',
            'OPENROUTER_URL': DEFAULT_OPENROUTER_URL,
            'CONVERSATION_MODEL': DEFAULT_CONVERSATION_MODEL,
            'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'GAME_MAIN_MODEL': DEFAULT_CONVERSATION_MODEL,
            'GAME_SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GEMINI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI_CODE': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DEEPSEEK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO_TTS': '',
            'ASSIST_API_KEY_QWEN_INTL': '',
            'ASSIST_API_KEY_MINIMAX': '',
            'ASSIST_API_KEY_MINIMAX_INTL': '',
            'ASSIST_API_KEY_MIMO': '',
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN': '',
            'ASSIST_API_KEY_GROK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENROUTER': DEFAULT_CORE_API_KEY,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'AGENT_MODEL': DEFAULT_AGENT_MODEL,
            'REALTIME_MODEL': DEFAULT_REALTIME_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'CONVERSATION_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'CONVERSATION_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'GAME_MAIN_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'GAME_MAIN_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'GAME_SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'GAME_SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'CORRECTION_MODEL_URL': DEFAULT_CORRECTION_MODEL_URL,
            'CORRECTION_MODEL_API_KEY': DEFAULT_CORRECTION_MODEL_API_KEY,
            'EMOTION_MODEL_URL': DEFAULT_EMOTION_MODEL_URL,
            'EMOTION_MODEL_API_KEY': DEFAULT_EMOTION_MODEL_API_KEY,
            'VISION_MODEL_URL': DEFAULT_VISION_MODEL_URL,
            'VISION_MODEL_API_KEY': DEFAULT_VISION_MODEL_API_KEY,
            'AGENT_MODEL_URL': DEFAULT_AGENT_MODEL_URL,
            'AGENT_MODEL_API_KEY': DEFAULT_AGENT_MODEL_API_KEY,
            'REALTIME_MODEL_URL': DEFAULT_REALTIME_MODEL_URL,
            'REALTIME_MODEL_API_KEY': DEFAULT_REALTIME_MODEL_API_KEY,
            'TTS_MODEL_URL': DEFAULT_TTS_MODEL_URL,
            'TTS_MODEL_API_KEY': DEFAULT_TTS_MODEL_API_KEY,
            'OPENCLAW_URL': "http://127.0.0.1:8088",
            'OPENCLAW_TIMEOUT': 300.0,
            'OPENCLAW_DEFAULT_SENDER_ID': "neko_user",
        }

        core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        try:
            with open(str(self.get_config_path('core_config.json')), 'r', encoding='utf-8') as f:
                file_data = json.load(f)
            if isinstance(file_data, dict):
                core_cfg.update(file_data)
                # Template default assistApi='qwen' fills a value never saved in the file,
                # hiding absence from the following default-assist-to-free-when-core-is-free logic.
                # Restore following only when the file explicitly selects core=free and has never saved assistApi;
                # preserve template defaults for other missing-value cases.
                if 'assistApi' not in file_data and file_data.get('coreApi') == 'free':
                    core_cfg['assistApi'] = 'free'
            else:
                logger.warning("core_config.json 格式异常，使用默认配置。")

        except FileNotFoundError:
            logger.info("未找到 core_config.json，使用默认配置。")
        except Exception as e:
            logger.error("Error parsing Core API Key: %s", e)
        finally:
            if not isinstance(core_cfg, dict):
                core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])
        config['RESOLVED_PROVIDER_URLS'] = (
            dict(core_cfg.get('resolvedProviderUrls'))
            if isinstance(core_cfg.get('resolvedProviderUrls'), dict)
            else {}
        )

        # API keys: fall back to CORE_API_KEY only for providers matching coreApi/assistApi.
        if core_cfg.get('coreApiKey'):
            config['CORE_API_KEY'] = core_cfg['coreApiKey']

        _core_api_provider = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        _assist_api_provider = core_cfg.get('assistApi')
        if not _assist_api_provider:
            _assist_api_provider = 'free' if _core_api_provider == 'free' else 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}
        _core_key_fallback = config['CORE_API_KEY'] if config['CORE_API_KEY'] != 'free-access' else ''

        def _fb(provider: str) -> str:
            return _core_key_fallback if provider in _fallback_providers else ''

        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or _fb('qwen')
        config['ASSIST_API_KEY_QWEN_INTL'] = core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl')
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or _fb('openai')
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or _fb('glm')
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or _fb('step')
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or _fb('silicon')
        config['ASSIST_API_KEY_GEMINI'] = core_cfg.get('assistApiKeyGemini', '') or _fb('gemini')
        config['ASSIST_API_KEY_KIMI'] = core_cfg.get('assistApiKeyKimi', '') or _fb('kimi')
        config['ASSIST_API_KEY_KIMI_CODE'] = core_cfg.get('assistApiKeyKimiCode', '') or _fb('kimi_code')
        config['ASSIST_API_KEY_DEEPSEEK'] = core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek')
        config['ASSIST_API_KEY_DOUBAO'] = core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao')
        config['ASSIST_API_KEY_DOUBAO_TTS'] = core_cfg.get('assistApiKeyDoubaoTts', '')
        # MiniMax / MiMo are assist-only TTS providers; coreApiKey is not necessarily compatible.
        # Do not fall back, which would put invalid keys in TTS credentials and cause 401,
        # hiding the real missing-TTS-provider-key guidance.
        config['ASSIST_API_KEY_MINIMAX'] = core_cfg.get('assistApiKeyMinimax', '')
        config['ASSIST_API_KEY_MINIMAX_INTL'] = core_cfg.get('assistApiKeyMinimaxIntl', '')
        config['ASSIST_API_KEY_MIMO'] = core_cfg.get('assistApiKeyMimo', '')
        config['ASSIST_API_KEY_MIMO_TOKEN_PLAN'] = core_cfg.get('assistApiKeyMimoTokenPlan', '')
        config['useMimoTokenPlan'] = _as_bool(core_cfg.get('useMimoTokenPlan', False))
        config['ASSIST_API_KEY_ELEVENLABS'] = core_cfg.get('assistApiKeyElevenlabs', '')
        config['ASSIST_API_KEY_GROK'] = core_cfg.get('assistApiKeyGrok', '') or _fb('grok')
        config['ASSIST_API_KEY_CLAUDE'] = core_cfg.get('assistApiKeyClaude', '') or _fb('claude')
        config['ASSIST_API_KEY_OPENROUTER'] = core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter')
        config['ASSIST_API_KEY_ORCAROUTER'] = core_cfg.get('assistApiKeyOrcarouter', '') or _fb('orcarouter')
        # Requesty and Opper are assist-only; a realtime core key cannot
        # authenticate their gateways and must not be sent to them.
        config['ASSIST_API_KEY_REQUESTY'] = core_cfg.get('assistApiKeyRequesty', '')
        config['ASSIST_API_KEY_OPPER'] = core_cfg.get('assistApiKeyOpper', '')

        if core_cfg.get('mcpToken'):
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']

        openclaw_url = core_cfg.get('openclawUrl')
        # Normalize 8089->8088 only in memory, never on disk: get_core_config commonly runs in to_thread,
        # so reader-side writes race with /core_api saves. Persist through startup's
        # migrate_openclaw_url_port, before concurrent writers exist.
        _migrated_openclaw_url = self._migrated_openclaw_url(openclaw_url)
        if _migrated_openclaw_url:
            core_cfg['openclawUrl'] = _migrated_openclaw_url
            openclaw_url = _migrated_openclaw_url
        if isinstance(openclaw_url, str) and openclaw_url.strip():
            config['OPENCLAW_URL'] = openclaw_url.strip()
        try:
            openclaw_timeout = core_cfg.get('openclawTimeout', config['OPENCLAW_TIMEOUT'])
            openclaw_timeout = float(openclaw_timeout)
            if not math.isfinite(openclaw_timeout) or openclaw_timeout <= 0:
                raise ValueError("openclawTimeout must be a positive finite number")
            config['OPENCLAW_TIMEOUT'] = openclaw_timeout
        except (TypeError, ValueError):
            config['OPENCLAW_TIMEOUT'] = 300.0
        openclaw_sender = core_cfg.get('openclawDefaultSenderId')
        if isinstance(openclaw_sender, str) and openclaw_sender.strip():
            config['OPENCLAW_DEFAULT_SENDER_ID'] = openclaw_sender.strip()

        core_api_profiles = get_core_api_profiles()
        assist_api_profiles = get_assist_api_profiles()
        assist_api_key_fields = get_assist_api_key_fields()
        # Provider -> **original** ledger field names in core_config.json (e.g. assistApiKeyQwen).
        # Distinguishes actual persisted ledger entries from ASSIST_API_KEY_* values merely derived by _fb()
        # from CORE_API_KEY; the latter must not override keys saved in slots.
        try:
            from utils.api_config_loader import get_config as _get_api_config
            _raw_api_config = _get_api_config()
            _api_key_registry = _raw_api_config.get('api_key_registry', {}) or {}
            _raw_assist_providers = _raw_api_config.get('assist_api_providers', {}) or {}
        except Exception:
            _api_key_registry = {}
            _raw_assist_providers = {}

        def _uses_fixed_models(provider_key: str) -> bool:
            # Free and fixed_model providers (e.g. Kimi Code) accept only their own models; never send user-saved model IDs.
            if provider_key == 'free':
                return True
            raw_profile = _raw_assist_providers.get(provider_key)
            return isinstance(raw_profile, dict) and (
                _as_bool(raw_profile.get('fixed_model', False))
                or _as_bool(raw_profile.get('is_free_version', False))
            )

        assist_api_key_raw_fields = {
            _pk: _entry.get('config_field', '')
            for _pk, _entry in _api_key_registry.items()
            if isinstance(_entry, dict) and _entry.get('config_field')
        }

        # Core API profile
        core_api_value = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        config['CORE_API_TYPE'] = core_api_value
        core_profile = core_api_profiles.get(core_api_value)
        if core_profile:
            config.update(core_profile)
            resolved_core_url = self._get_saved_provider_url(
                core_cfg, 'core', core_api_value, core_profile, 'CORE_URL', 'CORE_URLS'
            )
            if resolved_core_url:
                config['CORE_URL'] = resolved_core_url

        # Assist API profile
        # Always honor explicitly selected assistApi, even when coreApi=free. Users may combine
        # free realtime (core=free) + paid text/Agent (assist=qwen, etc.); free realtime
        # and paid assist are independent paths with no reason to pin the latter to free.
        # Only when assist is not explicitly selected, inherit coreApi preference: core=free defaults to free,
        # others to qwen.
        assist_api_value = core_cfg.get('assistApi')
        if not assist_api_value:
            assist_api_value = 'free' if core_api_value == 'free' else 'qwen'

        config['assistApi'] = assist_api_value

        assist_profile = assist_api_profiles.get(assist_api_value)
        if not assist_profile and assist_api_value != 'qwen':
            logger.warning("未知的 assistApi '%s'，回退到 qwen。", assist_api_value)
            assist_api_value = 'qwen'
            config['assistApi'] = assist_api_value
            assist_profile = assist_api_profiles.get(assist_api_value)

        if assist_profile:
            config.update(assist_profile)
            resolved_assist_url = self._get_saved_provider_url(
                core_cfg, 'assist', assist_api_value, assist_profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
            )
            if resolved_assist_url:
                config['OPENROUTER_URL'] = resolved_assist_url
        use_mimo_token_plan = (
            assist_api_value == 'mimo'
            and _as_bool(core_cfg.get('useMimoTokenPlan', False))
        )
        if use_mimo_token_plan:
            token_plan_urls = config.get('MIMO_TOKEN_PLAN_OPENROUTER_URLS')
            if not isinstance(token_plan_urls, list):
                token_plan_urls = []
            token_plan_profile = {
                'OPENROUTER_URL': config.get(
                    'MIMO_TOKEN_PLAN_OPENROUTER_URL',
                    'https://token-plan-cn.xiaomimimo.com/v1',
                ),
                'OPENROUTER_URLS': token_plan_urls or [
                    'https://token-plan-cn.xiaomimimo.com/v1',
                    'https://token-plan-sgp.xiaomimimo.com/v1',
                    'https://token-plan-ams.xiaomimimo.com/v1',
                ],
            }
            token_plan_url = self._get_saved_provider_url(
                core_cfg,
                'assist',
                'mimo_token_plan',
                token_plan_profile,
                'OPENROUTER_URL',
                'OPENROUTER_URLS',
            )
            config['OPENROUTER_URL'] = token_plan_url or token_plan_profile['OPENROUTER_URL']
        # Agent API defaults to assist API's agent_model, falling back to VISION_MODEL when absent.
        config['AGENT_MODEL'] = config.get('AGENT_MODEL') or config.get('VISION_MODEL', '')
        config['AGENT_MODEL_URL'] = config.get('AGENT_MODEL_URL') or config.get('VISION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
        config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        key_field = (
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN'
            if use_mimo_token_plan
            else assist_api_key_fields.get(assist_api_value)
        )
        is_dedicated_key_assist = assist_api_value in ('requesty', 'opper')
        derived_key = ''
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key and not is_dedicated_key_assist:
                config['AUDIO_API_KEY'] = derived_key

        # AUDIO_API_KEY also backs CosyVoice and saved voice buckets; preserve
        # its legacy fallback independently of the text router credential.
        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = _core_key_fallback
        if derived_key or is_dedicated_key_assist:
            # A missing Requesty or Opper dedicated key must stay empty for text/Agent.
            config['OPENROUTER_API_KEY'] = derived_key
        elif not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = _core_key_fallback

        # Agent API key fallback: follow assist API key unless explicitly configured.
        if not config.get('AGENT_MODEL_API_KEY'):
            config['AGENT_MODEL_API_KEY'] = config.get('OPENROUTER_API_KEY', '')

        # Map custom API configuration using uppercase underscore internal keys, retaining existing defaults for omitted fields.
        enable_custom_api = core_cfg.get('enableCustomApi', False)
        config['ENABLE_CUSTOM_API'] = enable_custom_api

        # GPT-SoVITS enablement uses ttsModelProvider as the single source of truth (see
        # docs/design/tts-voice-source-unification.md §3/§4). Derive it at this choke point;
        # all 13 downstream readers (core.py routing / URL resolution and TTS_VOICE_ID override here /
        # get_model_api_config is_custom self-healing / routers), plus workers'
        # _gptsovits_is_selected, continue using GPTSOVITS_ENABLED unchanged.
        # Derivation: an **explicit** ttsModelProvider selection is authoritative: enable only for
        # gptsovits, disable for others (vllm_omni/mimo/custom…), ignoring legacy gptsovitsEnabled.
        # Only absent explicit selection (missing/empty provider, or follow_assist/follow_core sentinels
        # meaning follow assist/core, the default unselected dropdown values) falls back to the old toggle,
        # preserving pre-#1830 users who enabled GSV by checkbox while retaining default follow_*.
        # Never treat follow_* as explicit providers, or existing GSV users (gptsovitsEnabled=true +
        # ttsModelProvider='follow_assist') would be wrongly disabled after upgrade (Codex PR#1850 P1).
        # Deliberately avoid legacy-flag OR dropdown: the frontend retired gptsovitsEnabled writes, so incremental
        # merges make old flags sticky. Explicit switching follows the dropdown; switching to follow_* is lazily cleared
        # to False by config_router's save choke point (see its comment).
        _tts_model_provider = str(core_cfg.get('ttsModelProvider', '') or '').strip()
        if _tts_model_provider in ('', 'follow_assist', 'follow_core'):
            config['GPTSOVITS_ENABLED'] = _as_bool(core_cfg.get('gptsovitsEnabled', False))
        else:
            config['GPTSOVITS_ENABLED'] = (_tts_model_provider == 'gptsovits')

        config['ELEVENLABS_API_KEY'] = core_cfg.get('assistApiKeyElevenlabs', '')
        config['TTS_PROVIDER'] = core_cfg.get('ttsProvider', '')

        # Include raw frontend vLLM-Omni TTS fields in the core_config snapshot so
        # core.py can determine external TTS enablement and derive reuse keys matching actual worker arguments.
        # Exclude credential ttsModelApiKey; tts_client.py still reads it from persisted
        # configuration, avoiding expanding sensitive fields in general configuration snapshots.
        for _model_provider_prefix in (
            'conversation', 'summary', 'gameMain', 'gameSummary', 'correction',
            'emotion', 'vision', 'agent', 'omni', 'tts',
        ):
            _raw_provider = str(
                core_cfg.get(f'{_model_provider_prefix}ModelProvider', '') or ''
            )
            config[f'{_model_provider_prefix}ModelProvider'] = (
                self._normalize_realtime_provider(_raw_provider)
                if _model_provider_prefix == 'omni'
                else _raw_provider
            )
        # **Raw persisted** omniModelUrl, not resolved REALTIME_MODEL_URL: distinguishes
        # an actually hand-entered realtime endpoint from a provider-profile default.
        # Legacy configurations could hand-enter URLs before omniModelProvider existed; keep treating those as local.
        config['omniModelUrl'] = str(core_cfg.get('omniModelUrl', '') or '')
        config['ttsModelUrl'] = str(core_cfg.get('ttsModelUrl', '') or '')
        config['ttsModelId'] = str(core_cfg.get('ttsModelId', '') or '')
        config['ttsVoiceId'] = str(core_cfg.get('ttsVoiceId', '') or '')

        # Disable TTS.
        _raw_disable_tts = core_cfg.get('disableTts', False)
        if isinstance(_raw_disable_tts, bool):
            config['DISABLE_TTS'] = _raw_disable_tts
        elif isinstance(_raw_disable_tts, str):
            config['DISABLE_TTS'] = _raw_disable_tts.lower() in ('true', '1', 'yes', 'on')
        else:
            config['DISABLE_TTS'] = False

        # Text-reply length guard ceiling (tiktoken o200k_base tokens; reroll above limit;
        # after rerolls exhaust, finalize after truncating at the last sentence-ending punctuation).
        try:
            config['TEXT_GUARD_MAX_LENGTH'] = int(core_cfg.get('textGuardMaxLength', 300))
            if config['TEXT_GUARD_MAX_LENGTH'] <= 0:
                config['TEXT_GUARD_MAX_LENGTH'] = 300
        except (TypeError, ValueError):
            config['TEXT_GUARD_MAX_LENGTH'] = 300
        
        # GPT-SoVITS is a local TTS runtime independent of enableCustomApi. Saved
        # ttsModelUrl is the GSV server URL and must not be overwritten by follow_core/follow_assist
        # LLM URLs. Empty values default at runtime to 127.0.0.1 without configuration writes.
        if config['GPTSOVITS_ENABLED']:
            config['TTS_MODEL_URL'] = normalize_gsv_api_url(core_cfg.get('ttsModelUrl'))

        # Allow model-field overrides only when custom API is enabled.
        if enable_custom_api:
            assist_model_defaults = {
                key: config.get(key, '') for key in self._SLOT_PROFILE_MODEL_KEYS.values()
            }
            assist_model_defaults['SUMMARY_MODEL'] = (
                assist_profile.get('SUMMARY_MODEL') or assist_profile.get('CONVERSATION_MODEL', '')
            )
            # URL / Model ID fields: empty values fall back to existing configuration.
            # API-key override policy depends on selected provider:
            #   - follow_core / follow_assist / '' (legacy absent field): retain values derived above
            #   - specific provider or 'custom': allow overrides, including empty strings for local providers needing no key
            def _resolve_follow_model_url(prefix: str, provider: str) -> str:
                """Recompute follow_* URLs for the current provider, avoiding stale regions saved historically."""
                if provider == 'follow_assist':
                    return config.get('OPENROUTER_URL', '')
                if provider == 'follow_conversation':
                    return config.get('CONVERSATION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider == 'follow_summary':
                    return config.get('SUMMARY_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider != 'follow_core':
                    return ''

                if prefix == 'omni':
                    return config.get('CORE_URL', '')

                follow_core_profile = assist_api_profiles.get(core_api_value)
                if isinstance(follow_core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'assist',
                        core_api_value,
                        follow_core_profile,
                        'OPENROUTER_URL',
                        'OPENROUTER_URLS',
                    )
                    return resolved_url or follow_core_profile.get('OPENROUTER_URL', '')

                if isinstance(core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'core',
                        core_api_value,
                        core_profile,
                        'CORE_URL',
                        'CORE_URLS',
                    )
                    return resolved_url or core_profile.get('CORE_URL', '')
                return ''

            def _resolve_follow_model_id(prefix: str, provider: str) -> str:
                """Default model of the provider a follow_* slot points at, '' to keep the current value."""
                profile_key = self._SLOT_PROFILE_MODEL_KEYS.get(prefix)
                if not profile_key:
                    return ''
                is_game_slot = prefix in ('gameMain', 'gameSummary')
                if provider == 'follow_core':
                    # URL and key now use the core provider, so the model must too; retaining the snapshot's
                    # assist model would send provider A's model name to provider B's endpoint.
                    follow_core_profile = assist_api_profiles.get(core_api_value)
                    if isinstance(follow_core_profile, dict):
                        return (follow_core_profile.get(profile_key, '')
                                or (follow_core_profile.get('VISION_MODEL', '') if prefix == 'agent' else '')
                                or follow_core_profile.get('CONVERSATION_MODEL', '')
                                or config.get('CORE_MODEL', ''))
                    return config.get('CORE_MODEL', '')
                if provider == 'follow_assist' and is_game_slot:
                    return assist_model_defaults.get(profile_key, '')
                return ''

            def _resolve_named_provider_model_id(prefix: str, provider: str, slot_url: str) -> str:
                """Tier default of a named provider slot, '' when the slot has no such default."""
                profile_key = self._SLOT_PROFILE_MODEL_KEYS.get(prefix)
                if not profile_key or not provider or provider == 'custom' or provider.startswith('follow_'):
                    return ''
                named_profile = assist_api_profiles.get(provider)
                if not isinstance(named_profile, dict):
                    return ''
                candidates = self._provider_url_candidates(
                    named_profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
                )
                if not slot_url or not any(same_endpoint(slot_url, c) for c in candidates):
                    return ''
                return str(named_profile.get(profile_key)
                           or (named_profile.get('VISION_MODEL') if prefix == 'agent' else '')
                           or named_profile.get('CONVERSATION_MODEL') or '').strip()

            _custom_api_fields = [
                # (frontend field prefix, model config key, URL config key, API-key config key)
                ('conversation', 'CONVERSATION_MODEL', 'CONVERSATION_MODEL_URL', 'CONVERSATION_MODEL_API_KEY'),
                ('summary',      'SUMMARY_MODEL',      'SUMMARY_MODEL_URL',      'SUMMARY_MODEL_API_KEY'),
                ('gameMain',     'GAME_MAIN_MODEL',    'GAME_MAIN_MODEL_URL',    'GAME_MAIN_MODEL_API_KEY'),
                ('gameSummary',  'GAME_SUMMARY_MODEL', 'GAME_SUMMARY_MODEL_URL', 'GAME_SUMMARY_MODEL_API_KEY'),
                ('correction',   'CORRECTION_MODEL',    'CORRECTION_MODEL_URL',   'CORRECTION_MODEL_API_KEY'),
                ('emotion',      'EMOTION_MODEL',       'EMOTION_MODEL_URL',      'EMOTION_MODEL_API_KEY'),
                ('vision',       'VISION_MODEL',        'VISION_MODEL_URL',       'VISION_MODEL_API_KEY'),
                ('agent',        'AGENT_MODEL',         'AGENT_MODEL_URL',        'AGENT_MODEL_API_KEY'),
                ('omni',         'REALTIME_MODEL',      'REALTIME_MODEL_URL',     'REALTIME_MODEL_API_KEY'),
                ('tts',          'TTS_MODEL',           'TTS_MODEL_URL',          'TTS_MODEL_API_KEY'),
            ]
            for prefix, model_key, url_key, apikey_key in _custom_api_fields:
                # Read normalized snapshot values above rather than raw core_cfg: when omni selects a provider
                # without realtime support, the snapshot already falls back to follow_core. URL/model/key
                # overrides must follow that same path, avoiding leftover autofilled values.
                provider = config.get(f'{prefix}ModelProvider', '')
                # follow_core / follow_assist URLs are readonly frontend-linked hints autofilled by
                # static/js/api_key_settings.js: onCustomModelProviderChange, not evidence
                # of user-selected custom deployments. Only omni/tts are affected:
                #   - omni: get_model_api_config sees nonempty REALTIME_MODEL + _URL ->
                #     forces api_type='local' (TODO unimplemented) -> core_api_type='local' ->
                #     TTS dispatch reaches dummy_tts_worker -> silence
                #   - tts: linked values pollute TTS_MODEL_URL and send tts_custom to the wrong provider
                # Other model types (conversation/summary/correction/emotion/vision/agent)
                # use chat-completion REST without a local branch; skipping URLs would alter their
                # follow_* routing (see PR #1084 review thread), so skip only for omni/tts.
                # Note: under follow_*, omni/tts cannot rely on get_model_api_config fallback
                # to read user-entered modelId (fallback uses CORE_MODEL, not REALTIME_MODEL/TTS_MODEL),
                # so these special prefixes retain existing follow resolution.
                is_follow = provider in ('follow_core', 'follow_assist', 'follow_conversation', 'follow_summary')
                # With GSV enabled, ttsModelUrl is the GPT-SoVITS server URL, not a follow_*-linked
                # LLM URL. Even with default ttsModelProvider=follow_assist,
                # preserve the GSV URL first, or conversation TTS would connect to assist LLM endpoints.
                gsv_enabled_for_url = config['GPTSOVITS_ENABLED']
                gsv_tts_url_override = prefix == 'tts' and gsv_enabled_for_url
                skip_url_for_follow = (
                    is_follow
                    and prefix in ('omni', 'tts')
                    and not gsv_tts_url_override
                )

                # URL: empty falls back to existing configuration; skip omni/tts follow_*.
                cfg_url = core_cfg.get(f'{prefix}ModelUrl')
                if gsv_tts_url_override:
                    config[url_key] = normalize_gsv_api_url(cfg_url or config.get(url_key))
                elif not skip_url_for_follow:
                    if is_follow:
                        followed_url = _resolve_follow_model_url(prefix, provider)
                        if followed_url:
                            config[url_key] = followed_url
                    else:
                        if cfg_url is not None:
                            config[url_key] = cfg_url or config.get(url_key, '')

                # Model ID: empty falls back to existing configuration.
                cfg_model = core_cfg.get(f'{prefix}ModelId')
                named_model = _resolve_named_provider_model_id(
                    prefix, provider, str(config.get(url_key) or '').strip()
                )
                if provider == 'follow_conversation':
                    config[model_key] = config.get('CONVERSATION_MODEL', '')
                elif provider == 'follow_summary':
                    config[model_key] = config.get('SUMMARY_MODEL', '')
                elif provider in ('follow_core', 'follow_assist'):
                    followed_provider = core_api_value if provider == 'follow_core' else assist_api_value
                    if (
                        not _uses_fixed_models(followed_provider)
                        and
                        prefix not in ('gameMain', 'gameSummary', 'omni', 'tts')
                        and isinstance(cfg_model, str)
                        and cfg_model.strip()
                    ):
                        config[model_key] = cfg_model.strip()
                    else:
                        followed_model = _resolve_follow_model_id(prefix, provider)
                        if followed_model:
                            config[model_key] = followed_model
                elif named_model and (
                    _uses_fixed_models(provider)
                    or not (isinstance(cfg_model, str) and cfg_model.strip())
                ):
                    # Empty named-provider slots use that provider's tier defaults; custom / legacy '' have no
                    # defaults and retain the fallback below.
                    config[model_key] = named_model
                elif cfg_model is not None:
                    config[model_key] = cfg_model or config.get(model_key, '')

                # API-key handling:
                #   follow_core -> derive from core API key
                #   follow_assist -> derive from assist API key (OPENROUTER_API_KEY includes assist->core fallback)
                #   named provider -> resolve API ledger ASSIST_API_KEY_*; see _book_key below
                #   custom / legacy '' -> use stored value, including empty strings for local providers needing no key
                #
                # Skip derivation for GSV enabled + prefix='tts' + default ttsModelProvider='follow_*':
                # derivation would set TTS_MODEL_API_KEY to OPENROUTER_API_KEY / CORE_API_KEY (LLM
                # keys, possibly Gemini / DeepSeek), then get_model_api_config('tts_custom')'s
                # is_gsv_url branch returns the unrelated key unchanged; get_tts_api_key('cosyvoice')
                # receives a wrong key and CosyVoice clone authentication fails. Skipping retains persisted TTS_MODEL_API_KEY
                # (usually empty because GSV users need not fill it), allowing the downstream is_gsv_url branch's
                # ASSIST_API_KEY_QWEN fallback to take over.
                skip_key_for_follow_gsv = (
                    is_follow
                    and prefix == 'tts'
                    and gsv_enabled_for_url
                )
                if provider == 'follow_core':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('CORE_API_KEY', '')
                elif provider == 'follow_assist':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('OPENROUTER_API_KEY', '')
                elif provider == 'follow_conversation':
                    config[apikey_key] = config.get('CONVERSATION_MODEL_API_KEY', '')
                elif provider == 'follow_summary':
                    config[apikey_key] = config.get('SUMMARY_MODEL_API_KEY', '')
                else:
                    # Named-provider slots: the API ledger (ASSIST_API_KEY_*) is authoritative for keys,
                    # not slot fields. Selecting a provider copies ledger values into a readonly slot input,
                    # （api_key_settings.js: onCustomModelProviderChange → setKeyReadonly），
                    # but GET /core_api masks the ledger with a sentinel (#2559), so the copied value is a sentinel,
                    # rejected on save by apply_core_config_secret_update. The slot consequently remains
                    # empty forever and runtime requests use empty keys. Read the ledger directly here:
                    #   - existing broken configurations heal without resaving;
                    #   - matches the readonly UI meaning of keys autofilled from the API ledger;
                    #   - preserves the safety invariant that sentinels never persist.
                    # Providers absent from the ledger (custom / legacy '' / TTS-registry-only providers such as
                    # gptsovits) fall back to stored slot values, preserving existing paths.
                    # Priority: explicitly stored ledger key > existing slot value > derived ledger value.
                    #
                    # The middle level is essential: _fb() derives ASSIST_API_KEY_* from CORE_API_KEY for
                    # providers matching coreApi/assistApi. That is a default, not something the user
                    # saved; overriding legacy slot keys with it would break working configurations.
                    #
                    # Convert all three sources to strings before strip: users can edit core_config.json manually,
                    # so fields may contain any JSON type. Direct .strip() could make all of get_core_config()
                    # raise AttributeError, crashing startup rather than degrading one slot.
                    _raw_cfg_key = core_cfg.get(f'{prefix}ModelApiKey')
                    cfg_key = (
                        str(_raw_cfg_key).strip() if _raw_cfg_key is not None else None
                    )
                    # Doubao speech exception: although present in the ledger mapping, slot fields are authoritative;
                    # voice_storage.get_tts_api_key('doubao_tts') prefers the slot, with ledger fallback,
                    # and POST arbitration deliberately preserves legacy keys stored only in ttsModelApiKey.
                    # Preferring the ledger here would create opposite resolution paths for identical credentials.
                    # Never redirect the entire TTS slot through the ledger. Its dropdown includes pseudo-options recognized by
                    # the registry but whose dispatch never reads ttsModelProvider (minimax / elevenlabs /
                    # cosyvoice…); workers still depend on core API. Resolving those providers' genuine
                    # keys into TTS_MODEL_API_KEY here would send them to the
                    # core provider's TTS endpoint, another cross-provider credential disclosure.
                    # TTS credentials are already governed by slot fields and provider-specific resolution
                    # (voice_storage.get_tts_api_key / workers reading raw ttsModelApiKey directly);
                    # preserve those paths.
                    _uses_key_book = bool(provider) and prefix != 'tts'
                    _raw_book_field = (
                        assist_api_key_raw_fields.get(provider, '') if _uses_key_book else ''
                    )
                    _explicit_book = (
                        str(core_cfg.get(_raw_book_field) or '').strip() if _raw_book_field else ''
                    )
                    _book_field = assist_api_key_fields.get(provider) if _uses_key_book else ''
                    _derived_book = (
                        str(config.get(_book_field) or '').strip() if _book_field else ''
                    )
                    # A provider's ledger key belongs only with its own endpoint. UI-generated configurations
                    # guarantee matching origins because selecting providers autofills readonly URLs, but
                    # legacy/imported/manually edited configurations may retain another provider's URL; resolving a genuine
                    # key then sends provider A's credentials to provider B.
                    # Empty URLs pose no risk: the incomplete custom triple causes a full downstream fallback.
                    # Both sources contain the **same** key (_derived_book merely adds
                    # ASSIST_API_KEY_* derivation), so guard both; guarding only the explicit source
                    # leaks through derivation.
                    _slot_url = str(config.get(url_key) or '').strip()
                    if (_explicit_book or _derived_book) and _slot_url:
                        _profile = assist_api_profiles.get(provider)
                        _candidates = (
                            self._provider_url_candidates(
                                _profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
                            )
                            if isinstance(_profile, dict)
                            else []
                        )
                        # Reuse utils.http.url.same_endpoint for origin matching rather than reinventing it:
                        # vision-slot credential inheritance already refined it over six rounds (trailing slashes / path case /
                        # userinfo / malformed ports / default ports / malformed IPv6). A local
                        # lowercase-and-strip-slash implementation repeats those pitfalls; indeed the first version
                        # equated /V1 with /v1 and failed to recognize explicit :443.
                        if not any(same_endpoint(_slot_url, c) for c in _candidates):
                            # Endpoint belongs elsewhere: withhold this provider's key, falling back to existing slot values.
                            # Missing/old credentials yield diagnosable 401s instead of silent disclosure to the wrong provider.
                            _explicit_book = ''
                            _derived_book = ''
                    if _explicit_book:
                        config[apikey_key] = _explicit_book
                    elif cfg_key:
                        config[apikey_key] = cfg_key
                    elif _derived_book:
                        config[apikey_key] = _derived_book
                    elif cfg_key is not None:
                        config[apikey_key] = cfg_key

            # TTS Voice ID serves as fallback for character voice_id.
            if core_cfg.get('ttsVoiceId') is not None:
                config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        if config['GPTSOVITS_ENABLED'] and core_cfg.get('ttsVoiceId') is not None:
            config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        # Share one region decision across the whole snapshot: unresolved decisions are recomputed every time, so Steam
        # initialization mid-loop could leave early URLs on lanlan.tech and later ones on lanlan.app,
        # splitting one core_config snapshot across regions.
        #
        # Before moving this outside the loop, confirm some URL **actually needs it**: region selection starts GeoIP
        # probing, and the free-route gate originally lived in _adjust_free_api_url's initial early return
        # (invariant #2). Unconditional selection exposes custom-API users' IPs to third parties too.
        needs_region = self._config_needs_region(config)
        # Switching at runtime from custom/paid to free routing: probing may be asleep in a failed attempt's backoff (up to
        # 600s), with no wake point here; only startup warmup invokes _kick_ip_probe. Starting a session immediately after switching
        # freezes routing for its lifetime, pinning it to the mainland fallback for up to 600s.
        # Kick only on the unselected -> selected edge: every configuration read waking probes defeats backoff and causes
        # ip-api.com request storms when blocked. The process's first free-route read is also an edge, but the probe
        # thread has not started (_ensure_ip_probe_started lives in _check_non_mainland below);
        # _kick_ip_probe sees thread is None and no-ops, preserving the first backoff.
        from utils.config_manager import ConfigManager
        if needs_region != ConfigManager._free_route_selected:
            ConfigManager._free_route_selected = needs_region
            if needs_region:
                self._kick_ip_probe()
        snapshot_non_mainland = False
        if needs_region:
            try:
                snapshot_non_mainland = self._check_non_mainland()
            except Exception:
                # _check_non_mainland already has layered fallbacks; exceptions reaching here are unexpected.
                # Swallowing them silently pins overseas snapshots to mainland routing without logs, leaving only
                # reports of occasional whole-session slowness. Record the failure before falling back.
                logger.warning(
                    "[GeoIP] 区域判定抛出异常，本次快照按大陆线路组装", exc_info=True
                )
                snapshot_non_mainland = False
        for key, value in config.items():
            if key.endswith('_URL') and isinstance(value, str):
                config[key] = self._adjust_free_api_url(
                    value, True,
                    # Exempt AGENT_MODEL_URL from region rewriting: free-agent-model deliberately pins the CN
                    # lanlan.tech text endpoint (see _normalize_agent_url, an identity function
                    # **unable to undo** this loop's rewrite, so exemption must happen here). Pass
                    # non_mainland=False rather than skipping the call entirely: livestream-prefix derivation
                    # precedes region rewriting in _adjust_free_api_url. Exempt region selection,
                    # not local forwarding.
                    non_mainland=False if key == 'AGENT_MODEL_URL' else snapshot_non_mainland,
                )

        # Agent model always uses international API regardless of region
        if isinstance(config.get('AGENT_MODEL_URL'), str):
            config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        # Keep a raw, independent image slot: no chat fallback or implicit generation.
        config["IMAGE_GENERATION_CONFIG"] = {
            key: core_cfg.get(key, "")
            for key in ("imageModelProvider", "imageModelUrl", "imageModelId",
                        "imageModelApiKey", "assistApiKeyOpenai",
                        "assistApiKeyQwen", "assistApiKeyQwenIntl")
        }
        # Reuse the same provider-gated legacy key resolution as the Key Book.
        # Never introduce a cross-provider fallback for image generation.
        for field, resolved_field in (
            ("assistApiKeyOpenai", "ASSIST_API_KEY_OPENAI"),
            ("assistApiKeyQwen", "ASSIST_API_KEY_QWEN"),
            ("assistApiKeyQwenIntl", "ASSIST_API_KEY_QWEN_INTL"),
        ):
            config["IMAGE_GENERATION_CONFIG"][field] = config.get(resolved_field, "")
        return config

    def get_model_api_config(self, model_type: str, *, _core_config: dict | None = None) -> dict:
        """
        Get the API config for the given model type (automatically handling custom API priority)

        Args:
            model_type: model type, one of:
                - 'summary': summary model (falls back to assist API)
                - 'correction': correction model (falls back to assist API)
                - 'emotion': emotion analysis model (falls back to assist API)
                - 'vision': vision model (falls back to assist API)
                - 'realtime': realtime speech model (falls back to core API)
                - 'tts_default': default TTS (falls back to core API, used by OmniOfflineClient)
                - 'tts_custom': custom TTS (falls back to assist API, used for voice_id scenarios)
            _core_config: optional pre-read get_core_config snapshot, so a caller that already
                holds one does not pay a second core_config.json read. Private: async callers
                reach it through aget_model_api_config(core_config=...).

        Returns:
            dict: config containing:
                - 'model': model name
                - 'api_key': API key
                - 'base_url': API endpoint URL
                - 'is_custom': whether a custom API config is used
        """
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites (incl. the nested provider-type
        # resolvers and the tts_custom Qwen-profile fallback below).
        from utils.config_manager import get_assist_api_profiles, get_core_api_profiles

        core_config = self.get_core_config() if _core_config is None else _core_config
        enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)
        if model_type == "image":
            from utils.image_generation.config import resolve_image_config
            image = resolve_image_config(
                core_config.get("IMAGE_GENERATION_CONFIG", {}),
                enabled=enable_custom_api,
            )
            if image is None:
                return {"enabled": False}
            return {
                "enabled": True, "provider": image.provider,
                "protocol": image.protocol, "base_url": image.base_url,
                "model": image.model, "api_key": image.api_key, "is_custom": True,
            }

        # With GPT-SoVITS enabled, tts_custom counts as custom API: selecting GSV means enabling custom TTS in
        # product terms, but frontend api_key_settings.js does not also select
        # ENABLE_CUSTOM_API. Heal this backend state so the common GSV-only selection does not
        # produce is_custom=False and disable the entire GSV path (dispatcher /
        # check_custom_tts_voice_allowed / /custom_tts_voices).
        # Extend only tts_custom, leaving other slot-toggle behavior unchanged.
        gsv_enabled_for_tts = (
            model_type == 'tts_custom'
            and core_config.get('GPTSOVITS_ENABLED', False)
        )
        treat_as_custom = enable_custom_api or gsv_enabled_for_tts

        # Map model types to configuration fields.
        # fallback_type: 'assist' = assist API, 'core' = core API.
        model_type_mapping = {
            'conversation': {
                'custom_model': 'CONVERSATION_MODEL',
                'custom_url': 'CONVERSATION_MODEL_URL',
                'custom_key': 'CONVERSATION_MODEL_API_KEY',
                'default_model': 'CONVERSATION_MODEL',
                'fallback_type': 'assist',
            },
            'summary': {
                'custom_model': 'SUMMARY_MODEL',
                'custom_url': 'SUMMARY_MODEL_URL',
                'custom_key': 'SUMMARY_MODEL_API_KEY',
                'default_model': 'SUMMARY_MODEL',
                'fallback_type': 'assist',
            },
            'game_main': {
                'custom_model': 'GAME_MAIN_MODEL',
                'custom_url': 'GAME_MAIN_MODEL_URL',
                'custom_key': 'GAME_MAIN_MODEL_API_KEY',
                'default_model': 'GAME_MAIN_MODEL',
                'fallback_type': 'conversation',
            },
            'game_summary': {
                'custom_model': 'GAME_SUMMARY_MODEL',
                'custom_url': 'GAME_SUMMARY_MODEL_URL',
                'custom_key': 'GAME_SUMMARY_MODEL_API_KEY',
                'default_model': 'GAME_SUMMARY_MODEL',
                'fallback_type': 'summary',
            },
            'correction': {
                'custom_model': 'CORRECTION_MODEL',
                'custom_url': 'CORRECTION_MODEL_URL',
                'custom_key': 'CORRECTION_MODEL_API_KEY',
                'default_model': 'CORRECTION_MODEL',
                'fallback_type': 'assist',
            },
            'emotion': {
                'custom_model': 'EMOTION_MODEL',
                'custom_url': 'EMOTION_MODEL_URL',
                'custom_key': 'EMOTION_MODEL_API_KEY',
                'default_model': 'EMOTION_MODEL',
                'fallback_type': 'assist',
            },
            'vision': {
                'custom_model': 'VISION_MODEL',
                'custom_url': 'VISION_MODEL_URL',
                'custom_key': 'VISION_MODEL_API_KEY',
                'default_model': 'VISION_MODEL',
                'fallback_type': 'assist',
            },
            'agent': {
                'custom_model': 'AGENT_MODEL',
                'custom_url': 'AGENT_MODEL_URL',
                'custom_key': 'AGENT_MODEL_API_KEY',
                'default_model': 'AGENT_MODEL',
                'fallback_type': 'assist',
            },
            'realtime': {
                'custom_model': 'REALTIME_MODEL',
                'custom_url': 'REALTIME_MODEL_URL',
                'custom_key': 'REALTIME_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # Realtime models fall back to core API.
            },
            'tts_default': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # Default TTS falls back to core API.
            },
            'tts_custom': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'assist',  # Custom TTS falls back to assist API.
            },
        }
        
        if model_type not in model_type_mapping:
            raise ValueError(f"Unknown model_type: {model_type}. Valid types: {list(model_type_mapping.keys())}")
        
        mapping = model_type_mapping[model_type]

        def _normalize_provider_type_value(value: object) -> str:
            provider_type = str(value or 'openai_compatible').strip().lower()
            if provider_type not in ('openai_compatible', 'anthropic', 'websocket'):
                return 'openai_compatible'
            return provider_type

        def _provider_type_from_assist_key(provider_key: str) -> str:
            profile = get_assist_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return 'openai_compatible'

        def _provider_type_from_core_key(provider_key: str) -> str:
            profile = get_core_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider_key)

        def _resolved_provider_type_for_model(target_model_type: str, _seen: frozenset[str] = frozenset()) -> str:
            if target_model_type in _seen:
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            seen = _seen | frozenset((target_model_type,))
            prefix_by_type = {
                'conversation': 'conversation',
                'summary': 'summary',
                'game_main': 'gameMain',
                'game_summary': 'gameSummary',
                'correction': 'correction',
                'emotion': 'emotion',
                'vision': 'vision',
                'agent': 'agent',
                'realtime': 'omni',
                'tts_default': 'tts',
                'tts_custom': 'tts',
            }
            prefix = prefix_by_type.get(target_model_type, target_model_type)
            provider = str(core_config.get(f'{prefix}ModelProvider') or '').strip()
            if provider == 'custom':
                return 'openai_compatible'
            if provider == 'follow_conversation':
                if target_model_type == 'conversation':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('conversation', seen)
            if provider == 'follow_summary':
                if target_model_type == 'summary':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('summary', seen)
            if provider == 'follow_core':
                return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
            if provider == 'follow_assist':
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            def _fallback_provider_type() -> str:
                """Protocol of whatever this slot actually falls back to."""
                fallback_type = model_type_mapping.get(target_model_type, {}).get('fallback_type')
                if fallback_type == 'core':
                    return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
                if fallback_type == 'conversation':
                    return _resolved_provider_type_for_model('conversation', seen)
                if fallback_type == 'summary':
                    return _resolved_provider_type_for_model('summary', seen)
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))

            # Named-provider **protocols** likewise apply only when the slot URL truly belongs to that provider.
            # Incomplete slots have URLs filled from assist upstream (especially AGENT_MODEL_URL's
            # VISION -> OPENROUTER fallback chain). Choosing protocol from the dropdown then creates
            # assist URL + Anthropic protocol, precisely the mismatch Fix D removes,
            # via enabled-but-incomplete custom API instead of disabled custom API.
            #
            # Share credential criteria (same_endpoint + provider candidates): provide protocol and credentials
            # together or neither, never provider A's protocol with provider B's key.
            if provider:
                _slot_url = str(
                    core_config.get(
                        model_type_mapping.get(target_model_type, {}).get('custom_url', '')
                    ) or ''
                ).strip()
                if _slot_url:
                    _profile = get_assist_api_profiles().get(provider)
                    _candidates = (
                        self._provider_url_candidates(
                            _profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
                        )
                        if isinstance(_profile, dict)
                        else []
                    )
                    if not any(same_endpoint(_slot_url, c) for c in _candidates):
                        return _fallback_provider_type()
            if not provider:
                fallback_type = model_type_mapping.get(target_model_type, {}).get('fallback_type')
                if fallback_type == 'core':
                    return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
                if fallback_type == 'conversation':
                    return _resolved_provider_type_for_model('conversation', seen)
                if fallback_type == 'summary':
                    return _resolved_provider_type_for_model('summary', seen)
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider)

        def _resolved_realtime_api_type() -> str:
            """Wire dialect for a custom-configured realtime session.

            Only two answers are coherent here. ``local`` means an endpoint the
            user typed themselves. Everything else follows the core provider —
            including an explicitly named one.

            Returning a named provider's own identity looks like honouring the
            user's pick, but this value is not just the realtime dialect: it
            becomes the process-wide core api type, which also selects the TTS
            worker, the native-voice catalog and the audio credential. Those
            still resolve against the core provider, so a divergent pick
            produced e.g. a Qwen TTS worker holding the OpenAI core key —
            silence, plus one vendor's credential sent to another. The audio
            stack has exactly one provider identity; a per-slot override cannot
            express a second one, so it is not offered (the omni dropdown lists
            only the follow options and the custom entry) and not honoured here.
            """
            omni_provider = str(core_config.get('omniModelProvider') or '').strip()
            if omni_provider == 'custom':
                return 'local'
            # Legacy configurations allowed hand-entered realtime endpoints before omniModelProvider existed; they have
            # no provider value but are genuinely custom endpoints and must preserve previous behavior.
            # Require actually persisted omniModelUrl; profile-default-filled URLs never enter
            # this key, avoiding misclassification of unselected configurations as custom.
            if not omni_provider and str(core_config.get('omniModelUrl') or '').strip():
                return 'local'
            return str(core_config.get('CORE_API_TYPE', '') or '')

        if model_type == 'game_main':
            provider = str(core_config.get('gameMainModelProvider') or 'follow_conversation').strip()
            if not treat_as_custom or provider == 'follow_conversation':
                return self.get_model_api_config('conversation', _core_config=core_config)
        elif model_type == 'game_summary':
            provider = str(core_config.get('gameSummaryModelProvider') or 'follow_summary').strip()
            if not treat_as_custom or provider == 'follow_summary':
                return self.get_model_api_config('summary', _core_config=core_config)
        
        # Agent always uses dedicated AGENT_MODEL_URL, but is_custom is True only with enableCustomApi.
        # AGENT_MODEL_URL does **not** undergo region rewriting: _normalize_agent_url
        # is an identity function because free-agent-model deliberately pins the CN text endpoint. The former comment
        # claimed lanlan.app normalization, contradicting implementation and already misleading a review.
        if treat_as_custom or model_type == 'agent':
            custom_model = core_config.get(mapping['custom_model'], '')
            custom_url = core_config.get(mapping['custom_url'], '')
            custom_key = core_config.get(mapping['custom_key'], '')

            # In GSV mode, voice_id identifies the voice with no model concept, so URL alone counts as configured;
            # do not broaden all tts_custom cases, preserving cosyvoice users' existing fallthrough.
            is_gsv_url = (
                gsv_enabled_for_tts
                and custom_url.startswith(('http://', 'https://'))
            )

            # Use complete custom configuration when available.
            if (custom_model and custom_url) or is_gsv_url:
                resolved_api_key = custom_key
                # With only GSV enabled and TTS_MODEL_API_KEY empty, the CosyVoice clone path still reuses
                # tts_custom (register_voice -> get_tts_api_key('cosyvoice')
                # -> api_key here). Returning an empty key makes CosyVoice report
                # TTS_AUDIO_API_KEY_MISSING; ASSIST_API_KEY_QWEN fallback preserves the CosyVoice cloning
                # capability users had before enabling GSV.
                if is_gsv_url and not resolved_api_key and model_type == 'tts_custom':
                    resolved_api_key = (core_config.get('ASSIST_API_KEY_QWEN') or '').strip()
                return {
                    'model': custom_model,
                    'api_key': resolved_api_key,
                    'base_url': custom_url,
                    'is_custom': treat_as_custom,
                    # Slot dropdown values represent protocol only while their custom configuration actually applies.
                    # Agent alone bypasses treat_as_custom to enter this branch directly
                    # (`if treat_as_custom or model_type == 'agent'` above).
                    # Disabling custom API falls URL/key back to assist; protocol must follow,
                    # or leftover claude/kimi_code selections create assist URL + Anthropic protocol.
                    # GSV keeps treat_as_custom true (= enable_custom_api or gsv_enabled),
                    # so remains unaffected.
                    'provider_type': (
                        _resolved_provider_type_for_model(model_type)
                        if treat_as_custom
                        else _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                    ),
                    # Realtime api_type must identify the WebSocket provider's dialect.
                    # Previously unconditional local remains unimplemented and also pollutes global state through
                    # lifecycle core_api_type assignment, causing dummy TTS dispatch.
                    # Only hand-entered endpoints are truly local. Explicit named providers retain
                    # their identities; others (follow_core / legacy empty) follow core API.
                    'api_type': (
                        _resolved_realtime_api_type() if model_type == 'realtime' else None
                    ),
                }
        
        # Custom CosyVoice voice fallback: prefer the user's saved Qwen CosyVoice API,
        # falling back to assist API only when absent (CosyVoice is currently the sole provider supporting voice cloning).
        if model_type == 'tts_custom':
            active_assist = str(core_config.get('assistApi') or '').strip()
            qwen_candidates = []
            if active_assist in ('qwen', 'qwen_intl'):
                qwen_candidates.append(active_assist)
            qwen_candidates.extend(['qwen', 'qwen_intl'])

            seen_qwen = set()
            for qwen_provider in qwen_candidates:
                if qwen_provider in seen_qwen:
                    continue
                seen_qwen.add(qwen_provider)
                key_field = 'ASSIST_API_KEY_QWEN_INTL' if qwen_provider == 'qwen_intl' else 'ASSIST_API_KEY_QWEN'
                qwen_api_key = (core_config.get(key_field) or '').strip()
                if not qwen_api_key:
                    continue
                if qwen_provider == active_assist:
                    base_url = core_config.get('OPENROUTER_URL', '')
                else:
                    qwen_profile = get_assist_api_profiles().get(qwen_provider, {})
                    resolved_urls = core_config.get('RESOLVED_PROVIDER_URLS')
                    resolved_core_cfg = {
                        'resolvedProviderUrls': resolved_urls if isinstance(resolved_urls, dict) else {},
                    }
                    base_url = (
                        self._get_saved_provider_url(
                            resolved_core_cfg,
                            'assist',
                            qwen_provider,
                            qwen_profile,
                            'OPENROUTER_URL',
                            'OPENROUTER_URLS',
                        )
                        or qwen_profile.get('OPENROUTER_URL', core_config.get('OPENROUTER_URL', ''))
                    )
                return {
                    'model': core_config.get(mapping['default_model'], ''), # Placeholder; downstream replaces it with the actual model.
                    'api_key': qwen_api_key,
                    'base_url': base_url,
                    'is_custom': False,
                    'provider_type': _provider_type_from_assist_key(qwen_provider),
                }

        # Select API fallback based on fallback_type.
        #
        # Protocol must share the URL's origin. Returned base_url / api_key come entirely from core (or assist
        # below), while provider_type previously used _resolved_provider_type_for_model,
        # based on dropdown values ungated by ENABLE_CUSTOM_API. Selecting claude/kimi_code
        # and later disabling custom API therefore paired assist URL/key with Anthropic
        # protocol, breaking requests. Fallback protocols follow fallback targets, making mismatch impossible.
        if mapping['fallback_type'] == 'core':
            # Fall back to core API configuration.
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('CORE_API_KEY', ''),
                'base_url': core_config.get('CORE_URL', ''),
                'is_custom': False,
                'provider_type': _provider_type_from_core_key(
                    core_config.get('CORE_API_TYPE', '')
                ),
                # Realtime models falling back to core use configured CORE_API_TYPE.
                'api_type': core_config.get('CORE_API_TYPE', '') if model_type == 'realtime' else None,
            }
        elif mapping['fallback_type'] == 'conversation':
            return self.get_model_api_config('conversation', _core_config=core_config)
        elif mapping['fallback_type'] == 'summary':
            return self.get_model_api_config('summary', _core_config=core_config)
        else:
            # Fall back to assist API configuration with matching protocol, as explained above.
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('OPENROUTER_API_KEY', ''),
                'base_url': core_config.get('OPENROUTER_URL', ''),
                'is_custom': False,
                'provider_type': _normalize_provider_type_value(
                    core_config.get('PROVIDER_TYPE')
                ),
            }

    def is_agent_api_ready(self) -> tuple[bool, list[str]]:
        """
        Agent mode readiness check:
        - a usable AGENT_MODEL (model/url/api_key) is required
        - whether it is free (quota counting / frontend hints) is decided separately by
          is_agent_free() and is unrelated to this check: readiness only cares whether the
          model/url/key trio is filled in and a request can be made. free-access is a valid
          placeholder token for the truly free agent and should pass the gate; dirty configs
          (placeholder key against a self-paid endpoint) are caught downstream by 401, not here.
        """
        reasons = []
        agent_api = self.get_model_api_config('agent')
        if not (agent_api.get('model') or '').strip():
            reasons.append("Agent 模型未配置")
        if not (agent_api.get('base_url') or '').strip():
            reasons.append("Agent API URL 未配置")
        api_key = (agent_api.get('api_key') or '').strip()
        if not api_key:
            reasons.append("Agent API Key 未配置或不可用")
        return len(reasons) == 0, reasons

    def is_agent_free(self) -> bool:
        """Whether the Agent actually in use is the built-in free Agent model (free-agent-model).

        The single source of truth for "is the agent free" — quota counting and the frontend
        "free model may be congested" hint both read it. Dual of is_free_voice() (the
        voice/core dimension): even when using the free voice (core=free), if the agent is
        switched to a self-paid/custom model, this returns False.
        """
        agent_model = (self.get_model_api_config('agent').get('model') or '').strip()
        return agent_model == self._free_agent_model_name

    def is_free_voice(self) -> bool:
        """Whether the built-in free voice is in use (core=free). The single source of truth for
        "is voice free" — free preset voices, hidden main cloud voices and the default YUI
        fallback all read it. Dual of is_agent_free().

        Realtime and text TTS share the same voice and follow core, independent of assist:
        hide_cloud_main hides the main CosyVoice/Qwen cloud bucket in free mode. Provider-keyed
        clone buckets are handled separately by get_voices_for_current_api(), and remain visible
        when the corresponding CosyVoice clone API key is configured.
        """
        return (self.get_core_config().get('CORE_API_TYPE') or '') == 'free'
