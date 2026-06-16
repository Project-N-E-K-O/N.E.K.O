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

    class AvatarStandInController {
        constructor(director) {
            this.director = director;
        }

        getCue(day, sceneId) {
            const api = root && root.YuiGuideAvatarStandIn ? root.YuiGuideAvatarStandIn : null;
            if (!api || typeof api.getCue !== 'function') {
                return null;
            }
            try {
                return api.getCue(day, sceneId);
            } catch (_) {
                return null;
            }
        }

        normalizeSceneCue(scene) {
            const config = scene && scene.avatarStandIn;
            if (!config || typeof config !== 'object') {
                return null;
            }
            const resource = typeof config.resource === 'string' ? config.resource.trim() : '';
            const position = typeof config.position === 'string' ? config.position.trim() : '';
            if (!resource || !position) {
                return null;
            }
            const delayMs = Number.isFinite(Number(config.delayMs))
                ? Math.max(0, Math.floor(Number(config.delayMs)))
                : 900;
            const durationMs = Number.isFinite(Number(config.durationMs))
                ? Math.max(0, Math.floor(Number(config.durationMs)))
                : 5000;
            return {
                delayMs,
                durationMs,
                resource,
                position
            };
        }

        resolveCue(scene, day) {
            const sceneCue = this.normalizeSceneCue(scene);
            return sceneCue || this.getCue(day, scene && scene.id);
        }

        schedule(scene, day, sceneRunId) {
            const director = this.director;
            if (!scene || scene.petalTransition === true) {
                director.clearAvatarStandIn({ clearPending: true, restoreModel: true });
                return;
            }
            const cue = this.resolveCue(scene, day);
            if (!cue) {
                return;
            }
            if (director.avatarStandInShowTimer) {
                root.clearTimeout(director.avatarStandInShowTimer);
                director.avatarStandInShowTimer = null;
            }
            const token = director.avatarStandInToken + 1;
            director.avatarStandInToken = token;
            director.avatarStandInShowTimer = root.setTimeout(() => {
                director.avatarStandInShowTimer = null;
                if (
                    token !== director.avatarStandInToken
                    || sceneRunId !== director.sceneRunId
                    || director.isStopping()
                    || director.destroyed
                ) {
                    return;
                }
                director.showAvatarStandIn(cue, token);
            }, Math.max(0, Number(cue.delayMs) || 0));
        }

        clear(options) {
            const director = this.director;
            const normalizedOptions = options || {};
            if (normalizedOptions.clearPending !== false && director.avatarStandInShowTimer) {
                root.clearTimeout(director.avatarStandInShowTimer);
                director.avatarStandInShowTimer = null;
            }
            if (director.avatarStandInFadeTimer) {
                root.clearTimeout(director.avatarStandInFadeTimer);
                director.avatarStandInFadeTimer = null;
            }
            if (director.avatarStandInHideTimer) {
                root.clearTimeout(director.avatarStandInHideTimer);
                director.avatarStandInHideTimer = null;
            }
            if (normalizedOptions.preserveToken !== true) {
                director.avatarStandInToken += 1;
            }
            if (director.overlay && typeof director.overlay.clearAvatarStandIn === 'function') {
                director.overlay.clearAvatarStandIn();
            }
            if (normalizedOptions.restoreModel !== false) {
                const restores = Array.isArray(director.avatarStandInOpacityRestores)
                    ? director.avatarStandInOpacityRestores
                    : [];
                director.avatarStandInOpacityRestores = null;
                restores.forEach((restore) => {
                    try {
                        restore();
                    } catch (_) {}
                });
            }
            director.avatarStandInActive = false;
        }
    }

    return {
        AvatarStandInController
    };
});
