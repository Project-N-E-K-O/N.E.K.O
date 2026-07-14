(function (namespace) {
    'use strict';

    class CursorAnchorStore {
        constructor() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }

        rememberScenePoint(sceneId, point) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            if (
                !normalizedSceneId
                || !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.scenePoints[normalizedSceneId] = {
                x: point.x,
                y: point.y
            };
            return true;
        }

        getScenePoint(sceneIds) {
            const candidates = Array.isArray(sceneIds) ? sceneIds : [sceneIds];
            for (let index = 0; index < candidates.length; index += 1) {
                const sceneId = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                const point = sceneId ? this.scenePoints[sceneId] : null;
                if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) {
                    return {
                        x: point.x,
                        y: point.y
                    };
                }
            }
            return null;
        }

        rememberLatestExternalizedPoint(point) {
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.latestExternalizedPoint = {
                x: point.x,
                y: point.y,
                at: Number(point.at) || Date.now(),
                kind: typeof point.kind === 'string' ? point.kind : '',
                effect: typeof point.effect === 'string' ? point.effect : '',
                effectDurationMs: Number.isFinite(point.effectDurationMs)
                    ? Math.max(0, Math.floor(point.effectDurationMs))
                    : 0,
                settled: point.settled === true
            };
            return true;
        }

        getLatestExternalizedPoint(maxAgeMs) {
            const point = this.latestExternalizedPoint;
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return null;
            }
            const latestAt = Number(point.at);
            const ageLimit = Number.isFinite(maxAgeMs) ? maxAgeMs : 30000;
            if (Number.isFinite(latestAt) && Date.now() - latestAt > ageLimit) {
                return null;
            }
            return {
                x: point.x,
                y: point.y
            };
        }

        clear() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }
    }

    namespace.CursorAnchorStore = CursorAnchorStore;
})(window.__YuiGuideDirector);
