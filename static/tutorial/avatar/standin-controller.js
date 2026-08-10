(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialAvatarStandInController = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    const DEFAULT_TUTORIAL_STAND_IN_TIMING = Object.freeze({
        fadeOutMs: 1500,
        approachMs: 2000,
        holdMs: 2500,
        returnMs: 2000
    });

    function resolveCueDuration(value, fallback) {
        return Number.isFinite(Number(value))
            ? Math.max(0, Math.floor(Number(value)))
            : fallback;
    }

    function resolveTutorialStandInTiming() {
        const avatarStage = root && root.YuiGuideAvatarStage;
        const sharedTiming = avatarStage && avatarStage.TUTORIAL_AVATAR_PROBE_TIMING;
        return {
            fadeOutMs: resolveCueDuration(
                sharedTiming && sharedTiming.fadeOutMs,
                DEFAULT_TUTORIAL_STAND_IN_TIMING.fadeOutMs
            ),
            approachMs: resolveCueDuration(
                sharedTiming && sharedTiming.approachMs,
                DEFAULT_TUTORIAL_STAND_IN_TIMING.approachMs
            ),
            holdMs: resolveCueDuration(
                sharedTiming && sharedTiming.holdMs,
                DEFAULT_TUTORIAL_STAND_IN_TIMING.holdMs
            ),
            returnMs: resolveCueDuration(
                sharedTiming && sharedTiming.returnMs,
                DEFAULT_TUTORIAL_STAND_IN_TIMING.returnMs
            )
        };
    }

    function resolveCueTiming(cue, scene, director) {
        const narrationDurationMs = director && typeof director.getAvatarFloatingNarrationDurationMs === 'function'
            ? resolveCueDuration(director.getAvatarFloatingNarrationDurationMs(
                scene && scene.voiceKey,
                scene && scene.text
            ), 0)
            : 0;
        const timing = resolveTutorialStandInTiming();
        const hideMs = timing.fadeOutMs;
        const appearMs = timing.approachMs;
        const holdMs = timing.holdMs;
        const entryMs = hideMs + appearMs;
        const exitMs = hideMs + timing.returnMs;
        const totalDurationMs = entryMs + holdMs;
        const fullDurationMs = totalDurationMs + exitMs;
        const rawDelayMs = Number.isFinite(Number(cue.delay))
            ? Number(cue.delay)
            : Number(cue.delayMs);
        const preferredDelayMs = Math.max(0, Number.isFinite(rawDelayMs) ? rawDelayMs : 0);
        const delayMs = narrationDurationMs > 0
            ? Math.min(preferredDelayMs, Math.max(0, narrationDurationMs - fullDurationMs))
            : preferredDelayMs;
        return Object.assign({}, cue, {
            delay: delayMs,
            hideMs: hideMs,
            appearMs: appearMs,
            holdMs: holdMs,
            totalDurationMs: totalDurationMs,
            fullDurationMs: fullDurationMs
        });
    }

    class AvatarStandInController {
        constructor(director) {
            this.director = director;
        }

        getCue(day, sceneId) {
            const api = root && root.YuiGuideAvatarStandIn ? root.YuiGuideAvatarStandIn : null;
            if (!api || typeof api.getCue !== 'function') {
                return null;
            }
            return api.getCue(day, sceneId);
        }

        schedule(scene, day, sceneRunId) {
            const director = this.director;
            if (!director || !scene) {
                return false;
            }
            if (scene.petalTransition === true) {
                this.clear({ clearPending: true, restoreModel: true });
                return false;
            }
            const rawCue = this.getCue(day, scene.id);
            if (!rawCue) {
                return false;
            }
            const cue = resolveCueTiming(rawCue, scene, director);
            this.clear({ clearPending: true, restoreModel: true });
            const token = director.avatarStandInToken + 1;
            director.avatarStandInToken = token;
            const rawDelayMs = Number.isFinite(Number(cue.delay))
                ? Number(cue.delay)
                : Number(cue.delayMs);
            const delayMs = Math.max(0, Number.isFinite(rawDelayMs) ? rawDelayMs : 0);
            director.avatarStandInShowTimer = root.setTimeout(() => {
                director.avatarStandInShowTimer = null;
                if (
                    token !== director.avatarStandInToken
                    || sceneRunId !== director.sceneRunId
                    || director.isStopping()
                ) {
                    return;
                }
                director.showAvatarStandIn(cue, token);
            }, delayMs);
            return true;
        }

        clear(options) {
            const director = this.director;
            if (!director) {
                return false;
            }
            const normalizedOptions = options || {};
            if (normalizedOptions.clearPending !== false && director.avatarStandInShowTimer) {
                root.clearTimeout(director.avatarStandInShowTimer);
                director.avatarStandInShowTimer = null;
            }
            if (director.avatarStandInHideTimer) {
                root.clearTimeout(director.avatarStandInHideTimer);
                director.avatarStandInHideTimer = null;
            }
            if (normalizedOptions.preserveToken !== true) {
                director.avatarStandInToken += 1;
            }
            if (typeof director.stopAvatarStandInPerformance === 'function') {
                try {
                    Promise.resolve(director.stopAvatarStandInPerformance('avatar_standin_clear')).catch(() => {});
                } catch (_) {}
            }
            director.avatarStandInActive = false;
            return true;
        }
    }

    return {
        AvatarStandInController,
        resolveCueTiming
    };
});
