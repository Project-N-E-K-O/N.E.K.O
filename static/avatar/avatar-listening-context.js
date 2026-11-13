/* One-shot observation before a user's text turn; transported by the existing
 * partition-aware music bridge. No storage, polling, diagnostics or LLM calls. */
(function () {
    'use strict';
    if (window.nekoListeningContext) return;
    const pending = new Map(), captures = new WeakMap();
    let revision = 0, key = '';
    function playback() {
        const state = window.getMusicPlaybackSnapshot?.();
        const next = state?.audible ? state.playbackId : '';
        if (next !== key) {
            key = next; revision++;
            for (const job of pending.values()) job.finish(null);
        }
        return next ? state : null;
    }
    function observe(requestId) {
        const playing = playback();
        if (!playing) return null;
        window.nekoMusicMood?.sync();
        const mood = window.nekoMusicMood?.snapshot();
        const listening = window.nekoMusicListening?.snapshot();
        const manager = window.live2dManager;
        const resource = manager?._manualExpressionResource;
        const performance = resource?.active ? resource.performance?.snapshot() : null;
        const frame = resource?.active ? resource.observedFrame : null;
        const model = manager?.currentModel, rect = manager?.getHeadScreenRectInfo?.()?.rect;
        const visible = model && model.visible !== false && model.renderable !== false && model.worldAlpha > 0.1
            && document.visibilityState === 'visible' && rect && rect.width >= 4 && rect.height >= 4
            && rect.left + rect.width > 0 && rect.top + rect.height > 0
            && rect.left < window.innerWidth && rect.top < window.innerHeight;
        const result = mood?.playbackId === playing.playbackId && mood.state === 'ready' ? mood.result : null;
        const facts = [];
        let expression = null;
        // A classification is not evidence that an expression or tear was rendered.
        if (listening?.source === 'music' && listening.playbackId === playing.playbackId
            && visible && frame && frame.weight >= 0.8 && Date.now() - frame.at <= 500
            && /^expressions\/listening(?:-(joyful|tender|calm|sad|intense))?\.exp3\.json$/.test(listening.expression || '')) {
            expression = listening.expression;
            const p = frame.parameters;
            if (p.ParamMouthForm >= 0.35) facts.push('smile');
            if (p.ParamMouthForm <= -0.5) facts.push('sad_face');
            if (Math.abs(p.ParamAngleZ) >= 6) facts.push('head_tilt');
            // Nonstandard hand IDs are only known for these audited rigs.
            if (['yui-origin', 'yui-lolita', 'yui-sister'].includes(manager.modelName)) {
                if (p.Param76 >= 0.8) facts.push('hand_near_mouth');
                if (p.Param74 >= 0.8) facts.push('heart_hands');
                if (p.Param75 >= 0.8 || p.Param77 >= 0.8) facts.push('raised_palm');
            }
            if (performance?.visibleCue === 'tears') facts.push('tears');
        }
        return { version: 1, request_id: requestId, captured_at: Date.now(), playback_id: playing.playbackId,
            track: { name: String(playing.track?.name || '').slice(0, 120), artist: String(playing.track?.artist || '').slice(0, 120) },
            mood: result ? { track_id: result.track_id, category: result.category, confidence: result.confidence,
                source: result.source, classifier_version: result.classifier_version } : null,
            expression, facts };
    }
    function remember(snapshot) {
        if (snapshot) captures.set(snapshot, revision);
        return snapshot;
    }
    function capture(requestId) {
        const playing = playback();
        if (!playing || typeof requestId !== 'string') return Promise.resolve(null);
        if (window.live2dManager?.currentModel) return Promise.resolve(remember(observe(requestId)));
        return new Promise(resolve => {
            const nonce = window.crypto.randomUUID();
            const job = { playbackId: playing.playbackId, requestId, finish(value) {
                window.clearTimeout(job.timer); pending.delete(nonce); resolve(remember(value));
            } };
            job.timer = window.setTimeout(() => job.finish(null), 300);
            pending.set(nonce, job);
            window.__nekoBroadcastChatMirror?.({ type: 'listening_context_request', nonce, requestId,
                playbackId: playing.playbackId });
        });
    }
    window.addEventListener('neko:listening-context', ({ detail: data }) => {
        if (!data || typeof data.nonce !== 'string' || data.nonce.length > 100) return;
        if (data.type === 'listening_context_request') {
            if (!window.live2dManager?.currentModel || typeof data.requestId !== 'string' || data.requestId.length > 128) return;
            const snapshot = observe(data.requestId);
            window.__nekoBroadcastChatMirror?.({ type: 'listening_context_response', target: data.sender,
                nonce: data.nonce, snapshot: snapshot?.playback_id === data.playbackId ? snapshot : null });
        } else if (data.type === 'listening_context_response') {
            const job = pending.get(data.nonce);
            if (!job) return;
            const current = playback(), value = data.snapshot;
            job.finish(current && current.playbackId === job.playbackId && value?.playback_id === job.playbackId
                && value.request_id === job.requestId && Date.now() - value.captured_at >= 0
                && Date.now() - value.captured_at < 1000 ? value : null);
        }
    });
    window.addEventListener('neko-music-playback-change', playback);
    window.addEventListener('pagehide', () => { for (const job of pending.values()) job.finish(null); });
    window.nekoListeningContext = { async capture(requestId) {
        try { return await capture(requestId); } catch (_) { return null; }
    }, forMessage(snapshot) {
        const current = playback();
        return snapshot && captures.get(snapshot) === revision && current?.playbackId === snapshot.playback_id
            && Date.now() - snapshot.captured_at <= 30000 ? snapshot : null;
    } };
})();
