(function () {
    'use strict';

    const TARGET_SAMPLE_RATE = 48000;
    const RUNTIME_CHUNK_SAMPLES = 480;
    const REFERENCE_RECORDING_MS = 3000;
    const VERIFICATION_RECORDING_MS = 5000;
    const MAX_RECORDING_MS = VERIFICATION_RECORDING_MS;
    const ENROLLMENT_SEGMENT_COUNT = 4;
    const SEGMENT_HEADER = 'X-Voice-Identity-Segment';
    const AUDIO_CONTRACT_HEADER = 'X-Voice-Audio-Contract';
    const AUDIO_CONTRACT_ID = 'owner-campplus-desktop-v1';
    const CAPTURE_TIMEOUT_GRACE_MS = 1000;
    const FLUSH_TIMEOUT_MS = 400;
    const SILENCE_HINT_MS = 800;
    const ACTIVE_FRAME_RMS = 0.008;
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
        audio_contract_mismatch: 'voiceIdentity.reasonAudioContractMismatch',
        secure_storage_unavailable: 'voiceIdentity.reasonSecureStorageUnavailable',
        enrollment_active: 'voiceIdentity.reasonEnrollmentActive',
        runtime_degraded: 'voiceIdentity.reasonRuntimeDegraded',
        unsupported_asr_route: 'voiceIdentity.reasonUnsupportedAsrRoute',
        shadow_mode: 'voiceIdentity.reasonShadowMode'
    });
    const ENROLLMENT_ERROR_MESSAGES = Object.freeze({
        invalid_pcm: ['voiceIdentity.errorInvalidPcm', '录音格式无效，请重新录入。'],
        audio_too_long: ['voiceIdentity.errorAudioTooLong', '录音时间过长，请换一句较短的话重新录入。'],
        speech_too_short: ['voiceIdentity.errorSpeechTooShort', '没有检测到足够的语音，请重新说一句完整的话。'],
        volume_too_low: ['voiceIdentity.errorVolumeTooLow', '录音音量过低，请重录当前段。'],
        no_speech_detected: ['voiceIdentity.errorNoSpeechDetected', '没有检测到有效语音，请重录当前段。'],
        silence: ['voiceIdentity.errorSilence', '没有检测到声音，请检查麦克风后重试。'],
        severe_clipping: ['voiceIdentity.errorSevereClipping', '声音过大或失真，请稍微远离麦克风。'],
        incomplete_capture: ['voiceIdentity.errorIncompleteCapture', '录音没有完整采集，请重试。'],
        inconsistent_segments: ['voiceIdentity.errorInconsistentSegments', '几段声音差异较大，请按提示重新录入。'],
        voice_samples_inconsistent: ['voiceIdentity.errorVoiceSamplesInconsistent', '几段声音差异较大，请按提示重新录入。'],
        owner_verification_failed: ['voiceIdentity.errorOwnerVerificationFailed', '声纹验证未通过，请重录当前段。'],
        stale_enrollment: ['voiceIdentity.errorStaleEnrollment', '本次录入已过期，请重新开始。']
    });

    const state = {
        csrfToken: '',
        enrollmentId: null,
        enrollmentRemainingSeconds: null,
        nextSegmentIndex: 1,
        profileId: null,
        profileAvailable: false,
        profileRevision: null,
        requestedEnabled: false,
        effectiveEnabled: false,
        effectiveReason: 'no_profile',
        mediaStream: null,
        audioContext: null,
        captureAbort: null,
        captureFinish: null,
        recording: false,
        saving: false,
        cancelPending: false,
        cancelReleaseWhenIdle: false,
        statusEpoch: 0,
        filterPending: false,
        busy: false,
        initialized: false,
        closeStarted: false,
        startSettled: null,
        voiceStatus: 'waiting',
        lastVoiceAt: 0,
        segmentIndex: 0,
        segmentPhase: 'idle',
        segmentAdvance: null,
        uiPhase: 'idle',
        initializationError: false
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
        elements.stepCount = document.getElementById('voice-identity-step-count');
        elements.stepTitle = document.getElementById('voice-identity-step-title');
        elements.stepBody = document.getElementById('voice-identity-step-body');
        elements.prompt = document.getElementById('voice-identity-prompt');
        elements.progress = typeof document.querySelectorAll === 'function' ? Array.from(document.querySelectorAll('#voice-identity-progress span')) : [];
        elements.next = document.getElementById('voice-identity-next');
        elements.captureLabel = document.getElementById('voice-identity-capture-label');
        elements.voiceState = document.getElementById('voice-identity-voice-state');
        elements.timer = document.getElementById('voice-identity-timer');
        elements.message = document.getElementById('voice-identity-message');
        elements.retry = document.getElementById('voice-identity-retry');
        elements.start = document.getElementById('voice-identity-start');
        elements.finish = document.getElementById('voice-identity-finish');
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
            error.payload = result.payload;
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
            const rawRemainingSeconds = firstScalar(
                [enrollment], ['remaining_seconds'], null
            );
            const remainingSeconds = Number(rawRemainingSeconds);
            state.enrollmentRemainingSeconds = rawRemainingSeconds !== null
                && Number.isFinite(remainingSeconds) ? remainingSeconds : null;
            state.profileId = firstString(
                [status, enrollment],
                ['profile_id'],
                state.profileId
            );
            const rawNextSegment = firstScalar(
                [enrollment, status], ['next_segment_index'], 1
            );
            const nextSegment = Number(rawNextSegment);
            state.nextSegmentIndex = Number.isInteger(nextSegment)
                && nextSegment >= 1 && nextSegment <= ENROLLMENT_SEGMENT_COUNT
                ? nextSegment : 1;
        } else if (
            Object.prototype.hasOwnProperty.call(status, 'enrollment_active')
            || Object.prototype.hasOwnProperty.call(status, 'enrollment')
        ) {
            state.enrollmentId = null;
            state.enrollmentRemainingSeconds = null;
            state.nextSegmentIndex = 1;
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
        const requestEpoch = state.statusEpoch;
        try {
            const status = await apiRequest('/status', { method: 'GET' });
            if (requestEpoch === state.statusEpoch) applyStatus(status);
            return requestEpoch === state.statusEpoch ? status : null;
        } catch (_) {
            return null;
        }
    }

    function setMessage(message, isError) {
        elements.message.textContent = message || '';
        elements.message.classList.toggle('error', Boolean(isError));
        if (typeof elements.message.setAttribute === 'function') {
            elements.message.setAttribute('role', isError ? 'alert' : 'status');
            elements.message.setAttribute('aria-live', isError ? 'assertive' : 'polite');
        }
        if (elements.retry) elements.retry.hidden = !isError || !state.initializationError;
    }

    function enrollmentErrorMessage(error) {
        const code = error && (error.message || error.code);
        const configured = code && ENROLLMENT_ERROR_MESSAGES[code];
        if (configured) return translate(configured[0], configured[1]);
        if (['invalid_pcm', 'speech_too_short', 'silence', 'severe_clipping', 'audio_too_long', 'volume_too_low', 'no_speech_detected', 'incomplete_capture'].includes(code)) return translate('voiceIdentity.qualityCheckFailed', '声音质量未达标，请重录当前段。');
        if (code === 'media_devices_unavailable' || code === 'audio_worklet_unavailable' || (error && ['NotAllowedError', 'NotFoundError', 'NotReadableError'].includes(error.name))) return translate('voiceIdentity.microphoneDenied', '无法使用麦克风，请检查权限和设备。');
        if (code === 'page_config_unavailable' || code === 'csrf_token_unavailable' || code === 'status_unavailable' || code === 'initialization_failed') return translate('voiceIdentity.initializationFailed', '声纹录入初始化失败，请检查连接后重试。');
        if (code === 'profile_status_unavailable' || code === 'profile_not_confirmed' || code === 'request_failed' || (error && error.status >= 500)) return translate('voiceIdentity.saveFailed', '声纹保存未完成，请稍后重试。');
        if (code === 'crypto_unavailable') return translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。');
        if (error && (error.status === 0 || !error.status)) return translate('voiceIdentity.backendUnavailable', '无法连接声纹服务，请检查网络后重试。');
        return translate('voiceIdentity.requestFailed', '操作失败，请稍后重试。');
    }

    function enrollmentVerification(payload) {
        const verification = payload && typeof payload.verification === 'object'
            ? payload.verification : null;
        if (!verification || typeof verification.passed !== 'boolean') return null;
        const matchPercent = Number(verification.match_percent);
        return {
            passed: verification.passed,
            matchPercent: Number.isFinite(matchPercent) ? matchPercent : null,
        };
    }

    function verificationRetryMessage(verification) {
        const percent = verification && verification.matchPercent !== null
            ? verification.matchPercent : '--';
        return translate(
            'voiceIdentity.verificationRetry',
            `声纹验证未通过（最低匹配 ${percent}%），请保持自然语气重录当前段。`,
            { percent },
        );
    }

    function phaseLabel() {
        const labels = { idle: ['voiceIdentity.phaseIdle', '准备录入'], preparing: ['voiceIdentity.phasePreparing', '正在准备录音…'], recording: ['voiceIdentity.phaseRecording', '正在录音…'], checking: ['voiceIdentity.phaseChecking', '正在检查声音质量…'], ready: ['voiceIdentity.phaseSegmentReady', '本段已保存，可以继续下一段'], retry: ['voiceIdentity.phaseRetry', '请重录当前段'], finalizing: ['voiceIdentity.phaseFinalizing', '正在完成声纹保存…'], success: ['voiceIdentity.phaseSuccess', '声纹录入完成'] };
        const item = labels[state.uiPhase] || labels.idle;
        return translate(item[0], item[1]);
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
        const hasMessage = Boolean(
            elements.message
            && elements.message.textContent
            && elements.message.classList.contains('error')
        );
        const enrollmentActive = !state.profileAvailable
            || state.busy || state.cancelPending || Boolean(state.enrollmentId);
        const enrollmentVisible = enrollmentActive || hasMessage;
        elements.enrollment.hidden = !enrollmentVisible;
        elements.profileControls.hidden = !state.profileAvailable || enrollmentActive;
        elements.statusDot.className = 'status-dot';
        if (state.effectiveEnabled) elements.statusDot.classList.add('ready');
        else if (state.profileAvailable) elements.statusDot.classList.add('warning');
        elements.profileStatus.textContent = reasonMessage();

        const pending = !state.initialized || state.busy
            || state.cancelPending || state.filterPending;
        const enrollmentUnavailable = ['secure_storage_unavailable', 'model_unavailable']
            .includes(state.effectiveReason);
        elements.start.hidden = state.busy || state.cancelPending
            || (state.profileAvailable && !state.enrollmentId);
        elements.start.disabled = pending || enrollmentUnavailable;
        elements.start.textContent = state.enrollmentId
            ? translate('voiceIdentity.continueEnrollment', '继续录入')
            : translate('voiceIdentity.startEnrollment', '开始录入');
        elements.cancel.hidden = !state.busy && !state.cancelPending && !state.enrollmentId;
        elements.cancel.disabled = state.cancelPending;
        elements.reenroll.disabled = pending || enrollmentUnavailable;
        elements.delete.disabled = pending;
        if (!state.filterPending) elements.filter.checked = state.requestedEnabled;
        elements.filter.disabled = pending;
        if (elements.retry) {
            elements.retry.hidden = !state.initializationError || state.busy;
            elements.retry.disabled = state.busy;
        }
    }

    function fixedPrompts() {
        const prompts = [];
        for (let index = 1; index <= ENROLLMENT_SEGMENT_COUNT; index += 1) {
            prompts.push(translate(
                `voiceIdentity.readingPrompt${index}`,
                ['今天我想和你分享一件趣事。', '窗外的光线正在慢慢变化。', '今天也用自然的声音聊天。', '我正在用自己平时的声音说话。'][index - 1],
            ));
        }
        return prompts;
    }

    function renderEnrollment() {
        const active = state.segmentIndex > 0;
        const captureVisible = active && state.uiPhase !== 'idle' && state.uiPhase !== 'success';
        elements.captureStatus.hidden = !captureVisible;
        elements.captureStatus.classList.toggle('saving', state.saving);
        elements.captureStatus.classList.toggle('voice-detected', state.voiceStatus === 'detected');
        elements.captureStatus.classList.toggle('voice-quiet', state.voiceStatus === 'quiet');
        elements.captureStatus.classList.toggle('voice-waiting', state.voiceStatus === 'waiting');
        elements.captureLabel.textContent = phaseLabel();
        if (elements.voiceState) {
            const voiceStatusKeys = { detected: ['voiceIdentity.voiceDetected', '检测到声音'], quiet: ['voiceIdentity.voiceQuiet', '声音偏小，请靠近麦克风'], waiting: ['voiceIdentity.voiceWaiting', '等待说话'] };
            const configured = voiceStatusKeys[state.voiceStatus] || voiceStatusKeys.waiting;
            elements.voiceState.textContent = state.saving ? '' : translate(configured[0], configured[1]);
        }
        if (elements.finish) {
            elements.finish.hidden = !state.recording;
            elements.finish.disabled = !state.recording;
            elements.finish.textContent = translate('voiceIdentity.finish', '说完了，保存');
        }
        if (elements.next) {
            const nextVisible = state.segmentPhase === 'ready' || state.segmentPhase === 'retry';
            elements.next.hidden = !nextVisible;
            elements.next.disabled = !nextVisible;
            elements.next.textContent = translate(state.segmentPhase === 'retry' ? 'voiceIdentity.retrySegment' : 'voiceIdentity.nextSegment', state.segmentPhase === 'retry' ? '重录本段' : '开始下一段');
        }
        if (elements.stepCount) {
            const fallback = '第 ' + state.segmentIndex + ' / ' + ENROLLMENT_SEGMENT_COUNT + ' 段';
            elements.stepCount.textContent = active ? translate('voiceIdentity.stepCount', fallback, { current: state.segmentIndex, total: ENROLLMENT_SEGMENT_COUNT }) : '';
        }
        if (elements.progress) elements.progress.forEach((item, index) => {
            const completed = active && index < state.segmentIndex - 1;
            const current = active && index === state.segmentIndex - 1;
            item.classList.toggle('active', completed || current);
            item.classList.toggle('completed', completed);
            item.classList.toggle('current', current);
            if (typeof item.setAttribute === 'function') {
                item.setAttribute('aria-current', current ? 'step' : 'false');
                const progressKey = completed
                    ? 'voiceIdentity.segmentCompleted'
                    : current ? 'voiceIdentity.segmentCurrent' : 'voiceIdentity.segmentPending';
                const progressFallback = completed
                    ? `Segment ${index + 1} completed`
                    : current ? `Segment ${index + 1} current` : `Segment ${index + 1} pending`;
                item.setAttribute('aria-label', translate(progressKey, progressFallback, { index: index + 1 }));
            }
            if (completed) item.textContent = '✓';
            else item.textContent = String(index + 1);
        });
        if (elements.stepTitle) elements.stepTitle.textContent = active ? translate('voiceIdentity.readingPromptLabel', '朗读提示语') : translate('voiceIdentity.privacyTitle', '录入 3 段声纹和 1 段验证语音');
        if (elements.stepBody) elements.stepBody.textContent = active ? translate('voiceIdentity.privacyBody', '请使用平时聊天的自然音量和语速朗读下面这句话，说完后点击保存。') : translate('voiceIdentity.privacyBody', '按提示完成 3 段参考录音和 1 段验证录音，第 1 至 3 段最长 3 秒，第 4 段最长 5 秒，说完后点击保存。');
        if (elements.prompt) {
            const prompt = active ? fixedPrompts()[state.segmentIndex - 1] : '';
            elements.prompt.textContent = prompt || '';
            elements.prompt.hidden = !prompt;
        }
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
            let selectedMicrophoneId = null;
            try { selectedMicrophoneId = localStorage.getItem('neko_selected_microphone'); } catch (_) {}
            const constraints = {
                noiseSuppression: false,
                echoCancellation: true,
                autoGainControl: true,
                channelCount: 1
            };
            if (selectedMicrophoneId) constraints.deviceId = { exact: selectedMicrophoneId };
            try {
                state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: constraints, video: false });
            } catch (error) {
                if (!selectedMicrophoneId || !['NotFoundError', 'OverconstrainedError'].includes(error && error.name)) throw error;
                state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: constraints, video: false });
            }
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

    function asPcm16(data) {
        if (data instanceof Int16Array) return data;
        if (data instanceof ArrayBuffer) return new Int16Array(data);
        if (data && data.buffer instanceof ArrayBuffer) {
            return new Int16Array(data.buffer, data.byteOffset || 0, data.byteLength / 2);
        }
        return new Int16Array(data || 0);
    }

    function updateVoiceActivity(chunk) {
        if (!chunk || !chunk.length) return;
        let sumSquares = 0;
        for (let index = 0; index < chunk.length; index += 1) {
            const sample = chunk[index] / 32768;
            sumSquares += sample * sample;
        }
        const rms = Math.sqrt(sumSquares / chunk.length);
        const now = performance.now();
        if (rms >= ACTIVE_FRAME_RMS) {
            state.voiceStatus = 'detected';
            state.lastVoiceAt = now;
        } else if (rms > ACTIVE_FRAME_RMS * 0.25) {
            state.voiceStatus = 'quiet';
        }
        renderEnrollment();
    }

    async function capturePcm16(maxRecordingMs = MAX_RECORDING_MS) {
        if (!Number.isFinite(maxRecordingMs) || maxRecordingMs <= 0) {
            throw new Error('invalid_recording_duration');
        }
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
        let inputGain = null;
        const mute = context.createGain();
        const chunks = [];
        let capturedSamples = 0;
        let startedAt = performance.now();
        let finishCapture = null;
        let flushTimeoutId = null;
        inputGain = context.createGain();
        let gainDb = 0;
        try {
            const savedGainDb = Number(localStorage.getItem('neko_mic_gain_db'));
            if (Number.isFinite(savedGainDb) && savedGainDb >= -5 && savedGainDb <= 25) gainDb = savedGainDb;
        } catch (_) {}
        const dbToLinear = window.appUtils && typeof window.appUtils.dbToLinear === 'function'
            ? window.appUtils.dbToLinear
            : value => Math.pow(10, value / 20);
        inputGain.gain.value = dbToLinear(gainDb);
        mute.gain.value = 0;
        source.connect(inputGain);
        inputGain.connect(processor);
        processor.connect(mute);
        mute.connect(context.destination);
        await context.resume();

        state.voiceStatus = 'waiting';
        state.lastVoiceAt = startedAt;
        const timer = window.setInterval(function () {
            const now = performance.now();
            const elapsed = Math.min(maxRecordingMs, now - startedAt);
            elements.timer.textContent = translate(
                'voiceIdentity.recordingSeconds',
                `${(elapsed / 1000).toFixed(1)} 秒`,
                { seconds: (elapsed / 1000).toFixed(1) }
            );
            if (now - state.lastVoiceAt >= SILENCE_HINT_MS) {
                state.voiceStatus = 'waiting';
                renderEnrollment();
            }
            if (elapsed >= maxRecordingMs && finishCapture) finishCapture();
        }, 100);
        try {
            await new Promise(function (resolve, reject) {
                let settled = false;
                let flushing = false;
                const captureTimeoutId = window.setTimeout(function () {
                    if (finishCapture) finishCapture(new Error('incomplete_capture'));
                }, maxRecordingMs + CAPTURE_TIMEOUT_GRACE_MS);
                const settle = function (error) {
                    if (settled) return;
                    settled = true;
                    window.clearTimeout(captureTimeoutId);
                    if (flushTimeoutId !== null) window.clearTimeout(flushTimeoutId);
                    if (error) reject(error);
                    else resolve();
                };
                finishCapture = function (error) {
                    if (settled) return;
                    if (error) {
                        settle(error);
                        return;
                    }
                    if (flushing) return;
                    flushing = true;
                    try {
                        processor.port.postMessage({ type: 'flush' });
                    } catch (_) {
                        settle(new Error('incomplete_capture'));
                        return;
                    }
                    flushTimeoutId = window.setTimeout(function () {
                        settle(new Error('incomplete_capture'));
                    }, FLUSH_TIMEOUT_MS);
                };
                state.captureAbort = function (error) {
                    settle(error || new Error('capture_cancelled'));
                };
                state.captureFinish = finishCapture;
                processor.port.onmessage = function (event) {
                    const data = event.data;
                    if (data && data.type === 'flush_complete') {
                        const tail = asPcm16(data.pcmData);
                        if (tail.length) {
                            chunks.push(tail);
                            capturedSamples += tail.length;
                            updateVoiceActivity(tail);
                        }
                        settle();
                        return;
                    }
                    const chunk = asPcm16(data);
                    if (chunk.length === 0) return;
                    chunks.push(chunk);
                    capturedSamples += chunk.length;
                    updateVoiceActivity(chunk);
                };
            });
        } finally {
            state.captureAbort = null;
            state.captureFinish = null;
            window.clearInterval(timer);
            if (flushTimeoutId !== null) window.clearTimeout(flushTimeoutId);
            processor.port.onmessage = null;
            processor.disconnect();
            inputGain.disconnect();
            source.disconnect();
            mute.disconnect();
            elements.timer.textContent = '';
        }

        if (capturedSamples <= 0) throw new Error('incomplete_capture');
        const maximumSamples = TARGET_SAMPLE_RATE * maxRecordingMs / 1000;
        const minimumSamples = maximumSamples;
        const alignedSamples = Math.floor(
            Math.min(capturedSamples, maximumSamples) / RUNTIME_CHUNK_SAMPLES
        ) * RUNTIME_CHUNK_SAMPLES;
        if (alignedSamples < minimumSamples) throw new Error('speech_too_short');
        const pcm = new Int16Array(alignedSamples);
        let offset = 0;
        for (const chunk of chunks) {
            const remaining = pcm.length - offset;
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
        state.nextSegmentIndex = 1;
        applyStatus(payload);
    }

    async function startEnrollment() {
        if (state.busy || state.filterPending || state.cancelPending) return;
        state.statusEpoch += 1;
        let startSettled = null;
        let settleStart = null;
        let segmentRequestPending = false;
        let preserveActiveSession = false;
        const profileWasAvailable = state.profileAvailable;
        const profileRevisionBefore = state.profileRevision;
        state.busy = true;
        state.segmentIndex = 1;
        state.segmentPhase = 'preparing';
        state.uiPhase = 'preparing';
        setMessage('');
        render();
        try {
            await ensureMicrophone();
            if (state.closeStarted || state.cancelPending) return;
            startSettled = new Promise(function (resolve) { settleStart = resolve; });
            state.startSettled = startSettled;
            let started;
            try { started = await apiRequest('/enrollment/start', { method: 'POST' }); }
            finally {
                if (settleStart) settleStart();
                if (state.startSettled === startSettled) state.startSettled = null;
            }
            applyStatus(started);
            state.enrollmentId = firstString([started, started.enrollment], ['enrollment_id', 'id', 'session_id'], state.enrollmentId);
            state.profileId = firstString([started, started.enrollment], ['profile_id'], state.profileId || createProfileId());
            if (!state.enrollmentId) throw new Error('enrollment_id_missing');
            if (state.closeStarted || state.cancelPending) { await cancelSession({ keepalive: state.closeStarted, silent: true }); return; }
            const serverNextSegment = Number(firstScalar(
                [started, started.enrollment], ['next_segment_index'], 1
            ));
            let segment = Number.isInteger(serverNextSegment)
                && serverNextSegment >= 1 && serverNextSegment <= ENROLLMENT_SEGMENT_COUNT
                ? serverNextSegment : 1;
            segmentLoop: while (segment <= ENROLLMENT_SEGMENT_COUNT) {
                state.segmentIndex = segment;
                let segmentAccepted = false;
                while (!segmentAccepted) {
                    const recordingDurationMs = segment === ENROLLMENT_SEGMENT_COUNT
                        ? VERIFICATION_RECORDING_MS : REFERENCE_RECORDING_MS;
                    if (segment > 1) {
                        const refreshed = await reconcileStatus();
                        if (!refreshed && !state.enrollmentId) throw new Error('status_unavailable');
                    }
                    if (!state.enrollmentId || (
                        Number.isFinite(state.enrollmentRemainingSeconds)
                        && state.enrollmentRemainingSeconds <= 0
                    )) {
                        throw new Error('stale_enrollment');
                    }
                    state.segmentPhase = 'preparing'; state.uiPhase = 'preparing';
                    state.recording = false;
                    state.saving = false;
                    render();
                    await ensureMicrophone();
                    if (state.cancelPending || state.closeStarted) return;
                    state.segmentPhase = 'recording'; state.uiPhase = 'recording';
                    state.recording = true;
                    render();
                    let pcm16;
                    try {
                        try { pcm16 = await capturePcm16(recordingDurationMs); }
                        finally { state.recording = false; stopMicrophone(); }
                    } catch (error) {
                        if (state.cancelPending || state.closeStarted) return;
                        const retryable = ['incomplete_capture'].includes(error && error.message);
                        if (!retryable || !state.enrollmentId) throw error;
                        if (window.__voiceIdentityTestAutoAdvance) throw error;
                        state.saving = false;
                        state.segmentPhase = 'retry'; state.uiPhase = 'retry';
                        setMessage(enrollmentErrorMessage(error), true);
                        render();
                        const proceed = await new Promise(function (resolve) {
                            state.segmentAdvance = resolve;
                        });
                        state.segmentAdvance = null;
                        if (!proceed || state.cancelPending || state.closeStarted) return;
                        continue;
                    }
                    if (state.cancelPending || state.closeStarted) return;
                    state.segmentPhase = 'checking'; state.uiPhase = 'checking';
                    state.saving = true;
                    render();
                    segmentRequestPending = true;
                    try {
                        const payload = await apiRequest('/enrollment/segment', { method: 'PUT', body: pcm16, headers: { 'Content-Type': 'audio/pcm;format=pcm_s16le;rate=48000;channels=1', [AUDIO_CONTRACT_HEADER]: AUDIO_CONTRACT_ID, [SESSION_HEADER]: state.enrollmentId, [PROFILE_HEADER]: state.profileId, [SEGMENT_HEADER]: String(segment) } });
                        segmentRequestPending = false;
                        if (state.cancelPending || state.closeStarted) return;
                        applyStatus(payload);
                        const verification = segment === ENROLLMENT_SEGMENT_COUNT
                            ? enrollmentVerification(payload) : null;
                        if (verification && !verification.passed) {
                            const nextSegment = Number(
                                payload && payload.enrollment && payload.enrollment.next_segment_index,
                            );
                            state.saving = false;
                            state.segmentPhase = 'retry'; state.uiPhase = 'retry';
                            setMessage(verificationRetryMessage(verification), true);
                            render();
                            const proceed = await new Promise(function (resolve) {
                                state.segmentAdvance = resolve;
                            });
                            state.segmentAdvance = null;
                            if (!proceed || state.cancelPending || state.closeStarted) return;
                            if (nextSegment === 1) {
                                segment = 1;
                                state.segmentIndex = 1;
                                continue segmentLoop;
                            }
                            continue;
                        }
                        setMessage('');
                        segmentAccepted = true;
                    } catch (error) {
                        if (state.cancelPending || state.closeStarted) return;
                        const retryable = ['invalid_pcm', 'speech_too_short', 'silence', 'severe_clipping', 'audio_too_long', 'volume_too_low', 'no_speech_detected'].includes(error && error.message);
                        if (!retryable && state.enrollmentId) preserveActiveSession = true;
                        let canonical = error && error.payload && typeof error.payload === 'object'
                            ? error.payload : null;
                        if (canonical && canonical.enrollment) applyStatus(canonical);
                        if (!retryable && (!canonical || !canonical.enrollment)) canonical = await reconcileStatus();
                        if (canonical && state.enrollmentId) {
                            if (!retryable) preserveActiveSession = true;
                            const canonicalNext = Number(firstScalar(
                                [canonical.enrollment, canonical], ['next_segment_index'], null
                            ));
                            if (Number.isInteger(canonicalNext)
                                && canonicalNext >= 1 && canonicalNext <= ENROLLMENT_SEGMENT_COUNT
                                && canonicalNext !== segment) {
                                if (canonicalNext === 1 && segment === 3) {
                                    state.saving = false;
                                    state.segmentPhase = 'retry'; state.uiPhase = 'retry';
                                    setMessage(enrollmentErrorMessage(error), true);
                                    render();
                                    const proceed = await new Promise(function (resolve) {
                                        state.segmentAdvance = resolve;
                                    });
                                    state.segmentAdvance = null;
                                    if (!proceed || state.cancelPending || state.closeStarted) return;
                                    segment = 1;
                                    continue segmentLoop;
                                }
                                segment = canonicalNext - 1;
                                segmentAccepted = true;
                                continue;
                            }
                        }
                        if (!retryable || !state.enrollmentId) throw error;
                        segmentRequestPending = false;
                        if (window.__voiceIdentityTestAutoAdvance) throw error;
                        state.saving = false;
                        state.segmentPhase = 'retry'; state.uiPhase = 'retry';
                        setMessage(enrollmentErrorMessage(error), true);
                        render();
                        const proceed = await new Promise(function (resolve) {
                            state.segmentAdvance = resolve;
                        });
                        state.segmentAdvance = null;
                        if (!proceed || state.cancelPending || state.closeStarted) return;
                    }
                }
                state.saving = false;
                if (segment < ENROLLMENT_SEGMENT_COUNT) {
                    state.segmentPhase = 'ready'; state.uiPhase = 'ready';
                    render();
                    const proceed = await new Promise(function (resolve) {
                        state.segmentAdvance = resolve;
                        if (window.__voiceIdentityTestAutoAdvance && elements.next) window.setTimeout(function () { elements.next.emit('click'); }, 0);
                    });
                    state.segmentAdvance = null;
                    if (!proceed || state.cancelPending || state.closeStarted) return;
                }
                segment += 1;
            }
            state.segmentPhase = 'finalizing'; state.uiPhase = 'finalizing';
            state.saving = true;
            render();
            if (!state.profileAvailable && !await reconcileStatus()) throw new Error('profile_status_unavailable');
            if (!state.profileAvailable) throw new Error('profile_not_confirmed');
            state.enrollmentId = null;
            state.profileId = null;
            state.uiPhase = 'success';
            setMessage(enrollmentCompleteMessage(), false);
        } catch (error) {
            stopMicrophone();
            if (state.cancelPending || state.closeStarted) return;
            const reconciled = await reconcileStatus();
            const replacementConfirmed = segmentRequestPending && reconciled && state.profileAvailable && (!profileWasAvailable || (profileRevisionBefore !== null && state.profileRevision !== null && state.profileRevision !== profileRevisionBefore));
            if (replacementConfirmed) {
                state.enrollmentId = null; state.profileId = null; setMessage(enrollmentCompleteMessage(), false);
            } else if (preserveActiveSession && state.enrollmentId) {
                setMessage(enrollmentErrorMessage(error), true);
            } else {
                try { await cancelSession(); } catch (_) {}
                const microphoneError = error && (error.name === 'NotAllowedError' || error.name === 'NotFoundError' || error.name === 'NotReadableError' || error.message === 'audio_worklet_unavailable' || error.message === 'media_devices_unavailable');
                if (!state.cancelPending && !state.closeStarted) setMessage(microphoneError ? translate('voiceIdentity.microphoneDenied', '无法使用麦克风，请检查权限和设备。') : enrollmentErrorMessage(error), true);
            }
        } finally {
            stopMicrophone();
            if (settleStart && state.startSettled === startSettled) { state.startSettled = null; settleStart(); }
            if (state.segmentAdvance) { state.segmentAdvance(false); state.segmentAdvance = null; }
            state.recording = false; state.saving = false; state.segmentPhase = 'idle'; state.uiPhase = 'idle'; state.segmentIndex = 0; state.voiceStatus = 'waiting'; state.busy = false;
            if (state.cancelReleaseWhenIdle) {
                state.cancelReleaseWhenIdle = false;
                state.cancelPending = false;
            }
            render();
        }
    }

    async function cancelEnrollment(options) {
        const config = options || {};
        state.statusEpoch += 1;
        state.cancelPending = true;
        if (state.segmentAdvance) { state.segmentAdvance(false); state.segmentAdvance = null; }
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
            else state.cancelReleaseWhenIdle = true;
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
        if (elements.next) elements.next.addEventListener('click', function () {
            if ((state.segmentPhase === 'ready' || state.segmentPhase === 'retry') && state.segmentAdvance) state.segmentAdvance(true);
        });
        elements.finish.addEventListener('click', function () {
            if (state.recording && state.captureFinish) state.captureFinish();
        });
        elements.cancel.addEventListener('click', function () {
            cancelEnrollment().catch(function () {});
        });
        elements.delete.addEventListener('click', deleteProfile);
        elements.filter.addEventListener('change', updateFilter);
        if (elements.retry) elements.retry.addEventListener('click', retryConnection);
        window.addEventListener('localechange', render);
        const refreshVisibleStatus = function () {
            if (state.busy || state.cancelPending || document.visibilityState === 'hidden') return;
            reconcileStatus().catch(function () {});
        };
        window.addEventListener('focus', refreshVisibleStatus);
        document.addEventListener('visibilitychange', refreshVisibleStatus);
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

    async function retryConnection() {
        if (state.busy) return;
        state.busy = true;
        state.initializationError = false;
        setMessage('');
        render();
        try {
            await loadCsrfToken();
            const status = await apiRequest('/status', { method: 'GET' });
            state.initialized = true;
            applyStatus(status);
        } catch (error) {
            state.initializationError = true;
            setMessage(enrollmentErrorMessage(error), true);
        } finally {
            state.busy = false;
            render();
        }
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
            state.initializationError = false;
            applyStatus(status);
        } catch (error) {
            state.initializationError = true;
            setMessage(enrollmentErrorMessage(error), true);
        } finally {
            state.busy = false;
            render();
        }
    }

    document.addEventListener('DOMContentLoaded', initialize);
})();
