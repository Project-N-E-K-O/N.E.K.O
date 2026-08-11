/**
 * Selects at most one CAT1 desktop-window presentation for each sensing fact.
 * Runners keep their own lifecycle; this layer only removes listener-order
 * races when more than one presentation can reach the same native window.
 */
(function () {
    'use strict';

    const sensingContext = window.nekoDesktopWindowSensingContext;
    if (!sensingContext
        || typeof sensingContext.getCurrent !== 'function'
        || typeof sensingContext.subscribe !== 'function') {
        return;
    }

    const REQUIRED_RUNNER_KINDS = Object.freeze([
        'desktop-window-top-edge',
        'desktop-window-edge-peek',
    ]);
    const runners = new Map();
    let disposed = false;
    let replayTimer = 0;
    let presentationCooldownUntil = 0;
    let rearmOnNextFact = false;

    function getActiveRunner(button) {
        let active = null;
        runners.forEach((runner) => {
            if (active || typeof runner.isActive !== 'function') return;
            try {
                if (runner.isActive(button)) active = runner;
            } catch (_) {}
        });
        return active;
    }

    function processSensingResult(result) {
        if (disposed) return false;
        if (!REQUIRED_RUNNER_KINDS.every((kind) => runners.has(kind))) return false;
        const activeBefore = getActiveRunner(null);
        const eligibleRunners = [];
        let runnerFailed = false;
        runners.forEach((runner) => {
            if (typeof runner.handleSensingResult !== 'function') return;
            try {
                if (runner.handleSensingResult(result) === true) {
                    eligibleRunners.push(runner);
                }
            } catch (_) {
                runnerFailed = true;
            }
        });

        // A fact that terminates one presentation must not start another one
        // during the same revision. The next sensing fact may offer again.
        if (runnerFailed || activeBefore || getActiveRunner(null)) return false;

        if (rearmOnNextFact) {
            if (Date.now() < presentationCooldownUntil) return false;
            let rearmed = false;
            runners.forEach((runner) => {
                if (typeof runner.rearmOpportunity !== 'function') return;
                try {
                    if (runner.rearmOpportunity(result) === true) {
                        rearmed = true;
                        if (!eligibleRunners.includes(runner)) eligibleRunners.push(runner);
                    }
                } catch (_) {}
            });
            if (!rearmed) return false;
            rearmOnNextFact = false;
        }

        const candidates = [];
        let candidateFailed = false;
        eligibleRunners.forEach((runner) => {
            if (typeof runner.getCandidate !== 'function') return;
            let candidate = null;
            try { candidate = runner.getCandidate(); } catch (_) { candidateFailed = true; }
            if (!candidate || !Number.isFinite(Number(candidate.distancePx))) return;
            candidates.push({ runner: runner, candidate: candidate });
        });
        if (candidateFailed) return false;
        candidates.sort((left, right) => {
            const distanceDelta = Number(left.candidate.distancePx) - Number(right.candidate.distancePx);
            if (Math.abs(distanceDelta) > 0.000001) return distanceDelta;
            const priorityDelta = Number(left.runner.priority) - Number(right.runner.priority);
            if (Number.isFinite(priorityDelta) && priorityDelta !== 0) return priorityDelta;
            return String(left.runner.kind).localeCompare(String(right.runner.kind));
        });

        const selected = candidates[0];
        if (!selected || typeof selected.runner.startCandidate !== 'function') return false;
        let started = false;
        try { started = selected.runner.startCandidate(selected.candidate) === true; } catch (_) {}
        if (!started) return false;

        runners.forEach((runner) => {
            if (runner === selected.runner || typeof runner.consumeOpportunity !== 'function') return;
            try { runner.consumeOpportunity(result); } catch (_) {}
        });
        return true;
    }

    function scheduleCurrentReplay() {
        if (disposed || replayTimer) return;
        replayTimer = window.setTimeout(() => {
            replayTimer = 0;
            const current = sensingContext.getCurrent();
            if (current) processSensingResult(current);
        }, 0);
    }

    function register(runner) {
        if (disposed
            || !runner
            || typeof runner.kind !== 'string'
            || !runner.kind
            || runners.has(runner.kind)) {
            return function noop() {};
        }
        runners.set(runner.kind, runner);
        scheduleCurrentReplay();
        return function unregister() {
            if (runners.get(runner.kind) === runner) runners.delete(runner.kind);
        };
    }

    function cancel(button, options) {
        let cancelled = false;
        runners.forEach((runner) => {
            if (typeof runner.cancel !== 'function') return;
            try {
                cancelled = runner.cancel(button, options || {}) === true || cancelled;
            } catch (_) {}
        });
        return cancelled;
    }

    function completePresentation(cooldownMs) {
        const durationMs = Math.max(0, Number(cooldownMs) || 0);
        presentationCooldownUntil = Math.max(
            presentationCooldownUntil,
            Date.now() + durationMs
        );
        rearmOnNextFact = true;
    }

    function getState() {
        const active = getActiveRunner(null);
        return Object.freeze({
            activeKind: active ? active.kind : '',
            registeredKinds: Object.freeze(Array.from(runners.keys())),
            cooldownUntil: presentationCooldownUntil,
            rearmOnNextFact: rearmOnNextFact,
        });
    }

    const unsubscribe = sensingContext.subscribe(processSensingResult);

    function dispose() {
        if (disposed) return;
        disposed = true;
        if (replayTimer) {
            window.clearTimeout(replayTimer);
            replayTimer = 0;
        }
        if (typeof unsubscribe === 'function') {
            try { unsubscribe(); } catch (_) {}
        }
        runners.clear();
        presentationCooldownUntil = 0;
        rearmOnNextFact = false;
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
        try {
            delete window.NekoDesktopWindowInteractions;
        } catch (_) {
            window.NekoDesktopWindowInteractions = undefined;
        }
    }

    window.NekoDesktopWindowInteractions = Object.freeze({
        register: register,
        cancel: cancel,
        completePresentation: completePresentation,
        isActive(button) {
            return !!getActiveRunner(button);
        },
        getState: getState,
    });
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
})();
