/**
 * app-audio-capture.js — 麦克风捕获 / 释放 / 增益 / 静音检测 / 音量可视化
 *
 * 依赖：app-state.js（window.appState / window.appConst / window.appUtils）
 *
 * 导出：window.appAudioCapture
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;

    // ======================== DOM 辅助 ========================

    function micButton()          { return document.getElementById('micButton'); }
    function muteButton()         { return document.getElementById('muteButton'); }
    function screenButton()       { return document.getElementById('screenButton'); }
    function stopButton()         { return document.getElementById('stopButton'); }
    function resetSessionButton() { return document.getElementById('resetSessionButton'); }
    function statusElement()      { return document.getElementById('status'); }

    // ======================== 麦克风设备选择 ========================

    async function selectMicrophone(deviceId) {
        S.selectedMicrophoneId = deviceId;

        // 获取设备名称用于状态提示
        let deviceName = '系统默认麦克风';
        if (deviceId) {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                const audioInputs = devices.filter(device => device.kind === 'audioinput');
                const selectedDevice = audioInputs.find(device => device.deviceId === deviceId);
                if (selectedDevice) {
                    deviceName = selectedDevice.label || `麦克风 ${audioInputs.indexOf(selectedDevice) + 1}`;
                }
            } catch (error) {
                console.error(window.t('console.getDeviceNameFailed'), error);
            }
        }

        // 更新UI选中状态
        const options = document.querySelectorAll('.mic-option');
        options.forEach(option => {
            if ((option.classList.contains('default') && deviceId === null) ||
                (option.dataset.deviceId === deviceId && deviceId !== null)) {
                option.classList.add('selected');
            } else {
                option.classList.remove('selected');
            }
        });

        // 保存选择到服务器
        await saveSelectedMicrophone(deviceId);

        // 如果正在录音，先显示选择提示，然后延迟重启录音
        if (S.isRecording) {
            const wasRecording = S.isRecording;
            // 先显示选择提示
            window.showStatusToast(window.t ? window.t('app.deviceSelected', { device: deviceName }) : `已选择 ${deviceName}`, 3000);

            // 保存需要恢复的状态
            const shouldRestartProactiveVision = S.proactiveVisionEnabled && S.isRecording;
            const shouldRestartScreening = S.videoSenderInterval !== undefined && S.videoSenderInterval !== null;

            // 防止并发切换导致状态混乱
            if (window._isSwitchingMicDevice) {
                console.warn(window.t('console.deviceSwitchingWait'));
                window.showStatusToast(window.t ? window.t('app.deviceSwitching') : '设备切换中...', 2000);
                return;
            }
            window._isSwitchingMicDevice = true;

            try {
                // 停止语音期间主动视觉定时
                if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
                    window.stopProactiveVisionDuringSpeech();
                }
                // 停止屏幕共享
                if (typeof window.stopScreening === 'function') {
                    window.stopScreening();
                }
                // 停止静音检测
                stopSilenceDetection();
                // 清理输入analyser
                S.inputAnalyser = null;
                // 停止所有轨道
                if (S.stream instanceof MediaStream) {
                    S.stream.getTracks().forEach(track => track.stop());
                    S.stream = null;
                }
                // 清理 AudioContext 本地资源
                if (S.audioContext) {
                    if (S.audioContext.state !== 'closed') {
                        await S.audioContext.close().catch((e) => console.warn(window.t('console.audioContextCloseFailed'), e));
                    }
                    S.audioContext = null;
                }
                S.workletNode = null;

                // 等待一小段时间，确保选择提示显示出来
                await new Promise(resolve => setTimeout(resolve, 500));

                if (wasRecording) {
                    await startMicCapture();

                    // 重启屏幕共享（如果之前正在共享）
                    if (shouldRestartScreening) {
                        if (typeof window.startScreenSharing === 'function') {
                            try {
                                await window.startScreenSharing();
                            } catch (e) {
                                console.warn(window.t('console.restartScreenShareFailed'), e);
                            }
                        }
                    }
                    // 重启主动视觉（如果之前已启用）
                    if (shouldRestartProactiveVision) {
                        if (typeof window.acquireProactiveVisionStream === 'function') {
                            await window.acquireProactiveVisionStream();
                        }
                        if (typeof window.startProactiveVisionDuringSpeech === 'function') {
                            window.startProactiveVisionDuringSpeech();
                        }
                    }
                }
            } catch (e) {
                console.error(window.t('console.switchMicrophoneFailed'), e);
                window.showStatusToast(window.t ? window.t('app.deviceSwitchFailed') : '设备切换失败', 3000);

                // 完整清理：重置状态
                S.isRecording = false;
                window.isRecording = false;

                // 重置所有按钮状态
                const _mic = micButton();
                const _mute = muteButton();
                const _screen = screenButton();
                const _stop = stopButton();

                if (_mic) _mic.classList.remove('recording', 'active');
                if (_mute) _mute.classList.remove('recording', 'active');
                if (_screen) _screen.classList.remove('active');
                if (_stop) _stop.classList.remove('recording', 'active');

                // 同步浮动按钮状态
                if (typeof window.syncFloatingMicButtonState === 'function') {
                    window.syncFloatingMicButtonState(false);
                }
                if (typeof window.syncFloatingScreenButtonState === 'function') {
                    window.syncFloatingScreenButtonState(false);
                }

                // 启用/禁用按钮状态
                if (_mic)  _mic.disabled = false;
                if (_mute) _mute.disabled = true;
                if (_screen) _screen.disabled = true;
                if (_stop) _stop.disabled = true;

                // 显示文本输入区域
                const textInputArea = document.getElementById('text-input-area');
                if (textInputArea) {
                    textInputArea.classList.remove('hidden');
                }

                // 清理资源
                if (typeof window.stopScreening === 'function') {
                    window.stopScreening();
                }
                stopSilenceDetection();
                S.inputAnalyser = null;

                if (S.stream instanceof MediaStream) {
                    S.stream.getTracks().forEach(track => track.stop());
                    S.stream = null;
                }

                if (S.audioContext) {
                    if (S.audioContext.state !== 'closed') {
                        await S.audioContext.close().catch((err) => console.warn('AudioContext close 失败:', err));
                    }
                    S.audioContext = null;
                }
                S.workletNode = null;

                // 通知后端
                if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({ action: 'pause_session' }));
                }

                // 如果主动搭话已启用且选择了搭话方式，重置并开始定时
                if (S.proactiveChatEnabled && typeof window.hasAnyChatModeEnabled === 'function' && window.hasAnyChatModeEnabled()) {
                    window.lastUserInputTime = Date.now();
                    if (typeof window.resetProactiveChatBackoff === 'function') {
                        window.resetProactiveChatBackoff();
                    }
                }

                window._isSwitchingMicDevice = false;
                return;
            } finally {
                window._isSwitchingMicDevice = false;
            }
        } else {
            // 如果不在录音，直接显示选择提示
            window.showStatusToast(window.t ? window.t('app.deviceSelected', { device: deviceName }) : `已选择 ${deviceName}`, 3000);
        }
    }

    // 保存选择的麦克风到服务器和 localStorage
    async function saveSelectedMicrophone(deviceId) {
        try {
            if (deviceId) {
                localStorage.setItem('neko_selected_microphone', deviceId);
            } else {
                localStorage.removeItem('neko_selected_microphone');
            }
        } catch (e) { }

        try {
            const response = await fetch('/api/characters/set_microphone', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    microphone_id: deviceId
                })
            });

            if (!response.ok) {
                console.error(window.t('console.saveMicrophoneSelectionFailed'));
            }
        } catch (err) {
            console.error(window.t('console.saveMicrophoneSelectionError'), err);
        }
    }

    // 加载上次选择的麦克风（优先从 localStorage 加载，快速恢复）
    function loadSelectedMicrophone() {
        try {
            const saved = localStorage.getItem('neko_selected_microphone');
            if (saved) {
                S.selectedMicrophoneId = saved;
                console.log(`已加载麦克风设置: ${saved}`);
            }
        } catch (e) {
            S.selectedMicrophoneId = null;
        }
    }

    // ======================== 麦克风增益 ========================

    // 保存麦克风增益设置到 localStorage（保存分贝值）
    function saveMicGainSetting() {
        try {
            localStorage.setItem('neko_mic_gain_db', String(S.microphoneGainDb));
            console.log(`麦克风增益设置已保存: ${S.microphoneGainDb}dB`);
        } catch (err) {
            console.error('保存麦克风增益设置失败:', err);
        }
    }

    // 从 localStorage 加载麦克风增益设置
    function loadMicGainSetting() {
        try {
            const savedGainDb = localStorage.getItem('neko_mic_gain_db');
            if (savedGainDb !== null) {
                const gainDb = parseFloat(savedGainDb);
                // 验证增益值在有效范围内
                if (!isNaN(gainDb) && gainDb >= C.MIN_MIC_GAIN_DB && gainDb <= C.MAX_MIC_GAIN_DB) {
                    S.microphoneGainDb = gainDb;
                    console.log(`已加载麦克风增益设置: ${S.microphoneGainDb}dB`);
                } else {
                    console.warn(`无效的增益值 ${savedGainDb}dB，使用默认值 ${C.DEFAULT_MIC_GAIN_DB}dB`);
                    S.microphoneGainDb = C.DEFAULT_MIC_GAIN_DB;
                }
            } else {
                console.log(`未找到麦克风增益设置，使用默认值 ${C.DEFAULT_MIC_GAIN_DB}dB`);
            }
        } catch (err) {
            console.error('加载麦克风增益设置失败:', err);
            S.microphoneGainDb = C.DEFAULT_MIC_GAIN_DB;
        }
    }

    // 格式化增益显示（带正负号）
    function formatGainDisplay(db) {
        if (db > 0) {
            return `+${db}dB`;
        } else if (db === 0) {
            return '0dB';
        } else {
            return `${db}dB`;
        }
    }

    // 更新麦克风增益（供外部调用，参数为分贝值）
    window.setMicrophoneGain = function (gainDb) {
        if (gainDb >= C.MIN_MIC_GAIN_DB && gainDb <= C.MAX_MIC_GAIN_DB) {
            S.microphoneGainDb = gainDb;
            if (S.micGainNode) {
                S.micGainNode.gain.value = window.appUtils.dbToLinear(gainDb);
            }
            saveMicGainSetting();
            // 更新 UI 滑块（如果存在）
            const slider = document.getElementById('mic-gain-slider');
            const valueDisplay = document.getElementById('mic-gain-value');
            if (slider) slider.value = String(gainDb);
            if (valueDisplay) valueDisplay.textContent = formatGainDisplay(gainDb);
            console.log(`麦克风增益已设置: ${gainDb}dB`);
        }
    };

    // 获取当前麦克风增益（返回分贝值）
    window.getMicrophoneGain = function () {
        return S.microphoneGainDb;
    };

    // ======================== 静音检测 ========================

    function startSilenceDetection() {
        // 重置检测状态
        S.hasSoundDetected = false;

        // 清除之前的定时器(如果有)
        if (S.silenceDetectionTimer) {
            clearTimeout(S.silenceDetectionTimer);
        }

        // 启动5秒定时器
        S.silenceDetectionTimer = setTimeout(() => {
            if (!S.hasSoundDetected && S.isRecording) {
                window.showStatusToast(window.t ? window.t('app.micNoSound') : '⚠️ 麦克风无声音，请检查麦克风设置', 5000);
                console.warn('麦克风静音检测：5秒内未检测到声音');
            }
        }, 5000);
    }

    // 停止麦克风静音检测
    function stopSilenceDetection() {
        if (S.silenceDetectionTimer) {
            clearTimeout(S.silenceDetectionTimer);
            S.silenceDetectionTimer = null;
        }
        S.hasSoundDetected = false;
    }

    // 监测音频输入音量
    function monitorInputVolume() {
        if (!S.inputAnalyser || !S.isRecording) {
            return;
        }

        const dataArray = new Uint8Array(S.inputAnalyser.fftSize);
        S.inputAnalyser.getByteTimeDomainData(dataArray);

        // 计算音量(RMS)
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const val = (dataArray[i] - 128) / 128.0;
            sum += val * val;
        }
        const rms = Math.sqrt(sum / dataArray.length);

        // 如果音量超过阈值(0.01),认为检测到声音
        if (rms > 0.01) {
            if (!S.hasSoundDetected) {
                S.hasSoundDetected = true;
                console.log('麦克风静音检测：检测到声音，RMS =', rms);

                // 如果之前显示了无声音警告，现在检测到声音了，恢复正常状态显示
                const noSoundText = window.t ? window.t('voiceControl.noSound') : '麦克风无声音';
                const _status = statusElement();
                if (_status && _status.textContent.includes(noSoundText)) {
                    window.showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);
                    console.log('麦克风静音检测：检测到声音，已清除警告');
                }
            }
        }

        // 持续监测
        if (S.isRecording) {
            requestAnimationFrame(monitorInputVolume);
        }
    }

    // ======================== AudioWorklet ========================

    async function startAudioWorklet(mediaStream) {
        // 先清理旧的音频上下文，防止多个 worklet 同时发送数据导致 QPS 超限
        if (S.audioContext) {
            if (S.audioContext.state !== 'closed') {
                try {
                    await S.audioContext.close();
                } catch (e) {
                    console.warn('关闭旧音频上下文时出错:', e);
                    // 强制复位所有状态，防止状态不一致
                    const _mic = micButton();
                    if (_mic) _mic.classList.remove('recording', 'active');
                    if (typeof window.syncFloatingMicButtonState === 'function') {
                        window.syncFloatingMicButtonState(false);
                    }
                    if (typeof window.syncFloatingScreenButtonState === 'function') {
                        window.syncFloatingScreenButtonState(false);
                    }
                    const _mute = muteButton();
                    const _screen = screenButton();
                    const _stop = stopButton();
                    if (_mic) _mic.disabled = false;
                    if (_mute) _mute.disabled = true;
                    if (_screen) _screen.disabled = true;
                    if (_stop) _stop.disabled = true;
                    window.showStatusToast(window.t ? window.t('app.audioContextError') : '音频系统异常，请重试', 3000);
                    throw e;
                }
            }
            S.audioContext = null;
            S.workletNode = null;
        }

        // 创建音频上下文，强制使用 48kHz 采样率
        S.audioContext = new AudioContext({ sampleRate: 48000 });
        console.log("音频上下文采样率 (强制48kHz):", S.audioContext.sampleRate);

        // 创建媒体流源
        const source = S.audioContext.createMediaStreamSource(mediaStream);

        // 创建增益节点用于麦克风音量放大
        S.micGainNode = S.audioContext.createGain();
        const linearGain = window.appUtils.dbToLinear(S.microphoneGainDb);
        S.micGainNode.gain.value = linearGain;
        console.log(`麦克风增益已设置: ${S.microphoneGainDb}dB (${linearGain.toFixed(2)}x)`);

        // 创建analyser节点用于监测输入音量
        S.inputAnalyser = S.audioContext.createAnalyser();
        S.inputAnalyser.fftSize = 2048;
        S.inputAnalyser.smoothingTimeConstant = 0.8;

        // 连接 source → gainNode → analyser（用于音量检测，检测增益后的音量）
        source.connect(S.micGainNode);
        S.micGainNode.connect(S.inputAnalyser);

        try {
            // 加载AudioWorklet处理器
            await S.audioContext.audioWorklet.addModule('/static/audio-processor.js');

            // 根据连接类型确定目标采样率
            const isMobile = window.appUtils.isMobile;
            const targetSampleRate = isMobile() ? 16000 : 48000;
            console.log(`音频采样率配置: 原始=${S.audioContext.sampleRate}Hz, 目标=${targetSampleRate}Hz, 移动端=${isMobile()}`);

            // 创建AudioWorkletNode
            S.workletNode = new AudioWorkletNode(S.audioContext, 'audio-processor', {
                processorOptions: {
                    originalSampleRate: S.audioContext.sampleRate,
                    targetSampleRate: targetSampleRate
                }
            });

            // 监听处理器发送的消息
            S.workletNode.port.onmessage = (event) => {
                const audioData = event.data;

                // Focus模式：focusModeEnabled为true且AI正在播放语音时，自动静音麦克风
                if (S.focusModeEnabled === true && S.isPlaying === true) {
                    return;
                }

                if (S.isRecording && S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: Array.from(audioData),
                        input_type: 'audio'
                    }));
                }
            };

            // 连接节点：gainNode → workletNode（音频经过增益处理后发送）
            S.micGainNode.connect(S.workletNode);

            // 所有初始化成功后，才标记为录音状态
            S.isRecording = true;
            window.isRecording = true;

        } catch (err) {
            console.error('加载AudioWorklet失败:', err);
            console.dir(err);
            window.showStatusToast(window.t ? window.t('app.audioWorkletFailed') : 'AudioWorklet加载失败', 5000);
            stopSilenceDetection();
        }
    }

    // ======================== 录音开始/停止 ========================

    // 开麦，按钮on click
    async function startMicCapture() {
        const _mic = micButton();
        const _mute = muteButton();
        const _screen = screenButton();
        const _stop = stopButton();
        const _reset = resetSessionButton();

        try {
            // 开始录音前添加录音状态类到两个按钮
            if (_mic) _mic.classList.add('recording');

            // 隐藏文本输入区（仅非移动端），确保语音/文本互斥
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea && !window.appUtils.isMobile()) {
                textInputArea.classList.add('hidden');
            }

            if (!S.audioPlayerContext) {
                S.audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
                if (typeof window.syncAudioGlobals === 'function') {
                    window.syncAudioGlobals();
                }
            }

            if (S.audioPlayerContext.state === 'suspended') {
                await S.audioPlayerContext.resume();
            }

            // 获取麦克风流，使用选择的麦克风设备ID
            const baseAudioConstraints = {
                noiseSuppression: false,
                echoCancellation: true,
                autoGainControl: true,
                channelCount: 1
            };

            const constraints = {
                audio: S.selectedMicrophoneId
                    ? { ...baseAudioConstraints, deviceId: { exact: S.selectedMicrophoneId } }
                    : baseAudioConstraints
            };

            S.stream = await navigator.mediaDevices.getUserMedia(constraints);

            // 检查音频轨道状态
            const audioTracks = S.stream.getAudioTracks();
            console.log(window.t('console.audioTrackCount'), audioTracks.length);
            console.log(window.t('console.audioTrackStatus'), audioTracks.map(track => ({
                label: track.label,
                enabled: track.enabled,
                muted: track.muted,
                readyState: track.readyState
            })));

            if (audioTracks.length === 0) {
                console.error(window.t('console.noAudioTrackAvailable'));
                window.showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);
                if (_mic) {
                    _mic.classList.remove('recording');
                    _mic.classList.remove('active');
                }
                throw new Error('没有可用的音频轨道');
            }

            await startAudioWorklet(S.stream);

            if (_mic)    _mic.disabled = true;
            if (_mute)   _mute.disabled = false;
            if (_screen) _screen.disabled = false;
            if (_stop)   _stop.disabled = true;
            if (_reset)  _reset.disabled = false;
            window.showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);

            // 确保active类存在
            if (_mic && !_mic.classList.contains('active')) {
                _mic.classList.add('active');
            }
            if (typeof window.syncFloatingMicButtonState === 'function') {
                window.syncFloatingMicButtonState(true);
            }

            // 立即更新音量显示状态（显示"检测中"）
            updateMicVolumeStatusNow(true);

            // 开始录音时，停止主动搭话定时器
            if (typeof window.stopProactiveChatSchedule === 'function') {
                window.stopProactiveChatSchedule();
            }
        } catch (err) {
            console.error(window.t('console.getMicrophonePermissionFailed'), err);
            window.showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);

            // 失败时恢复文本输入区
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }

            // 失败时移除录音状态类
            if (_mic) {
                _mic.classList.remove('recording');
                _mic.classList.remove('active');
            }
            throw err;
        }
    }

    // 闭麦，按钮on click
    async function stopMicCapture() {
        S.isSwitchingMode = true;

        // 隐藏语音准备提示（防止残留）
        if (typeof window.hideVoicePreparingToast === 'function') {
            window.hideVoicePreparingToast();
        }

        // 清理 session Promise 相关状态
        if (window.sessionTimeoutId) {
            clearTimeout(window.sessionTimeoutId);
            window.sessionTimeoutId = null;
        }
        if (S.sessionStartedRejecter) {
            try {
                S.sessionStartedRejecter(new Error('Session aborted'));
            } catch (e) { /* ignore already handled */ }
            S.sessionStartedRejecter = null;
        }
        if (S.sessionStartedResolver) {
            S.sessionStartedResolver = null;
        }

        const _mic = micButton();
        const _mute = muteButton();
        const _screen = screenButton();
        const _stop = stopButton();
        const _reset = resetSessionButton();

        // 停止录音时移除录音状态类
        if (_mic) {
            _mic.classList.remove('recording');
            _mic.classList.remove('active');
        }
        if (_screen) _screen.classList.remove('active');

        // 同步浮动按钮状态
        if (typeof window.syncFloatingMicButtonState === 'function') {
            window.syncFloatingMicButtonState(false);
        }
        if (typeof window.syncFloatingScreenButtonState === 'function') {
            window.syncFloatingScreenButtonState(false);
        }

        // 立即更新音量显示状态（显示"未录音"）
        updateMicVolumeStatusNow(false);

        stopRecording();

        if (_mic)    _mic.disabled = false;
        if (_mute)   _mute.disabled = true;
        if (_screen) _screen.disabled = true;
        if (_stop)   _stop.disabled = true;
        if (_reset)  _reset.disabled = false;

        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) textInputArea.classList.remove('hidden');

        // 停止录音后，重置主动搭话退避级别并开始定时
        if (S.proactiveChatEnabled && typeof window.hasAnyChatModeEnabled === 'function' && window.hasAnyChatModeEnabled()) {
            window.lastUserInputTime = Date.now();
            if (typeof window.resetProactiveChatBackoff === 'function') {
                window.resetProactiveChatBackoff();
            }
        }

        // 显示待机状态
        const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
        window.showStatusToast(window.t ? window.t('app.standby', { name: lanlanName }) : `${lanlanName}待机中...`, 2000);

        // 延迟重置模式切换标志
        setTimeout(() => {
            S.isSwitchingMode = false;
        }, 500);
    }

    // 停止录音（内部辅助，清理音频管道与后端通信）
    function stopRecording() {
        // 停止语音期间主动视觉定时
        if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
            window.stopProactiveVisionDuringSpeech();
        }
        // 输入结束/打断时重置搜歌任务
        if (typeof window.invalidatePendingMusicSearch === 'function') {
            window.invalidatePendingMusicSearch();
        }

        if (typeof window.stopScreening === 'function') {
            window.stopScreening();
        }
        if (!S.isRecording) return;

        S.isRecording = false;
        window.isRecording = false;
        window.currentGeminiMessage = null;

        // 重置语音模式用户转录合并追踪
        S.lastVoiceUserMessage = null;
        S.lastVoiceUserMessageTime = 0;

        // 清理 AI 回复相关的队列和缓冲区
        window._realisticGeminiQueue = [];
        window._realisticGeminiBuffer = '';
        window._geminiTurnFullText = '';
        window._pendingMusicCommand = '';
        window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
        window.currentTurnGeminiBubbles = [];
        window._isProcessingRealisticQueue = false;

        // 停止静音检测
        stopSilenceDetection();

        // 清理输入analyser
        S.inputAnalyser = null;

        // 停止所有轨道
        if (S.stream) {
            S.stream.getTracks().forEach(track => track.stop());
        }

        // 关闭AudioContext
        if (S.audioContext) {
            if (S.audioContext.state !== 'closed') {
                S.audioContext.close();
            }
            S.audioContext = null;
            S.workletNode = null;
        }

        // 通知服务器暂停会话
        if (S.socket && S.socket.readyState === WebSocket.OPEN) {
            S.socket.send(JSON.stringify({
                action: 'pause_session'
            }));
        }
    }

    // ======================== 音量可视化 ========================

    // 启动麦克风音量可视化
    function startMicVolumeVisualization() {
        // 先停止现有的动画
        stopMicVolumeVisualization();

        // 缓存 DOM 引用，仅在元素被销毁时重新查询
        let cachedBarFill = document.getElementById('mic-volume-bar-fill');
        let cachedStatus = document.getElementById('mic-volume-status');
        let cachedHint = document.getElementById('mic-volume-hint');
        let cachedPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic') || document.getElementById('mmd-popup-mic');

        function updateVolumeDisplay() {
            // 仅当缓存元素被移出 DOM 时才重新查询（popup 重建场景）
            if (!cachedBarFill || !cachedBarFill.isConnected) {
                cachedBarFill = document.getElementById('mic-volume-bar-fill');
                cachedStatus = document.getElementById('mic-volume-status');
                cachedHint = document.getElementById('mic-volume-hint');
                cachedPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic') || document.getElementById('mmd-popup-mic');
            }

            if (!cachedBarFill) {
                stopMicVolumeVisualization();
                return;
            }

            // 检查弹出框是否仍然可见
            if (!cachedPopup || cachedPopup.style.display === 'none' || !cachedPopup.offsetParent) {
                S.micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
                return;
            }

            // 检查是否正在录音且有 analyser
            if (S.isRecording && S.inputAnalyser) {
                // 获取音频数据
                const dataArray = new Uint8Array(S.inputAnalyser.frequencyBinCount);
                S.inputAnalyser.getByteFrequencyData(dataArray);

                // 计算平均音量 (0-255)
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    sum += dataArray[i];
                }
                const average = sum / dataArray.length;

                // 转换为百分比 (0-100)，使用对数缩放使显示更自然
                const volumePercent = Math.min(100, (average / 128) * 100);

                // 更新音量条
                cachedBarFill.style.width = `${volumePercent}%`;

                // 根据音量设置颜色
                if (volumePercent < 5) {
                    cachedBarFill.style.backgroundColor = '#dc3545'; // 红色 - 无声音
                } else if (volumePercent < 20) {
                    cachedBarFill.style.backgroundColor = '#ffc107'; // 黄色 - 音量偏低
                } else if (volumePercent > 90) {
                    cachedBarFill.style.backgroundColor = '#fd7e14'; // 橙色 - 音量过高
                } else {
                    cachedBarFill.style.backgroundColor = '#28a745'; // 绿色 - 正常
                }

                // 更新状态文字
                if (cachedStatus) {
                    if (volumePercent < 5) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeNoSound') : '无声音';
                        cachedStatus.style.color = '#dc3545';
                    } else if (volumePercent < 20) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeLow') : '音量偏低';
                        cachedStatus.style.color = '#ffc107';
                    } else if (volumePercent > 90) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeHigh') : '音量较高';
                        cachedStatus.style.color = '#fd7e14';
                    } else {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeNormal') : '正常';
                        cachedStatus.style.color = '#28a745';
                    }
                }

                // 更新提示文字
                if (cachedHint) {
                    if (volumePercent < 5) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintNoSound') : '检测不到声音，请检查麦克风';
                    } else if (volumePercent < 20) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintLow') : '音量较低，建议调高增益';
                    } else {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintOk') : '麦克风工作正常';
                    }
                }
            } else {
                // 未录音状态
                cachedBarFill.style.width = '0%';
                cachedBarFill.style.backgroundColor = '#4f8cff';
                if (cachedStatus) {
                    cachedStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
                    cachedStatus.style.color = 'var(--neko-popup-text-sub)';
                }
                if (cachedHint) {
                    cachedHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
                }
            }

            // 继续下一帧
            S.micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
        }

        // 启动动画循环
        S.micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
    }

    // 停止麦克风音量可视化
    function stopMicVolumeVisualization() {
        if (S.micVolumeAnimationId) {
            cancelAnimationFrame(S.micVolumeAnimationId);
            S.micVolumeAnimationId = null;
        }
    }

    // 立即更新音量显示状态（用于录音状态变化时立即反映）
    function updateMicVolumeStatusNow(recording) {
        const volumeBarFill = document.getElementById('mic-volume-bar-fill');
        const volumeStatus = document.getElementById('mic-volume-status');
        const volumeHint = document.getElementById('mic-volume-hint');

        if (recording) {
            if (volumeStatus) {
                volumeStatus.textContent = window.t ? window.t('microphone.volumeDetecting') : '检测中...';
                volumeStatus.style.color = '#4f8cff';
            }
            if (volumeHint) {
                volumeHint.textContent = window.t ? window.t('microphone.volumeHintDetecting') : '正在检测麦克风输入...';
            }
            if (volumeBarFill) {
                volumeBarFill.style.backgroundColor = '#4f8cff';
            }
        } else {
            if (volumeBarFill) {
                volumeBarFill.style.width = '0%';
                volumeBarFill.style.backgroundColor = '#4f8cff';
            }
            if (volumeStatus) {
                volumeStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
                volumeStatus.style.color = 'var(--neko-popup-text-sub)';
            }
            if (volumeHint) {
                volumeHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
            }
        }
    }

    // ======================== 暴露到 window（向后兼容） ========================
    window.startMicCapture = startMicCapture;
    window.stopMicCapture = stopMicCapture;
    window.stopRecording = stopRecording;
    window.startSilenceDetection = startSilenceDetection;
    window.stopSilenceDetection = stopSilenceDetection;
    window.monitorInputVolume = monitorInputVolume;
    window.selectMicrophone = selectMicrophone;
    window.loadSelectedMicrophone = loadSelectedMicrophone;
    window.saveSelectedMicrophone = saveSelectedMicrophone;
    window.saveMicGainSetting = saveMicGainSetting;
    window.loadMicGainSetting = loadMicGainSetting;
    window.formatGainDisplay = formatGainDisplay;
    window.startMicVolumeVisualization = startMicVolumeVisualization;
    window.stopMicVolumeVisualization = stopMicVolumeVisualization;
    window.updateMicVolumeStatusNow = updateMicVolumeStatusNow;
    // setMicrophoneGain / getMicrophoneGain 已在上方直接定义为 window 属性

    // ======================== 模块导出 ========================
    mod.selectMicrophone = selectMicrophone;
    mod.saveSelectedMicrophone = saveSelectedMicrophone;
    mod.loadSelectedMicrophone = loadSelectedMicrophone;
    mod.saveMicGainSetting = saveMicGainSetting;
    mod.loadMicGainSetting = loadMicGainSetting;
    mod.formatGainDisplay = formatGainDisplay;
    mod.startSilenceDetection = startSilenceDetection;
    mod.stopSilenceDetection = stopSilenceDetection;
    mod.monitorInputVolume = monitorInputVolume;
    mod.startAudioWorklet = startAudioWorklet;
    mod.startMicCapture = startMicCapture;
    mod.stopMicCapture = stopMicCapture;
    mod.stopRecording = stopRecording;
    mod.startMicVolumeVisualization = startMicVolumeVisualization;
    mod.stopMicVolumeVisualization = stopMicVolumeVisualization;
    mod.updateMicVolumeStatusNow = updateMicVolumeStatusNow;

    // ======================== 麦克风设备列表 UI ========================

    var micPermissionGranted = false;
    var cachedMicDevices = null;

    /** 请求麦克风权限并缓存设备列表 */
    async function ensureMicrophonePermission() {
        if (micPermissionGranted && cachedMicDevices) {
            return cachedMicDevices;
        }
        try {
            var tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            tempStream.getTracks().forEach(function (track) { track.stop(); });
            micPermissionGranted = true;
            console.log('麦克风权限已获取');
            var devices = await navigator.mediaDevices.enumerateDevices();
            cachedMicDevices = devices.filter(function (d) { return d.kind === 'audioinput'; });
            return cachedMicDevices;
        } catch (error) {
            console.warn('请求麦克风权限失败:', error);
            try {
                var devices2 = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices2.filter(function (d) { return d.kind === 'audioinput'; });
                return cachedMicDevices;
            } catch (enumError) {
                console.error('获取设备列表失败:', enumError);
                return [];
            }
        }
    }

    // 监听设备变化，更新缓存
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', async function () {
            console.log('检测到设备变化，刷新麦克风列表...');
            try {
                var devices = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices.filter(function (d) { return d.kind === 'audioinput'; });
                var micPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic') || document.getElementById('mmd-popup-mic');
                if (micPopup && micPopup.style.display === 'flex') {
                    await window.renderFloatingMicList();
                }
            } catch (error) {
                console.error('设备变化后更新列表失败:', error);
            }
        });
    }

    /** 为浮动弹出框渲染麦克风列表 */
    window.renderFloatingMicList = async function (popupArg) {
        var micPopup = popupArg || document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic') || document.getElementById('mmd-popup-mic');
        if (!micPopup) return false;

        try {
            var audioInputs = await ensureMicrophonePermission();
            micPopup.innerHTML = '';

            if (audioInputs.length === 0) {
                var noMicItem = document.createElement('div');
                noMicItem.textContent = window.t ? window.t('microphone.noDevices') : '没有检测到麦克风设备';
                noMicItem.style.padding = '8px 12px';
                noMicItem.style.color = 'var(--neko-popup-text-sub)';
                noMicItem.style.fontSize = '13px';
                micPopup.appendChild(noMicItem);
                return false;
            }

            // ===== 双栏布局 =====
            var leftColumn = document.createElement('div');
            Object.assign(leftColumn.style, { flex: '1', minWidth: '180px', display: 'flex', flexDirection: 'column', overflowY: 'auto' });

            var rightColumn = document.createElement('div');
            Object.assign(rightColumn.style, { flex: '1', minWidth: '160px', display: 'flex', flexDirection: 'column', overflowY: 'auto' });

            // ===== 左栏 1. 扬声器音量 =====
            var speakerContainer = document.createElement('div');
            speakerContainer.className = 'speaker-volume-container';
            speakerContainer.style.padding = '8px 12px';

            var speakerHeader = document.createElement('div');
            Object.assign(speakerHeader.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' });

            var speakerLabel = document.createElement('span');
            speakerLabel.textContent = window.t ? window.t('speaker.volumeLabel') : '扬声器音量';
            speakerLabel.setAttribute('data-i18n', 'speaker.volumeLabel');
            Object.assign(speakerLabel.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var speakerValue = document.createElement('span');
            speakerValue.id = 'speaker-volume-value';
            speakerValue.textContent = S.speakerVolume + '%';
            Object.assign(speakerValue.style, { fontSize: '12px', color: '#4f8cff', fontWeight: '500' });

            speakerHeader.appendChild(speakerLabel);
            speakerHeader.appendChild(speakerValue);
            speakerContainer.appendChild(speakerHeader);

            var speakerSlider = document.createElement('input');
            speakerSlider.type = 'range';
            speakerSlider.id = 'speaker-volume-slider';
            speakerSlider.min = '0';
            speakerSlider.max = '100';
            speakerSlider.step = '1';
            speakerSlider.value = String(S.speakerVolume);
            Object.assign(speakerSlider.style, { width: '100%', height: '6px', borderRadius: '3px', cursor: 'pointer', accentColor: '#4f8cff' });

            speakerSlider.addEventListener('input', function (e) {
                var newVol = parseInt(e.target.value, 10);
                S.speakerVolume = newVol;
                speakerValue.textContent = newVol + '%';
                if (S.speakerGainNode) {
                    S.speakerGainNode.gain.setTargetAtTime(newVol / 100, S.speakerGainNode.context.currentTime, 0.05);
                }
            });
            speakerSlider.addEventListener('change', function () {
                if (typeof window.saveSpeakerVolumeSetting === 'function') window.saveSpeakerVolumeSetting();
            });
            speakerContainer.appendChild(speakerSlider);

            var speakerHint = document.createElement('div');
            speakerHint.textContent = window.t ? window.t('speaker.volumeHint') : '调节AI语音的播放音量';
            speakerHint.setAttribute('data-i18n', 'speaker.volumeHint');
            Object.assign(speakerHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            speakerContainer.appendChild(speakerHint);
            leftColumn.appendChild(speakerContainer);

            // 分隔线
            var sep1 = document.createElement('div');
            Object.assign(sep1.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '8px 0' });
            leftColumn.appendChild(sep1);

            // ===== 左栏 2. 麦克风增益 =====
            var gainContainer = document.createElement('div');
            gainContainer.className = 'mic-gain-container';
            gainContainer.style.padding = '8px 12px';

            var gainHeader = document.createElement('div');
            Object.assign(gainHeader.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' });

            var gainLabel = document.createElement('span');
            gainLabel.textContent = window.t ? window.t('microphone.gainLabel') : '麦克风增益';
            Object.assign(gainLabel.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var gainValueEl = document.createElement('span');
            gainValueEl.id = 'mic-gain-value';
            gainValueEl.textContent = formatGainDisplay(S.microphoneGainDb);
            Object.assign(gainValueEl.style, { fontSize: '12px', color: '#4f8cff', fontWeight: '500' });

            gainHeader.appendChild(gainLabel);
            gainHeader.appendChild(gainValueEl);
            gainContainer.appendChild(gainHeader);

            var gainSlider = document.createElement('input');
            gainSlider.type = 'range';
            gainSlider.id = 'mic-gain-slider';
            gainSlider.min = String(C.MIN_MIC_GAIN_DB);
            gainSlider.max = String(C.MAX_MIC_GAIN_DB);
            gainSlider.step = '1';
            gainSlider.value = String(S.microphoneGainDb);
            Object.assign(gainSlider.style, { width: '100%', height: '6px', borderRadius: '3px', cursor: 'pointer', accentColor: '#4f8cff' });

            gainSlider.addEventListener('input', function (e) {
                var newGainDb = parseFloat(e.target.value);
                S.microphoneGainDb = newGainDb;
                gainValueEl.textContent = formatGainDisplay(newGainDb);
                if (S.micGainNode) {
                    S.micGainNode.gain.value = window.appUtils.dbToLinear(newGainDb);
                }
            });
            gainSlider.addEventListener('change', function () { saveMicGainSetting(); });
            gainContainer.appendChild(gainSlider);

            var gainHint = document.createElement('div');
            gainHint.textContent = window.t ? window.t('microphone.gainHint') : '如果麦克风声音太小，可以调高增益';
            Object.assign(gainHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            gainContainer.appendChild(gainHint);
            leftColumn.appendChild(gainContainer);

            var sep2 = document.createElement('div');
            Object.assign(sep2.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '8px 0' });
            leftColumn.appendChild(sep2);

            // ===== 左栏 3. 音量可视化 =====
            var volumeContainer = document.createElement('div');
            volumeContainer.className = 'mic-volume-container';
            volumeContainer.style.padding = '8px 12px';

            var volumeLabelDiv = document.createElement('div');
            Object.assign(volumeLabelDiv.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' });

            var volumeLabelText = document.createElement('span');
            volumeLabelText.textContent = window.t ? window.t('microphone.volumeLabel') : '实时麦克风音量';
            Object.assign(volumeLabelText.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var volumeStatus = document.createElement('span');
            volumeStatus.id = 'mic-volume-status';
            volumeStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
            Object.assign(volumeStatus.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)' });

            volumeLabelDiv.appendChild(volumeLabelText);
            volumeLabelDiv.appendChild(volumeStatus);
            volumeContainer.appendChild(volumeLabelDiv);

            var volumeBarBg = document.createElement('div');
            volumeBarBg.id = 'mic-volume-bar-bg';
            Object.assign(volumeBarBg.style, { width: '100%', height: '8px', backgroundColor: 'var(--neko-mic-volume-bg, #e9ecef)', borderRadius: '4px', overflow: 'hidden', position: 'relative' });

            var volumeBarFill = document.createElement('div');
            volumeBarFill.id = 'mic-volume-bar-fill';
            Object.assign(volumeBarFill.style, { width: '0%', height: '100%', backgroundColor: '#4f8cff', borderRadius: '4px', transition: 'width 0.05s ease-out, background-color 0.1s ease' });

            volumeBarBg.appendChild(volumeBarFill);
            volumeContainer.appendChild(volumeBarBg);

            var volumeHint = document.createElement('div');
            volumeHint.id = 'mic-volume-hint';
            volumeHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
            Object.assign(volumeHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            volumeContainer.appendChild(volumeHint);
            leftColumn.appendChild(volumeContainer);

            // ===== 右栏：设备列表 =====
            var deviceTitle = document.createElement('div');
            Object.assign(deviceTitle.style, { padding: '8px 12px 6px', fontSize: '13px', fontWeight: '600', color: '#4f8cff', display: 'flex', alignItems: 'center', gap: '6px', borderBottom: '1px solid var(--neko-popup-separator)', marginBottom: '4px' });
            var deviceTitleIcon = document.createElement('span');
            deviceTitleIcon.textContent = '🎙️';
            deviceTitleIcon.style.fontSize = '14px';
            var deviceTitleText = document.createElement('span');
            deviceTitleText.textContent = window.t ? window.t('microphone.deviceTitle') : '选择麦克风设备';
            deviceTitleText.setAttribute('data-i18n', 'microphone.deviceTitle');
            deviceTitle.appendChild(deviceTitleIcon);
            deviceTitle.appendChild(deviceTitleText);
            rightColumn.appendChild(deviceTitle);

            // 默认麦克风选项
            var defaultOption = document.createElement('button');
            defaultOption.className = 'mic-option';
            defaultOption.textContent = window.t ? window.t('microphone.defaultDevice') : '系统默认麦克风';
            if (S.selectedMicrophoneId === null) defaultOption.classList.add('selected');
            Object.assign(defaultOption.style, { padding: '8px 12px', cursor: 'pointer', border: 'none', background: S.selectedMicrophoneId === null ? 'var(--neko-popup-selected-bg)' : 'transparent', borderRadius: '6px', transition: 'background 0.2s ease', fontSize: '13px', width: '100%', textAlign: 'left', color: S.selectedMicrophoneId === null ? '#4f8cff' : 'var(--neko-popup-text)', fontWeight: S.selectedMicrophoneId === null ? '500' : '400' });
            defaultOption.addEventListener('mouseenter', function () { if (S.selectedMicrophoneId !== null) defaultOption.style.background = 'var(--neko-popup-hover)'; });
            defaultOption.addEventListener('mouseleave', function () { if (S.selectedMicrophoneId !== null) defaultOption.style.background = 'transparent'; });
            defaultOption.addEventListener('click', async function () { await selectMicrophone(null); updateMicListSelection(); });
            rightColumn.appendChild(defaultOption);

            var sep3 = document.createElement('div');
            Object.assign(sep3.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '5px 0' });
            rightColumn.appendChild(sep3);

            // 各个设备选项
            audioInputs.forEach(function (device, idx) {
                var option = document.createElement('button');
                option.className = 'mic-option';
                option.dataset.deviceId = device.deviceId;
                option.textContent = device.label || (window.t ? window.t('microphone.deviceLabel', { index: idx + 1 }) : '麦克风 ' + (idx + 1));
                if (S.selectedMicrophoneId === device.deviceId) option.classList.add('selected');
                Object.assign(option.style, { padding: '8px 12px', cursor: 'pointer', border: 'none', background: S.selectedMicrophoneId === device.deviceId ? 'var(--neko-popup-selected-bg)' : 'transparent', borderRadius: '6px', transition: 'background 0.2s ease', fontSize: '13px', width: '100%', textAlign: 'left', color: S.selectedMicrophoneId === device.deviceId ? '#4f8cff' : 'var(--neko-popup-text)', fontWeight: S.selectedMicrophoneId === device.deviceId ? '500' : '400' });
                option.addEventListener('mouseenter', function () { if (S.selectedMicrophoneId !== device.deviceId) option.style.background = 'var(--neko-popup-hover)'; });
                option.addEventListener('mouseleave', function () { if (S.selectedMicrophoneId !== device.deviceId) option.style.background = 'transparent'; });
                option.addEventListener('click', async function () { await selectMicrophone(device.deviceId); updateMicListSelection(); });
                rightColumn.appendChild(option);
            });

            // 组装
            micPopup.appendChild(leftColumn);
            var verticalDivider = document.createElement('div');
            Object.assign(verticalDivider.style, { width: '1px', backgroundColor: 'var(--neko-popup-separator)', alignSelf: 'stretch', margin: '8px 0' });
            micPopup.appendChild(verticalDivider);
            micPopup.appendChild(rightColumn);

            startMicVolumeVisualization();
            return true;
        } catch (error) {
            console.error('渲染麦克风列表失败:', error);
            micPopup.innerHTML = '';
            var errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('microphone.loadFailed') : '获取麦克风列表失败';
            Object.assign(errorItem.style, { padding: '8px 12px', color: '#dc3545', fontSize: '13px' });
            micPopup.appendChild(errorItem);
            return false;
        }
    };

    /** 轻量级更新：仅更新选中状态 */
    function updateMicListSelection() {
        var micPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic') || document.getElementById('mmd-popup-mic');
        if (!micPopup) return;
        var options = micPopup.querySelectorAll('.mic-option');
        options.forEach(function (option) {
            var deviceId = option.dataset.deviceId;
            var isSelected = (deviceId === undefined && S.selectedMicrophoneId === null) ||
                (deviceId === S.selectedMicrophoneId);
            if (isSelected) {
                option.classList.add('selected');
                option.style.background = 'var(--neko-popup-selected-bg)';
                option.style.color = '#4f8cff';
                option.style.fontWeight = '500';
            } else {
                option.classList.remove('selected');
                option.style.background = 'transparent';
                option.style.color = 'var(--neko-popup-text)';
                option.style.fontWeight = '400';
            }
        });
    }

    // 页面加载后预请求麦克风权限
    setTimeout(async function () {
        console.log('[麦克风] 页面加载，预先请求麦克风权限...');
        try {
            await ensureMicrophonePermission();
            console.log('[麦克风] 权限预请求完成，设备列表已缓存');
            window.dispatchEvent(new CustomEvent('mic-permission-ready'));
        } catch (error) {
            console.warn('[麦克风] 预请求权限失败:', error);
        }
    }, 500);

    // 延迟渲染麦克风列表
    setTimeout(function () {
        window.renderFloatingMicList();
    }, 1500);

    mod.ensureMicrophonePermission = ensureMicrophonePermission;
    mod.updateMicListSelection = updateMicListSelection;

    window.appAudioCapture = mod;
})();
