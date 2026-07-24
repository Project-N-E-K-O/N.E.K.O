(function () {
    'use strict';

    const PC_OVERLAY_RUN_ID_STORAGE_KEY = 'yuiGuidePcOverlayRunId';
    const sevenDayState = window.NekoSevenDayTutorialState || null;
    if (!sevenDayState) {
        console.error('[TutorialBoot] 七日教程状态模块不可用，跳过启动预测');
        return;
    }

    const state = {
        predictedRound: null,
        userModelBootSkippedRound: null,
        userModelBootSkipped: false,
        directTutorialBootClaimed: false,
        predictionSuppressed: false,
        claimReason: ''
    };
    let overlayRunId = '';

    function normalizeRound(value) {
        return sevenDayState.normalizeRound(value);
    }

    function loadGuideState() {
        return sevenDayState.loadState();
    }

    function isAuthoritativeStateReady() {
        return typeof sevenDayState.isReady !== 'function' || sevenDayState.isReady();
    }

    function computePredictedRound() {
        const guideState = loadGuideState();
        return sevenDayState.getNextAutoRound(
            guideState,
            sevenDayState.getTodayLocalDate()
        );
    }

    function getPredictedRound() {
        state.predictedRound = computePredictedRound();
        return state.predictedRound;
    }

    function isTutorialBootAvailable() {
        return !(typeof window.innerWidth === 'number' && window.innerWidth <= 768);
    }

    function shouldBootIntoTutorial() {
        if (!isAuthoritativeStateReady()) {
            return false;
        }
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
        if (!isAuthoritativeStateReady()) {
            return state.directTutorialBootClaimed === true;
        }
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
