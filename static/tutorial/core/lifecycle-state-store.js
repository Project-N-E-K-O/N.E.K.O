(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialLifecycleStores = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    class TutorialLifecycleStateStore {
        constructor() {
            this.resetEndReason();
        }

        normalizeRawReason(reason) {
            const normalized = typeof reason === 'string' ? reason.trim().toLowerCase() : '';
            return normalized || 'destroy';
        }

        normalizeReason(reason) {
            const normalized = this.normalizeRawReason(reason);

            if (normalized === 'complete') {
                return 'complete';
            }

            if (normalized === 'skip' || normalized === 'escape' || normalized === 'angry_exit') {
                return 'skip';
            }

            return 'destroy';
        }

        setEndReason(reason) {
            if (this.endRawReason) {
                return this.endReason || 'destroy';
            }

            const rawReason = this.normalizeRawReason(reason);
            this.endRawReason = rawReason;
            this.endReason = this.normalizeReason(rawReason);
            return this.endReason;
        }

        resolveEndMeta(options) {
            const normalizedOptions = options || {};
            const finalSteps = Array.isArray(normalizedOptions.finalSteps)
                ? normalizedOptions.finalSteps
                : [];
            const currentStep = Number.isFinite(normalizedOptions.currentStep)
                ? normalizedOptions.currentStep
                : -1;

            if (this.endReason || this.endRawReason) {
                return {
                    reason: this.endReason || 'destroy',
                    rawReason: this.endRawReason || this.endReason || 'destroy'
                };
            }

            if (finalSteps.length > 0 && currentStep >= finalSteps.length - 1) {
                return {
                    reason: 'complete',
                    rawReason: 'complete'
                };
            }

            return {
                reason: 'destroy',
                rawReason: 'destroy'
            };
        }

        createYuiGuideEndDetail(options) {
            const normalizedOptions = options || {};
            const rawReason = this.normalizeRawReason(normalizedOptions.reason);

            return {
                page: normalizedOptions.page || '',
                runtimePage: normalizedOptions.runtimePage || '',
                reason: this.normalizeReason(rawReason),
                rawReason: rawReason
            };
        }

        createTerminationRequest(options) {
            const normalizedOptions = options || {};
            const sourcePage = typeof normalizedOptions.sourcePage === 'string'
                ? normalizedOptions.sourcePage.trim()
                : '';
            if (!sourcePage || sourcePage === 'home') {
                return null;
            }

            const rawReason = this.normalizeRawReason(
                normalizedOptions.rawReason || normalizedOptions.reason || 'destroy'
            );

            return {
                action: 'yui_guide_request_termination',
                sourcePage: sourcePage,
                targetPage: 'home',
                reason: rawReason,
                tutorialReason: rawReason,
                timestamp: Number.isFinite(normalizedOptions.timestamp)
                    ? normalizedOptions.timestamp
                    : Date.now()
            };
        }

        resetEndReason() {
            this.endReason = null;
            this.endRawReason = null;
        }

        getEndRawReason() {
            return this.endRawReason;
        }

        getEndReason() {
            return this.endReason;
        }
    }

    return {
        TutorialLifecycleStateStore
    };
});
