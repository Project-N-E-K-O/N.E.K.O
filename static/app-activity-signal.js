/**
 * app-activity-signal.js — frontend half of the cross-platform OS-activity
 * signal channel (issue #1023, follow-up to PR #1015's activity tracker).
 *
 * In remote / non-Windows deployments the Python backend can't read the
 * foreground window, system idle, CPU, or GPU — those signals are about
 * the *server*, not the user. PR #1015 left
 * ``UserActivityTracker.push_external_system_signal()`` as the documented
 * way to feed them from outside; this file POSTs there from the renderer
 * on a 5s heartbeat, sourcing the data through the Electron preload
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
 * per lanlan_name (matches our heartbeat) and a 30s TTL on each push
 * (so a stalled heartbeat triggers fallback to the local collector).
 *
 * Single-window invariant: load this script only from the always-on
 * desktop UI (``templates/index.html``), not from the chat popup —
 * the chat window comes and goes, the desktop pet stays.
 */
(function () {
    'use strict';

    // ── tuning ──────────────────────────────────────────────────────
    // Match backend ``_EXTERNAL_SIGNAL_MIN_INTERVAL`` (5.0s). Faster
    // bumps the rate limit and wastes bridge calls; slower lets the
    // 30s TTL expire if anything blips.
    var HEARTBEAT_INTERVAL_MS = 5000;
    // After this many consecutive failures, throttle the log spam (we
    // keep retrying, just stop printing every time).
    var LOG_FAILURE_QUIET_THRESHOLD = 3;
    var ENDPOINT = '/api/activity_signal';

    var heartbeatTimer = null;
    var inFlight = false;            // re-entrancy guard if the previous POST is still running
    var consecutiveFailures = 0;
    var hasLoggedBridgeMissing = false;

    function resolveLanlanName() {
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
            // Log once and let the failure counter throttle the rest.
            if (consecutiveFailures < LOG_FAILURE_QUIET_THRESHOLD) {
                console.warn('[activity-signal] bridge read failed:', e);
            }
            return null;
        }
    }

    async function pushOnce(lanlanName) {
        if (inFlight) {
            // Previous POST still running (rare — endpoint is fast).
            // Skip this tick rather than queueing up; backend rate
            // limit would reject it anyway.
            return;
        }
        var payload = await readSignalsFromBridge();
        if (payload === null) {
            // Bridge read failed or returned nothing usable. Skip silently —
            // tracker's 30s TTL will expire and the local collector takes
            // over, which is the documented fallback for this case.
            return;
        }
        payload.lanlan_name = lanlanName;
        inFlight = true;
        try {
            var resp = await fetch(ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                cache: 'no-store',
                // Don't keep the page alive on close just for the heartbeat.
                keepalive: false,
            });
            if (resp.ok) {
                if (consecutiveFailures > 0) {
                    console.info('[activity-signal] recovered after '
                        + consecutiveFailures + ' failure(s)');
                }
                consecutiveFailures = 0;
                return;
            }
            // 429 = rate-limited (we're heartbeating too fast somehow — shouldn't
            // happen with the 5s interval, but if it does we just skip).
            // 404 = lanlan_name not registered yet (boot race) — silent.
            // 503 = tracker not yet initialised (boot race) — silent.
            // Anything else: count toward failure log throttle.
            if (resp.status !== 429 && resp.status !== 404 && resp.status !== 503) {
                consecutiveFailures++;
                if (consecutiveFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                    console.warn(
                        '[activity-signal] push failed: HTTP ' + resp.status,
                    );
                }
            }
        } catch (e) {
            consecutiveFailures++;
            if (consecutiveFailures <= LOG_FAILURE_QUIET_THRESHOLD) {
                console.warn('[activity-signal] push exception:', e);
            }
        } finally {
            inFlight = false;
        }
    }

    function start() {
        if (heartbeatTimer !== null) {
            return; // already running
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
    // hidden window. Resume on visible. The 30s TTL covers brief tab
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
