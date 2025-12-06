// ==================== 全局窗口管理函数 ====================
// 关闭所有已打开的设置窗口（弹窗）
window.closeAllSettingsWindows = function() {
    // 关闭 app.js 中跟踪的窗口
    if (window._openSettingsWindows) {
        Object.keys(window._openSettingsWindows).forEach(url => {
            const winRef = window._openSettingsWindows[url];
            try {
                if (winRef && !winRef.closed) {
                    winRef.close();
                }
            } catch (_) {
                // 忽略跨域导致的 close 异常
            }
            delete window._openSettingsWindows[url];
        });
    }
    
    // 关闭 live2d-ui.js 中跟踪的窗口（如果有 Live2DManager 实例）
    if (window.live2dManager && window.live2dManager._openSettingsWindows) {
        Object.keys(window.live2dManager._openSettingsWindows).forEach(url => {
            const winRef = window.live2dManager._openSettingsWindows[url];
            try {
                if (winRef && !winRef.closed) {
                    winRef.close();
                }
            } catch (_) {
                // 忽略跨域导致的 close 异常
            }
            delete window.live2dManager._openSettingsWindows[url];
        });
    }
};

// ==================== 应用初始化 ====================
function init_app(){
    const micButton = document.getElementById('micButton');
    const muteButton = document.getElementById('muteButton');
    const screenButton = document.getElementById('screenButton');
    const stopButton = document.getElementById('stopButton');
    const resetSessionButton = document.getElementById('resetSessionButton');
    const returnSessionButton = document.getElementById('returnSessionButton');
    const statusElement = document.getElementById('status');
    const statusToast = document.getElementById('status-toast');
    
    // Status 气泡框显示函数
    let statusToastTimeout = null;
    function showStatusToast(message, duration = 3000) {
        console.log('[Status Toast] 显示消息:', message, '持续时间:', duration);
        
        if (!message || message.trim() === '') {
            // 如果消息为空，隐藏气泡框
            if (statusToast) {
                statusToast.classList.remove('show');
                statusToast.classList.add('hide');
                setTimeout(() => {
                    statusToast.textContent = '';
                }, 300);
            }
            return;
        }
        
        if (!statusToast) {
            console.error('[Status Toast] statusToast 元素不存在！');
            return;
        }
        
        // 清除之前的定时器
        if (statusToastTimeout) {
            clearTimeout(statusToastTimeout);
            statusToastTimeout = null;
        }
        
        // 更新内容
        statusToast.textContent = message;
        
        // 确保元素可见
        statusToast.style.display = 'block';
        statusToast.style.visibility = 'visible';
        
        // 显示气泡框
        statusToast.classList.remove('hide');
        // 使用 setTimeout 确保样式更新
        setTimeout(() => {
            statusToast.classList.add('show');
            console.log('[Status Toast] 已添加 show 类，元素:', statusToast, '类列表:', statusToast.classList);
        }, 10);
        
        // 自动隐藏
        statusToastTimeout = setTimeout(() => {
            statusToast.classList.remove('show');
            statusToast.classList.add('hide');
            setTimeout(() => {
                statusToast.textContent = '';
            }, 300);
        }, duration);
        
        // 同时更新隐藏的 status 元素（保持兼容性）
        if (statusElement) {
            statusElement.textContent = message || '';
        }
    }
    
    // 将 showStatusToast 暴露到全局作用域，方便调试和测试
    window.showStatusToast = showStatusToast;
    const chatContainer = document.getElementById('chatContainer');
    const textInputBox = document.getElementById('textInputBox');
    const textSendButton = document.getElementById('textSendButton');
    const screenshotButton = document.getElementById('screenshotButton');
    const screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
    const screenshotsList = document.getElementById('screenshots-list');
    const screenshotCount = document.getElementById('screenshot-count');
    const clearAllScreenshots = document.getElementById('clear-all-screenshots');

    let audioContext;
    let workletNode;
    let stream;
    let isRecording = false;
    // 暴露 isRecording 到全局，供其他模块检查
    window.isRecording = false;
    let socket;
    let currentGeminiMessage = null;
    let audioPlayerContext = null;
    let videoTrack, videoSenderInterval;
    let audioBufferQueue = [];
    let screenshotCounter = 0; // 截图计数器
    let isPlaying = false;
    let audioStartTime = 0;
    let scheduledSources = [];
    let animationFrameId;
    let seqCounter = 0;
    let globalAnalyser = null;
    let lipSyncActive = false;
    let screenCaptureStream = null; // 暂存屏幕共享stream，不再需要每次都弹窗选择共享区域，方便自动重连
    // 新增：当前选择的麦克风设备ID
    let selectedMicrophoneId = null;
    
    // 麦克风静音检测相关变量
    let silenceDetectionTimer = null;
    let hasSoundDetected = false;
    let inputAnalyser = null;
    
    // 模式管理
    let isTextSessionActive = false;
    let isSwitchingMode = false; // 新增：模式切换标志
    let sessionStartedResolver = null; // 用于等待 session_started 消息
    
    // 主动搭话功能相关
    let proactiveChatEnabled = false;
    let proactiveChatTimer = null;
    let proactiveChatBackoffLevel = 0; // 退避级别：0=30s, 1=1min, 2=2min, 3=4min, etc.
    const PROACTIVE_CHAT_BASE_DELAY = 30000; // 30秒基础延迟
    
    // Focus模式为true时，AI播放语音时会自动静音麦克风（不允许打断）
    let focusModeEnabled = false;
    
    // 暴露到全局作用域，供 live2d.js 等其他模块访问和修改
    window.proactiveChatEnabled = proactiveChatEnabled;
    window.focusModeEnabled = focusModeEnabled;
    
    // WebSocket心跳保活
    let heartbeatInterval = null;
    const HEARTBEAT_INTERVAL = 30000; // 30秒发送一次心跳
    
    // WebSocket自动重连定时器ID（用于在切换角色时取消之前的重连）
    let autoReconnectTimeoutId = null;

    function isMobile() {
      return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
        navigator.userAgent
      );
    }

    // 建立WebSocket连接
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${protocol}://${window.location.host}/ws/${lanlan_config.lanlan_name}`;
        console.log('[WebSocket] 正在连接，猫娘名称:', lanlan_config.lanlan_name, 'URL:', wsUrl);
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            console.log('WebSocket连接已建立');
            
            // 启动心跳保活机制
            if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
            }
            heartbeatInterval = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'ping'
                    }));
                }
            }, HEARTBEAT_INTERVAL);
            console.log('心跳保活机制已启动');
        };

        socket.onmessage = (event) => {
            if (event.data instanceof Blob) {
                // 处理二进制音频数据
                console.log("收到新的音频块")
                handleAudioBlob(event.data);
                return;
            }

            try {
                const response = JSON.parse(event.data);
                // 调试：记录所有收到的WebSocket消息类型
                if (response.type === 'catgirl_switched') {
                    console.log('[WebSocket] 📨 收到catgirl_switched消息:', response);
                }


                if (response.type === 'gemini_response') {
                    // 检查是否是新消息的开始
                    const isNewMessage = response.isNewMessage || false;
                    appendMessage(response.text, 'gemini', isNewMessage);
                } else if (response.type === 'user_transcript') {
                    // 处理用户语音转录，显示在聊天界面
                    appendMessage(response.text, 'user', true);
                } else if (response.type === 'user_activity') {
                    clearAudioQueue();
                } else if (response.type === 'cozy_audio') {
                    // 处理音频响应
                    console.log("收到新的音频头")
                    const isNewMessage = response.isNewMessage || false;

                    if (isNewMessage) {
                        // 如果是新消息，清空当前音频队列
                        clearAudioQueue();
                    }

                    // 根据数据格式选择处理方法
                    if (response.format === 'base64') {
                        handleBase64Audio(response.audioData, isNewMessage);
                    }
                } else if (response.type === 'screen_share_error') {
                    // 屏幕分享/截图错误，复位按钮状态
                    const translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(translatedMessage, 4000);
                    
                    // 停止屏幕分享
                    stopScreening();
                    
                    // 清理屏幕捕获流
                    if (screenCaptureStream) {
                        screenCaptureStream.getTracks().forEach(track => track.stop());
                        screenCaptureStream = null;
                    }
                    
                    // 复位按钮状态
                    if (isRecording) {
                        // 在语音模式下（屏幕分享）
                        micButton.disabled = true;
                        muteButton.disabled = false;
                        screenButton.disabled = false;
                        stopButton.disabled = true;
                        resetSessionButton.disabled = false;
                    } else if (isTextSessionActive) {
                        // 在文本模式下（截图）
                        screenshotButton.disabled = false;
                    }
                } else if (response.type === 'catgirl_switched') {
                    // 处理猫娘切换通知（从后端WebSocket推送）
                    const newCatgirl = response.new_catgirl;
                    const oldCatgirl = response.old_catgirl;
                    console.log('[WebSocket] ✅ 收到猫娘切换通知，从', oldCatgirl, '切换到', newCatgirl);
                    console.log('[WebSocket] 当前前端猫娘:', lanlan_config.lanlan_name);
                    handleCatgirlSwitch(newCatgirl, oldCatgirl);
                } else if (response.type === 'status') {
                    // 如果正在切换模式且收到"已离开"消息，则忽略
                    if (isSwitchingMode && response.message.includes('已离开')) {
                        console.log('模式切换中，忽略"已离开"状态消息');
                        return;
                    }
                    
                    // 检测严重错误，自动隐藏准备提示（兜底机制）
                    const criticalErrorKeywords = ['连续失败', '已停止', '自动重试', '崩溃', '欠费', 'API Key被'];
                    if (criticalErrorKeywords.some(keyword => response.message.includes(keyword))) {
                        console.log('检测到严重错误，隐藏准备提示');
                        hideVoicePreparingToast();
                    }
                    
                    // 翻译后端发送的状态消息
                    const translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(translatedMessage, 4000);
                    if (response.message === `${lanlan_config.lanlan_name}失联了，即将重启！`){
                        if (isRecording === false && !isTextSessionActive){
                            showStatusToast(window.t ? window.t('app.catgirlResting', {name: lanlan_config.lanlan_name}) : `${lanlan_config.lanlan_name}正在打盹...`, 5000);
                        } else if (isTextSessionActive) {
                            showStatusToast(window.t ? window.t('app.textChatting') : `正在文本聊天中...`, 5000);
                        } else {
                            stopRecording();
                            if (socket.readyState === WebSocket.OPEN) {
                                socket.send(JSON.stringify({
                                    action: 'end_session'
                                }));
                            }
                            hideLive2d();
                            micButton.disabled = true;
                            muteButton.disabled = true;
                            screenButton.disabled = true;
                            stopButton.disabled = true;
                            resetSessionButton.disabled = true;
                            returnSessionButton.disabled = true;

                            setTimeout(async () => {
                                try {
                                    // 创建一个 Promise 来等待 session_started 消息
                                    const sessionStartPromise = new Promise((resolve, reject) => {
                                        sessionStartedResolver = resolve;
                                        
                                        // 设置超时（15秒），如果超时则拒绝
                                        setTimeout(() => {
                                            if (sessionStartedResolver) {
                                                sessionStartedResolver = null;
                                                reject(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                                            }
                                        }, 10000);
                                    });
                                    
                                    // 发送start session事件
                                    socket.send(JSON.stringify({
                                        action: 'start_session',
                                        input_type: 'audio'
                                    }));
                                    
                                    // 等待session真正启动成功
                                    await sessionStartPromise;
                                    
                                    showLive2d();
                                    await startMicCapture();
                                    if (screenCaptureStream != null){
                                        await startScreenSharing();
                                    }
                                    showStatusToast(window.t ? window.t('app.restartComplete', {name: lanlan_config.lanlan_name}) : `重启完成，${lanlan_config.lanlan_name}回来了！`, 4000);
                                } catch (error) {
                                    console.error("重启时出错:", error);
                                    hideVoicePreparingToast(); // 确保重启失败时隐藏准备提示
                                    showStatusToast(window.t ? window.t('app.restartFailed', {error: error.message}) : `重启失败: ${error.message}`, 5000);
                                }
                            }, 7500); // 7.5秒后执行
                        }
                    }
                } else if (response.type === 'expression') {
                    window.LanLan1.registered_expressions[response.message]();
                } else if (response.type === 'system' && response.data === 'turn end') {
                    console.log('收到turn end事件，开始情感分析');
                    // 消息完成时进行情感分析
                    if (currentGeminiMessage) {
                        const fullText = currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
                        setTimeout(async () => {
                            const emotionResult = await analyzeEmotion(fullText);
                            if (emotionResult && emotionResult.emotion) {
                                console.log('消息完成，情感分析结果:', emotionResult);
                                applyEmotion(emotionResult.emotion);
                            }
                        }, 100);
                    }
                    
                    // AI回复完成后，重置主动搭话计时器（如果已开启且在文本模式）
                    if (proactiveChatEnabled && !isRecording) {
                        resetProactiveChatBackoff();
                    }
                } else if (response.type === 'session_preparing') {
                    console.log('收到session_preparing事件，模式:', response.input_mode);
                    // 显示持续性的准备中提示
                    const preparingMessage = response.input_mode === 'text' 
                        ? (window.t ? window.t('app.textSystemPreparing') : '文本系统准备中，请稍候...')
                        : (window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中，请稍候...');
                    showVoicePreparingToast(preparingMessage);
                } else if (response.type === 'session_started') {
                    console.log('收到session_started事件，模式:', response.input_mode);
                    // 隐藏准备中提示
                    hideVoicePreparingToast();
                    // 解析 session_started Promise
                    if (sessionStartedResolver) {
                        // 清除可能存在的超时定时器（通过全局变量）
                        if (window.sessionTimeoutId) {
                            clearTimeout(window.sessionTimeoutId);
                            window.sessionTimeoutId = null;
                        }
                        sessionStartedResolver(response.input_mode);
                        sessionStartedResolver = null;
                    }
                } else if (response.type === 'reload_page') {
                    console.log('收到reload_page事件：', response.message);
                    // 显示提示信息
                    showStatusToast(response.message || (window.t ? window.t('app.configUpdated') : '配置已更新，页面即将刷新'), 3000);
                    
                    // 延迟2.5秒后刷新页面，让后端有足够时间完成session关闭和配置重新加载
                    setTimeout(() => {
                        console.log('开始刷新页面...');
                        window.location.reload();
                    }, 2500);
                } else if (response.type === 'auto_close_mic') {
                    console.log('收到auto_close_mic事件，自动关闭麦克风');
                    // 长时间无语音输入，模拟用户手动关闭语音会话
                    if (isRecording) {
                        // 直接触发闭麦按钮点击，走完整的关闭流程（包括通知后端）
                        muteButton.click();
                        
                        // 显示提示信息
                        showStatusToast(response.message || (window.t ? window.t('app.autoMuteTimeout') : '长时间无语音输入，已自动关闭麦克风'), 4000);
                    }
                }
            } catch (error) {
                console.error('处理消息失败:', error);
            }
        };

        socket.onclose = () => {
            console.log('WebSocket连接已关闭');
            
            // 清理心跳定时器
            if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
                heartbeatInterval = null;
                console.log('心跳保活机制已停止');
            }
            
            // 重置文本session状态，因为后端会清理session
            if (isTextSessionActive) {
                isTextSessionActive = false;
                console.log('WebSocket断开，已重置文本session状态');
            }
            
            // 如果不是正在切换猫娘，才自动重连（避免与手动重连冲突）
            if (!isSwitchingCatgirl) {
                // 保存 setTimeout ID，以便在 handleCatgirlSwitch 中取消
                autoReconnectTimeoutId = setTimeout(connectWebSocket, 3000);
            }
        };

        socket.onerror = (error) => {
            console.error('WebSocket错误:', error);
        };
    }

    // 初始化连接
    connectWebSocket();

    // 添加消息到聊天界面
    function appendMessage(text, sender, isNewMessage = true) {
        function getCurrentTimeString() {
            return new Date().toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        }

        if (sender === 'gemini' && !isNewMessage && currentGeminiMessage) {
            // 追加到现有的Gemini消息
            // currentGeminiMessage.textContent += text;
            currentGeminiMessage.insertAdjacentHTML('beforeend', text.replaceAll('\n', '<br>'));
        } else {
            // 创建新消息
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', sender);
            
            // 根据sender设置不同的图标
            const icon = sender === 'user' ? '💬' : '🎀';
            messageDiv.textContent = "[" + getCurrentTimeString() + "] " + icon + " " + text;
            chatContainer.appendChild(messageDiv);

            // 如果是Gemini消息，更新当前消息引用
            if (sender === 'gemini') {
                currentGeminiMessage = messageDiv;
                
                // 如果是AI第一次回复，更新状态并检查成就
                if (isFirstAIResponse) {
                    isFirstAIResponse = false;
                    console.log('检测到AI第一次回复');
                    checkAndUnlockFirstDialogueAchievement();
                }
            }
        }
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }


        // 全局变量用于缓存麦克风列表和缓存时间戳
    let cachedMicrophones = null;
    let cacheTimestamp = 0;
    const CACHE_DURATION = 30000; // 缓存30秒
    
    // 首次交互跟踪
    let isFirstUserInput = true; // 跟踪是否为用户第一次输入
    let isFirstAIResponse = true; // 跟踪是否为AI第一次回复
    
    // 检查并解锁首次对话成就
    async function checkAndUnlockFirstDialogueAchievement() {
        // 当用户和AI都完成首次交互后调用API
        if (!isFirstUserInput && !isFirstAIResponse) {
            try {
                console.log('首次对话完成，尝试解锁成就');
                const response = await fetch('/api/steam/set-achievement-status/ACH_FIRST_DIALOGUE', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
                
                if (response.ok) {
                    console.log('成就解锁API调用成功');
                } else {
                    console.error('成就解锁API调用失败');
                }
            } catch (error) {
                console.error('成就解锁过程中发生错误:', error);
            }
        }
    }

    // 麦克风选择器UI已移除（旧sidebar系统），保留核心函数供live2d.js浮动按钮系统使用
    
    // 选择麦克风
    async function selectMicrophone(deviceId) {
        selectedMicrophoneId = deviceId;
        
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
                console.error('获取设备名称失败:', error);
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
        if (isRecording) {
            const wasRecording = isRecording;
            // 先显示选择提示
            showStatusToast(window.t ? window.t('app.deviceSelected', {device: deviceName}) : `已选择 ${deviceName}`, 3000);
            // 延迟重启录音，让用户看到选择提示
            await stopMicCapture();
            // 等待一小段时间，确保选择提示显示出来
            await new Promise(resolve => setTimeout(resolve, 500));
            if (wasRecording) {
                await startMicCapture();
            }
        } else {
            // 如果不在录音，直接显示选择提示
            showStatusToast(window.t ? window.t('app.deviceSelected', {device: deviceName}) : `已选择 ${deviceName}`, 3000);
        }
    }
    
    // 保存选择的麦克风到服务器
    async function saveSelectedMicrophone(deviceId) {
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
                console.error('保存麦克风选择失败');
            }
        } catch (err) {
            console.error('保存麦克风选择时发生错误:', err);
        }
    }
    
    // 加载上次选择的麦克风
    async function loadSelectedMicrophone() {
        try {
            const response = await fetch('/api/characters/get_microphone');
            if (response.ok) {
                const data = await response.json();
                selectedMicrophoneId = data.microphone_id || null;
            }
        } catch (err) {
            console.error('加载麦克风选择失败:', err);
            selectedMicrophoneId = null;
        }
    }
    
    // 开麦，按钮on click
    async function startMicCapture() {
        try {
            // 开始录音前添加录音状态类到两个按钮
            micButton.classList.add('recording');
            
            if (!audioPlayerContext) {
                audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
            }

            if (audioPlayerContext.state === 'suspended') {
                await audioPlayerContext.resume();
            }

            // 获取麦克风流，使用选择的麦克风设备ID
            const constraints = {
                audio: selectedMicrophoneId ? { deviceId: { exact: selectedMicrophoneId } } : true
            };
            
            stream = await navigator.mediaDevices.getUserMedia(constraints);

            // 检查音频轨道状态
            const audioTracks = stream.getAudioTracks();
            console.log("音频轨道数量:", audioTracks.length);
            console.log("音频轨道状态:", audioTracks.map(track => ({
                label: track.label,
                enabled: track.enabled,
                muted: track.muted,
                readyState: track.readyState
            })));

            if (audioTracks.length === 0) {
                console.error("没有可用的音频轨道");
                showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);
                // 移除已添加的类
                micButton.classList.remove('recording');
                micButton.classList.remove('active');
                // 抛出错误，让外层 catch 块处理按钮状态恢复
                throw new Error('没有可用的音频轨道');
            }

            await startAudioWorklet(stream);

            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = false;
            stopButton.disabled = true;
            resetSessionButton.disabled = false;
            showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);
            
            // 确保active类存在（已经在点击时添加，这里确保存在）
            if (!micButton.classList.contains('active')) {
                micButton.classList.add('active');
            }
            
            // 开始录音时，停止主动搭话定时器
            stopProactiveChatSchedule();
        } catch (err) {
            console.error('获取麦克风权限失败:', err);
            showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);
            // 失败时移除录音状态类
            micButton.classList.remove('recording');
            // 移除active类
            micButton.classList.remove('active');
            // 抛出错误，让外层 catch 块处理按钮状态恢复
            throw err;
        }
    }

    async function stopMicCapture(){ // 闭麦，按钮on click
        isSwitchingMode = true; // 开始模式切换（从语音切换到待机/文本模式）
        
        // 停止录音时移除录音状态类
        micButton.classList.remove('recording');
        
        // 移除active类
        micButton.classList.remove('active');
        screenButton.classList.remove('active');
        
        // 同步浮动按钮状态
        syncFloatingMicButtonState(false);
        
        stopRecording();
        micButton.disabled = false;
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        resetSessionButton.disabled = false;
        
        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        textInputArea.classList.remove('hidden');
        
        // 停止录音后，重置主动搭话退避级别并开始定时
        if (proactiveChatEnabled) {
            resetProactiveChatBackoff();
        }
        
        // 如果是从语音模式切换回来，显示待机状态
        showStatusToast(window.t ? window.t('app.standby', {name: lanlan_config.lanlan_name}) : `${lanlan_config.lanlan_name}待机中...`, 2000);
        
        // 延迟重置模式切换标志，确保"已离开"消息已经被忽略
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
    }

    async function getMobileCameraStream() {
      const makeConstraints = (facing) => ({
        video: {
          facingMode: facing,
          frameRate: { ideal: 1, max: 1 },
        },
        audio: false,
      });

      const attempts = [
        { label: 'rear', constraints: makeConstraints({ ideal: 'environment' }) },
        { label: 'front', constraints: makeConstraints('user') },
        { label: 'any', constraints: { video: { frameRate: { ideal: 1, max: 1 } }, audio: false } },
      ];

      let lastError;

      for (const attempt of attempts) {
        try {
          console.log(`Trying ${attempt.label} camera @ ${1}fps…`);
          return await navigator.mediaDevices.getUserMedia(attempt.constraints);
        } catch (err) {
          console.warn(`${attempt.label} failed →`, err);
          showStatusToast(err.toString(), 4000);
          return err;
        }
      }
    }

    async function startScreenSharing(){ // 分享屏幕，按钮on click
        // 检查是否在录音状态
        if (!isRecording) {
            showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
            return;
        }
        
        try {
            // 初始化音频播放上下文
            showLive2d();
            if (!audioPlayerContext) {
                audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
            }

            // 如果上下文被暂停，则恢复它
            if (audioPlayerContext.state === 'suspended') {
                await audioPlayerContext.resume();
            }
            let captureStream;

            if (screenCaptureStream == null){
                if (isMobile()) {
                // On mobile we capture the *camera* instead of the screen.
                // `environment` is the rear camera (iOS + many Androids). If that's not
                // available the UA will fall back to any camera it has.
                screenCaptureStream = await getMobileCameraStream();

                } else {
                // Desktop/laptop: capture the user's chosen screen / window / tab.
                screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                    video: {
                    cursor: 'always',
                    frameRate: 1,
                    },
                    audio: false,
                });
                }
            }
            startScreenVideoStreaming(screenCaptureStream, isMobile() ? 'camera' : 'screen');

            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = true;
            stopButton.disabled = false;
            resetSessionButton.disabled = false;
            
            // 添加active类以保持激活状态的颜色
            screenButton.classList.add('active');

            // 当用户停止共享屏幕时
            screenCaptureStream.getVideoTracks()[0].onended = () => {
                stopScreening();
                screenButton.classList.remove('active');
            };

            // 获取麦克风流
            if (!isRecording) showStatusToast(window.t ? window.t('app.micNotOpen') : '没开麦啊喂！', 3000);
          } catch (err) {
            console.error(isMobile() ? '摄像头访问失败:' : '屏幕共享失败:', err);
            console.error('启动失败 →', err);
            let hint = '';
            switch (err.name) {
              case 'NotAllowedError':
                hint = '请检查 iOS 设置 → Safari → 摄像头 权限是否为"允许"';
                break;
              case 'NotFoundError':
                hint = '未检测到摄像头设备';
                break;
              case 'NotReadableError':
              case 'AbortError':
                hint = '摄像头被其它应用占用？关闭扫码/拍照应用后重试';
                break;
            }
            showStatusToast(`${err.name}: ${err.message}${hint ? `\n${hint}` : ''}`, 5000);
          }
    }

    async function stopScreenSharing(){ // 停止共享，按钮on click
        stopScreening();
        micButton.disabled = true;
        muteButton.disabled = false;
        screenButton.disabled = false;
        stopButton.disabled = true;
        resetSessionButton.disabled = false;
        screenCaptureStream = null;
        showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);
        
        // 移除active类
        screenButton.classList.remove('active');
    }

    window.switchMicCapture = async () => {
        if (muteButton.disabled) {
            await startMicCapture();
        } else {
            await stopMicCapture();
        }
    }
    window.switchScreenSharing = async () => {
        if (stopButton.disabled) {
            // 检查是否在录音状态
            if (!isRecording) {
                showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
                return;
            }
            await startScreenSharing();
        } else {
            await stopScreenSharing();
        }
    }

    // 显示语音准备提示框
    function showVoicePreparingToast(message) {
        // 检查是否已存在提示框，避免重复创建
        let toast = document.getElementById('voice-preparing-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'voice-preparing-toast';
            document.body.appendChild(toast);
        }
        
        // 确保样式始终一致（每次更新时都重新设置）
        toast.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background-image: url('/static/icons/reminder_blue.png');
            background-size: 100% 100%;
            background-position: center;
            background-repeat: no-repeat;
            background-color: transparent;
            color: white;
            padding: 20px 32px;
            border-radius: 16px;
            font-size: 16px;
            font-weight: 600;
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 12px;
            animation: voiceToastFadeIn 0.3s ease;
            pointer-events: none;
            width: 320px;
            box-sizing: border-box;
            justify-content: center;
        `;
        
        // 添加动画样式（只添加一次）
        if (!document.querySelector('style[data-voice-toast-animation]')) {
            const style = document.createElement('style');
            style.setAttribute('data-voice-toast-animation', 'true');
            style.textContent = `
                @keyframes voiceToastFadeIn {
                    from {
                        opacity: 0;
                        transform: translate(-50%, -50%) scale(0.8);
                    }
                    to {
                        opacity: 1;
                        transform: translate(-50%, -50%) scale(1);
                    }
                }
                @keyframes voiceToastPulse {
                    0%, 100% {
                        transform: scale(1);
                    }
                    50% {
                        transform: scale(1.1);
                    }
                }
            `;
            document.head.appendChild(style);
        }
        
        // 更新消息内容
        toast.innerHTML = `
            <div style="
                width: 20px;
                height: 20px;
                border: 3px solid rgba(255, 255, 255, 0.3);
                border-top-color: white;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            "></div>
            <span>${message}</span>
        `;
        
        // 添加旋转动画
        const spinStyle = document.createElement('style');
        spinStyle.textContent = `
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
        `;
        if (!document.querySelector('style[data-spin-animation]')) {
            spinStyle.setAttribute('data-spin-animation', 'true');
            document.head.appendChild(spinStyle);
        }
        
        toast.style.display = 'flex';
    }
    
    // 隐藏语音准备提示框
    function hideVoicePreparingToast() {
        const toast = document.getElementById('voice-preparing-toast');
        if (toast) {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }
    }
    
    // 显示"可以说话了"提示
    function showReadyToSpeakToast() {
        let toast = document.getElementById('voice-ready-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'voice-ready-toast';
            document.body.appendChild(toast);
        }
        
        // 确保样式始终一致（和前两个弹窗一样的大小）
        toast.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background-image: url('/static/icons/reminder_midori.png');
            background-size: 100% 100%;
            background-position: center;
            background-repeat: no-repeat;
            background-color: transparent;
            color: white;
            padding: 20px 32px;
            border-radius: 16px;
            font-size: 16px;
            font-weight: 600;
            box-shadow: none;
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 12px;
            animation: voiceToastFadeIn 0.3s ease;
            pointer-events: none;
            width: 320px;
            box-sizing: border-box;
            justify-content: center;
        `;
        
        toast.innerHTML = `
            <img src="/static/icons/ready_to_talk.png" style="width: 36px; height: 36px; object-fit: contain; display: block; flex-shrink: 0;" alt="ready">
            <span style="display: flex; align-items: center;">${window.t ? window.t('app.readyToSpeak') : '可以开始说话了！'}</span>
        `;
        
        // 2秒后自动消失
        setTimeout(() => {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }, 2000);
    }

    // 同步浮动麦克风按钮状态的辅助函数
    function syncFloatingMicButtonState(isActive) {
        if (window.live2dManager && window.live2dManager._floatingButtons && window.live2dManager._floatingButtons.mic) {
            const floatingMicBtn = window.live2dManager._floatingButtons.mic.button;
            if (floatingMicBtn) {
                floatingMicBtn.dataset.active = isActive ? 'true' : 'false';
                const imgOff = window.live2dManager._floatingButtons.mic.imgOff;
                const imgOn = window.live2dManager._floatingButtons.mic.imgOn;
                if (imgOff && imgOn) {
                    imgOff.style.opacity = isActive ? '0' : '1';
                    imgOn.style.opacity = isActive ? '1' : '0';
                }
            }
        }
    }

    // 开始麦克风录音
    micButton.addEventListener('click', async () => {
        // 如果按钮已禁用或正在录音，直接返回
        if (micButton.disabled || isRecording) {
            return;
        }
        
        // 如果已经有 active 类，说明正在处理中，直接返回（防止重复点击）
        if (micButton.classList.contains('active')) {
            return;
        }
        
        // 立即添加激活状态类，保持常亮状态
        micButton.classList.add('active');
        
        // 同步更新浮动按钮状态，防止浮动按钮状态不同步导致图标变灰
        syncFloatingMicButtonState(true);
        
        // 立即禁用按钮，锁定直到连接成功或失败
        micButton.disabled = true;
        
        // 立即显示准备提示
        showVoicePreparingToast(window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中...');
        
        // 如果有活跃的文本会话，先结束它
        if (isTextSessionActive) {
            isSwitchingMode = true; // 开始模式切换
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'end_session'
                }));
            }
            isTextSessionActive = false;
            showStatusToast(window.t ? window.t('app.switchingToVoice') : '正在切换到语音模式...', 3000);
            showVoicePreparingToast(window.t ? window.t('app.switchingToVoice') : '正在切换到语音模式...');
            // 增加等待时间，确保后端完全清理资源
            await new Promise(resolve => setTimeout(resolve, 1500)); // 从500ms增加到1500ms
        }
        
        // 隐藏文本输入区（仅非移动端）
        const textInputArea = document.getElementById('text-input-area');
        if (!isMobile()) {
            textInputArea.classList.add('hidden');
        }
        
        // 禁用所有语音按钮（micButton 已在函数开始处禁用）
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        resetSessionButton.disabled = true;
        returnSessionButton.disabled = true;
        
        showStatusToast(window.t ? window.t('app.initializingVoice') : '正在初始化语音对话...', 3000);
        showVoicePreparingToast(window.t ? window.t('app.connectingToServer') : '正在连接服务器...');
        
        try {
            // 创建一个 Promise 来等待 session_started 消息
            let timeoutId = null; // 在外部作用域定义，以便清除
            const sessionStartPromise = new Promise((resolve, reject) => {
                sessionStartedResolver = resolve;
                
                // 清除之前的超时定时器（如果存在）
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                }
                
                // 设置超时（10秒），如果超时则拒绝
                timeoutId = setTimeout(() => {
                    if (sessionStartedResolver) {
                        const resolver = sessionStartedResolver;
                        sessionStartedResolver = null; // 先清除，防止重复触发
                        window.sessionTimeoutId = null; // 清除全局定时器ID
                        // 更新提示信息，显示超时
                        showVoicePreparingToast(window.t ? window.t('app.sessionTimeout') || '连接超时' : '连接超时，请检查网络连接');
                        reject(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                    } else {
                        window.sessionTimeoutId = null; // 即使 resolver 不存在也清除
                    }
                }, 10000); // 10秒超时
                
                // 保存到全局变量，以便在 session_started 事件中清除
                window.sessionTimeoutId = timeoutId;
            });
            
            // 发送start session事件
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'audio'
                }));
            } else {
                // WebSocket未连接，清除超时定时器和状态
                if (timeoutId) {
                    clearTimeout(timeoutId);
                }
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
                if (sessionStartedResolver) {
                    sessionStartedResolver = null;
                }
                throw new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接');
            }
            
            // 等待session真正启动成功
            try {
                await sessionStartPromise;
                // 成功时清除超时定时器
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
            } catch (error) {
                // 超时或错误时清除超时定时器
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
                throw error; // 重新抛出错误，让外层 catch 处理
            }
            
            showStatusToast(window.t ? window.t('app.initializingMic') : '正在初始化麦克风...', 3000);
            showVoicePreparingToast(window.t ? window.t('app.initializingMic') : '正在初始化麦克风...');
            
            // 显示Live2D
            showLive2d();
            await startMicCapture();
            
            // 录音启动成功后，隐藏准备提示，显示"可以说话了"提示
            hideVoicePreparingToast();
            
            // 延迟1秒显示"可以说话了"提示，确保系统真正准备好
            setTimeout(() => {
                showReadyToSpeakToast();
            }, 1000);
            
            isSwitchingMode = false; // 模式切换完成
        } catch (error) {
            console.error('启动语音会话失败:', error);
            
            // 清除所有超时定时器和状态
            if (window.sessionTimeoutId) {
                clearTimeout(window.sessionTimeoutId);
                window.sessionTimeoutId = null;
            }
            if (sessionStartedResolver) {
                sessionStartedResolver = null;
            }
            
            // 隐藏准备提示
            hideVoicePreparingToast();
            
            // 失败时：移除激活状态（按钮变暗），恢复按钮（允许再次点击）
            micButton.classList.remove('active');
            
            // 同步更新浮动按钮状态，确保浮动按钮也变灰
            syncFloatingMicButtonState(false);
            
            micButton.disabled = false;
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = false;
            textInputArea.classList.remove('hidden');
            showStatusToast(window.t ? window.t('app.startFailed', {error: error.message}) : `启动失败: ${error.message}`, 5000);
            isSwitchingMode = false; // 切换失败，重置标志
            
            // 移除其他按钮的active类
            screenButton.classList.remove('active');
        }
    });

    // 开始屏幕共享
    screenButton.addEventListener('click', startScreenSharing);

    // 停止屏幕共享
    stopButton.addEventListener('click', stopScreenSharing);

    // 停止对话
    muteButton.addEventListener('click', stopMicCapture);

    resetSessionButton.addEventListener('click', () => {
        console.log('[App] resetSessionButton 被点击！当前 isGoodbyeMode 检查');
        isSwitchingMode = true; // 开始重置会话（也是一种模式切换）
        
        // 检查是否是"请她离开"触发的
        const isGoodbyeMode = window.live2dManager && window.live2dManager._goodbyeClicked;
        console.log('[App] 检测 isGoodbyeMode =', isGoodbyeMode, 'goodbyeClicked =', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
        
        // 检查 hideLive2d 前的容器状态
        const live2dContainer = document.getElementById('live2d-container');
        console.log('[App] hideLive2d 前容器状态:', {
            存在: !!live2dContainer,
            当前类: live2dContainer ? live2dContainer.className : 'undefined',
            classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
            display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
        });
        
        hideLive2d()
        
        // 检查 hideLive2d 后的容器状态
        console.log('[App] hideLive2d 后容器状态:', {
            存在: !!live2dContainer,
            当前类: live2dContainer ? live2dContainer.className : 'undefined',
            classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
            display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
        });
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                action: 'end_session'
            }));
        }
        stopRecording();
        clearAudioQueue();
        
        // 重置所有状态
        isTextSessionActive = false;
        
        // 移除所有按钮的active类
        micButton.classList.remove('active');
        screenButton.classList.remove('active');
        
        // 清除所有截图
        screenshotsList.innerHTML = '';
        screenshotThumbnailContainer.classList.remove('show');
        updateScreenshotCount();
        screenshotCounter = 0;
        
        // 根据模式执行不同逻辑
        console.log('[App] 执行分支判断，isGoodbyeMode =', isGoodbyeMode);
        if (!isGoodbyeMode) {
            // 非"请她离开"模式：显示文本输入区并启用按钮
            console.log('[App] 执行普通结束会话逻辑');
            
            // 结束会话后，重置主动搭话计时器（如果已开启）
            if (proactiveChatEnabled) {
                resetProactiveChatBackoff();
            }
            // 显示文本输入区
            const textInputArea = document.getElementById('text-input-area');
            textInputArea.classList.remove('hidden');
            
            // 启用所有输入
            micButton.disabled = false;
            textSendButton.disabled = false;
            textInputBox.disabled = false;
            screenshotButton.disabled = false;
            
            // 禁用语音控制按钮
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = true;  // 禁用"请她回来"按钮
            
            showStatusToast(window.t ? window.t('app.sessionEnded') : '会话已结束', 3000);
        } else {
            // "请她离开"模式：隐藏所有内容
            console.log('[App] 执行"请她离开"模式逻辑');
            
            // 重置 goodbyeClicked 标志（在处理完成后）
            if (window.live2dManager) {
                window.live2dManager._goodbyeClicked = false;
            }
            console.log('[App] 已重置 goodbyeClicked 标志为 false');
            
            // "请她离开"模式：隐藏所有内容
            const textInputArea = document.getElementById('text-input-area');
            textInputArea.classList.add('hidden');
            
            // 禁用所有按钮
            micButton.disabled = true;
            textSendButton.disabled = true;
            textInputBox.disabled = true;
            screenshotButton.disabled = true;
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = false;  // 启用"请她回来"按钮
            
            // "请她离开"时，停止主动搭话定时器
            stopProactiveChatSchedule();
            
            showStatusToast('', 0);
        }
        
        // 延迟重置模式切换标志，确保"已离开"消息已经被忽略
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
    });

    // "请她回来"按钮事件
    returnSessionButton.addEventListener('click', () => {
        isSwitchingMode = true; // 开始模式切换
        
        // 显示Live2D模型
        showLive2d();
        
        // 清除所有语音相关的状态类（确保按钮不会显示为激活状态）
        micButton.classList.remove('recording');
        micButton.classList.remove('active');
        screenButton.classList.remove('active');
        
        // 确保停止录音状态
        isRecording = false;
        window.isRecording = false;
        
        // 同步更新Live2D浮动按钮的状态
        if (window.live2dManager && window.live2dManager._floatingButtons) {
            // 更新麦克风和屏幕分享按钮状态
            ['mic', 'screen'].forEach(buttonId => {
                const buttonData = window.live2dManager._floatingButtons[buttonId];
                if (buttonData && buttonData.button) {
                    buttonData.button.dataset.active = 'false';
                    // 更新图标显示：显示off图标，隐藏on图标
                    if (buttonData.imgOff) {
                        buttonData.imgOff.style.opacity = '1';
                    }
                    if (buttonData.imgOn) {
                        buttonData.imgOn.style.opacity = '0';
                    }
                }
            });
        }
        
        // 启用所有基本输入按钮
        micButton.disabled = false;
        textSendButton.disabled = false;
        textInputBox.disabled = false;
        screenshotButton.disabled = false;
        resetSessionButton.disabled = false;
        
        // 禁用语音控制按钮（文本模式下不需要）
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        
        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        textInputArea.classList.remove('hidden');
        
        // 如果是"请她离开"后返回，需要重新建立会话
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                action: 'start_session',
                input_type: 'text',
                new_session: true  // 开始新会话
            }));
            
            // 标记文本会话为活跃状态
            isTextSessionActive = true;
            
            showStatusToast(window.t ? window.t('app.returning', {name: lanlan_config.lanlan_name}) : `🫴 ${lanlan_config.lanlan_name}回来了！正在重新连接...`, 3000);
            
            // 重置主动搭话定时器（如果已开启）
            if (proactiveChatEnabled) {
                resetProactiveChatBackoff();
            }
        } else {
            showStatusToast(window.t ? window.t('app.websocketNotConnected') : 'WebSocket未连接！', 4000);
        }
        
        // 延迟重置模式切换标志
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
    });
    
    // 文本发送按钮事件
    textSendButton.addEventListener('click', async () => {
        const text = textInputBox.value.trim();
        const hasScreenshots = screenshotsList.children.length > 0;
        
        // 如果既没有文本也没有截图，静默返回
        if (!text && !hasScreenshots) {
            return;
        }
        
        // 如果还没有启动session，先启动
        if (!isTextSessionActive) {
            // 临时禁用文本输入
            textSendButton.disabled = true;
            textInputBox.disabled = true;
            screenshotButton.disabled = true;
            resetSessionButton.disabled = false;
            
            showStatusToast(window.t ? window.t('app.initializingText') : '正在初始化文本对话...', 3000);
            
            try {
                // 创建一个 Promise 来等待 session_started 消息
                const sessionStartPromise = new Promise((resolve, reject) => {
                    sessionStartedResolver = resolve;
                    
                    // 设置超时（15秒），如果超时则拒绝
                    setTimeout(() => {
                        if (sessionStartedResolver) {
                            sessionStartedResolver = null;
                            reject(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                        }
                    }, 15000);
                });
                
                // 启动文本session
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'start_session',
                        input_type: 'text',
                        new_session: false
                    }));
                } else {
                    throw new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接');
                }
                
                // 等待session真正启动成功
                await sessionStartPromise;
                
                isTextSessionActive = true;
                showLive2d();
                
                // 重新启用文本输入
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;
                
                showStatusToast(window.t ? window.t('app.textChattingShort') : '正在文本聊天中', 2000);
            } catch (error) {
                console.error('启动文本session失败:', error);
                hideVoicePreparingToast(); // 确保失败时隐藏准备提示
                showStatusToast(window.t ? window.t('app.startFailed', {error: error.message}) : `启动失败: ${error.message}`, 5000);
                
                // 重新启用按钮，允许用户重试
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;
                
                return; // 启动失败，不继续发送消息
            }
        }
        
        // 发送消息
        if (socket.readyState === WebSocket.OPEN) {
            // 先发送所有截图
            if (hasScreenshots) {
                const screenshotItems = Array.from(screenshotsList.children);
                for (const item of screenshotItems) {
                    const img = item.querySelector('.screenshot-thumbnail');
                    if (img && img.src) {
                        socket.send(JSON.stringify({
                            action: 'stream_data',
                            data: img.src,
                            input_type: isMobile() ? 'camera' : 'screen'
                        }));
                    }
                }
                
                // 在聊天界面显示截图提示
                const screenshotCount = screenshotItems.length;
                appendMessage(`📸 [已发送${screenshotCount}张截图]`, 'user', true);
                
                // 清空截图列表
                screenshotsList.innerHTML = '';
                screenshotThumbnailContainer.classList.remove('show');
                updateScreenshotCount();
            }
            
            // 再发送文本（如果有）
            if (text) {
                socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: text,
                    input_type: 'text'
                }));
                
                // 清空输入框
                textInputBox.value = '';
                
                // 在聊天界面显示用户消息
                appendMessage(text, 'user', true);
                
                // 如果是用户第一次输入，更新状态并检查成就
                if (isFirstUserInput) {
                    isFirstUserInput = false;
                    console.log('检测到用户第一次输入');
                    checkAndUnlockFirstDialogueAchievement();
                }
            }
            
            // 文本聊天后，重置主动搭话计时器（如果已开启）
            if (proactiveChatEnabled) {
                resetProactiveChatBackoff();
            }
            
            showStatusToast(window.t ? window.t('app.textChattingShort') : '正在文本聊天中', 2000);
        } else {
            showStatusToast(window.t ? window.t('app.websocketNotConnected') : 'WebSocket未连接！', 4000);
        }
    });
    
    // 支持Enter键发送（Shift+Enter换行）
    textInputBox.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            textSendButton.click();
        }
    });
    
    // 截图按钮事件
    screenshotButton.addEventListener('click', async () => {
        try {
            // 临时禁用截图按钮，防止重复点击
            screenshotButton.disabled = true;
            showStatusToast(window.t ? window.t('app.capturing') : '正在截图...', 2000);
            
            let captureStream;
            
            // 获取屏幕或摄像头流
            if (isMobile()) {
                // 移动端使用摄像头
                captureStream = await getMobileCameraStream();
            } else {
                // 桌面端使用屏幕共享
                captureStream = await navigator.mediaDevices.getDisplayMedia({
                    video: {
                        cursor: 'always',
                    },
                    audio: false,
                });
            }
            
            // 创建video元素来加载流
            const video = document.createElement('video');
            video.srcObject = captureStream;
            video.autoplay = true;
            video.muted = true;
            
            // 等待视频加载完成
            await video.play();
            
            // 创建canvas来捕获帧
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext('2d');
            
            // 捕获当前帧
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.8); // base64 jpeg
            
            // 停止捕获流
            captureStream.getTracks().forEach(track => track.stop());
            
            // 添加截图到待发送列表（不立即发送）
            addScreenshotToList(dataUrl);
            
            showStatusToast(window.t ? window.t('app.screenshotAdded') : '截图已添加，点击发送一起发送', 3000);
            
            // 重新启用截图按钮
            screenshotButton.disabled = false;
            
        } catch (err) {
            console.error('截图失败:', err);
            
            // 根据错误类型显示不同提示
            let errorMsg = window.t ? window.t('app.screenshotFailed') : '截图失败';
            if (err.name === 'NotAllowedError') {
                errorMsg = window.t ? window.t('app.screenshotCancelled') : '用户取消了截图';
            } else if (err.name === 'NotFoundError') {
                errorMsg = window.t ? window.t('app.deviceNotFound') : '未找到可用的媒体设备';
            } else if (err.name === 'NotReadableError') {
                errorMsg = window.t ? window.t('app.deviceNotAccessible') : '无法访问媒体设备';
            } else if (err.message) {
                errorMsg = window.t ? window.t('app.screenshotFailed') + ': ' + err.message : `截图失败: ${err.message}`;
            }
            
            showStatusToast(errorMsg, 5000);
            
            // 重新启用截图按钮
            screenshotButton.disabled = false;
        }
    });
    
    // 添加截图到列表
    function addScreenshotToList(dataUrl) {
        screenshotCounter++;
        
        // 创建截图项容器
        const item = document.createElement('div');
        item.className = 'screenshot-item';
        item.dataset.index = screenshotCounter;
        
        // 创建缩略图
        const img = document.createElement('img');
        img.className = 'screenshot-thumbnail';
        img.src = dataUrl;
        img.alt = window.t ? window.t('chat.screenshotAlt', {index: screenshotCounter}) : `截图 ${screenshotCounter}`;
        img.title = window.t ? window.t('chat.screenshotTitle', {index: screenshotCounter}) : `点击查看截图 ${screenshotCounter}`;
        
        // 点击缩略图可以在新标签页查看大图
        img.addEventListener('click', () => {
            window.open(dataUrl, '_blank');
        });
        
        // 创建删除按钮
        const removeBtn = document.createElement('button');
        removeBtn.className = 'screenshot-remove';
        removeBtn.innerHTML = '×';
        removeBtn.title = window.t ? window.t('chat.removeScreenshot') : '移除此截图';
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeScreenshotFromList(item);
        });
        
        // 创建索引标签
        const indexLabel = document.createElement('span');
        indexLabel.className = 'screenshot-index';
        indexLabel.textContent = `#${screenshotCounter}`;
        
        // 组装元素
        item.appendChild(img);
        item.appendChild(removeBtn);
        item.appendChild(indexLabel);
        
        // 添加到列表
        screenshotsList.appendChild(item);
        
        // 更新计数和显示容器
        updateScreenshotCount();
        screenshotThumbnailContainer.classList.add('show');
        
        // 自动滚动到最新的截图
        setTimeout(() => {
            screenshotsList.scrollLeft = screenshotsList.scrollWidth;
        }, 100);
    }
    
    // 从列表中移除截图
    function removeScreenshotFromList(item) {
        item.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => {
            item.remove();
            updateScreenshotCount();
            
            // 如果没有截图了，隐藏容器
            if (screenshotsList.children.length === 0) {
                screenshotThumbnailContainer.classList.remove('show');
            }
        }, 300);
    }
    
    // 更新截图计数
    function updateScreenshotCount() {
        const count = screenshotsList.children.length;
        screenshotCount.textContent = count;
    }
    
    // 清空所有截图
    clearAllScreenshots.addEventListener('click', async () => {
        if (screenshotsList.children.length === 0) return;
        
        if (await showConfirm(
            window.t ? window.t('dialogs.clearScreenshotsConfirm') : '确定要清空所有待发送的截图吗？',
            window.t ? window.t('dialogs.clearScreenshots') : '清空截图',
            {danger: true}
        )) {
            screenshotsList.innerHTML = '';
            screenshotThumbnailContainer.classList.remove('show');
            updateScreenshotCount();
        }
    });

    // 情感分析功能
    async function analyzeEmotion(text) {
        console.log('analyzeEmotion被调用，文本:', text);
        try {
            const response = await fetch('/api/emotion/analysis', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: text,
                    lanlan_name: lanlan_config.lanlan_name
                })
            });

            if (!response.ok) {
                console.warn('情感分析请求失败:', response.status);
                return null;
            }

            const result = await response.json();
            console.log('情感分析API返回结果:', result);
            
            if (result.error) {
                console.warn('情感分析错误:', result.error);
                return null;
            }

            return result;
        } catch (error) {
            console.error('情感分析请求异常:', error);
            return null;
        }
    }

    // 应用情感到Live2D模型
    function applyEmotion(emotion) {
        if (window.LanLan1 && window.LanLan1.setEmotion) {
            console.log('调用window.LanLan1.setEmotion:', emotion);
            window.LanLan1.setEmotion(emotion);
        } else {
            console.warn('情感功能未初始化');
        }
    }

    // 启动麦克风静音检测
    function startSilenceDetection() {
        // 重置检测状态
        hasSoundDetected = false;
        
        // 清除之前的定时器(如果有)
        if (silenceDetectionTimer) {
            clearTimeout(silenceDetectionTimer);
        }
        
        // 启动5秒定时器
        silenceDetectionTimer = setTimeout(() => {
            if (!hasSoundDetected && isRecording) {
                showStatusToast(window.t ? window.t('app.micNoSound') : '⚠️ 麦克风无声音，请检查麦克风设置', 5000);
                console.warn('麦克风静音检测：5秒内未检测到声音');
            }
        }, 5000);
    }
    
    // 停止麦克风静音检测
    function stopSilenceDetection() {
        if (silenceDetectionTimer) {
            clearTimeout(silenceDetectionTimer);
            silenceDetectionTimer = null;
        }
        hasSoundDetected = false;
    }
    
    // 监测音频输入音量
    function monitorInputVolume() {
        if (!inputAnalyser || !isRecording) {
            return;
        }
        
        const dataArray = new Uint8Array(inputAnalyser.fftSize);
        inputAnalyser.getByteTimeDomainData(dataArray);
        
        // 计算音量(RMS)
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const val = (dataArray[i] - 128) / 128.0;
            sum += val * val;
        }
        const rms = Math.sqrt(sum / dataArray.length);
        
        // 如果音量超过阈值(0.01),认为检测到声音
        if (rms > 0.01) {
            if (!hasSoundDetected) {
                hasSoundDetected = true;
                console.log('麦克风静音检测：检测到声音，RMS =', rms);
                
                // 如果之前显示了无声音警告，现在检测到声音了，恢复正常状态显示
                // 检查隐藏的 status 元素是否包含无声音警告（保持兼容性）
                const noSoundText = window.t ? window.t('voiceControl.noSound') : '麦克风无声音';
                if (statusElement && statusElement.textContent.includes(noSoundText)) {
                    showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);
                    console.log('麦克风静音检测：检测到声音，已清除警告');
                }
            }
        }
        
        // 持续监测
        if (isRecording) {
            requestAnimationFrame(monitorInputVolume);
        }
    }

    // 使用AudioWorklet开始音频处理
    async function startAudioWorklet(stream) {
        isRecording = true;
        window.isRecording = true;

        // 先清理旧的音频上下文，防止多个 worklet 同时发送数据导致 QPS 超限
        if (audioContext) {
            // 只有在未关闭状态下才尝试关闭
            if (audioContext.state !== 'closed') {
                try {
                    await audioContext.close();
                } catch (e) {
                    console.warn('关闭旧音频上下文时出错:', e);
                    // 强制复位所有状态，防止状态不一致
                    isRecording = false;
                    window.isRecording = false;
                    micButton.classList.remove('recording', 'active');
                    syncFloatingMicButtonState(false);
                    micButton.disabled = false;
                    muteButton.disabled = true;
                    screenButton.disabled = true;
                    stopButton.disabled = true;
                    showStatusToast(window.t ? window.t('app.audioContextError') : '音频系统异常，请重试', 3000);
                    throw e; // 重新抛出错误，阻止后续执行
                }
            }
            audioContext = null;
            workletNode = null;
        }

        // 创建音频上下文
        audioContext = new AudioContext();
        console.log("音频上下文采样率:", audioContext.sampleRate);

        // 创建媒体流源
        const source = audioContext.createMediaStreamSource(stream);
        
        // 创建analyser节点用于监测输入音量
        inputAnalyser = audioContext.createAnalyser();
        inputAnalyser.fftSize = 2048;
        inputAnalyser.smoothingTimeConstant = 0.8;
        
        // 连接source到analyser(用于音量检测)
        source.connect(inputAnalyser);

        try {
            // 加载AudioWorklet处理器
            await audioContext.audioWorklet.addModule('/static/audio-processor.js');

            // 创建AudioWorkletNode
            workletNode = new AudioWorkletNode(audioContext, 'audio-processor', {
                processorOptions: {
                    originalSampleRate: audioContext.sampleRate,
                    targetSampleRate: 16000
                }
            });

            // 监听处理器发送的消息
            workletNode.port.onmessage = (event) => {
                const audioData = event.data;

                // Focus模式：focusModeEnabled为true且AI正在播放语音时，自动静音麦克风（不回传麦克风音频）
                if (focusModeEnabled === true && isPlaying === true) {
                    // 处于focus模式且AI语音播放中，跳过回传麦克风音频，实现自动静音
                    return;
                }

                if (isRecording && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: Array.from(audioData),
                        input_type: 'audio'
                    }));
                }
            };

            // 连接节点
            source.connect(workletNode);
            // 不需要连接到destination，因为我们不需要听到声音
            // workletNode.connect(audioContext.destination);
            
            // 启动静音检测
            startSilenceDetection();
            monitorInputVolume();

        } catch (err) {
            console.error('加载AudioWorklet失败:', err);
            console.dir(err); // <--- 使用 console.dir()
            showStatusToast(window.t ? window.t('app.audioWorkletFailed') : 'AudioWorklet加载失败', 5000);
            stopSilenceDetection();
        }
    }


    // 停止录屏
    function stopScreening() {
        if (videoSenderInterval) clearInterval(videoSenderInterval);
    }

    // 停止录音
    function stopRecording() {

        stopScreening();
        if (!isRecording) return;

        isRecording = false;
        window.isRecording = false;
        currentGeminiMessage = null;
        
        // 停止静音检测
        stopSilenceDetection();
        
        // 清理输入analyser
        inputAnalyser = null;

        // 停止所有轨道
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
        }

        // 关闭AudioContext
        if (audioContext) {
            // 只有在未关闭状态下才关闭，防止重复关闭导致错误
            if (audioContext.state !== 'closed') {
                audioContext.close();
            }
            audioContext = null;
            workletNode = null;
        }

        // 通知服务器暂停会话
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                action: 'pause_session'
            }));
        }
        // statusElement.textContent = '录制已停止';
    }

    // 清空音频队列并停止所有播放
    function clearAudioQueue() {
        // 停止所有计划的音频源
        scheduledSources.forEach(source => {
            try {
                source.stop();
            } catch (e) {
                // 忽略已经停止的源
            }
        });

        // 清空队列和计划源列表
        scheduledSources = [];
        audioBufferQueue = [];
        isPlaying = false;
        audioStartTime = 0;
        nextStartTime = 0; // 新增：重置预调度时间
    }


    function scheduleAudioChunks() {
        const scheduleAheadTime = 5;

        initializeGlobalAnalyser();

        // 关键：预调度所有在lookahead时间内的chunk
        while (nextChunkTime < audioPlayerContext.currentTime + scheduleAheadTime) {
            if (audioBufferQueue.length > 0) {
                const { buffer: nextBuffer } = audioBufferQueue.shift();
                console.log('ctx', audioPlayerContext.sampleRate,
                    'buf', nextBuffer.sampleRate);

                const source = audioPlayerContext.createBufferSource();
                source.buffer = nextBuffer;
                // source.connect(audioPlayerContext.destination);


                // 创建analyser节点用于lipSync
                // const analyser = audioPlayerContext.createAnalyser();
                // analyser.fftSize = 2048;
                // source.connect(analyser);
                // analyser.connect(audioPlayerContext.destination);
                // if (window.LanLan1 && window.LanLan1.live2dModel) {
                //     startLipSync(window.LanLan1.live2dModel, analyser);
                // }


                source.connect(globalAnalyser);

                if (!lipSyncActive && window.LanLan1 && window.LanLan1.live2dModel) {
                    startLipSync(window.LanLan1.live2dModel, globalAnalyser);
                    lipSyncActive = true;
                }

                // 精确时间调度
                source.start(nextChunkTime);
                // console.log(`调度chunk在时间: ${nextChunkTime.toFixed(3)}`);

                // 设置结束回调处理lipSync停止
                source.onended = () => {
                    // if (window.LanLan1 && window.LanLan1.live2dModel) {
                    //     stopLipSync(window.LanLan1.live2dModel);
                    // }
                    const index = scheduledSources.indexOf(source);
                    if (index !== -1) {
                        scheduledSources.splice(index, 1);
                    }

                    if (scheduledSources.length === 0 && audioBufferQueue.length === 0) {
                        if (window.LanLan1 && window.LanLan1.live2dModel) {
                            stopLipSync(window.LanLan1.live2dModel);
                        }
                        lipSyncActive = false;
                        isPlaying = false; // 新增：所有音频播放完毕，重置isPlaying
                    }
                };

                // // 更新下一个chunk的时间
                nextChunkTime += nextBuffer.duration;

                scheduledSources.push(source);
            } else {
                break;
            }
        }

        // 继续调度循环
        setTimeout(scheduleAudioChunks, 25); // 25ms间隔检查
    }


    async function handleAudioBlob(blob) {
        // 你现有的PCM处理代码...
        const pcmBytes = await blob.arrayBuffer();
        if (!pcmBytes || pcmBytes.byteLength === 0) {
            console.warn('收到空的PCM数据，跳过处理');
            return;
        }

        if (!audioPlayerContext) {
            audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
        }

        if (audioPlayerContext.state === 'suspended') {
            await audioPlayerContext.resume();
        }

        const int16Array = new Int16Array(pcmBytes);
        const audioBuffer = audioPlayerContext.createBuffer(1, int16Array.length, 48000);
        const channelData = audioBuffer.getChannelData(0);
        for (let i = 0; i < int16Array.length; i++) {
            channelData[i] = int16Array[i] / 32768.0;
        }

        const bufferObj = { seq: seqCounter++, buffer: audioBuffer };
        audioBufferQueue.push(bufferObj);

        let i = audioBufferQueue.length - 1;
        while (i > 0 && audioBufferQueue[i].seq < audioBufferQueue[i - 1].seq) {
            [audioBufferQueue[i], audioBufferQueue[i - 1]] =
              [audioBufferQueue[i - 1], audioBufferQueue[i]];
            i--;
        }

        // 如果是第一次，初始化调度
        if (!isPlaying) {
            nextChunkTime = audioPlayerContext.currentTime + 0.1;
            isPlaying = true;
            scheduleAudioChunks(); // 开始调度循环
        }
    }

    function startScreenVideoStreaming(stream, input_type) {
        const video = document.createElement('video');
        // console.log('Ready for sharing 1')

        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;
        // console.log('Ready for sharing 2')

        videoTrack = stream.getVideoTracks()[0];
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // 定时抓取当前帧并编码为jpeg
        video.play().then(() => {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            videoSenderInterval = setInterval(() => {
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                const dataUrl = canvas.toDataURL('image/jpeg', 0.8); // base64 jpeg

                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: dataUrl,
                        input_type: input_type,
                    }));
                }
            }, 1000); } // 每100ms一帧
        )
    }

    function initializeGlobalAnalyser() {
        if (!globalAnalyser && audioPlayerContext) {
            globalAnalyser = audioPlayerContext.createAnalyser();
            globalAnalyser.fftSize = 2048;
            globalAnalyser.connect(audioPlayerContext.destination);
        }
    }

    function startLipSync(model, analyser) {
        const dataArray = new Uint8Array(analyser.fftSize);

        function animate() {
            analyser.getByteTimeDomainData(dataArray);
            // 简单求音量（RMS 或最大振幅）
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) {
                const val = (dataArray[i] - 128) / 128; // 归一化到 -1~1
                sum += val * val;
            }
            const rms = Math.sqrt(sum / dataArray.length);
            // 这里可以调整映射关系
            const mouthOpen = Math.min(1, rms * 8); // 放大到 0~1
            // 通过统一通道设置嘴巴开合，屏蔽 motion 对嘴巴的控制
            if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                window.LanLan1.setMouth(mouthOpen);
            }

            animationFrameId = requestAnimationFrame(animate);
        }

        animate();
    }

    function stopLipSync(model) {
        cancelAnimationFrame(animationFrameId);
        if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
            window.LanLan1.setMouth(0);
        } else if (model && model.internalModel && model.internalModel.coreModel) {
            // 兜底
            try { model.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", 0); } catch (_) {}
        }
    }

    // 隐藏live2d函数
    function hideLive2d() {
        console.log('[App] hideLive2d函数被调用');
        const container = document.getElementById('live2d-container');
        console.log('[App] hideLive2d调用前，容器类列表:', container.classList.toString());
        
        // 首先清除任何可能干扰动画的强制显示样式
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('opacity');
        
        // 添加minimized类，触发CSS过渡动画
        container.classList.add('minimized');
        console.log('[App] hideLive2d调用后，容器类列表:', container.classList.toString());
        
        // 添加一个延迟检查，确保类被正确添加
        setTimeout(() => {
            console.log('[App] 延迟检查容器类列表:', container.classList.toString());
        }, 100);
    }

    // 显示live2d函数
    function showLive2d() {
        console.log('[App] showLive2d函数被调用');
        
        // 检查是否处于"请她离开"状态，如果是则直接返回，不执行显示逻辑
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[App] showLive2d: 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }
        
        const container = document.getElementById('live2d-container');
        console.log('[App] showLive2d调用前，容器类列表:', container.classList.toString());
        
        // 确保浮动按钮显示（使用 !important 强制显示，覆盖所有其他逻辑）
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            // 直接设置 !important 样式，不先清除（避免被鼠标跟踪逻辑覆盖）
            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
        }
        
        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.style.removeProperty('display');
            lockIcon.style.removeProperty('visibility');
            lockIcon.style.removeProperty('opacity');
        }
        
        // 原生按钮和status栏应该永不出现，保持隐藏状态
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');
        
        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }
        
        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }
        
        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });
        
        const statusElement = document.getElementById('status');
        if (statusElement) {
            statusElement.style.setProperty('display', 'none', 'important');
            statusElement.style.setProperty('visibility', 'hidden', 'important');
            statusElement.style.setProperty('opacity', '0', 'important');
        }

        // 强制显示live2d容器
        container.style.visibility = 'visible';
        container.style.display = 'block';
        container.style.opacity = '1';

        // 强制浏览器重新计算样式，确保过渡效果正常
        void container.offsetWidth;

        // 移除minimized类，触发过渡动画
        container.classList.remove('minimized');
        
        // 如果容器没有其他类，完全移除class属性以避免显示为class=""
        if (container.classList.length === 0) {
            container.removeAttribute('class');
        }
        
        console.log('[App] showLive2d调用后，容器类列表:', container.classList.toString());
    }
    window.startScreenSharing = startScreenSharing;
    window.stopScreenSharing  = stopScreenSharing;
    window.screen_share       = startScreenSharing;
    
    // ========== 连接浮动按钮到原有功能 ==========
    
    // 麦克风按钮（toggle模式）
    window.addEventListener('live2d-mic-toggle', async (e) => {
        if (e.detail.active) {
            // 想要开启语音：如果 micButton 已经有 active 类，说明正在处理中或已激活，直接返回
            if (micButton.classList.contains('active')) {
                return;
            }
            // 开始语音
            micButton.click(); // 触发原有的麦克风按钮点击
        } else {
            // 想要关闭语音：允许执行，即使 micButton 有 active 类（因为这是正常的退出操作）
            muteButton.click(); // 触发原有的停止按钮点击
        }
    });
    
    // 屏幕分享按钮（toggle模式）
    window.addEventListener('live2d-screen-toggle', async (e) => {
        if (e.detail.active) {
            // 开启屏幕分享
            screenButton.click();
        } else {
            // 关闭屏幕分享
            stopButton.click();
        }
    });
    
    // Agent工具按钮（只展开弹出框，不执行操作）
    window.addEventListener('live2d-agent-click', () => {
        // 不执行任何操作，只是展开弹出框
        console.log('Agent工具按钮被点击，显示弹出框');
    });
    
    // 设置按钮 - 填充弹出框内容
    let settingsPopupInitialized = false;
    window.addEventListener('live2d-settings-click', () => {
        console.log('设置按钮被点击');
        
        // 每次打开设置弹出框时，同步 window 中的最新值到局部变量
        if (typeof window.focusModeEnabled !== 'undefined') {
            focusModeEnabled = window.focusModeEnabled;
        }
        if (typeof window.proactiveChatEnabled !== 'undefined') {
            proactiveChatEnabled = window.proactiveChatEnabled;
        }
        
        // 如果已经初始化过，更新开关状态
        if (settingsPopupInitialized) {
            const proactiveChatToggle = document.getElementById('proactive-chat-toggle-l2d');
            const focusModeToggle = document.getElementById('focus-mode-toggle-l2d');
            if (proactiveChatToggle) {
                proactiveChatToggle.checked = proactiveChatEnabled;
            }
            if (focusModeToggle) {
                focusModeToggle.checked = focusModeEnabled;
            }
            return; // 已初始化，直接返回
        }
        
        // 仅第一次点击时填充内容
        if (!settingsPopupInitialized) {
            const popup = document.getElementById('live2d-popup-settings');
            if (popup) {
                // 清空现有内容
                popup.innerHTML = '';
                
                // 创建设置项容器
                const container = document.createElement('div');
                container.style.cssText = 'min-width: 200px; max-width: 300px;';
                
                // 主动搭话开关
                const proactiveChatDiv = document.createElement('div');
                proactiveChatDiv.style.cssText = 'padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(0,0,0,0.1);';
                const proactiveChatSpan = document.createElement('span');
                proactiveChatSpan.style.fontSize = '14px';
                proactiveChatSpan.textContent = window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话';
                proactiveChatSpan.setAttribute('data-i18n', 'settings.toggles.proactiveChat');
                proactiveChatDiv.appendChild(proactiveChatSpan);
                const proactiveChatCheckbox = document.createElement('input');
                proactiveChatCheckbox.type = 'checkbox';
                proactiveChatCheckbox.id = 'proactive-chat-toggle-l2d';
                proactiveChatCheckbox.style.cssText = 'cursor: pointer; width: 18px; height: 18px;';
                proactiveChatDiv.appendChild(proactiveChatCheckbox);
                container.appendChild(proactiveChatDiv);
                
                // Focus模式开关
                const focusModeDiv = document.createElement('div');
                focusModeDiv.style.cssText = 'padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(0,0,0,0.1);';
                const focusModeSpan = document.createElement('span');
                focusModeSpan.style.fontSize = '14px';
                focusModeSpan.textContent = window.t ? window.t('settings.toggles.allowInterrupt') : '🎯 允许打断';
                focusModeSpan.setAttribute('data-i18n', 'settings.toggles.allowInterrupt');
                focusModeDiv.appendChild(focusModeSpan);
                const focusModeCheckbox = document.createElement('input');
                focusModeCheckbox.type = 'checkbox';
                focusModeCheckbox.id = 'focus-mode-toggle-l2d';
                focusModeCheckbox.style.cssText = 'cursor: pointer; width: 18px; height: 18px;';
                focusModeDiv.appendChild(focusModeCheckbox);
                container.appendChild(focusModeDiv);
                
                // 页面链接
                const links = [
                    { href: `/memory_browser`, text: '📝 记忆管理' },
                    { href: `/chara_manager`, text: '👤 角色设置' },
                    { href: `/l2d?lanlan_name=${lanlan_config.lanlan_name}`, text: '🎨 Live2D管理' },
                    { href: `/api_key`, textKey: 'settings.menu.apiSettings', text: window.t ? window.t('settings.menu.apiSettings') : '🔑 API设置' },
                    { href: `/steam_workshop_manager`, textKey: 'steam.workshop', text: window.t ? window.t('steam.workshop') : 'steam创意工坊' }
                ];
                
                // 已打开的设置窗口引用映射（URL -> Window对象）
                if (!window._openSettingsWindows) {
                    window._openSettingsWindows = {};
                }
                
                links.forEach(link => {
                    const linkDiv = document.createElement('div');
                    linkDiv.style.cssText = 'display: block; padding: 10px 12px; text-decoration: none; color: #333; font-size: 14px; border-bottom: 1px solid rgba(0,0,0,0.05); transition: background 0.2s; cursor: pointer;';
                    linkDiv.textContent = link.text;
                    if (link.textKey) {
                        linkDiv.setAttribute('data-i18n', link.textKey);
                    }
                    linkDiv.onmouseenter = () => linkDiv.style.background = 'rgba(79, 140, 255, 0.1)';
                    linkDiv.onmouseleave = () => linkDiv.style.background = 'transparent';
                    linkDiv.onclick = (e) => {
                        e.preventDefault();
                        const url = link.href;
                        
                        // 检查是否已有该URL的窗口打开
                        if (window._openSettingsWindows[url]) {
                            const existingWindow = window._openSettingsWindows[url];
                            // 检查窗口是否仍然打开
                            if (existingWindow && !existingWindow.closed) {
                                // 聚焦到已存在的窗口
                                existingWindow.focus();
                                return;
                            } else {
                                // 窗口已关闭，清除引用
                                delete window._openSettingsWindows[url];
                            }
                        }
                        
                        // 打开新窗口并保存引用
                        const newWindow = window.open(url, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                        if (newWindow) {
                            window._openSettingsWindows[url] = newWindow;
                            
                            // 监听窗口关闭事件，清除引用
                            const checkClosed = setInterval(() => {
                                if (newWindow.closed) {
                                    delete window._openSettingsWindows[url];
                                    clearInterval(checkClosed);
                                }
                            }, 500);
                        }
                    };
                    container.appendChild(linkDiv);
                });
                
                popup.appendChild(container);
                
                // 设置初始状态
                const proactiveChatToggle = document.getElementById('proactive-chat-toggle-l2d');
                const focusModeToggle = document.getElementById('focus-mode-toggle-l2d');
                
                // 从 window 同步最新值到局部变量（防止从 l2d 页面返回时值丢失）
                if (typeof window.proactiveChatEnabled !== 'undefined') {
                    proactiveChatEnabled = window.proactiveChatEnabled;
                }
                if (typeof window.focusModeEnabled !== 'undefined') {
                    focusModeEnabled = window.focusModeEnabled;
                }
                
                if (proactiveChatToggle) {
                    proactiveChatToggle.checked = proactiveChatEnabled;
                    proactiveChatToggle.addEventListener('change', (event) => {
                        event.stopPropagation();
                        proactiveChatEnabled = event.target.checked;
                        window.proactiveChatEnabled = proactiveChatEnabled; // 同步到全局
                        saveSettings();
                        
                        console.log(`主动搭话已${proactiveChatEnabled ? '开启' : '关闭'}`);
                        
                        if (proactiveChatEnabled) {
                            resetProactiveChatBackoff();
                        } else {
                            stopProactiveChatSchedule();
                        }
                    });
                }
                
                if (focusModeToggle) {
                    focusModeToggle.checked = focusModeEnabled;
                    focusModeToggle.addEventListener('change', (event) => {
                        event.stopPropagation();
                        focusModeEnabled = event.target.checked;
                        window.focusModeEnabled = focusModeEnabled; // 同步到全局
                        saveSettings();
                        
                        console.log(`Focus模式已${focusModeEnabled ? '开启' : '关闭'}`);
                    });
                }
                
                settingsPopupInitialized = true;
                console.log('设置弹出框已初始化');
                
            }
        }
    });
    
    // 睡觉按钮（请她离开）
    window.addEventListener('live2d-goodbye-click', () => {
        console.log('[App] 请她离开按钮被点击，开始隐藏所有按钮');
        console.log('[App] 当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
        
        // 第一步：立即设置标志位，防止任何后续逻辑显示按钮
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = true;
        }
        console.log('[App] 设置 goodbyeClicked 为 true，当前状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
        
        // 【修复】立即关闭所有弹窗，防止遗留的弹窗区域阻塞鼠标事件
        // 这里直接操作 DOM，不使用动画延迟，确保弹窗立即完全隐藏
        // 注意：需要同时选中 live2d-popup-* 和 live2d-mic-popup（麦克风弹窗ID格式不同）
        const allPopups = document.querySelectorAll('[id^="live2d-popup-"], #live2d-mic-popup');
        allPopups.forEach(popup => {
            popup.style.setProperty('display', 'none', 'important');
            popup.style.setProperty('visibility', 'hidden', 'important');
            popup.style.setProperty('opacity', '0', 'important');
            popup.style.setProperty('pointer-events', 'none', 'important');
        });
        // 同时清除所有弹窗定时器
        if (window.live2dManager && window.live2dManager._popupTimers) {
            Object.values(window.live2dManager._popupTimers).forEach(timer => {
                if (timer) clearTimeout(timer);
            });
            window.live2dManager._popupTimers = {};
        }
        console.log('[App] 已关闭所有弹窗，数量:', allPopups.length);
        
        // 【修复】隐藏 Live2D canvas，使 Electron 的 alpha 检测认为该区域完全透明
        // 仅设置 pointer-events: none 不够，因为 Electron 根据像素 alpha 值来决定事件转发
        // 必须设置 visibility: hidden 来确保 canvas 不渲染任何像素
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'hidden', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已隐藏 live2d-canvas（visibility: hidden），Electron 将认为该区域透明');
        }
        
        // 在隐藏 DOM 之前先读取 "请她离开" 按钮的位置（避免隐藏后 getBoundingClientRect 返回异常）
        const goodbyeButton = document.getElementById('live2d-btn-goodbye');
        let savedGoodbyeRect = null;
        if (goodbyeButton) {
            try {
                savedGoodbyeRect = goodbyeButton.getBoundingClientRect();
            } catch (e) {
                savedGoodbyeRect = null;
            }
        }

        // 第二步：立即隐藏所有浮动按钮和锁按钮（设置为 !important 防止其他代码覆盖）
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            floatingButtons.style.setProperty('display', 'none', 'important');
            floatingButtons.style.setProperty('visibility', 'hidden', 'important');
            floatingButtons.style.setProperty('opacity', '0', 'important');
        }

        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.style.setProperty('display', 'none', 'important');
            lockIcon.style.setProperty('visibility', 'hidden', 'important');
            lockIcon.style.setProperty('opacity', '0', 'important');
        }

        // 第三步：显示独立的"请她回来"按钮（显示在原来"请她离开"按钮的位置）
        const returnButtonContainer = document.getElementById('live2d-return-button-container');
        if (returnButtonContainer) {
            // 如果我们之前成功读取到原按钮的位置，就基于该矩形进行居中定位
            if (savedGoodbyeRect) {
                // 获取返回容器尺寸，兼容尚未加入DOM或display:none的情况
                const containerWidth = returnButtonContainer.offsetWidth || 64;
                const containerHeight = returnButtonContainer.offsetHeight || 64;

                // 将返回按钮居中放在原按钮位置
                const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2 + window.scrollX);
                const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2 + window.scrollY);

                returnButtonContainer.style.left = `${Math.max(0, Math.min(left, window.innerWidth - containerWidth))}px`;
                returnButtonContainer.style.top = `${Math.max(0, Math.min(top, window.innerHeight - containerHeight))}px`;
                returnButtonContainer.style.transform = 'none'; // 移除居中transform
            } else {
                // 回退：如果无法读取原按钮位置，则将返回按钮放在右下角上方的预设位置
                const fallbackRight = 16;
                const fallbackBottom = 116;
                returnButtonContainer.style.right = `${fallbackRight}px`;
                returnButtonContainer.style.bottom = `${fallbackBottom}px`;
                // 清除 left/top/transform 以避免冲突
                returnButtonContainer.style.left = '';
                returnButtonContainer.style.top = '';
                returnButtonContainer.style.transform = 'none';
            }

            returnButtonContainer.style.display = 'flex';
            returnButtonContainer.style.pointerEvents = 'auto';
        }
        
        // 第四步：立即隐藏所有 side-btn 按钮和侧边栏
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');
        
        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }
        
        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }
        
        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });
        
        // 第五步：自动折叠对话区
        const chatContainerEl = document.getElementById('chat-container');
        const toggleChatBtn = document.getElementById('toggle-chat-btn');
        if (chatContainerEl && !chatContainerEl.classList.contains('minimized')) {
            // 如果对话区当前是展开的，模拟点击折叠按钮
            if (toggleChatBtn) {
                toggleChatBtn.click();
            }
        }
        
        // 第六步：触发原有的离开逻辑（关闭会话并让live2d消失）
        if (resetSessionButton) {
            // 延迟一点点执行，确保隐藏操作已经生效
            setTimeout(() => {
                console.log('[App] 触发 resetSessionButton.click()，当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
                resetSessionButton.click();
            }, 10);
        } else {
            console.error('[App] ❌ resetSessionButton 未找到！');
        }
    });
    
    // 请她回来按钮
    window.addEventListener('live2d-return-click', () => {
        console.log('[App] 请她回来按钮被点击，开始恢复所有界面');
        
        // 第一步：同步 window 中的设置值到局部变量（防止从 l2d 页面返回时值丢失）
        if (typeof window.focusModeEnabled !== 'undefined') {
            focusModeEnabled = window.focusModeEnabled;
            console.log('[App] 同步 focusModeEnabled:', focusModeEnabled);
        }
        if (typeof window.proactiveChatEnabled !== 'undefined') {
            proactiveChatEnabled = window.proactiveChatEnabled;
            console.log('[App] 同步 proactiveChatEnabled:', proactiveChatEnabled);
        }
        
        // 第二步：清除"请她离开"标志
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = false;
        }
        if (window.live2d) {
            window.live2d._goodbyeClicked = false;
        }
        
        // 第三步：隐藏独立的"请她回来"按钮
        const returnButtonContainer = document.getElementById('live2d-return-button-container');
        if (returnButtonContainer) {
            returnButtonContainer.style.display = 'none';
            returnButtonContainer.style.pointerEvents = 'none';
        }
        
        // 第四步：恢复live2d容器（移除minimized类）
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            console.log('[App] 移除minimized类前，容器类列表:', live2dContainer.classList.toString());
            live2dContainer.classList.remove('minimized');
            
            // 如果容器没有其他类，完全移除class属性以避免显示为class=""
            if (live2dContainer.classList.length === 0) {
                live2dContainer.removeAttribute('class');
            }
            
            console.log('[App] 移除minimized类后，容器类列表:', live2dContainer.classList.toString());
            live2dContainer.style.removeProperty('display');
            live2dContainer.style.removeProperty('visibility');
            live2dContainer.style.removeProperty('opacity');
        }
        
        // 【修复】恢复 Live2D canvas 的可见性和鼠标事件
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            // 恢复 visibility，使 Electron 能够检测到 canvas 像素
            live2dCanvas.style.removeProperty('visibility');
            live2dCanvas.style.visibility = 'visible';
            
            // 根据当前锁定状态恢复 pointerEvents
            const isLocked = window.live2dManager ? window.live2dManager.isLocked : true;
            live2dCanvas.style.removeProperty('pointer-events');
            live2dCanvas.style.pointerEvents = isLocked ? 'none' : 'auto';
            console.log('[App] 已恢复 live2d-canvas 的可见性和鼠标事件，isLocked:', isLocked);
        }
        
        // 第五步：恢复锁按钮
        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.style.display = 'block';
            lockIcon.style.removeProperty('visibility');
            lockIcon.style.removeProperty('opacity');
        }
        
        // 第六步：恢复浮动按钮系统（使用 !important 强制显示，覆盖之前的隐藏样式）
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            // 先清除所有可能的隐藏样式
            floatingButtons.style.removeProperty('display');
            floatingButtons.style.removeProperty('visibility');
            floatingButtons.style.removeProperty('opacity');
            
            // 使用 !important 强制显示，确保覆盖之前的隐藏样式
            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
            
            // 恢复所有按钮的显示状态（清除之前"请她离开"时设置的 display: 'none'）
            if (window.live2dManager && window.live2dManager._floatingButtons) {
                Object.keys(window.live2dManager._floatingButtons).forEach(btnId => {
                    const buttonData = window.live2dManager._floatingButtons[btnId];
                    if (buttonData && buttonData.button) {
                        // 清除 display 样式，让按钮正常显示
                        buttonData.button.style.removeProperty('display');
                    }
                });
            }
        }
        
        // 第七步：恢复对话区
        const chatContainerEl = document.getElementById('chat-container');
        const toggleChatBtn = document.getElementById('toggle-chat-btn');
        if (chatContainerEl && chatContainerEl.classList.contains('minimized')) {
            // 如果对话区当前是折叠的，模拟点击展开按钮
            if (toggleChatBtn) {
                toggleChatBtn.click();
            }
        }
        
        // 第八步：恢复基本的按钮状态（但不自动开始新会话）
        // 注意：不再触发 returnSessionButton.click()，因为那会自动发送 start_session 消息
        // 用户只是想让形象回来，不需要自动开始语音或文本对话
        
        // 设置模式切换标志
        isSwitchingMode = true;
        
        // 清除所有语音相关的状态类（确保按钮不会显示为激活状态）
        micButton.classList.remove('recording');
        micButton.classList.remove('active');
        screenButton.classList.remove('active');
        
        // 确保停止录音状态
        isRecording = false;
        window.isRecording = false;
        
        // 同步更新Live2D浮动按钮的状态
        if (window.live2dManager && window.live2dManager._floatingButtons) {
            ['mic', 'screen'].forEach(buttonId => {
                const buttonData = window.live2dManager._floatingButtons[buttonId];
                if (buttonData && buttonData.button) {
                    buttonData.button.dataset.active = 'false';
                    if (buttonData.imgOff) {
                        buttonData.imgOff.style.opacity = '1';
                    }
                    if (buttonData.imgOn) {
                        buttonData.imgOn.style.opacity = '0';
                    }
                }
            });
        }
        
        // 启用所有基本输入按钮
        micButton.disabled = false;
        textSendButton.disabled = false;
        textInputBox.disabled = false;
        screenshotButton.disabled = false;
        resetSessionButton.disabled = false;
        
        // 禁用语音控制按钮（文本模式下不需要）
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        
        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) {
            textInputArea.classList.remove('hidden');
        }
        
        // 标记文本会话为非活跃状态（用户需要手动发送消息才会开始会话）
        isTextSessionActive = false;
        
        // 显示欢迎消息，提示用户可以开始对话
        showStatusToast(window.t ? window.t('app.welcomeBack', {name: lanlan_config.lanlan_name}) : `🫴 ${lanlan_config.lanlan_name}回来了！`, 3000);
        
        // 延迟重置模式切换标志
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
        
        console.log('[App] 请她回来完成，未自动开始会话，等待用户主动发起对话');
    });
    
    // ========== Agent控制逻辑 ==========
    
    // Agent 定时检查器（暴露到 window 供 live2d-ui.js 调用）
    let agentCheckInterval = null;
    let lastFlagsSyncTime = 0;
    const FLAGS_SYNC_INTERVAL = 3000; // 每3秒同步一次后端flags状态
    
    // 【新增】追踪 Agent 弹窗是否打开
    let isAgentPopupOpen = false;
    
    // 检查 Agent 能力（供轮询使用）
    const checkAgentCapabilities = async () => {
        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentMcpCheckbox = document.getElementById('live2d-agent-mcp');
        
        // 【改进1】只有当总开关关闭 且 弹窗未打开时，才停止轮询
        // 如果弹窗打开，即使总开关关闭也要继续轮询（显示服务器状态）
        if (!agentMasterCheckbox || (!agentMasterCheckbox.checked && !isAgentPopupOpen)) {
            console.log('[App] Agent总开关未开启且弹窗已关闭，停止可用性轮询');
            window.stopAgentAvailabilityCheck();
            return;
        }
        
        // 【改进2】如果总开关未开启，跳过能力检查和flags同步，只进行基础的连通性检查
        if (!agentMasterCheckbox.checked) {
            // 弹窗打开但总开关未开启时，只检查服务器连通性
            try {
                const healthOk = await checkToolServerHealth();
                const wasDisabled = agentMasterCheckbox.disabled;
                if (healthOk) {
                    // 服务器在线但总开关未开启
                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
                    // 如果之前是禁用状态（离线），现在上线了，更新状态提示
                    if (wasDisabled) {
                        setFloatingAgentStatus('Agent服务器就绪');
                    }
                } else {
                    // 服务器离线
                    agentMasterCheckbox.disabled = true;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                    if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
                    setFloatingAgentStatus('Agent服务器未启动');
                }
            } catch (e) {
                // 检查失败
                agentMasterCheckbox.disabled = true;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
            }
            return; // 总开关未开启，不进行后续的能力检查和flags同步
        }
        
        // 【新增】定期从后端同步 flags 状态（每3秒一次）
        const now = Date.now();
        if (now - lastFlagsSyncTime >= FLAGS_SYNC_INTERVAL) {
            lastFlagsSyncTime = now;
            try {
                const resp = await fetch('/api/agent/flags');
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.success) {
                        const analyzerEnabled = data.analyzer_enabled || false;
                        const flags = data.agent_flags || {};
                        
                        // 如果后端 analyzer 被关闭，同步关闭前端总开关
                        // 【修复】如果总开关正在处理用户操作（_processing），则跳过同步，防止闪烁
                        if (!analyzerEnabled && agentMasterCheckbox.checked && !agentMasterCheckbox._processing) {
                            console.log('[App] 后端 analyzer 已关闭，同步关闭前端总开关');
                            agentMasterCheckbox.checked = false;
                            agentMasterCheckbox._autoDisabled = true;
                            agentMasterCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                            agentMasterCheckbox._autoDisabled = false;
                            if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
                            // 复位子开关
                            [agentKeyboardCheckbox, agentMcpCheckbox].forEach(cb => {
                                if (cb) {
                                    cb.checked = false;
                                    cb.disabled = true;
                                    if (typeof cb._updateStyle === 'function') cb._updateStyle();
                                }
                            });
                            setFloatingAgentStatus('Agent模式已关闭');
                            // 【改进】只有在弹窗也关闭的情况下才停止轮询
                            if (!isAgentPopupOpen) {
                                window.stopAgentAvailabilityCheck();
                            }
                            window.stopAgentTaskPolling();
                            return;
                        }
                        
                        // 同步子开关的 checked 状态（如果后端状态与前端不一致）
                        // 【修复】增加 _processing 检查，防止用户点击时被轮询覆盖
                        if (agentKeyboardCheckbox && !agentKeyboardCheckbox._processing && agentKeyboardCheckbox.checked !== (flags.computer_use_enabled || false)) {
                            console.log('[App] 同步键鼠控制开关状态:', flags.computer_use_enabled);
                            agentKeyboardCheckbox.checked = flags.computer_use_enabled || false;
                            agentKeyboardCheckbox._autoDisabled = true;
                            agentKeyboardCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                            agentKeyboardCheckbox._autoDisabled = false;
                            if (typeof agentKeyboardCheckbox._updateStyle === 'function') agentKeyboardCheckbox._updateStyle();
                        }
                        if (agentMcpCheckbox && !agentMcpCheckbox._processing && agentMcpCheckbox.checked !== (flags.mcp_enabled || false)) {
                            console.log('[App] 同步MCP工具开关状态:', flags.mcp_enabled);
                            agentMcpCheckbox.checked = flags.mcp_enabled || false;
                            agentMcpCheckbox._autoDisabled = true;
                            agentMcpCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                            agentMcpCheckbox._autoDisabled = false;
                            if (typeof agentMcpCheckbox._updateStyle === 'function') agentMcpCheckbox._updateStyle();
                        }
                    }
                }
            } catch (e) {
                console.warn('[App] 轮询同步 flags 失败:', e);
            }
        }
        
        const checks = [
            { id: 'live2d-agent-keyboard', capability: 'computer_use', flagKey: 'computer_use_enabled', name: '键鼠控制' },
            { id: 'live2d-agent-mcp', capability: 'mcp', flagKey: 'mcp_enabled', name: 'MCP工具' }
        ];
        for (const {id, capability, flagKey, name} of checks) {
            const cb = document.getElementById(id);
            if (!cb) continue;
            
            // 【改进3】再次检查总开关，防止在轮询中途被关闭
            if (!agentMasterCheckbox.checked) {
                cb.disabled = true;
                if (typeof cb._updateStyle === 'function') cb._updateStyle();
                continue;
            }
            
            const available = await checkCapability(capability, false);
            
            // 【防竞态】检查完成后再次确认总开关仍然开启
            if (!agentMasterCheckbox.checked) {
                cb.disabled = true;
                if (typeof cb._updateStyle === 'function') cb._updateStyle();
                continue;
            }
            
            cb.disabled = !available;
            cb.title = available ? name : (window.t ? window.t('settings.toggles.unavailable', {name: name}) : `${name}不可用`);
            if (typeof cb._updateStyle === 'function') cb._updateStyle();
            
            // 如果不可用但开关是开的，需要关闭它并通知后端
            if (!available && cb.checked) {
                console.log(`[App] ${name}变为不可用，自动关闭`);
                cb.checked = false;
                // 触发 change 事件以更新 UI（但使用自定义标记避免重复发送请求）
                cb._autoDisabled = true;  // 标记这是自动关闭，避免change事件处理器再次发送请求
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb._autoDisabled = false;
                // 通知后端关闭该功能
                try {
                    await fetch('/api/agent/flags', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            lanlan_name: lanlan_config.lanlan_name,
                            flags: {[flagKey]: false}
                        })
                    });
                } catch(e) {
                    console.warn(`[App] 通知后端关闭${name}失败:`, e);
                }
                setFloatingAgentStatus(`${name}已断开`);
            }
        }
    };
    
    // 启动 Agent 可用性定时检查（由 Agent 总开关打开时调用）
    window.startAgentAvailabilityCheck = function() {
        // 清除之前的定时器
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
        }
        
        // 重置 flags 同步时间，确保立即同步一次
        lastFlagsSyncTime = 0;
        
        // 立即检查一次
        checkAgentCapabilities();
        
        // 每秒检查一次
        agentCheckInterval = setInterval(checkAgentCapabilities, 1000);
    };
    
    // 停止 Agent 可用性定时检查（由 Agent 总开关关闭时调用）
    window.stopAgentAvailabilityCheck = function() {
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }
    };
    
    // 浮动Agent status更新函数
    function setFloatingAgentStatus(msg) {
        const statusEl = document.getElementById('live2d-agent-status');
        if (statusEl) {
            statusEl.textContent = msg || '';
        }
    }
    
    // 检查Agent服务器健康状态
    async function checkToolServerHealth() {
        try {
            const resp = await fetch(`/api/agent/health`);
            if (!resp.ok) throw new Error('not ok');
            return true;
        } catch (e) {
            return false;
        }
    }
    
    // 检查Agent能力
    async function checkCapability(kind, showError = true) {
        const apis = {
            computer_use: { url: '/api/agent/computer_use/availability', name: '键鼠控制' },
            mcp: { url: '/api/agent/mcp/availability', name: 'MCP工具' }
        };
        const config = apis[kind];
        if (!config) return false;
        
        try {
            const r = await fetch(config.url);
            if (!r.ok) return false;
            const j = await r.json();
            if (!j.ready) {
                if (showError) {
                    setFloatingAgentStatus(j.reasons?.[0] || `${config.name}不可用`);
                }
                return false;
            }
            return true;
        } catch (e) {
            return false;
        }
    }
    
    // 连接Agent弹出框中的开关到Agent控制逻辑
    // 使用事件监听替代固定延迟，确保在浮动按钮创建完成后才绑定事件
    const setupAgentCheckboxListeners = () => {
        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentMcpCheckbox = document.getElementById('live2d-agent-mcp');
        
        if (!agentMasterCheckbox) {
            console.warn('[App] Agent开关元素未找到，跳过绑定');
            return;
        }
        
        console.log('[App] Agent开关元素已找到，开始绑定事件监听器');
        
        // 【修复频繁开关竞态问题】操作序列号，用于取消过期的异步操作
        let masterOperationSeq = 0;
        let keyboardOperationSeq = 0;
        let mcpOperationSeq = 0;
        
        // 标记这些 checkbox 有外部处理器（用于 live2d-ui.js 中的 _processing 标志管理）
        agentMasterCheckbox._hasExternalHandler = true;
        if (agentKeyboardCheckbox) agentKeyboardCheckbox._hasExternalHandler = true;
        if (agentMcpCheckbox) agentMcpCheckbox._hasExternalHandler = true;
        
        // 辅助函数：同步更新 checkbox 的 UI 样式
        const syncCheckboxUI = (checkbox) => {
            if (checkbox && typeof checkbox._updateStyle === 'function') {
                checkbox._updateStyle();
            }
        };
        
        // 辅助函数：重置子开关状态和 UI
        // 【改进3】关闭时保持灰色，并设置明确的title提示
        const resetSubCheckboxes = () => {
            const names = {
                'live2d-agent-keyboard': window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
                'live2d-agent-mcp': window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具'
            };
            [agentKeyboardCheckbox, agentMcpCheckbox].forEach(cb => {
                if (cb) {
                    cb.disabled = true;
                    cb.checked = false;
                    // 设置title提示需要先开启总开关
                    const name = names[cb.id] || '';
                    cb.title = window.t ? window.t('settings.toggles.masterRequired', {name: name}) : `请先开启Agent总开关`;
                    syncCheckboxUI(cb);
                }
            });
        };
        
        // 初始化时，确保键鼠控制和MCP工具默认禁用（除非Agent总开关已开启）
        if (!agentMasterCheckbox.checked) {
            resetSubCheckboxes();
        }
        
        // Agent总开关逻辑
        agentMasterCheckbox.addEventListener('change', async () => {
            // 【修复频繁开关竞态】每次操作递增序列号
            const currentSeq = ++masterOperationSeq;
            const isChecked = agentMasterCheckbox.checked;
            console.log('[App] Agent总开关状态变化:', isChecked, '序列号:', currentSeq);
            
            // 辅助函数：检查当前操作是否已过期
            const isExpired = () => {
                if (currentSeq !== masterOperationSeq) {
                    console.log('[App] 总开关操作已过期，序列号:', currentSeq, '当前:', masterOperationSeq);
                    return true;
                }
                return false;
            };
            
            // _processing 标志已在 live2d-ui.js 的点击处理中设置
            // 这里确保标志存在（以防 change 事件来自其他来源）
            if (!agentMasterCheckbox._processing) {
                agentMasterCheckbox._processing = true;
            }
            
            try {
                if (isChecked) {
                    // 【兜底】查询期间禁用总开关，防止重复点击
                    agentMasterCheckbox.disabled = true;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                    setFloatingAgentStatus('Agent服务器连接中...');
                    syncCheckboxUI(agentMasterCheckbox);
                    
                    let healthOk = false;
                    try {
                        healthOk = await checkToolServerHealth();
                        if (!healthOk) throw new Error('tool server down');
                    } catch (e) {
                        // 【竞态检查】操作过期则直接返回
                        if (isExpired()) return;
                        setFloatingAgentStatus('Agent服务器未启动');
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);
                        return;
                    }
                    
                    // 【竞态检查】操作过期则直接返回
                    if (isExpired()) return;
                    
                    // 查询成功，恢复总开关可交互状态
                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    syncCheckboxUI(agentMasterCheckbox);
                    setFloatingAgentStatus('Agent模式已开启');
                    
                    // 【改进2】检查可用性期间，先将键鼠和MCP按钮保持灰色
                    if (agentKeyboardCheckbox) {
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentKeyboardCheckbox);
                    }
                    if (agentMcpCheckbox) {
                        agentMcpCheckbox.disabled = true;
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentMcpCheckbox);
                    }
                    
                    // 检查键鼠控制和MCP工具的可用性
                    await Promise.all([
                        (async () => {
                            if (!agentKeyboardCheckbox) return;
                            const available = await checkCapability('computer_use', false);
                            // 【防竞态】检查操作序列号和总开关状态
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentKeyboardCheckbox.disabled = true;
                                agentKeyboardCheckbox.checked = false;
                                syncCheckboxUI(agentKeyboardCheckbox);
                                return;
                            }
                            agentKeyboardCheckbox.disabled = !available;
                            agentKeyboardCheckbox.title = available ? (window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制') : (window.t ? window.t('settings.toggles.unavailable', {name: window.t('settings.toggles.keyboardControl')}) : '键鼠控制不可用');
                            syncCheckboxUI(agentKeyboardCheckbox);
                        })(),
                        (async () => {
                            if (!agentMcpCheckbox) return;
                            const available = await checkCapability('mcp', false);
                            // 【防竞态】检查操作序列号和总开关状态
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentMcpCheckbox.disabled = true;
                                agentMcpCheckbox.checked = false;
                                syncCheckboxUI(agentMcpCheckbox);
                                return;
                            }
                            agentMcpCheckbox.disabled = !available;
                            agentMcpCheckbox.title = available ? (window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具') : (window.t ? window.t('settings.toggles.unavailable', {name: window.t('settings.toggles.mcpTools')}) : 'MCP工具不可用');
                            syncCheckboxUI(agentMcpCheckbox);
                        })()
                    ]);
                    
                    // 【竞态检查】Promise.all 完成后再次检查
                    if (isExpired()) return;
                    
                    try {
                        const r = await fetch('/api/agent/flags', {
                            method:'POST', 
                            headers:{'Content-Type':'application/json'}, 
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name, 
                                flags: {agent_enabled:true, computer_use_enabled:false, mcp_enabled:false}
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');
                        
                        // 【竞态检查】API 请求完成后检查操作是否过期
                        if (isExpired()) {
                            console.log('[App] flags API 完成后操作已过期');
                            return;
                        }
                        
                        // 启用 analyzer（确保 agent 模式开启时 analyze API 可用）
                        await fetch('/api/agent/admin/control', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({action: 'enable_analyzer'})
                        });
                        
                        // 【防竞态】API请求完成后确认操作序列号和总开关状态
                        if (isExpired() || !agentMasterCheckbox.checked) {
                            console.log('[App] API请求完成后操作已过期或总开关已关闭，不启动轮询');
                            resetSubCheckboxes();
                            return;
                        }
                        
                        // 启动定时检查器
                        window.startAgentAvailabilityCheck();
                    } catch(e) {
                        // 【竞态检查】错误处理前检查操作是否过期
                        if (isExpired()) return;
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);
                        resetSubCheckboxes();
                        // 确保任务轮询已停止
                        window.stopAgentTaskPolling();
                        setFloatingAgentStatus('开启失败');
                    }
                } else {
                    // 关闭操作：立即停止相关检查和轮询，然后进行清理
                    // 停止可用性定时检查器（但不影响弹窗打开时的连通性检查）
                    window.stopAgentAvailabilityCheck();
                    
                    // 停止任务轮询并隐藏 HUD
                    window.stopAgentTaskPolling();
                    
                    // 重置子开关（立即执行，不依赖异步结果）
                    resetSubCheckboxes();
                    
                    setFloatingAgentStatus('Agent模式已关闭');
                    
                    // 同步总开关自身的 UI
                    syncCheckboxUI(agentMasterCheckbox);
                    
                    // 禁用 analyzer 并停止所有任务（disable_analyzer 会 cascade 调用 end_all）
                    try {
                        await fetch('/api/agent/admin/control', {
                            method: 'POST', 
                            headers: {'Content-Type': 'application/json'}, 
                            body: JSON.stringify({action: 'disable_analyzer'})
                        });
                        
                        // 【竞态检查】如果操作已过期，跳过后续请求
                        if (isExpired()) {
                            console.log('[App] 关闭操作已过期，跳过后续API调用');
                            return;
                        }
                        
                        await fetch('/api/agent/flags', {
                            method: 'POST', 
                            headers: {'Content-Type': 'application/json'}, 
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name, 
                                flags: {agent_enabled: false, computer_use_enabled: false, mcp_enabled: false}
                            })
                        });
                    } catch(e) {
                        // 【竞态检查】错误处理前检查操作是否过期
                        if (!isExpired()) {
                            setFloatingAgentStatus('Agent模式已关闭（部分清理失败）');
                        }
                    }
                }
            } finally {
                // 清除处理中标志
                agentMasterCheckbox._processing = false;
            }
        });
        
        // 子开关通用处理函数（使用闭包捕获对应的序列号变量）
        const setupSubCheckbox = (checkbox, capability, flagKey, name, getSeq, setSeq) => {
            if (!checkbox) return;
            checkbox.addEventListener('change', async () => {
                // 【修复频繁开关竞态】每次操作递增序列号
                const currentSeq = setSeq();
                const isChecked = checkbox.checked;
                
                // 辅助函数：检查当前操作是否已过期
                const isExpired = () => {
                    if (currentSeq !== getSeq()) {
                        console.log(`[App] ${name}开关操作已过期，序列号:`, currentSeq, '当前:', getSeq());
                        return true;
                    }
                    return false;
                };
                
                // 如果是自动禁用触发的change事件，跳过处理（避免重复发送请求）
                if (checkbox._autoDisabled) {
                    console.log(`[App] ${name}开关自动关闭，跳过change处理`);
                    return;
                }
                
                console.log(`[App] ${name}开关状态变化:`, isChecked, '序列号:', currentSeq);
                if (!agentMasterCheckbox?.checked) {
                    checkbox.checked = false;
                    syncCheckboxUI(checkbox);
                    checkbox._processing = false;
                    return;
                }
                
                // 确保处理中标志存在
                if (!checkbox._processing) {
                    checkbox._processing = true;
                }
                
                try {
                    const enabled = isChecked;
                    if (enabled) {
                        const ok = await checkCapability(capability);
                        
                        // 【防竞态】检查操作序列号和总开关状态
                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}检查期间操作已过期或总开关已关闭，取消操作`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }
                        
                        if (!ok) {
                            setFloatingAgentStatus(`${name}不可用`);
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            return;
                        }
                    }
                    // 注：enabled=true时上面已检查；enabled=false时无await，入口检查已足够
                    
                    try {
                        const r = await fetch('/api/agent/flags', {
                            method:'POST', 
                            headers:{'Content-Type':'application/json'}, 
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name, 
                                flags: {[flagKey]: enabled}
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');
                        
                        // 【防竞态】请求完成后检查操作序列号和总开关状态
                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}请求完成后操作已过期或总开关已关闭，强制关闭`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }
                        
                        setFloatingAgentStatus(enabled ? `${name}已开启` : `${name}已关闭`);
                        // 关闭成功时也需要同步 UI
                        if (!enabled) {
                            syncCheckboxUI(checkbox);
                        }
                    } catch(e) {
                        // 【竞态检查】错误处理前检查操作是否过期
                        if (isExpired()) return;
                        if (enabled) {
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            setFloatingAgentStatus(`${name}开启失败`);
                        }
                    }
                } finally {
                    // 清除处理中标志
                    checkbox._processing = false;
                    checkbox._processingChangeId = null;
                }
            });
        };
        
        // 键鼠控制开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentKeyboardCheckbox, 
            'computer_use', 
            'computer_use_enabled', 
            '键鼠控制',
            () => keyboardOperationSeq,
            () => ++keyboardOperationSeq
        );
        
        // MCP工具开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentMcpCheckbox, 
            'mcp', 
            'mcp_enabled', 
            'MCP工具',
            () => mcpOperationSeq,
            () => ++mcpOperationSeq
        );
        
        // 从后端同步 flags 状态到前端开关（完整同步，处理所有情况）
        async function syncFlagsFromBackend() {
            try {
                const resp = await fetch('/api/agent/flags');
                if (!resp.ok) return false;
                const data = await resp.json();
                if (!data.success) return false;
                
                const flags = data.agent_flags || {};
                const analyzerEnabled = data.analyzer_enabled || false;
                const cuEnabled = flags.computer_use_enabled || false;
                const mcpEnabled = flags.mcp_enabled || false;
                
                console.log('[App] 从后端获取 flags 状态:', {analyzerEnabled, cuEnabled, mcpEnabled});
                
                // 同步总开关状态
                if (agentMasterCheckbox) {
                    agentMasterCheckbox.checked = analyzerEnabled;
                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    syncCheckboxUI(agentMasterCheckbox);
                }
                
                // 同步键鼠控制子开关
                if (agentKeyboardCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，根据后端状态设置
                        agentKeyboardCheckbox.checked = cuEnabled;
                        agentKeyboardCheckbox.disabled = false; // 先设为可用，后续可用性检查会更新
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制';
                    } else {
                        // Agent 未开启，复位子开关
                        agentKeyboardCheckbox.checked = false;
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', {name: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制'}) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentKeyboardCheckbox);
                }
                
                // 同步 MCP 工具子开关
                if (agentMcpCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，根据后端状态设置
                        agentMcpCheckbox.checked = mcpEnabled;
                        agentMcpCheckbox.disabled = false; // 先设为可用，后续可用性检查会更新
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具';
                    } else {
                        // Agent 未开启，复位子开关
                        agentMcpCheckbox.checked = false;
                        agentMcpCheckbox.disabled = true;
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', {name: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具'}) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentMcpCheckbox);
                }
                
                return analyzerEnabled;
            } catch (e) {
                console.warn('[App] 同步 flags 状态失败:', e);
                return false;
            }
        }
        
        // 暴露同步函数供外部调用（如定时轮询）
        window.syncAgentFlagsFromBackend = syncFlagsFromBackend;
        
        // 监听 Agent 弹窗打开事件，在弹窗显示时检查服务器状态并同步 flags
        window.addEventListener('live2d-agent-popup-opening', async () => {
            // 【新增】标记弹窗已打开
            isAgentPopupOpen = true;
            
            // 【修复】查询期间禁用所有开关，防止误操作
            // 禁用总开关，显示连接中状态，锁定title
            agentMasterCheckbox.disabled = true;
            agentMasterCheckbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
            syncCheckboxUI(agentMasterCheckbox);
            
            // 【修复】同时禁用子开关，显示查询中状态
            if (agentKeyboardCheckbox) {
                agentKeyboardCheckbox.disabled = true;
                agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                syncCheckboxUI(agentKeyboardCheckbox);
            }
            if (agentMcpCheckbox) {
                agentMcpCheckbox.disabled = true;
                agentMcpCheckbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                syncCheckboxUI(agentMcpCheckbox);
            }
            
            setFloatingAgentStatus('Agent服务器连接中...');
            
            try {
                const ok = await checkToolServerHealth();
                if (ok) {
                    // 服务器在线，同步 flags 状态
                    const analyzerEnabled = await syncFlagsFromBackend();
                    
                    // 启用总开关，恢复title（syncFlagsFromBackend 已设置，这里确保一致）
                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    syncCheckboxUI(agentMasterCheckbox);
                    
                    // 根据当前状态显示提示并启动相关轮询
                    if (analyzerEnabled) {
                        setFloatingAgentStatus('Agent模式已开启');
                        // 启动可用性检查（会更新子开关的 disabled 状态）
                        window.startAgentAvailabilityCheck();
                        // 启动任务轮询 HUD
                        window.startAgentTaskPolling();
                    } else {
                        setFloatingAgentStatus('Agent服务器就绪');
                        // 【改进】弹窗打开时也启动轮询，以便检测服务器状态
                        // 轮询函数会自行判断是否执行能力检查
                        window.startAgentAvailabilityCheck();
                        window.stopAgentTaskPolling();
                    }
                } else {
                    // 服务器离线，复位所有开关
                    agentMasterCheckbox.checked = false;
                    agentMasterCheckbox.disabled = true;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                    syncCheckboxUI(agentMasterCheckbox);
                    resetSubCheckboxes();
                    setFloatingAgentStatus('Agent服务器未启动');
                    // 【改进】即使离线也启动轮询，以便检测服务器上线
                    window.startAgentAvailabilityCheck();
                }
            } catch (e) {
                // 检查失败，复位所有开关
                agentMasterCheckbox.checked = false;
                agentMasterCheckbox.disabled = true;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                syncCheckboxUI(agentMasterCheckbox);
                resetSubCheckboxes();
                setFloatingAgentStatus('Agent服务器未启动');
                // 【改进】即使出错也启动轮询，以便检测服务器上线
                window.startAgentAvailabilityCheck();
            }
        });
        
        // 【新增】监听 Agent 弹窗关闭事件
        window.addEventListener('live2d-agent-popup-closed', () => {
            isAgentPopupOpen = false;
            console.log('[App] Agent弹窗已关闭');
            
            // 如果总开关未开启，停止轮询
            if (!agentMasterCheckbox || !agentMasterCheckbox.checked) {
                window.stopAgentAvailabilityCheck();
            }
        });
        
        console.log('[App] Agent开关事件监听器绑定完成');
    };
    
    // ========== Agent 任务 HUD 轮询逻辑 ==========
    let agentTaskPollingInterval = null;
    let agentTaskTimeUpdateInterval = null;
    
    // 启动任务状态轮询
    window.startAgentTaskPolling = function() {
        if (agentTaskPollingInterval) return; // 已经在运行
        
        console.log('[App] 启动 Agent 任务状态轮询');
        
        // 确保 HUD 已创建并显示
        if (window.live2dManager) {
            window.live2dManager.createAgentTaskHUD();
            window.live2dManager.showAgentTaskHUD();
        }
        
        // 立即执行一次
        fetchAndUpdateTaskStatus();
        
        // 每 2 秒轮询一次任务状态
        agentTaskPollingInterval = setInterval(fetchAndUpdateTaskStatus, 2000);
        
        // 每秒更新运行时间显示
        agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
    };
    
    // 停止任务状态轮询
    window.stopAgentTaskPolling = function() {
        console.log('[App] 停止 Agent 任务状态轮询');
        
        if (agentTaskPollingInterval) {
            clearInterval(agentTaskPollingInterval);
            agentTaskPollingInterval = null;
        }
        
        if (agentTaskTimeUpdateInterval) {
            clearInterval(agentTaskTimeUpdateInterval);
            agentTaskTimeUpdateInterval = null;
        }
        
        // 隐藏 HUD
        if (window.live2dManager) {
            window.live2dManager.hideAgentTaskHUD();
        }
    };
    
    // 获取并更新任务状态
    async function fetchAndUpdateTaskStatus() {
        try {
            const response = await fetch('/api/agent/task_status');
            if (!response.ok) {
                console.warn('[App] 获取任务状态失败:', response.status);
                return;
            }
            
            const data = await response.json();
            if (data.success && window.live2dManager) {
                window.live2dManager.updateAgentTaskHUD(data);
            }
        } catch (error) {
            console.warn('[App] 任务状态轮询出错:', error);
        }
    }
    
    // 更新运行中任务的时间显示
    function updateTaskRunningTimes() {
        const taskList = document.getElementById('agent-task-list');
        if (!taskList) return;
        
        const timeElements = taskList.querySelectorAll('[id^="task-time-"]');
        timeElements.forEach(timeEl => {
            const taskId = timeEl.id.replace('task-time-', '');
            const card = document.querySelector(`.task-card[data-task-id="${taskId}"]`);
            if (!card) return;
            
            // 从原始 start_time 重新计算（存储在 data 属性中）
            const startTimeStr = card.dataset.startTime;
            if (startTimeStr) {
                const startTime = new Date(startTimeStr);
                const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
                const minutes = Math.floor(elapsed / 60);
                const seconds = elapsed % 60;
                timeEl.innerHTML = `<span style="color: #64748b;">⏱️</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        });
    }
    
    // 检查是否需要显示任务 HUD（键鼠或 MCP 任一开启）
    function checkAndToggleTaskHUD() {
        const keyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const mcpCheckbox = document.getElementById('live2d-agent-mcp');
        
        const keyboardEnabled = keyboardCheckbox && keyboardCheckbox.checked;
        const mcpEnabled = mcpCheckbox && mcpCheckbox.checked;
        
        if (keyboardEnabled || mcpEnabled) {
            window.startAgentTaskPolling();
        } else {
            window.stopAgentTaskPolling();
        }
    }
    
    // 监听 Agent 子开关变化来控制 HUD 显示
    window.addEventListener('live2d-floating-buttons-ready', () => {
        // 延迟确保元素已创建
        setTimeout(() => {
            const keyboardCheckbox = document.getElementById('live2d-agent-keyboard');
            const mcpCheckbox = document.getElementById('live2d-agent-mcp');
            
            if (keyboardCheckbox) {
                keyboardCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }
            if (mcpCheckbox) {
                mcpCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }
            
            console.log('[App] Agent 任务 HUD 控制已绑定');
        }, 100);
    });
    // ========== Agent 任务 HUD 轮询逻辑结束 ==========
    
    // 监听浮动按钮创建完成事件
    window.addEventListener('live2d-floating-buttons-ready', () => {
        console.log('[App] 收到浮动按钮就绪事件，开始绑定Agent开关');
        setupAgentCheckboxListeners();
    }, { once: true });  // 只执行一次
    
    // 麦克风权限和设备列表预加载（修复 UI 2.0 中权限请求时机导致的bug）
    let micPermissionGranted = false;
    let cachedMicDevices = null;
    
    // 预先请求麦克风权限并缓存设备列表
    async function ensureMicrophonePermission() {
        if (micPermissionGranted && cachedMicDevices) {
            return cachedMicDevices;
        }
        
        try {
            // 方法1：先请求一次短暂的麦克风访问来触发权限请求
            // 这样后续 enumerateDevices() 才能返回带 label 的设备信息
            const tempStream = await navigator.mediaDevices.getUserMedia({ 
                audio: true 
            });
            
            // 立即释放流，我们只是为了触发权限
            tempStream.getTracks().forEach(track => track.stop());
            
            micPermissionGranted = true;
            console.log('麦克风权限已获取');
            
            // 现在可以获取完整的设备列表（带 label）
            const devices = await navigator.mediaDevices.enumerateDevices();
            cachedMicDevices = devices.filter(device => device.kind === 'audioinput');
            
            return cachedMicDevices;
        } catch (error) {
            console.warn('请求麦克风权限失败:', error);
            // 即使权限失败，也尝试获取设备列表（可能没有 label）
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices.filter(device => device.kind === 'audioinput');
                return cachedMicDevices;
            } catch (enumError) {
                console.error('获取设备列表失败:', enumError);
                return [];
            }
        }
    }
    
    // 监听设备变化，更新缓存
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', async () => {
            console.log('检测到设备变化，刷新麦克风列表...');
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices.filter(device => device.kind === 'audioinput');
                // 如果弹出框当前是显示的，刷新它
                const micPopup = document.getElementById('live2d-mic-popup');
                if (micPopup && micPopup.style.display === 'flex') {
                    await window.renderFloatingMicList();
                }
            } catch (error) {
                console.error('设备变化后更新列表失败:', error);
            }
        });
    }
    
    // 为浮动弹出框渲染麦克风列表（修复版本：确保有权限后再渲染）
    window.renderFloatingMicList = async () => {
        const micPopup = document.getElementById('live2d-mic-popup');
        if (!micPopup) {
            return false;
        }
        
        try {
            // 确保已经有麦克风权限，并获取设备列表
            const audioInputs = await ensureMicrophonePermission();
            
            micPopup.innerHTML = '';
            
            if (audioInputs.length === 0) {
                const noMicItem = document.createElement('div');
                noMicItem.textContent = window.t ? window.t('microphone.noDevices') : '没有检测到麦克风设备';
                noMicItem.style.padding = '8px 12px';
                noMicItem.style.color = '#666';
                noMicItem.style.fontSize = '13px';
                micPopup.appendChild(noMicItem);
                return false;
            }
            
            // 添加默认麦克风选项
            const defaultOption = document.createElement('button');
            defaultOption.className = 'mic-option';
            // 不设置 dataset.deviceId，让它保持 undefined（表示默认）
            defaultOption.textContent = window.t ? window.t('microphone.defaultDevice') : '系统默认麦克风';
            if (selectedMicrophoneId === null) {
                defaultOption.classList.add('selected');
            }
            Object.assign(defaultOption.style, {
                padding: '8px 12px',
                cursor: 'pointer',
                border: 'none',
                background: selectedMicrophoneId === null ? '#e6f0ff' : 'transparent',
                borderRadius: '6px',
                transition: 'background 0.2s ease',
                fontSize: '13px',
                width: '100%',
                textAlign: 'left',
                color: selectedMicrophoneId === null ? '#4f8cff' : '#333',
                fontWeight: selectedMicrophoneId === null ? '500' : '400'
            });
            defaultOption.addEventListener('mouseenter', () => {
                if (selectedMicrophoneId !== null) {
                    defaultOption.style.background = 'rgba(79, 140, 255, 0.1)';
                }
            });
            defaultOption.addEventListener('mouseleave', () => {
                if (selectedMicrophoneId !== null) {
                    defaultOption.style.background = 'transparent';
                }
            });
            defaultOption.addEventListener('click', async () => {
                await selectMicrophone(null);
                // 只更新选中状态，不重新渲染整个列表
                updateMicListSelection();
            });
            micPopup.appendChild(defaultOption);
            
            // 添加分隔线
            const separator = document.createElement('div');
            separator.style.height = '1px';
            separator.style.backgroundColor = '#eee';
            separator.style.margin = '5px 0';
            micPopup.appendChild(separator);
            
            // 添加各个麦克风设备选项
            audioInputs.forEach(device => {
                const option = document.createElement('button');
                option.className = 'mic-option';
                option.dataset.deviceId = device.deviceId; // 存储设备ID用于更新选中状态
                const micIndex = audioInputs.indexOf(device) + 1;
                option.textContent = device.label || (window.t ? window.t('microphone.deviceLabel', {index: micIndex}) : `麦克风 ${micIndex}`);
                if (selectedMicrophoneId === device.deviceId) {
                    option.classList.add('selected');
                }
                
                Object.assign(option.style, {
                    padding: '8px 12px',
                    cursor: 'pointer',
                    border: 'none',
                    background: selectedMicrophoneId === device.deviceId ? '#e6f0ff' : 'transparent',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    fontSize: '13px',
                    width: '100%',
                    textAlign: 'left',
                    color: selectedMicrophoneId === device.deviceId ? '#4f8cff' : '#333',
                    fontWeight: selectedMicrophoneId === device.deviceId ? '500' : '400'
                });
                
                option.addEventListener('mouseenter', () => {
                    if (selectedMicrophoneId !== device.deviceId) {
                        option.style.background = 'rgba(79, 140, 255, 0.1)';
                    }
                });
                option.addEventListener('mouseleave', () => {
                    if (selectedMicrophoneId !== device.deviceId) {
                        option.style.background = 'transparent';
                    }
                });
                
                option.addEventListener('click', async () => {
                    await selectMicrophone(device.deviceId);
                    // 只更新选中状态，不重新渲染整个列表
                    updateMicListSelection();
                });
                
                micPopup.appendChild(option);
            });
            
            return true;
        } catch (error) {
            console.error('渲染麦克风列表失败:', error);
            micPopup.innerHTML = '';
            const errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('microphone.loadFailed') : '获取麦克风列表失败';
            errorItem.style.padding = '8px 12px';
            errorItem.style.color = '#dc3545';
            errorItem.style.fontSize = '13px';
            micPopup.appendChild(errorItem);
            return false;
        }
    };
    
    // 轻量级更新：仅更新麦克风列表的选中状态（不重新渲染整个列表）
    function updateMicListSelection() {
        const micPopup = document.getElementById('live2d-mic-popup');
        if (!micPopup) return;
        
        // 更新所有选项的选中状态
        const options = micPopup.querySelectorAll('.mic-option');
        options.forEach(option => {
            const deviceId = option.dataset.deviceId;
            const isSelected = (deviceId === undefined && selectedMicrophoneId === null) || 
                             (deviceId === selectedMicrophoneId);
            
            if (isSelected) {
                option.classList.add('selected');
                option.style.background = '#e6f0ff';
                option.style.color = '#4f8cff';
                option.style.fontWeight = '500';
            } else {
                option.classList.remove('selected');
                option.style.background = 'transparent';
                option.style.color = '#333';
                option.style.fontWeight = '400';
            }
        });
    }
    
    // 页面加载后预先请求麦克风权限（修复核心bug：确保权限在用户点击前就已获取）
    setTimeout(async () => {
        console.log('[麦克风] 页面加载，预先请求麦克风权限...');
        try {
            await ensureMicrophonePermission();
            console.log('[麦克风] 权限预请求完成，设备列表已缓存');
            // 触发事件通知权限已准备好（兼容可能依赖此事件的其他代码）
            window.dispatchEvent(new CustomEvent('mic-permission-ready'));
        } catch (error) {
            console.warn('[麦克风] 预请求权限失败（用户可能拒绝）:', error);
        }
    }, 500); // 页面加载后半秒开始预请求
    
    // 延迟渲染麦克风列表到弹出框（确保弹出框DOM已创建）
    setTimeout(() => {
        window.renderFloatingMicList();
    }, 1500);
    
    // 主动搭话定时触发功能
    function scheduleProactiveChat() {
        // 清除现有定时器
        if (proactiveChatTimer) {
            clearTimeout(proactiveChatTimer);
            proactiveChatTimer = null;
        }
        
        // 如果主动搭话未开启，不执行
        if (!proactiveChatEnabled) {
            return;
        }
        
        // 只在非语音模式下执行（语音模式下不触发主动搭话）
        // 文本模式或待机模式都可以触发主动搭话
        if (isRecording) {
            console.log('语音模式中，不安排主动搭话');
            return;
        }
        
        // 计算延迟时间（指数退避）
        const delay = PROACTIVE_CHAT_BASE_DELAY * Math.pow(2, proactiveChatBackoffLevel);
        console.log(`主动搭话：${delay / 1000}秒后触发（退避级别：${proactiveChatBackoffLevel}）`);
        
        proactiveChatTimer = setTimeout(async () => {
            console.log('触发主动搭话...');
            await triggerProactiveChat();
            
            // 增加退避级别（最多到4分钟，即level 3）
            if (proactiveChatBackoffLevel < 3) {
                proactiveChatBackoffLevel++;
            }
            
            // 安排下一次
            scheduleProactiveChat();
        }, delay);
    }
    
    async function triggerProactiveChat() {
        try {
            const response = await fetch('/api/proactive_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    lanlan_name: lanlan_config.lanlan_name
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                if (result.action === 'chat') {
                    console.log('主动搭话已发送:', result.message);
                    // 后端会直接通过session发送消息和TTS，前端无需处理显示
                } else if (result.action === 'pass') {
                    console.log('AI选择不搭话');
                }
            } else {
                console.warn('主动搭话失败:', result.error);
            }
        } catch (error) {
            console.error('主动搭话触发失败:', error);
        }
    }
    
    function resetProactiveChatBackoff() {
        // 重置退避级别
        proactiveChatBackoffLevel = 0;
        // 重新安排定时器
        scheduleProactiveChat();
    }
    
    function stopProactiveChatSchedule() {
        if (proactiveChatTimer) {
            clearTimeout(proactiveChatTimer);
            proactiveChatTimer = null;
        }
    }
    
    // 暴露函数到全局作用域，供 live2d.js 调用
    window.resetProactiveChatBackoff = resetProactiveChatBackoff;
    window.stopProactiveChatSchedule = stopProactiveChatSchedule;
    
    // 保存设置到localStorage
    function saveSettings() {
        // 从全局变量读取最新值（确保同步 live2d.js 中的更改）
        const currentProactive = typeof window.proactiveChatEnabled !== 'undefined' 
            ? window.proactiveChatEnabled 
            : proactiveChatEnabled;
        const currentFocus = typeof window.focusModeEnabled !== 'undefined' 
            ? window.focusModeEnabled 
            : focusModeEnabled;
        
        const settings = {
            proactiveChatEnabled: currentProactive,
            focusModeEnabled: currentFocus
        };
        localStorage.setItem('project_neko_settings', JSON.stringify(settings));
        
        // 同步回局部变量，保持一致性
        proactiveChatEnabled = currentProactive;
        focusModeEnabled = currentFocus;
    }
    
    // 暴露到全局作用域，供 live2d.js 等其他模块调用
    window.saveNEKOSettings = saveSettings;
    
    // 从localStorage加载设置
    function loadSettings() {
        try {
            const saved = localStorage.getItem('project_neko_settings');
            if (saved) {
                const settings = JSON.parse(saved);
                // 使用 ?? 运算符提供更好的默认值处理（避免将 false 误判为需要使用默认值）
                proactiveChatEnabled = settings.proactiveChatEnabled ?? false;
                window.proactiveChatEnabled = proactiveChatEnabled; // 同步到全局
                // Focus模式：从localStorage加载设置
                focusModeEnabled = settings.focusModeEnabled ?? false;
                window.focusModeEnabled = focusModeEnabled; // 同步到全局
                
                console.log('已加载设置:', {
                    proactiveChatEnabled: proactiveChatEnabled,
                    focusModeEnabled: focusModeEnabled,
                    focusModeDesc: focusModeEnabled ? 'AI说话时自动静音麦克风（不允许打断）' : '允许打断AI说话'
                });
            } else {
                // 如果没有保存的设置，也要确保全局变量被初始化
                console.log('未找到保存的设置，使用默认值');
                window.proactiveChatEnabled = proactiveChatEnabled;
                window.focusModeEnabled = focusModeEnabled;
            }
        } catch (error) {
            console.error('加载设置失败:', error);
            // 出错时也要确保全局变量被初始化
            window.proactiveChatEnabled = proactiveChatEnabled;
            window.focusModeEnabled = focusModeEnabled;
        }
    }
    
    // 加载设置
    loadSettings();
    
    // 如果已开启主动搭话，立即启动定时器
    if (proactiveChatEnabled) {
        scheduleProactiveChat();
    }
    
    // 猫娘切换处理函数（通过WebSocket推送触发）
    let isSwitchingCatgirl = false;  // 标记是否正在切换猫娘，防止自动重连冲突
    
    async function handleCatgirlSwitch(newCatgirl, oldCatgirl) {
        console.log('[猫娘切换] handleCatgirlSwitch 被调用，参数:', {newCatgirl, oldCatgirl, current: lanlan_config.lanlan_name, isSwitchingCatgirl});
        
        if (isSwitchingCatgirl) {
            console.log('[猫娘切换] ⚠️ 正在切换中，忽略重复的切换请求');
            return;
        }
        
        if (!newCatgirl) {
            console.log('[猫娘切换] ⚠️ 新猫娘名称为空，忽略');
            return;
        }
        
        console.log('[猫娘切换] 🚀 开始切换，从', lanlan_config.lanlan_name, '切换到', newCatgirl);
        
        // 显示切换提示
        showStatusToast(window.t ? window.t('app.switchingCatgirl', {name: newCatgirl}) : `正在切换到 ${newCatgirl}...`, 3000);
        
        // 标记正在切换，防止自动重连冲突
        isSwitchingCatgirl = true;
        
        // 取消之前的自动重连定时器（避免使用旧角色名重连）
        if (autoReconnectTimeoutId) {
            clearTimeout(autoReconnectTimeoutId);
            autoReconnectTimeoutId = null;
            console.log('[猫娘切换] 已取消之前的自动重连定时器');
        }
        
        // 清理活跃的会话状态
        if (isRecording) {
            console.log('[猫娘切换] 停止录音');
            stopRecording();
        }
        
        // 清空音频队列
        if (typeof clearAudioQueue === 'function') {
            console.log('[猫娘切换] 清空音频队列');
            clearAudioQueue();
        }
        
        // 重置文本会话状态
        if (isTextSessionActive) {
            console.log('[猫娘切换] 结束文本会话');
            isTextSessionActive = false;
        }

        // 更新配置
        const oldCatgirlName = lanlan_config.lanlan_name;
        
        // 关闭旧的 WebSocket 连接
        if (socket) {
            console.log('[猫娘切换] 关闭旧的 WebSocket 连接');
            socket.close();
            socket = null;
        }
        
        // 清除心跳定时器
        if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
            heartbeatInterval = null;
        }
        
        // 更新 lanlan_config.lanlan_name 为新的角色名
        lanlan_config.lanlan_name = newCatgirl;
        console.log('[猫娘切换] 已更新 lanlan_config.lanlan_name 为:', newCatgirl);
        
        // 等待一小段时间确保旧连接完全关闭
        await new Promise(resolve => setTimeout(resolve, 100));
        
        // 重新连接 WebSocket
        console.log('[猫娘切换] 重新连接 WebSocket，新猫娘:', newCatgirl);
        connectWebSocket();
        
        // 更新页面标题
        document.title = `${newCatgirl} Terminal - Project N.E.K.O.`;
        
        // 重新加载 Live2D 模型（强制重新加载，因为猫娘已切换）
        try {
            console.log('[猫娘切换] 开始重新加载 Live2D 模型...');
            const modelResponse = await fetch(`/api/characters/current_live2d_model?catgirl_name=${encodeURIComponent(newCatgirl)}`);
            const modelData = await modelResponse.json();
            
            console.log('[猫娘切换] Live2D 模型 API 响应:', modelData);
            
            if (modelData.success && modelData.model_name && modelData.model_info) {
                console.log('[猫娘切换] 检测到新猫娘的 Live2D 模型:', modelData.model_name, '路径:', modelData.model_info.path);
                
                // 如果是回退模型，显示提示
                if (modelData.model_info.is_fallback) {
                    console.log('[猫娘切换] ⚠️ 新猫娘未设置Live2D模型，使用默认模型 mao_pro');
                }
                
                // 检查 live2dManager 是否存在并已初始化
                if (!window.live2dManager) {
                    console.error('[猫娘切换] live2dManager 不存在，无法重新加载模型');
                } else if (!window.live2dManager.pixi_app) {
                    console.error('[猫娘切换] live2dManager 未初始化，无法重新加载模型');
                } else {
                    const currentModel = window.live2dManager.getCurrentModel();
                    const currentModelPath = currentModel ? (currentModel.url || '') : '';
                    const newModelPath = modelData.model_info.path;
                    
                    console.log('[猫娘切换] 当前模型路径:', currentModelPath);
                    console.log('[猫娘切换] 新模型路径:', newModelPath);
                    
                    // 重新加载模型（无论路径是否相同，因为猫娘已切换）
                    console.log('[猫娘切换] 重新加载 Live2D 模型，当前路径:', currentModelPath, '新路径:', newModelPath);
                    
                    // 获取模型配置
                    const modelConfigRes = await fetch(newModelPath);
                    if (modelConfigRes.ok) {
                        const modelConfig = await modelConfigRes.json();
                        modelConfig.url = newModelPath;
                        
                        console.log('[猫娘切换] 开始加载模型配置...');
                        
                        // 加载用户偏好设置
                        const preferences = await window.live2dManager.loadUserPreferences();
                        let modelPreferences = null;
                        if (preferences && preferences.length > 0) {
                            modelPreferences = preferences.find(p => p && p.model_path === newModelPath);
                            if (modelPreferences) {
                                console.log('[猫娘切换] 找到模型偏好设置:', modelPreferences);
                            } else {
                                console.log('[猫娘切换] 未找到模型偏好设置，将使用默认设置');
                            }
                        }
                        
                        // 加载新模型
                        await window.live2dManager.loadModel(modelConfig, {
                            preferences: modelPreferences,
                            isMobile: window.innerWidth <= 768
                        });
                        
                        // 更新全局引用
                        if (window.LanLan1) {
                            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                            window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();
                        }
                        
                        console.log('[猫娘切换] Live2D 模型已重新加载完成');
                    } else {
                        console.error('[猫娘切换] 无法获取模型配置，状态:', modelConfigRes.status);
                    }
                }
            } else {
                console.warn('[猫娘切换] 无法获取新猫娘的 Live2D 模型信息，尝试加载默认模型 mao_pro:', modelData);
                
                // 前端回退机制：如果后端没有返回有效的模型信息，尝试直接加载mao_pro
                try {
                    console.log('[猫娘切换] 尝试回退到默认模型 mao_pro');
                    
                    if (window.live2dManager && window.live2dManager.pixi_app) {
                        // 查找mao_pro模型
                        const modelsResponse = await fetch('/api/live2d/models');
                        if (modelsResponse.ok) {
                            const models = await modelsResponse.json();
                            const maoProModel = models.find(m => m.name === 'mao_pro');
                            
                            if (maoProModel) {
                                console.log('[猫娘切换] 找到默认模型 mao_pro，路径:', maoProModel.path);
                                
                                // 获取模型配置
                                const modelConfigRes = await fetch(maoProModel.path);
                                if (modelConfigRes.ok) {
                                    const modelConfig = await modelConfigRes.json();
                                    modelConfig.url = maoProModel.path;
                                    
                                    // 加载默认模型
                                    await window.live2dManager.loadModel(modelConfig, {
                                        isMobile: window.innerWidth <= 768
                                    });
                                    
                                    // 更新全局引用
                                    if (window.LanLan1) {
                                        window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                        window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                                        window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();
                                    }
                                    
                                    console.log('[猫娘切换] 已成功回退到默认模型 mao_pro');
                                } else {
                                    console.error('[猫娘切换] 无法获取默认模型配置，状态:', modelConfigRes.status);
                                }
                            } else {
                                console.error('[猫娘切换] 未找到默认模型 mao_pro');
                            }
                        } else {
                            console.error('[猫娘切换] 无法获取模型列表');
                        }
                    } else {
                        console.error('[猫娘切换] live2dManager 未初始化，无法加载默认模型');
                    }
                } catch (fallbackError) {
                    console.error('[猫娘切换] 回退到默认模型失败:', fallbackError);
                }
            }
            showStatusToast(window.t ? window.t('app.switchedCatgirl', {name: newCatgirl}) : `已切换到 ${newCatgirl}`, 3000);
        } catch (error) {
            console.error('[猫娘切换] 重新加载 Live2D 模型失败:', error);
            showStatusToast(window.t ? window.t('app.switchCatgirlFailed', {name: newCatgirl}) : `切换到 ${newCatgirl} 失败`, 4000);
            console.error('[猫娘切换] 错误堆栈:', error.stack);
        } finally {
            // 在所有操作完成后重置标记
            isSwitchingCatgirl = false;
            console.log('[猫娘切换] 切换流程已完成，重置标记');
        }
        
        console.log('[猫娘切换] 切换完成，已重新连接 WebSocket');
    }
    
    // 确保原生按钮和status栏在初始化时就被强制隐藏，永不出现
    const ensureHiddenElements = () => {
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');
        const statusElement = document.getElementById('status');
        
        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }
        
        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }
        
        if (statusElement) {
            statusElement.style.setProperty('display', 'none', 'important');
            statusElement.style.setProperty('visibility', 'hidden', 'important');
            statusElement.style.setProperty('opacity', '0', 'important');
        }
        
        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });
        
        console.log('[初始化] 原生按钮和status栏已强制隐藏');
    };
    
    // 立即执行一次
    ensureHiddenElements();
    
    // 使用MutationObserver监听特定元素的样式变化，确保这些元素始终保持隐藏
    const observerCallback = (mutations) => {
        // 避免递归调用：只在元素变为可见时才强制隐藏
        let needsHiding = false;
        mutations.forEach(mutation => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                const target = mutation.target;
                const computedStyle = window.getComputedStyle(target);
                if (computedStyle.display !== 'none' || computedStyle.visibility !== 'hidden') {
                    needsHiding = true;
                }
            }
        });
        
        if (needsHiding) {
            ensureHiddenElements();
        }
    };
    
    const observer = new MutationObserver(observerCallback);
    
    // 只监听sidebar、sidebarbox和status元素的样式变化
    const elementsToObserve = [
        document.getElementById('sidebar'),
        document.getElementById('sidebarbox'),
        document.getElementById('status')
    ].filter(Boolean);
    
    elementsToObserve.forEach(element => {
        observer.observe(element, {
            attributes: true,
            attributeFilter: ['style']
        });
    });
} // 兼容老按钮

const ready = () => {
    if (ready._called) return;
    ready._called = true;
    init_app();
};

// 检查页面加载状态，如果已加载完成则直接执行
if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(ready, 1); // 使用setTimeout确保异步执行，避免阻塞当前脚本执行
} else {
    document.addEventListener("DOMContentLoaded", ready);
    window.addEventListener("load", ready);
}

// 页面加载后显示启动提示
window.addEventListener("load", () => {
    setTimeout(() => {
        if (typeof window.showStatusToast === 'function' && typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(window.t ? window.t('app.started', {name: lanlan_config.lanlan_name}) : `${lanlan_config.lanlan_name}已启动`, 3000);
        }
    }, 1000);
});

// 监听voice_id更新消息
window.addEventListener('message', function(event) {
    if (event.data.type === 'voice_id_updated') {
        console.log('[Voice Clone] 收到voice_id更新消息:', event.data.voice_id);
        if (typeof window.showStatusToast === 'function' && typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(window.t ? window.t('app.voiceUpdated', {name: lanlan_config.lanlan_name}) : `${lanlan_config.lanlan_name}的语音已更新`, 3000);
        }
    }
});

