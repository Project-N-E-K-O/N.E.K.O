(function () {
    'use strict';

    const STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    const DAY = 1;
    const HOST_WAIT_INTERVAL_MS = 80;
    const HOST_WAIT_TIMEOUT_MS = 5000;

    let activeSession = null;

    function now() {
        return Date.now();
    }

    function readStore() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            return parsed && typeof parsed === 'object' ? parsed : { days: {} };
        } catch (_) {
            return { days: {} };
        }
    }

    function writeStore(store) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
        } catch (_) {}
    }

    function updateDayEntry(patch) {
        const store = readStore();
        const days = store.days && typeof store.days === 'object' ? store.days : {};
        const key = String(DAY);
        days[key] = Object.assign({}, days[key] || {}, patch || {}, {
            day: DAY,
            updatedAt: now()
        });
        store.days = days;
        writeStore(store);
        return days[key];
    }

    function hasCompletedFinalDay() {
        const store = readStore();
        const days = store.days && typeof store.days === 'object' ? store.days : null;
        const finalDay = days && days['7'];
        return !!(finalDay && finalDay.completed === true);
    }

    function isDayCompleted(day) {
        const store = readStore();
        const days = store.days && typeof store.days === 'object' ? store.days : null;
        const entry = days && days[String(day)];
        return !!(entry && entry.completed === true);
    }

    function isPeriodActive() {
        return !!activeSession;
    }

    function getHost() {
        return window.reactChatWindowHost || null;
    }

    function waitForHost() {
        const startedAt = now();
        return new Promise((resolve) => {
            function tick() {
                const host = getHost();
                if (host && typeof host.setNewUserIcebreakerPrompt === 'function') {
                    resolve(host);
                    return;
                }
                if (now() - startedAt >= HOST_WAIT_TIMEOUT_MS) {
                    resolve(null);
                    return;
                }
                window.setTimeout(tick, HOST_WAIT_INTERVAL_MS);
            }
            tick();
        });
    }

    function getPromptOptions() {
        return [
            { choice: 'chat', label: translate('tutorial.icebreaker.day1.chat', '先聊聊天') },
            { choice: 'voice', label: translate('tutorial.icebreaker.day1.voice', '试试语音') },
            { choice: 'explore', label: translate('tutorial.icebreaker.day1.explore', '看看功能') }
        ];
    }

    function translate(key, fallback) {
        try {
            if (typeof window.t === 'function') {
                const translated = window.t(key, fallback);
                if (typeof translated === 'string' && translated && translated !== key) {
                    return translated;
                }
            }
        } catch (_) {}
        return fallback;
    }

    function resolveLanlanName() {
        try {
            if (window.appState && typeof window.appState.lanlan_name === 'string' && window.appState.lanlan_name) {
                return window.appState.lanlan_name;
            }
        } catch (_) {}
        try {
            if (window.lanlan_config && typeof window.lanlan_config.lanlan_name === 'string' && window.lanlan_config.lanlan_name) {
                return window.lanlan_config.lanlan_name;
            }
        } catch (_) {}
        return '';
    }

    async function postContext(role, text, sessionId) {
        const lanlanName = resolveLanlanName();
        const content = String(text || '').trim();
        if (!lanlanName || !content || typeof fetch !== 'function') {
            return false;
        }
        try {
            const response = await fetch('/api/icebreaker/context', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    lanlan_name: lanlanName,
                    role: role,
                    text: content,
                    session_id: sessionId || ''
                })
            });
            if (!response.ok) {
                return false;
            }
            const body = await response.json().catch(() => null);
            return !!(body && body.ok === true);
        } catch (error) {
            console.warn('[NewUserIcebreaker] context write failed:', error);
            return false;
        }
    }

    async function start(reason) {
        if (activeSession || isDayCompleted(DAY) || hasCompletedFinalDay()) {
            return false;
        }

        activeSession = {
            id: 'new-user-icebreaker-' + DAY + '-' + now().toString(36),
            day: DAY,
            reason: reason || 'tutorial-ended',
            startedAt: now()
        };
        updateDayEntry({
            triggeredAt: activeSession.startedAt,
            sessionId: activeSession.id,
            reason: activeSession.reason,
            completed: false
        });

        const host = await waitForHost();
        if (!activeSession) {
            return false;
        }
        if (!host) {
            console.warn('[NewUserIcebreaker] React chat host unavailable');
            activeSession = null;
            return false;
        }

        host.setNewUserIcebreakerPrompt({
            sessionId: activeSession.id,
            options: getPromptOptions()
        });
        return true;
    }

    async function completeFromChoice(detail) {
        if (!activeSession) {
            return;
        }
        const session = activeSession;
        if (session.choiceInFlight) {
            return;
        }
        const sessionId = String(detail && detail.sessionId || '');
        if (sessionId && sessionId !== session.id) {
            return;
        }
        session.choiceInFlight = true;
        const option = detail && detail.option && typeof detail.option === 'object' ? detail.option : {};
        const choice = String((detail && detail.choice) || option.choice || '');
        const label = String((detail && detail.label) || option.label || '');
        try {
            const contextSynced = await postContext('user', label || choice, session.id);

            updateDayEntry({
                sessionId: session.id,
                choice: choice,
                label: label,
                completed: contextSynced,
                completedAt: contextSynced ? now() : null,
                contextSyncPending: !contextSynced
            });
            if (activeSession === session) {
                activeSession = null;
            }
        } finally {
            session.choiceInFlight = false;
        }
    }

    function handleTutorialEnded(event) {
        const detail = event && event.detail ? event.detail : {};
        if (detail.page && detail.page !== 'home') {
            return;
        }
        start(detail.reason || 'tutorial-ended');
    }

    window.newUserIcebreaker = {
        start: start,
        getActiveSession: function () {
            return activeSession;
        }
    };

    window.NekoNewUserIcebreakerState = {
        readStore: readStore,
        hasCompletedDay: isDayCompleted,
        isPeriodActive: isPeriodActive
    };

    window.addEventListener('neko:tutorial-completed', handleTutorialEnded);
    window.addEventListener('neko:tutorial-skipped', handleTutorialEnded);
    window.addEventListener('neko:icebreaker-choice-selected', function (event) {
        completeFromChoice(event && event.detail ? event.detail : {});
    });
})();
