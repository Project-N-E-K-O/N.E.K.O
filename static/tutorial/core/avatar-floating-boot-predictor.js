(function () {
    'use strict';

    const AVATAR_FLOATING_GUIDE_STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1'];
    const PC_OVERLAY_RUN_ID_STORAGE_KEY = 'yuiGuidePcOverlayRunId';
    const PC_OVERLAY_SEQUENCE_STORAGE_KEY = 'yuiGuidePcOverlaySequence';
    const PC_OVERLAY_LOADING_ICON = '/static/icons/emotion_model_icon.png';
    const PC_OVERLAY_LOADING_DISPLAY_SCOPE = 'primary';
    const ROUND_COUNT = 7;

    const state = {
        predictedRound: null,
        userModelBootSkippedRound: null,
        userModelBootSkipped: false,
        directTutorialBootClaimed: false,
        predictionSuppressed: false,
        loadingActive: false,
        claimReason: ''
    };
    let overlayRunId = '';
    let overlaySequence = 0;

    function getTodayLocalDate() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function normalizeRound(value) {
        const round = Number(value);
        return Number.isInteger(round) && round >= 1 && round <= ROUND_COUNT ? round : null;
    }

    function normalizeRoundList(value) {
        if (!Array.isArray(value)) {
            return [];
        }
        return Array.from(new Set(
            value
                .map(item => Number(item))
                .filter(item => Number.isInteger(item) && item >= 1 && item <= ROUND_COUNT)
        )).sort((left, right) => left - right);
    }

    function loadGuideState() {
        let parsed = {};
        try {
            const raw = window.localStorage && window.localStorage.getItem(AVATAR_FLOATING_GUIDE_STORAGE_KEY);
            parsed = raw ? JSON.parse(raw) : {};
        } catch (_) {
            parsed = {};
        }
        return {
            firstSeenDate: parsed.firstSeenDate || getTodayLocalDate(),
            completedRounds: normalizeRoundList(parsed.completedRounds),
            skippedRounds: normalizeRoundList(parsed.skippedRounds),
            pendingRound: normalizeRound(parsed.pendingRound),
            manualResetRound: normalizeRound(parsed.manualResetRound),
            lastAutoShownDate: parsed.lastAutoShownDate || ''
        };
    }

    function getDateDeltaDays(firstSeenDate, today) {
        const matchFirst = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(firstSeenDate || ''));
        const matchToday = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(today || ''));
        if (!matchFirst || !matchToday) {
            return 0;
        }
        const firstDate = new Date(Number(matchFirst[1]), Number(matchFirst[2]) - 1, Number(matchFirst[3]));
        const todayDate = new Date(Number(matchToday[1]), Number(matchToday[2]) - 1, Number(matchToday[3]));
        const diffMs = todayDate.getTime() - firstDate.getTime();
        return Number.isFinite(diffMs) ? Math.max(0, Math.floor(diffMs / 86400000)) : 0;
    }

    function hasLegacyHomeTutorialSeen() {
        try {
            return HOME_TUTORIAL_KEYS.some(key => window.localStorage && window.localStorage.getItem(key) === 'true');
        } catch (_) {
            return false;
        }
    }

    function computePredictedRound() {
        const guideState = loadGuideState();
        const completed = new Set(guideState.completedRounds);
        const skipped = new Set(guideState.skippedRounds);

        if (!completed.has(1) && hasLegacyHomeTutorialSeen()) {
            completed.add(1);
        }

        if (guideState.manualResetRound) {
            return guideState.manualResetRound;
        }
        if (guideState.lastAutoShownDate === getTodayLocalDate()) {
            return null;
        }
        if (guideState.pendingRound && !completed.has(guideState.pendingRound) && !skipped.has(guideState.pendingRound)) {
            return guideState.pendingRound;
        }
        if (!completed.has(1) && !skipped.has(1)) {
            return 1;
        }

        const maxDueRound = Math.min(ROUND_COUNT, getDateDeltaDays(guideState.firstSeenDate, getTodayLocalDate()) + 1);
        for (let round = 2; round <= maxDueRound; round += 1) {
            if (!completed.has(round) && !skipped.has(round)) {
                return round;
            }
        }
        return null;
    }

    function getPredictedRound() {
        state.predictedRound = computePredictedRound();
        return state.predictedRound;
    }

    function isTutorialBootAvailable() {
        return !(typeof window.innerWidth === 'number' && window.innerWidth <= 768);
    }

    function shouldBootIntoTutorial() {
        if (state.predictionSuppressed) {
            return false;
        }
        if (!isTutorialBootAvailable()) {
            state.predictedRound = null;
            return false;
        }
        return !!getPredictedRound();
    }

    function shouldSkipUserModelBoot() {
        if (!isTutorialBootAvailable()) {
            return false;
        }
        return shouldBootIntoTutorial() || state.directTutorialBootClaimed === true;
    }

    function markUserModelBootSkipped(reason) {
        state.userModelBootSkipped = true;
        state.userModelBootSkippedRound = state.predictedRound || getPredictedRound();
        state.claimReason = reason || state.claimReason || 'user-model-boot-skipped';
        return true;
    }

    function claimDirectTutorialBoot(round, reason) {
        if (!isTutorialBootAvailable()) {
            state.predictedRound = null;
            state.directTutorialBootClaimed = false;
            return false;
        }
        const normalizedRound = normalizeRound(round);
        state.predictionSuppressed = false;
        state.predictedRound = normalizedRound || getPredictedRound();
        state.directTutorialBootClaimed = !!state.predictedRound;
        state.claimReason = reason || 'direct-tutorial-boot';
        return state.directTutorialBootClaimed;
    }

    function releaseDirectTutorialBoot(reason, options) {
        const keepUserModelBootSkipped = options && options.keepUserModelBootSkipped === true;
        if (options && options.suppressPrediction === true) {
            state.predictionSuppressed = true;
        }
        state.directTutorialBootClaimed = false;
        if (!keepUserModelBootSkipped) {
            state.userModelBootSkipped = false;
            state.userModelBootSkippedRound = null;
        }
        state.claimReason = reason || '';
    }

    function createPcOverlayRunId() {
        return 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    }

    function ensurePcOverlayRunId() {
        if (overlayRunId) {
            return overlayRunId;
        }
        try {
            const stored = window.sessionStorage && window.sessionStorage.getItem(PC_OVERLAY_RUN_ID_STORAGE_KEY);
            overlayRunId = stored || createPcOverlayRunId();
            if (window.sessionStorage) {
                window.sessionStorage.setItem(PC_OVERLAY_RUN_ID_STORAGE_KEY, overlayRunId);
            }
        } catch (_) {
            overlayRunId = createPcOverlayRunId();
        }
        return overlayRunId;
    }

    function nextPcOverlaySequence() {
        try {
            const stored = window.sessionStorage && Number(window.sessionStorage.getItem(PC_OVERLAY_SEQUENCE_STORAGE_KEY));
            if (Number.isFinite(stored) && stored > overlaySequence) {
                overlaySequence = stored;
            }
        } catch (_) {}
        overlaySequence += 1;
        try {
            if (window.sessionStorage) {
                window.sessionStorage.setItem(PC_OVERLAY_SEQUENCE_STORAGE_KEY, String(overlaySequence));
            }
        } catch (_) {}
        return overlaySequence;
    }

    function isPcLoadingOverlayBridge(bridge) {
        return !!(
            bridge
            && typeof bridge === 'object'
            && typeof bridge.begin === 'function'
            && typeof bridge.update === 'function'
            && typeof bridge.clear === 'function'
        );
    }

    function getPcLoadingOverlayBridge() {
        if (isPcLoadingOverlayBridge(window.nekoTutorialLoadingOverlay)) {
            return window.nekoTutorialLoadingOverlay;
        }
        if (window.nekoTutorialOverlay && isPcLoadingOverlayBridge(window.nekoTutorialOverlay.loadingOverlay)) {
            return window.nekoTutorialOverlay.loadingOverlay;
        }
        if (isPcLoadingOverlayBridge(window.nekoTutorialOverlay)) {
            return window.nekoTutorialOverlay;
        }
        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.beginLoading === 'function'
            && typeof window.nekoTutorialOverlay.updateLoading === 'function'
            && typeof window.nekoTutorialOverlay.clearLoading === 'function'
        ) {
            return {
                begin: payload => window.nekoTutorialOverlay.beginLoading(payload),
                update: payload => window.nekoTutorialOverlay.updateLoading(payload),
                clear: payload => window.nekoTutorialOverlay.clearLoading(payload)
            };
        }
        return null;
    }

    function beginDirectTutorialLoading(reason) {
        const bridge = getPcLoadingOverlayBridge();
        if (!bridge || typeof bridge.begin !== 'function' || typeof bridge.update !== 'function') {
            return false;
        }
        const tutorialRunId = ensurePcOverlayRunId();
        const sequence = nextPcOverlaySequence();
        state.loadingActive = true;
        try {
            const beginResult = bridge.begin({
                tutorialRunId,
                reason: reason || 'direct-tutorial-loading',
                displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE
            });
            if (beginResult && typeof beginResult.catch === 'function') {
                beginResult.catch(() => {
                    state.loadingActive = false;
                });
            }
            const updateResult = bridge.update({
                tutorialRunId,
                sequence,
                displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE,
                payload: {
                    loading: {
                        visible: true,
                        reason: reason || 'direct-tutorial-loading',
                        displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE,
                        emotionIconUrl: PC_OVERLAY_LOADING_ICON
                    }
                }
            });
            if (updateResult && typeof updateResult.catch === 'function') {
                updateResult.catch(() => {
                    state.loadingActive = false;
                });
            }
            return true;
        } catch (_) {
            state.loadingActive = false;
            return false;
        }
    }

    function clearDirectTutorialLoading(reason) {
        const bridge = getPcLoadingOverlayBridge();
        if (!state.loadingActive && !overlayRunId) {
            return false;
        }
        state.loadingActive = false;
        if (!bridge) {
            return false;
        }
        const tutorialRunId = ensurePcOverlayRunId();
        const sequence = nextPcOverlaySequence();
        try {
            if (typeof bridge.update === 'function') {
                const updateResult = bridge.update({
                    tutorialRunId,
                    sequence,
                    displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE,
                    payload: {
                        loading: null,
                        reason: reason || 'direct-tutorial-loading-clear',
                        displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE
                    }
                });
                if (updateResult && typeof updateResult.catch === 'function') {
                    updateResult.catch(() => {});
                }
            }
        } catch (_) {}
        try {
            if (typeof bridge.clear === 'function') {
                const clearResult = bridge.clear({
                    tutorialRunId,
                    reason: reason || 'direct-tutorial-loading-clear',
                    displayScope: PC_OVERLAY_LOADING_DISPLAY_SCOPE
                });
                if (clearResult && typeof clearResult.catch === 'function') {
                    clearResult.catch(() => {});
                }
            }
        } catch (_) {}
        return true;
    }

    async function recoverUserModelBoot(reason) {
        const shouldRecover = state.userModelBootSkipped || state.directTutorialBootClaimed;
        if (!shouldRecover) {
            return false;
        }
        state.predictionSuppressed = true;
        clearDirectTutorialLoading(reason || 'recover-user-model');
        releaseDirectTutorialBoot(reason || 'recover-user-model', {
            keepUserModelBootSkipped: true,
            suppressPrediction: true
        });
        const modelType = String(window.lanlan_config && window.lanlan_config.model_type || 'live2d').toLowerCase();
        const subType = String(window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
        try {
            const isPngtuberModel = modelType === 'pngtuber';
            if (isPngtuberModel) {
                if (typeof window.loadPNGTuberAvatar === 'function') {
                    await window.loadPNGTuberAvatar(window.lanlan_config && window.lanlan_config.pngtuber || {});
                    if (window.pngtuberManager && typeof window.pngtuberManager.show === 'function') {
                        window.pngtuberManager.show();
                    }
                    return true;
                }
                if (typeof window.showCurrentModel === 'function') {
                    await window.showCurrentModel();
                    return true;
                }
                return false;
            }
            const isMmdModel = modelType === 'live3d' && subType === 'mmd';
            if (isMmdModel) {
                if (typeof window.autoInitMMDOnMainPage === 'function') {
                    await window.autoInitMMDOnMainPage();
                    if (window.mmdManager && window.mmdManager.currentModel) {
                        return true;
                    }
                }
                if (typeof window.initMMDModel === 'function') {
                    await window.initMMDModel();
                }
                if (typeof window.showCurrentModel === 'function') {
                    await window.showCurrentModel();
                    return true;
                }
                return false;
            } else if ((modelType === 'vrm' || modelType === 'live3d') && typeof window.initVRMModel === 'function') {
                await window.initVRMModel();
                return true;
            }
            if (typeof window.initLive2DModel === 'function') {
                await window.initLive2DModel();
                return true;
            }
            if (typeof window.showCurrentModel === 'function') {
                await window.showCurrentModel();
                return true;
            }
            return false;
        } finally {
            releaseDirectTutorialBoot(reason || 'recover-user-model');
        }
    }

    window.NekoAvatarFloatingBoot = {
        shouldBootIntoTutorial,
        shouldSkipUserModelBoot,
        getPredictedRound,
        getSkippedUserModelBootRound() {
            return state.userModelBootSkippedRound;
        },
        markUserModelBootSkipped,
        claimDirectTutorialBoot,
        releaseDirectTutorialBoot,
        beginDirectTutorialLoading,
        clearDirectTutorialLoading,
        recoverUserModelBoot,
        wasUserModelBootSkipped() {
            return state.userModelBootSkipped === true;
        },
        isDirectTutorialBootClaimed() {
            return state.directTutorialBootClaimed === true;
        }
    };
})();
