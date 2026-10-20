/* Shared recommendation choices and evidence for web and Electron chat hosts. */
(function () {
    'use strict';
    const KEY = 'neko.music-recommendation-event.v1.';
    const MAX_QUEUE = 256;
    const messages = new Map();
    const inFlight = new Map();
    const dispatched = new Set();
    const decisionIds = new Map();
    let disposed = false;
    let lifecycleEpoch = 0;
    const placeholders = new Map();
    const retiredIds = new Set();
    let snapshotRequestId = '';
    let flushing = null;
    let queue = [];
    let retryTimer = null;
    let retryDelay = 1000;
    let evidenceEpoch = 0;
    let observedPageConfig = null;
    const requestSnapshot = () => {
        if (!disposed && hasCharacter() && typeof window.__nekoBroadcastChatMirror === 'function') {
            snapshotRequestId = uid();
            window.__nekoBroadcastChatMirror({ type: 'recommendation_request', requestId: snapshotRequestId, character: currentCharacter() });
        }
    };
    function observePageConfig() {
        // The templates load index.js after this controller, so its readiness
        // promise may only exist by DOMContentLoaded. Subscribe once per promise.
        const ready = window.pageConfigReady;
        if (!ready || typeof ready.then !== 'function' || ready === observedPageConfig) return;
        observedPageConfig = ready;
        Promise.resolve(ready).then(requestSnapshot).catch(() => {});
    }
    const isRecommendation = block => block && ['music_recommendation', 'proactive_recommendation'].includes(block.type);
    const hasCharacter = () => !!(window.lanlan_config && window.lanlan_config.lanlan_name);
    const currentCharacter = () => (window.lanlan_config && window.lanlan_config.lanlan_name) || 'Neko';
    const belongsHere = message => hasCharacter() && message && message.author === currentCharacter();
    function closePlaceholders(ids) {
        for (const [popup, id] of placeholders) {
            if (ids && !ids.has(id)) continue;
            try { if (!popup.closed) popup.close(); } catch (_) {}
            placeholders.delete(popup);
        }
    }
    function retire(ids) {
        const retired = new Set(ids.slice(-128));
        const host = window.reactChatWindowHost;
        for (const id of retired) {
            retiredIds.add(id);
            const message = messages.get(id);
            messages.delete(id);
            inFlight.delete(id);
            dispatched.delete(id);
            decisionIds.delete(id + ':accepted');
            decisionIds.delete(id + ':skipped');
            if (message && host && typeof host.removeMessage === 'function') host.removeMessage(message.id);
        }
        // This is a short UI replay fence, not a persistent deletion ledger.
        while (retiredIds.size > 256) retiredIds.delete(retiredIds.values().next().value);
        closePlaceholders(retired);
    }
    window.addEventListener('react-chat-window:messages-cleared', () => {
        lifecycleEpoch++;
        snapshotRequestId = '';
        const ids = Array.from(messages.keys());
        retire(ids);
        closePlaceholders();
        if (ids.length && typeof window.__nekoBroadcastChatMirror === 'function') {
            window.__nekoBroadcastChatMirror({ type: 'recommendation_reset', ids });
        }
        // Evidence already queued or produced by a continuing player is independent
        // of the retired chat UI and must still reach its original recommendation.
    });
    const contentCategories = new Set(['news', 'community', 'video', 'personal', 'meme', 'vision', 'window', 'web', 'work_break', 'anti_slack']);
    function contentUrl(value) {
        if (!value) return '';
        try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; }
        catch (_) { return ''; }
    }
    window.addEventListener('neko:chat-mirror', event => {
        const data = event.detail || {};
        if (data.type === 'recommendation_reset') {
            if (Array.isArray(data.ids)) retire(data.ids.filter(id => typeof id === 'string'));
            return;
        }
        if (data.type === 'recommendation_request') {
            if (!hasCharacter() || !data.requestId || data.character !== currentCharacter()) return;
            if (messages.size && typeof window.__nekoBroadcastChatMirror === 'function') {
                window.__nekoBroadcastChatMirror({ type: 'recommendation_snapshot', target: data.sender,
                    requestId: data.requestId, character: currentCharacter(),
                    messages: Array.from(messages.values()).filter(belongsHere) });
            }
            return;
        }
        if (data.type === 'recommendation_snapshot') {
            const host = window.reactChatWindowHost;
            if (!host || !Array.isArray(data.messages)) return;
            if (!hasCharacter() || !snapshotRequestId || data.requestId !== snapshotRequestId || data.character !== currentCharacter()) return;
            for (const message of data.messages.slice(-128)) {
                const block = message && message.blocks && message.blocks[0];
                if (!isRecommendation(block) || !belongsHere(message) || retiredIds.has(block.recommendationId)
                    || messages.has(block.recommendationId)) continue;
                remember(message);
                host.appendMessage(message);
            }
            return;
        }
        const message = data.message || data.patch;
        const block = message && message.blocks && message.blocks.find(isRecommendation);
        const previous = block && messages.get(block.recommendationId);
        if (block && (!hasCharacter() || retiredIds.has(block.recommendationId) || (data.message && !belongsHere(message)))) {
            const host = window.reactChatWindowHost;
            if (host && typeof host.removeMessage === 'function') host.removeMessage(data.messageId || message.id);
            return;
        }
        if (block && (data.message || previous)) remember(Object.assign({}, previous, message,
            { id: data.messageId || message.id, blocks: [block] }));
    });
    try {
        for (let i = 0; i < localStorage.length && queue.length < MAX_QUEUE; i++) {
            const key = localStorage.key(i);
            if (!key || !key.startsWith(KEY)) continue;
            try {
                const item = JSON.parse(localStorage.getItem(key));
                if (item && typeof item.id === 'string' && item.event) queue.push(item);
            } catch (_) { /* Preserve a damaged item for diagnosis. */ }
        }
        queue.sort((a, b) => (a.event.occurred_at || 0) - (b.event.occurred_at || 0));
    } catch (_) { /* A damaged queue never enables playback. */ }
    const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    async function request(id, event, retry = true) {
        const security = window.nekoLocalMutationSecurity;
        if (!security || typeof security.getMutationHeaders !== 'function') throw new Error('feedback_security_not_ready');
        const headers = await security.getMutationHeaders();
        const response = await fetch('/api/proactive/recommendations/' + encodeURIComponent(id) + '/events', {
            method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
            body: JSON.stringify(event), signal: AbortSignal.timeout(5000), keepalive: true
        });
        const result = await response.json();
        if (response.status === 403 && retry && security && security.refreshToken) {
            await security.refreshToken();
            return request(id, event, false);
        }
        if (!response.ok || !result.success) {
            const error = new Error(result.error_code || 'feedback_unavailable');
            error.code = result.error_code;
            error.permanent = (response.status === 409 || response.status === 422)
                && !['feedback_capacity_reached', 'feedback_upload_capacity_reached'].includes(result.error_code);
            throw error;
        }
        return result;
    }
    function update(id, state, error, fields) {
        const message = messages.get(id);
        if (!message || disposed) return;
        const block = Object.assign({}, message.blocks[0], fields, { state: state, error: !!error });
        message.blocks = [block];
        const host = window.reactChatWindowHost;
        if (typeof window.__nekoMirrorChatUpdate === 'function') window.__nekoMirrorChatUpdate(host, message.id, { blocks: [block] });
        else if (host) host.updateMessage(message.id, { blocks: [block] });
    }
    function remember(message) {
        const id = message.blocks[0].recommendationId;
        // Host rendering may sanitize the display author. Ownership remains the
        // original offer's character, including after choices and mirror updates.
        const previous = messages.get(id);
        if (previous) message = Object.assign({}, message, { author: previous.author });
        messages.set(id, message);
        // Only UI bookkeeping is evicted; pending evidence is kept separately.
        if (messages.size > 128) messages.delete(messages.keys().next().value);
    }
    function cancelEvidenceRetry() {
        if (!retryTimer) return;
        clearTimeout(retryTimer.handle);
        retryTimer = null;
    }
    function scheduleEvidenceRetry() {
        if (disposed || !queue.length || retryTimer) return;
        const timer = { handle: null };
        retryTimer = timer;
        timer.handle = setTimeout(() => {
            if (retryTimer !== timer) return;
            retryTimer = null;
            if (!disposed) void flush();
        }, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30000);
    }
    async function flush() {
        if (flushing || disposed) return flushing;
        const epoch = evidenceEpoch;
        const operation = (async () => {
            const deferred = new Set();
            // A bounded pass preserves order within each recommendation while
            // allowing the current account's facts past another account's item.
            for (const item of queue.slice(0, MAX_QUEUE)) {
                if (disposed || epoch !== evidenceEpoch) break;
                if (deferred.has(item.id)) continue;
                try { await request(item.id, item.event); }
                catch (error) {
                    // The UUID already belongs to its original owner in SQLite.
                    // A different account (or logout) cannot acknowledge it;
                    // retain the exact event for when that owner returns.
                    if (error.code === 'recommendation_not_found') {
                        deferred.add(item.id);
                        continue;
                    }
                    if (!error.permanent) break;
                }
                const index = queue.indexOf(item);
                if (index !== -1) queue.splice(index, 1);
                try { localStorage.removeItem(KEY + item.event.event_id); } catch (_) {}
            }
        })();
        flushing = operation;
        try { await operation; } finally {
            if (flushing === operation) {
                flushing = null;
                if (!queue.length) {
                    cancelEvidenceRetry();
                    retryDelay = 1000;
                } else {
                    // Also closes the window where new evidence or pageshow
                    // arrived after this pass's final check but before release.
                    scheduleEvidenceRetry();
                }
            }
        }
    }
    function evidence(id, kind, fields) {
        if (queue.length >= MAX_QUEUE) {
            console.warn('[Music recommendation] Evidence queue is full');
            return;
        }
        const item = { id, event: Object.assign({ event_id: uid(), kind, occurred_at: Date.now() / 1000 }, fields) };
        queue.push(item);
        // One key per event prevents two windows overwriting each other's outbox.
        try { localStorage.setItem(KEY + item.event.event_id, JSON.stringify(item)); }
        catch (_) { console.warn('[Music recommendation] Could not persist pending evidence'); }
        void flush();
    }
    function offer(recommendation, turnId) {
        if (!recommendation || !recommendation.id || disposed || retiredIds.has(recommendation.id)) return false;
        if (messages.has(recommendation.id)) return true;
        const host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') return false;
        const message = {
            id: 'music-recommendation-' + recommendation.id, role: 'assistant',
            author: currentCharacter(),
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            turnId: turnId || undefined,
            blocks: [{ type: 'music_recommendation', recommendationId: recommendation.id,
                track: recommendation.track, state: recommendation.state || 'pending',
                expiresAt: Number(recommendation.expires_at) * 1000 || 0 }]
        };
        remember(message);
        if (typeof window.__nekoMirrorChatAppend === 'function') window.__nekoMirrorChatAppend(host, message);
        else host.appendMessage(message);
        return true;
    }
    function offerContent(recommendation, turnId) {
        if (!recommendation || !recommendation.id || !recommendation.content || disposed || retiredIds.has(recommendation.id)) return false;
        if (!contentCategories.has(recommendation.category)) return false;
        const href = contentUrl(recommendation.content.url);
        const screenObservation = recommendation.category === 'vision' && !recommendation.content.url
            && typeof recommendation.content.title === 'string' && recommendation.content.title.trim();
        if (!href && !screenObservation) return false;
        if (messages.has(recommendation.id)) return true;
        const host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') return false;
        const message = {
            id: 'proactive-recommendation-' + recommendation.id, role: 'assistant',
            author: currentCharacter(),
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            turnId: turnId || undefined,
            blocks: [{ type: 'proactive_recommendation', recommendationId: recommendation.id,
                category: recommendation.category, content: Object.assign({}, recommendation.content, {url: href}),
                state: recommendation.state || 'pending', expiresAt: Number(recommendation.expires_at) * 1000 || 0 }]
        };
        remember(message);
        if (typeof window.__nekoMirrorChatAppend === 'function') window.__nekoMirrorChatAppend(host, message);
        else host.appendMessage(message);
        return true;
    }
    async function decide(message, id, kind) {
        if (inFlight.has(id) || disposed) return;
        const previous = messages.get(id);
        if (!belongsHere(previous) || retiredIds.has(id) || !['pending', 'saving'].includes(previous.blocks[0].state)) return;
        remember(message);
        const epoch = lifecycleEpoch;
        const operation = {};
        inFlight.set(id, operation);
        const isCurrent = () => !disposed && epoch === lifecycleEpoch && inFlight.get(id) === operation
            && belongsHere(messages.get(id)) && !retiredIds.has(id);
        update(id, 'saving');
        const block = message.blocks[0];
        const href = block.type === 'proactive_recommendation' && block.category !== 'meme' && contentUrl(block.content.url);
        const shell = window.electronShell;
        let placeholder = null;
        // Browser popup permission belongs to this click, before the durable decision await.
        if (kind === 'accepted' && href && !(shell && typeof shell.openExternal === 'function')) {
            try { placeholder = window.open('', '_blank'); if (placeholder) { placeholder.opener = null; placeholders.set(placeholder, id); } } catch (_) {}
        }
        const closePlaceholder = () => { placeholders.delete(placeholder); try { if (placeholder && !placeholder.closed) placeholder.close(); } catch (_) {} };
        const key = id + ':' + kind;
        if (!decisionIds.has(key)) decisionIds.set(key, uid());
        const event = { event_id: decisionIds.get(key), kind, occurred_at: Date.now() / 1000 };
        try {
            const result = await request(id, event);
            if (!isCurrent()) return;
            // Another window may already have advanced playback while this
            // decision response was in flight. Do not roll its status back.
            const currentState = messages.get(id)?.blocks[0].state || 'saving';
            if (!['accepted', 'skipped'].includes(result.state)
                || ['pending', 'saving', 'accepted'].includes(currentState)) update(id, result.state);
            if (block.type === 'proactive_recommendation') {
                if (kind !== 'accepted' || !result.action_grant || dispatched.has(id)) { closePlaceholder(); return; }
                dispatched.add(id);
                if (href) {
                    if (shell && typeof shell.openExternal === 'function') await shell.openExternal(href);
                    else if (placeholder && !placeholder.closed) { placeholder.location.href = href; placeholders.delete(placeholder); }
                    else update(id, 'accepted', true);
                }
                return;
            }
            if (kind !== 'accepted' || !result.playback_grant || dispatched.has(id)) return;
            dispatched.add(id);
            // Consent is for this track only; never substitute another song.
            const playback = typeof window.dispatchMusicPlayDetailed === 'function'
                ? await window.dispatchMusicPlayDetailed(block.track, {
                    source: 'proactive', recommendationId: id, cardScopeId: 'recommendation:' + id
                }) : { ok: false, reason: 'ui_not_ready' };
            if (!isCurrent()) return;
            if (!playback.ok) {
                update(id, 'failed');
                evidence(id, 'failed', { playback_id: 'unstarted:' + id, reason: String(playback.reason || 'player_error').slice(0, 64) });
            }
        } catch (error) {
            closePlaceholder();
            if (!isCurrent()) return;
            if (block.type === 'proactive_recommendation' && dispatched.has(id)) {
                // A dispatch error does not undo an already committed preference.
                update(id, 'accepted', true);
            } else if (dispatched.has(id)) {
                update(id, 'failed');
                evidence(id, 'failed', { playback_id: 'unstarted:' + id, reason: 'player_error' });
            } else if (['pending', 'saving'].includes(messages.get(id)?.blocks[0].state || 'saving')) {
                update(id, error.permanent ? 'unavailable' : 'pending', !error.permanent);
            }
        } finally {
            if (!isCurrent()) closePlaceholder();
            if (inFlight.get(id) === operation) inFlight.delete(id);
            if (dispatched.size > 128) dispatched.delete(dispatched.values().next().value);
            if (decisionIds.size > 256) decisionIds.delete(decisionIds.keys().next().value);
        }
    }
    window.addEventListener('react-chat-window:action', event => {
        const detail = event.detail || {};
        const action = detail.action || {};
        const payload = action.payload || {};
        const block = detail.message && detail.message.blocks && detail.message.blocks.find(isRecommendation);
        if (!block || block.recommendationId !== payload.recommendationId) return;
        if (retiredIds.has(block.recommendationId) || !belongsHere(messages.get(block.recommendationId))) return;
        if (action.action === 'music_recommendation_shown') evidence(block.recommendationId, 'shown');
        if (action.action === 'proactive_recommendation_shown') evidence(block.recommendationId, 'shown');
        if (['proactive_recommendation_dismiss', 'proactive_recommendation_reveal'].includes(action.action) && block.type === 'proactive_recommendation' && block.state === 'accepted' && block.category === 'meme') {
            remember(detail.message);
            update(block.recommendationId, block.state, false, { dismissed: action.action === 'proactive_recommendation_dismiss' });
        }
        if (['music_recommendation_decision', 'proactive_recommendation_decision'].includes(action.action) && ['accepted', 'skipped'].includes(payload.kind)) {
            void decide(detail.message, block.recommendationId, payload.kind);
        }
    });
    window.addEventListener('online', () => { void flush(); });
    document.addEventListener('DOMContentLoaded', () => {
        if (flushing) void flushing.then(() => flush());
        else void flush();
        observePageConfig();
        requestSnapshot();
    }, { once: true });
    window.addEventListener('pagehide', () => {
        disposed = true; lifecycleEpoch++;
        evidenceEpoch++;
        cancelEvidenceRetry();
        inFlight.clear();
        snapshotRequestId = '';
        closePlaceholders();
    });
    window.addEventListener('pageshow', () => {
        disposed = false;
        for (const [id, message] of messages) if (message.blocks[0].state === 'saving') update(id, 'pending', true);
        observePageConfig();
        requestSnapshot();
        void flush();
    });
    window.nekoMusicRecommendations = {
        offer,
        hasPending() {
            return Array.from(messages.values()).some(message => {
                const block = message.blocks[0];
                return belongsHere(message) && block.type === 'music_recommendation'
                    && (block.state === 'pending' || block.state === 'saving') && Date.now() < block.expiresAt;
            });
        },
        recordPlayback(id, state, fields) {
            if (!id) return;
            update(id, state);
            evidence(id, state, fields);
        }
    };
    window.nekoRecommendations = {
        offer: offerContent,
        captureOfferContext() {
            const epoch = lifecycleEpoch, character = currentCharacter();
            return () => !disposed && epoch === lifecycleEpoch && character === currentCharacter();
        }
    };
    observePageConfig();
    requestSnapshot();
    void flush();
})();
