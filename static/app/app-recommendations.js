/* Shared recommendation choices and evidence for web and Electron chat hosts. */
(function () {
    'use strict';
    const KEY = 'neko.music-recommendation-event.v1.';
    const CAPTURE_KEY = 'neko.recommendation-capture.v2.';
    const MAX_QUEUE = 256;
    const messages = new Map();
    const inFlight = new Map();
    const dispatched = new Set();
    const decisionIds = new Map();
    const musicCountdowns = new Map();
    const countdownShown = new Set();
    const MUSIC_COUNTDOWN_MS = 10000;
    let disposed = false;
    let lifecycleEpoch = 0;
    let autoplayVisibilityEpoch = 0;
    const placeholders = new Map();
    const retiredIds = new Set();
    let snapshotRequestId = '';
    let flushing = null;
    let queue = [];
    let retryTimer = null;
    let retryDelay = 1000;
    let evidenceEpoch = 0;
    let observedPageConfig = null;
    const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    const documentClockId = uid().replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, '$1-$2-$3-$4-$5');
    const offerContexts = new Map();
    let captureBinding = null;
    let captureRefresh = null;
    let captureSupported = false;
    let captureOwner = null;
    let authorityEpoch = 0;
    const captureTick = () => typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? Math.floor(performance.now()) : null;
    const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
    function withoutProof(value) {
        if (Array.isArray(value)) return value.map(withoutProof);
        if (!value || typeof value !== 'object') return value;
        const result = {};
        for (const key of Object.keys(value).sort()) if (key !== 'time_proof') result[key] = withoutProof(value[key]);
        return result;
    }
    const canonicalEvent = event => JSON.stringify(withoutProof(event));
    const canonicalEventBytes = event => unescape(encodeURIComponent(canonicalEvent(event)));
    // Local handoff hashes use the original JSON, before Python adds schema defaults.
    // Keep the fallback synchronous for ordinary HTTP hosts without SubtleCrypto.
    function eventHash(event) {
        const input = canonicalEventBytes(event);
        const bytes = Array.from(input, c => c.charCodeAt(0));
        const bits = bytes.length * 8;
        bytes.push(128);
        while (bytes.length % 64 !== 56) bytes.push(0);
        for (let i = 7; i >= 0; i--) bytes.push(Math.floor(bits / 2 ** (8 * i)) & 255);
        const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
            0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
            0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
            0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
            0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
        const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
        const rotate = (x, n) => (x >>> n) | (x << (32 - n));
        for (let offset = 0; offset < bytes.length; offset += 64) {
            const w = new Array(64);
            for (let i = 0; i < 16; i++) w[i] = (bytes[offset+i*4]<<24)|(bytes[offset+i*4+1]<<16)|(bytes[offset+i*4+2]<<8)|bytes[offset+i*4+3];
            for (let i = 16; i < 64; i++) {
                const a = w[i-15], b = w[i-2];
                w[i] = (w[i-16] + (rotate(a,7)^rotate(a,18)^(a>>>3)) + w[i-7] + (rotate(b,17)^rotate(b,19)^(b>>>10))) | 0;
            }
            let [a,b,c,d,e,f,g,j] = h;
            for (let i = 0; i < 64; i++) {
                const t = (j+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[i]+w[i])|0;
                const u = ((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))|0;
                j=g;g=f;f=e;e=(d+t)|0;d=c;c=b;b=a;a=(t+u)|0;
            }
            [a,b,c,d,e,f,g,j].forEach((v, i) => { h[i] = (h[i] + v) | 0; });
        }
        return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
    }
    function handoffComplete(event, result) {
        if (!event.context) return true; // Rolling compatibility with an older local backend.
        const receipt = result && result.telemetry;
        return !!receipt && receipt.event_id === event.event_id && receipt.payload_hash === eventHash(event)
            && ['durable', 'discarded'].includes(receipt.status);
    }
    function eventStorageKey(event) { return (event.context ? CAPTURE_KEY : KEY) + event.event_id; }
    function removeQueued(item) {
        const index = queue.indexOf(item);
        if (index !== -1) queue.splice(index, 1);
        try { localStorage.removeItem(eventStorageKey(item.event)); } catch (_) {}
    }
    function queueEvent(id, event) {
        const existing = queue.find(item => item.event.event_id === event.event_id);
        if (existing) return existing;
        if (queue.length >= MAX_QUEUE) return null;
        const item = { id, event };
        queue.push(item);
        try { localStorage.setItem(eventStorageKey(event), JSON.stringify(item)); }
        catch (_) { console.warn('[Music recommendation] Could not persist pending evidence'); }
        return item;
    }
    function invalidateCapture(authorityLost = false) {
        if (authorityLost) {
            captureOwner = null;
            authorityEpoch++;
        }
        if (!captureBinding) return;
        captureBinding = null;
        window.dispatchEvent(new CustomEvent('neko:feedback-capture-changed'));
    }
    async function refreshCapture() {
        if (captureRefresh || disposed) return captureRefresh;
        const revision = evidenceEpoch;
        const operation = (async () => {
            const security = window.nekoLocalMutationSecurity;
            if (!security || typeof security.getMutationHeaders !== 'function') return;
            const headers = await security.getMutationHeaders();
            const sendTick = captureTick();
            const response = await fetch('/api/proactive/feedback/capture-context', {
                method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
                body: JSON.stringify({ clock_session_id: documentClockId }), signal: AbortSignal.timeout(5000)
            });
            const receiveTick = captureTick();
            if (disposed || revision !== evidenceEpoch) return;
            // An authentication proxy may return an empty or HTML 401 body.
            // Its explicit status retires this identity before JSON decoding.
            if (response.status === 401) { invalidateCapture(true); return; }
            const result = await response.json();
            if (response.status === 426 && !disposed && revision === evidenceEpoch) {
                window.dispatchEvent(new CustomEvent('neko:feedback-upgrade-required'));
            }
            if (disposed || revision !== evidenceEpoch) return;
            if (!response.ok || !result.success || !result.context) {
                invalidateCapture(['authentication_required', 'account_mismatch', 'session_changed'].includes(result.error_code));
                return;
            }
            const value = result.context;
            if (typeof value.owner_key !== 'string' || !value.anchor_token || !value.anchor_id
                || !Number.isFinite(sendTick) || !Number.isFinite(receiveTick)) { invalidateCapture(); return; }
            const previous = captureBinding && captureBinding.value;
            if (captureOwner !== null && captureOwner !== value.owner_key) authorityEpoch++;
            captureOwner = value.owner_key;
            captureBinding = { value: copy(value), sendTick, receiveTick };
            if (!previous || ['owner_key','account_incarnation','clear_generation','registration_id','registration_version','binding_revision']
                .some(key => previous[key] !== value[key])) {
                window.dispatchEvent(new CustomEvent('neko:feedback-capture-changed'));
            }
        })().catch(() => { if (!disposed && revision === evidenceEpoch) invalidateCapture(); });
        captureRefresh = operation;
        try { await operation; } finally { if (captureRefresh === operation) captureRefresh = null; }
    }
    function captureContext(offerContext) {
        const tick = captureTick();
        const result = { version: 'desktop-feedback-capture-v2',
            capture: { clock_session_id: documentClockId } };
        if (Number.isFinite(tick)) result.capture.tick_ms = tick;
        const bound = captureBinding;
        if (!bound || (offerContext && bound.value.owner_key !== offerContext.owner_key)) return result;
        for (const key of ['owner_key','account_incarnation','clear_generation','registration_id','registration_version','binding_revision']) {
            if (bound.value[key] !== undefined) result[key] = bound.value[key];
        }
        result.capture.anchor_id = bound.value.anchor_id;
        result.time_proof = { anchor_token: bound.value.anchor_token,
            send_tick_ms: bound.sendTick, receive_tick_ms: bound.receiveTick };
        return result;
    }
    function rememberOfferContext(id, value) {
        if (!value || typeof value.owner_key !== 'string' || value.offer_id !== id) return;
        if (JSON.stringify(offerContexts.get(id)) === JSON.stringify(value)) return;
        offerContexts.set(id, copy(value));
        if (!captureSupported) {
            captureSupported = true;
            for (const item of queue.slice()) if (!item.event.context) removeQueued(item);
            // The loaded queue is capped. Retire dormant legacy entries too;
            // a later reload must not resurrect pre-upgrade evidence.
            try {
                for (let i = localStorage.length - 1; i >= 0; i--) {
                    const key = localStorage.key(i);
                    if (key && key.startsWith(KEY)) localStorage.removeItem(key);
                }
            } catch (_) { /* Storage access failure cannot affect normal playback. */ }
        }
        void refreshCapture();
    }
    window.nekoFeedbackCapture = { capture: captureContext, refresh: refreshCapture };
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
            cancelMusicCountdown(id);
            countdownShown.delete(id);
            retiredIds.add(id);
            const message = messages.get(id);
            messages.delete(id);
            offerContexts.delete(id);
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
            if (!key || (!key.startsWith(KEY) && !key.startsWith(CAPTURE_KEY))) continue;
            try {
                const item = JSON.parse(localStorage.getItem(key));
                if (item && typeof item.id === 'string' && item.event) queue.push(item);
            } catch (_) { /* Preserve a damaged item for diagnosis. */ }
        }
        queue.sort((a, b) => (a.event.occurred_at || 0) - (b.event.occurred_at || 0));
    } catch (_) { /* A damaged queue never enables playback. */ }
    async function request(id, event, retry = true) {
        const security = window.nekoLocalMutationSecurity;
        if (!security || typeof security.getMutationHeaders !== 'function') throw new Error('feedback_security_not_ready');
        const headers = await security.getMutationHeaders();
        const response = await fetch('/api/proactive/recommendations/' + encodeURIComponent(id) + '/events', {
            method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers,
                event.context ? { 'X-NEKO-Feedback-Canonical': btoa(canonicalEventBytes(event)) } : {}),
            body: JSON.stringify(event), signal: AbortSignal.timeout(5000), keepalive: true
        });
        const result = await response.json();
        // The backend may learn a clear while the same audio keeps playing.
        // Retire only the original fact; refresh qualification for future captures.
        if (event.context && result.telemetry && ['binding_changed', 'binding_unavailable', 'control_unavailable', 'time_unproven']
            .includes(result.telemetry.reason)) void refreshCapture();
        if (response.status === 403 && retry && security && security.refreshToken) {
            await security.refreshToken();
            return request(id, event, false);
        }
        if (!response.ok || !result.success) {
            const error = new Error(result.error_code || 'feedback_unavailable');
            error.code = result.error_code;
            error.telemetry = result.telemetry;
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
        if (state !== 'pending' || error) cancelMusicCountdown(id);
        message.blocks = [block];
        const host = window.reactChatWindowHost;
        if (typeof window.__nekoMirrorChatUpdate === 'function') window.__nekoMirrorChatUpdate(host, message.id, { blocks: [block] });
        else if (host) host.updateMessage(message.id, { blocks: [block] });
    }
    function remember(message) {
        const id = message.blocks[0].recommendationId;
        rememberOfferContext(id, message.blocks[0].offer_context);
        // Host rendering may sanitize the display author. Ownership remains the
        // original offer's character, including after choices and mirror updates.
        const previous = messages.get(id);
        if (previous) message = Object.assign({}, message, { author: previous.author });
        const previousState = previous?.blocks[0].state;
        const incomingState = message.blocks[0].state;
        // unavailable is a local refusal, not a saved decision. An accepted
        // mirror can follow it when another window committed before a toggle.
        const savedDecision = previous && !['pending', 'saving', 'unavailable'].includes(previousState);
        const stateCorrected = previous && ((!['pending', 'saving'].includes(previousState)
            && ['pending', 'saving'].includes(incomingState))
            || (savedDecision && (incomingState === 'unavailable'
                || (previousState !== 'accepted' && incomingState === 'accepted'))));
        if (stateCorrected) message = Object.assign({}, message, { blocks: [previous.blocks[0]] });
        let deadlineCorrected = false;
        if (previous?.blocks[0].autoPlayAt > 0 && message.blocks[0].type === 'music_recommendation'
            && message.blocks[0].state === 'pending' && Number.isFinite(message.blocks[0].autoPlayAt)) {
            const deadline = message.blocks[0].autoPlayAt > 0
                ? Math.min(previous.blocks[0].autoPlayAt, message.blocks[0].autoPlayAt) : previous.blocks[0].autoPlayAt;
            deadlineCorrected = deadline !== message.blocks[0].autoPlayAt;
            message = Object.assign({}, message, { blocks: [Object.assign({}, message.blocks[0], {
                autoPlayAt: deadline
            })] });
        }
        messages.set(id, message);
        // The mirror host has already rendered the incoming patch. Correct it
        // through the same host/broadcast path; equal deadlines do not echo.
        if (deadlineCorrected || stateCorrected) update(id, message.blocks[0].state, message.blocks[0].error);
        if (message.blocks[0].state !== 'pending' || message.blocks[0].error) cancelMusicCountdown(id);
        else if (countdownShown.has(id) && message.blocks[0].autoPlayAt > 0) armMusicCountdown(id);
        // Only UI bookkeeping is evicted; pending evidence is kept separately.
        if (messages.size > 128) {
            const oldest = messages.keys().next().value;
            cancelMusicCountdown(oldest);
            countdownShown.delete(oldest);
            messages.delete(oldest);
            offerContexts.delete(oldest);
        }
    }
    function cancelMusicCountdown(id) {
        const timer = musicCountdowns.get(id);
        if (timer) clearTimeout(timer.handle);
        musicCountdowns.delete(id);
    }
    function armMusicCountdown(id) {
        const message = messages.get(id), block = message && message.blocks[0];
        if (disposed || document.visibilityState === 'hidden' || !belongsHere(message)
            || !block || block.type !== 'music_recommendation' || block.state !== 'pending'
            || block.error || !Number.isFinite(block.autoPlayAt) || retiredIds.has(id)) return;
        if (block.autoPlayAt === 0) {
            update(id, 'pending', false, { autoPlayAt: Date.now() + MUSIC_COUNTDOWN_MS });
        }
        const deadline = message.blocks[0].autoPlayAt;
        // Historical or resumed proposals never catch up by unexpectedly playing immediately.
        if (deadline <= Date.now() || (block.expiresAt && deadline >= block.expiresAt)) return;
        if (musicCountdowns.get(id)?.deadline === deadline) return;
        cancelMusicCountdown(id);
        const timer = { deadline, handle: null };
        timer.handle = setTimeout(() => {
            if (musicCountdowns.get(id) !== timer) return;
            musicCountdowns.delete(id);
            const current = messages.get(id), active = current && current.blocks[0];
            if (disposed || document.visibilityState === 'hidden' || !belongsHere(current)
                || retiredIds.has(id) || !active || active.state !== 'pending' || active.error
                || active.autoPlayAt !== deadline || (active.expiresAt && Date.now() >= active.expiresAt)) return;
            if (Date.now() < deadline) { armMusicCountdown(id); return; }
            void decide(current, id, 'accepted', true);
        }, deadline - Date.now());
        musicCountdowns.set(id, timer);
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
                try {
                    const result = await request(item.id, item.event);
                    if (!handoffComplete(item.event, result)) {
                        deferred.add(item.id);
                        continue;
                    }
                }
                catch (error) {
                    if (item.event.context && handoffComplete(item.event, error)) {
                        removeQueued(item);
                        continue;
                    }
                    // The UUID already belongs to its original owner in SQLite.
                    // A different account (or logout) cannot acknowledge it;
                    // retain the exact event for when that owner returns.
                    if (error.code === 'recommendation_not_found') {
                        deferred.add(item.id);
                        continue;
                    }
                    if (item.event.context) { deferred.add(item.id); continue; }
                    if (!error.permanent) break;
                }
                removeQueued(item);
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
        const event = Object.assign({ event_id: uid(), kind, occurred_at: Date.now() / 1000 }, fields);
        const original = fields && fields.offer_context || offerContexts.get(id);
        if (original) {
            event.offer_context = copy(original);
            event.context = captureContext(original);
        }
        queueEvent(id, event);
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
                offer_context: copy(recommendation.offer_context),
                track: recommendation.track, state: recommendation.state || 'pending',
                autoPlayAt: 0,
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
                offer_context: copy(recommendation.offer_context),
                category: recommendation.category, content: Object.assign({}, recommendation.content, {url: href}),
                directDelivery: recommendation.category === 'meme' || !!screenObservation,
                state: recommendation.state || 'pending', expiresAt: Number(recommendation.expires_at) * 1000 || 0 }]
        };
        remember(message);
        if (typeof window.__nekoMirrorChatAppend === 'function') window.__nekoMirrorChatAppend(host, message);
        else host.appendMessage(message);
        return true;
    }
    // The player-side skip cooldown in music_ui.js never sees a card refused
    // before playback. The backend counts the user's own card choices instead,
    // keyed by card id so retries from any window count once and a changed
    // choice replaces the earlier one. Reported independent of whether the
    // evidence store accepts the event. Countdown auto-accepts are not a choice.
    const reportedMusicChoices = new Map();
    function reportMusicCardChoice(id, kind, character) {
        if (reportedMusicChoices.get(id) === kind) return;
        reportedMusicChoices.delete(id);
        reportedMusicChoices.set(id, kind);
        if (reportedMusicChoices.size > 256) reportedMusicChoices.delete(reportedMusicChoices.keys().next().value);
        const body = JSON.stringify({ lanlan_name: character || '', recommendation_id: id, decision: kind });
        void (async () => {
            const headers = { 'Content-Type': 'application/json' };
            const security = window.nekoLocalMutationSecurity;
            if (security && typeof security.getMutationHeaders === 'function') {
                try { Object.assign(headers, await security.getMutationHeaders()); } catch (_) {}
            }
            // A lost report must stay retryable by the next click on this card.
            const forget = () => { if (reportedMusicChoices.get(id) === kind) reportedMusicChoices.delete(id); };
            try {
                const response = await fetch('/api/proactive/music_card_decision', {
                    method: 'POST', headers, keepalive: true, signal: AbortSignal.timeout(5000), body
                });
                if (!response || !response.ok) forget();
            } catch (_) { forget(); /* cooldown is best effort; the card decision itself is unaffected */ }
        })();
    }
    async function decide(message, id, kind, automatic = false) {
        if (inFlight.has(id) || disposed) return;
        const previous = messages.get(id);
        if (!belongsHere(previous) || retiredIds.has(id) || !['pending', 'saving'].includes(previous.blocks[0].state)) return;
        if (previous.blocks[0].type === 'proactive_recommendation' && previous.blocks[0].directDelivery) return;
        if (!automatic && previous.blocks[0].type === 'music_recommendation') reportMusicCardChoice(id, kind, previous.author);
        remember(message);
        const epoch = lifecycleEpoch;
        const visibilityEpoch = autoplayVisibilityEpoch;
        const operation = {};
        inFlight.set(id, operation);
        const originalOffer = offerContexts.get(id);
        const originalAuthorityEpoch = authorityEpoch;
        const originalAuthorityKnown = captureOwner !== null
            || (typeof originalOffer?.origin_context?.account_incarnation === 'string'
                && originalOffer.origin_context.account_incarnation.length > 0);
        // A failed statistics refresh alone cannot stop ordinary playback.
        // Explicit identity loss or a verified account switch retires only the
        // operations already in flight; fresh anonymous choices remain usable.
        const hasOriginalOwner = () => !originalOffer || ((!originalAuthorityKnown || originalAuthorityEpoch === authorityEpoch)
            && (!captureBinding || originalOffer.owner_key === captureBinding.value.owner_key));
        const isCurrent = () => !disposed && epoch === lifecycleEpoch && inFlight.get(id) === operation
            && hasOriginalOwner() && belongsHere(messages.get(id)) && !retiredIds.has(id);
        // Outlives the HTTP/dispatch await: the UI may still be waiting to play
        // after inFlight is released. A hide-and-show must not restore permission.
        const getCancellationReason = () => disposed || epoch !== lifecycleEpoch
            || !hasOriginalOwner() || !belongsHere(messages.get(id)) || retiredIds.has(id) ? 'recommendation_retired'
            : automatic && (visibilityEpoch !== autoplayVisibilityEpoch || document.visibilityState === 'hidden')
                ? 'autoplay_visibility_changed' : '';
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
        if (!decisionIds.has(key)) decisionIds.set(key, { eventId: uid(), occurredAt: Date.now() / 1000, reason: automatic ? 'countdown_elapsed' : '' });
        const decision = decisionIds.get(key);
        if (!decision.event) {
            decision.event = { event_id: decision.eventId, kind, occurred_at: decision.occurredAt };
            if (decision.reason) decision.event.reason = decision.reason;
            if (originalOffer) {
                decision.event.offer_context = copy(originalOffer);
                decision.event.context = captureContext(originalOffer);
            }
        }
        const event = decision.event;
        const pendingEvidence = event.context ? queueEvent(id, event) : null;
        try {
            const result = await request(id, event);
            if (pendingEvidence && handoffComplete(event, result)) removeQueued(pendingEvidence);
            if (!isCurrent()) return;
            // Another window may already have advanced playback while this
            // decision response was in flight. Do not roll its status back.
            const currentState = messages.get(id)?.blocks[0].state || 'saving';
            if (!['accepted', 'skipped'].includes(result.state)
                || ['pending', 'saving', 'accepted'].includes(currentState)) update(id, result.state);
            if (event.context && kind === 'accepted' && (result.playback_grant || result.action_grant) && !dispatched.has(id)) {
                const security = window.nekoLocalMutationSecurity;
                const headers = await security.getMutationHeaders();
                if (!isCurrent()) return;
                const response = await fetch('/api/proactive/recommendations/' + encodeURIComponent(id) + '/dispatch', {
                    method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
                    body: JSON.stringify({ event_id: event.event_id }), signal: AbortSignal.timeout(5000)
                });
                const claim = await response.json();
                if (!isCurrent()) return;
                if (!response.ok || !claim.success || !claim.dispatch_grant) { closePlaceholder(); return; }
            }
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
            // The durable decision remains a fact, but hiding during its await
            // revokes this automatic dispatch even if the page is visible again.
            const cancellationReason = getCancellationReason();
            if (cancellationReason) {
                update(id, 'failed');
                evidence(id, 'failed', { playback_id: 'unstarted:' + id, reason: cancellationReason });
                return;
            }
            dispatched.add(id);
            // Consent is for this track only; never substitute another song.
            const playback = typeof window.dispatchMusicPlayDetailed === 'function'
                ? await window.dispatchMusicPlayDetailed(block.track, {
                    source: 'proactive', recommendationId: id, cardScopeId: 'recommendation:' + id,
                    offerContext: copy(originalOffer),
                    getCancellationReason
                }) : { ok: false, reason: 'ui_not_ready' };
            if (!isCurrent()) return;
            if (!playback.ok && messages.get(id)?.blocks[0].state !== 'failed') {
                update(id, 'failed');
                evidence(id, 'failed', { playback_id: 'unstarted:' + id, reason: String(playback.reason || 'player_error').slice(0, 64) });
            }
        } catch (error) {
            closePlaceholder();
            if (pendingEvidence && handoffComplete(event, error)) removeQueued(pendingEvidence);
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
            if (!disposed && epoch === lifecycleEpoch && originalOffer && originalAuthorityKnown && originalAuthorityEpoch !== authorityEpoch
                && belongsHere(messages.get(id)) && ['pending', 'saving', 'accepted'].includes(messages.get(id)?.blocks[0].state)) {
                update(id, 'unavailable');
            }
            if (inFlight.get(id) === operation) inFlight.delete(id);
            if (dispatched.size > 128) dispatched.delete(dispatched.values().next().value);
            if (decisionIds.size > 256) decisionIds.delete(decisionIds.keys().next().value);
            if (pendingEvidence && queue.includes(pendingEvidence)) scheduleEvidenceRetry();
        }
    }
    window.addEventListener('react-chat-window:action', event => {
        const detail = event.detail || {};
        const action = detail.action || {};
        const payload = action.payload || {};
        const block = detail.message && detail.message.blocks && detail.message.blocks.find(isRecommendation);
        if (!block || block.recommendationId !== payload.recommendationId) return;
        if (retiredIds.has(block.recommendationId) || !belongsHere(messages.get(block.recommendationId))) return;
        if (action.action === 'music_recommendation_shown') {
            evidence(block.recommendationId, 'shown');
            countdownShown.add(block.recommendationId);
            armMusicCountdown(block.recommendationId);
        }
        if (action.action === 'proactive_recommendation_shown') evidence(block.recommendationId, 'shown');
        if (['proactive_recommendation_dismiss', 'proactive_recommendation_reveal'].includes(action.action) && block.type === 'proactive_recommendation' && (block.state === 'accepted' || block.directDelivery) && block.category === 'meme') {
            remember(detail.message);
            update(block.recommendationId, block.state, false, { dismissed: action.action === 'proactive_recommendation_dismiss' });
        }
        if (['music_recommendation_decision', 'proactive_recommendation_decision'].includes(action.action) && ['accepted', 'skipped'].includes(payload.kind)) {
            void decide(detail.message, block.recommendationId, payload.kind);
        }
    });
    window.addEventListener('online', () => { if (captureSupported) void refreshCapture(); void flush(); });
    window.addEventListener('neko:feedback-control-changed', () => { if (captureSupported) void refreshCapture(); });
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
        for (const id of musicCountdowns.keys()) cancelMusicCountdown(id);
        inFlight.clear();
        snapshotRequestId = '';
        closePlaceholders();
    });
    window.addEventListener('pageshow', () => {
        disposed = false;
        if (captureSupported) void refreshCapture();
        for (const [id, message] of messages) if (message.blocks[0].state === 'saving') update(id, 'pending', true);
        observePageConfig();
        requestSnapshot();
        void flush();
        for (const id of countdownShown) armMusicCountdown(id);
    });
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            autoplayVisibilityEpoch++;
            for (const id of musicCountdowns.keys()) cancelMusicCountdown(id);
        } else {
            if (captureSupported) void refreshCapture();
            for (const id of countdownShown) armMusicCountdown(id);
        }
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
            if (state !== 'measurement') update(id, state);
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
