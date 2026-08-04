(function () {
    'use strict';

    const TARGET_SAMPLE_RATE = 16000;
    const RECORDING_MS = 4000;
    const MAX_RECORDING_MS = 8000;
    const SESSION_HEADER = 'X-Voice-Identity-Enrollment';
    const API_ROOT = '/api/voice-identity';

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
        busy: false,
        closeStarted: false
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
        const headers = new Headers(config.headers || {});
        if (config.method && config.method !== 'GET') {
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
        if (!response.ok) {
            const error = new Error(payload.error || 'request_failed');
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function applyStatus(payload) {
        const enrollment = payload && payload.enrollment ? payload.enrollment : {};
        const profile = payload && payload.profile ? payload.profile : {};
        const filter = payload && payload.filter ? payload.filter : {};
        state.sessionId = enrollment.session_id || null;
        state.stage = enrollment.stage || 'idle';
        state.profileAvailable = profile.available === true;
        state.persistenceState = profile.state || 'empty';
        state.filterEnabled = filter.enabled === true;
        render();
    }

    function setMessage(message, isError) {
        elements.message.textContent = message || '';
        elements.message.classList.toggle('error', Boolean(isError));
    }

    function stageNumber(stage) {
        const order = {
            fixed_1: 1,
            fixed_2: 2,
            fixed_3: 3,
            free_verify_1: 4,
            free_verify_2: 5,
            ready_to_commit: 5
        };
        return order[stage] || 0;
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
            '今天我想和你分享一件有趣的小事，也期待听到你的回应。',
            '窗外的光线慢慢变化，我会用自然的语气继续和你聊天。',
            '无论今天忙碌还是轻松，我都希望这段对话让人感到自在。'
        ];
    }

    function renderProfile() {
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
        elements.reenroll.disabled = state.busy || state.recording;
        elements.delete.disabled = !state.profileAvailable || state.busy || state.recording;
        elements.filter.checked = state.filterEnabled;
        elements.filter.disabled = !state.profileAvailable || state.busy || state.recording;
    }

    function renderWizard() {
        const activeStep = stageNumber(state.stage);
        elements.progress.forEach(function (item, index) {
            item.classList.toggle('active', index < Math.max(1, activeStep));
        });
        const isIdle = state.stage === 'idle';
        const isFixed = state.stage.startsWith('fixed_');
        const isFree = state.stage.startsWith('free_verify_');
        elements.start.hidden = !isIdle;
        elements.record.hidden = !(isFixed || isFree);
        elements.cancel.hidden = isIdle;
        elements.start.disabled = state.busy;
        elements.record.disabled = state.busy || state.recording;
        elements.cancel.disabled = state.busy || state.recording;
        elements.record.classList.toggle('recording', state.recording);
        const recordLabel = elements.record.querySelector('span:last-child');
        if (recordLabel) {
            recordLabel.textContent = state.recording
                ? translate('voiceIdentity.recording', '正在录音…')
                : translate('voiceIdentity.record', '开始录音');
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
        if (state.mediaStream && state.mediaStream.active) {
            return;
        }
        state.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false
            },
            video: false
        });
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
            throw new Error('audio_context_unavailable');
        }
        state.audioContext = new AudioContext();
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
        mute.gain.value = 0;
        processor.onaudioprocess = function (event) {
            chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
        };
        source.connect(processor);
        processor.connect(mute);
        mute.connect(context.destination);

        const startedAt = performance.now();
        const timer = window.setInterval(function () {
            const elapsed = Math.min(MAX_RECORDING_MS, performance.now() - startedAt);
            elements.timer.textContent = translate(
                'voiceIdentity.recordingSeconds',
                `${(elapsed / 1000).toFixed(1)} s`,
                { seconds: (elapsed / 1000).toFixed(1) }
            );
        }, 100);
        await new Promise(function (resolve) {
            window.setTimeout(resolve, Math.min(RECORDING_MS, MAX_RECORDING_MS));
        });
        window.clearInterval(timer);
        processor.disconnect();
        source.disconnect();
        mute.disconnect();
        processor.onaudioprocess = null;
        elements.timer.textContent = '';

        const sampleCount = chunks.reduce(function (sum, chunk) {
            return sum + chunk.length;
        }, 0);
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
        if (state.busy) return;
        state.busy = true;
        setMessage('');
        render();
        try {
            await ensureMicrophone();
            const payload = await apiRequest('/enrollment/start', {
                method: 'POST'
            });
            applyStatus(payload);
        } catch (error) {
            stopMicrophone();
            const microphoneError = error && (
                error.name === 'NotAllowedError'
                || error.name === 'NotFoundError'
            );
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
        } finally {
            state.busy = false;
            render();
        }
    }

    async function recordCurrentStep() {
        if (state.busy || state.recording || !state.sessionId) return;
        state.busy = true;
        state.recording = true;
        setMessage('');
        render();
        try {
            const pcm16 = await capturePcm16();
            const verification = state.stage.startsWith('free_verify_');
            const payload = await apiRequest(
                verification ? '/enrollment/verify' : '/enrollment/segment',
                {
                    method: 'POST',
                    body: pcm16,
                    headers: {
                        'Content-Type': 'audio/L16;rate=16000;channels=1'
                    }
                }
            );
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
                const committed = await apiRequest('/enrollment/commit', {
                    method: 'POST'
                });
                applyStatus(committed);
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
        } catch (_) {
            setMessage(
                translate(
                    'voiceIdentity.requestFailed',
                    '操作失败，请稍后重试。'
                ),
                true
            );
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
        state.sessionId = null;
        state.stage = 'idle';
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
            }
        } catch (_) {
            if (!config.silent) {
                setMessage(
                    translate(
                        'voiceIdentity.requestFailed',
                        '操作失败，请稍后重试。'
                    ),
                    true
                );
            }
        } finally {
            render();
        }
    }

    async function deleteProfile() {
        let confirmed = true;
        if (typeof window.showConfirm === 'function') {
            confirmed = await window.showConfirm(
                translate(
                    'voiceIdentity.deleteConfirm',
                    '删除后需要重新录入才能使用声纹过滤。'
                ),
                translate('voiceIdentity.delete', '删除 Profile'),
                { danger: true }
            );
        }
        if (!confirmed) return;
        state.busy = true;
        render();
        try {
            const payload = await apiRequest('/profile', { method: 'DELETE' });
            applyStatus(payload);
            setMessage('');
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

    async function updateFilter() {
        const desired = elements.filter.checked;
        elements.filter.disabled = true;
        try {
            const payload = await apiRequest('/filter', {
                method: 'PUT',
                body: JSON.stringify({ enabled: desired }),
                headers: { 'Content-Type': 'application/json' }
            });
            applyStatus(payload);
        } catch (_) {
            elements.filter.checked = state.filterEnabled;
            setMessage(
                translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                true
            );
        } finally {
            render();
        }
    }

    function bindEvents() {
        elements.start.addEventListener('click', startEnrollment);
        elements.reenroll.addEventListener('click', startEnrollment);
        elements.record.addEventListener('click', recordCurrentStep);
        elements.cancel.addEventListener('click', function () {
            cancelEnrollment();
        });
        elements.delete.addEventListener('click', deleteProfile);
        elements.filter.addEventListener('change', updateFilter);
        window.nekoBeforeWindowClose = async function () {
            state.closeStarted = true;
            await cancelEnrollment({ silent: true });
            return true;
        };
        window.addEventListener('pagehide', function () {
            if (!state.closeStarted && state.sessionId) {
                cancelEnrollment({ keepalive: true, silent: true });
            } else {
                stopMicrophone();
            }
        });
    }

    async function initialize() {
        cacheElements();
        bindEvents();
        render();
        try {
            await loadCsrfToken();
            applyStatus(await apiRequest('/status', { method: 'GET' }));
        } catch (_) {
            setMessage(
                translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。'),
                true
            );
        }
    }

    document.addEventListener('DOMContentLoaded', initialize);
})();
