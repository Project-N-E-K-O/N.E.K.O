(function () {
    'use strict';
    if (window.nekoMusicListening) return;

    // Music is an ambient pose, not a sixth chat emotion. Only models that
    // explicitly provide this expression opt in. Chat and touch keep priority.
    const FILE = 'expressions/listening.exp3.json';
    const EMOTIONS = new Set(['happy', 'sad', 'angry', 'surprised', 'neutral']);
    let owner = null, pending = null, busy = false, stopped = false;
    let holdUntil = 0, turn = null, speech = null, revision = 0;
    let request = null;
    let lastPlaying = false;
    let chatEmotion = null, changedAt = Date.now(), lastTrack = '';
    function expressionFile(manager) {
        const category = window.nekoMusicMood?.category() || 'unknown';
        const file = 'expressions/listening-' + category + '.exp3.json';
        return manager?.fileReferences?.Expressions?.some(item => item.File === file) ? file : FILE;
    }

    function managerForModel() {
        const type = window.lanlan_config && window.lanlan_config.model_type;
        if (type && type !== 'live2d') return null;
        const manager = window.live2dManager;
        if (!manager || !manager.currentModel) return null;
        const expressions = manager.fileReferences && manager.fileReferences.Expressions;
        return Array.isArray(expressions) && expressions.some(item => item.File === FILE) ? manager : null;
    }

    function ownsPose() {
        return owner && owner.manager.currentModel === owner.model
            && owner.manager._transientExpressionGeneration === owner.generation;
    }

    function interrupt() {
        changedAt = Date.now();
        revision++;
        if (request) request.abort();
        request = null;
    }

    function cancelPendingChat() {
        if (pending) pending.controller.abort();
        pending = null;
    }

    async function releasePose() {
        const previous = owner;
        const owned = ownsPose();
        owner = null;
        if (owned) await previous.manager.smoothResetToInitialState(650);
    }

    function musicPlaying() {
        return typeof window.isMusicAudible === 'function' && window.isMusicAudible();
    }

    async function tick() {
        if (stopped) return;
        // A loader may still own an SDK reservation while this worker is busy.
        // Revoke it on stop now, before a late native load can enter the queue.
        const playing = musicPlaying();
        const track = window.getMusicPlaybackSnapshot?.()?.playbackId || '';
        if (track !== lastTrack) { lastTrack = track; interrupt(); }
        if (owner && owner.file !== expressionFile(owner.manager)) interrupt();
        if (lastPlaying && !playing) interrupt();
        lastPlaying = playing;
        if (busy) return;
        busy = true;
        try {
            const manager = managerForModel();
            if (owner && !ownsPose()) {
                // A click, guide, or model replacement took over. Never clear it.
                owner = null;
                holdUntil = Math.max(holdUntil, Date.now() + 8000);
            }
            const listening = !!manager && playing && !turn && !speech
                && !pending && Date.now() >= holdUntil;
            if (owner && (!listening || owner.manager !== manager || owner.file !== expressionFile(manager) || owner.playbackId !== lastTrack)) await releasePose();
            if (!manager || stopped) return;

            if (pending) {
                const next = pending;
                if (next.model !== manager.currentModel || Date.now() > next.expires) {
                    if (pending === next) cancelPendingChat();
                    return;
                }
                const applied = await manager.setEmotion(next.emotion, {
                    signal: next.controller.signal,
                    isCurrent: () => !stopped && pending === next && manager.currentModel === next.model
                });
                if (pending === next && applied !== false) pending = null;
                return;
            }
            if (!listening || owner || turn || speech || Date.now() < holdUntil) return;
            if (manager.isEmotionChanging || manager.hasActiveActionMotion()) return;
            // Respect the existing avatar capability arbiter (guides, games, etc.).
            if (!manager._allowsAvatarOperation({}, 'expression')) return;
            const model = manager.currentModel, version = revision, file = expressionFile(manager);
            const controller = new AbortController();
            request = controller;
            const isCurrent = () => !stopped && revision === version
                && manager.currentModel === model && musicPlaying() && !turn && !speech && !pending;
            // Let the previous chat expression settle before easing back into
            // enjoyment. A newer chat/model/interaction may cancel this handoff.
            if (manager.hasActiveTransientExpression()) {
                const generation = manager._transientExpressionGeneration;
                await manager.smoothResetToInitialState(550);
                if (!isCurrent() || manager._transientExpressionGeneration !== generation + 1) return;
            }
            const applied = await manager.playExpression('listening', file, {
                signal: controller.signal, isCurrent, fadeInDuration: 900,
                onApplied: () => {
                    changedAt = Date.now();
                    owner = { manager, model, file, playbackId: lastTrack, generation: manager._transientExpressionGeneration };
                }
            });
            if (request === controller) request = null;
            if (applied && isCurrent()) {
                changedAt = Date.now();
                owner = { manager, model, file, playbackId: lastTrack, generation: manager._transientExpressionGeneration };
            } else if (owner) {
                await releasePose();
            } else if (!applied) {
                holdUntil = Math.max(holdUntil, Date.now() + 2000);
            }
        } catch (error) {
            if (error.name !== 'AbortError') console.warn('[Music listening]', error);
            holdUntil = Math.max(holdUntil, Date.now() + 2000);
        } finally {
            busy = false;
        }
    }

    function applyChatEmotion(emotion) {
        const manager = managerForModel();
        if (!manager || !EMOTIONS.has(emotion)) return false;
        chatEmotion = emotion; changedAt = Date.now();
        cancelPendingChat();
        interrupt();
        holdUntil = Date.now() + (emotion === 'sad' || emotion === 'angry' ? 15000 : 8000);
        pending = { emotion, model: manager.currentModel, expires: Date.now() + 20000,
            controller: new AbortController() };
        void tick();
        return true;
    }

    function begin(event, speaking) {
        const id = event.detail && event.detail.turnId || 'current';
        if (speaking) speech = id;
        else {
            if (turn !== id) cancelPendingChat();
            turn = id;
        }
        holdUntil = Math.max(holdUntil, Date.now() + 8000);
        interrupt();
        void tick();
    }

    function finish(event, speaking) {
        const id = event.detail && event.detail.turnId;
        if (speaking) {
            if (!id || id === speech) speech = null;
        } else if (!id || id === turn) turn = null;
        holdUntil = Math.max(holdUntil, Date.now() + 3500);
        void tick();
    }

    window.addEventListener('neko-assistant-turn-start', event => begin(event, false));
    window.addEventListener('neko-assistant-turn-end', event => finish(event, false));
    window.addEventListener('neko-assistant-speech-start', event => begin(event, true));
    window.addEventListener('neko-assistant-speech-end', event => finish(event, true));
    window.addEventListener('neko-assistant-speech-cancel', event => {
        finish(event, true); finish(event, false);
    });
    window.addEventListener('neko-assistant-speech-unavailable', event => finish(event, true));
    window.addEventListener('neko-music-mood-change', () => { interrupt(); void tick(); });
    window.addEventListener('neko-music-playback-change', () => { void tick(); });
    const timer = window.setInterval(() => { void tick(); }, 400);
    window.addEventListener('pagehide', event => {
        if (event.persisted) return;
        stopped = true; interrupt(); cancelPendingChat(); window.clearInterval(timer);
        if (ownsPose()) owner.manager.clearExpression();
        owner = null;
    });
    window.nekoMusicListening = { applyChatEmotion, tick, snapshot: () => ({
        playing: musicPlaying(), trackId: window.nekoMusicMood?.snapshot()?.result?.track_id || null,
        playbackId: lastTrack, chatEmotion, songMood: window.nekoMusicMood?.snapshot(),
        expression: ownsPose() ? owner.file : (window.live2dManager?.hasActiveTransientExpression?.()
            ? window.live2dManager.currentExpressionFile || null : null),
        source: ownsPose() ? 'music' : (turn || speech || pending || (chatEmotion && Date.now() < holdUntil) ? 'chat' : 'avatar'),
        reason: turn ? 'chat_turn' : speech ? 'speech' : pending ? 'chat_expression_pending'
            : Date.now() < holdUntil ? 'emotion_hold' : !musicPlaying() ? 'not_playing'
            : window.live2dManager?.hasActiveActionMotion() ? 'action_motion'
            : !managerForModel() ? 'unsupported_model'
            : !window.live2dManager?._allowsAvatarOperation({}, 'expression') ? 'interaction_priority'
            : busy ? 'transition' : 'ready',
        turnActive: !!turn, speechActive: !!speech, holdUntil, changedAt
    }) };
})();
