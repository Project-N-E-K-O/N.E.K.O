(function () {
    'use strict';

    const STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const ICEBREAKER_STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    const ICEBREAKER_RESET_EVENT = 'neko:new-user-icebreaker-reset';
    const RESET_EVENT = 'neko:avatar-floating-guide-reset';
    const RESET_BROADCAST_KEY = 'neko_avatar_floating_guide_reset_event';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1'];
    const HOME_MANUAL_INTENT_KEY = 'neko_tutorial_home_yui_v1_manual_intent';
    const ROUND_COUNT = 7;
    const RESET_HISTORY_LIMIT = 20;

    function getTodayLocalDate() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function normalizeRound(day) {
        const round = Number(day);
        if (!Number.isInteger(round) || round < 1 || round > ROUND_COUNT) {
            throw new Error(`Invalid tutorial day: ${day}`);
        }
        return round;
    }

    function normalizeRoundList(value) {
        if (!Array.isArray(value)) return [];

        return Array.from(new Set(
            value
                .map(item => Number(item))
                .filter(item => Number.isInteger(item) && item >= 1 && item <= ROUND_COUNT)
        )).sort((left, right) => left - right);
    }

    function normalizeOptionalRound(value) {
        const round = Number(value);
        return Number.isInteger(round) && round >= 1 && round <= ROUND_COUNT ? round : null;
    }

    function omitRound(value, round) {
        return normalizeRoundList(value).filter(item => item !== round);
    }

    function loadGuideState() {
        let parsed = {};
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            parsed = raw ? JSON.parse(raw) : {};
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 状态读取失败，使用空状态:', error);
            parsed = {};
        }

        return {
            version: 1,
            firstSeenDate: parsed.firstSeenDate || getTodayLocalDate(),
            completedRounds: normalizeRoundList(parsed.completedRounds),
            skippedRounds: normalizeRoundList(parsed.skippedRounds),
            currentRound: normalizeOptionalRound(parsed.currentRound),
            pendingRound: normalizeOptionalRound(parsed.pendingRound),
            manualResetRound: normalizeOptionalRound(parsed.manualResetRound),
            lastAutoShownRound: normalizeOptionalRound(parsed.lastAutoShownRound),
            lastAutoShownDate: parsed.lastAutoShownDate || '',
            lastEndState: parsed.lastEndState && typeof parsed.lastEndState === 'object' ? parsed.lastEndState : null,
            updatedAt: parsed.updatedAt || null,
            resetHistory: Array.isArray(parsed.resetHistory) ? parsed.resetHistory.slice(-RESET_HISTORY_LIMIT) : [],
        };
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

    function resetIcebreakerDay(day) {
        const round = normalizeRound(day);
        const key = String(round);
        let store = { version: 1, days: {} };
        try {
            const raw = localStorage.getItem(ICEBREAKER_STORAGE_KEY);
            store = raw ? JSON.parse(raw) : store;
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 破冰状态读取失败，使用空状态:', error);
        }

        if (!store || typeof store !== 'object') {
            store = { version: 1, days: {} };
        }
        if (!store.days || typeof store.days !== 'object') {
            store.days = {};
        }
        delete store.days[key];

        try {
            localStorage.setItem(ICEBREAKER_STORAGE_KEY, JSON.stringify(store));
            window.dispatchEvent(new CustomEvent(ICEBREAKER_RESET_EVENT, {
                detail: { day: round },
            }));
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 破冰状态重置失败:', error);
        }
    }

    function resetAllIcebreakerDays() {
        try {
            localStorage.setItem(ICEBREAKER_STORAGE_KEY, JSON.stringify({
                version: 1,
                days: {},
            }));
            window.dispatchEvent(new CustomEvent(ICEBREAKER_RESET_EVENT, {
                detail: { day: 'all' },
            }));
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 破冰状态重置失败:', error);
        }
    }

    function dispatchGuideResetEvent(detail) {
        window.dispatchEvent(new CustomEvent(RESET_EVENT, { detail }));

        try {
            localStorage.setItem(RESET_BROADCAST_KEY, JSON.stringify({
                day: detail.day,
                source: detail.source,
                resetAt: detail.resetAt,
            }));
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 跨窗口重置广播失败:', error);
        }
    }

    function resetGuideRoundState(day, options = {}) {
        const round = normalizeRound(day);
        const resetAt = new Date().toISOString();
        const source = options.source || 'home_reset_button';
        const state = loadGuideState();

        resetIcebreakerDay(round);
        state.completedRounds = omitRound(state.completedRounds, round);
        state.skippedRounds = omitRound(state.skippedRounds, round);
        if (state.currentRound === round) {
            state.currentRound = null;
        }
        if (state.lastAutoShownRound === round) {
            state.lastAutoShownRound = null;
            state.lastAutoShownDate = '';
        }
        if (state.lastEndState && state.lastEndState.day === round) {
            state.lastEndState = null;
        }
        state.pendingRound = round;
        state.manualResetRound = round;
        state.updatedAt = resetAt;
        state.resetHistory = state.resetHistory.concat([{ day: round, source, resetAt }]).slice(-RESET_HISTORY_LIMIT);

        if (!saveGuideState(state)) {
            return null;
        }
        dispatchGuideResetEvent({ day: round, source, resetAt, state });
        return state;
    }

    function resetAllGuideRoundState(options = {}) {
        const resetAt = new Date().toISOString();
        const source = options.source || 'all_tutorial_reset';
        const state = loadGuideState();

        resetAllIcebreakerDays();
        state.firstSeenDate = getTodayLocalDate();
        state.completedRounds = [];
        state.skippedRounds = [];
        state.currentRound = null;
        state.pendingRound = 1;
        state.manualResetRound = 1;
        state.lastAutoShownRound = null;
        state.lastAutoShownDate = '';
        state.lastEndState = null;
        state.updatedAt = resetAt;
        state.resetHistory = state.resetHistory.concat([{ day: 'all', source, resetAt }]).slice(-RESET_HISTORY_LIMIT);

        if (!saveGuideState(state)) {
            return null;
        }
        dispatchGuideResetEvent({ day: 'all', source, resetAt, state });
        return state;
    }

    function clearHomeTutorialPromptResetState(day) {
        const round = normalizeRound(day);
        if (round === 1) {
            HOME_TUTORIAL_KEYS.forEach(key => localStorage.removeItem(key));
            localStorage.setItem(HOME_MANUAL_INTENT_KEY, 'true');
        } else {
            HOME_TUTORIAL_KEYS.forEach(key => localStorage.setItem(key, 'true'));
            localStorage.removeItem(HOME_MANUAL_INTENT_KEY);
        }
    }

    function getTutorialAvatarManager() {
        const manager = window.universalTutorialManager || null;
        if (!manager || typeof manager.startAvatarFloatingGuideRound !== 'function') {
            return null;
        }
        return manager;
    }

    function waitForTutorialAvatarManager(timeoutMs = 4000) {
        const existing = getTutorialAvatarManager();
        if (existing) return Promise.resolve(existing);

        if (typeof window.initUniversalTutorialManager === 'function' &&
            !window.__universalTutorialManagerInitialized) {
            window.initUniversalTutorialManager().then(initialized => {
                if (initialized !== false) {
                    window.__universalTutorialManagerInitialized = true;
                }
            }).catch(error => {
                console.warn('[AvatarFloatingGuideReset] 初始化教程管理器失败:', error);
            });
        }

        const startedAt = Date.now();
        return new Promise(resolve => {
            const timer = setInterval(() => {
                const manager = getTutorialAvatarManager();
                if (manager) {
                    clearInterval(timer);
                    resolve(manager);
                    return;
                }
                if (Date.now() - startedAt >= timeoutMs) {
                    clearInterval(timer);
                    resolve(null);
                }
            }, 100);
        });
    }

    async function startFormalAvatarFloatingGuideRound(day, options = {}) {
        const round = normalizeRound(day);
        const manager = await waitForTutorialAvatarManager();
        if (!manager || typeof manager.startAvatarFloatingGuideRound !== 'function') {
            throw new Error('avatar_floating_formal_manager_unavailable');
        }
        return manager.startAvatarFloatingGuideRound(round, {
            source: options.source || 'home_reset_button',
        });
    }

    async function resetHomeTutorialDay(day, options = {}) {
        const round = normalizeRound(day);
        const source = options.source || 'home_reset_button';
        let state = null;
        const manager = window.universalTutorialManager || null;
        if (manager && typeof manager.resetAvatarFloatingGuideRoundState === 'function') {
            state = manager.resetAvatarFloatingGuideRoundState(round, options);
            resetIcebreakerDay(round);
            dispatchGuideResetEvent({
                day: round,
                source,
                resetAt: state && state.updatedAt ? state.updatedAt : new Date().toISOString(),
                state,
            });
        } else {
            state = resetGuideRoundState(round, options);
        }

        clearHomeTutorialPromptResetState(round);

        showResetToast(round);
        return state;
    }

    async function resetAllAvatarFloatingGuideDays(options = {}) {
        const state = resetAllGuideRoundState(options);
        clearHomeTutorialPromptResetState(1);
        return state;
    }

    async function startAvatarFloatingGuideDay(day, options = {}) {
        return startFormalAvatarFloatingGuideRound(day, {
            source: options.source || 'home_reset_button',
        });
    }

    function translateResetMessage(key, fallback, options = {}) {
        let message = fallback;
        if (typeof window.t === 'function') {
            const translated = window.t(key, options);
            if (typeof translated === 'string' && translated && translated !== key) {
                message = translated;
            }
        }
        return String(message || '').replace(/\{\{\s*day\s*\}\}/g, String(options.day || ''));
    }

    function showResetToast(day) {
        const message = translateResetMessage(
            'tutorial.reset.daySuccess',
            '已重置第 {{day}} 天新手教程，请刷新 Neko 后启动。',
            { day }
        );
        if (typeof window.showTutorialResetNotice === 'function') {
            void window.showTutorialResetNotice(message);
            return;
        }
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, 2500, { priority: 1 });
            return;
        }
        if (typeof window.alert === 'function') {
            window.alert(message);
            return;
        }
        console.log('[AvatarFloatingGuideReset]', message);
    }

    function bindResetButtons(root = document) {
        const buttons = Array.from(root.querySelectorAll('[data-home-tutorial-reset-day]'));
        buttons.forEach(button => {
            if (button.dataset.tutorialResetBound === 'true') return;
            button.dataset.tutorialResetBound = 'true';
            button.addEventListener('click', async () => {
                const day = Number(button.dataset.homeTutorialResetDay);
                button.disabled = true;
                try {
                    await resetHomeTutorialDay(day, {
                        source: 'memory_browser_reset_button',
                    });
                } catch (error) {
                    console.error('[AvatarFloatingGuideReset] 重置失败:', error);
                    const message = translateResetMessage(
                        'tutorial.reset.dayFailed',
                        '新手教程重置失败，请稍后再试。',
                        { day }
                    );
                    if (typeof window.showTutorialResetNotice === 'function') {
                        void window.showTutorialResetNotice(message, { variant: 'error' });
                    } else if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(
                            message,
                            3000,
                            { priority: 2 }
                        );
                    }
                } finally {
                    button.disabled = false;
                }
            });
        });
    }

    function bootstrap() {
        bindResetButtons();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
    } else {
        bootstrap();
    }

    window.AvatarFloatingGuideReset = {
        STORAGE_KEY,
        RESET_EVENT,
        loadGuideState,
        resetGuideRoundState,
        resetAllGuideRoundState,
        startAvatarFloatingGuideDay,
        resetAllAvatarFloatingGuideDays,
        resetAvatarFloatingGuideDay: resetHomeTutorialDay,
        resetHomeTutorialDay,
        bindResetButtons,
    };
    window.resetHomeTutorialDay = resetHomeTutorialDay;
    window.resetAvatarFloatingGuideDay = resetHomeTutorialDay;
    window.resetAllAvatarFloatingGuideDays = resetAllAvatarFloatingGuideDays;
    window.startAvatarFloatingGuideDay = startAvatarFloatingGuideDay;
})();
