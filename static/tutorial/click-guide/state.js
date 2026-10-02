(function (root) {
    'use strict';
    let state = null;
    let settled = false;
    async function refresh() {
        if (typeof root.waitForStorageLocationStartupBarrier === 'function') {
            await root.waitForStorageLocationStartupBarrier();
        } else if (root.__nekoStorageLocationStartupBarrier) {
            await root.__nekoStorageLocationStartupBarrier;
        }
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch('/api/click-guide/state', { cache: 'no-store', signal: controller.signal });
            if (!response.ok) throw new Error('click_guide_state_unavailable');
            state = await response.json();
            return state;
        } finally {
            clearTimeout(timer);
        }
    }
    const isChat = ['/chat', '/chat_full'].includes(root.location.pathname.replace(/\/$/, ''));
    const ready = (isChat ? Promise.resolve(null) : refresh()).catch(error => { console.warn('[ClickGuide]', error); return null; })
        .finally(() => { settled = true; });
    async function update(action, values = {}, expectedRevision = state?.revision) {
        const security = root.nekoLocalMutationSecurity;
        if (!security) throw new Error('click_guide_security_unavailable');
        const body = JSON.stringify({ action, ...values, expectedRevision });
        async function submit() {
            return fetch('/api/click-guide/state', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...await security.getMutationHeaders() },
                body
            });
        }
        let response = await submit();
        let result = await response.json();
        if (response.status === 403 && result.error_code === 'csrf_validation_failed') {
            await security.refreshToken();
            response = await submit();
            result = await response.json();
        }
        if (result.state) state = result.state;
        if (!response.ok) throw new Error(response.status === 409 ? 'click_guide_state_conflict' : 'click_guide_save_failed');
        return state;
    }
    function isSevenDayOverride(sevenDay) {
        if (!Number.isFinite(state?.selectedAt)) return false;
        const latestReset = sevenDay?.resetHistory?.at(-1);
        return Date.parse(latestReset?.resetAt) > state.selectedAt;
    }
    function projectSevenDay(progress) {
        if (state?.choice === 'click' && !state.pending && progress?.manualResetRound
                && !isSevenDayOverride(progress)) {
            return { ...progress, manualResetRound: null, pendingRound: null };
        }
        return progress;
    }
    function resumeSevenDay() {
        const sevenDay = root.NekoSevenDayTutorialState;
        const progress = sevenDay?.loadState();
        const resumed = projectSevenDay(progress);
        if (resumed !== progress) {
            // Called only after authoritative state is ready in home startup.
            resumed.updatedAt = new Date().toISOString();
            sevenDay.saveState(resumed);
        }
        return resumed;
    }
    root.NekoClickGuideState = { ready: () => ready, isReady: () => settled, refresh, update, get: () => state, isSevenDayOverride, projectSevenDay, resumeSevenDay };
})(window);
