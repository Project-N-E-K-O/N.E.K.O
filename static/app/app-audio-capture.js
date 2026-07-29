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
    const MIC_LEASE = Object.freeze({
        NONE: 'none',
        GAME: 'game',
        CORE: 'core'
    });
    let voiceLeaseGeneration = 0;
    let lastVoiceLeaseFingerprint = '';
    // Cancellation token for an IN-FLIGHT microphone start (Codex P2).
    // S.isRecording only flips at the very END of startAudioWorklet, after
    // getUserMedia() and audioWorklet.addModule() have both awaited — so every
    // "stop the mic" guard keyed on S.isRecording === true is a no-op for the
    // whole startup window. The pending start then completed anyway, set
    // recording true and re-claimed through refreshMicLease() the very lease the
    // backend had just revoked, uploading PCM into a blocked route. Bumping this
    // invalidates any start still inside that window.
    //
    // The token each attempt compares against is a LOCAL const in
    // startMicCapture, deliberately not a module field. A module-level
    // "pending token" is re-armed by the NEXT startMicCapture: attempt #1 is
    // invalidated (generation moves past its token), attempt #2 then writes
    // both the token and the generation to the same new value, and #1's guard
    // compares equal again and commits -- re-claiming through refreshMicLease()
    // the exact lease this counter exists to protect.
    let micStartGeneration = 0;

    function invalidatePendingMicStart() {
        micStartGeneration += 1;
    }

    function currentVoiceInputControlState() {
        return {
            owner: resolveMicLeaseOwner(),
            hard_muted: S.isMicMuted === true,
            focus_suppressed: (
                S.focusModeEnabled === true
                && S.isPlaying === true
            ),
            // Engagement marker for the backend voice-connection claim gate:
            // the onopen force-sync also fires from windows that merely
            // opened (second /chat_full window). A snapshot stamped
            // engaged: false is provably passive — neither recording nor
            // starting a voice session — and must not steal the voice
            // connection identity from a window that is actively recording.
            engaged: (
                S.isRecording === true
                || S.voiceStartPending === true
                || window.isMicStarting === true
            )
        };
    }

    function sendVoiceInputControlState(force) {
        if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return false;
        const state = currentVoiceInputControlState();
        const fingerprint = JSON.stringify(state);
        if (force !== true && fingerprint === lastVoiceLeaseFingerprint) return true;
        voiceLeaseGeneration += 1;
        S.socket.send(JSON.stringify({
            action: 'voice_input_control',
            event: 'lease_sync',
            owner: state.owner,
            hard_muted: state.hard_muted,
            focus_suppressed: state.focus_suppressed,
            engaged: state.engaged,
            lease_generation: voiceLeaseGeneration
        }));
        lastVoiceLeaseFingerprint = fingerprint;
        return true;
    }

    function syncVoiceInputControlState(socket) {
        if (socket && socket !== S.socket) return false;
        if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return false;

        // 每条 WebSocket 都有独立的 generation scope；第一条消息就是完整状态。
        voiceLeaseGeneration = 0;
        lastVoiceLeaseFingerprint = '';
        return sendVoiceInputControlState(true);
    }

    function setVoiceInputLifecycleState(state) {
        const allowed = new Set([
            'off', 'local_listen', 'prewarming', 'active', 'draining',
            'warm_idle', 'deep_sleep', 'backoff', 'blocked', 'suspended'
        ]);
        if (!allowed.has(state) || S.voiceInputLifecycleState === state) return;
        S.voiceInputLifecycleState = state;
        document.documentElement.setAttribute('data-voice-input-state', state);
        window.dispatchEvent(new CustomEvent('voice-input-lifecycle-changed', {
            detail: { state, route_mode: S.independentAsrActive ? 'independent' : 'blocked' }
        }));
    }

    window.addEventListener('voice-input-socket-open', function (event) {
        syncVoiceInputControlState(event && event.detail && event.detail.socket);
    });

    function setMicLeaseOwner(owner) {
        if (!Object.values(MIC_LEASE).includes(owner)) {
            throw new Error(`Invalid microphone lease owner: ${owner}`);
        }
        if (S.micLeaseOwner !== owner) {
            S.micLeaseOwner = owner;
            window.dispatchEvent(new CustomEvent('mic-lease-changed', {
                detail: { owner }
            }));
        }
        if (owner === MIC_LEASE.NONE || S.isMicMuted === true) {
            setVoiceInputLifecycleState('off');
        } else if (owner === MIC_LEASE.GAME) {
            setVoiceInputLifecycleState('suspended');
        } else if (
            S.voiceInputLifecycleState === 'off'
            || S.voiceInputLifecycleState === 'suspended'
        ) {
            setVoiceInputLifecycleState('local_listen');
        }
        sendVoiceInputControlState(false);
        return owner;
    }

    function resolveMicLeaseOwner() {
        if (!S.isRecording) return MIC_LEASE.NONE;
        if (S.gameVoiceSttGateActive) return MIC_LEASE.GAME;
        return MIC_LEASE.CORE;
    }

    function refreshMicLease() {
        // setMicLeaseOwner() already sends the (fingerprint-deduped) control
        // snapshot — no extra send here.
        return setMicLeaseOwner(resolveMicLeaseOwner());
    }

    function canUploadOrdinaryMicFrame() {
        if (refreshMicLease() !== MIC_LEASE.CORE) return false;
        const state = currentVoiceInputControlState();
        return !state.hard_muted && !state.focus_suppressed;
    }

    // ======================== DOM 辅助 ========================

    function micButton()          { return document.getElementById('micButton'); }
    function muteButton()         { return document.getElementById('muteButton'); }
    function screenButton()       { return document.getElementById('screenButton'); }
    function stopButton()         { return document.getElementById('stopButton'); }
    function resetSessionButton() { return document.getElementById('resetSessionButton'); }
    function statusElement()      { return document.getElementById('status'); }

    // ======================== 屏幕共享开关按钮（设置面板内嵌） ========================
    // 开关按钮从屏幕源子窗口底部移到「屏幕共享」与「选择麦克风」两个设置项中间；
    // 启用时播放像素扫过动画（参考视频按钮的像素填充效果）。
    // 共享状态以隐藏的 #screenButton 的 .active class 为准（见 common_ui.js）。

    var shareToggleButtonRegistry = [];
    var shareToggleStateObserver = null;
    var shareToggleObserverRetryTimer = null;

    function isScreenShareActive() {
        var btn = screenButton();
        return !!(btn && btn.classList.contains('active'));
    }

    function injectShareToggleStyles() {
        if (document.getElementById('neko-share-toggle-styles')) return;
        var style = document.createElement('style');
        style.id = 'neko-share-toggle-styles';
        style.textContent = [
            // 未启用：白色胶囊轨道（仿参考视频）
            '.neko-share-toggle-btn{position:relative;overflow:hidden;width:100%;box-sizing:border-box;min-height:44px;padding:10px 48px;margin:4px 0 6px;border:1px solid rgba(0,0,0,.07);border-radius:999px;background:#f4f4f7;color:var(--neko-popup-text,#333);cursor:pointer;font-size:14px;font-weight:600;pointer-events:auto;transition:color .2s ease,box-shadow .2s ease,transform .1s ease;}',
            '.neko-share-toggle-btn:hover{box-shadow:inset 0 0 0 1px rgba(0,0,0,.05);}',
            '.neko-share-toggle-btn:active{transform:scale(.97);}',
            '.neko-share-toggle-btn:disabled{opacity:.6;cursor:default;}',
            // 未开语音会话时点击的抖动提示（明确反馈「点到了但不能用」）
            '@keyframes nekoShareToggleNudge{0%,100%{transform:translateX(0);}25%{transform:translateX(-3px);}75%{transform:translateX(3px);}}',
            '.neko-share-toggle-btn.is-nudged{animation:nekoShareToggleNudge .12s ease 2;}',
            '.neko-share-toggle-btn.is-active{color:#fff;}',
            // 右端的紫色目标点
            '.neko-share-toggle-btn .neko-share-toggle-goal{position:absolute;z-index:0;top:50%;right:16px;width:6px;height:6px;margin-top:-3px;border-radius:50%;background:#8a5ce8;pointer-events:none;}',
            '.neko-share-toggle-btn .neko-share-toggle-label{position:relative;z-index:1;display:block;text-align:center;pointer-events:none;transition:color .2s ease;}',
            // 文字延迟变白：等像素填充波前扫到中部再切换，避免白字落在白轨道上
            '.neko-share-toggle-btn.is-active .neko-share-toggle-label{transition:color .3s ease .35s;}',
            // 白色滑块：默认在左端，启用后滑到右端（始终盖在像素层之上）
            '.neko-share-toggle-btn .neko-share-toggle-knob{position:absolute;z-index:2;top:4px;bottom:4px;left:4px;width:32px;border-radius:10px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.18);pointer-events:none;transition:left .4s ease;}',
            '.neko-share-toggle-btn.is-active .neko-share-toggle-knob{left:calc(100% - 36px);}',
            // 像素填充画布：低分辨率画布经 CSS 放大 + pixelated，呈现马赛克块感
            '.neko-share-toggle-btn canvas.neko-share-toggle-fill{position:absolute;inset:0;z-index:0;width:100%;height:100%;pointer-events:none;opacity:0;transition:opacity .12s linear;image-rendering:pixelated;}',
            '.neko-share-toggle-btn.is-active canvas.neko-share-toggle-fill{opacity:1;}',
            // 迷你版：嵌在「屏幕共享」设置行右侧的行内胶囊开关（未开启为灰色轨道 + 白色旋钮）
            '.neko-share-toggle-btn.neko-share-toggle-mini{display:inline-block;width:64px;min-height:26px;height:26px;padding:0;margin:0;flex-shrink:0;align-self:center;cursor:pointer;background:#e2e2e8;border-color:rgba(0,0,0,.05);}',
            '.neko-share-toggle-mini .neko-share-toggle-label{display:none;}',
            '.neko-share-toggle-mini .neko-share-toggle-knob{width:18px;top:3px;bottom:3px;left:3px;border-radius:7px;}',
            '.neko-share-toggle-mini.is-active .neko-share-toggle-knob{left:calc(100% - 21px);}',
            '.neko-share-toggle-mini .neko-share-toggle-goal{right:8px;width:5px;height:5px;margin-top:-2.5px;}',
            '.neko-share-toggle-btn.is-busy{opacity:.6;cursor:default;}'
        ].join('\n');
        document.head.appendChild(style);
    }

    // ---- 像素溶解引擎：仿参考视频的随机马赛克扫过效果 ----
    // 色板以中深紫为主，浅紫仅作零星高光（与参考视频一致）
    var SHARE_PIXEL_PALETTE = ['#8a5ce8', '#8f63ec', '#9772f0', '#9772f0', '#a181f5', '#a181f5', '#b79fff', '#cdbdff'];
    var SHARE_PIXEL_CELL_PX = 3;      // 每个像素块的 CSS 尺寸
    var SHARE_PIXEL_JITTER = 0.2;     // 填充前沿的随机抖动幅度（产生锯齿边缘与前置散点）
    var SHARE_PIXEL_FADE = 0.15;      // 前沿软过渡宽度（像素透明度渐显，形成渐变边）
    var SHARE_PIXEL_MIN_ALPHA = 0.3;  // 左端最终透明度上限（形成视频的浅紫渐变尾）
    var SHARE_PIXEL_FILL_MS = 1300;   // 启用扫过时长
    var SHARE_PIXEL_SHIMMER_MS = 100; // 填满后像素闪烁间隔

    function createSharePixelFx(canvas) {
        var ctx = canvas.getContext('2d');
        var cols = 0;
        var rows = 0;
        var seeds = null;   // 前沿抖动随机数
        var tints = null;   // 每格颜色索引
        var spans = null;   // 少量格子画成 2x2，模拟视频里大小不一的块
        var progress = 0;
        var rafId = null;
        var shimmerTimer = null;

        function resize() {
            var host = canvas.parentElement;
            var w = host ? host.clientWidth : 0;
            var h = host ? host.clientHeight : 0;
            var nextCols = Math.max(1, Math.round(w / SHARE_PIXEL_CELL_PX));
            var nextRows = Math.max(1, Math.round(h / SHARE_PIXEL_CELL_PX));
            if (nextCols === cols && nextRows === rows && seeds) return;
            cols = nextCols;
            rows = nextRows;
            canvas.width = cols;
            canvas.height = rows;
            var count = cols * rows;
            seeds = new Float32Array(count);
            tints = new Uint8Array(count);
            spans = new Uint8Array(count);
            for (var i = 0; i < count; i++) {
                seeds[i] = Math.random();
                tints[i] = (Math.random() * SHARE_PIXEL_PALETTE.length) | 0;
                spans[i] = Math.random() < 0.12 ? 1 : 0;
            }
        }

        function draw() {
            resize();
            ctx.clearRect(0, 0, cols, rows);
            if (progress <= 0) return;
            var spread = 1 - SHARE_PIXEL_JITTER;
            for (var y = 0; y < rows; y++) {
                for (var x = 0; x < cols; x++) {
                    var i = y * cols + x;
                    var xNorm = cols <= 1 ? 1 : x / (cols - 1);
                    // 从右向左推进；阈值叠加随机抖动形成不规则前沿
                    var threshold = (1 - xNorm) * spread + seeds[i] * SHARE_PIXEL_JITTER;
                    // 前沿软过渡：刚越线的像素半透明，逐渐加深（视频的渐变边）
                    var fade = (progress - threshold) / SHARE_PIXEL_FADE;
                    if (fade > 0) {
                        var alpha = fade >= 1 ? 1 : fade;
                        // 越靠左透明度越低，填满后左端保留浅紫渐变尾
                        alpha *= SHARE_PIXEL_MIN_ALPHA + (1 - SHARE_PIXEL_MIN_ALPHA) * xNorm;
                        ctx.globalAlpha = alpha;
                        ctx.fillStyle = SHARE_PIXEL_PALETTE[tints[i]];
                        var big = spans[i];
                        ctx.fillRect(x, y, big ? 2 : 1, big ? 2 : 1);
                    }
                }
            }
            ctx.globalAlpha = 1;
        }

        function stopShimmer() {
            if (shimmerTimer) { clearInterval(shimmerTimer); shimmerTimer = null; }
        }

        function startShimmer() {
            stopShimmer();
            // 闪烁点随机出现，但整体沿一个从右向左移动的波前依次点亮（仿参考视频）
            var sweepX = cols - 1;
            var band = Math.max(2, Math.round(cols * 0.15));
            shimmerTimer = setInterval(function () {
                if (!canvas.isConnected) { stopShimmer(); return; }
                // 随机改写少量格子的色阶，形成填满后的闪烁感（位置集中在波前附近）
                var twinkles = Math.max(1, Math.round(cols * rows * 0.025));
                for (var n = 0; n < twinkles; n++) {
                    var x = sweepX + ((Math.random() * band) | 0);
                    if (x >= cols) x = cols - 1;
                    var y = (Math.random() * rows) | 0;
                    var i = y * cols + x;
                    tints[i] = (Math.random() * SHARE_PIXEL_PALETTE.length) | 0;
                }
                draw();
                sweepX -= Math.max(1, Math.round(cols * 0.12));
                if (sweepX < 0) sweepX = cols - 1;
            }, SHARE_PIXEL_SHIMMER_MS);
        }

        function cancelAnimation() {
            if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        }

        function animateTo(target, done) {
            cancelAnimation();
            var from = progress;
            var distance = Math.abs(target - from);
            if (distance <= 0) { if (done) done(); return; }
            var duration = SHARE_PIXEL_FILL_MS * distance;
            var startTime = null;
            function frame(now) {
                if (!canvas.isConnected) { rafId = null; return; }
                if (startTime === null) startTime = now;
                var t = Math.min(1, (now - startTime) / duration);
                progress = from + (target - from) * t;
                draw();
                if (t < 1) {
                    rafId = requestAnimationFrame(frame);
                } else {
                    rafId = null;
                    if (done) done();
                }
            }
            rafId = requestAnimationFrame(frame);
        }

        return {
            activate: function (instant) {
                resize();
                stopShimmer();
                if (instant) {
                    cancelAnimation();
                    progress = 1;
                    draw();
                    startShimmer();
                } else {
                    animateTo(1, startShimmer);
                }
            },
            deactivate: function (instant, done) {
                stopShimmer();
                if (instant) {
                    cancelAnimation();
                    progress = 0;
                    draw();
                    if (done) done();
                } else {
                    animateTo(0, done);
                }
            },
            // 已处于开启状态时再次点击：从头重播一次开启扫过动画
            replay: function () {
                resize();
                stopShimmer();
                progress = 0;
                draw();
                animateTo(1, startShimmer);
            }
        };
    }

    function syncShareToggleButtons(instant) {
        shareToggleButtonRegistry = shareToggleButtonRegistry.filter(function (btn) { return btn.isConnected; });
        var active = isScreenShareActive();
        shareToggleButtonRegistry.forEach(function (btn) {
            if (typeof btn._nekoSetShareActive === 'function') btn._nekoSetShareActive(active, !!instant);
        });
    }

    function ensureShareToggleStateObserver() {
        if (shareToggleStateObserver) return;
        var target = screenButton();
        if (!target) {
            if (!shareToggleObserverRetryTimer) {
                shareToggleObserverRetryTimer = setTimeout(function () {
                    shareToggleObserverRetryTimer = null;
                    ensureShareToggleStateObserver();
                }, 500);
            }
            return;
        }
        shareToggleStateObserver = new MutationObserver(function () { syncShareToggleButtons(false); });
        shareToggleStateObserver.observe(target, { attributes: true, attributeFilter: ['class'] });
    }

    function createScreenShareToggleButton(options) {
        injectShareToggleStyles();
        ensureShareToggleStateObserver();

        var mini = !!(options && options.mini);
        // 用 span + role=button：迷你版会嵌在设置行 <button> 内，原生 button 嵌套是非法 HTML
        var button = document.createElement('span');
        button.className = 'neko-share-toggle-btn' + (mini ? ' neko-share-toggle-mini' : '');
        button.setAttribute('role', 'button');
        button.setAttribute('tabindex', '0');
        button.dataset.nekoScreenShareAction = 'toggle';

        var fill = document.createElement('canvas');
        fill.className = 'neko-share-toggle-fill';
        fill.setAttribute('aria-hidden', 'true');

        var goal = document.createElement('span');
        goal.className = 'neko-share-toggle-goal';
        goal.setAttribute('aria-hidden', 'true');

        var label = document.createElement('span');
        label.className = 'neko-share-toggle-label';

        var knob = document.createElement('span');
        knob.className = 'neko-share-toggle-knob';
        knob.setAttribute('aria-hidden', 'true');

        button.appendChild(fill);
        button.appendChild(goal);
        button.appendChild(label);
        button.appendChild(knob);

        function shareLabel() { return window.t ? window.t('buttons.screenShare') : 'Screen Share'; }
        function stopLabel() { return window.t ? window.t('voiceControl.stopShare') : 'Stop Sharing'; }

        var pixelFx = createSharePixelFx(fill);
        button._nekoSetShareActive = function (active, instant) {
            label.textContent = active ? stopLabel() : shareLabel();
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            if (button._nekoShareActive === active) return;
            button._nekoShareActive = active;
            if (active) {
                button.classList.add('is-active');
                pixelFx.activate(!!instant);
            } else {
                // 反向溶解期间保持画布可见，结束后再隐藏
                pixelFx.deactivate(!!instant, function () {
                    if (!button._nekoShareActive) button.classList.remove('is-active');
                });
                if (instant) button.classList.remove('is-active');
            }
        };

        async function handleToggleClick(event) {
            event.stopPropagation();
            if (button._nekoShareBusy) return;
            var active = isScreenShareActive();
            console.log('[屏幕共享开关] 点击, 当前状态:', active ? '共享中' : '未共享', ', 语音会话:', !!window.isRecording);
            if (active) {
                // 开启状态下每次点击都重播一次开启时的像素扫过动画
                pixelFx.replay();
            }
            button._nekoShareBusy = true;
            button.classList.add('is-busy');
            try {
                if (active && typeof window.stopScreenSharing === 'function') {
                    await window.stopScreenSharing();
                } else if (!active && typeof window.startScreenSharing === 'function') {
                    if (!window.isRecording) {
                        // 抖动提示 + Toast，明确告知需要先开语音会话
                        button.classList.remove('is-nudged');
                        void button.offsetWidth;
                        button.classList.add('is-nudged');
                        setTimeout(function () { button.classList.remove('is-nudged'); }, 300);
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(
                                window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话',
                                3000
                            );
                        }
                        return;
                    }
                    await window.startScreenSharing();
                }
            } finally {
                button._nekoShareBusy = false;
                button.classList.remove('is-busy');
                syncShareToggleButtons(false);
            }
        }

        button.addEventListener('click', handleToggleClick);
        button.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleToggleClick(event);
            }
        });

        shareToggleButtonRegistry = shareToggleButtonRegistry.filter(function (btn) { return btn.isConnected; });
        shareToggleButtonRegistry.push(button);
        // 每次重新显示（弹窗重渲染）时，若共享处于开启状态则重播一次开启动画；未开启则直接落位
        button._nekoSetShareActive(isScreenShareActive(), !isScreenShareActive());
        return button;
    }

    // ======================== 游戏语音 STT Gate ========================

    function getGameVoiceSpeechRecognition() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    }

    function gameVoiceRequestId() {
        return `game-voice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function logGameVoiceSttDiagnostics(reason) {
        const tracks = S.stream instanceof MediaStream
            ? S.stream.getAudioTracks().map(track => ({
                label: track.label || '',
                enabled: track.enabled,
                muted: track.muted,
                readyState: track.readyState
            }))
            : [];
        console.log('[GameVoiceSTT][Diag] env:', {
            reason,
            speechRecognition: !!getGameVoiceSpeechRecognition(),
            secureContext: !!window.isSecureContext,
            protocol: window.location ? window.location.protocol : '',
            visibility: document.visibilityState,
            selectedMicrophoneId: S.selectedMicrophoneId || '',
            ordinaryStreamTracks: tracks
        });
        if (S.selectedMicrophoneId) {
            console.warn('[GameVoiceSTT][Diag] SpeechRecognition 不能指定 selectedMicrophoneId，会使用浏览器默认麦克风；若默认麦不是当前项目麦，可能 no-speech。');
        }
        if (navigator.permissions && typeof navigator.permissions.query === 'function') {
            navigator.permissions.query({ name: 'microphone' }).then(function (status) {
                console.log('[GameVoiceSTT][Diag] microphone permission:', status && status.state);
            }).catch(function (error) {
                console.log('[GameVoiceSTT][Diag] microphone permission query unavailable:', error && error.message ? error.message : error);
            });
        }
        if (navigator.mediaDevices && typeof navigator.mediaDevices.enumerateDevices === 'function') {
            navigator.mediaDevices.enumerateDevices().then(function (devices) {
                const audioInputs = devices
                    .filter(device => device.kind === 'audioinput')
                    .map(device => ({
                        deviceId: device.deviceId,
                        label: device.label || '',
                        groupId: device.groupId || ''
                    }));
                console.log('[GameVoiceSTT][Diag] audio inputs:', audioInputs);
            }).catch(function (error) {
                console.log('[GameVoiceSTT][Diag] enumerate audio inputs failed:', error && error.message ? error.message : error);
            });
        }
    }

    function getGameVoiceSttRouteSnapshot() {
        return {
            gameType: S.gameVoiceSttGameType || 'soccer',
            sessionId: S.gameVoiceSttSessionId || S.gameRouteSessionId || ''
        };
    }

    async function submitGameVoiceSttTranscript(transcript, routeSnapshot) {
        const text = String(transcript || '').trim();
        if (!text) return;

        const lanlanName = S.gameRouteLanlanName || (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
        if (!lanlanName) {
            console.warn('[GameVoiceSTT] missing lanlan_name, drop transcript');
            return;
        }

        const frozenRoute = routeSnapshot || getGameVoiceSttRouteSnapshot();
        const gameType = frozenRoute.gameType || 'soccer';
        const sessionId = frozenRoute.sessionId || '';
        const requestId = gameVoiceRequestId();
        console.log(`[GameVoiceSTT] 最终转写 | game=${gameType} request=${requestId} text="${text}"`);
        try {
            const response = await fetch(`/api/game/${encodeURIComponent(gameType)}/route/voice-transcript`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    lanlan_name: lanlanName,
                    session_id: sessionId,
                    transcript: text,
                    request_id: requestId,
                    source: 'main_voice_stt_gate'
                })
            });
            const result = await response.json().catch(() => null);
            if (!response.ok) {
                console.warn('[GameVoiceSTT] transcript route failed:', response.status, result);
                return;
            }
            if (result && result.handled === false && result.reason === 'session_id_mismatch') {
                console.info('[GameVoiceSTT] session mismatch, restarting hidden STT gate with the current route session');
                stopGameVoiceSttGate({ keepActive: true, restoreOrdinaryMic: false });
                if (S.gameVoiceSttGateActive && S.isRecording && !S.isMicMuted) {
                    S.gameVoiceSttRestartTimer = setTimeout(startGameVoiceSttGate, 250);
                }
                return;
            }
            if (result && result.handled === false && result.reason === 'game_route_inactive') {
                console.info('[GameVoiceSTT] game route inactive, stopping hidden STT gate');
                stopGameVoiceSttGate();
                return;
            }
            console.log(`[GameVoiceSTT] 已提交足球路由 | game=${gameType} request=${requestId} handled=${result ? result.handled !== false : 'unknown'} text="${text}"`);
        } catch (error) {
            console.warn('[GameVoiceSTT] transcript submit failed:', error);
        }
    }

    function releaseOrdinaryMicCaptureForGameVoiceSttGate() {
        if (S.workletNode) {
            try { S.workletNode.disconnect(); } catch (_) { /* noop */ }
            S.workletNode = null;
        }
        S.inputAnalyser = null;
        S.micGainNode = null;

        if (S.stream instanceof MediaStream) {
            S.stream.getTracks().forEach(track => track.stop());
            S.stream = null;
        }

        if (S.audioContext) {
            const context = S.audioContext;
            S.audioContext = null;
            if (context.state !== 'closed') {
                context.close().catch((error) => console.warn('[GameVoiceSTT] close ordinary audio context failed:', error));
            }
        }

        stopSilenceDetection();
    }

    function restoreOrdinaryMicCaptureAfterGameVoiceSttFailure(reason, error) {
        console.warn('[GameVoiceSTT] restoring ordinary mic capture after STT gate failure:', reason, error || '');
        stopGameVoiceSttGate({ restoreOrdinaryMic: false });
        if (S.isRecording && typeof startMicCapture === 'function') {
            Promise.resolve(startMicCapture()).catch(function (restoreError) {
                console.warn('[GameVoiceSTT] restore ordinary mic capture failed:', restoreError);
            });
        }
    }

    function restoreOrdinaryMicCaptureAfterGameVoiceSttStop(reason) {
        if (!S.isRecording || typeof startMicCapture !== 'function') {
            return;
        }
        const ordinaryPipelineAlive = !!(S.stream && S.audioContext && S.workletNode);
        if (ordinaryPipelineAlive) {
            return;
        }
        Promise.resolve(startMicCapture()).catch(function (restoreError) {
            console.warn(`[GameVoiceSTT] restore ordinary mic capture after ${reason || 'stop'} failed:`, restoreError);
        });
    }

    function startGameVoiceSttGate() {
        if (!S.gameVoiceSttGateActive || !S.isRecording || S.isMicMuted) {
            return false;
        }
        setMicLeaseOwner(MIC_LEASE.GAME);
        if (S.gameVoiceSttListening) {
            releaseOrdinaryMicCaptureForGameVoiceSttGate();
            return true;
        }

        const SpeechRecognition = getGameVoiceSpeechRecognition();
        if (!SpeechRecognition) {
            if (!S.gameVoiceSttUnsupportedNotified) {
                S.gameVoiceSttUnsupportedNotified = true;
                console.warn('[GameVoiceSTT] 当前浏览器不支持 SpeechRecognition，无法启动游戏语音 STT gate');
                if (typeof window.showStatusToast === 'function') {
                    window.showStatusToast(window.t ? window.t('app.gameVoiceSttNotSupported') : '当前浏览器不支持游戏语音转写，请暂时使用文本输入。', 4000);
                }
            }
            return false;
        }

        const routeSnapshot = getGameVoiceSttRouteSnapshot();
        if (S.gameVoiceSttRecognition) {
            try { S.gameVoiceSttRecognition.abort(); } catch (_) { /* noop */ }
            S.gameVoiceSttRecognition = null;
        }

        const recognition = new SpeechRecognition();
        recognition.lang = (function () {
            const raw = (typeof window.i18next !== 'undefined' && window.i18next.language)
                || (typeof navigator !== 'undefined' && navigator.language)
                || 'zh-CN';
            const tag = String(raw).toLowerCase();
            if (tag.startsWith('zh-tw') || tag === 'zh-hant' || tag.startsWith('zh-hk')) return 'zh-TW';
            if (tag.startsWith('zh')) return 'zh-CN';
            if (tag.startsWith('en')) return 'en-US';
            if (tag.startsWith('ja')) return 'ja-JP';
            if (tag.startsWith('ko')) return 'ko-KR';
            if (tag.startsWith('ru')) return 'ru-RU';
            if (tag.startsWith('es')) return 'es-ES';
            if (tag.startsWith('pt')) return 'pt-BR';
            return raw;
        })();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition._gameVoiceRouteSnapshot = routeSnapshot;
        recognition.onstart = function () {
            if (S.gameVoiceSttRecognition !== recognition) return;
            S.gameVoiceSttListening = true;
            S.gameVoiceSttStopping = false;
            console.log('[GameVoiceSTT][Diag] recognition start');
        };
        recognition.onaudiostart = function () {
            console.log('[GameVoiceSTT][Diag] audio start');
        };
        recognition.onsoundstart = function () {
            console.log('[GameVoiceSTT][Diag] sound start');
        };
        recognition.onspeechstart = function () {
            console.log('[GameVoiceSTT][Diag] speech start');
        };
        recognition.onspeechend = function () {
            console.log('[GameVoiceSTT][Diag] speech end');
        };
        recognition.onsoundend = function () {
            console.log('[GameVoiceSTT][Diag] sound end');
        };
        recognition.onaudioend = function () {
            console.log('[GameVoiceSTT][Diag] audio end');
        };
        recognition.onnomatch = function (event) {
            console.warn('[GameVoiceSTT][Diag] no match:', event);
        };
        recognition.onresult = function (event) {
            let finalText = '';
            const startIndex = typeof event.resultIndex === 'number' ? event.resultIndex : 0;
            console.log('[GameVoiceSTT][Diag] result event:', {
                resultIndex: startIndex,
                resultCount: event.results ? event.results.length : 0
            });
            for (let i = startIndex; i < event.results.length; i++) {
                const result = event.results[i];
                if (!result || result.isFinal === false) continue;
                finalText += (result[0] && result[0].transcript) || '';
            }
            if (finalText.trim()) {
                void submitGameVoiceSttTranscript(finalText, recognition._gameVoiceRouteSnapshot);
            }
        };
        recognition.onerror = function (event) {
            const errorCode = (event && event.error) || 'unknown';
            console.warn('[GameVoiceSTT] recognition error:', errorCode, event);
            if (errorCode === 'no-speech') {
                console.warn('[GameVoiceSTT][Diag] no-speech: 识别器启动了但没有形成可用语音。优先检查默认麦克风是否正确、是否有 audio/sound/speech start 日志。');
            }
            if (errorCode === 'not-allowed' || errorCode === 'service-not-allowed') {
                if (typeof window.showStatusToast === 'function') {
                    window.showStatusToast(window.t ? window.t('app.gameVoiceSttMicPermissionDenied') : '游戏语音转写没有麦克风权限，请检查浏览器权限。', 4000);
                }
                restoreOrdinaryMicCaptureAfterGameVoiceSttFailure(errorCode, event);
            }
        };
        recognition.onend = function () {
            if (S.gameVoiceSttRecognition !== recognition) return;
            S.gameVoiceSttListening = false;
            if (S.gameVoiceSttRestartTimer) {
                clearTimeout(S.gameVoiceSttRestartTimer);
                S.gameVoiceSttRestartTimer = null;
            }
            if (S.gameVoiceSttGateActive && S.isRecording && !S.isMicMuted && !S.gameVoiceSttStopping) {
                S.gameVoiceSttRestartTimer = setTimeout(startGameVoiceSttGate, 250);
            }
            S.gameVoiceSttStopping = false;
        };
        S.gameVoiceSttRecognition = recognition;

        try {
            S.gameVoiceSttStopping = false;
            logGameVoiceSttDiagnostics('start');
            releaseOrdinaryMicCaptureForGameVoiceSttGate();
            S.gameVoiceSttRecognition.start();
            S.gameVoiceSttListening = true;
            console.log(`[GameVoiceSTT] STT gate 已启动 | game=${S.gameVoiceSttGameType || 'soccer'} recording=${!!S.isRecording} ordinary_mic=released`);
            return true;
        } catch (error) {
            if (error && error.name === 'InvalidStateError') {
                S.gameVoiceSttListening = true;
                console.log('[GameVoiceSTT] STT gate 已在运行');
                return true;
            }
            console.warn('[GameVoiceSTT] recognition start failed:', error);
            S.gameVoiceSttListening = false;
            restoreOrdinaryMicCaptureAfterGameVoiceSttFailure('recognition_start_failed', error);
            return false;
        }
    }

    function stopGameVoiceSttGate(options) {
        const keepActive = options && options.keepActive === true;
        const restoreOrdinaryMic = !(options && options.restoreOrdinaryMic === false);
        if (!keepActive) {
            S.gameVoiceSttGateActive = false;
            S.gameVoiceSttGameType = '';
            S.gameVoiceSttSessionId = '';
        }
        if (S.gameVoiceSttRestartTimer) {
            clearTimeout(S.gameVoiceSttRestartTimer);
            S.gameVoiceSttRestartTimer = null;
        }
        S.gameVoiceSttStopping = true;
        const recognition = S.gameVoiceSttRecognition;
        S.gameVoiceSttRecognition = null;
        if (recognition) {
            try {
                recognition.stop();
            } catch (error) {
                try { recognition.abort(); } catch (_) { /* noop */ }
            }
        }
        S.gameVoiceSttListening = false;
        S.gameVoiceSttStopping = false;
        if (!keepActive && restoreOrdinaryMic) {
            restoreOrdinaryMicCaptureAfterGameVoiceSttStop('gate stop');
        }
        refreshMicLease();
    }

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
                S.voiceChatActive = false;
                const textInputArea = document.getElementById('text-input-area');
                if (textInputArea) {
                    textInputArea.classList.remove('hidden');
                }
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
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

    // ======================== 降噪开关 ========================

    function saveNoiseReductionSetting() {
        try {
            localStorage.setItem('neko_noise_reduction', S.noiseReductionEnabled ? '1' : '0');
        } catch (e) { }
        // 同步到后端 conversation-settings
        try {
            fetch('/api/config/conversation-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ noiseReductionEnabled: S.noiseReductionEnabled })
            });
        } catch (e) { }
    }

    function loadNoiseReductionSetting() {
        try {
            var saved = localStorage.getItem('neko_noise_reduction');
            if (saved !== null) {
                S.noiseReductionEnabled = saved === '1';
            }
        } catch (e) { }
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

        // mute 状态下 audio 在 worklet onmessage 处被丢弃，根本没送到后端，
        // 此时 analyser 仍连在增益链上能听到本地噪声（键盘/风扇/呼吸）。
        // 把这部分 RMS 当 0：不读、不写 userRecentSpeechTime，避免 proactive
        // guard 把"本地噪声"误判成"用户在说话"导致语音模式 nudge 被静默
        // skip 卡死 (`_isUserRecentlySpeaking()` 8s 窗口拖尾)。
        if (S.isMicMuted) {
            requestAnimationFrame(monitorInputVolume);
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
            // C: 为前端 proactive guard 持续打点"最近一次有声音"。
            // 阈值与下面 hasSoundDetected 共用 0.01；这里每帧都写（~16ms 一次），
            // 不做去抖，保证 proactive tick 能读到最新值。与后端
            // _user_recent_activity_time 对称：不等 sustain、不等 VAD 判定，
            // 只要麦克风真的收到过声音就算"用户可能在说话"。
            S.userRecentSpeechTime = Date.now();
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

    /**
     * Open the capture pipeline for ONE microphone-start attempt.
     *
     * ``startToken`` is that attempt's identity, taken from micStartGeneration
     * before its first await. Returns true when the pipeline was committed and
     * false when the attempt was superseded and unwound -- the caller MUST
     * check, because an unwound attempt has torn the hardware down and must not
     * continue into its success path.
     *
     * A caller that passes no token cannot prove it is current, so it unwinds.
     * That is deliberate: this whole subsystem is fail-closed, and the only
     * consumer is startMicCapture below (the module export exists for tests).
     */
    async function startAudioWorklet(mediaStream, startToken) {
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

                if (!canUploadOrdinaryMicFrame()) {
                    return;
                }

                if (S.isRecording && S.socket && S.socket.readyState === WebSocket.OPEN) {
                    // 8-byte header: ASCII "NEKO" + little-endian sample rate，
                    // 后续直接附 PCM16，避免每帧 JSON 数组的带宽与 GC 开销。
                    const pcm16 = audioData instanceof Int16Array
                        ? audioData
                        : new Int16Array(audioData);
                    const frame = new ArrayBuffer(8 + pcm16.byteLength);
                    const header = new DataView(frame);
                    header.setUint8(0, 0x4E);
                    header.setUint8(1, 0x45);
                    header.setUint8(2, 0x4B);
                    header.setUint8(3, 0x4F);
                    header.setUint32(4, targetSampleRate, true);
                    // 字节级拷贝按平台字节序输出 PCM，而 wire 格式要求
                    // little-endian。所有主流浏览器 / JS 引擎都是 LE，这里
                    // 显式依赖该假设以保持每帧热路径零逐样本开销；若未来
                    // 出现 BE 平台，需改为 DataView 逐样本 setInt16(LE)。
                    new Uint8Array(frame, 8).set(new Uint8Array(
                        pcm16.buffer,
                        pcm16.byteOffset,
                        pcm16.byteLength
                    ));
                    S.socket.send(frame);
                }
            };

            // 连接节点：gainNode → workletNode（音频经过增益处理后发送）
            S.micGainNode.connect(S.workletNode);

            // 用户主动开麦，意味着要讲话；focus mode 的 isPlaying guard 此刻必须让路。
            // 切档案后自动触发的 greeting 音频播完如果没把 isPlaying 复位（finalize
            // 路径的前置条件没兜住就会粘住），下一次开麦每一帧都会被 focus 拦掉，
            // 表现为"Electron 显示可以说话但 STT 无反应"。用户此刻的意图是明确的，
            // 不管 flag 是粘住还是真在播 AI 音频，都应该让位给用户输入。
            S.isPlaying = false;

            // Last gate before the commit. Everything above only awaited; this
            // is where the microphone actually becomes live and where
            // refreshMicLease() would re-claim the lease. A text takeover (or
            // any fail-closed verdict) that landed while getUserMedia() and
            // addModule() were in flight found S.isRecording still false, so its
            // stopRecording() early-returned and could not prevent this. Unwind
            // instead of committing: without this the pending start re-claims a
            // lease the backend just revoked and feeds a blocked route.
            if (startToken !== micStartGeneration || S.voiceInputRouteBlocked === true) {
                console.log('[App] microphone start was superseded while opening; unwinding');
                try {
                    if (mediaStream && typeof mediaStream.getTracks === 'function') {
                        mediaStream.getTracks().forEach(track => track.stop());
                    }
                } catch (_) {
                    // best-effort teardown
                }
                if (S.stream) {
                    try {
                        S.stream.getTracks().forEach(track => track.stop());
                    } catch (_) {
                        // best-effort teardown
                    }
                    S.stream = null;
                }
                if (S.audioContext) {
                    if (S.audioContext.state !== 'closed') {
                        S.audioContext.close();
                    }
                    S.audioContext = null;
                }
                S.workletNode = null;
                S.micGainNode = null;
                S.inputAnalyser = null;
                stopSilenceDetection();
                // Deliberately NOT refreshMicLease(): the lease is the
                // backend's now, and re-emitting a snapshot from a window that
                // never started recording is exactly the re-claim being
                // prevented. S.isRecording was never set, so nothing to reset.
                //
                // false, not a bare return: the caller awaits this and would
                // otherwise run its whole success path -- disabling the mic
                // button, toasting "speaking", lighting the floating button and
                // silencing proactive chat -- against hardware that no longer
                // exists.
                return false;
            }

            // 所有初始化成功后，才标记为录音状态
            S.isRecording = true;
            window.isRecording = true;
            refreshMicLease();
            return true;

        } catch (err) {
            console.error('加载AudioWorklet失败:', err);
            console.dir(err);
            window.showStatusToast(window.t ? window.t('app.audioWorkletFailed') : 'AudioWorklet加载失败', 5000);
            stopSilenceDetection();
        }
    }

    // ======================== 录音开始/停止 ========================

    // 开麦，按钮on click
    function abortVoiceStartForBlockedRoute() {
        // Unwind the "starting voice" UI after startMicCapture refused a
        // fail-closed route. Deliberately NOT a thrown error: the generic
        // catch would replace the accurate ASR failure toast with a generic
        // "session start failed".
        const _mic = micButton();
        const _mute = muteButton();
        const _screen = screenButton();
        if (_mic) {
            _mic.classList.remove('recording');
            _mic.classList.remove('active');
            _mic.disabled = false;
        }
        if (_mute) _mute.disabled = true;
        if (_screen) _screen.disabled = true;
        // Also cancel a start still inside its getUserMedia/addModule window:
        // clearing S.isRecording cannot reach one that has not set it yet.
        invalidatePendingMicStart();
        S.isRecording = false;
        window.isRecording = false;
        S.voiceChatActive = false;
        S.voiceStartPending = false;
        window.isMicStarting = false;
        if (typeof window.hideVoicePreparingToast === 'function') {
            window.hideVoicePreparingToast();
        }
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) textInputArea.classList.remove('hidden');
        if (typeof window.syncVoiceChatComposerHidden === 'function') {
            window.syncVoiceChatComposerHidden(false);
        }
        if (typeof window.syncFloatingMicButtonState === 'function') {
            window.syncFloatingMicButtonState(false);
        }
        refreshMicLease();
    }

    async function startMicCapture() {
        // Refuse to open the hardware microphone onto a route the backend has
        // already fail-closed. This is THE guard that closes the startup-failure
        // hole: on a cold voice start the mic is opened only AFTER
        // session_started, i.e. after the ASR_INDEPENDENT_* failure status, so a
        // server-side lease revoke has nothing to revoke yet -- and this
        // function's own refreshMicLease() would re-claim the lease from
        // scratch anyway (_handle_voice_input_control enforces only generation
        // monotonicity, and the revoke reset the generation to -1, so the next
        // client snapshot wins unconditionally). Placed at the top rather than
        // at the two await-sessionStartPromise call sites because three more
        // callers -- the device-change restore paths below -- can also reopen
        // the mic on a dead route, and one guard covers all five.
        if (S.voiceInputRouteBlocked === true) {
            console.log('[App] voice route is fail-closed; refusing to open the microphone');
            return;
        }
        // Claim this attempt BEFORE the first await. Anything that invalidates
        // pending starts from here on makes the commit at the end of
        // startAudioWorklet a no-op, which is the only way to cover the
        // getUserMedia() half of the window as well.
        micStartGeneration += 1;
        const micStartToken = micStartGeneration;
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
            if (!window.appUtils.isMobile() && typeof window.syncVoiceChatComposerHidden === 'function') {
                window.syncVoiceChatComposerHidden(true);
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

            const micStartCommitted = await startAudioWorklet(S.stream, micStartToken);
            if (!micStartCommitted) {
                // Superseded or fail-closed while opening: the hardware is
                // already torn down, so restore the pre-start UI and leave
                // WITHOUT the success path below. Not an error -- no toast, and
                // nothing to throw at a caller who did nothing wrong.
                if (_mic) {
                    _mic.classList.remove('recording');
                    _mic.classList.remove('active');
                }
                const cancelledTextInputArea = document.getElementById('text-input-area');
                if (cancelledTextInputArea) {
                    cancelledTextInputArea.classList.remove('hidden');
                }
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }
                if (typeof window.syncFloatingMicButtonState === 'function') {
                    window.syncFloatingMicButtonState(false);
                }
                return;
            }
            if (S.gameVoiceSttGateActive) {
                startGameVoiceSttGate();
            }

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

            const hasOuterVoiceStartLifecycle = !!(S.voiceStartPending || window.isMicStarting);

            if (_mic) {
                _mic.classList.remove('recording');
                _mic.classList.remove('active');
            }
            if (!hasOuterVoiceStartLifecycle) {
                S.isRecording = false;
                window.isRecording = false;
                S.voiceChatActive = false;
                const textInputArea = document.getElementById('text-input-area');
                if (textInputArea) {
                    textInputArea.classList.remove('hidden');
                }
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }
            }
            stopGameVoiceSttGate({ restoreOrdinaryMic: false });
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
        S.voiceChatActive = false;
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) textInputArea.classList.remove('hidden');
        if (typeof window.syncVoiceChatComposerHidden === 'function') {
            window.syncVoiceChatComposerHidden(false);
        }

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
    function stopRecording(options) {
        options = options || {};
        const notifyServer = options.notifyServer !== false;
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
        stopGameVoiceSttGate({ restoreOrdinaryMic: false });
        if (typeof window.removeExternalAsrPreview === 'function') {
            window.removeExternalAsrPreview();
        }
        // Ordinary user stop must also drop the independent-ASR route flags.
        // Only failure paths (BLOCKED / terminal ASR_INDEPENDENT_* statuses in
        // app-websocket.js) reset them otherwise, so the mic settings hint
        // would keep claiming "Independent ASR active" after the session
        // ended. The next voice session re-derives both fields from fresh
        // ASR_INDEPENDENT_* status events (lifecycle.py _start_session_activate
        // re-runs the route on every start_session).
        S.independentAsrActive = false;
        S.independentAsrProvider = '';
        if (!S.isRecording) return;

        S.isRecording = false;
        window.isRecording = false;
        refreshMicLease();
        window.currentGeminiMessage = null;

        // 重置语音模式用户转录合并追踪
        S.lastVoiceUserMessage = null;
        S.lastVoiceUserMessageTime = 0;

        // 清理 AI 回复相关的队列和缓冲区
        window._realisticGeminiQueue = [];
        window._realisticGeminiBuffer = '';
        window._geminiTurnFullText = '';
        window._geminiTurnEndSealed = false;
        window._pendingMusicCommand = '';
        window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
        window.currentTurnGeminiBubbles = [];
        window._isProcessingRealisticQueue = false;
        window._realisticProcessingOwner = null;

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
        if (notifyServer && S.socket && S.socket.readyState === WebSocket.OPEN) {
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
        // 时域采样 buffer 提到闭包级复用，避免每帧分配 ~8KB Float32Array
        // 在 60fps 下产生 ~480KB/s 的 GC 抖动。
        let timeDomainBuffer = null;

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
                // 用时域数据反映 worklet/AI 实际收到的线性振幅。
                // 频域 + 默认 dB 刻度（-100..-30dB）会在人声常见电平就饱和，
                // 软件增益和过载在条上看不出区别，正是用户反馈的根因。
                //
                // 必须用 getFloatTimeDomainData 而不是 byte：byte 量化步长 1/128，
                // byte=255 实际覆盖 [127/128, ∞) 浮点区间，loud-but-clean 信号
                // (峰值 0.99 但 worklet 不会硬切) 也会被误判成 clip。
                const fftSize = S.inputAnalyser.fftSize;
                if (!timeDomainBuffer || timeDomainBuffer.length !== fftSize) {
                    timeDomainBuffer = new Float32Array(fftSize);
                }
                S.inputAnalyser.getFloatTimeDomainData(timeDomainBuffer);

                let peak = 0;
                let sumSq = 0;
                let clippedCount = 0;
                for (let i = 0; i < fftSize; i++) {
                    const val = timeDomainBuffer[i];
                    const abs = val < 0 ? -val : val;
                    if (abs > peak) peak = abs;
                    sumSq += val * val;
                    // worklet 的 `Math.max(-1, Math.min(1, x))*0x7FFF` 只在浮点
                    // 严格越过 ±1 时才硬切。0.999 留一点浮点比较容差。
                    if (abs >= 0.999) clippedCount++;
                }
                const rms = Math.sqrt(sumSq / fftSize);

                // 显示用 peak（更直观地反映"接近削顶"的距离），
                // 状态判定结合 RMS：信号能量高于 noise floor 才进入分级。
                const volumePercent = Math.min(100, peak * 100);
                // 一帧内 >=0.5% 样本撞到 ±1 视作过载（≈10/2048）。worklet
                // 的 `Math.max(-1, Math.min(1, x))*0x7FFF` 在这个边界硬切，
                // 失真无关用户是否说话，所以唯一无歧义的红色告警就是 clip。
                const isClipping = clippedCount >= fftSize * 0.005;
                // hasSignal：RMS 高于后端 AGC noise floor（0.015）的半档，
                // 视作"用户在说话"——只有这种情况才对偏低/正常做颜色提示，
                // 没说话时不能用警告色把用户吓到。
                const hasSignal = rms >= 0.008;
                const lowVolume = hasSignal && peak < 0.15;
                // high 必须门控 hasSignal：静默期键盘/桌面敲击等瞬态噪声
                // peak 可能短暂 > 0.85 但 RMS 仍低于 noise floor，没有 hasSignal
                // 守住会让"等待中"被误判为"音量较高"。
                const high = hasSignal && !isClipping && peak > 0.85;

                // 更新音量条（条宽始终跟着 peak，没说话时自然就短）
                cachedBarFill.style.width = `${volumePercent}%`;

                // 根据状态设置颜色
                if (isClipping) {
                    cachedBarFill.style.backgroundColor = '#dc3545'; // 红 - 过载（唯一警告）
                } else if (high) {
                    cachedBarFill.style.backgroundColor = '#fd7e14'; // 橙 - 接近过载
                } else if (lowVolume) {
                    cachedBarFill.style.backgroundColor = '#ffc107'; // 黄 - 在说话但偏低
                } else if (hasSignal) {
                    cachedBarFill.style.backgroundColor = '#28a745'; // 绿 - 正常
                } else {
                    cachedBarFill.style.backgroundColor = '#4f8cff'; // 蓝 - 静默/等待
                }

                // 更新状态文字
                if (cachedStatus) {
                    if (isClipping) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeClipping') : '过载';
                        cachedStatus.style.color = '#dc3545';
                    } else if (high) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeHigh') : '音量较高';
                        cachedStatus.style.color = '#fd7e14';
                    } else if (lowVolume) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeLow') : '音量偏低';
                        cachedStatus.style.color = '#ffc107';
                    } else if (hasSignal) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeNormal') : '正常';
                        cachedStatus.style.color = '#28a745';
                    } else {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeWaiting') : '等待声音';
                        cachedStatus.style.color = 'var(--neko-popup-text-sub)';
                    }
                }

                // 更新提示文字（分支顺序与上面的 status 保持一致：
                // clipping → high → lowVolume → hasSignal → idle）
                if (cachedHint) {
                    if (isClipping) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintClipping') : '麦克风增益过高，音频被削顶，AI 可能识别异常，请调低增益';
                    } else if (high) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintHigh') : '音量偏高，建议调低增益';
                    } else if (lowVolume) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintLow') : '音量较低，建议调高增益';
                    } else if (hasSignal) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintOk') : '麦克风工作正常';
                    } else {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintWaiting') : '麦克风正在监听，请说话';
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
    window.abortVoiceStartForBlockedRoute = abortVoiceStartForBlockedRoute;
    window.invalidatePendingMicStart = invalidatePendingMicStart;
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
    window.startGameVoiceSttGate = startGameVoiceSttGate;
    window.stopGameVoiceSttGate = stopGameVoiceSttGate;

    function isTutorialShortcutBlockedForMicMute() {
        if (typeof window.isNekoShortcutBlockedByTutorial === 'function') {
            return window.isNekoShortcutBlockedByTutorial();
        }
        return window.isInTutorial === true;
    }

    window.toggleMicMute = function(showToast = true) {
        if (isTutorialShortcutBlockedForMicMute()) {
            console.log('[Electron Shortcut] toggleMicMute: blocked - tutorial active');
            return S.isMicMuted;
        }
        S.isMicMuted = !S.isMicMuted;
        refreshMicLease();
        if (S.isMicMuted) {
            stopSilenceDetection();
            // 立刻清掉"用户最近在说话"的时间戳。否则 mute 前最后一帧
            // RMS 写入的 userRecentSpeechTime 会在 8s 内继续让
            // _isUserRecentlySpeaking() 返回 true，proactive nudge
            // 在窗口期内仍会被 skip。
            S.userRecentSpeechTime = 0;
        } else if (S.isRecording) {
            startSilenceDetection();
        }
        if (S.gameVoiceSttGateActive) {
            if (S.isMicMuted) {
                stopGameVoiceSttGate({ keepActive: true });
            } else {
                startGameVoiceSttGate();
            }
        }
        window.dispatchEvent(new CustomEvent('mic-mute-state-changed', {
            detail: { muted: S.isMicMuted }
        }));
        if (showToast && typeof window.showStatusToast === 'function') {
            const message = S.isMicMuted
                ? (window.t ? window.t('app.micMuted') : '麦克风已静音')
                : (window.t ? window.t('app.micUnmuted') : '麦克风已取消静音');
            window.showStatusToast(message, 2000);
        }
        return S.isMicMuted;
    };

    window.setMicMuted = function(muted, showToast = false) {
        S.isMicMuted = muted;
        refreshMicLease();
        if (S.isMicMuted) {
            stopSilenceDetection();
            // 与 toggleMicMute 对齐：进入 muted 时清掉时间戳，避免拖尾。
            S.userRecentSpeechTime = 0;
        } else if (S.isRecording) {
            startSilenceDetection();
        }
        if (S.gameVoiceSttGateActive) {
            if (S.isMicMuted) {
                stopGameVoiceSttGate({ keepActive: true });
            } else {
                startGameVoiceSttGate();
            }
        }
        window.dispatchEvent(new CustomEvent('mic-mute-state-changed', {
            detail: { muted: S.isMicMuted }
        }));
        if (showToast && typeof window.showStatusToast === 'function') {
            const message = S.isMicMuted
                ? (window.t ? window.t('app.micMuted') : '麦克风已静音')
                : (window.t ? window.t('app.micUnmuted') : '麦克风已取消静音');
            window.showStatusToast(message, 2000);
        }
    };

    window.isMicMuted = function() {
        return S.isMicMuted;
    };
    // setMicrophoneGain / getMicrophoneGain 已在上方直接定义为 window 属性

    // ======================== 模块导出 ========================
    mod.selectMicrophone = selectMicrophone;
    mod.saveSelectedMicrophone = saveSelectedMicrophone;
    mod.loadSelectedMicrophone = loadSelectedMicrophone;
    mod.saveMicGainSetting = saveMicGainSetting;
    mod.loadMicGainSetting = loadMicGainSetting;
    mod.loadNoiseReductionSetting = loadNoiseReductionSetting;
    mod.saveNoiseReductionSetting = saveNoiseReductionSetting;
    mod.formatGainDisplay = formatGainDisplay;
    mod.startSilenceDetection = startSilenceDetection;
    mod.stopSilenceDetection = stopSilenceDetection;
    mod.monitorInputVolume = monitorInputVolume;
    mod.startAudioWorklet = startAudioWorklet;
    mod.startMicCapture = startMicCapture;
    mod.stopMicCapture = stopMicCapture;
    mod.invalidatePendingMicStart = invalidatePendingMicStart;
    mod.stopRecording = stopRecording;
    mod.startMicVolumeVisualization = startMicVolumeVisualization;
    mod.stopMicVolumeVisualization = stopMicVolumeVisualization;
    mod.updateMicVolumeStatusNow = updateMicVolumeStatusNow;
    mod.startGameVoiceSttGate = startGameVoiceSttGate;
    mod.stopGameVoiceSttGate = stopGameVoiceSttGate;
    mod.refreshMicLease = refreshMicLease;
    mod.sendVoiceInputControlState = sendVoiceInputControlState;
    mod.canUploadOrdinaryMicFrame = canUploadOrdinaryMicFrame;
    mod.setVoiceInputLifecycleState = setVoiceInputLifecycleState;

    // ======================== 麦克风设备列表 UI ========================

    var micPermissionGranted = false;
    var cachedMicDevices = null;

    function ensureMicPopupScrollbarStyle() {
        if (document.getElementById('neko-mic-popup-scrollbar-style')) return;
        var style = document.createElement('style');
        style.id = 'neko-mic-popup-scrollbar-style';
        style.textContent = [
            '#live2d-popup-mic.neko-mic-popup-surface,',
            '#vrm-popup-mic.neko-mic-popup-surface,',
            '#mmd-popup-mic.neko-mic-popup-surface{overflow-y:hidden!important;scrollbar-width:none;}',
            '#live2d-popup-mic.neko-mic-popup-surface::-webkit-scrollbar,',
            '#vrm-popup-mic.neko-mic-popup-surface::-webkit-scrollbar,',
            '#mmd-popup-mic.neko-mic-popup-surface::-webkit-scrollbar,',
            '.neko-mic-popup-scroll::-webkit-scrollbar{width:0;height:0;}',
            '.neko-mic-popup-scroll{scrollbar-width:none;-ms-overflow-style:none;}',
            '.neko-mic-popup-scrollbar-thumb{position:absolute;right:3px;top:0;width:4px;min-height:18px;border-radius:999px;background:rgba(128,128,128,0.55);opacity:0;transition:opacity 120ms ease;pointer-events:none;z-index:2;}',
            '.neko-mic-popup-scrollbar-thumb.is-visible{opacity:1;}'
        ].join('');
        document.head.appendChild(style);
    }

    function attachTransientMicPopupScrollbar(scrollNode, hostNode) {
        var thumb = document.createElement('div');
        thumb.className = 'neko-mic-popup-scrollbar-thumb';
        hostNode.appendChild(thumb);
        var hideTimer = null;
        var frameId = null;
        var updateThumb = function () {
            frameId = null;
            var maxScroll = Math.max(0, scrollNode.scrollHeight - scrollNode.clientHeight);
            if (maxScroll <= 0) {
                thumb.classList.remove('is-visible');
                return;
            }
            var scrollRect = scrollNode.getBoundingClientRect();
            var hostRect = hostNode.getBoundingClientRect();
            var trackTop = scrollRect.top - hostRect.top;
            var trackHeight = scrollNode.clientHeight;
            var thumbHeight = Math.max(18, Math.round((scrollNode.clientHeight / scrollNode.scrollHeight) * trackHeight));
            var thumbTop = trackTop + Math.round((scrollNode.scrollTop / maxScroll) * Math.max(0, trackHeight - thumbHeight));
            thumb.style.height = thumbHeight + 'px';
            thumb.style.transform = 'translateY(' + thumbTop + 'px)';
        };
        var showScrollbar = function () {
            thumb.classList.add('is-visible');
            if (!frameId) frameId = window.requestAnimationFrame(updateThumb);
            if (hideTimer) window.clearTimeout(hideTimer);
            hideTimer = window.setTimeout(function () {
                thumb.classList.remove('is-visible');
                hideTimer = null;
            }, 850);
        };
        scrollNode.addEventListener('scroll', showScrollbar, { passive: true });
        scrollNode.addEventListener('wheel', showScrollbar, { passive: true });
        scrollNode.addEventListener('touchmove', showScrollbar, { passive: true });
        return function cleanupTransientMicPopupScrollbar() {
            if (hideTimer) {
                window.clearTimeout(hideTimer);
                hideTimer = null;
            }
            if (frameId) {
                window.cancelAnimationFrame(frameId);
                frameId = null;
            }
            scrollNode.removeEventListener('scroll', showScrollbar);
            scrollNode.removeEventListener('wheel', showScrollbar);
            scrollNode.removeEventListener('touchmove', showScrollbar);
            if (thumb.parentNode) thumb.parentNode.removeChild(thumb);
        };
    }

    /** 请求麦克风权限并缓存设备列表 */
    async function ensureMicrophonePermission() {
        if (micPermissionGranted && cachedMicDevices && cachedMicDevices.length > 0) {
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
        var popupId = micPopup.id;
        var isPopupAvailable = function () {
            if (!micPopup || !micPopup.isConnected) return false;
            if (popupId && document.getElementById(popupId) !== micPopup) return false;
            return micPopup.style.display === 'flex' && micPopup.style.opacity !== '0';
        };
        if (!isPopupAvailable()) return false;

        try {
            ensureMicPopupScrollbarStyle();
            micPopup.classList.add('neko-mic-popup-surface');
            micPopup.style.minWidth = '220px';
            micPopup.style.width = '220px';
            micPopup.style.maxWidth = '220px';
            micPopup.style.boxSizing = 'border-box';
            micPopup.style.overflowY = 'hidden';
            var audioInputs = cachedMicDevices;
            if (!audioInputs || audioInputs.length === 0 || !micPermissionGranted) {
                audioInputs = await ensureMicrophonePermission();
            }
            if (!isPopupAvailable()) return false;
if (typeof micPopup.__nekoMicScrollbarCleanup === 'function') {
                micPopup.__nekoMicScrollbarCleanup();
                micPopup.__nekoMicScrollbarCleanup = null;
            }
            micPopup.innerHTML = '';

            var hasMicrophoneDevices = audioInputs.length > 0;

            // ===== 双栏布局 =====
            var leftColumn = document.createElement('div');
            leftColumn.className = 'neko-mic-popup-scroll';
            Object.assign(leftColumn.style, { flex: '0 0 100%', width: '100%', minWidth: '0', minHeight: '0', maxWidth: '100%', display: 'flex', flexDirection: 'column', overflowY: 'auto' });
            micPopup.__nekoMicScrollbarCleanup = attachTransientMicPopupScrollbar(leftColumn, micPopup);

            if (micPopup.id) {
                document.querySelectorAll('[data-neko-sidepanel-owner="' + micPopup.id + '"].neko-mic-subwindow').forEach(function (panel) {
                    panel.remove();
                });
            }

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

            // 非线性轨道：thumb 位置走 0..SPEAKER_SLIDER_TRACK_MAX（千分比精度），
            // 经膝点映射成 0-200% 音量，使常规的 0-100% 占满轨道前 3/4、100% 落在锚点处。
            var SPEAKER_SLIDER_TRACK_MAX = 1000;
            // Matches Chromium/Electron's native range thumb; the anchor tick is cosmetic.
            var SPEAKER_SLIDER_THUMB_SIZE = 16;
            var SPEAKER_VOLUME_SNAP_RADIUS = 1;
            var SPEAKER_VOLUME_NORMAL_COLOR = '#4f8cff';
            var SPEAKER_VOLUME_BOOST_COLOR = '#ff9f43';

            function speakerTrackPosFromVolume(vol) {
                return Math.round(window.appUtils.valueToKneeTrack(
                    vol, C.DEFAULT_SPEAKER_VOLUME, C.MAX_SPEAKER_VOLUME, C.SPEAKER_VOLUME_KNEE_RATIO
                ) * SPEAKER_SLIDER_TRACK_MAX);
            }
            function speakerVolumeFromTrackPos(pos) {
                return Math.round(window.appUtils.kneeTrackToValue(
                    pos / SPEAKER_SLIDER_TRACK_MAX, C.DEFAULT_SPEAKER_VOLUME, C.MAX_SPEAKER_VOLUME, C.SPEAKER_VOLUME_KNEE_RATIO
                ));
            }
            var speakerVolumeAnchorTrackPos = speakerTrackPosFromVolume(C.DEFAULT_SPEAKER_VOLUME);
            var speakerSliderPointerActive = false;
            var speakerSliderHadPointerInput = false;
            function speakerThumbAlignedLeft(trackPos) {
                var ratio = trackPos / SPEAKER_SLIDER_TRACK_MAX;
                var halfThumb = SPEAKER_SLIDER_THUMB_SIZE / 2;
                var thumbOffsetPx = halfThumb * (1 - 2 * ratio);
                return 'calc(' + (ratio * 100) + '% + ' + thumbOffsetPx.toFixed(2) + 'px)';
            }
            function snapSpeakerTrackPos(pos) {
                return Math.abs(speakerVolumeFromTrackPos(pos) - C.DEFAULT_SPEAKER_VOLUME) <= SPEAKER_VOLUME_SNAP_RADIUS
                    ? speakerVolumeAnchorTrackPos
                    : pos;
            }
            function applySpeakerTrackPos(trackPos) {
                var newVol = speakerVolumeFromTrackPos(trackPos);
                S.speakerVolume = newVol;
                applySpeakerVolumeVisual(newVol);
                if (S.speakerGainNode) {
                    S.speakerGainNode.gain.setTargetAtTime(newVol / 100, S.speakerGainNode.context.currentTime, 0.05);
                }
            }

            var speakerSlider = document.createElement('input');
            speakerSlider.type = 'range';
            speakerSlider.id = 'speaker-volume-slider';
            speakerSlider.min = '0';
            speakerSlider.max = String(SPEAKER_SLIDER_TRACK_MAX);
            speakerSlider.step = '1';
            speakerSlider.value = String(speakerTrackPosFromVolume(S.speakerVolume));
            Object.assign(speakerSlider.style, { width: '100%', height: '6px', borderRadius: '3px', cursor: 'pointer', accentColor: SPEAKER_VOLUME_NORMAL_COLOR, position: 'relative', zIndex: '2', margin: '0' });

            // 超过标准音量（>100%）时数值与轨道染暖色，把增强区从常规区里区分出来
            function applySpeakerVolumeVisual(vol) {
                var color = vol > C.DEFAULT_SPEAKER_VOLUME ? SPEAKER_VOLUME_BOOST_COLOR : SPEAKER_VOLUME_NORMAL_COLOR;
                speakerValue.textContent = vol + '%';
                speakerValue.style.color = color;
                speakerSlider.style.accentColor = color;
            }
            applySpeakerVolumeVisual(S.speakerVolume);

            speakerSlider.addEventListener('pointerdown', function () {
                speakerSliderPointerActive = true;
                speakerSliderHadPointerInput = true;
            });
            speakerSlider.addEventListener('pointerup', function () {
                speakerSliderPointerActive = false;
            });
            speakerSlider.addEventListener('pointercancel', function () {
                speakerSliderPointerActive = false;
            });
            speakerSlider.addEventListener('input', function (e) {
                var trackPos = parseInt(e.target.value, 10);
                if (isNaN(trackPos)) return;
                if (speakerSliderPointerActive) {
                    var snappedTrackPos = snapSpeakerTrackPos(trackPos);
                    if (snappedTrackPos !== trackPos) {
                        trackPos = snappedTrackPos;
                        speakerSlider.value = String(snappedTrackPos);
                    }
                }
                applySpeakerTrackPos(trackPos);
            });
            speakerSlider.addEventListener('change', function (e) {
                var trackPos = parseInt(e.target.value, 10);
                if (!isNaN(trackPos) && speakerSliderHadPointerInput) {
                    var snappedTrackPos = snapSpeakerTrackPos(trackPos);
                    if (snappedTrackPos !== trackPos) {
                        speakerSlider.value = String(snappedTrackPos);
                        applySpeakerTrackPos(snappedTrackPos);
                    }
                }
                speakerSliderHadPointerInput = false;
                if (typeof window.saveSpeakerVolumeSetting === 'function') window.saveSpeakerVolumeSetting();
            });

            // 用相对定位容器在轨道 75% 处画一条 100% 标准锚点，告诉用户「这条线以上是增强」
            var speakerSliderWrap = document.createElement('div');
            Object.assign(speakerSliderWrap.style, { position: 'relative', width: '100%', height: '18px', display: 'flex', alignItems: 'center' });
            var speakerAnchorTick = document.createElement('div');
            Object.assign(speakerAnchorTick.style, {
                position: 'absolute', left: speakerThumbAlignedLeft(speakerVolumeAnchorTrackPos), top: '0', bottom: '0',
                width: '2px', transform: 'translateX(-50%)',
                backgroundColor: 'var(--neko-popup-text-sub)', opacity: '0.28', borderRadius: '1px', pointerEvents: 'none', zIndex: '0'
            });
            speakerSliderWrap.appendChild(speakerSlider);
            speakerSliderWrap.appendChild(speakerAnchorTick);
            speakerContainer.appendChild(speakerSliderWrap);

            var speakerHint = document.createElement('div');
            speakerHint.textContent = window.t ? window.t('speaker.volumeHint') : '调节AI语音的播放音量';
            speakerHint.setAttribute('data-i18n', 'speaker.volumeHint');
            Object.assign(speakerHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            speakerContainer.appendChild(speakerHint);
            leftColumn.appendChild(speakerContainer);

            // ===== 左栏 1.2. 空间音频开关（多屏立体声 + 距离衰减）=====
            var spatialContainer = document.createElement('div');
            spatialContainer.style.padding = '8px 12px';

            var spatialRow = document.createElement('div');
            Object.assign(spatialRow.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center' });

            var spatialLabel = document.createElement('span');
            spatialLabel.textContent = window.t ? window.t('speaker.spatialAudioLabel') : '空间音频';
            spatialLabel.setAttribute('data-i18n', 'speaker.spatialAudioLabel');
            Object.assign(spatialLabel.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var spatialEnabled = (window.appSpatialAudio && typeof window.appSpatialAudio.getEnabled === 'function')
                ? window.appSpatialAudio.getEnabled()
                : !!S.spatialAudioEnabled;

            var spatialToggle = document.createElement('label');
            Object.assign(spatialToggle.style, { position: 'relative', display: 'inline-block', width: '36px', height: '20px', flexShrink: '0' });
            var spatialInput = document.createElement('input');
            spatialInput.type = 'checkbox';
            spatialInput.checked = spatialEnabled;
            Object.assign(spatialInput.style, { opacity: '0', width: '0', height: '0' });
            var spatialSliderEl = document.createElement('span');
            Object.assign(spatialSliderEl.style, { position: 'absolute', cursor: 'pointer', top: '0', left: '0', right: '0', bottom: '0', backgroundColor: spatialEnabled ? '#4f8cff' : '#ccc', borderRadius: '10px', transition: 'background-color 0.2s' });
            var spatialKnob = document.createElement('span');
            Object.assign(spatialKnob.style, { position: 'absolute', content: '""', height: '16px', width: '16px', left: spatialEnabled ? '18px' : '2px', bottom: '2px', backgroundColor: 'white', borderRadius: '50%', transition: 'left 0.2s' });
            spatialSliderEl.appendChild(spatialKnob);
            spatialToggle.appendChild(spatialInput);
            spatialToggle.appendChild(spatialSliderEl);

            spatialInput.addEventListener('change', function () {
                var on = spatialInput.checked;
                spatialSliderEl.style.backgroundColor = on ? '#4f8cff' : '#ccc';
                spatialKnob.style.left = on ? '18px' : '2px';
                if (window.appSpatialAudio && typeof window.appSpatialAudio.setEnabled === 'function') {
                    window.appSpatialAudio.setEnabled(on);
                } else {
                    S.spatialAudioEnabled = on;
                }
            });

            spatialRow.appendChild(spatialLabel);
            spatialRow.appendChild(spatialToggle);
            spatialContainer.appendChild(spatialRow);

            var spatialHint = document.createElement('div');
            spatialHint.textContent = window.t ? window.t('speaker.spatialAudioHint') : '根据猫娘窗口相对主屏的位置做立体声与距离衰减';
            spatialHint.setAttribute('data-i18n', 'speaker.spatialAudioHint');
            Object.assign(spatialHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            spatialContainer.appendChild(spatialHint);
            leftColumn.appendChild(spatialContainer);

            // 分隔线
            var sep1 = document.createElement('div');
            Object.assign(sep1.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '8px 0' });
            leftColumn.appendChild(sep1);

            // ===== 左栏 1.5. 降噪开关 =====
            var nrContainer = document.createElement('div');
            nrContainer.style.padding = '8px 12px';

            var nrRow = document.createElement('div');
            Object.assign(nrRow.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center' });

            var nrLabel = document.createElement('span');
            nrLabel.textContent = window.t ? window.t('microphone.noiseReduction') : '降噪';
            nrLabel.setAttribute('data-i18n', 'microphone.noiseReduction');
            Object.assign(nrLabel.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var nrToggle = document.createElement('label');
            Object.assign(nrToggle.style, { position: 'relative', display: 'inline-block', width: '36px', height: '20px', flexShrink: '0' });
            var nrInput = document.createElement('input');
            nrInput.type = 'checkbox';
            nrInput.checked = S.noiseReductionEnabled;
            Object.assign(nrInput.style, { opacity: '0', width: '0', height: '0' });
            var nrSlider = document.createElement('span');
            Object.assign(nrSlider.style, { position: 'absolute', cursor: 'pointer', top: '0', left: '0', right: '0', bottom: '0', backgroundColor: S.noiseReductionEnabled ? '#4f8cff' : '#ccc', borderRadius: '10px', transition: 'background-color 0.2s' });
            var nrKnob = document.createElement('span');
            Object.assign(nrKnob.style, { position: 'absolute', content: '""', height: '16px', width: '16px', left: S.noiseReductionEnabled ? '18px' : '2px', bottom: '2px', backgroundColor: 'white', borderRadius: '50%', transition: 'left 0.2s' });
            nrSlider.appendChild(nrKnob);
            nrToggle.appendChild(nrInput);
            nrToggle.appendChild(nrSlider);

            nrInput.addEventListener('change', function () {
                S.noiseReductionEnabled = nrInput.checked;
                nrSlider.style.backgroundColor = nrInput.checked ? '#4f8cff' : '#ccc';
                nrKnob.style.left = nrInput.checked ? '18px' : '2px';
                saveNoiseReductionSetting();
            });

            nrRow.appendChild(nrLabel);
            nrRow.appendChild(nrToggle);
            nrContainer.appendChild(nrRow);

            var nrHint = document.createElement('div');
            nrHint.textContent = window.t ? window.t('microphone.noiseReductionHint') : 'RNNoise AI 降噪';
            nrHint.setAttribute('data-i18n', 'microphone.noiseReductionHint');
            Object.assign(nrHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            nrContainer.appendChild(nrHint);
            leftColumn.appendChild(nrContainer);

            // ===== 独立 ASR 开关（下次语音 session 生效） =====
            var asrContainer = document.createElement('div');
            asrContainer.style.padding = '8px 12px';

            var asrRow = document.createElement('div');
            Object.assign(asrRow.style, { display: 'flex', justifyContent: 'space-between', alignItems: 'center' });

            var asrLabel = document.createElement('span');
            asrLabel.textContent = window.t ? window.t('microphone.independentAsr') : 'Independent ASR';
            asrLabel.setAttribute('data-i18n', 'microphone.independentAsr');
            Object.assign(asrLabel.style, { fontSize: '13px', color: 'var(--neko-popup-text)', fontWeight: '500' });

            var asrToggle = document.createElement('label');
            Object.assign(asrToggle.style, { position: 'relative', display: 'inline-block', width: '36px', height: '20px', flexShrink: '0' });
            var asrInput = document.createElement('input');
            asrInput.type = 'checkbox';
            asrInput.checked = S.independentAsrEnabled === true;
            Object.assign(asrInput.style, { opacity: '0', width: '0', height: '0' });
            var asrSlider = document.createElement('span');
            Object.assign(asrSlider.style, { position: 'absolute', cursor: 'pointer', top: '0', left: '0', right: '0', bottom: '0', backgroundColor: asrInput.checked ? '#4f8cff' : '#ccc', borderRadius: '10px', transition: 'background-color 0.2s' });
            var asrKnob = document.createElement('span');
            Object.assign(asrKnob.style, { position: 'absolute', content: '""', height: '16px', width: '16px', left: asrInput.checked ? '18px' : '2px', bottom: '2px', backgroundColor: 'white', borderRadius: '50%', transition: 'left 0.2s' });
            asrSlider.appendChild(asrKnob);
            asrToggle.appendChild(asrInput);
            asrToggle.appendChild(asrSlider);

            asrInput.addEventListener('change', function () {
                S.independentAsrEnabled = asrInput.checked;
                asrSlider.style.backgroundColor = asrInput.checked ? '#4f8cff' : '#ccc';
                asrKnob.style.left = asrInput.checked ? '18px' : '2px';
                if (window.appSettings && typeof window.appSettings.saveSettings === 'function') {
                    if (typeof window.appSettings.syncSettingsToServer === 'function') {
                        // Session start reads the SERVER-persisted value (asr_runtime.py
                        // _start_independent_asr_if_enabled -> aload_global_conversation_settings),
                        // so the fire-and-forget POST inside saveSettings() can race a
                        // mic start and silently keep the previous route. Persist
                        // locally first, then run the POST ourselves and publish it as
                        // S.pendingSettingsSyncPromise; ensureWebSocketOpen()
                        // (app-websocket.js) awaits it before any start_session send.
                        // userInitiated: true marks settings hydrated so the
                        // start_session handshake stamps this explicit choice
                        // even while the settings GET is failing.
                        // syncSettingsToServer serializes its POSTs internally
                        // and snapshots settings at send time, so flipping the
                        // toggle twice quickly cannot let the older request
                        // finish last and persist the stale value; the newer
                        // promise published below also resolves only after any
                        // predecessor POST completed.
                        window.appSettings.saveSettings({ skipServerSync: true });
                        var syncPromise = Promise.resolve(window.appSettings.syncSettingsToServer({ userInitiated: true }))
                            .catch(function () { /* syncSettingsToServer already logs failures */ })
                            .then(function () {
                                if (S.pendingSettingsSyncPromise === syncPromise) {
                                    S.pendingSettingsSyncPromise = null;
                                }
                            });
                        S.pendingSettingsSyncPromise = syncPromise;
                    } else {
                        window.appSettings.saveSettings();
                    }
                }
            });

            asrRow.appendChild(asrLabel);
            asrRow.appendChild(asrToggle);
            asrContainer.appendChild(asrRow);

            var asrHint = document.createElement('div');
            var asrHintKey = S.independentAsrActive
                ? 'microphone.independentAsrActive'
                : (S.independentAsrEnabled ? 'microphone.independentAsrNextSession' : 'microphone.independentAsrNative');
            var asrHintParams = { providerKey: S.independentAsrProvider || 'unknown' };
            asrHint.setAttribute('data-i18n', asrHintKey);
            asrHint.setAttribute('data-i18n-params', JSON.stringify(asrHintParams));
            asrHint.textContent = window.t
                ? window.t(asrHintKey, asrHintParams)
                : (S.independentAsrActive ? 'Independent ASR active' : (S.independentAsrEnabled ? 'Takes effect next voice session' : 'Using Omni native recognition'));
            Object.assign(asrHint.style, { fontSize: '11px', color: 'var(--neko-popup-text-sub)', marginTop: '6px' });
            asrContainer.appendChild(asrHint);
            leftColumn.appendChild(asrContainer);

            var sep1b = document.createElement('div');
            Object.assign(sep1b.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '8px 0' });
            leftColumn.appendChild(sep1b);

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
            if (!hasMicrophoneDevices) {
                gainSlider.disabled = true;
                gainSlider.style.cursor = 'not-allowed';
                gainSlider.style.opacity = '0.55';
                gainLabel.style.color = 'var(--neko-popup-text-sub)';
                gainValueEl.style.color = 'var(--neko-popup-text-sub)';
            }

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

            var MIC_ACTION_HOVER_COLLAPSE_MS = 260;
            var activeMicActionKey = null;
            var micActionHoverCollapseTimer = null;
            var micActionHoverOpenGeneration = 0;

            function getOwnedMicSubwindow() {
                var ownerSelector = micPopup.id
                    ? '[data-neko-sidepanel-owner="' + micPopup.id + '"].neko-mic-subwindow'
                    : '.neko-mic-subwindow';
                return document.querySelector(ownerSelector);
            }

            function clearMicActionHoverCollapseTimer() {
                if (micActionHoverCollapseTimer) {
                    clearTimeout(micActionHoverCollapseTimer);
                    micActionHoverCollapseTimer = null;
                }
            }

            function isMicActionHoverSurfaceActive() {
                var hoveredAction = leftColumn.querySelector('[data-neko-mic-main-action]:hover');
                if (hoveredAction) return true;
                var panel = getOwnedMicSubwindow();
                return !!(panel && panel.isConnected && panel.matches(':hover'));
            }

            function closeMicSubwindow() {
                clearMicActionHoverCollapseTimer();
                activeMicActionKey = null;
                var ownerSelector = micPopup.id ? '[data-neko-sidepanel-owner="' + micPopup.id + '"]' : '.neko-mic-subwindow';
                document.querySelectorAll(ownerSelector + '.neko-mic-subwindow').forEach(function (panel) {
                    panel.remove();
                });
            }

            function scheduleMicActionHoverCollapse() {
                clearMicActionHoverCollapseTimer();
                micActionHoverCollapseTimer = setTimeout(function () {
                    micActionHoverCollapseTimer = null;
                    if (isMicActionHoverSurfaceActive()) return;
                    closeMicSubwindow();
                    leftColumn.querySelectorAll('[data-neko-mic-main-action]').forEach(function (btn) {
                        btn.style.background = 'transparent';
                    });
                }, MIC_ACTION_HOVER_COLLAPSE_MS);
            }

            function wireMicSubwindowHoverBridge(panel) {
                if (!panel || panel._nekoMicHoverBridgeWired) return;
                panel._nekoMicHoverBridgeWired = true;
                panel.addEventListener('mouseenter', function () {
                    clearMicActionHoverCollapseTimer();
                });
                panel.addEventListener('mouseleave', function () {
                    scheduleMicActionHoverCollapse();
                });
            }

            function openMicActionPanel(actionKey, openFn) {
                clearMicActionHoverCollapseTimer();
                var existing = getOwnedMicSubwindow();
                if (activeMicActionKey === actionKey && existing && existing.isConnected) {
                    wireMicSubwindowHoverBridge(existing);
                    return Promise.resolve(existing);
                }
                activeMicActionKey = actionKey;
                var generation = ++micActionHoverOpenGeneration;
                return Promise.resolve(openFn()).then(function () {
                    if (generation !== micActionHoverOpenGeneration || activeMicActionKey !== actionKey) return null;
                    var panel = getOwnedMicSubwindow();
                    if (panel) {
                        panel.setAttribute('data-neko-mic-action-key', actionKey);
                        wireMicSubwindowHoverBridge(panel);
                    }
                    return panel;
                });
            }

            function positionMicSubwindow(panel) {
                if (!panel || !micPopup || !micPopup.isConnected) return;
                var rect = micPopup.getBoundingClientRect();
                var panelWidth = panel.offsetWidth || 320;
                var panelHeight = panel.offsetHeight || 360;
                var gap = 8;
                var left = rect.right + gap;
                var opensLeft = micPopup.dataset && micPopup.dataset.opensLeft === 'true';
                if (opensLeft || left + panelWidth > window.innerWidth - gap) {
                    left = rect.left - panelWidth - gap;
                }
                left = Math.max(gap, Math.min(left, window.innerWidth - panelWidth - gap));
                var top = Math.max(gap, Math.min(rect.top, window.innerHeight - panelHeight - gap));
                panel.style.left = left + 'px';
                panel.style.top = top + 'px';
            }

            function createMicSubwindow(title, iconText, width) {
                // Keep activeMicActionKey; only tear down the previous DOM panel.
                clearMicActionHoverCollapseTimer();
                var ownerSelector = micPopup.id ? '[data-neko-sidepanel-owner="' + micPopup.id + '"]' : '.neko-mic-subwindow';
                document.querySelectorAll(ownerSelector + '.neko-mic-subwindow').forEach(function (panel) {
                    panel.remove();
                });
                var panel = document.createElement('div');
                panel.className = 'neko-mic-subwindow';
                if (micPopup.id) panel.setAttribute('data-neko-sidepanel-owner', micPopup.id);
                panel.setAttribute('data-neko-sidepanel', '');
                Object.assign(panel.style, {
                    position: 'fixed',
                    zIndex: '100003',
                    width: width || '320px',
                    maxHeight: 'min(420px, calc(100vh - 16px))',
                    overflowY: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '4px',
                    padding: '8px',
                    boxSizing: 'border-box',
                    background: 'var(--neko-popup-bg, rgba(255, 255, 255, 0.82))',
                    backdropFilter: 'saturate(180%) blur(20px)',
                    border: 'var(--neko-popup-border, 1px solid rgba(255, 255, 255, 0.18))',
                    borderRadius: '8px',
                    boxShadow: 'var(--neko-popup-shadow, 0 8px 24px rgba(0,0,0,0.16))',
                    pointerEvents: 'auto',
                    cursor: 'default',
                    color: 'var(--neko-popup-text)'
                });

                var stopSubwindowEvent = function (e) {
                    if (document.body.classList.contains('neko-model-dragging')) return;
                    e.stopPropagation();
                };
                ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(function (evt) {
                    panel.addEventListener(evt, stopSubwindowEvent, true);
                });
                panel.addEventListener('click', stopSubwindowEvent);

                var header = document.createElement('div');
                Object.assign(header.style, {
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '8px',
                    padding: '4px 6px 8px',
                    borderBottom: '1px solid var(--neko-popup-separator)',
                    marginBottom: '4px',
                    flexShrink: '0'
                });

                var titleWrap = document.createElement('div');
                Object.assign(titleWrap.style, { display: 'flex', alignItems: 'center', gap: '6px', minWidth: '0', color: '#4f8cff', fontSize: '13px', fontWeight: '600' });
                var icon = document.createElement('span');
                icon.textContent = iconText;
                icon.style.fontSize = '14px';
                var titleEl = document.createElement('span');
                titleEl.textContent = title;
                Object.assign(titleEl.style, { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
                titleWrap.appendChild(icon);
                titleWrap.appendChild(titleEl);

                var closeBtn = document.createElement('button');
                closeBtn.type = 'button';
                closeBtn.textContent = 'x';
                closeBtn.setAttribute('aria-label', 'Close');
                Object.assign(closeBtn.style, {
                    width: '24px',
                    height: '24px',
                    border: 'none',
                    borderRadius: '6px',
                    background: 'transparent',
                    color: 'var(--neko-popup-text-sub)',
                    cursor: 'pointer',
                    flexShrink: '0'
                });
                closeBtn.addEventListener('mouseenter', function () { closeBtn.style.background = 'var(--neko-popup-hover)'; });
                closeBtn.addEventListener('mouseleave', function () { closeBtn.style.background = 'transparent'; });
                closeBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    closeMicSubwindow();
                });

                header.appendChild(titleWrap);
                header.appendChild(closeBtn);
                panel.appendChild(header);

                var body = document.createElement('div');
                body.className = 'neko-mic-popup-scroll neko-mic-subwindow-body';
                Object.assign(body.style, {
                    display: 'flex',
                    flex: '1 1 auto',
                    flexDirection: 'column',
                    gap: '4px',
                    minHeight: '0',
                    overflowY: 'auto'
                });
                panel.appendChild(body);
                panel._nekoMicSubwindowBody = body;
                attachTransientMicPopupScrollbar(body, panel);

                document.body.appendChild(panel);
                requestAnimationFrame(function () { positionMicSubwindow(panel); });
                return panel;
            }

            function createMainActionButton(iconText, label, subLabel, actionKey, onClick, hoverGuard) {
                var button = document.createElement('button');
                button.type = 'button';
                button.dataset.nekoMicMainAction = actionKey;
                Object.assign(button.style, {
                    width: '100%',
                    minWidth: '0',
                    maxWidth: '100%',
                    boxSizing: 'border-box',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '9px 10px',
                    border: 'none',
                    borderRadius: '6px',
                    background: 'transparent',
                    color: 'var(--neko-popup-text)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    transition: 'background 0.2s ease'
                });
                var icon = document.createElement('span');
                icon.textContent = iconText;
                icon.style.fontSize = '15px';
                var textWrap = document.createElement('span');
                Object.assign(textWrap.style, { display: 'flex', flexDirection: 'column', minWidth: '0', width: '0', maxWidth: '100%', flex: '1 1 0%', overflow: 'hidden' });
                var labelEl = document.createElement('span');
                labelEl.textContent = label;
                Object.assign(labelEl.style, { display: 'block', maxWidth: '100%', fontSize: '13px', fontWeight: '600', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
                var subEl = document.createElement('span');
                subEl.className = 'neko-mic-action-sub-label';
                subEl.textContent = subLabel || '';
                Object.assign(subEl.style, { display: 'block', maxWidth: '100%', fontSize: '11px', color: 'var(--neko-popup-text-sub)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
                // Match settings menu chevron (Chat Settings / Animation / Advanced).
                var arrow = document.createElement('span');
                arrow.textContent = '\u203A';
                Object.assign(arrow.style, {
                    fontSize: '16px',
                    color: 'var(--neko-popup-text-sub, #999)',
                    lineHeight: '1',
                    flexShrink: '0'
                });
                textWrap.appendChild(labelEl);
                if (subLabel) textWrap.appendChild(subEl);
                button.appendChild(icon);
                button.appendChild(textWrap);
                button.appendChild(arrow);

                function openActionPanel() {
                    // 悬停守卫：指针在内嵌开关（如屏幕共享开关）上时不展开子面板，避免干扰点击
                    if (typeof hoverGuard === 'function' && hoverGuard()) {
                        return Promise.resolve(null);
                    }
                    button.style.background = 'var(--neko-popup-hover)';
                    return openMicActionPanel(actionKey, onClick).catch(function (error) {
                        console.error('[麦克风弹窗] 子窗口打开失败:', error);
                    });
                }

                // Hover-to-expand like settings side panels.
                button.addEventListener('mouseenter', function () {
                    openActionPanel();
                });
                button.addEventListener('mouseleave', function () {
                    button.style.background = 'transparent';
                    scheduleMicActionHoverCollapse();
                });
                button.addEventListener('click', function (e) {
                    e.stopPropagation();
                    openActionPanel();
                });
                return button;
            }

            function createMicDeviceOption(label, deviceId) {
                var option = document.createElement('button');
                option.type = 'button';
                option.className = 'mic-option';
                if (deviceId !== null) option.dataset.deviceId = deviceId;
                option.textContent = label;
                var isSelected = (deviceId === null && S.selectedMicrophoneId === null) || deviceId === S.selectedMicrophoneId;
                if (isSelected) option.classList.add('selected');
                Object.assign(option.style, { padding: '8px 12px', cursor: 'pointer', border: 'none', background: isSelected ? 'var(--neko-popup-selected-bg)' : 'transparent', borderRadius: '6px', transition: 'background 0.2s ease', fontSize: '13px', width: '100%', textAlign: 'left', color: isSelected ? '#4f8cff' : 'var(--neko-popup-text)', fontWeight: isSelected ? '500' : '400' });
                option.addEventListener('mouseenter', function () { if (!option.classList.contains('selected')) option.style.background = 'var(--neko-popup-hover)'; });
                option.addEventListener('mouseleave', function () { if (!option.classList.contains('selected')) option.style.background = 'transparent'; });
                option.addEventListener('click', async function (e) {
                    e.stopPropagation();
                    await selectMicrophone(deviceId);
                    updateMicListSelection();
                    document.querySelectorAll('[data-neko-mic-action="device"] .neko-mic-action-sub-label').forEach(function (labelEl) {
                        labelEl.textContent = label;
                    });
                });
                return option;
            }

            async function openMicDeviceSubwindow() {
                var panel = createMicSubwindow(
                    window.t ? window.t('microphone.deviceTitle') : 'Select Microphone',
                    '\uD83C\uDFA4',
                    '280px'
                );
                panel.classList.add('neko-mic-device-subwindow');
                var panelBody = panel._nekoMicSubwindowBody || panel;
                var listBody = document.createElement('div');
                Object.assign(listBody.style, { display: 'flex', flexDirection: 'column', gap: '4px' });
                panelBody.appendChild(listBody);

                var loadingItem = document.createElement('div');
                loadingItem.textContent = window.t ? window.t('app.screenSource.loading') : 'Loading...';
                Object.assign(loadingItem.style, { padding: '8px 12px', color: 'var(--neko-popup-text-sub)', fontSize: '13px' });
                listBody.appendChild(loadingItem);
                positionMicSubwindow(panel);

                var devices = cachedMicDevices;
                if (!devices || devices.length === 0 || !micPermissionGranted) {
                    devices = await ensureMicrophonePermission();
                }
                listBody.innerHTML = '';

                var defaultLabel = window.t ? window.t('microphone.defaultDevice') : 'System Default Microphone';
                listBody.appendChild(createMicDeviceOption(defaultLabel, null));

                if (!devices || devices.length === 0) {
                    var noMicItem = document.createElement('div');
                    noMicItem.textContent = window.t ? window.t('microphone.noDevices') : 'No microphone devices detected';
                    Object.assign(noMicItem.style, { padding: '8px 12px', color: 'var(--neko-popup-text-sub)', fontSize: '13px' });
                    listBody.appendChild(noMicItem);
                    requestAnimationFrame(function () { positionMicSubwindow(panel); });
                    return;
                }

                var sep = document.createElement('div');
                Object.assign(sep.style, { height: '1px', backgroundColor: 'var(--neko-popup-separator)', margin: '5px 0' });
                listBody.appendChild(sep);

                devices.forEach(function (device, idx) {
                    var label = device.label || (window.t ? window.t('microphone.deviceLabel', { index: idx + 1 }) : 'Microphone ' + (idx + 1));
                    listBody.appendChild(createMicDeviceOption(label, device.deviceId));
                });
                requestAnimationFrame(function () { positionMicSubwindow(panel); });
            }

            async function openScreenSourceSubwindow() {
                var panel = createMicSubwindow(
                    window.t ? window.t('buttons.screenShare') : 'Screen Share',
                    '\uD83D\uDDA5\uFE0F',
                    '360px'
                );
                var panelBody = panel._nekoMicSubwindowBody || panel;
                var screenSourceList = document.createElement('div');
                screenSourceList.id = micPopup.id ? micPopup.id + '-screen-sources' : 'neko-mic-popup-screen-sources';
                screenSourceList.className = 'neko-mic-popup-screen-sources';
                Object.assign(screenSourceList.style, {
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '2px',
                    minHeight: '80px'
                });
                panelBody.appendChild(screenSourceList);
                // 开始/停止共享开关已移至设置面板「屏幕共享」与「选择麦克风」之间
                // （createScreenShareToggleButton），子窗口仅保留屏幕/窗口源列表。
                positionMicSubwindow(panel);
                if (typeof window.renderFloatingScreenSourceList === 'function') {
                    await window.renderFloatingScreenSourceList(screenSourceList, { requireVisible: false });
                    positionMicSubwindow(panel);
                }
            }

            var deviceButtonLabel = window.t ? window.t('microphone.deviceTitle') : 'Select Microphone';
            var currentMicLabel = S.selectedMicrophoneId === null
                ? (window.t ? window.t('microphone.defaultDevice') : 'System Default Microphone')
                : (audioInputs.find(function (device) { return device.deviceId === S.selectedMicrophoneId; }) || {}).label || deviceButtonLabel;
            var screenButtonLabel = window.t ? window.t('buttons.screenShare') : 'Screen Share';

            var firstContent = leftColumn.firstChild;
            // 悬停守卫：指针落在行内开关上时不展开屏幕源面板，避免面板弹出干扰点击开关
            var shareToggleButtonHolder = { current: null };
            function screenRowHoverGuard() {
                var toggle = shareToggleButtonHolder.current;
                return !!(toggle && toggle.matches(':hover'));
            }
            var screenActionButton = createMainActionButton(
                '\uD83D\uDDA5\uFE0F',
                screenButtonLabel,
                window.t ? window.t('app.screenSource.screens') : 'Screens',
                'screen',
                openScreenSourceSubwindow,
                screenRowHoverGuard
            );
            leftColumn.insertBefore(screenActionButton, firstContent);
            // 合并为一行：行右侧嵌入迷你胶囊开关（替换原来的 chevron 箭头），
            // 点击行其余位置仍展开屏幕源选择，点击开关本身开始/停止共享
            var shareToggleButton = createScreenShareToggleButton({ mini: true });
            shareToggleButtonHolder.current = shareToggleButton;
            screenActionButton.replaceChild(shareToggleButton, screenActionButton.lastChild);
            // 屏幕共享行：标题允许换行显示（去掉省略号截断），
            // 保证葡语 "Compartilhamento de tela"、俄语 "Демонстрация экрана" 等长文案也能完整显示
            var screenTextWrap = screenActionButton.children[1];
            if (screenTextWrap && screenTextWrap.firstChild) {
                screenTextWrap.firstChild.style.whiteSpace = 'normal';
                screenTextWrap.firstChild.style.lineHeight = '1.2';
                screenTextWrap.firstChild.style.overflow = 'visible';
            }
            var micActionButton = createMainActionButton(
                '\uD83C\uDFA4',
                deviceButtonLabel,
                currentMicLabel,
                'device',
                openMicDeviceSubwindow
            );
            micActionButton.dataset.nekoMicAction = 'device';
            leftColumn.insertBefore(micActionButton, firstContent);

            // 组装
            micPopup.appendChild(leftColumn);

            startMicVolumeVisualization();
            return true;
        } catch (error) {
            if (!isPopupAvailable()) return false;
            console.error('渲染麦克风列表失败:', error);
            micPopup.innerHTML = '';
            var errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('microphone.loadFailed') : '获取麦克风列表失败';
            Object.assign(errorItem.style, { padding: '8px 12px', color: '#dc3545', fontSize: '13px' });
            micPopup.appendChild(errorItem);
            return true;
        }
    };

    /** 轻量级更新：仅更新选中状态 */
    function updateMicListSelection() {
        var options = document.querySelectorAll('#live2d-popup-mic .mic-option, #vrm-popup-mic .mic-option, #mmd-popup-mic .mic-option, .neko-mic-device-subwindow .mic-option');
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
