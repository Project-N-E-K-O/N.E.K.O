(function () {
    'use strict';
    if (window.nekoMusicMood) return;
    let key = '', model = null, controller = null, result = null, state = 'idle';
    let changedAt = Date.now(), playbackId = '';
    function describe(track) {
        if (!track) return null;
        const url = String(track.url || '');
        const match = url.match(/\/api\/music\/play\/netease\/(\d+)/);
        const netease = match ? match[1] : (track.source === 'netease' ? String(track.id || '') : '');
        // Never send signed URLs, covers, playback IDs, or chat context to inference.
        const id = netease ? 'netease:' + netease : track.id
            ? String(track.source || 'local') + ':' + track.id
            : 'metadata:' + JSON.stringify([track.name || '', track.artist || '']);
        return { track_id: id.slice(0, 240), netease_id: /^\d{1,20}$/.test(netease) ? netease : '',
            name: String(track.name || '').slice(0, 200), artist: String(track.artist || '').slice(0, 200),
            tags: Array.isArray(track.tags) ? track.tags.slice(0, 12).map(tag => String(tag).slice(0, 60)) : [] };
    }
    function sync() {
        const snapshot = window.getMusicPlaybackSnapshot?.();
        const nextModel = window.live2dManager?.currentModel;
        const track = describe(snapshot?.track);
        const nextKey = snapshot?.audible && nextModel && track
            ? JSON.stringify([snapshot.playbackId, track]) : '';
        if (nextKey === key && model === nextModel) return;
        controller?.abort(); controller = null;
        key = nextKey; model = nextModel; result = null;
        playbackId = key ? snapshot.playbackId : '';
        state = key ? 'pending' : 'idle'; changedAt = Date.now();
        window.dispatchEvent(new CustomEvent('neko-music-mood-change'));
        if (!key) return;
        const token = key, target = model, abort = new AbortController();
        controller = abort;
        const timer = window.setTimeout(() => abort.abort(), 21000);
        fetch('/api/music/mood', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(track), signal: abort.signal }).then(response => {
            if (!response.ok) throw new Error('classification unavailable');
            return response.json();
        }).then(data => {
            if (controller !== abort || key !== token || model !== target || abort.signal.aborted
                || window.live2dManager?.currentModel !== target || !window.isMusicAudible?.()) return;
            if (data.track_id !== track.track_id) return;
            result = data; state = 'ready'; changedAt = Date.now();
        }).catch(() => {
            if (controller === abort) { state = 'unavailable'; changedAt = Date.now(); }
        }).finally(() => {
            window.clearTimeout(timer);
            if (controller === abort) window.dispatchEvent(new CustomEvent('neko-music-mood-change'));
        });
    }
    window.nekoMusicMood = { sync, snapshot: () => ({ state, changedAt, result, playbackId }),
        category: () => result && result.confidence >= 0.6 ? result.category : 'unknown' };
    window.addEventListener('neko-music-playback-change', sync);
    const timer = window.setInterval(sync, 400);
    window.addEventListener('pagehide', event => {
        if (!event.persisted) { controller?.abort(); window.clearInterval(timer); }
    });
})();
