/**
 * Official mini-game -> host voice-session control bridge.
 *
 * Mini-games may request the existing N.E.K.O microphone session to start or
 * stop, but they never own microphone capture themselves. The host page keeps
 * ownership of MicLease, provider routing, teardown, and the actual micButton
 * flow; this module only carries bounded same-origin control messages.
 */
(function () {
    'use strict';

    var CHANNEL_NAME = 'neko_game_voice_control_channel';
    var STORAGE_KEY = 'neko_game_voice_control_message';
    var WINDOW_EVENT = 'neko-game-voice-control-message';
    var STATE_POLL_INTERVAL_MS = 250;
    var COMMAND_TIMEOUT_MS = 12000;
    var senderId = 'host-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 9);
    var S = window.appState;
    var disposed = false;
    var commandInFlight = false;
    var stateTimer = null;
    var lastStateFingerprint = '';
    var storageHandler = null;
    var windowEventHandler = null;
    var gameWindowStateHandler = null;
    var transcriptionStateHandler = null;
    var voiceTranscriptHandler = null;
    var channel = null;
    var seenMessageIds = new Set();
    var seenMessageOrder = [];

    if (!S) {
        console.warn('[GameVoiceControl] appState unavailable; host bridge not started');
        return;
    }

    function currentRoute() {
        return {
            active: S.gameRouteActive === true,
            gameType: String(S.gameRouteGameType || ''),
            sessionId: String(S.gameRouteSessionId || ''),
            routeInstanceId: String(S.gameRouteInstanceId || '')
        };
    }

    function currentVoiceState(extra, routeOverride) {
        var route = routeOverride || currentRoute();
        var micButton = document.getElementById('micButton');
        var active = S.isRecording === true;
        var starting = !active && (S.voiceStartPending === true || window.isMicStarting === true);
        var transcriptionMode = String(S.gameVoiceTranscriptionMode || 'unavailable');
        var transcriptionProvider = String(S.gameVoiceTranscriptionProvider || '');
        var transcriptionReady = active && S.gameVoiceTranscriptionReady === true;
        var transcriptionReason = String(S.gameVoiceTranscriptionReason || '');
        if ((active || starting) && transcriptionMode === 'unavailable' && S.voiceInputRouteBlocked !== true) {
            transcriptionMode = 'backend_pending';
            transcriptionProvider = '';
            transcriptionReady = false;
            transcriptionReason = 'route_resolving';
        }
        if (!route.active || (!active && !starting)) {
            transcriptionMode = 'unavailable';
            transcriptionProvider = '';
            transcriptionReady = false;
            transcriptionReason = route.active ? 'voice_inactive' : 'route_inactive';
        }
        return Object.assign({
            type: 'game_voice_control_state',
            sender_id: senderId,
            timestamp: Date.now(),
            available: route.active && !!micButton && typeof window.stopMicCapture === 'function',
            route_active: route.active,
            game_type: route.gameType,
            session_id: route.sessionId,
            sdk_route_instance_id: route.routeInstanceId,
            active: active,
            starting: starting,
            muted: S.isMicMuted === true,
            busy: commandInFlight,
            capture_owner: 'host',
            transcription_mode: transcriptionMode,
            provider: transcriptionProvider,
            ready: transcriptionReady,
            transcription_reason: transcriptionReason
        }, extra || {});
    }

    function postMessage(payload, ephemeral) {
        if (disposed) return false;
        var messageId = String(payload && payload.message_id || (
            'voice-message-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 9)
        ));
        var message = Object.assign({}, payload || {}, {
            message_id: messageId,
            storage_nonce: messageId
        });
        var posted = false;
        if (channel) {
            try {
                channel.postMessage(message);
                posted = true;
            } catch (error) {
                console.warn('[GameVoiceControl] BroadcastChannel post failed; falling back:', error);
                try { channel.close(); } catch (_) { /* unusable channel */ }
                channel = null;
            }
        }
        var serialized = '';
        try {
            serialized = JSON.stringify(message);
            localStorage.setItem(STORAGE_KEY, serialized);
            if (ephemeral) {
                localStorage.removeItem(STORAGE_KEY);
                posted = true;
            } else {
                setTimeout(function () {
                    try {
                        if (localStorage.getItem(STORAGE_KEY) === serialized) {
                            localStorage.removeItem(STORAGE_KEY);
                        }
                    } catch (_) {}
                }, 0);
                posted = true;
            }
        } catch (error) {
            if (!posted) console.warn('[GameVoiceControl] state transport unavailable:', error);
        }
        try {
            if (typeof window.dispatchEvent === 'function' && typeof window.CustomEvent === 'function') {
                window.dispatchEvent(new window.CustomEvent(WINDOW_EVENT, {
                    detail: serialized ? JSON.parse(serialized) : Object.assign({}, message)
                }));
                posted = true;
            }
        } catch (error) {
            if (!posted) console.warn('[GameVoiceControl] same-document transport unavailable:', error);
        }
        return posted;
    }

    function broadcastState(extra, force, routeOverride) {
        var state = currentVoiceState(extra, routeOverride);
        var fingerprint = JSON.stringify([
            state.available,
            state.route_active,
            state.game_type,
            state.session_id,
            state.sdk_route_instance_id,
            state.active,
            state.starting,
            state.muted,
            state.busy,
            state.capture_owner,
            state.transcription_mode,
            state.provider,
            state.ready,
            state.transcription_reason,
            state.ok,
            state.reason,
            state.request_id
        ]);
        if (!force && fingerprint === lastStateFingerprint) return state;
        lastStateFingerprint = fingerprint;
        postMessage(state);
        return state;
    }

    function routeMatches(request) {
        var route = currentRoute();
        if (!route.active) return false;
        if (String(request.game_type || '') !== route.gameType) return false;
        var requestedSessionId = String(request.session_id || '');
        if (requestedSessionId && route.sessionId && requestedSessionId !== route.sessionId) return false;
        // The route generation is REQUIRED here, not merely compared when both
        // sides happen to have one. It is what keeps a non-SDK route (soccer /
        // badminton never mint one) out of voice control, and it is minted from
        // crypto on every runtime.start(), so a stale window cannot carry the
        // live one. It is not a secret and makes no claim to be: the same
        // unauthenticated GET /api/game/route/active that the page reads its
        // identity from returns it, which is exactly why a reloaded host
        // recovers without asking anyone for anything.
        var requestedRouteInstanceId = String(request.sdk_route_instance_id || '');
        if (!route.routeInstanceId || !requestedRouteInstanceId) return false;
        return requestedRouteInstanceId === route.routeInstanceId;
    }

    function routeSnapshotIsCurrent(snapshot) {
        var route = currentRoute();
        return route.active === true
            && snapshot
            && route.gameType === snapshot.gameType
            && route.sessionId === snapshot.sessionId
            && route.routeInstanceId === snapshot.routeInstanceId;
    }

    function voiceStartSettled() {
        return S.isRecording === true || (
            S.voiceStartPending !== true
            && window.isMicStarting !== true
            && !document.getElementById('micButton')?.classList.contains('active')
        );
    }

    function voiceStopSettled() {
        return S.isRecording !== true
            && S.voiceStartPending !== true
            && window.isMicStarting !== true;
    }

    async function waitFor(predicate, timeoutMs) {
        var deadline = Date.now() + timeoutMs;
        while (!disposed && Date.now() < deadline) {
            if (predicate()) return true;
            await new Promise(function (resolve) { setTimeout(resolve, 50); });
        }
        return predicate();
    }

    async function startOfficialVoiceSession() {
        if (S.isRecording === true) return true;
        if (S.voiceStartPending === true || window.isMicStarting === true) {
            await waitFor(voiceStartSettled, COMMAND_TIMEOUT_MS);
            return S.isRecording === true;
        }
        var micButton = document.getElementById('micButton');
        if (!micButton || micButton.disabled) return false;
        micButton.click();
        await waitFor(voiceStartSettled, COMMAND_TIMEOUT_MS);
        return S.isRecording === true;
    }

    async function stopOfficialVoiceSession() {
        if (voiceStopSettled()) return true;
        if (typeof window.stopMicCapture !== 'function') return false;
        await Promise.resolve(window.stopMicCapture());
        await waitFor(voiceStopSettled, COMMAND_TIMEOUT_MS);
        return voiceStopSettled();
    }

    async function handleRequest(request) {
        if (disposed || !request || request.type !== 'game_voice_control_request') return;
        if (request.sender_id === senderId) return;
        var action = String(request.action || 'query');
        var requestId = String(request.request_id || '');
        if (!['query', 'start', 'stop', 'toggle'].includes(action)) return;

        if (!routeMatches(request)) {
            broadcastState({
                ok: false,
                reason: 'route_mismatch',
                request_id: requestId,
                available: false,
                active: false,
                starting: false
            }, true, {
                active: false,
                gameType: String(request.game_type || ''),
                sessionId: String(request.session_id || ''),
                routeInstanceId: String(request.sdk_route_instance_id || '')
            });
            return;
        }
        var acceptedRoute = currentRoute();
        if (action === 'query') {
            broadcastState({ ok: true, reason: 'state', request_id: requestId }, true, acceptedRoute);
            return;
        }
        if (commandInFlight) {
            broadcastState({ ok: false, reason: 'busy', request_id: requestId }, true);
            return;
        }

        commandInFlight = true;
        broadcastState({ ok: true, reason: 'working', request_id: requestId }, true, acceptedRoute);
        try {
            var voiceWasActive = S.isRecording === true
                || S.voiceStartPending === true
                || window.isMicStarting === true;
            var effectiveAction = action === 'toggle'
                ? ((S.isRecording === true || S.voiceStartPending === true || window.isMicStarting === true) ? 'stop' : 'start')
                : action;
            var ok = effectiveAction === 'start'
                ? await startOfficialVoiceSession()
                : await stopOfficialVoiceSession();
            if (!routeSnapshotIsCurrent(acceptedRoute)) {
                // The microphone is process-global, and this module does not own
                // it -- the host page owns MicLease, teardown and the micButton
                // flow (see the header). A superseded command therefore only
                // releases what IT opened, and only when nothing took over:
                //
                //  * never re-start. `voiceWasActive` was sampled before this
                //    command ran and carries no attribution, so "restoring" it
                //    could switch the microphone on for a route that ended and
                //    a user who never asked.
                //  * never stop on a route -> route handoff. The replacement
                //    route (or the user) may be mid-utterance on that capture,
                //    and tearing it down loses what they were saying with no
                //    transcript and nothing logged.
                // Nothing is torn down here at all. Two narrower attempts both
                // failed on the same rock: this module cannot tell "the capture
                // I started" from "a capture something else now owns", because
                // the microphone is process-global and no capture identity is
                // exposed to the bridge.
                //  * stopping whenever the mic ended up on killed a replacement
                //    route's (or the user's) live capture mid-utterance;
                //  * stopping only what this command clicked, and only once no
                //    route remained, still killed ordinary chat capture -- route
                //    exit resumes it via startMicCapture() in app-websocket.js,
                //    so "no route" does not mean "no owner".
                // The host page owns MicLease, teardown and the micButton flow
                // (see the header); a command whose route is gone reports and
                // stops there. A microphone left on is visible in the UI and the
                // host's own route-exit path decides its fate.
                broadcastState({
                    ok: false,
                    reason: 'route_superseded',
                    request_id: requestId,
                    available: false,
                    active: false,
                    starting: false
                }, true, {
                    active: false,
                    gameType: acceptedRoute.gameType,
                    sessionId: acceptedRoute.sessionId,
                    routeInstanceId: acceptedRoute.routeInstanceId
                });
                return;
            }
            broadcastState({
                ok: ok,
                reason: ok ? (effectiveAction === 'start' ? 'started' : 'stopped') : (effectiveAction + '_failed'),
                request_id: requestId
            }, true, acceptedRoute);
        } catch (error) {
            console.warn('[GameVoiceControl] host command failed:', error);
            broadcastState({ ok: false, reason: 'command_failed', request_id: requestId }, true, acceptedRoute);
        } finally {
            commandInFlight = false;
            broadcastState({}, true);
        }
    }

    function acceptMessage(message) {
        var messageId = String(message && (message.message_id || message.storage_nonce) || '');
        if (messageId) {
            if (seenMessageIds.has(messageId)) return;
            seenMessageIds.add(messageId);
            seenMessageOrder.push(messageId);
            while (seenMessageOrder.length > 128) {
                seenMessageIds.delete(seenMessageOrder.shift());
            }
        }
        void handleRequest(message);
    }

    try {
        if (typeof BroadcastChannel === 'function') {
            channel = new BroadcastChannel(CHANNEL_NAME);
            channel.onmessage = function (event) { acceptMessage(event && event.data); };
        }
    } catch (error) {
        channel = null;
        console.warn('[GameVoiceControl] BroadcastChannel unavailable; using localStorage fallback:', error);
    }

    // Always listen to the fallback path. Different Electron webviews can
    // disagree about BroadcastChannel availability during reload; listening
    // on both sides keeps that partial-failure case usable without posting the
    // same command twice.
    storageHandler = function (event) {
        if (!event || event.key !== STORAGE_KEY || !event.newValue) return;
        try { acceptMessage(JSON.parse(event.newValue)); }
        catch (_) {}
    };
    window.addEventListener('storage', storageHandler);
    windowEventHandler = function (event) {
        acceptMessage(event && event.detail);
    };
    window.addEventListener(WINDOW_EVENT, windowEventHandler);

    // app-websocket's reconnect reconciliation dispatches this event from an
    // authoritative /api/game/route/active read. Mirror it into appState so a
    // host page reloaded after the game opened can still accept voice control.
    gameWindowStateHandler = function (event) {
        var detail = event && event.detail ? event.detail : {};
        var action = String(detail.action || '');
        var incomingSessionId = String(detail.sessionId || '');
        var incomingRouteInstanceId = String(detail.routeInstanceId || '');
        if (action === 'opened') {
            S.gameRouteActive = true;
            S.gameRouteGameType = String(detail.gameType || '');
            S.gameRouteLanlanName = String(detail.lanlanName || '');
            S.gameRouteSessionId = incomingSessionId;
            S.gameRouteInstanceId = incomingRouteInstanceId;
        } else if (action === 'closed') {
            var currentSessionId = String(S.gameRouteSessionId || '');
            if (incomingSessionId && currentSessionId && incomingSessionId !== currentSessionId) return;
            var currentRouteInstanceId = String(S.gameRouteInstanceId || '');
            if (
                incomingRouteInstanceId
                && currentRouteInstanceId
                && incomingRouteInstanceId !== currentRouteInstanceId
            ) return;
            var closingRoute = {
                active: false,
                gameType: String(detail.gameType || S.gameRouteGameType || ''),
                sessionId: incomingSessionId || currentSessionId,
                routeInstanceId: incomingRouteInstanceId || currentRouteInstanceId
            };
            S.gameRouteActive = false;
            broadcastState({
                available: false,
                active: false,
                starting: false,
                reason: 'route_closed'
            }, true, {
                active: false,
                gameType: closingRoute.gameType,
                sessionId: closingRoute.sessionId,
                routeInstanceId: closingRoute.routeInstanceId
            });
            S.gameRouteGameType = '';
            S.gameRouteLanlanName = '';
            S.gameRouteSessionId = '';
            S.gameRouteInstanceId = '';
        } else {
            return;
        }
        if (action === 'opened') broadcastState({}, true);
    };
    window.addEventListener('neko-game-window-state-change', gameWindowStateHandler);

    transcriptionStateHandler = function () {
        broadcastState({}, true);
    };
    window.addEventListener(
        'neko-game-voice-transcription-state-change',
        transcriptionStateHandler
    );

    // app-websocket emits this only for a non-empty final transcript. Relay
    // the normalized host result to the active game session; games never see
    // provider responses or microphone audio through this bridge.
    voiceTranscriptHandler = function (event) {
        var route = currentRoute();
        var detail = event && event.detail ? event.detail : {};
        var transcript = String(detail.text || '').trim();
        if (!route.active || !transcript) return;
        var sourceGameType = String(detail.gameType || '');
        var sourceSessionId = String(detail.sessionId || '');
        var sourceRouteInstanceId = String(detail.routeInstanceId || '');
        if (!sourceGameType || !sourceSessionId) return;
        if (sourceGameType !== route.gameType || sourceSessionId !== route.sessionId) return;
        if (route.routeInstanceId && sourceRouteInstanceId !== route.routeInstanceId) return;
        postMessage({
            type: 'game_voice_transcript',
            sender_id: senderId,
            timestamp: Date.now(),
            game_type: route.gameType,
            session_id: route.sessionId,
            sdk_route_instance_id: route.routeInstanceId,
            request_id: String(detail.requestId || ''),
            source: String(detail.source || 'voice'),
            text: transcript
        }, true);
    };
    window.addEventListener('neko:user-voice-content-received', voiceTranscriptHandler);

    function dispose() {
        if (disposed) return;
        disposed = true;
        if (stateTimer) {
            clearInterval(stateTimer);
            stateTimer = null;
        }
        if (storageHandler) {
            window.removeEventListener('storage', storageHandler);
            storageHandler = null;
        }
        if (windowEventHandler) {
            window.removeEventListener(WINDOW_EVENT, windowEventHandler);
            windowEventHandler = null;
        }
        if (gameWindowStateHandler) {
            window.removeEventListener('neko-game-window-state-change', gameWindowStateHandler);
            gameWindowStateHandler = null;
        }
        if (transcriptionStateHandler) {
            window.removeEventListener(
                'neko-game-voice-transcription-state-change',
                transcriptionStateHandler
            );
            transcriptionStateHandler = null;
        }
        if (voiceTranscriptHandler) {
            window.removeEventListener('neko:user-voice-content-received', voiceTranscriptHandler);
            voiceTranscriptHandler = null;
        }
        if (channel) {
            channel.onmessage = null;
            try { channel.close(); } catch (_) {}
            channel = null;
        }
        seenMessageIds.clear();
        seenMessageOrder.length = 0;
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
    }

    stateTimer = setInterval(function () { broadcastState({}, false); }, STATE_POLL_INTERVAL_MS);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
    broadcastState({}, true);
})();
