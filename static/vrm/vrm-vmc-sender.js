/**
 * VRM -> VMC bridge.
 *
 * Samples three-vrm in the render loop and sends frames through the isolated
 * /api/vmc/ws socket. The backend performs coordinate/name conversion and
 * emits OSC/UDP; chat WebSocket traffic is never involved.
 */
(function () {
    'use strict';

    if (
        window.vrmVmcSender
        && window.vrmVmcSender.__isVmcLazyLoader !== true
    ) {
        return;
    }

    const HUMANOID_BONE_NAMES = Object.freeze([
        'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
        'leftEye', 'rightEye', 'jaw',
        'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
        'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
        'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'leftToes',
        'rightUpperLeg', 'rightLowerLeg', 'rightFoot', 'rightToes',
        'leftThumbMetacarpal', 'leftThumbProximal', 'leftThumbDistal',
        'leftIndexProximal', 'leftIndexIntermediate', 'leftIndexDistal',
        'leftMiddleProximal', 'leftMiddleIntermediate', 'leftMiddleDistal',
        'leftRingProximal', 'leftRingIntermediate', 'leftRingDistal',
        'leftLittleProximal', 'leftLittleIntermediate', 'leftLittleDistal',
        'rightThumbMetacarpal', 'rightThumbProximal', 'rightThumbDistal',
        'rightIndexProximal', 'rightIndexIntermediate', 'rightIndexDistal',
        'rightMiddleProximal', 'rightMiddleIntermediate', 'rightMiddleDistal',
        'rightRingProximal', 'rightRingIntermediate', 'rightRingDistal',
        'rightLittleProximal', 'rightLittleIntermediate', 'rightLittleDistal',
    ]);

    const DEFAULT_SEND_RATE_HZ = 60;
    const STATUS_POLL_INTERVAL_MS = 5000;
    const RECONNECT_INITIAL_DELAY_MS = 500;
    const RECONNECT_MAX_DELAY_MS = 10000;
    const SOURCE_IDLE_TIMEOUT_MS = 3000;
    const FRAME_ACK_TIMEOUT_MS = 1500;
    const MAX_BUFFERED_BYTES = 256 * 1024;
    const CSRF_HEADER_NAME = 'X-CSRF-Token';
    // VMC owns its coordinate origin. vrm.scene is also moved/scaled/rotated
    // by webpage layout and drag controls, so it must never be used as the
    // transmitted avatar root.
    const VMC_LOCAL_ROOT = Object.freeze({
        px: 0, py: 0, pz: 0,
        qx: 0, qy: 0, qz: 0, qw: 1,
    });

    const state = {
        enabled: false,
        sendRateHz: DEFAULT_SEND_RATE_HZ,
        minIntervalSec: 1 / DEFAULT_SEND_RATE_HZ,
        nextSampleTs: 0,
        nextFrameSequence: 1,
        ackWaiters: new Map(),
        ws: null,
        wsReady: false,
        connectPromise: null,
        reconnectTimer: null,
        reconnectAttempts: 0,
        reconnectRefreshAuth: false,
        statusPollTimer: null,
        controlGeneration: 0,
        statusRequestSequence: 0,
        sourceActive: false,
        lastSourceSampleMs: 0,
        sourceIdleTimer: null,
        releaseGeneration: 0,
        releaseInProgress: false,
        bonesBuf: [],
        exprBuf: [],
        currentVrm: null,
        knownExpressionNames: new Set(),
        retiringExpressionNames: new Set(),
        tPoseDeadline: 0,
        tPoseGeneration: 0,
        samplingSuspended: false,
        samplingSuspensionGeneration: 0,
    };

    function roundTransform(value) {
        return Math.round(value * 1e6) / 1e6;
    }

    async function getCsrfToken(forceRefresh) {
        try {
            const security = window.nekoLocalMutationSecurity;
            if (security) {
                if (forceRefresh && typeof security.refreshToken === 'function') {
                    await security.refreshToken();
                }
                if (typeof security.getMutationHeaders === 'function') {
                    const headers = await security.getMutationHeaders();
                    const token = headers && headers[CSRF_HEADER_NAME];
                    if (typeof token === 'string' && token) return token;
                }
            }
        } catch (_) { /* use direct page-config fallback */ }

        try {
            const response = await fetch('/api/config/page_config', {
                cache: 'no-store',
                credentials: 'same-origin',
            });
            if (!response.ok) return '';
            const data = await response.json();
            return typeof data.autostart_csrf_token === 'string'
                ? data.autostart_csrf_token : '';
        } catch (_) {
            return '';
        }
    }

    async function mutationFetch(path, body) {
        async function send(forceRefresh) {
            const token = await getCsrfToken(forceRefresh);
            const headers = {};
            if (token) headers[CSRF_HEADER_NAME] = token;
            const options = {
                method: 'POST',
                credentials: 'same-origin',
                headers,
            };
            if (body !== undefined) {
                headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify(body);
            }
            return fetch(path, options);
        }

        let response = await send(false);
        if (response.status === 403) response = await send(true);
        return response;
    }

    function websocketUrl() {
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return scheme + '//' + window.location.host + '/api/vmc/ws';
    }

    function closeWebSocket() {
        state.wsReady = false;
        for (const waiter of state.ackWaiters.values()) {
            clearTimeout(waiter.timeout);
            waiter.resolve(false);
        }
        state.ackWaiters.clear();
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
        state.reconnectAttempts = 0;
        state.reconnectRefreshAuth = false;
        const socket = state.ws;
        state.ws = null;
        if (socket && socket.readyState < WebSocket.CLOSING) {
            try { socket.close(1000, 'VMC disabled'); } catch (_) { /* ignored */ }
        }
    }

    function clearSourceState() {
        state.sourceActive = false;
        state.lastSourceSampleMs = 0;
        if (state.sourceIdleTimer) {
            clearTimeout(state.sourceIdleTimer);
            state.sourceIdleTimer = null;
        }
        state.currentVrm = null;
        state.knownExpressionNames.clear();
        state.retiringExpressionNames.clear();
        state.tPoseDeadline = 0;
    }

    function releaseSource(vrm) {
        if (vrm && state.currentVrm && vrm !== state.currentVrm) return false;

        const releaseGeneration = ++state.releaseGeneration;
        const releaseSocket = state.ws;
        const expressionNames = Array.from(new Set([
            ...state.knownExpressionNames,
            ...state.retiringExpressionNames,
        ]));
        let releaseAck = null;
        if (state.wsReady && releaseSocket) {
            const expressionChunks = expressionNames.length > 0
                ? Array.from(
                    { length: Math.ceil(expressionNames.length / 256) },
                    (_, index) => expressionNames.slice(index * 256, (index + 1) * 256)
                )
                : [[]];
            // Send sequentially so a large custom expression set cannot trip
            // the browser bufferedAmount guard halfway through release.
            releaseAck = (async function () {
                for (const [index, chunk] of expressionChunks.entries()) {
                    const result = sendFrameEnvelope({
                        root: VMC_LOCAL_ROOT,
                        bones: [],
                        expressions: chunk.map(name => ({ name, value: 0 })),
                        t_pose: false,
                        t_pose_generation: state.tPoseGeneration,
                        source_released: index === expressionChunks.length - 1,
                        ts: Date.now(),
                    }, {
                        messageType: 'release',
                        requireAck: true,
                        allowInactive: true,
                    });
                    if (!result || !result.ackPromise) return false;
                    if (!await result.ackPromise) return false;
                }
                return true;
            })();
        }

        clearSourceState();
        if (releaseAck) {
            state.releaseInProgress = true;
            releaseAck.finally(function () {
                if (releaseGeneration !== state.releaseGeneration) return;
                state.releaseInProgress = false;
                if (!state.sourceActive && state.ws === releaseSocket) {
                    closeWebSocket();
                }
            });
        } else {
            state.releaseInProgress = false;
            closeWebSocket();
        }
        return true;
    }

    function checkSourceActivity() {
        state.sourceIdleTimer = null;
        if (!state.sourceActive) return;
        const idleMs = performance.now() - state.lastSourceSampleMs;
        if (idleMs >= SOURCE_IDLE_TIMEOUT_MS) {
            releaseSource(state.currentVrm);
            return;
        }
        state.sourceIdleTimer = setTimeout(
            checkSourceActivity,
            Math.max(1, SOURCE_IDLE_TIMEOUT_MS - idleMs)
        );
    }

    function markSourceActive() {
        state.sourceActive = true;
        state.lastSourceSampleMs = performance.now();
        if (!state.sourceIdleTimer) {
            state.sourceIdleTimer = setTimeout(
                checkSourceActivity,
                SOURCE_IDLE_TIMEOUT_MS
            );
        }
    }

    function scheduleReconnect(refreshAuth) {
        if (!state.enabled || !state.sourceActive) return;
        // An early `error` may be followed by a more informative 4403 close.
        // Preserve the stronger token-refresh request even when a retry timer
        // has already been scheduled.
        state.reconnectRefreshAuth =
            state.reconnectRefreshAuth || !!refreshAuth;
        if (state.reconnectTimer) return;
        const delay = Math.min(
            RECONNECT_INITIAL_DELAY_MS * Math.pow(2, state.reconnectAttempts),
            RECONNECT_MAX_DELAY_MS
        );
        state.reconnectAttempts += 1;
        state.reconnectTimer = setTimeout(function () {
            state.reconnectTimer = null;
            const shouldRefreshAuth = state.reconnectRefreshAuth;
            state.reconnectRefreshAuth = false;
            ensureWebSocket(shouldRefreshAuth);
        }, delay);
    }

    function beginControlMutation() {
        state.controlGeneration += 1;
        return state.controlGeneration;
    }

    function finishControlMutation(generation) {
        if (state.controlGeneration !== generation) return false;
        // Invalidate status requests that started while this mutation was in
        // flight before publishing the mutation's result to local state.
        state.controlGeneration += 1;
        return true;
    }

    function ensureWebSocket(forceTokenRefresh) {
        if (!state.enabled || !state.sourceActive) return Promise.resolve(false);
        // The render loop calls this function whenever a frame cannot be sent.
        // Respect the scheduled backoff instead of reconnecting on the next
        // animation frame and bypassing scheduleReconnect().
        if (state.reconnectTimer) return Promise.resolve(false);
        if (
            state.ws
            && state.ws.readyState === WebSocket.OPEN
            && state.wsReady
        ) {
            return Promise.resolve(true);
        }
        if (state.connectPromise) return state.connectPromise;

        state.connectPromise = (async function () {
            const token = await getCsrfToken(!!forceTokenRefresh);
            if (!token || !state.enabled) {
                if (!token && state.enabled) scheduleReconnect(true);
                return false;
            }
            return new Promise(function (resolve) {
                let settled = false;
                const socket = new WebSocket(websocketUrl());
                state.ws = socket;
                state.wsReady = false;

                const timeout = setTimeout(function () {
                    if (!settled) {
                        settled = true;
                        try { socket.close(4408, 'VMC auth timeout'); } catch (_) { /* ignored */ }
                        scheduleReconnect(false);
                        resolve(false);
                    }
                }, 5000);

                socket.onopen = function () {
                    socket.send(JSON.stringify({ type: 'auth', csrf_token: token }));
                };
                socket.onmessage = function (event) {
                    try {
                        const message = JSON.parse(event.data);
                        if (
                            state.ws === socket
                            && message
                            && message.type === 'frame_ack'
                            && Number.isInteger(message.sequence)
                        ) {
                            const waiter = state.ackWaiters.get(message.sequence);
                            if (waiter) {
                                clearTimeout(waiter.timeout);
                                state.ackWaiters.delete(message.sequence);
                                waiter.resolve(message.sent === true);
                            }
                            return;
                        }
                        if (
                            state.ws === socket
                            && message
                            && message.type === 'ready'
                        ) {
                            state.wsReady = true;
                            state.reconnectAttempts = 0;
                            state.reconnectRefreshAuth = false;
                            if (state.reconnectTimer) {
                                clearTimeout(state.reconnectTimer);
                                state.reconnectTimer = null;
                            }
                            clearTimeout(timeout);
                            if (!settled) {
                                settled = true;
                                resolve(true);
                            }
                            console.info('[VRM-VMC] dedicated WebSocket ready');
                        }
                    } catch (_) { /* ignore unknown control messages */ }
                };
                socket.onerror = function () {
                    if (!settled) {
                        clearTimeout(timeout);
                        settled = true;
                        resolve(false);
                    }
                    // Some WebView implementations emit error without a
                    // timely close event. Force close so the reconnect path
                    // cannot remain stuck on a half-open socket.
                    try { socket.close(); } catch (_) { /* ignored */ }
                    if (state.ws === socket) scheduleReconnect(false);
                };
                socket.onclose = function (event) {
                    clearTimeout(timeout);
                    const isCurrentSocket = state.ws === socket;
                    if (isCurrentSocket) {
                        state.wsReady = false;
                        state.ws = null;
                    }
                    if (!settled) {
                        settled = true;
                        resolve(false);
                    }
                    if (state.enabled && isCurrentSocket) {
                        console.warn(
                            '[VRM-VMC] WebSocket closed; reconnecting',
                            'code=' + event.code,
                            'reason=' + (event.reason || 'none')
                        );
                        scheduleReconnect(event.code === 4403);
                    }
                };
            });
        })().catch(function (error) {
            console.warn('[VRM-VMC] WebSocket setup failed; retrying with backoff:', error);
            scheduleReconnect(false);
            return false;
        }).finally(function () {
            state.connectPromise = null;
        });
        return state.connectPromise;
    }

    function sendFrameEnvelope(payload, options) {
        const config = options || {};
        const messageType = config.messageType || 'frame';
        const requireAck = config.requireAck === true;
        const allowInactive = config.allowInactive === true;
        const socket = state.ws;
        if (
            !state.wsReady
            || !socket
            || socket.readyState !== WebSocket.OPEN
            || (!allowInactive && !state.sourceActive)
        ) {
            ensureWebSocket();
            return null;
        }
        // Drop at the producer when the server/UDP path is slower than render.
        if (socket.bufferedAmount > MAX_BUFFERED_BYTES) return null;
        const sequence = state.nextFrameSequence++;
        let ackPromise = null;
        if (requireAck) {
            ackPromise = new Promise(function (resolve) {
                const timeout = setTimeout(function () {
                    state.ackWaiters.delete(sequence);
                    resolve(false);
                }, FRAME_ACK_TIMEOUT_MS);
                state.ackWaiters.set(sequence, { resolve, timeout });
            });
        }
        try {
            socket.send(JSON.stringify({
                type: messageType,
                sequence,
                require_ack: requireAck,
                payload,
            }));
            return { sequence, ackPromise };
        } catch (_) {
            const waiter = state.ackWaiters.get(sequence);
            if (waiter) {
                clearTimeout(waiter.timeout);
                state.ackWaiters.delete(sequence);
                waiter.resolve(false);
            }
            state.wsReady = false;
            try { socket.close(); } catch (_) { /* ignored */ }
            return null;
        }
    }

    function updateStatusPolling() {
        if (!state.enabled) {
            if (state.statusPollTimer) {
                clearInterval(state.statusPollTimer);
                state.statusPollTimer = null;
            }
            return;
        }
        if (!state.statusPollTimer) {
            state.statusPollTimer = setInterval(
                syncStatusFromBackend,
                STATUS_POLL_INTERVAL_MS
            );
        }
    }

    async function syncStatusFromBackend() {
        const controlGeneration = state.controlGeneration;
        const requestSequence = ++state.statusRequestSequence;
        const samplingSuspensionGeneration =
            state.samplingSuspensionGeneration;
        try {
            const response = await fetch('/api/vmc/status', {
                credentials: 'same-origin',
            });
            if (!response.ok) return;
            const data = await response.json();
            if (!data || data.success === false) return;
            if (
                controlGeneration !== state.controlGeneration
                || requestSequence !== state.statusRequestSequence
            ) {
                return;
            }

            state.enabled = !!data.enabled;
            window.__NEKO_VMC_ACTIVE__ = state.enabled;
            updateStatusPolling();
            if (!state.enabled) clearSourceState();
            applySendRate(data.send_rate_hz);
            if (
                state.enabled
                && state.samplingSuspended
                && samplingSuspensionGeneration
                    === state.samplingSuspensionGeneration
            ) {
                state.samplingSuspended = false;
                resetSampleSchedule();
                console.info('[VRM-VMC] sampling resumed after backend status sync');
            }
            if (state.enabled && state.sourceActive) ensureWebSocket();
            else if (!state.enabled || !state.releaseInProgress) closeWebSocket();

            if (data.t_pose_requested) {
                const duration = Number.isFinite(data.t_pose_duration_sec)
                    && data.t_pose_duration_sec > 0
                    ? data.t_pose_duration_sec : 2.0;
                state.tPoseDeadline = performance.now() + duration * 1000;
                if (Number.isInteger(data.t_pose_generation)) {
                    state.tPoseGeneration = data.t_pose_generation;
                }
            }
        } catch (_) { /* retry on next poll */ }
    }

    function getRawBoneNode(humanoid, boneName) {
        if (typeof humanoid.getRawBoneNode === 'function') {
            return humanoid.getRawBoneNode(boneName);
        }
        const entry = humanoid.humanBones && humanoid.humanBones[boneName];
        return entry && entry.node;
    }

    function vectorComponent(value, index, key, fallback) {
        if (Array.isArray(value) && Number.isFinite(value[index])) return value[index];
        if (value && Number.isFinite(value[key])) return value[key];
        return fallback;
    }

    function boneTransform(humanoid, boneName, restPose) {
        const node = getRawBoneNode(humanoid, boneName);
        if (!node) return null;
        const rest = restPose ? restPose[boneName] : null;
        const position = rest && rest.position;
        const rotation = rest && rest.rotation;
        return {
            name: boneName,
            px: roundTransform(vectorComponent(position, 0, 'x', node.position.x)),
            py: roundTransform(vectorComponent(position, 1, 'y', node.position.y)),
            pz: roundTransform(vectorComponent(position, 2, 'z', node.position.z)),
            qx: roundTransform(vectorComponent(rotation, 0, 'x', node.quaternion.x)),
            qy: roundTransform(vectorComponent(rotation, 1, 'y', node.quaternion.y)),
            qz: roundTransform(vectorComponent(rotation, 2, 'z', node.quaternion.z)),
            qw: roundTransform(vectorComponent(rotation, 3, 'w', node.quaternion.w)),
        };
    }

    function sample(vrm) {
        if (state.samplingSuspended) return;
        if (!vrm || !vrm.humanoid) return;
        if (!state.enabled) return;
        markSourceActive();
        const nowMs = performance.now();
        const nowSeconds = nowMs / 1000;
        if (
            state.nextSampleTs <= 0
            || nowSeconds - state.nextSampleTs > state.minIntervalSec * 4
        ) {
            state.nextSampleTs = nowSeconds;
        }
        if (nowSeconds < state.nextSampleTs) return;
        state.nextSampleTs += state.minIntervalSec;

        const isTPose = state.tPoseDeadline > nowMs;
        if (state.tPoseDeadline > 0 && !isTPose) state.tPoseDeadline = 0;

        if (state.currentVrm !== vrm) {
            // Preserve old names only until one zero-value retirement frame is
            // accepted. Current-model expressions are always queued first so
            // the backend's 256-expression safety cap cannot starve them.
            state.retiringExpressionNames = new Set([
                ...state.retiringExpressionNames,
                ...state.knownExpressionNames,
            ]);
            state.knownExpressionNames.clear();
            state.currentVrm = vrm;
        }

        state.bonesBuf.length = 0;
        const restPose = isTPose ? vrm.humanoid.rawRestPose : null;
        for (const boneName of HUMANOID_BONE_NAMES) {
            const transform = boneTransform(vrm.humanoid, boneName, restPose);
            if (transform) state.bonesBuf.push(transform);
        }

        // Send every known expression every frame, including zero, so a value
        // cannot remain stuck in a receiver after speech or model changes.
        const currentExpressions = new Map();
        const manager = vrm.expressionManager;
        if (manager && Array.isArray(manager.expressions)) {
            for (const expression of manager.expressions) {
                const name = expression && expression.expressionName;
                if (typeof name !== 'string' || !name) continue;
                const weight = Number.isFinite(expression.weight) ? expression.weight : 0;
                currentExpressions.set(name, isTPose ? 0 : Math.max(0, Math.min(1, weight)));
                state.knownExpressionNames.add(name);
            }
        }
        state.exprBuf.length = 0;
        for (const name of state.knownExpressionNames) {
            if (state.exprBuf.length >= 256) break;
            state.exprBuf.push({
                name,
                value: currentExpressions.has(name) ? currentExpressions.get(name) : 0,
            });
        }
        const retiringNamesInFrame = [];
        for (const name of state.retiringExpressionNames) {
            if (state.exprBuf.length >= 256) break;
            if (state.knownExpressionNames.has(name)) continue;
            state.exprBuf.push({ name, value: 0 });
            retiringNamesInFrame.push(name);
        }

        const frameResult = sendFrameEnvelope({
            root: VMC_LOCAL_ROOT,
            bones: state.bonesBuf,
            expressions: state.exprBuf,
            t_pose: isTPose,
            t_pose_generation: state.tPoseGeneration,
            ts: Date.now(),
        }, {
            requireAck: retiringNamesInFrame.length > 0,
        });
        if (frameResult && frameResult.ackPromise) {
            const namesAwaitingAck = retiringNamesInFrame.slice();
            frameResult.ackPromise.then(function (sent) {
                if (!sent) return;
                for (const name of namesAwaitingAck) {
                    state.retiringExpressionNames.delete(name);
                }
            });
        }
    }

    function resetSampleSchedule() {
        state.nextSampleTs = 0;
    }

    function applySendRate(sendRateHz) {
        const nextRate = Number.isFinite(sendRateHz)
            ? sendRateHz : DEFAULT_SEND_RATE_HZ;
        if (nextRate !== state.sendRateHz) {
            state.sendRateHz = nextRate;
            state.minIntervalSec = 1 / Math.max(1, nextRate);
            resetSampleSchedule();
        } else {
            state.minIntervalSec = 1 / Math.max(1, nextRate);
        }
    }

    const api = {
        sample,
        syncStatusFromBackend,

        async enable(host, port, sendRateHz) {
            const generation = beginControlMutation();
            try {
                const body = {};
                if (host !== undefined) body.host = host;
                if (port !== undefined) body.port = port;
                if (sendRateHz !== undefined) body.send_rate_hz = sendRateHz;
                const response = await mutationFetch('/api/vmc/enable', body);
                const data = await response.json();
                if (!response.ok || !data || data.success === false) {
                    finishControlMutation(generation);
                    return null;
                }
                if (!finishControlMutation(generation)) return data;
                state.enabled = true;
                window.__NEKO_VMC_ACTIVE__ = true;
                updateStatusPolling();
                state.samplingSuspended = false;
                resetSampleSchedule();
                applySendRate(data.send_rate_hz);
                if (state.sourceActive) await ensureWebSocket();
                return data;
            } catch (error) {
                finishControlMutation(generation);
                console.error('[VRM-VMC] enable failed:', error);
                return null;
            }
        },

        async disable() {
            const generation = beginControlMutation();
            try {
                const response = await mutationFetch('/api/vmc/disable');
                const data = await response.json();
                if (!response.ok || !data || data.success === false) {
                    finishControlMutation(generation);
                    return data;
                }
                if (!finishControlMutation(generation)) return data;
                state.enabled = false;
                window.__NEKO_VMC_ACTIVE__ = false;
                updateStatusPolling();
                clearSourceState();
                closeWebSocket();
                return data;
            } catch (error) {
                finishControlMutation(generation);
                console.error('[VRM-VMC] disable failed:', error);
                return null;
            }
        },

        async requestTPose(durationSec) {
            const body = {};
            if (durationSec !== undefined) body.duration_sec = durationSec;
            const response = await mutationFetch('/api/vmc/t_pose', body);
            const data = await response.json();
            if (response.ok && data && data.success !== false) {
                const duration = Number.isFinite(data.t_pose_duration_sec)
                    && data.t_pose_duration_sec > 0
                    ? data.t_pose_duration_sec : 2.0;
                state.tPoseDeadline = performance.now() + duration * 1000;
                if (Number.isInteger(data.t_pose_generation)) {
                    state.tPoseGeneration = data.t_pose_generation;
                }
            }
            return data;
        },

        releaseVrm: releaseSource,
        isEnabled: function () { return state.enabled; },
        getSendRateHz: function () { return state.sendRateHz; },
        suspendSampling: function () {
            state.samplingSuspended = true;
            state.samplingSuspensionGeneration += 1;
        },
    };

    window.__NEKO_VMC_ACTIVE__ = false;
    window.vrmVmcSender = api;
    window.addEventListener('beforeunload', function () {
        state.enabled = false;
        window.__NEKO_VMC_ACTIVE__ = false;
        updateStatusPolling();
        closeWebSocket();
    }, { once: true });
    console.info('[VRM-VMC] dedicated sender module loaded on demand');
})();
