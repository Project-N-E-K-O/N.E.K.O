(function () {
    'use strict';

    const TARGET_SAMPLE_RATE = 16000;
    const RECORDING_MS = 4000;
    const CAPTURE_TIMEOUT_GRACE_MS = 1000;
    const WINDOW_CLOSE_START_WAIT_MS = 500;
    const SESSION_HEADER = 'X-Voice-Identity-Enrollment';
    const API_ROOT = '/api/voice-identity';
    const ENROLLMENT_STAGE_ORDER = Object.freeze({
        fixed_1: 1,
        fixed_2: 2,
        fixed_3: 3,
        free_verify_1: 4,
        free_verify_2: 5,
        ready_to_commit: 6
    });

    const state = {
        csrfToken: '',
        sessionId: null,
        stage: 'idle',
        profileAvailable: false,
        persistenceState: 'empty',
        filterEnabled: false,
        mediaStream: null,
        audioContext: null,
        recording: false,
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
        elements.stepCount = document.getElementById('voice-identity-step-count');
        elements.stepTitle = document.getElementById('voice-identity-step-title');
        elements.stepBody = document.getElementById('voice-identity-step-body');
        elements.prompt = document.getElementById('voice-identity-prompt');
        elements.timer = document.getElementById('voice-identity-timer');
        elements.message = document.getElementById('voice-identity-message');
        elements.start = document.getElementById('voice-identity-start');
        elements.record = document.getElementById('voice-identity-record');
        elements.cancel = document.getElementById('voice-identity-cancel');
        elements.reenroll = document.getElementById('voice-identity-reenroll');
        elements.delete = document.getElementById('voice-identity-delete');
        elements.filter = document.getElementById('voice-identity-filter');
        elements.progress = Array.from(document.querySelectorAll('.step-progress span'));
    }

    async function loadCsrfToken() {
        const response = await fetch('/api/config/page_config', {
            cache: 'no-store',
            credentials: 'same-origin'
        });
        if (!response.ok) {
            throw new Error('page_config_unavailable');
        }
        const payload = await response.json();
        state.csrfToken = typeof payload.autostart_csrf_token === 'string'
            ? payload.autostart_csrf_token
            : '';
        if (!state.csrfToken) {
            throw new Error('csrf_token_unavailable');
        }
    }

    async function apiRequest(path, options) {
        const config = options || {};
        const method = String(config.method || 'GET').toUpperCase();
        const isMutation = method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';

        async function sendOnce() {
            const headers = new Headers(config.headers || {});
            if (isMutation) {
                headers.set('X-CSRF-Token', state.csrfToken);
            }
            if (state.sessionId) {
                headers.set(SESSION_HEADER, state.sessionId);
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
        const { response, payload } = result;
        if (!response.ok) {
            const error = new Error(payload.error || 'request_failed');
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function applyStatus(payload) {
        const status = payload && typeof payload === 'object' ? payload : {};
        if (Object.prototype.hasOwnProperty.call(status, 'enrollment')) {
            const enrollment = status.enrollment || {};
            const nextStage = enrollment.stage || 'idle';
            if (nextStage === 'idle') {
                state.sessionId = null;
            } else if (Object.prototype.hasOwnProperty.call(enrollment, 'session_id')) {
                state.sessionId = enrollment.session_id || null;
            }
            state.stage = nextStage;
        }
        if (Object.prototype.hasOwnProperty.call(status, 'profile')) {
            const profile = status.profile || {};
            state.profileAvailable = profile.available === true;
            state.persistenceState = profile.state || 'empty';
            if (
                !state.profileAvailable
                && !Object.prototype.hasOwnProperty.call(status, 'filter')
            ) {
                state.filterEnabled = false;
            }
        }
        if (Object.prototype.hasOwnProperty.call(status, 'filter')) {
            const filter = status.filter || {};
            state.filterEnabled = filter.enabled === true;
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

    function stageNumber(stage) {
        return Math.min(ENROLLMENT_STAGE_ORDER[stage] || 0, 5);
    }

    function fixedPrompts() {
        let translated = null;
        if (window.i18next && typeof window.i18next.t === 'function') {
            translated = window.i18next.t(
                'voiceIdentity.fixedPrompts',
                { returnObjects: true }
            );
        } else if (typeof window.t === 'function') {
            translated = window.t(
                'voiceIdentity.fixedPrompts',
                { returnObjects: true }
            );
        }
        if (Array.isArray(translated) && translated.length === 3) {
            return translated;
        }
        return [
            '今天我想和你分享一件趣事。',
            '窗外的光线正在慢慢变化。',
            '今天也用自然的声音聊天。'
        ];
    }

    function renderProfile() {
        const isIdle = state.stage === 'idle';
        elements.statusDot.className = 'status-dot';
        if (state.profileAvailable) {
            elements.statusDot.classList.add(
                state.persistenceState === 'secure_storage_unavailable'
                    ? 'warning'
                    : 'ready'
            );
            elements.profileStatus.textContent = state.persistenceState === 'secure_storage_unavailable'
                ? translate(
                    'voiceIdentity.persistenceUnavailable',
                    'Profile 已在本次运行中激活，但本地持久化不可用'
                )
                : translate('voiceIdentity.profileReady', 'Owner Profile 已保存并激活');
        } else {
            elements.profileStatus.textContent = translate(
                'voiceIdentity.profileMissing',
                '尚未录入 Owner 声纹'
            );
        }
        elements.reenroll.disabled = !state.initialized || !isIdle
            || state.busy || state.recording || state.filterPending;
        elements.delete.disabled = !state.profileAvailable || !isIdle
            || !state.initialized || state.busy || state.recording
            || state.filterPending;
        if (!state.filterPending) {
            elements.filter.checked = state.filterEnabled;
        }
        elements.filter.disabled = !state.profileAvailable || !isIdle
            || !state.initialized || state.busy || state.recording
            || state.filterPending;
    }

    function renderWizard() {
        const activeStep = stageNumber(state.stage);
        elements.progress.forEach(function (item, index) {
            item.classList.toggle('active', index < Math.max(1, activeStep));
        });
        const isIdle = state.stage === 'idle';
        const isFixed = state.stage.startsWith('fixed_');
        const isFree = state.stage.startsWith('free_verify_');
        const isReadyToCommit = state.stage === 'ready_to_commit';
        elements.start.hidden = !isIdle;
        elements.record.hidden = !(isFixed || isFree || isReadyToCommit);
        elements.cancel.hidden = isIdle;
        elements.start.disabled = !state.initialized || state.busy
            || state.filterPending;
        elements.record.disabled = state.busy || state.recording
            || state.cancelPending;
        elements.cancel.disabled = state.busy || state.recording || state.cancelPending;
        elements.record.classList.toggle('recording', state.recording);
        const recordLabel = elements.record.querySelector('span:last-child');
        if (recordLabel) {
            recordLabel.textContent = state.recording
                ? translate('voiceIdentity.recording', '正在录音…')
                : translate(
                    isReadyToCommit ? 'voiceIdentity.retry' : 'voiceIdentity.record',
                    isReadyToCommit ? '重试' : '开始录音'
                );
        }

        elements.prompt.hidden = true;
        elements.stepCount.textContent = activeStep
            ? translate('voiceIdentity.stepCount', `步骤 ${activeStep} / 5`, {
                current: activeStep,
                total: 5
            })
            : '';

        if (isIdle) {
            elements.stepTitle.textContent = translate(
                'voiceIdentity.privacyTitle',
                '开始前请了解'
            );
            elements.stepBody.textContent = translate(
                'voiceIdentity.privacyBody',
                '声纹仅在本机处理和保存，不会发送给 ASR Provider。'
            );
            return;
        }
        if (isFixed) {
            const index = Math.max(0, Number(state.stage.slice(-1)) - 1);
            elements.stepTitle.textContent = translate(
                'voiceIdentity.fixedTitle',
                '朗读固定文案'
            );
            elements.stepBody.textContent = translate(
                'voiceIdentity.fixedHelp',
                '请使用平时聊天的自然音量和语速朗读下方文字。'
            );
            elements.prompt.textContent = fixedPrompts()[index];
            elements.prompt.hidden = false;
            return;
        }
        if (isFree) {
            const first = state.stage === 'free_verify_1';
            elements.stepTitle.textContent = translate(
                first ? 'voiceIdentity.freeTitle1' : 'voiceIdentity.freeTitle2',
                first ? '自由说话测试 1' : '自由说话测试 2'
            );
            elements.stepBody.textContent = translate(
                first ? 'voiceIdentity.freePrompt1' : 'voiceIdentity.freePrompt2',
                first
                    ? '请自由说几句话，内容不限，像平时聊天一样即可。'
                    : '请再自由说几句话，内容可以和上一次不同。'
            );
            return;
        }
        elements.stepTitle.textContent = translate(
            'voiceIdentity.saving',
            '正在保存并激活…'
        );
        elements.stepBody.textContent = '';
    }

    function render() {
        renderProfile();
        renderWizard();
    }

    async function ensureMicrophone() {
        if (!state.mediaStream || !state.mediaStream.active) {
            state.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false
                },
                video: false
            });
        }
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
            throw new Error('audio_context_unavailable');
        }
        if (!state.audioContext || state.audioContext.state === 'closed') {
            state.audioContext = new AudioContext();
        }
        if (state.audioContext.state === 'suspended') {
            await state.audioContext.resume();
        }
    }

    function resampleTo16k(input, sourceRate) {
        if (sourceRate === TARGET_SAMPLE_RATE) {
            return input;
        }
        const outputLength = Math.max(
            1,
            Math.round(input.length * TARGET_SAMPLE_RATE / sourceRate)
        );
        const output = new Float32Array(outputLength);
        const scale = sourceRate / TARGET_SAMPLE_RATE;
        if (scale > 1) {
            const cutoff = 0.5 / scale;
            const halfTaps = Math.max(8, Math.ceil(scale * 4));
            const kernels = new Map();
            for (let index = 0; index < outputLength; index += 1) {
                const center = (index + 0.5) * scale - 0.5;
                const anchor = Math.floor(center);
                const fraction = center - anchor;
                const phase = Math.round(fraction * 1000000);
                let kernel = kernels.get(phase);
                if (!kernel) {
                    kernel = [];
                    for (let offset = -halfTaps; offset <= halfTaps; offset += 1) {
                        const distance = offset - fraction;
                        if (Math.abs(distance) > halfTaps) continue;
                        const sinc = distance === 0
                            ? 2 * cutoff
                            : Math.sin(2 * Math.PI * cutoff * distance)
                                / (Math.PI * distance);
                        const window = 0.5
                            + 0.5 * Math.cos(Math.PI * distance / halfTaps);
                        kernel.push({ offset, weight: sinc * window });
                    }
                    kernels.set(phase, kernel);
                }
                let weighted = 0;
                let weightTotal = 0;
                for (const tap of kernel) {
                    const sourceIndex = anchor + tap.offset;
                    if (sourceIndex < 0 || sourceIndex >= input.length) continue;
                    weighted += input[sourceIndex] * tap.weight;
                    weightTotal += tap.weight;
                }
                output[index] = weightTotal === 0 ? 0 : weighted / weightTotal;
            }
            return output;
        }
        for (let index = 0; index < outputLength; index += 1) {
            const position = index * scale;
            const left = Math.floor(position);
            const right = Math.min(left + 1, input.length - 1);
            const mix = position - left;
            output[index] = input[left] * (1 - mix) + input[right] * mix;
        }
        return output;
    }

    function floatToPcm16(samples) {
        const pcm = new Int16Array(samples.length);
        for (let index = 0; index < samples.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, samples[index]));
            pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        }
        return pcm;
    }

    async function capturePcm16() {
        await ensureMicrophone();
        const context = state.audioContext;
        const source = context.createMediaStreamSource(state.mediaStream);
        const processor = context.createScriptProcessor(2048, 1, 1);
        const mute = context.createGain();
        const chunks = [];
        const maxSourceSamples = Math.ceil(
            context.sampleRate * RECORDING_MS / 1000
        );
        let capturedSamples = 0;
        mute.gain.value = 0;
        source.connect(processor);
        processor.connect(mute);
        mute.connect(context.destination);

        const startedAt = performance.now();
        const timer = window.setInterval(function () {
            const elapsed = Math.min(RECORDING_MS, performance.now() - startedAt);
            elements.timer.textContent = translate(
                'voiceIdentity.recordingSeconds',
                `${(elapsed / 1000).toFixed(1)} s`,
                { seconds: (elapsed / 1000).toFixed(1) }
            );
        }, 100);
        try {
            await new Promise(function (resolve, reject) {
                let settled = false;
                let timeoutId = null;
                const finish = function (error) {
                    if (settled) return;
                    settled = true;
                    if (timeoutId !== null) window.clearTimeout(timeoutId);
                    if (error) reject(error);
                    else resolve();
                };
                processor.onaudioprocess = function (event) {
                    const input = event.inputBuffer.getChannelData(0);
                    const remaining = maxSourceSamples - capturedSamples;
                    if (remaining <= 0) return;
                    const length = Math.min(input.length, remaining);
                    chunks.push(new Float32Array(input.subarray(0, length)));
                    capturedSamples += length;
                    if (capturedSamples >= maxSourceSamples) finish();
                };
                timeoutId = window.setTimeout(function () {
                    finish(new Error('incomplete_capture'));
                }, RECORDING_MS + CAPTURE_TIMEOUT_GRACE_MS);
            });
        } finally {
            window.clearInterval(timer);
            processor.disconnect();
            source.disconnect();
            mute.disconnect();
            processor.onaudioprocess = null;
            elements.timer.textContent = '';
        }

        const sampleCount = chunks.reduce(function (sum, chunk) {
            return sum + chunk.length;
        }, 0);
        if (sampleCount === 0) {
            throw new Error('empty_capture');
        }
        const joined = new Float32Array(sampleCount);
        let offset = 0;
        chunks.forEach(function (chunk) {
            joined.set(chunk, offset);
            offset += chunk.length;
        });
        return floatToPcm16(
            resampleTo16k(joined, context.sampleRate)
        ).buffer;
    }

    function stopMicrophone() {
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

    async function startEnrollment() {
        if (state.busy || state.filterPending) return;
        let startRequestPending = false;
        let startSettled = null;
        let settleStart = null;
        state.busy = true;
        setMessage('');
        render();
        try {
            await ensureMicrophone();
            stopMicrophone();
            if (state.closeStarted) return;
            startSettled = new Promise(function (resolve) {
                settleStart = resolve;
            });
            state.startSettled = startSettled;
            startRequestPending = true;
            const payload = await apiRequest('/enrollment/start', {
                method: 'POST'
            });
            startRequestPending = false;
            applyStatus(payload);
        } catch (error) {
            stopMicrophone();
            const recovered = startRequestPending
                && await reconcileStatus()
                && Boolean(state.sessionId);
            const microphoneError = error && (
                error.name === 'NotAllowedError'
                || error.name === 'NotFoundError'
                || error.name === 'NotReadableError'
            );
            if (!recovered) {
                setMessage(
                    microphoneError
                        ? translate(
                            'voiceIdentity.microphoneDenied',
                            '无法使用麦克风，请检查权限和设备。'
                        )
                        : translate(
                            'voiceIdentity.requestFailed',
                            '操作失败，请稍后重试。'
                        ),
                    true
                );
            }
        } finally {
            state.busy = false;
            if (settleStart) {
                if (state.startSettled === startSettled) state.startSettled = null;
                settleStart();
            }
            render();
        }
    }

    async function commitEnrollment() {
        const profileAlreadyAvailable = state.profileAvailable;
        let reconciliationAttempted = false;
        try {
            const committed = await apiRequest('/enrollment/commit', {
                method: 'POST'
            });
            applyStatus(committed);
            const completeStatus = committed && typeof committed === 'object'
                && Object.prototype.hasOwnProperty.call(committed, 'enrollment')
                && Object.prototype.hasOwnProperty.call(committed, 'profile');
            if (!completeStatus) {
                reconciliationAttempted = true;
                if (!await reconcileStatus()) {
                    throw new Error('commit_status_unavailable');
                }
            }
            if (state.stage !== 'idle' || !state.profileAvailable) {
                throw new Error('commit_not_confirmed');
            }
        } catch (error) {
            const reconciled = reconciliationAttempted
                ? state.stage === 'idle' && state.profileAvailable
                : await reconcileStatus();
            if (
                !reconciled
                || state.stage !== 'idle'
                || !state.profileAvailable
                || profileAlreadyAvailable
            ) throw error;
        }
        stopMicrophone();
        setMessage(
            state.persistenceState === 'secure_storage_unavailable'
                ? translate(
                    'voiceIdentity.persistenceUnavailable',
                    'Profile 已在本次运行中激活，但本地持久化不可用'
                )
                : translate(
                    'voiceIdentity.enrollmentComplete',
                    'Owner Profile 已保存并激活。'
                ),
            state.persistenceState === 'secure_storage_unavailable'
        );
    }

    async function recordCurrentStep() {
        if (
            state.busy || state.recording || state.cancelPending || !state.sessionId
        ) return;
        let uploadRequestPending = false;
        let uploadStage = null;
        state.busy = true;
        state.recording = state.stage !== 'ready_to_commit';
        setMessage('');
        render();
        try {
            if (state.stage === 'ready_to_commit') {
                await commitEnrollment();
                return;
            }
            let pcm16;
            try {
                pcm16 = await capturePcm16();
            } finally {
                stopMicrophone();
            }
            state.recording = false;
            render();
            const verification = state.stage.startsWith('free_verify_');
            uploadStage = state.stage;
            uploadRequestPending = true;
            const payload = await apiRequest(
                verification ? '/enrollment/verify' : '/enrollment/segment',
                {
                    method: 'POST',
                    body: pcm16,
                    headers: {
                        'Content-Type': 'audio/pcm;format=pcm_s16le;rate=16000;channels=1'
                    }
                }
            );
            uploadRequestPending = false;
            applyStatus(payload);
            if (verification) {
                const passed = payload.verification && payload.verification.passed;
                setMessage(
                    passed
                        ? translate(
                            'voiceIdentity.verificationPassed',
                            '本次自由说话测试已通过。'
                        )
                        : translate(
                            'voiceIdentity.verificationRetry',
                            '这次未能确认，请保持自然语气再试一次。'
                        ),
                    !passed
                );
            }
            if (state.stage === 'ready_to_commit') {
                await commitEnrollment();
            }
        } catch (error) {
            let recovered = uploadRequestPending
                && await reconcileStatus()
                && (ENROLLMENT_STAGE_ORDER[state.stage] || 0)
                    > (ENROLLMENT_STAGE_ORDER[uploadStage] || 0);
            if (recovered && state.stage === 'ready_to_commit') {
                try {
                    await commitEnrollment();
                } catch (_) {
                    recovered = false;
                }
            }
            const microphoneError = error && (
                error.name === 'NotAllowedError'
                || error.name === 'NotFoundError'
                || error.name === 'NotReadableError'
            );
            if (!recovered) {
                setMessage(
                    microphoneError
                        ? translate(
                            'voiceIdentity.microphoneDenied',
                            '无法使用麦克风，请检查权限和设备。'
                        )
                        : translate(
                            'voiceIdentity.requestFailed',
                            '操作失败，请稍后重试。'
                        ),
                    true
                );
            }
        } finally {
            state.recording = false;
            state.busy = false;
            render();
        }
    }

    async function cancelEnrollment(options) {
        const config = options || {};
        if (!state.sessionId) {
            stopMicrophone();
            return;
        }
        const sessionId = state.sessionId;
        if (config.keepalive) {
            state.sessionId = null;
            state.stage = 'idle';
        } else {
            setMessage('');
            state.cancelPending = true;
            render();
        }
        stopMicrophone();
        const headers = new Headers({
            'X-CSRF-Token': state.csrfToken,
            [SESSION_HEADER]: sessionId
        });
        try {
            if (config.keepalive) {
                await fetch(`${API_ROOT}/enrollment/cancel`, {
                    method: 'POST',
                    headers,
                    credentials: 'same-origin',
                    keepalive: true
                });
            } else {
                const payload = await apiRequest('/enrollment/cancel', {
                    method: 'POST',
                    headers
                });
                applyStatus(payload);
                setMessage('');
            }
        } catch (_) {
            const reconciled = !config.keepalive && await reconcileStatus();
            if (!config.silent && (!reconciled || state.sessionId)) {
                setMessage(
                    translate(
                        'voiceIdentity.requestFailed',
                        '操作失败，请稍后重试。'
                    ),
                    true
                );
            }
        } finally {
            state.cancelPending = false;
            render();
        }
    }

    async function deleteProfile() {
        if (state.busy || state.filterPending) return;
        state.busy = true;
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
                    translate('voiceIdentity.delete', '删除 Profile'),
                    { danger: true }
                );
            } else if (typeof window.confirm === 'function') {
                confirmed = window.confirm(message);
            }
            if (!confirmed) return;
            const payload = await apiRequest('/profile', { method: 'DELETE' });
            applyStatus(payload);
            setMessage('');
        } catch (_) {
            const reconciled = await reconcileStatus();
            if (reconciled && !state.profileAvailable) {
                setMessage('');
            } else {
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
        if (state.filterPending) return;
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
            if (!reconciled || state.filterEnabled !== desired) {
                elements.filter.checked = state.filterEnabled;
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
        elements.record.addEventListener('click', recordCurrentStep);
        elements.cancel.addEventListener('click', function () {
            return cancelEnrollment();
        });
        elements.delete.addEventListener('click', deleteProfile);
        elements.filter.addEventListener('change', updateFilter);
        window.addEventListener('localechange', render);
        window.nekoBeforeWindowClose = async function () {
            state.closeStarted = true;
            stopMicrophone();
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
        window.addEventListener('pageshow', function (event) {
            if (event.persisted) state.closeStarted = false;
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
