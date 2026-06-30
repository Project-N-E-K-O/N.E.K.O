(function () {
    'use strict';

    const AVATAR_FLOATING_GUIDE_STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1', 'neko_tutorial_home'];
    const ROUND_COUNT = 7;

    const state = {
        predictedRound: null,
        userModelBootSkipped: false,
        directTutorialBootClaimed: false,
        predictionSuppressed: false,
        claimReason: ''
    };

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
        if (guideState.pendingRound && !completed.has(guideState.pendingRound) && !skipped.has(guideState.pendingRound)) {
            return guideState.pendingRound;
        }
        if (!completed.has(1) && !skipped.has(1)) {
            return 1;
        }
        if (guideState.lastAutoShownDate === getTodayLocalDate()) {
            return null;
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

    function shouldBootIntoTutorial() {
        if (state.predictionSuppressed) {
            return false;
        }
        return !!getPredictedRound();
    }

    function shouldSkipUserModelBoot() {
        return shouldBootIntoTutorial() || state.directTutorialBootClaimed === true;
    }

    function markUserModelBootSkipped(reason) {
        state.userModelBootSkipped = true;
        state.predictionSuppressed = false;
        state.claimReason = reason || state.claimReason || 'user-model-boot-skipped';
        return true;
    }

    function claimDirectTutorialBoot(round, reason) {
        const normalizedRound = normalizeRound(round);
        state.predictionSuppressed = false;
        state.predictedRound = normalizedRound || getPredictedRound();
        state.directTutorialBootClaimed = !!state.predictedRound;
        state.claimReason = reason || 'direct-tutorial-boot';
        return state.directTutorialBootClaimed;
    }

    function releaseDirectTutorialBoot(reason) {
        state.directTutorialBootClaimed = false;
        state.userModelBootSkipped = false;
        state.claimReason = reason || '';
    }

    async function recoverUserModelBoot(reason) {
        const shouldRecover = state.userModelBootSkipped || state.directTutorialBootClaimed;
        if (!shouldRecover) {
            return false;
        }
        state.predictionSuppressed = true;
        releaseDirectTutorialBoot(reason || 'recover-user-model');
        if (typeof window.showCurrentModel === 'function') {
            await window.showCurrentModel();
            return true;
        }
        const modelType = String(window.lanlan_config && window.lanlan_config.model_type || 'live2d').toLowerCase();
        const subType = String(window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
        if ((modelType === 'live3d' && subType === 'mmd') && typeof window.initMMDModel === 'function') {
            await window.initMMDModel();
            return true;
        }
        if ((modelType === 'vrm' || modelType === 'live3d') && typeof window.initVRMModel === 'function') {
            await window.initVRMModel();
            return true;
        }
        if (typeof window.initLive2DModel === 'function') {
            await window.initLive2DModel();
            return true;
        }
        return false;
    }

    window.NekoAvatarFloatingBoot = {
        shouldBootIntoTutorial,
        shouldSkipUserModelBoot,
        getPredictedRound,
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
