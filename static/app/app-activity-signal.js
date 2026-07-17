/**
 * app-activity-signal.js — frontend half of the cross-platform OS-activity
 * signal channel (issue #1023, follow-up to PR #1015's activity tracker).
 *
 * In remote / non-Windows deployments the Python backend can't read the
 * foreground window, system idle, CPU, or GPU — those signals are about
 * the *server*, not the user. PR #1015 left
 * ``UserActivityTracker.push_external_system_signal()`` as the documented
 * way to feed them from outside; this file POSTs there from the renderer
 * on a 6s heartbeat, sourcing the data through the Electron preload
 * bridge (``window.nekoActivitySignal`` — exposed by the NEKO-PC
 * companion PR, defined in NEKO-PC ``src/main.js`` +
 * ``src/preload-*.js``).
 *
 * Defensive against the bridge being absent: pure browsers (mobile
 * shell, dev runs without the Electron wrapper, NEKO-PC older than the
 * companion PR) skip the heartbeat entirely — the backend tracker
 * falls back to its local collector in degraded mode, which is what
 * #1015 already documents for these cases.
 *
 * Pairs with backend endpoint ``POST /api/activity_signal`` defined in
 * ``main_routers/system_router.py``. Endpoint enforces a 5s rate limit
 * per lanlan_name; this client intentionally waits 6s to leave room for
 * timer / transport jitter. The tracker uses a 20s TTL on each accepted
 * push, so a stalled heartbeat still falls back to the local collector.
 *
 * Single-window invariant: load this script only from the always-on
 * desktop UI (``templates/index.html``), not from the chat popup —
 * the chat window comes and goes, the desktop pet stays.
 */
(function () {
    'use strict';

    // ── tuning ──────────────────────────────────────────────────────
    // Stay above backend ``_EXTERNAL_SIGNAL_MIN_INTERVAL`` (5.0s).
    // Using the exact same 5s boundary caused normal timer / transport
    // jitter to produce repeated 429 responses. At 6s, two dropped
    // pushes put the next successful attempt at about 18s, inside the
    // tracker's 20s freshness TTL with room for normal jitter.
    var HEARTBEAT_INTERVAL_MS = 6000;
    // After this many consecutive failures, throttle the log spam (we
    // keep retrying, just stop printing every time).
    var LOG_FAILURE_QUIET_THRESHOLD = 3;
    // After this many consecutive 403s (CSRF/Origin rejections), STOP
    // the heartbeat — not throttle it, stop it. Issue #1479 / wehos:
    // a 6s heartbeat that 403s forever is a silent permanent
    // degradation (tracker falls back to local collector but nothing
    // visible alerts the user, and we're burning HTTP cycles for
    // nothing). 6 ticks × 6s = 36s is well past the 20s tracker TTL — enough room
    // for the page_config token bootstrap to win any boot-time race
    // before we give up.
    var MAX_CONSECUTIVE_CSRF_FAILURES = 6;
    var ENDPOINT = '/api/activity_signal';

    var heartbeatTimer = null;
    var inFlight = false;            // re-entrancy guard if the previous POST is still running
    var consecutiveFailures = 0;
    var consecutiveCsrfFailures = 0; // dedicated counter for 403 — separate from generic failures so transient 5xx don't trip the stop-the-heartbeat gate
    var heartbeatStoppedDueToCsrf = false; // sticky flag: once we give up, don't auto-restart on visibilitychange
    var hasLoggedBridgeMissing = false;

    function resolveLanlanName() {
        // Order matters: ``window.appState.lanlan_name`` first, then
        // ``window.lanlan_config.lanlan_name`` as fallback. During a
        // character switch the renderer updates ``appState`` ahead of
        // ``lanlan_config`` (see ``static/app-character.js`` switch
        // sequence + the existing precedent in
        // ``static/app/app-react-chat-window`` ~line 1442 where
        // CodeRabbit flagged the same lag in a prior PR). Reading
        // ``lanlan_config`` first would push a few heartbeats to the
        // *old* tracker during the switch window — 404'd by backend
        // but loses the immediate post-switch OS-signal coverage.
        try {
            var st = window.appState;
            if (st && typeof st.lanlan_name === 'string' && st.lanlan_name) {
                return st.lanlan_name;
            }
        } catch (_) {}
        try {
            var cfg = window.lanlan_config;
            if (cfg && typeof cfg.lanlan_name === 'string' && cfg.lanlan_name) {
                return cfg.lanlan_name;
            }
        } catch (_) {}
        try {
            var params = new URLSearchParams(window.location.search);
            var fromUrl = params.get('lanlan_name');
            if (fromUrl) return fromUrl;
        } catch (_) {}
        return '';
    }

    function hasBridge() {
        return Boolean(
            typeof window !== 'undefined'
            && window.nekoActivitySignal
            && typeof window.nekoActivitySignal.read === 'function'
        );
    }

    /**
     * Pull signals from the Electron bridge. Returns the snake_case
     * payload the backend expects, or ``null`` on any failure (bridge
     * rejected, fields wrong shape, etc — surfaced as "no signal" so
     * we just skip this tick).
     */
    async function readSignalsFromBridge() {
        try {
            var snapshot = await window.nekoActivitySignal.read();
            if (!snapshot || typeof snapshot !== 'object') {
                return null;
            }
            var payload = {};
            // Bridge keys are camelCase (Node convention); backend
            // expects snake_case. Each field is independently optional —
            // a partial snapshot is still better than no push.
            if (typeof snapshot.windowTitle === 'string') {
                payload.window_title = snapshot.windowTitle;
            }
            if (typeof snapshot.processName === 'string') {
                payload.process_name = snapshot.processName;
            }
            if (typeof snapshot.idleSeconds === 'number'
                && isFinite(snapshot.idleSeconds)
                && snapshot.idleSeconds >= 0) {
                payload.idle_seconds = snapshot.idleSeconds;
            }
            if (typeof snapshot.cpuAvg30s === 'number'
                && isFinite(snapshot.cpuAvg30s)
                && snapshot.cpuAvg30s >= 0
                && snapshot.cpuAvg30s <= 100) {
                payload.cpu_avg_30s = snapshot.cpuAvg30s;
            }
            if (typeof snapshot.gpuUtilization === 'number'
                && isFinite(snapshot.gpuUtilization)
                && snapshot.gpuUtilization >= 0
                && snapshot.gpuUtilization <= 100) {
                payload.gpu_utilization = snapshot.gpuUtilization;
            }
            return payload;
        } catch (e) {
            // Bridge call itself threw — distinct from "bridge returned
            // an empty snapshot" because it usually means IPC died.
            // Bump the failure counter so the log throttle below kicks
            // in: ``readSignalsFromBridge`` returns ``null`` either for
            // benign "no signal" or for real failures, and we want
            // repeat failures to fall under the same 3-then-quiet
            // policy as POST failures. Then log gated by current
            // counter (after increment, ``<= threshold``).
            consecutiveFailures++;
            if (consecutiveFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                console.warn('[activity-signal] bridge read failed:', e);
            }
            return null;
        }
    }

    /**
     * Resolve the X-CSRF-Token header from
     * ``window.nekoLocalMutationSecurity`` (set up by
     * ``static/app-prompt-shared.js`` after fetching
     * ``/api/config/page_config``). Returns ``{}`` if the helper is
     * missing or throws — the backend will then reject with 403 and
     * the caller's retry path handles it.
     */
    async function getCsrfMutationHeaders() {
        try {
            var sec = window.nekoLocalMutationSecurity;
            if (sec && typeof sec.getMutationHeaders === 'function') {
                return await sec.getMutationHeaders();
            }
        } catch (_) { /* fall through */ }
        return {};
    }

    /**
     * Read a 403 response body and decide whether the rejection came
     * from ``_validate_local_mutation_request`` (i.e. CSRF/Origin
     * guard) vs. any other 403 — business rule, downstream service,
     * future role-based check, etc. Mirrors the contract in
     * ``static/app-prompt-shared.js`` 436-541 and
     * ``static/tutorial/core/universal-manager.js`` 120-134: only
     * ``error_code === "csrf_validation_failed"`` triggers the
     * refresh / retry / stop-the-heartbeat path (CodeRabbit Major on
     * PR #1532).
     *
     * ``resp.clone()`` so the caller can still read the original body
     * if they need to (and so the original ``Response.bodyUsed`` flag
     * doesn't trip on a second consumer).
     */
    async function isCsrfValidationFailure(resp) {
        if (!resp || resp.status !== 403) return false;
        try {
            var cloned = typeof resp.clone === 'function' ? resp.clone() : resp;
            var body = await cloned.json();
            return Boolean(body && body.error_code === 'csrf_validation_failed');
        } catch (_) {
            // Non-JSON 403 body / parse failure → not a unified-guard
            // rejection; treat as a generic 403 (could be a reverse
            // proxy / WAF / future business rule).
            return false;
        }
    }

    async function pushOnce(lanlanName) {
        // Set the in-flight latch BEFORE the bridge read so two ticks
        // can't both clear the ``if (inFlight)`` check while one is
        // mid-await. On a slow IPC (Linux xprop, macOS Screen Recording
        // prompt) the bridge read can take seconds; without this latch
        // a subsequent tick would re-enter, hit the backend twice in
        // quick succession, and trip the 5s rate limit unnecessarily.
        if (inFlight) {
            // Previous tick still in flight (rare — endpoint is fast).
            // Skip rather than queue; the backend rate limit would
            // reject the duplicate anyway.
            return;
        }
        inFlight = true;
        try {
            var payload = await readSignalsFromBridge();
            if (payload === null) {
                // Bridge read failed or returned nothing usable. Skip
                // silently — tracker's 20s TTL will expire and the
                // local collector takes over, which is the documented
                // fallback for this case. ``readSignalsFromBridge``
                // already incremented ``consecutiveFailures`` if the
                // bridge call itself threw.
                return;
            }
            // F6 (Codex on PR #1477): bridge returned an object but
            // every field failed type/range validation, so the payload
            // is empty. Posting just lanlan_name is worse than skipping
            // — the tracker's ``push_external_system_signal`` defaults
            // absent numerics to 0.0 and flips ``os_signals_available``
            // to true, silently overwriting real state with "idle=0,
            // cpu=0, no window". Skip and let the 20s TTL hand back to
            // the local collector. Backend also rejects empty payloads
            // as a defence-in-depth, but skipping client-side saves an
            // HTTP roundtrip and a rate-limit hit.
            if (Object.keys(payload).length === 0) {
                return;
            }
            payload.lanlan_name = lanlanName;
            var body = JSON.stringify(payload);

            async function sendOnce() {
                var csrfHeaders = await getCsrfMutationHeaders();
                return fetch(ENDPOINT, {
                    method: 'POST',
                    headers: Object.assign(
                        { 'Content-Type': 'application/json' },
                        csrfHeaders,
                    ),
                    body: body,
                    cache: 'no-store',
                    // Don't keep the page alive on close just for the heartbeat.
                    keepalive: false,
                });
            }

            var resp = await sendOnce();
            // CodeRabbit Major on PR #1532: only the unified-guard's
            // ``csrf_validation_failed`` rejection should drive the
            // refresh / retry / stop-the-heartbeat path. Other 403s
            // (future business rules, reverse proxy, WAF, …) need to
            // go through the generic-failure branch instead.
            var isCsrf403 = await isCsrfValidationFailure(resp);

            // CSRF-403 retry-once: the page_config token bootstrap
            // might not have completed on the very first heartbeat
            // tick. Try refreshing the cached token and resending
            // before counting this toward the stop-the-heartbeat
            // threshold. Do NOT retry on the *second* 403 of the same
            // call — that means the token's genuinely unavailable,
            // not a race, and a tight retry loop would just double the
            // 403 rate.
            if (isCsrf403) {
                var sec = window.nekoLocalMutationSecurity;
                if (sec && typeof sec.refreshToken === 'function') {
                    try {
                        await sec.refreshToken();
                        resp = await sendOnce();
                        // Re-classify after retry — a successful retry
                        // turns this from a CSRF-403 into 200 / 4xx /
                        // 5xx, and we want the rest of this function
                        // to treat it that way.
                        isCsrf403 = await isCsrfValidationFailure(resp);
                    } catch (_) { /* fall through to 403 handling below */ }
                }
            }

            if (resp.ok) {
                if (consecutiveFailures > 0 || consecutiveCsrfFailures > 0) {
                    console.info('[activity-signal] recovered after '
                        + (consecutiveFailures + consecutiveCsrfFailures)
                        + ' failure(s)');
                }
                consecutiveFailures = 0;
                consecutiveCsrfFailures = 0;
                return;
            }

            // Any non-CSRF response (any 200/4xx/5xx that isn't
            // ``csrf_validation_failed``) breaks the *consecutive*
            // CSRF streak — without this, alternating CSRF-403 / 500
            // / non-CSRF-403 sequences would still trip the
            // stop-the-heartbeat threshold (Codex P2 on PR #1532).
            if (!isCsrf403) {
                consecutiveCsrfFailures = 0;
            }

            if (isCsrf403) {
                // CSRF/Origin gate rejected us — different failure
                // mode from 5xx (transient backend issue, retry) or
                // 4xx-business (skip). Spinning forever just burns
                // cycles. Track separately and stop the heartbeat
                // after MAX_CONSECUTIVE_CSRF_FAILURES so the tracker
                // degrades cleanly to its local collector instead of
                // looking up at silent 403s forever.
                consecutiveCsrfFailures++;
                if (consecutiveCsrfFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                    console.warn(
                        '[activity-signal] push rejected with '
                        + 'csrf_validation_failed; token may not be '
                        + 'granted yet — will retry.',
                    );
                }
                if (consecutiveCsrfFailures >= MAX_CONSECUTIVE_CSRF_FAILURES
                    && !heartbeatStoppedDueToCsrf) {
                    heartbeatStoppedDueToCsrf = true;
                    console.error(
                        '[activity-signal] '
                        + consecutiveCsrfFailures
                        + ' consecutive csrf_validation_failed '
                        + 'rejections — heartbeat stopped to avoid '
                        + 'silent spin. Tracker will fall back to its '
                        + 'local collector (degraded mode on '
                        + 'non-Windows / remote backends). To '
                        + 'diagnose: verify '
                        + '``window.nekoLocalMutationSecurity`` is '
                        + 'exposed and ``GET /api/config/page_config``'
                        + ' returns ``autostart_csrf_token``.',
                    );
                    stop();
                }
                return;
            }

            // Generic failure path:
            //   429 = rate-limited (we're heartbeating too fast somehow — shouldn't
            //       happen with the 5s interval, but if it does we just skip).
            //   404 = lanlan_name not registered yet (boot race) — silent.
            //   503 = tracker not yet initialised (boot race) — silent.
            //   Other 4xx (including non-CSRF 403s like reverse-proxy rejects)
            //       and 5xx → count toward generic failure log throttle.
            if (resp.status !== 429 && resp.status !== 404 && resp.status !== 503) {
                consecutiveFailures++;
                if (consecutiveFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                    console.warn(
                        '[activity-signal] push failed: HTTP ' + resp.status,
                    );
                }
            }
        } catch (e) {
            // Network error / fetch threw — we never heard back from
            // the server, so this is NOT a CSRF rejection. Don't let
            // network blips contribute to the 403 streak (Codex P2 on
            // PR #1532).
            consecutiveCsrfFailures = 0;
            consecutiveFailures++;
            if (consecutiveFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                console.warn('[activity-signal] push exception:', e);
            }
        } finally {
            inFlight = false;
            // 排空响应体，立即释放 fetch 响应的 Mojo 数据管道共享缓冲（默认容量 ~2MB）。
            // 成功路径（resp.ok）与多数失败路径都从不读 body，而 isCsrfValidationFailure
            // 对非 403 直接 return false 也不读 —— 未消费的响应体会让该数据管道一直占着
            // MEM_MAPPED 直到 GC。每 5s 心跳漏一块 ~2MB，挂机数小时 committed 涨到 10G+
            // （renderer 私有提交 / 任务管理器「提交大小」/ DevTools heap snapshot 都看
            // 不到，只在系统 commit charge 上暴涨，需 VirtualQueryEx 按 MEM_MAPPED 口径才
            // 量得到）。用 body.cancel() 不读内容直接释放管道；bodyUsed 守卫避免重复消费。
            try {
                if (typeof resp !== 'undefined' && resp && resp.body && !resp.bodyUsed) {
                    resp.body.cancel();
                }
            } catch (_) { /* 已消费 / 无 body / 环境不支持，忽略 */ }
        }
    }

    function start() {
        if (heartbeatTimer !== null) {
            return; // already running
        }
        if (heartbeatStoppedDueToCsrf) {
            // We previously gave up after MAX_CONSECUTIVE_CSRF_FAILURES.
            // ``visibilitychange`` would normally restart the timer
            // when the tab comes back; suppress that here so a hidden
            // → visible flip doesn't undo the explicit stop. A page
            // reload starts the heartbeat fresh (sticky flag is
            // module-scoped, not persisted), which is the right
            // recovery path because that's also when the
            // ``nekoLocalMutationSecurity`` helper re-bootstraps.
            return;
        }
        if (!hasBridge()) {
            if (!hasLoggedBridgeMissing) {
                hasLoggedBridgeMissing = true;
                console.info(
                    '[activity-signal] Electron bridge '
                    + '(window.nekoActivitySignal) not exposed — heartbeat '
                    + 'disabled. Backend tracker will use its local collector '
                    + '(degraded mode on non-Windows / remote backends).',
                );
            }
            return;
        }
        var lanlanName = resolveLanlanName();
        if (!lanlanName) {
            // No character yet — happens during very early boot. Retry
            // every tick; resolveLanlanName is cheap.
            console.debug(
                '[activity-signal] lanlan_name not resolved yet; '
                + 'will retry each heartbeat.',
            );
        }

        async function tick() {
            // Resolve lanlan_name on every tick rather than caching once
            // at start — character switches mid-session would otherwise
            // keep pushing into the old tracker.
            var current = resolveLanlanName();
            if (current) {
                await pushOnce(current);
            }
        }

        // First tick immediately (don't wait 5s for the first signal
        // after the page loads), then on interval.
        tick();
        heartbeatTimer = setInterval(tick, HEARTBEAT_INTERVAL_MS);
    }

    function stop() {
        if (heartbeatTimer !== null) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    // Expose for tests / manual control. Not a public contract — kept
    // intentionally on a namespaced global rather than ``window.*``
    // directly so it's easy to grep for callers.
    window.nekoActivitySignalClient = {
        start: start,
        stop: stop,
        // Exposed for unit-test hooks; not for production callers.
        _resolveLanlanName: resolveLanlanName,
        _hasBridge: hasBridge,
    };

    // Auto-start once DOM is settled. We don't depend on any specific
    // DOM nodes, but waiting until DOMContentLoaded keeps the heartbeat
    // from racing the early bridge-injection phase.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
        start();
    }

    // Pause on tab hide to avoid burning bridge IPC + network for a
    // hidden window. Resume on visible. The 20s TTL covers brief tab
    // switches, so the user won't see stale data on resume — the next
    // tick fires within HEARTBEAT_INTERVAL_MS.
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            stop();
        } else if (document.visibilityState === 'visible') {
            start();
        }
    });
})();
