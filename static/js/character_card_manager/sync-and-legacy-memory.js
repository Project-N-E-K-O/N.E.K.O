// Part responsibility: cross-page character synchronization, unload cleanup, and legacy-memory management.

const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';
const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';
const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';

function handleCloudsaveCharacterSync(data) {
    if (!data || data.type !== CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE) return;
    if (hasUnsavedNewCatgirlDraft()) {
        console.log('[CharacterCardManager] Unsaved draft detected, deferring sync refresh');
        return;
    }
    console.log('[CharacterCardManager] Received cloudsave sync:', data.action);
    loadCharacterCards().catch(e => console.warn('Cloudsave sync refresh failed:', e));
}

(function initCloudsaveSync() {
    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);
            channel.onmessage = function (event) {
                handleCloudsaveCharacterSync(event.data);
            };
        } catch (e) {
            console.warn('BroadcastChannel init failed:', e);
        }
    }

    window.addEventListener('storage', function (event) {
        if (event.key !== CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY) return;
        try {
            const data = JSON.parse(event.newValue);
            handleCloudsaveCharacterSync(data);
        } catch (e) {
            console.warn('localStorage sync parse failed:', e);
        }
    });
})();

// sendBeacon 生命周期
window.addEventListener('beforeunload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});

window.addEventListener('unload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});
