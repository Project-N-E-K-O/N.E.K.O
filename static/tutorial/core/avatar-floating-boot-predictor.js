(function () {
    'use strict';

    const AVATAR_FLOATING_GUIDE_STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1'];
    const PC_OVERLAY_RUN_ID_STORAGE_KEY = 'yuiGuidePcOverlayRunId';
    const ROUND_COUNT = 7;

    const state = {
        predictedRound: null,
        userModelBootSkippedRound: null,
        userModelBootSkipped: false,
        directTutorialBootClaimed: false,
        predictionSuppressed: false,
        claimReason: ''
    };
    let overlayRunId = '';

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

    async function recoverUserModelBoot(reason) {
        const shouldRecover = state.userModelBootSkipped || state.directTutorialBootClaimed;
        if (!shouldRecover) {
            return false;
        }
        state.predictionSuppressed = true;
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
        recoverUserModelBoot,
        wasUserModelBootSkipped() {
            return state.userModelBootSkipped === true;
        },
        isDirectTutorialBootClaimed() {
            return state.directTutorialBootClaimed === true;
        }
    };
})();
