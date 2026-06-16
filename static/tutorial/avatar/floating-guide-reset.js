(function () {
    'use strict';

    const STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const RESET_EVENT = 'neko:avatar-floating-guide-reset';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1', 'neko_tutorial_home'];
    const HOME_MANUAL_INTENT_KEY = 'neko_tutorial_home_manual_intent';
    const ROUND_COUNT = 7;

    function normalizeRound(day) {
        const round = Number(day);
        if (!Number.isInteger(round) || round < 1 || round > ROUND_COUNT) {
            throw new Error(`Invalid tutorial day: ${day}`);
        }
        return round;
    }

    function loadGuideState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] Failed to read guide state:', error);
            return {};
        }
    }

    function saveGuideState(state) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
            return true;
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] Failed to persist guide state:', error);
            return false;
        }
    }

    function clearHomeTutorialPromptResetState(round) {
        try {
            HOME_TUTORIAL_KEYS.forEach((key) => localStorage.removeItem(key));
            localStorage.setItem(HOME_MANUAL_INTENT_KEY, 'true');
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] Failed to reset home tutorial prompt state:', error);
        }
        window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
            detail: { page: 'home', round, reason: 'avatar_floating_guide_reset' },
        }));
    }

    function dispatchGuideResetEvent(detail) {
        window.dispatchEvent(new CustomEvent(RESET_EVENT, { detail }));
    }

    async function resetHomeTutorialDay(day, options = {}) {
        const round = normalizeRound(day);
        const resetAt = new Date().toISOString();
        const source = options.source || 'home_reset_button';
        const state = loadGuideState();
        const omitRound = (value) => Array.isArray(value) ? value.filter(item => Number(item) !== round) : [];

        clearHomeTutorialPromptResetState(round);
        state.completedRounds = omitRound(state.completedRounds);
        state.skippedRounds = omitRound(state.skippedRounds);
        if (Number(state.currentRound) === round) {
            state.currentRound = null;
        }
        if (Number(state.lastAutoShownRound) === round) {
            state.lastAutoShownRound = null;
            state.lastAutoShownDate = '';
        }
        state.pendingRound = round;
        state.manualResetRound = round;
        state.updatedAt = resetAt;
        state.resetHistory = Array.isArray(state.resetHistory) ? state.resetHistory.slice(-19) : [];
        state.resetHistory.push({ day: round, source, resetAt });
        saveGuideState(state);
        dispatchGuideResetEvent({ day: round, source, resetAt });
        return state;
    }

    async function startAvatarFloatingGuideDay(day, options = {}) {
        const round = normalizeRound(day);
        if (
            window.universalTutorialManager
            && typeof window.universalTutorialManager.startAvatarFloatingGuideRound === 'function'
        ) {
            return startFormalAvatarFloatingGuideRound(day, {
                source: options.source || 'home_reset_button'
            });
        }
        await resetHomeTutorialDay(round, options);
        return false;
    }

    async function startFormalAvatarFloatingGuideRound(day, options = {}) {
        const round = normalizeRound(day);
        if (
            !window.universalTutorialManager
            || typeof window.universalTutorialManager.startAvatarFloatingGuideRound !== 'function'
        ) {
            return false;
        }
        return window.universalTutorialManager.startAvatarFloatingGuideRound(round, {
            source: options.source || 'home_reset_button'
        });
    }

    window.AvatarFloatingGuideReset = {
        resetHomeTutorialDay,
        startAvatarFloatingGuideDay,
        startFormalAvatarFloatingGuideRound,
    };
})();
