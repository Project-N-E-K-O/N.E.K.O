(function (root) {
    'use strict';
    const BASE_CONSTRAINTS = Object.freeze({ noiseSuppression: false, echoCancellation: true, autoGainControl: true, channelCount: 1 });
    let idSequence = 0;
    function operationId() {
        if (root.crypto && typeof root.crypto.randomUUID === 'function') return root.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        if (root.crypto && typeof root.crypto.getRandomValues === 'function') root.crypto.getRandomValues(bytes);
        else bytes.forEach((_, index) => { bytes[index] = Math.floor(Math.random() * 256); });
        return 'voice-' + Date.now().toString(36) + '-' + (++idSequence).toString(36) + '-' + Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    }
    function liveTrack(stream) {
        if (!stream || typeof stream.getAudioTracks !== 'function') return null;
        return stream.getAudioTracks().find(track => track.readyState === 'live') || null;
    }
    function stop(stream) {
        if (stream && typeof stream.getTracks === 'function') stream.getTracks().forEach(track => track.stop());
    }
    function gain(value) {
        const db = Number(value);
        return Number.isFinite(db) ? Math.min(25, Math.max(-5, db)) : 0;
    }
    async function open(mediaDevices, deviceId, isCurrent) {
        let stream;
        let fallback = false;
        try {
            stream = await mediaDevices.getUserMedia({ audio: deviceId ? { ...BASE_CONSTRAINTS, deviceId: { exact: deviceId } } : { ...BASE_CONSTRAINTS }, video: false });
        } catch (error) {
            if (!deviceId || !['NotFoundError', 'NotReadableError', 'OverconstrainedError'].includes(error && error.name)) throw error;
            if (!isCurrent()) throw new Error('capture_cancelled');
            stream = await mediaDevices.getUserMedia({ audio: { ...BASE_CONSTRAINTS }, video: false });
            fallback = true;
        }
        if (!isCurrent()) { stop(stream); throw new Error('capture_cancelled'); }
        const track = liveTrack(stream);
        if (!track) { stop(stream); const error = new Error('microphone_unavailable'); error.name = 'NotReadableError'; throw error; }
        const settings = typeof track.getSettings === 'function' ? track.getSettings() : {};
        return { stream, track, fallback, deviceId: settings.deviceId || '', label: track.label || '' };
    }
    const api = Object.freeze({ liveTrack, stop, gain, open, operationId, constraints: BASE_CONSTRAINTS });
    root.nekoMicrophoneInput = api;
    if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window === 'object' ? window : globalThis);
