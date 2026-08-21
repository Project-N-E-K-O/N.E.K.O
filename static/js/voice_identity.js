(function () {
    'use strict';

    const TARGET_SAMPLE_RATE = 16000;
    const RECORDING_MS = 4000;
    const CAPTURE_TIMEOUT_GRACE_MS = 1000;
    const WINDOW_CLOSE_START_WAIT_MS = 500;
    const SESSION_HEADER = 'X-Voice-Identity-Enrollment';
    const PROFILE_HEADER = 'X-Voice-Identity-Profile';
    const API_ROOT = '/api/voice-identity';
    const EFFECTIVE_REASON_KEYS = Object.freeze({
        disabled: 'voiceIdentity.reasonDisabled',
        ready: 'voiceIdentity.profileReady',
        no_profile: 'voiceIdentity.profileMissing',
        model_unavailable: 'voiceIdentity.reasonModelUnavailable',
        profile_incompatible: 'voiceIdentity.reasonProfileIncompatible',
        secure_storage_unavailable: 'voiceIdentity.reasonSecureStorageUnavailable',
        enrollment_active: 'voiceIdentity.reasonEnrollmentActive',
        runtime_degraded: 'voiceIdentity.reasonRuntimeDegraded',
        unsupported_asr_route: 'voiceIdentity.reasonUnsupportedAsrRoute',
        shadow_mode: 'voiceIdentity.reasonShadowMode'
    });
    const ENROLLMENT_ERROR_MESSAGES = Object.freeze({
        invalid_pcm: ['voiceIdentity.errorInvalidPcm', '录音格式无效，请重新录入。'],
        audio_too_long: ['voiceIdentity.errorAudioTooLong', '录音时间过长，请重新录入。']
    });

    const state = {
        csrfToken: '',
        enrollmentId: null,
        profileId: null,
        profileAvailable: false,
        profileRevision: null,
        requestedEnabled: false,
        effectiveEnabled: false,
        effectiveReason: 'no_profile',
        mediaStream: null,
        audioContext: null,
        captureAbort: null,
        recording: false,
        saving: false,
        cancelPending: false,
        filterPending: false,
        busy: false,
        initialized: false,
        closeStarted: false,
        startSettled: null
    };

    const elements = {};

    function translate(key, fallback, options) {
        if (typeof window.t === 'function') {
            const translated = window.t(key, options || {});
            if (typeof translated === 'string' && translated && translated !== key) {
                return translated;
            }
        }
        return fallback;
    }

    function cacheElements() {
        elements.statusDot = document.getElementById('voice-identity-status-dot');
        elements.profileStatus = document.getElementById('voice-identity-profile-status');
        elements.enrollment = document.getElementById('voice-identity-enrollment');
        elements.captureStatus = document.getElementById('voice-identity-capture-status');
        elements.captureLabel = document.getElementById('voice-identity-capture-label');
        elements.timer = document.getElementById('voice-identity-timer');
        elements.message = document.getElementById('voice-identity-message');
        elements.start = document.getElementById('voice-identity-start');
        elements.cancel = document.getElementById('voice-identity-cancel');
        elements.profileControls = document.getElementById('voice-identity-profile-controls');
        elements.reenroll = document.getElementById('voice-identity-reenroll');
        elements.delete = document.getElementById('voice-identity-delete');
        elements.filter = document.getElementById('voice-identity-filter');
    }

    async function loadCsrfToken() {
        const response = await fetch('/api/config/page_config', {
            cache: 'no-store',
            credentials: 'same-origin'
        });
        if (!response.ok) throw new Error('page_config_unavailable');
        const payload = await response.json();
        state.csrfToken = typeof payload.autostart_csrf_token === 'string'
            ? payload.autostart_csrf_token
            : '';
        if (!state.csrfToken) throw new Error('csrf_token_unavailable');
    }

    async function apiRequest(path, options) {
        const config = options || {};
        const method = String(config.method || 'GET').toUpperCase();
        const isMutation = method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';

        async function sendOnce() {
            const headers = new Headers(config.headers || {});
            if (isMutation) headers.set('X-CSRF-Token', state.csrfToken);
            if (state.enrollmentId && !headers.has(SESSION_HEADER)) {
                headers.set(SESSION_HEADER, state.enrollmentId);
            }
            const response = await fetch(`${API_ROOT}${path}`, {
                credentials: 'same-origin',
                cache: 'no-store',
                ...config,
                headers
            });
            let payload = {};
            try {
                payload = await response.json();
            } catch (_) {
                payload = {};
            }
            return { response, payload };
        }

        let result = await sendOnce();
        if (
            isMutation
            && result.response.status === 403
            && result.payload.error_code === 'csrf_validation_failed'
        ) {
            await loadCsrfToken();
            result = await sendOnce();
        }
        if (!result.response.ok) {
            const error = new Error(result.payload.error_code || 'request_failed');
            error.status = result.response.status;
            throw error;
        }
        return result.payload;
    }

    function firstBoolean(sources, names, fallback) {
        for (const source of sources) {
            if (!source || typeof source !== 'object') continue;
            for (const name of names) {
                if (typeof source[name] === 'boolean') return source[name];
            }
        }
        return fallback;
    }

    function firstString(sources, names, fallback) {
        for (const source of sources) {
            if (!source || typeof source !== 'object') continue;
            for (const name of names) {
                if (typeof source[name] === 'string' && source[name]) return source[name];
            }
        }
        return fallback;
    }

    function firstScalar(sources, names, fallback) {
        for (const source of sources) {
            if (!source || typeof source !== 'object') continue;
            for (const name of names) {
                if (typeof source[name] === 'string' || typeof source[name] === 'number') {
                    return source[name];
                }
            }
        }
        return fallback;
    }

    function applyStatus(payload) {
        const status = payload && typeof payload === 'object' ? payload : {};
        const enrollment = status.enrollment && typeof status.enrollment === 'object'
            ? status.enrollment
            : {};
        const profile = status.profile && typeof status.profile === 'object'
            ? status.profile
            : {};
        const filter = status.filter && typeof status.filter === 'object'
            ? status.filter
            : {};
        const enrollmentId = firstString(
            [status, enrollment],
            ['enrollment_id', 'id', 'session_id'],
            null
        );
        const enrollmentActive = firstBoolean(
            [status, enrollment],
            ['enrollment_active', 'active'],
            Boolean(enrollmentId)
        );
        if (enrollmentActive && enrollmentId) {
            state.enrollmentId = enrollmentId;
            state.profileId = firstString(
                [status, enrollment],
                ['profile_id'],
                state.profileId
            );
        } else if (
            Object.prototype.hasOwnProperty.call(status, 'enrollment_active')
            || Object.prototype.hasOwnProperty.call(status, 'enrollment')
        ) {
            state.enrollmentId = null;
            state.profileId = null;
        }

        state.profileAvailable = firstBoolean(
            [status, profile],
            ['has_profile', 'profile_available', 'available'],
            state.profileAvailable
        );
        state.profileRevision = firstScalar(
            [status, profile],
            ['profile_generation'],
            state.profileRevision
        );
        state.requestedEnabled = firstBoolean(
            [status, filter],
            ['requested_enabled', 'enabled'],
            state.requestedEnabled
        );
        state.effectiveEnabled = firstBoolean(
            [status, filter],
            ['effective_enabled'],
            state.requestedEnabled && state.profileAvailable
        );
        state.effectiveReason = firstString(
            [status, filter],
            ['effective_reason', 'reason'],
            state.effectiveEnabled ? 'ready' : (state.profileAvailable ? 'disabled' : 'no_profile')
        );
        if (!state.profileAvailable) {
            state.effectiveEnabled = false;
        }
        render();
    }

    async function reconcileStatus() {
        try {
            const status = await apiRequest('/status', { method: 'GET' });
            applyStatus(status);
            return true;
        } catch (_) {
            return false;
        }
    }

    function setMessage(message, isError) {
        elements.message.textContent = message || '';
        elements.message.classList.toggle('error', Boolean(isError));
    }

    function enrollmentErrorMessage(error) {
        const configured = error && ENROLLMENT_ERROR_MESSAGES[error.message];
        if (!configured) {
            return translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。');
        }
        return translate(configured[0], configured[1]);
    }

    function reasonMessage() {
        if (!state.profileAvailable) {
            if (['disabled', 'no_profile'].includes(state.effectiveReason)) {
                return translate('voiceIdentity.profileMissing', '尚未录入 Owner 声纹');
            }
            const unavailableKey = EFFECTIVE_REASON_KEYS[state.effectiveReason]
                || 'voiceIdentity.reasonRuntimeDegraded';
            return translate(unavailableKey, '声纹暂时不可用，独立 ASR 将正常放行');
        }
        if (state.effectiveEnabled) {
            return translate('voiceIdentity.profileReady', 'Owner 声纹已保存并启用');
        }
        if (!state.requestedEnabled || state.effectiveReason === 'disabled') {
            return translate('voiceIdentity.profileSavedDisabled', 'Owner 声纹已保存，过滤当前关闭');
        }
        const key = EFFECTIVE_REASON_KEYS[state.effectiveReason]
            || 'voiceIdentity.reasonRuntimeDegraded';
        return translate(key, '声纹暂时不可用，独立 ASR 将正常放行');
    }

    function enrollmentCompleteMessage() {
        if (state.effectiveEnabled) {
            return translate(
                'voiceIdentity.enrollmentComplete',
                'Owner 声纹已保存并启用。'
            );
        }
        if (!state.requestedEnabled) {
            return translate(
                'voiceIdentity.profileSavedDisabled',
                'Owner 声纹已保存，过滤当前关闭'
            );
        }
        return reasonMessage();
    }

    function renderProfile() {
        const enrollmentVisible = !state.profileAvailable
            || state.busy || state.cancelPending || Boolean(state.enrollmentId);
        elements.enrollment.hidden = !enrollmentVisible;
        elements.profileControls.hidden = !state.profileAvailable || enrollmentVisible;
        elements.statusDot.className = 'status-dot';
        if (state.effectiveEnabled) elements.statusDot.classList.add('ready');
        else if (state.profileAvailable) elements.statusDot.classList.add('warning');
        elements.profileStatus.textContent = reasonMessage();

        const pending = !state.initialized || state.busy
            || state.cancelPending || state.filterPending;
        const enrollmentUnavailable = !state.profileAvailable
            && state.effectiveReason === 'secure_storage_unavailable';
        elements.start.hidden = state.profileAvailable || state.busy || state.cancelPending;
        elements.start.disabled = pending || enrollmentUnavailable;
        elements.cancel.hidden = !state.busy && !state.cancelPending && !state.enrollmentId;
        elements.cancel.disabled = state.cancelPending;
        elements.reenroll.disabled = pending;
        elements.delete.disabled = pending;
        if (!state.filterPending) elements.filter.checked = state.requestedEnabled;
        elements.filter.disabled = pending;
    }

    function renderEnrollment() {
        const captureVisible = state.recording || state.saving;
        elements.captureStatus.hidden = !captureVisible;
        elements.captureStatus.classList.toggle('saving', state.saving);
        elements.captureLabel.textContent = state.saving
            ? translate('voiceIdentity.saving', '正在加密保存并启用…')
            : translate('voiceIdentity.recording', '正在录音…');
    }

    function render() {
        renderProfile();
        renderEnrollment();
    }

    async function ensureMicrophone() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('media_devices_unavailable');
        }
        if (!state.mediaStream) {
            state.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1 },
                video: false
            });
        }
        if (!state.audioContext) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass || typeof AudioWorkletNode !== 'function') {
                throw new Error('audio_worklet_unavailable');
            }
            const context = new AudioContextClass();
            try {
                await context.audioWorklet.addModule('/static/audio-processor.js');
            } catch (error) {
                await context.close();
                throw error;
            }
            state.audioContext = context;
        }
    }

    async function capturePcm16() {
        await ensureMicrophone();
        const context = state.audioContext;
        const source = context.createMediaStreamSource(state.mediaStream);
        const processor = new AudioWorkletNode(context, 'audio-processor', {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            outputChannelCount: [1],
            processorOptions: {
                originalSampleRate: context.sampleRate,
                targetSampleRate: TARGET_SAMPLE_RATE
            }
        });
        const mute = context.createGain();
        const chunks = [];
        const targetSamples = TARGET_SAMPLE_RATE * RECORDING_MS / 1000;
        let capturedSamples = 0;
        let finishCapture = null;
        mute.gain.value = 0;
        source.connect(processor);
        processor.connect(mute);
        mute.connect(context.destination);
        await context.resume();

        const startedAt = performance.now();
        const timer = window.setInterval(function () {
            const elapsed = Math.min(RECORDING_MS, performance.now() - startedAt);
            elements.timer.textContent = translate(
                'voiceIdentity.recordingSeconds',
                `${(elapsed / 1000).toFixed(1)} 秒`,
                { seconds: (elapsed / 1000).toFixed(1) }
            );
        }, 100);
        try {
            await new Promise(function (resolve, reject) {
                let settled = false;
                const timeoutId = window.setTimeout(function () {
                    finishCapture(new Error('incomplete_capture'));
                }, RECORDING_MS + CAPTURE_TIMEOUT_GRACE_MS);
                finishCapture = function (error) {
                    if (settled) return;
                    settled = true;
                    window.clearTimeout(timeoutId);
                    if (error) reject(error);
                    else resolve();
                };
                state.captureAbort = finishCapture;
                processor.port.onmessage = function (event) {
                    const chunk = event.data instanceof Int16Array
                        ? event.data
                        : new Int16Array(event.data);
                    if (chunk.length === 0) return;
                    chunks.push(chunk);
                    capturedSamples += chunk.length;
                    if (capturedSamples >= targetSamples) finishCapture();
                };
            });
        } finally {
            state.captureAbort = null;
            window.clearInterval(timer);
            processor.port.onmessage = null;
            processor.disconnect();
            source.disconnect();
            mute.disconnect();
            elements.timer.textContent = '';
        }

        if (capturedSamples < targetSamples) throw new Error('incomplete_capture');
        const pcm = new Int16Array(targetSamples);
        let offset = 0;
        for (const chunk of chunks) {
            const remaining = targetSamples - offset;
            if (remaining <= 0) break;
            pcm.set(chunk.subarray(0, remaining), offset);
            offset += Math.min(chunk.length, remaining);
        }
        return pcm.buffer;
    }

    function stopMicrophone(reason) {
        const abort = state.captureAbort;
        state.captureAbort = null;
        if (abort) abort(new Error(reason || 'capture_cancelled'));
        if (state.mediaStream) {
            state.mediaStream.getTracks().forEach(function (track) {
                track.stop();
            });
            state.mediaStream = null;
        }
        if (state.audioContext) {
            const context = state.audioContext;
            state.audioContext = null;
            Promise.resolve(context.close()).catch(function () {});
        }
    }

    function createProfileId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        if (!window.crypto || typeof window.crypto.getRandomValues !== 'function') {
            throw new Error('crypto_unavailable');
        }
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, function (value) {
            return value.toString(16).padStart(2, '0');
        }).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    async function cancelSession(options) {
        const config = options || {};
        const enrollmentId = state.enrollmentId;
        if (!enrollmentId) return;
        const headers = new Headers({
            'X-CSRF-Token': state.csrfToken,
            [SESSION_HEADER]: enrollmentId
        });
        if (config.keepalive) {
            state.enrollmentId = null;
            state.profileId = null;
            await fetch(`${API_ROOT}/enrollment/cancel`, {
                method: 'POST',
                headers,
                credentials: 'same-origin',
                keepalive: true
            });
            return;
        }
        const payload = await apiRequest('/enrollment/cancel', {
            method: 'POST',
            headers
        });
        state.enrollmentId = null;
        state.profileId = null;
        applyStatus(payload);
    }

    async function startEnrollment() {
        if (state.busy || state.filterPending || state.cancelPending) return;
        let startSettled = null;
        let settleStart = null;
        let uploadStarted = false;
        const profileWasAvailable = state.profileAvailable;
        const profileRevisionBefore = state.profileRevision;
        state.busy = true;
        setMessage('');
        render();
        try {
            await ensureMicrophone();
            if (state.closeStarted || state.cancelPending) return;
            startSettled = new Promise(function (resolve) {
                settleStart = resolve;
            });
            state.startSettled = startSettled;
            let started;
            try {
                started = await apiRequest('/enrollment/start', { method: 'POST' });
            } finally {
                if (settleStart) settleStart();
                if (state.startSettled === startSettled) state.startSettled = null;
            }
            applyStatus(started);
            state.enrollmentId = firstString(
                [started, started.enrollment],
                ['enrollment_id', 'id', 'session_id'],
                state.enrollmentId
            );
            state.profileId = firstString(
                [started, started.enrollment],
                ['profile_id'],
                state.profileId || createProfileId()
            );
            if (!state.enrollmentId) throw new Error('enrollment_id_missing');
            if (state.closeStarted || state.cancelPending) {
                await cancelSession({
                    keepalive: state.closeStarted,
                    silent: true
                });
                return;
            }

            state.recording = true;
            render();
            const pcm16 = await capturePcm16();
            state.recording = false;
            state.saving = true;
            stopMicrophone();
            render();
            uploadStarted = true;
            const completed = await apiRequest('/enrollment/profile', {
                method: 'PUT',
                body: pcm16,
                headers: {
                    'Content-Type': 'audio/pcm;format=pcm_s16le;rate=16000;channels=1',
                    [SESSION_HEADER]: state.enrollmentId,
                    [PROFILE_HEADER]: state.profileId
                }
            });
            applyStatus(completed);
            if (!state.profileAvailable && !await reconcileStatus()) {
                throw new Error('profile_status_unavailable');
            }
            if (!state.profileAvailable) throw new Error('profile_not_confirmed');
            state.enrollmentId = null;
            state.profileId = null;
            setMessage(enrollmentCompleteMessage(), false);
        } catch (error) {
            stopMicrophone();
            const reconciled = await reconcileStatus();
            const replacementConfirmed = uploadStarted
                && reconciled
                && state.profileAvailable
                && (
                    !profileWasAvailable
                    || (
                        profileRevisionBefore !== null
                        && state.profileRevision !== null
                        && state.profileRevision !== profileRevisionBefore
                    )
                );
            if (replacementConfirmed) {
                state.enrollmentId = null;
                state.profileId = null;
                setMessage(enrollmentCompleteMessage(), false);
            } else {
                try {
                    await cancelSession();
                } catch (_) {}
                const microphoneError = error && (
                    error.name === 'NotAllowedError'
                    || error.name === 'NotFoundError'
                    || error.name === 'NotReadableError'
                    || error.message === 'audio_worklet_unavailable'
                    || error.message === 'media_devices_unavailable'
                );
                if (!state.cancelPending && !state.closeStarted) {
                    setMessage(
                        microphoneError
                            ? translate(
                                'voiceIdentity.microphoneDenied',
                                '无法使用麦克风，请检查权限和设备。'
                            )
                            : enrollmentErrorMessage(error),
                        true
                    );
                }
            }
        } finally {
            stopMicrophone();
            if (settleStart && state.startSettled === startSettled) {
                state.startSettled = null;
                settleStart();
            }
            state.recording = false;
            state.saving = false;
            state.busy = false;
            state.cancelPending = false;
            render();
        }
    }

    async function cancelEnrollment(options) {
        const config = options || {};
        state.cancelPending = true;
        stopMicrophone('capture_cancelled');
        render();
        try {
            await cancelSession(config);
            if (!config.silent) setMessage('');
        } catch (_) {
            if (!config.keepalive) {
                const reconciled = await reconcileStatus();
                if (!config.silent && (!reconciled || state.enrollmentId)) {
                    setMessage(
                        translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                        true
                    );
                }
            }
        } finally {
            if (!state.busy) state.cancelPending = false;
            render();
        }
    }

    async function deleteProfile() {
        if (state.busy || state.filterPending) return;
        state.busy = true;
        setMessage('');
        render();
        try {
            const message = translate(
                'voiceIdentity.deleteConfirm',
                '删除后需要重新录入才能使用声纹过滤。'
            );
            let confirmed = false;
            if (typeof window.showConfirm === 'function') {
                confirmed = await window.showConfirm(
                    message,
                    translate('voiceIdentity.delete', '删除声纹'),
                    { danger: true }
                );
            } else if (typeof window.confirm === 'function') {
                confirmed = window.confirm(message);
            }
            if (!confirmed) return;
            const payload = await apiRequest('/profile', { method: 'DELETE' });
            applyStatus(payload);
            if (state.profileAvailable) await reconcileStatus();
        } catch (_) {
            const reconciled = await reconcileStatus();
            if (!reconciled || state.profileAvailable) {
                setMessage(
                    translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                    true
                );
            }
        } finally {
            state.busy = false;
            render();
        }
    }

    async function updateFilter() {
        if (state.filterPending || state.busy) return;
        const desired = elements.filter.checked;
        state.filterPending = true;
        setMessage('');
        render();
        try {
            const payload = await apiRequest('/filter', {
                method: 'PUT',
                body: JSON.stringify({ enabled: desired }),
                headers: { 'Content-Type': 'application/json' }
            });
            applyStatus(payload);
        } catch (_) {
            const reconciled = await reconcileStatus();
            if (!reconciled || state.requestedEnabled !== desired) {
                elements.filter.checked = state.requestedEnabled;
                setMessage(
                    translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                    true
                );
            }
        } finally {
            state.filterPending = false;
            render();
        }
    }

    function bindEvents() {
        elements.start.addEventListener('click', startEnrollment);
        elements.reenroll.addEventListener('click', startEnrollment);
        elements.cancel.addEventListener('click', function () {
            cancelEnrollment().catch(function () {});
        });
        elements.delete.addEventListener('click', deleteProfile);
        elements.filter.addEventListener('change', updateFilter);
        window.addEventListener('localechange', render);
        window.nekoBeforeWindowClose = async function () {
            state.closeStarted = true;
            state.cancelPending = true;
            stopMicrophone('capture_cancelled');
            const pendingStart = state.startSettled;
            if (pendingStart) {
                let timeoutId = null;
                const waitLimit = new Promise(function (resolve) {
                    timeoutId = window.setTimeout(resolve, WINDOW_CLOSE_START_WAIT_MS);
                });
                await Promise.race([pendingStart, waitLimit]);
                if (timeoutId !== null) window.clearTimeout(timeoutId);
            }
            cancelEnrollment({ keepalive: true, silent: true }).catch(function () {});
            return true;
        };
        window.addEventListener('pagehide', function () {
            window.nekoBeforeWindowClose().catch(function () {});
        });
        window.addEventListener('pageshow', async function (event) {
            if (!event.persisted) return;
            state.closeStarted = false;
            state.cancelPending = false;
            state.busy = true;
            render();
            const reconciled = await reconcileStatus();
            if (!reconciled) {
                setMessage(
                    translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                    true
                );
            }
            state.busy = false;
            render();
        });
    }

    async function initialize() {
        cacheElements();
        bindEvents();
        state.busy = true;
        render();
        try {
            await loadCsrfToken();
            const status = await apiRequest('/status', { method: 'GET' });
            state.initialized = true;
            applyStatus(status);
        } catch (_) {
            setMessage(
                translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                true
            );
        } finally {
            state.busy = false;
            render();
        }
    }

    document.addEventListener('DOMContentLoaded', initialize);
})();
