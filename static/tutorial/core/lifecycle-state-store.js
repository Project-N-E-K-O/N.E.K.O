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

    class HomeTutorialPromptLifecycleStateStore {
        constructor(stateRef, heartbeatTokenFactory) {
            this.state = stateRef;
            this.createHeartbeatToken = typeof heartbeatTokenFactory === 'function'
                ? heartbeatTokenFactory
                : function noopHeartbeatToken() { return ''; };
        }

        setPromptDrivenTutorialToken(token) {
            this.state.promptDrivenTutorialToken = token || null;
        }

        clearPromptDrivenTutorialToken() {
            this.setPromptDrivenTutorialToken(null);
        }

        setTutorialRunToken(token) {
            this.state.tutorialRunToken = token || null;
        }

        clearTutorialRunToken() {
            this.setTutorialRunToken(null);
        }

        setHomeTutorialCompleted(completed) {
            this.state.homeTutorialCompleted = completed === true;
        }

        markHomeTutorialCompleted() {
            this.setHomeTutorialCompleted(true);
        }

        markManualHomeTutorialViewed() {
            this.state.manualHomeTutorialViewed = true;
        }

        resetTutorialCompletionState() {
            this.state.homeTutorialCompleted = false;
            this.state.manualHomeTutorialViewed = false;
            this.state.tutorialStarted = false;
            this.state.tutorialRunning = false;
            this.state.tutorialStartRequested = false;
        }

        clearHeartbeatCompletionMarkers(snapshot) {
            if (!snapshot) {
                return;
            }
            snapshot.homeTutorialCompleted = false;
            snapshot.manualHomeTutorialViewed = false;
        }

        createHeartbeatSnapshot(metrics) {
            const normalizedMetrics = metrics || {};
            const snapshot = {
                foregroundMsDelta: normalizedMetrics.foregroundMsDelta || 0,
                homeInteractionsDelta: normalizedMetrics.homeInteractionsDelta || 0,
                chatTurnsDelta: normalizedMetrics.chatTurnsDelta || 0,
                voiceSessionsDelta: normalizedMetrics.voiceSessionsDelta || 0,
                homeTutorialCompleted: this.state.homeTutorialCompleted,
                manualHomeTutorialViewed: this.state.manualHomeTutorialViewed,
                unloadQueued: false,
            };

            if (this.hasReplaySensitiveHeartbeatMetrics(snapshot)) {
                snapshot.heartbeatToken = typeof this.createHeartbeatToken === 'function'
                    ? this.createHeartbeatToken()
                    : '';
            }

            return snapshot;
        }

        hasReplaySensitiveHeartbeatMetrics(snapshot) {
            if (!snapshot) {
                return false;
            }

            return snapshot.foregroundMsDelta > 0
                || snapshot.homeInteractionsDelta > 0
                || snapshot.chatTurnsDelta > 0
                || snapshot.voiceSessionsDelta > 0;
        }

        shouldFlushHeartbeatSnapshot(snapshot) {
            if (!snapshot) {
                return false;
            }

            return this.hasReplaySensitiveHeartbeatMetrics(snapshot)
                || snapshot.homeTutorialCompleted
                || snapshot.manualHomeTutorialViewed;
        }

        buildHeartbeatPayload(snapshot) {
            const payload = {
                heartbeat_token: snapshot.heartbeatToken,
                foreground_ms_delta: snapshot.foregroundMsDelta,
                home_interactions_delta: snapshot.homeInteractionsDelta,
                chat_turns_delta: snapshot.chatTurnsDelta,
                voice_sessions_delta: snapshot.voiceSessionsDelta,
                home_tutorial_completed: snapshot.homeTutorialCompleted,
                manual_home_tutorial_viewed: snapshot.manualHomeTutorialViewed,
            };

            if (!snapshot.heartbeatToken) {
                delete payload.heartbeat_token;
            }

            return payload;
        }
    }

    return {
        TutorialLifecycleStateStore,
        HomeTutorialPromptLifecycleStateStore
    };
});
