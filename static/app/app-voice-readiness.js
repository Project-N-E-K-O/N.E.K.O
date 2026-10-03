(function (root) {
    'use strict';
    root.createVoiceCaptureReadiness = function (S, stop) {
        const makeId = () => root.nekoMicrophoneInput ? root.nekoMicrophoneInput.operationId() : root.crypto.randomUUID();
        let socket = null;
        let localSession = makeId();
        let revision = 0;
        let isolation = null;
        let activation = null;
        let retiredSessions = new Set();
        const waiting = new Map();
        const children = new Set();
        const bridge = root.nekoVoiceEnrollment;
        let panel = null;
        let label = null;
        let retry = null;
        let restart = null;
        let restartRequired = false;
        let retryOperation = null;
        function clearIsolation(owned) {
            if (!owned) return;
            if (owned.expiry !== null) root.clearTimeout(owned.expiry);
            owned.expiry = null;
            if (isolation === owned) isolation = null;
        }
        function boundIsolation(owned, milliseconds) {
            if (owned.expiry !== null) root.clearTimeout(owned.expiry);
            owned.expiry = root.setTimeout(() => clearIsolation(owned), milliseconds);
        }
        async function compensatePreview(details) {
            const owned = isolation && isolation.operationId === details.request_id ? isolation : null;
            if (owned) {
                owned.token = details.token;
                boundIsolation(owned, Math.max(1, Math.min(60, Number(details.ttl_seconds) || 60)) * 1000);
            }
            try { await control('preview_end', { token: details.token }); clearIsolation(owned); }
            catch (_) { /* The matching ticket expiry bounds an uncertain release. */ }
        }
        function t(key, fallback) { const text = root.t && root.t(key); return text && text !== key ? text : fallback; }
        function reset() {
            if (activation && activation.session_id) retiredSessions.add(activation.session_id);
            if (retiredSessions.size > 32) retiredSessions.delete(retiredSessions.values().next().value);
            activation = null;
            restartRequired = false;
            retryOperation = null;
            if (retry) retry.disabled = false;
            S.voiceSessionActivationIdentity = '';
            S.voiceSessionActivationRevision = 0;
            S.voiceSessionActivationState = '';
            document.documentElement.removeAttribute('data-voice-session-activation-state');
            render();
        }
        function render() {
            if (!panel) return;
            panel.hidden = !activation || S.isRecording !== true || ['disabled', 'closed'].includes(activation.state);
            if (!activation) return;
            label.textContent = t('voiceIdentity.activation_' + activation.state, activation.state);
            if (activation.reason && activation.state === 'unavailable') label.textContent += ' — ' + t('voiceIdentity.activationReason_' + activation.reason, t('voiceIdentity.activationRepair', 'Check resources and retry voice activation.'));
            if (restartRequired) label.textContent = t('voiceIdentity.activationRestartRequired', 'Close and reopen the microphone to start a new voice session.');
            retry.hidden = restartRequired || activation.state !== 'unavailable';
            restart.hidden = true;
            retry.textContent = t('voiceIdentity.activationRetry', 'Retry activation');
            restart.textContent = t('voiceIdentity.activationRestart', 'Restart voice session');
        }
        function mount() {
            panel = document.createElement('aside');
            panel.className = 'voice-session-readiness';
            panel.hidden = true;
            panel.setAttribute('aria-label', t('voiceIdentity.activationTitle', 'Voice activation'));
            label = document.createElement('span'); label.setAttribute('role', 'status'); label.setAttribute('aria-live', 'polite');
            retry = document.createElement('button'); retry.type = 'button'; retry.textContent = t('voiceIdentity.activationRetry', 'Retry activation');
            retry.addEventListener('click', async () => {
                if (!activation || !S.isRecording || restartRequired) return;
                const current = activation;
                const requestSocket = S.socket;
                const ownedRetry = {};
                retryOperation = ownedRetry;
                retry.disabled = true;
                try { await control('activation_retry', { ...current }); } catch (error) { if (retryOperation === ownedRetry && S.socket === requestSocket && S.isRecording && activation && activation.session_id === current.session_id && activation.microphone_generation === current.microphone_generation) {
                    restartRequired = error.message === 'voice_session_restart_required' || (!error.voiceControlConfirmed && error.message === 'voice_control_timeout');
                    render();
                    label.textContent = restartRequired ? t('voiceIdentity.activationRestartRequired', 'Close and reopen the microphone to start a new voice session.') : t('voiceIdentity.activationRetryFailed', 'Retry failed. Check the connection and resources.');
                } }
                finally { if (retryOperation === ownedRetry) { retryOperation = null; retry.disabled = false; } }
            });
            restart = document.createElement('button'); restart.type = 'button'; restart.hidden = true;
            panel.append(label, retry, restart);
            document.body.appendChild(panel);
        }
        async function register(recording) {
            if (socket !== S.socket) {
                socket = S.socket; localSession = makeId(); revision = 0;
                reset();
            }
            const current = { sessionId: localSession, revision: String(++revision), recording };
            if (bridge) {
                const result = await bridge.registerCapture(current);
                if (!result || result.accepted !== true || socket !== S.socket) throw new Error('capture_owner_unavailable');
            }
            return current;
        }
        function control(event, extra, requestId) {
            const ownerSocket = S.socket;
            if (!ownerSocket || ownerSocket.readyState !== 1) return Promise.reject(new Error('capture_owner_unavailable'));
            const id = requestId || makeId();
            return new Promise((resolve, reject) => {
                const timeoutMs = event === 'activation_retry' ? 45000 : 8000;
                const timeout = root.setTimeout(() => { waiting.delete(id); reject(new Error('voice_control_timeout')); }, timeoutMs);
                waiting.set(id, { socket: ownerSocket, event, resolve, reject, timeout });
                try { ownerSocket.send(JSON.stringify({ action: 'voice_identity_control', event, request_id: id, ...(extra || {}) })); }
                catch (error) { root.clearTimeout(timeout); waiting.delete(id); reject(error); }
            });
        }
        function controlResult(details, sourceSocket) {
            const pending = details && waiting.get(details.request_id);
            if (!pending && details && details.event === 'preview_begin' && details.ok === true && details.token && sourceSocket === S.socket) {
                compensatePreview(details);
                return;
            }
            if (!pending || pending.socket !== sourceSocket || sourceSocket !== S.socket || pending.event !== details.event) return;
            waiting.delete(details.request_id); root.clearTimeout(pending.timeout);
            if (details.ok === true) pending.resolve(details); else {
                const error = new Error(details.reason || 'voice_control_failed');
                error.voiceControlConfirmed = true;
                pending.reject(error);
            }
        }
        async function prepare(request) {
            if (request.event === 'release') {
                // Release owns its token even when a newer local operation has
                // replaced it; the server matches the token without touching the
                // successor. Never discard an old cleanup because a flag changed.
                if (request.token) await control('preview_end', { token: request.token });
                if (isolation && request.operationId === isolation.operationId && request.token === isolation.token) {
                    clearIsolation(isolation);
                }
                return { stopped: true, sessionId: request.sessionId || localSession, revision: request.revision || String(revision) };
            }
            if (request.sessionId && (request.sessionId !== localSession || request.revision !== String(revision))) throw new Error('capture_cancelled');
            if (isolation) throw new Error('capture_owner_busy');
            const atSocket = S.socket;
            const identity = { sessionId: localSession, revision: String(revision) };
            const owned = { operationId: request.operationId, token: null, expiry: null };
            isolation = owned;
            // A lost begin/release acknowledgement must never leave the client
            // permanently fenced. This includes the begin response budget and
            // the maximum server ticket lifetime; expiry never restarts input.
            boundIsolation(owned, 68000);
            // Stop fences pending microphone setup and tears down the graph before
            // asking the server to seal and drain its common PCM input route.
            let beginSent = false;
            let physicalStopped = false;
            try {
                await stop();
                const hasLiveTrack = S.stream && typeof S.stream.getAudioTracks === 'function' && S.stream.getAudioTracks().some(track => track.readyState === 'live');
                if (isolation !== owned || atSocket !== S.socket || identity.sessionId !== localSession || identity.revision !== String(revision) || S.isRecording || hasLiveTrack) throw new Error('capture_cancelled');
                physicalStopped = true;
                beginSent = true;
                const result = await control('preview_begin', {}, request.operationId);
                if (isolation !== owned || atSocket !== S.socket || identity.sessionId !== localSession || identity.revision !== String(revision) || !result.token || !(result.ttl_seconds > 0 && result.ttl_seconds <= 60)) {
                    if (result.token) compensatePreview(result);
                    throw new Error('capture_cancelled');
                }
                owned.token = result.token;
                boundIsolation(owned, result.ttl_seconds * 1000);
                return { ...identity, stopped: true, physicalStopped: true, token: result.token, ttl_seconds: result.ttl_seconds, noise_reduction_enabled: result.noise_reduction_enabled };
            } catch (error) {
                if (!beginSent || error.voiceControlConfirmed) clearIsolation(owned);
                const hasLiveTrack = S.stream && typeof S.stream.getAudioTracks === 'function' && S.stream.getAudioTracks().some(track => track.readyState === 'live');
                if (physicalStopped && atSocket === S.socket && identity.sessionId === localSession && identity.revision === String(revision) && !S.isRecording && !hasLiveTrack) {
                    // Plain data survives contextBridge; custom Error fields do
                    // not. This confirms capture only, never server isolation.
                    return { ...identity, stopped: false, physicalStopped: true, reason: error.message };
                }
                throw error;
            }
        }
        function activationStatus(details, sourceSocket) {
            if (!details || sourceSocket !== S.socket || S.isRecording !== true || restartRequired || retiredSessions.has(details.session_id)) return false;
            if (!['disabled', 'preparing', 'waiting', 'verifying', 'replaying', 'active', 'unavailable', 'closed'].includes(details.state)) return false;
            if (activation) {
                if (details.session_id !== activation.session_id) {
                    // A session transition must be signalled by preparing. A late
                    // old active/unavailable notification cannot claim the new mic.
                    if (details.state !== 'preparing') return false;
                    retiredSessions.add(activation.session_id);
                    if (retiredSessions.size > 32) retiredSessions.delete(retiredSessions.values().next().value);
                } else {
                    const generations = ['microphone_generation', 'route_generation', 'profile_revision', 'permission_revision'];
                    if (generations.some(key => Number(details[key]) < Number(activation[key]))) return false;
                    const sameGeneration = generations.every(key => details[key] === activation[key]);
                    if (sameGeneration && Number(details.revision) <= Number(activation.revision)) return false;
                }
            }
            activation = { ...details };
            if (details.state !== 'unavailable') restartRequired = false;
            render(); return true;
        }
        if (bridge) {
            bridge.onStopCapture(prepare);
            bridge.onOpen(request => {
                if (request.sessionId !== localSession || request.revision !== String(revision)) return;
                root.open(request.url, 'neko-voice-enrollment');
            });
            register(false).catch(() => {});
        }
        root.addEventListener('message', async event => {
            if (event.origin !== root.location.origin || !event.source || !event.data || typeof event.data.operationId !== 'string') return;
            if (event.data.type === 'neko-voice-enrollment-release' && children.has(event.source)) {
                children.delete(event.source);
                prepare({ ...event.data, event: 'release' }).catch(() => {});
                return;
            }
            if (event.source.opener !== root || event.data.type !== 'neko-voice-enrollment-prepare') return;
            children.add(event.source);
            if (children.size > 32) children.delete(children.values().next().value);
            let result;
            try { result = await prepare(event.data); } catch (_) { result = { stopped: false }; }
            event.source.postMessage({ type: 'neko-voice-enrollment-stopped', operationId: event.data.operationId, ...result }, root.location.origin);
        });
        root.addEventListener('localechange', render);
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true }); else mount();
        return {
            register, reset, controlResult, activationStatus,
            blocked: () => !!isolation || restartRequired,
            disconnected: () => { reset(); waiting.forEach(p => { root.clearTimeout(p.timeout); p.reject(new Error('capture_owner_unavailable')); }); waiting.clear(); },
            stopped: () => { reset(); if (bridge && !isolation) register(false).catch(() => {}); }
        };
    };
})(window);
