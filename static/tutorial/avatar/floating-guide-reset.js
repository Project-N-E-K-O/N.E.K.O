(function () {
    'use strict';

    const STATE_API = window.NekoSevenDayTutorialState || null;
    if (!STATE_API) {
        console.error('[AvatarFloatingGuideReset] seven-day tutorial state is unavailable');
        return;
    }
    const STORAGE_KEY = STATE_API.STORAGE_KEY;
    const ICEBREAKER_STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    const ICEBREAKER_RESET_EVENT = 'neko:new-user-icebreaker-reset';
    const RESET_EVENT = 'neko:avatar-floating-guide-reset';
    const RESET_BROADCAST_KEY = 'neko_avatar_floating_guide_reset_event';

    function normalizeRound(day) {
        const round = STATE_API.normalizeRound(day);
        if (!round) {
            throw new Error(`Invalid tutorial day: ${day}`);
        }
        return round;
    }

    function loadGuideState() {
        return STATE_API.loadState();
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
        const source = options.source || 'home_reset_button';

        resetIcebreakerDay(round);
        const state = STATE_API.resetRound(round, { source });
        dispatchGuideResetEvent({ day: round, source, resetAt: state.updatedAt, state });
        return state;
    }

    function resetAllGuideRoundState(options = {}) {
        const source = options.source || 'all_tutorial_reset';

        resetAllIcebreakerDays();
        const state = STATE_API.resetAll({ source });
        dispatchGuideResetEvent({ day: 'all', source, resetAt: state.updatedAt, state });
        return state;
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

        if (typeof STATE_API.flush === 'function') {
            await STATE_API.flush();
        }

        showResetToast(round);
        return state;
    }

    async function resetAllAvatarFloatingGuideDays(options = {}) {
        const state = resetAllGuideRoundState(options);
        if (typeof STATE_API.flush === 'function') {
            await STATE_API.flush();
        }
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
