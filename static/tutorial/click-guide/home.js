(function (root) {
    'use strict';
    const api = root.NekoClickGuide;
    const stateApi = root.NekoClickGuideState;
    const t = key => root.t('clickGuide.' + key);
    const isChat = ['/chat', '/chat_full'].includes(location.pathname.replace(/\/$/, ''));
    // The home replay targets the compact /chat window. Full chat is passive
    // so the shared channel cannot run two copies of the same chat stage.
    const receivesChatGuide = location.pathname.replace(/\/$/, '') === '/chat';
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('neko_click_guide') : null;
    let active = false;
    let runId = null;
    let currentRunner = null;
    let remoteResolve = null;
    let lastRemoteRun = null;
    let remoteStopped = false;
    let lastPing = 0;
    let pageHidden = false;

    function send(type, id, reason, details = {}) {
        const message = { action: 'click_guide', type, runId: id, reason, ...details };
        channel?.postMessage(message);
        const bridge = root.nekoTutorialOverlay;
        if (isChat) bridge?.relayToPet?.(message);
        else bridge?.relayToChat?.(message);
    }
    function setActive(value) {
        active = value;
        root.isNekoClickGuideActive = value;
        root.dispatchEvent(new CustomEvent('neko:click-guide-active', { detail: { active: value } }));
    }
    function labels() {
        return { tour: t('tour'), next: t('next'), back: t('back'), skip: t('skip'), unavailable: t('unavailable'),
            nativeFallback: t('nativeFallback') };
    }
    function run(steps, section, options = {}) {
        if (pageHidden) return Promise.resolve({ reason: 'failed' });
        return new Promise((resolve, reject) => {
            const runner = api.createRunner({ steps, labels: { ...labels(), section }, ...options,
                onEnd: reason => resolve({ reason, history: runner.history, skipped: runner.skipped }),
                presentation: api.createNativePresentation() });
            currentRunner = runner;
            runner.start().catch(reject);
        });
    }
    async function runChat(onReady, resume) {
        let restore;
        try {
            restore = await api.prepareChat();
            if (pageHidden || (isChat && remoteStopped)) return { reason: 'failed' };
            const steps = api.chatSteps();
            if (steps[resume?.startIndex]?.id === 'restore') {
                root.reactChatWindowHost.setChatSurfaceMode('minimized');
                await api.waitUntil(() => root.reactChatWindowHost.getChatSurfaceMode() === 'minimized',
                    new AbortController().signal, 3000);
            }
            onReady?.();
            return await run(steps, t('sections.chat'), resume);
        } finally {
            await restore?.();
        }
    }
    async function receive(message) {
        if (!message || message.action !== 'click_guide' || typeof message.runId !== 'string') return;
        if (receivesChatGuide && message.type === 'start' && !active && lastRemoteRun !== message.runId) {
            if (root.isInTutorial || root.isNekoHomeTutorialPending) return;
            lastRemoteRun = runId = message.runId;
            remoteStopped = false;
            lastPing = Date.now();
            setActive(true);
            const lease = setInterval(() => {
                if (Date.now() - lastPing > 6000) {
                    remoteStopped = true;
                    void currentRunner?.stop('failed');
                }
                send('heartbeat', runId);
            }, 1000);
            let result = { reason: 'failed' };
            try { result = await runChat(() => send('ready', runId), message.resume); }
            catch (error) { console.warn('[ClickGuide] Chat:', error); }
            finally {
                clearInterval(lease);
                send('done', runId, result.reason, { history: result.history, skipped: result.skipped });
                setActive(false);
                runId = null;
            }
        } else if (message.runId === runId) {
            if (isChat && message.type === 'heartbeat') lastPing = Date.now();
            if (message.type === 'stop' && isChat) {
                remoteStopped = true;
                await currentRunner?.stop(message.reason === 'failed' ? 'failed' : 'skipped');
            }
            if (!isChat) remoteResolve?.(message);
        }
    }
    channel?.addEventListener('message', event => void receive(event.data));
    root.addEventListener('neko:tutorial-overlay-relay', event => void receive(event.detail));
    root.addEventListener('message', event => {
        if (event.origin !== location.origin) return;
        const data = event.data;
        if (data?.__nekoTutorialOverlayRelay === true) void receive(data.payload);
        else if (data?.action === '__nekoTutorialOverlayRelay') void receive(data.detail);
    });
    root.addEventListener('pagehide', () => {
        pageHidden = true;
        if (runId) send(isChat ? 'done' : 'stop', runId, 'failed');
        remoteResolve?.({ type: 'done', reason: 'failed' });
        void currentRunner?.stop('failed');
    });
    root.addEventListener('pageshow', () => { pageHidden = false; });

    async function remoteChat(resume) {
        return new Promise(resolve => {
            let ready = false;
            const preparationDeadline = Date.now() + 30000;
            let timer = setTimeout(() => finish('failed'), 15000);
            const heartbeat = setInterval(() => send('heartbeat', runId), 1000);
            function finish(reason, result = {}) {
                clearTimeout(timer);
                clearInterval(heartbeat);
                remoteResolve = null;
                resolve({ reason, ...result });
            }
            remoteResolve = message => {
                if (message.type === 'ready') ready = true;
                if (['ready', 'heartbeat'].includes(message.type)) {
                    clearTimeout(timer);
                    const remaining = ready ? 6000 : Math.min(6000, preparationDeadline - Date.now());
                    timer = setTimeout(() => finish('failed'), Math.max(0, remaining));
                }
                if (message.type === 'done') finish(message.reason,
                    { history: message.history, skipped: message.skipped });
            };
            send('start', runId, undefined, { resume });
        });
    }
    async function start({ startup = false } = {}) {
        if (active || root.isInTutorial || root.universalTutorialManager?.isTutorialRunning) {
            root.universalTutorialManager?.setHomeTutorialPending(false);
            return false;
        }
        runId = api.createRunId('click-');
        setActive(true);
        const manager = root.universalTutorialManager;
        manager?.clearStartupGreetingRelease('click-guide-start');
        let outcome = 'skipped';
        let restoreFloating;
        let saving = false;
        let finished = false;
        try {
            const state = await stateApi.refresh();
            await root.NekoSevenDayTutorialState?.ready?.();
            if (!state || state.choice !== 'click' || !state.pending
                    || stateApi.isSevenDayOverride?.(root.NekoSevenDayTutorialState?.loadState())) return false;
            const localChat = api.resolveTarget('#react-chat-window-shell');
            const native = root.nekoTutorialOverlay;
            const chat = resume => !localChat && native?.relayToChat ? remoteChat(resume) : runChat(null, resume);
            let chatResult = await chat();
            while (chatResult.reason === 'completed') {
                restoreFloating = await api.prepareFloating();
                const floating = await run(api.floatingSteps(), t('sections.floating'), { backAtStart: true });
                await restoreFloating();
                restoreFloating = null;
                if (floating.reason === 'back') {
                    const path = chatResult.history || [];
                    if (!path.length) { outcome = 'failed'; break; }
                    runId = api.createRunId('click-');
                    chatResult = await chat({ startIndex: path.at(-1), initialHistory: path.slice(0, -1),
                        initialSkipped: chatResult.skipped || [] });
                    continue;
                }
                outcome = floating.reason;
                if (outcome === 'failed') {
                    root.showStatusToast?.(t('connection.body'), 5000);
                    return false;
                }
                break;
            }
            if (chatResult.reason === 'failed' || outcome === 'failed') {
                if (pageHidden) return false;
                // A missing peer must not count as a completed chat tutorial.
                await run([{ title: t('connection.title'), body: t('connection.body'), nextLabel: t('close') }]);
                return false;
            }
            if (pageHidden) return false;
            saving = true;
            await stateApi.update('finish', { status: outcome }, state.revision);
            finished = true;
            return true;
        } catch (error) {
            console.warn('[ClickGuide] Session:', error);
            root.showStatusToast?.(t(saving ? 'saveFailed' : 'connection.body'), 5000);
            return false;
        } finally {
            try {
                send('stop', runId);
                await currentRunner?.stop('skipped');
            } finally {
                try { await restoreFloating?.(); }
                finally {
                    setActive(false);
                    runId = null;
                    if (finished || !startup) manager?.dispatchStartupGreetingRelease('click-guide-ended');
                    else manager?.setHomeTutorialPending(false);
                }
            }
        }
    }
    api.handleStartup = async function (manager) {
        if (isChat || manager.currentPage !== 'home') return false;
        try {
            const state = await stateApi.ready();
            // Only an explicit reactivation from the memory browser starts the click guide.
            // New and legacy unset states always continue the original seven-day flow.
            if (!state || state.choice !== 'click') return false;
            await root.NekoSevenDayTutorialState?.ready?.();
            if (stateApi.isSevenDayOverride?.(root.NekoSevenDayTutorialState?.loadState())) return false;
            // Preserve normal daily progression after replay, while suppressing
            // only a superseded manual restart of the seven-day tutorial.
            if (!state.pending) {
                stateApi.resumeSevenDay?.();
                await root.NekoSevenDayTutorialState?.flush?.();
                manager.setHomeTutorialPending(false);
                return false;
            }
            const languageWait = new AbortController();
            await api.waitUntil(() => manager.isI18nReady(), languageWait.signal, 15000);
            if (await start({ startup: true })) return true;
            // Retry the user's choice next time; only this session falls back.
            return false;
        } catch (error) {
            console.warn('[ClickGuide] Startup:', error);
            manager.setHomeTutorialPending(false);
            return false;
        }
    };
    api.startHome = start;
})(window);
