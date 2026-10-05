/** Numeric v2 小剧场在 N.E.K.O 本体中的页面级编排器。 */
(function () {
    'use strict';

    var api = {
        start: '/api/theater-numeric/session/start',
        session: '/api/theater-numeric/session',
        input: '/api/theater-numeric/session/input',
        end: '/api/theater-numeric/session/end',
        speakBlock: '/api/theater-numeric/session/speak-block',
        release: '/api/theater-numeric/session/release'
    };
    var POINTER_KEY = 'neko.theater.numeric.v2.capsule-pointer.v1';
    // 本体运行时只消费共享传输协议；胶囊状态、回放和跨窗口目标仍由本模块负责。
    var transport = window.nekoTheaterTransport;
    if (!transport) throw new Error('numeric_theater_transport_unavailable');
    var MESSAGE_SCHEMA = transport.MESSAGE_SCHEMA;
    var createId = transport.createId;
    function requestJson(url, options) {
        var opts = Object.assign({}, options || {});
        var claim = opts.activityClaimId || state.activityClaimId;
        if (claim) opts.headers = Object.assign({}, opts.headers || {}, { 'X-Neko-Theater-Activity': claim });
        return transport.requestJson(url, opts);
    }
    // 旧 Session 可能已保存过去的空桥段占位句；只在转场桥段中精确隐藏，不改写正式演绎记录。
    var LEGACY_EMPTY_TRANSITION_BRIDGE = '时间向前流转，现场随之转换。';
    var state = {
        active: false, phase: 'inactive', storyId: '', storyTitle: '', sessionId: '', revision: 0, lifecycleRevision: 0,
        playerName: '', catgirlName: '', activityCatgirlName: '', activityClaimId: '',
        sessionStatus: '', scene: null, history: [], suggestedInputs: [], invitationRecoveryAvailable: false,
        queueToken: 0, pendingTurn: null, pendingEnd: null, channel: null, hostReadyTimer: 0,
        draftRestore: null, ordinaryDraftRestore: null, composerVisibilityRestore: null,
        chatSurfaceModeRestore: null, windowClaimed: false,
        errorMessage: '', tokenUsage: null, endInFlight: null
    };
    var launchRequests = Object.create(null);
    var activeSpeechRequests = Object.create(null);
    var launchRequestOrder = [];
    var launchReplyTargets = Object.create(null);
    var desktopLaunchRelayTimers = Object.create(null);
    var launchEpoch = 0;
    var pendingLaunch = null;
    var startCleanupBarrier = Promise.resolve();
    var pendingSelectorEnd = null;
    var runtimeHostId = createId('theater_host_');
    var endConfirmationPending = false;
    var committedSnapshot = null;
    // 仅在内存中登记的主动搭话临时抑制；不写用户设置，页面关闭或崩溃时随之消失。
    var proactiveSuppressionClaimed = false;
    var CHAT_SURFACE_MODES = ['compact', 'full', 'minimized'];
    // 剧场自己写入输入区可见性时使用的来源标记；其他来源的写入代表剧场期间的真实变化。
    var THEATER_COMPOSER_REASON = 'theater-presentation';
    // Electron 下音频只进 Pet 窗口的真实 websocket（聊天窗口经代理转发且不转发 audio_chunk）；
    // 剧场窗口把仍有效的播放请求广播给其他窗口，由收到音频的窗口据此放行。
    var runtimeInstanceId = createId('theater_runtime_');
    var peerSpeechAllowlists = Object.create(null);
    // 放行表随广播窗口存活：剧场窗口在有待播对白期间按 REFRESH 周期重发，每次广播携带
    // ttl_ms，收音窗口只在该时长内接受新的音频头；剧场窗口崩溃或重载未能广播空表时，
    // 旧对白的新音频最多再被放行 ttl_ms。页面可见时用短 TTL；隐藏后 Chromium 会节流计时器
    // （至少 1 s，隐藏约 5 分钟后进入每分钟一次的强节流），此时改用覆盖强节流的长 TTL。
    var PEER_SPEECH_ALLOWLIST_REFRESH_MS = 2000;
    var PEER_SPEECH_ALLOWLIST_TTL_MS = 7000;
    var PEER_SPEECH_ALLOWLIST_HIDDEN_TTL_MS = 75000;
    // 收音窗口对收到的 ttl_ms 设上下限，缺失或非法时按短 TTL 处理。
    var PEER_SPEECH_ALLOWLIST_MIN_TTL_MS = 1000;
    var PEER_SPEECH_ALLOWLIST_MAX_TTL_MS = 120000;
    var speechAllowlistRefreshTimer = 0;
    var speechEventRelays = [];

    function t(key, fallback) {
        if (typeof window.t === 'function') {
            var value = window.t(key);
            if (typeof value === 'string' && value && value !== key) return value;
        }
        return fallback;
    }
    function postMessage(message) {
        var payload = transport.createMessage('theater-runtime', message);
        if (state.channel) { try { state.channel.postMessage(payload); } catch (_) {} }
        return payload;
    }
    function postDirect(target, message) {
        if (!target || typeof target.postMessage !== 'function') return false;
        try { target.postMessage(message, window.location.origin); return true; } catch (_) { return false; }
    }
    function desktopRuntimeRole() {
        var body = document.body;
        // 桌面端本体页和独立聊天页都会加载本 Runtime；角色必须由现有宿主标记确定，
        // 不能按剧本或窗口名称硬编码，否则会再次出现正文写入不可见窗口的问题。
        if (window.__LANLAN_IS_ELECTRON_PET__ === true) return 'pet';
        if (!body || !body.classList.contains('neko-electron-runtime')) return '';
        if (!body.classList.contains('electron-chat-window')) return '';
        return String(body.getAttribute('data-chat-host-kind') || 'compact');
    }
    function stopDesktopLaunchRelay(launchId) {
        var key = String(launchId || '');
        var timer = desktopLaunchRelayTimers[key];
        if (timer) window.clearInterval(timer);
        delete desktopLaunchRelayTimers[key];
    }
    function relayLaunchToDesktopChat(message) {
        var launchId = String(message && message.launch_id || '');
        if (!launchId || !state.channel) return false;
        stopDesktopLaunchRelay(launchId);
        var attempts = 0;
        function relay() {
            attempts += 1;
            // Electron Pet 只负责把选剧页的启动交给真正可见的紧凑胶囊窗口；
            // 同一 launch_id 在目标 Runtime 内幂等，短时重发只用于覆盖聊天页尚在加载的竞态。
            postMessage(Object.assign({}, message, { runtime_host_kind: 'compact' }));
            if (attempts >= 40) stopDesktopLaunchRelay(launchId);
        }
        relay();
        desktopLaunchRelayTimers[launchId] = window.setInterval(relay, 200);
        return true;
    }
    function rememberPointer() {
        try {
            // 演绎指针只服务当前程序生命周期内的页面刷新；完整退出后必须回到普通模式。
            // Session 和 Ledger 仍由后端保存，玩家下次可从剧本页主动“继续演绎”。
            if (!state.active) { window.sessionStorage.removeItem(POINTER_KEY); return; }
            var pointer = { story_id: state.storyId, session_id: state.sessionId };
            // 剧场期间宿主形态被临时覆盖为 compact；刷新后不能把覆盖值当作进入前的用户形态重新采集。
            if (CHAT_SURFACE_MODES.indexOf(state.chatSurfaceModeRestore) >= 0) pointer.chat_surface_mode = state.chatSurfaceModeRestore;
            window.sessionStorage.setItem(POINTER_KEY, JSON.stringify(pointer));
        } catch (_) {}
    }
    function readPointer() {
        try {
            var value = JSON.parse(window.sessionStorage.getItem(POINTER_KEY) || 'null');
            return value && value.story_id && value.session_id ? value : null;
        } catch (_) { return null; }
    }
    function host() { return window.reactChatWindowHost || null; }
    function captureOrdinaryDraft(chatHost) {
        if (state.active) return;
        state.ordinaryDraftRestore = null;
        var snapshot = chatHost && typeof chatHost.getState === 'function' ? chatHost.getState() : {};
        // 猫娘本地聊天有自己的草稿状态，不能误当作普通聊天草稿保存。
        if (snapshot.viewProps && snapshot.viewProps.catLocalTextOnly) return;
        var input = document.querySelector('#react-chat-window-root .composer-input');
        if (!input || typeof input.value !== 'string') return;
        // 主页面初始化模型时可能重挂 React；退出小剧场时用该快照恢复普通聊天草稿。
        state.ordinaryDraftRestore = { id: createId('theater_ordinary_draft_'), text: input.value };
    }
    function captureChatSurfaceMode(chatHost) {
        if (state.chatSurfaceModeRestore !== null || !chatHost || typeof chatHost.getChatSurfaceMode !== 'function') return;
        state.chatSurfaceModeRestore = String(chatHost.getChatSurfaceMode() || 'compact');
    }
    function restoreChatSurfaceMode(chatHost) {
        var mode = state.chatSurfaceModeRestore;
        state.chatSurfaceModeRestore = null;
        if (!mode || !chatHost || typeof chatHost.setChatSurfaceMode !== 'function') return;
        // 小剧场只临时占用胶囊界面，退出后恢复用户进入前选择的聊天形态。
        chatHost.setChatSurfaceMode(mode);
    }
    function hostSnapshot(chatHost) {
        var snapshot = chatHost && typeof chatHost.getState === 'function' ? chatHost.getState() : null;
        return snapshot && typeof snapshot === 'object' ? snapshot : {};
    }
    function hostComposerLocked(chatHost) {
        // 首页教程等外部输入锁由宿主持有；剧场只能叠加自己的禁用，不能把外部锁写成解锁。
        return hostSnapshot(chatHost).composerExternallyLocked === true;
    }
    function observeExternalComposerVisibility(chatHost) {
        var restore = state.composerVisibilityRestore;
        if (!restore || !chatHost) return;
        var snapshot = hostSnapshot(chatHost);
        // 剧场每次渲染都把两项写成可见；此刻仍为隐藏，说明是剧场期间由外部新设的状态。
        if (snapshot.composerHiddenRequested) restore.composerHidden = true;
        // “请她离开”与“回来”都会经宿主留下来源记录；剧场之后的非剧场记录就是用户的最新意图，
        // 即使它与剧场强制的可见值相同（剧场期间“回来”）也必须覆盖进入时的快照。
        var record = window.__nekoGoodbyeChatComposerHidden;
        if (record && typeof record === 'object' && record.reason !== THEATER_COMPOSER_REASON
            && Number(record.timestamp) >= restore.claimedAt) {
            restore.goodbyeComposerHidden = !!record.hidden;
        } else if (snapshot.goodbyeComposerHidden) {
            restore.goodbyeComposerHidden = true;
        }
    }
    function claimComposerVisibility(chatHost) {
        if (!state.active || !chatHost) return;
        if (!state.composerVisibilityRestore) {
            var snapshot = hostSnapshot(chatHost);
            state.composerVisibilityRestore = {
                composerHidden: !!snapshot.composerHiddenRequested,
                goodbyeComposerHidden: !!snapshot.goodbyeComposerHidden,
                claimedAt: Date.now()
            };
        } else {
            observeExternalComposerVisibility(chatHost);
        }
        // 快照只采集一次，但剧场活跃期间每次渲染都要重新声明输入区可见。
        if (typeof chatHost.setComposerHidden === 'function') chatHost.setComposerHidden(false);
        if (typeof chatHost.setGoodbyeComposerHidden === 'function') chatHost.setGoodbyeComposerHidden(false, THEATER_COMPOSER_REASON);
    }
    function restoreComposerVisibility(chatHost) {
        if (chatHost) observeExternalComposerVisibility(chatHost);
        var snapshot = state.composerVisibilityRestore;
        state.composerVisibilityRestore = null;
        if (!snapshot || !chatHost) return;
        if (typeof chatHost.setComposerHidden === 'function') chatHost.setComposerHidden(snapshot.composerHidden);
        if (typeof chatHost.setGoodbyeComposerHidden === 'function') {
            chatHost.setGoodbyeComposerHidden(snapshot.goodbyeComposerHidden, THEATER_COMPOSER_REASON);
        }
    }
    function claimAudioPlayback() {
        var audio = window.appAudioPlayback;
        if (audio && typeof audio.clearAudioQueueWithoutDecoderReset === 'function') {
            audio.clearAudioQueueWithoutDecoderReset();
        }
    }
    function currentSpeechAllowlist() {
        if (!state.active) return [];
        return Object.keys(activeSpeechRequests).filter(function (requestId) {
            return activeSpeechRequests[requestId] === state.queueToken;
        });
    }
    function speechAllowlistTtlMs() {
        var doc = window.document;
        var visibility = doc && doc.visibilityState;
        return visibility && visibility !== 'visible' ? PEER_SPEECH_ALLOWLIST_HIDDEN_TTL_MS : PEER_SPEECH_ALLOWLIST_TTL_MS;
    }
    function publishSpeechAllowlist() {
        // 只广播本窗口仍会接受的播放请求；换场、结束或退出后广播空表，收音窗口随即拒绝旧音频。
        var requestIds = currentSpeechAllowlist();
        postMessage({
            action: 'theater:speech-allowlist',
            runtime_instance: runtimeInstanceId,
            request_ids: requestIds,
            ttl_ms: speechAllowlistTtlMs()
        });
        // 有待播对白期间持续续期，长句不会在收音窗口因 TTL 被截断；空表后停止续期。
        if (requestIds.length && !speechAllowlistRefreshTimer) {
            speechAllowlistRefreshTimer = window.setInterval(publishSpeechAllowlist, PEER_SPEECH_ALLOWLIST_REFRESH_MS);
        } else if (!requestIds.length && speechAllowlistRefreshTimer) {
            window.clearInterval(speechAllowlistRefreshTimer);
            speechAllowlistRefreshTimer = 0;
        }
    }
    function republishSpeechAllowlistOnVisibilityChange() {
        // 可见性切换立即重发，收音窗口按新 TTL 续期：转入后台前先拿到长 TTL，回到前台恢复短 TTL。
        if (!speechAllowlistRefreshTimer && !currentSpeechAllowlist().length) return;
        publishSpeechAllowlist();
    }
    function withdrawSpeechAllowlistOnUnload() {
        // 页面关闭或重载时立即撤回放行表；崩溃时无法发送，由收音窗口的 TTL 兜底。
        if (!speechAllowlistRefreshTimer && !currentSpeechAllowlist().length) return;
        if (speechAllowlistRefreshTimer) window.clearInterval(speechAllowlistRefreshTimer);
        speechAllowlistRefreshTimer = 0;
        postMessage({ action: 'theater:speech-allowlist', runtime_instance: runtimeInstanceId, request_ids: [] });
    }
    function peerSpeechAllowlistTtlMs(message) {
        var ttl = Number(message.ttl_ms);
        if (!isFinite(ttl) || ttl <= 0) return PEER_SPEECH_ALLOWLIST_TTL_MS;
        return Math.min(PEER_SPEECH_ALLOWLIST_MAX_TTL_MS, Math.max(PEER_SPEECH_ALLOWLIST_MIN_TTL_MS, ttl));
    }
    function peerAllowsSpeech(requestId) {
        var now = Date.now();
        return Object.keys(peerSpeechAllowlists).some(function (instanceId) {
            var entry = peerSpeechAllowlists[instanceId];
            // 过期（广播窗口停止续期）只拒绝之后到达的音频头；已在播放或已排队的音频不清掉，
            // 它们开始时仍有效，崩溃窗口的迟到音频由拒绝新音频头拦下。
            if (!entry || entry.expiresAt <= now) { delete peerSpeechAllowlists[instanceId]; return false; }
            return entry.requestIds.indexOf(requestId) >= 0;
        });
    }
    function hasPeerSpeechAllowance() {
        var now = Date.now();
        return Object.keys(peerSpeechAllowlists).some(function (instanceId) {
            var entry = peerSpeechAllowlists[instanceId];
            return !!entry && entry.expiresAt > now && entry.requestIds.length > 0;
        });
    }
    function applyPeerSpeechAllowlist(message) {
        if (state.active) return;
        var instanceId = String(message.runtime_instance || '');
        if (!instanceId) return;
        var requestIds = Array.isArray(message.request_ids) ? message.request_ids.map(String) : [];
        var playing = String((window.appState || {}).currentPlayingSpeechCorrelationId || '');
        var wasAllowed = playing.indexOf('theater_speech_') === 0 && peerAllowsSpeech(playing);
        if (requestIds.length) {
            peerSpeechAllowlists[instanceId] = {
                requestIds: requestIds,
                expiresAt: Date.now() + peerSpeechAllowlistTtlMs(message)
            };
        } else delete peerSpeechAllowlists[instanceId];
        // 剧场窗口已作废正在播放的对白（换场、结束或退出）时，本窗口的播放队列也必须一并清掉。
        if (wasAllowed && !peerAllowsSpeech(playing)) claimAudioPlayback();
    }
    function relaySpeechEventToPeer(event) {
        // 只有在为其他窗口的剧场播放对白时转发播放边界，让剧场窗口按真实播放完成推进正文。
        if (state.active || !hasPeerSpeechAllowance()) return;
        var turnId = event && event.detail && event.detail.turnId;
        if (!turnId) return;
        postMessage({ action: 'theater:speech-event', event: event.type, turn_id: String(turnId) });
    }
    function suppressesProactiveChat() {
        // 抑制与剧场会话是否活跃绑定；启动阶段在会话激活前先行登记，避免停麦期间插入主动搭话。
        return state.active === true || proactiveSuppressionClaimed;
    }
    function blocksOrdinaryVoice() {
        // 普通语音、文字和头像互动共用同一判定：本窗口正在演绎，或其他窗口的剧场正在抑制。
        if (state.active === true) return true;
        // Electron 下剧场运行在紧凑聊天窗口，悬浮麦克风却在 Pet 窗口；另一窗口的剧场状态
        // 随主动搭话 leader 心跳传播，剧场窗口关闭或崩溃后按心跳 TTL 自动失效，不会永久锁住麦克风。
        var proactive = window.appProactive;
        try {
            return !!(proactive
                && typeof proactive.isProactiveSuppressedByPeer === 'function'
                && proactive.isProactiveSuppressedByPeer() === true);
        } catch (_) {
            return false;
        }
    }
    function notifyProactiveSuppressionChanged() {
        // 主动搭话调度器（含其他窗口中的 leader）按该查询决定是否调度；这里只通知它重新读取。
        var proactive = window.appProactive;
        if (!proactive || typeof proactive.refreshProactiveSuppression !== 'function') return;
        try { proactive.refreshProactiveSuppression(); } catch (_) {}
    }
    function lockProactiveChatForTheater() {
        // 绝不改写 appState.proactiveChatEnabled：它是会被 saveSettings 持久化并同步到其他窗口的用户设置。
        if (proactiveSuppressionClaimed) return;
        proactiveSuppressionClaimed = true;
        notifyProactiveSuppressionChanged();
    }
    function restoreProactiveChatAfterTheater() {
        proactiveSuppressionClaimed = false;
        // 无论本页是否持有登记都要通知：会话可能由被取代的启动释放过登记，只剩 state.active 在抑制。
        notifyProactiveSuppressionChanged();
    }
    async function stopOrdinaryVoiceInput() {
        var sharedState = window.appState || {};
        var voiceStartWasPending = sharedState.voiceStartPending === true
            || window.isMicStarting === true;
        var voiceWasActive = sharedState.isRecording === true
            || sharedState.voiceChatActive === true
            || voiceStartWasPending;
        if (!voiceWasActive) return true;
        var capture = window.appAudioCapture || {};
        var stopCapture = typeof capture.stopMicCapture === 'function'
            ? capture.stopMicCapture
            : window.stopMicCapture;
        if (typeof stopCapture !== 'function') return false;
        var recordingWasActive = sharedState.isRecording === true;
        if (voiceStartWasPending) {
            // 停麦只能清理采集资源；必须先推进语音启动世代，阻止等待中的旧协程稍后重新开麦。
            if (typeof window.cancelPendingSessionStart !== 'function') return false;
            window.cancelPendingSessionStart('Voice start cancelled by theater');
        }
        try {
            await stopCapture();
        } catch (_) {
            return false;
        }
        if (!recordingWasActive) {
            // 语音 Session 可能已启动但麦克风仍在准备；此时停麦不会发送 pause，需要显式收口后端。
            var websocket = window.appWebSocket;
            if (!websocket || typeof websocket.send !== 'function') return false;
            websocket.send({ action: 'pause_session' });
        }
        return true;
    }
    function ownLanlanName() {
        var config = window.lanlan_config;
        return config && typeof config.lanlan_name === 'string' ? config.lanlan_name.trim() : '';
    }
    function stopPeerOrdinaryVoiceInput(catgirlName) {
        // stopOrdinaryVoiceInput only sees this window's appState. In Electron the
        // floating mic lives in the Pet window, so a voice chat opened there before
        // the theater would keep streaming; ask the other windows to stop it too.
        // Theater activity is per character (/{lanlan_name} pages for different
        // characters share this channel), so the broadcast names the character the
        // theater is bound to and only that character's windows stop their voice.
        var name = String(catgirlName || '').trim() || ownLanlanName();
        postMessage({ action: 'theater:ordinary-voice-stop', catgirl_name: name });
    }
    function isOrdinaryVoiceStopForThisWindow(message) {
        var target = typeof message.catgirl_name === 'string' ? message.catgirl_name.trim() : '';
        var own = ownLanlanName();
        // Without a name on either side this cannot tell the characters apart, so it
        // keeps stopping: an ordinary mic left live mid-theater is the worse failure.
        return !target || !own || target === own;
    }
    var TYPEWRITER_INTERVAL_MS = 32;
    function historyEntry(id, type, text, author, displayKind, status) {
        return {
            id: id,
            type: type,
            text: String(text || '').trim(),
            author: author || undefined,
            displayKind: displayKind || undefined,
            status: status || undefined
        };
    }
    function narrationDisplayKind(phase) {
        // 普通互动和来源回应使用括号微动作；开场与换场桥保留独立场景旁白。
        return phase === 'ordinary' || phase === 'source_response' ? 'action' : 'scene';
    }
    function presentationBlock(type, text, phase) {
        var block = { type: type, text: text };
        if (type === 'narration') block.displayKind = narrationDisplayKind(phase);
        return block;
    }
    function mixedPerformanceBlocks(value, phase) {
        // 新合同只让模型输出一个混合字符串；这里按括号确定性拆分，供逐字展示和 TTS 复用。
        var source = String(value || '').trim();
        if (!source) return [];
        var pairs = { '（': '）', '(': ')' };
        var closers = { '）': true, ')': true };
        var blocks = [];
        var segmentStart = 0;
        var actionStart = -1;
        var expectedClose = '';
        function append(type, rawText, text) {
            if (!String(text || '').trim()) return;
            var block = presentationBlock(type, String(text).trim(), phase);
            // displayText 保留模型原始穿插形式；动作始终属于猫娘气泡，不继承 opening 的场景样式。
            block.displayText = rawText;
            block.preserveSpacing = true;
            if (type === 'narration') block.displayKind = 'action';
            blocks.push(block);
        }
        for (var index = 0; index < source.length; index += 1) {
            var char = source[index];
            if (expectedClose) {
                if (Object.prototype.hasOwnProperty.call(pairs, char)) return [];
                if (closers[char]) {
                    if (char !== expectedClose) return [];
                    append('narration', source.slice(actionStart, index + 1), source.slice(segmentStart, index));
                    expectedClose = '';
                    segmentStart = index + 1;
                }
                continue;
            }
            if (Object.prototype.hasOwnProperty.call(pairs, char)) {
                append('dialogue', source.slice(segmentStart, index), source.slice(segmentStart, index));
                actionStart = index;
                segmentStart = index + 1;
                expectedClose = pairs[char];
                continue;
            }
            if (closers[char]) return [];
        }
        if (expectedClose) return [];
        append('dialogue', source.slice(segmentStart), source.slice(segmentStart));
        return blocks;
    }
    function formatPresentationBlock(block) {
        if (block && Object.prototype.hasOwnProperty.call(block, 'displayText')) return String(block.displayText || '');
        var text = String(block && block.text || '').trim();
        if (!text || block.type !== 'narration' || block.displayKind !== 'action') return text;
        var wrapped = (text.startsWith('（') && text.endsWith('）'))
            || (text.startsWith('(') && text.endsWith(')'));
        return wrapped ? text : '（' + text + '）';
    }
    function contentBlocks(performance, fallbackPhase) {
        if (!performance || typeof performance !== 'object') return [];
        var containers = Array.isArray(performance.segments) ? performance.segments : [performance];
        var blocks = [];
        containers.forEach(function (container) {
            var phase = String(container && container.phase || fallbackPhase || '').trim();
            // 旧的换场记录没有 segments 时，宁可保留独立旁白，也不能把整段换场包装成微动作。
            if (!phase) phase = performance.transition_delivered ? 'transition_bridge' : 'ordinary';
            if (container && (Object.prototype.hasOwnProperty.call(container, 'scene_narration')
                || Object.prototype.hasOwnProperty.call(container, 'performance'))) {
                var sceneNarration = String(container.scene_narration || '').trim();
                if (phase === 'transition_bridge' && sceneNarration === LEGACY_EMPTY_TRANSITION_BRIDGE) {
                    sceneNarration = '';
                }
                if (sceneNarration) blocks.push(presentationBlock('narration', sceneNarration, 'scene'));
                // Fixed text is committed by the runtime, outside the actor/TTS body.
                var fixedNarrations = Array.isArray(container.fixed_narrations) ? container.fixed_narrations : [];
                fixedNarrations.filter(function (item) { return item.position === 'before'; }).forEach(function (item) {
                    blocks.push(presentationBlock('narration', item.text, 'scene'));
                });
                mixedPerformanceBlocks(container.performance, phase).forEach(function (block) { blocks.push(block); });
                fixedNarrations.filter(function (item) { return item.position === 'after'; }).forEach(function (item) {
                    blocks.push(presentationBlock('narration', item.text, 'scene'));
                });
                return;
            }
            var raw = Array.isArray(container && container.content) ? container.content : null;
            if (raw) {
                raw.forEach(function (block) {
                    var type = block && block.type;
                    var text = String(block && block.text || '').trim();
                    if (text && type === 'action') {
                        // Legacy ordered actions retain character-bubble
                        // formatting even in an opening or transition bridge.
                        blocks.push(presentationBlock('narration', text, 'ordinary'));
                        return;
                    }
                    if (text && (type === 'narration' || (type === 'dialogue' && block.speaker_id === 'active_catgirl'))) {
                        blocks.push(presentationBlock(type, text, phase));
                    }
                });
                return;
            }
            var narration = String(container && container.narration || '').trim();
            if (narration) blocks.push(presentationBlock('narration', narration, phase));
            (Array.isArray(container && container.dialogue) ? container.dialogue : []).forEach(function (line) {
                var text = String(line && line.text || '').trim();
                if (text && line.speaker_id === 'active_catgirl') blocks.push(presentationBlock('dialogue', text, phase));
            });
        });
        return blocks;
    }
    function performanceHistoryGroups(performance, fallbackPhase) {
        var groups = [];
        contentBlocks(performance, fallbackPhase).forEach(function (block, blockIndex) {
            // 开场和换场场景旁白沿用独立旁白气泡；场景内微动作才与对白合并。
            if (block.type === 'narration' && block.displayKind === 'scene') {
                groups.push({ type: 'narration', blocks: [{ block: block, blockIndex: blockIndex }] });
                return;
            }
            var current = groups[groups.length - 1];
            if (!current || current.type !== 'dialogue') {
                current = { type: 'dialogue', blocks: [] };
                groups.push(current);
            }
            current.blocks.push({ block: block, blockIndex: blockIndex });
            if (block.preserveSpacing) current.preserveSpacing = true;
        });
        return groups;
    }
    function historyGroupText(group) {
        return group.blocks.map(function (item) { return formatPresentationBlock(item.block); })
            .filter(Boolean)
            .join(group.preserveSpacing ? '' : '\n');
    }
    function buildCommittedHistory(snapshot) {
        var session = snapshot.session || {};
        var result = [];
        performanceHistoryGroups(session.opening_performance, 'opening').forEach(function (group, groupIndex) {
            var openingText = historyGroupText(group);
            if (!openingText) return;
            result.push(historyEntry(
                'opening-performance-' + groupIndex,
                group.type,
                openingText,
                group.type === 'dialogue' ? state.catgirlName : undefined,
                group.type === 'narration' ? 'scene' : undefined
            ));
        });
        (Array.isArray(session.performance_history) ? session.performance_history : []).forEach(function (record, recordIndex) {
            var revision = Number(record.revision || recordIndex + 1);
            var input = String(record.input_text || '').trim();
            if (input) result.push(historyEntry('player-' + revision, 'player_action', input, state.playerName));
            performanceHistoryGroups(record, 'ordinary').forEach(function (group, groupIndex) {
                var performanceText = historyGroupText(group);
                if (!performanceText) return;
                result.push(historyEntry(
                    'performance-' + revision + '-' + groupIndex,
                    group.type,
                    performanceText,
                    group.type === 'dialogue' ? state.catgirlName : undefined,
                    group.type === 'narration' ? 'scene' : undefined
                ));
            });
        });
        if (snapshot.scene && snapshot.scene.terminal && snapshot.scene.ending) {
            var ending = snapshot.scene.ending;
            result.push(historyEntry('ending-' + session.session_id, 'ending', [ending.title, ending.summary].filter(Boolean).join('：')));
        }
        return result;
    }
    // 用量只属于最近一次请求，不混入可导出的剧情历史；缺报时明确显示已知部分。
    function usagePresentation() {
        var usage = state.tokenUsage;
        if (!usage || !Array.isArray(usage.calls)) return null;
        function line(key, fallback, values) {
            return t(key, fallback).replace(/\{(\w+)\}/g, function (_, name) { return String(values[name]); });
        }
        var summary = line('theater.tokenUsageSummary', 'This request · input {input} · output {output} tokens · {calls} calls', {
            input: usage.input_tokens, output: usage.output_tokens, calls: usage.calls.length
        });
        if (!usage.complete) summary += ' · ' + t('theater.tokenUsagePartial', 'Partial usage; some calls were not reported');
        var detail = usage.calls.map(function (call, index) {
            // 按需查原文有独立费用，不能落入默认分支而被显示成演员调用。
            var stage = ['actor', 'suggestions', 'evaluator', 'review', 'dispute', 'history_lookup'].indexOf(call.stage) >= 0 ? call.stage : 'actor';
            return line('theater.tokenUsageCall', '{number}. {stage} · input {input} · output {output}', {
                number: index + 1, stage: t('theater.tokenStage_' + stage, stage),
                input: call.input_tokens == null ? '?' : call.input_tokens,
                output: call.output_tokens == null ? '?' : call.output_tokens
            });
        });
        detail.push(t('theater.tokenUsageHint', 'Provider usage includes cached input and reasoning output when reported. Extra calls are included; this is not a price estimate.'));
        return { summary: summary, detail: detail.join('\n') };
    }

    function presentation() {
        return {
            active: state.active,
            phase: state.phase,
            storyTitle: state.storyTitle,
            history: state.history.slice(),
            suggestedInputs: state.phase === 'awaiting_player' ? (state.invitationRecoveryAvailable
                ? [t('theater.reinvite', '重新邀请')].concat(state.suggestedInputs.filter(function (text) {
                    return text !== t('theater.reinvite', '重新邀请');
                })).slice(0, 3) : state.suggestedInputs.slice(0, 3)) : [],
            busy: ['loading', 'evaluating', 'ending', 'returning_selector'].indexOf(state.phase) >= 0,
            sessionEnded: state.sessionStatus === 'ended',
            errorMessage: state.errorMessage,
            tokenUsage: usagePresentation(),
            draftRestore: state.draftRestore,
            ordinaryDraftRestore: state.ordinaryDraftRestore
        };
    }
    function render() {
        var chatHost = host();
        if (!chatHost || typeof chatHost.setViewProps !== 'function') return false;
        if (state.active) captureChatSurfaceMode(chatHost);
        var externallyLocked = hostComposerLocked(chatHost);
        var compactState = state.active && state.phase === 'awaiting_player' && !externallyLocked ? 'input' : 'default';
        // 先让宿主知道剧场已接管，再把输入区设为可见：否则 goodbye 状态下的“恢复输入区”
        // 会在剧场投影生效前为普通聊天请求一次 Galgame 选项。
        chatHost.setViewProps({
            theaterPresentation: presentation(),
            chatSurfaceMode: 'compact',
            compactChatState: compactState,
            composerDisabled: externallyLocked || (state.active && state.phase !== 'awaiting_player')
        });
        claimComposerVisibility(chatHost);
        // 打字机每个字都会渲染；openWindow 会重挂窗口并重新请求普通 Galgame 选项，每个 Session 只需打开一次。
        if (state.active && !state.windowClaimed && typeof chatHost.openWindow === 'function') {
            state.windowClaimed = true;
            chatHost.openWindow();
        }
        return true;
    }
    function submitFromHost(text) {
        void submit(text, 'freeform').catch(function () {
            if (!state.active) return;
            state.phase = 'awaiting_player';
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            render();
        });
    }
    function submitSuggestedFromHost(text) {
        var source = state.invitationRecoveryAvailable && text === t('theater.reinvite', '重新邀请') ? 'reinvite' : 'suggestion';
        void submit(text, source).catch(function () {
            if (!state.active) return;
            state.phase = 'awaiting_player';
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            render();
        });
    }
    function bindHostCallbacks() {
        var chatHost = host();
        if (!chatHost) return false;
        if (typeof chatHost.setOnTheaterSubmit === 'function') {
            // 玩家自由输入直接进入剧场 Runtime，不经过普通聊天或猫娘局部聊天路由。
            chatHost.setOnTheaterSubmit(submitFromHost);
        }
        if (typeof chatHost.setOnTheaterSuggestedInputSelect === 'function') {
            // 推荐输入直接进入 Runtime 提交流程，不借用输入框草稿或普通 Galgame 回填链路。
            chatHost.setOnTheaterSuggestedInputSelect(submitSuggestedFromHost);
        }
        if (typeof chatHost.setOnTheaterEnd === 'function') chatHost.setOnTheaterEnd(function () { runtime.requestEnd(); });
        render();
        return true;
    }
    function waitForHost() {
        if (bindHostCallbacks()) return Promise.resolve(true);
        return new Promise(function (resolve) {
            var attempts = 80;
            window.clearInterval(state.hostReadyTimer);
            state.hostReadyTimer = window.setInterval(function () {
                attempts -= 1;
                var ready = bindHostCallbacks();
                if (ready || attempts <= 0) {
                    window.clearInterval(state.hostReadyTimer); state.hostReadyTimer = 0; resolve(ready);
                }
            }, 100);
        });
    }
    function applySnapshot(snapshot) {
        var session = snapshot.session || {};
        var participants = snapshot.participants || {};
        state.storyId = String(session.story_package_id || state.storyId);
        state.sessionId = String(session.session_id || state.sessionId);
        state.revision = Number(session.revision || 0);
        state.lifecycleRevision = Number(session.lifecycle_revision || 0);
        state.sessionStatus = String(session.status || 'active');
        // 玩家和猫娘署名都由服务端当前绑定提供，恢复旧记录时也不回退成通用占位名。
        state.playerName = String(participants.player_name || t('theater.player', 'Player'));
        state.catgirlName = String(participants.catgirl_name || 'Neko');
        // 服务端剧场信号按响应里的原始猫娘名登记；退出时只释放这一个角色，不回退到展示占位名。
        state.activityCatgirlName = String(participants.catgirl_name || '').trim();
        state.scene = snapshot.scene || null;
        state.storyTitle = String(snapshot.story_title || state.storyTitle || state.storyId);
        state.suggestedInputs = Array.isArray(snapshot.suggested_inputs) ? snapshot.suggested_inputs.map(String) : [];
        state.invitationRecoveryAvailable = snapshot.invitation_recovery_available === true;
        if (snapshot.end_receipt_pending) {
            state.errorMessage = t('theater.endReceiptPending', '演出已经结束；记忆确认暂未准备好，请稍后重新打开选剧页。');
        } else if (snapshot.evaluator_degraded) {
            state.errorMessage = t('theater.evaluatorDegraded', '判定暂时不可用，本轮数值未推进；已显示的作者邀请仍可确认。');
        }
        // 保留最近一次服务端已提交快照；表现播放被打断时可直接恢复完整历史和推荐输入。
        committedSnapshot = snapshot;
    }
    function readingDelay(text) { return Math.min(5000, Math.max(1100, Array.from(String(text || '')).length * 55)); }
    // 语音优先等播放完成事件；事件丢失时按完整对白保守估时，不能沿用读字的 5 秒上限。
    function speechTimeout(text) { return 5000 + Array.from(String(text || '')).length * 300; }
    function wait(ms, token) {
        return new Promise(function (resolve) {
            window.setTimeout(function () { resolve(token === state.queueToken); }, ms);
        });
    }
    function waitForSpeech(speechId, timeoutMs, token) {
        return new Promise(function (resolve) {
            var done = false;
            var timer;
            function finish() {
                if (done) return; done = true;
                window.clearTimeout(timer);
                window.removeEventListener('neko-assistant-speech-end', onEnd);
                window.removeEventListener('neko-assistant-speech-unavailable', onEnd);
                window.removeEventListener('neko-assistant-speech-cancel', onEnd);
                speechEventRelays = speechEventRelays.filter(function (relay) { return relay !== onEnd; });
                resolve(token === state.queueToken);
            }
            function onEnd(event) {
                var turnId = event && event.detail && event.detail.turnId;
                // Ordinary-session shutdown can emit an uncorrelated event;
                // only this theater utterance may release its playback wait.
                if (speechId && turnId && String(turnId) === String(speechId)) finish();
            }
            window.addEventListener('neko-assistant-speech-end', onEnd);
            window.addEventListener('neko-assistant-speech-unavailable', onEnd);
            window.addEventListener('neko-assistant-speech-cancel', onEnd);
            // 桌面端音频在其他窗口播放时，播放边界经跨窗口转发到达。
            speechEventRelays.push(onEnd);
            timer = window.setTimeout(finish, timeoutMs);
        });
    }
    async function typeBlock(historyId, block, token, immediate) {
        var entry = state.history.find(function (candidate) { return candidate.id === historyId; });
        if (!entry) return false;
        var text = formatPresentationBlock(block);
        var separator = entry.text && !block.preserveSpacing ? '\n' : '';
        if (token !== state.queueToken) return false;
        if (immediate) {
            entry.text += separator + text;
            render();
            return token === state.queueToken;
        }
        var characters = Array.from(separator + text);
        for (var index = 0; index < characters.length; index += 1) {
            if (token !== state.queueToken) return false;
            entry.text += characters[index];
            render();
            if (!await wait(TYPEWRITER_INTERVAL_MS, token)) return false;
        }
        return token === state.queueToken;
    }
    async function playDialogue(group, block, blockIndex, revision, token) {
        var alive = true;
        if (block.type === 'dialogue') {
            var dialogueItems = group.blocks.filter(function (item) {
                return item.block.type === 'dialogue';
            });
            var dialogueBlockIndexes = dialogueItems.map(function (item) { return item.blockIndex; });
            var dialogueText = dialogueItems.map(function (item) { return item.block.text; }).join(' ');
            var playbackRequestId = 'theater_speech_' + state.sessionId + '_' + revision + '_' + state.lifecycleRevision + '_' + blockIndex;
            activeSpeechRequests[playbackRequestId] = token;
            publishSpeechAllowlist();
            var result;
            try {
                result = await requestJson(api.speakBlock, { method: 'POST', body: {
                    story_id: state.storyId, session_id: state.sessionId, revision: revision, block_index: blockIndex,
                    lifecycle_revision: state.lifecycleRevision,
                    dialogue_block_indexes: dialogueBlockIndexes,
                    playback_request_id: playbackRequestId
                }});
            } catch (_) {
                // TTS 是表现层旁路；请求失败时按阅读时长继续，不能中断正文播放或锁住输入。
                result = { ok: false };
            }
            if (result.ok && result.speech_id && (result.audio_queued || result.audio_sent)) alive = await waitForSpeech(result.speech_id, speechTimeout(dialogueText), token);
            else alive = await wait(readingDelay(dialogueText), token);
            delete activeSpeechRequests[playbackRequestId];
            publishSpeechAllowlist();
        }
        return alive && token === state.queueToken;
    }
    async function playPerformance(performance, revision, options) {
        var token = ++state.queueToken;
        publishSpeechAllowlist();
        var groups = performanceHistoryGroups(performance, options && options.displayPhase || 'ordinary');
        var nextSuggestedInputs = state.suggestedInputs.slice();
        state.phase = 'performing'; state.suggestedInputs = []; render();
        if (options && options.playerInput && !options.playerAlreadyShown) {
            state.history.push(historyEntry('player-' + revision, 'player_action', options.playerInput, state.playerName));
        }
        var historyBaseId = options && options.historyId || 'performance-' + revision;
        // 开场首句前的场景和动作整段呈现；对白及后续段落仍按原有打字/语音时序播放。
        var openingPrefix = !!(options && options.displayPhase === 'opening');
        for (var groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
            var group = groups[groupIndex];
            var historyId = historyBaseId + '-' + groupIndex;
            state.history.push(historyEntry(
                historyId,
                group.type,
                '',
                group.type === 'dialogue' ? state.catgirlName : undefined,
                group.type === 'narration' ? 'scene' : undefined,
                'streaming'
            ));
            render();
            var speechPromise = null;
            for (var itemIndex = 0; itemIndex < group.blocks.length; itemIndex += 1) {
                var item = group.blocks[itemIndex];
                if (item.block.type === 'dialogue') openingPrefix = false;
                // 同一演绎段只在首个对白块发起一次合并 TTS；动作与后续对白仍按原顺序逐字显示。
                if (!speechPromise && item.block.type === 'dialogue') {
                    speechPromise = playDialogue(group, item.block, item.blockIndex, revision, token);
                }
                if (!await typeBlock(historyId, item.block, token, openingPrefix)) return;
            }
            if (speechPromise && !await speechPromise) return;
            var completedEntry = state.history.find(function (entry) { return entry.id === historyId; });
            if (completedEntry) completedEntry.status = 'sent';
            render();
        }
        if (state.sessionStatus === 'ended') {
            if (state.scene && state.scene.ending) state.history.push(historyEntry('ending-' + state.sessionId, 'ending', [state.scene.ending.title, state.scene.ending.summary].filter(Boolean).join('：')));
            state.phase = 'ended';
        } else {
            state.phase = 'awaiting_player';
            state.suggestedInputs = nextSuggestedInputs;
        }
        render();
    }
    function isCurrentLaunch(launchToken, storyId, sessionId) {
        return launchToken === launchEpoch
            && state.active
            && state.storyId === storyId
            && state.sessionId === sessionId;
    }
    async function prepareLaunchSurface(message, launchToken, boundCatgirlName) {
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        // 普通主动搭话只在小剧场运行期间暂停，退出时恢复进入前的用户状态。
        lockProactiveChatForTheater();
        stopPeerOrdinaryVoiceInput(boundCatgirlName);
        // 小剧场只接管文本胶囊；必须先停掉普通语音 Session，避免 ASR 和普通回复穿插进演绎。
        if (!await stopOrdinaryVoiceInput() || launchToken !== launchEpoch) {
            restoreProactiveChatAfterTheater();
            return false;
        }
        if (pendingLaunch && pendingLaunch.token === launchToken && pendingLaunch.activityClaimId) {
            if (state.activityClaimId && state.activityClaimId !== pendingLaunch.activityClaimId) {
                releaseServerTheaterActivity(state.activityCatgirlName, state.activityClaimId);
            }
            state.activityClaimId = pendingLaunch.activityClaimId;
        }
        var chatHost = host();
        captureOrdinaryDraft(chatHost);
        captureChatSurfaceMode(chatHost);
        if (state.storyId !== nextStoryId || state.sessionId !== nextSessionId) {
            // Reuse the capsule's draft projection for an accepted session change.
            // A failed launch or same-session replay must retain the current input.
            state.draftRestore = { id: createId('theater_draft_restore_'), text: '' };
        }
        if (state.active) {
            // 即使重新启动同一 Session，也必须先使旧正文和旧语音失效，避免两个播放协程交错写回。
            claimAudioPlayback();
            state.queueToken += 1;
            state.pendingTurn = null;
            publishSpeechAllowlist();
        }
        state.errorMessage = '';
        state.tokenUsage = message.token_usage || null;
        state.pendingEnd = null;
        pendingSelectorEnd = null;
        // 新启动（含同一 Session 被恢复后重新接管）使进行中的结束流程失效，迟到的结束响应不能关闭它。
        state.endInFlight = null;
        committedSnapshot = null;
        state.sessionStatus = '';
        state.scene = null;
        state.revision = 0;
        state.lifecycleRevision = 0;
        state.suggestedInputs = [];
        state.storyTitle = String(message.story_title || state.storyTitle || nextStoryId);
        state.history = [historyEntry(
            'opening-loading-' + nextSessionId,
            'narration',
            t('theater.loading', '正在准备舞台...'),
            undefined,
            'scene',
            'streaming'
        )];
        state.active = true;
        state.phase = 'loading';
        state.storyId = nextStoryId;
        state.sessionId = nextSessionId;
        state.windowClaimed = false;
        render();
        var hostReady = await waitForHost();
        if (!isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) return false;
        if (!hostReady) {
            clear('launch-host-unavailable');
            return false;
        }
        return true;
    }
    async function completeLaunch(message, launchToken, snapshot) {
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        if (!isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) return false;
        state.tokenUsage = message.token_usage || null;
        applySnapshot(snapshot);
        state.history = buildCommittedHistory(snapshot);
        if (snapshot.end_receipt_id) state.pendingEnd = {
            story_id: state.storyId, session_id: state.sessionId, revision: state.revision,
            end_receipt_id: snapshot.end_receipt_id, archive_request_id: snapshot.archive_request_id || ''
        };
        state.active = true;
        rememberPointer();
        claimAudioPlayback();
        var readyMessage = postMessage({ action: 'theater:launch-ready', launch_id: message.launch_id, story_id: state.storyId, session_id: state.sessionId });
        postDirect(launchReplyTargets[message.launch_id], readyMessage);
        delete launchReplyTargets[message.launch_id];
        if (message.launch_action === 'start' || message.launch_action === 'restart') {
            state.history = [];
            await playPerformance(snapshot.session.opening_performance, 0, {
                displayPhase: 'opening',
                historyId: 'opening-performance'
            });
        } else {
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            render();
        }
        return true;
    }
    async function performLaunch(message, launchToken) {
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        var snapshot;
        try {
            snapshot = await requestJson(api.session + '/' + encodeURIComponent(nextSessionId) + '?claim_activity=false&story_id=' + encodeURIComponent(nextStoryId));
        } catch (_) {
            // 候选快照读取失败时还未接管全局状态，保留当前健康演绎并只结束本次启动。
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        // 多个选剧页可能交错启动；候选快照通过世代与 revision 校验后才有权接管当前运行态。
        if (launchToken !== launchEpoch) {
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        if (!snapshot.ok || !snapshot.session || Number(snapshot.session.revision) !== Number(message.revision)) {
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        message.story_title = snapshot.story_title || message.story_title;
        var boundCatgirlName = snapshot.participants && snapshot.participants.catgirl_name;
        if (!pendingLaunch || pendingLaunch.cancelled) return false;
        var claim = createId('theater_activity_');
        if (pendingLaunch && pendingLaunch.token === launchToken) {
            pendingLaunch.activityClaimId = claim;
            pendingLaunch.catgirlName = boundCatgirlName;
        }
        var accepted = false;
        try {
            var claimed = await requestJson(api.session + '/' + encodeURIComponent(nextSessionId) + '?story_id=' + encodeURIComponent(nextStoryId), { activityClaimId: claim });
            if (claimed.activity_claimed === false || launchToken !== launchEpoch || !pendingLaunch || pendingLaunch.cancelled) return false;
            if (!claimed.ok || !claimed.session || Number(claimed.session.revision) !== Number(message.revision)) return false;
            snapshot = claimed;
            boundCatgirlName = snapshot.participants && snapshot.participants.catgirl_name;
            message.story_title = snapshot.story_title || message.story_title;
            if (!await prepareLaunchSurface(message, launchToken, boundCatgirlName)) return false;
            accepted = await completeLaunch(message, launchToken, snapshot);
            return accepted;
        } finally {
            if (!accepted) releaseServerTheaterActivity(boundCatgirlName, claim);
        }
    }
    async function performStart(message, launchToken) {
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        var startClaim = pendingLaunch && pendingLaunch.activityClaimId;
        if (!await prepareLaunchSurface(message, launchToken)) return false;
        // 胶囊接管完成后即可关闭选剧页；模型生成继续由本体持有，不受选剧窗口生命周期影响。
        var startPromise = requestJson(api.start, { method: 'POST', activityClaimId: startClaim, body: {
            story_id: nextStoryId,
            session_id: nextSessionId,
            character_id: String(message.character_id),
            replace_existing: message.replace_existing === true
        }});
        var startReadyMessage = postMessage({ action: 'theater:start-ready', launch_id: message.launch_id, story_id: nextStoryId, session_id: nextSessionId });
        postDirect(launchReplyTargets[message.launch_id], startReadyMessage);
        delete launchReplyTargets[message.launch_id];
        var snapshot;
        try {
            snapshot = await startPromise;
        } catch (_) {
            snapshot = { ok: false };
        }
        if (isCurrentLaunch(launchToken, nextStoryId, nextSessionId)
            && snapshot.ok && snapshot.resumed === true && snapshot.session
            && snapshot.session.status === 'ended'
            && snapshot.session.ended_reason === 'cancelled_start') {
            // Starting a new performance must not adopt a retired recovery slot.
            // Keep ordinary continue/restore semantics and retry replacement once.
            // The ended response retires its activity owner as well.
            releaseServerTheaterActivity('', startClaim);
            startClaim = createId('theater_activity_');
            pendingLaunch.activityClaimId = startClaim;
            state.activityClaimId = startClaim;
            if (snapshot.session.session_id === nextSessionId) {
                nextSessionId = createId('theater_session_');
                message.session_id = nextSessionId;
                pendingLaunch.sessionId = nextSessionId;
                state.sessionId = nextSessionId;
            }
            try {
                snapshot = await requestJson(api.start, {method: 'POST', activityClaimId: startClaim, body: {
                    story_id: nextStoryId, session_id: nextSessionId,
                    character_id: String(message.character_id), replace_existing: true
                }});
            } catch (_) { snapshot = {ok: false}; }
        }
        if (!isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) {
            // 开场生成期间已退出或被新启动取代：迟到的开场结果只丢弃，不能重新接管胶囊。
            if (snapshot.ok && snapshot.resumed !== true && snapshot.session
                && snapshot.session.session_id === nextSessionId && snapshot.session.status === 'active') {
                // Only retire the session created by this cancelled request.
                // Revision/lifecycle checks protect a session another operation advanced.
                try {
                    await requestJson(api.end, {method: 'POST', activityClaimId: startClaim, body: {
                        story_id: nextStoryId, session_id: nextSessionId,
                        cancelled_start: true,
                        base_revision: snapshot.session.revision,
                        base_lifecycle_revision: snapshot.session.lifecycle_revision || 0
                    }});
                } catch (_) {}
            }
            releaseServerTheaterActivity('', startClaim);
            return false;
        }
        if (!snapshot.ok || !snapshot.session) {
            state.phase = 'ended';
            state.sessionStatus = 'ended';
            state.history = [];
            state.errorMessage = t('theater.startFailed', '启动演出失败，请重试。');
            render();
            return false;
        }
        // The start broadcast went out before the binding was known and named this
        // window's character; repeat it for the bound character if that differs.
        var startedCatgirlName = String(snapshot.participants && snapshot.participants.catgirl_name || '').trim();
        if (startedCatgirlName && startedCatgirlName !== ownLanlanName()) stopPeerOrdinaryVoiceInput(startedCatgirlName);
        message.token_usage = snapshot.token_usage || null;
        message.launch_action = snapshot.resumed ? 'continue' : (message.replace_existing === true ? 'restart' : 'start');
        message.revision = Number(snapshot.session.revision);
        return completeLaunch(message, launchToken, snapshot);
    }
    function launch(message) {
        var launchId = String(message.launch_id || '');
        if (launchRequests[launchId]) return launchRequests[launchId];
        var launchToken = ++launchEpoch;
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        pendingLaunch = { token: launchToken, storyId: nextStoryId, sessionId: nextSessionId, activityClaimId: createId('theater_activity_') };
        var request = performLaunch(message, launchToken).catch(function () {
            if (isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) clear('launch-request-failed');
            return false;
        }).finally(function () {
            delete launchReplyTargets[launchId];
            if (pendingLaunch && pendingLaunch.token === launchToken) pendingLaunch = null;
        });
        launchRequests[launchId] = request;
        launchRequestOrder.push(launchId);
        if (launchRequestOrder.length > 64) delete launchRequests[launchRequestOrder.shift()];
        return request;
    }
    function startLaunch(message) {
        var launchId = String(message.launch_id || '');
        if (launchRequests[launchId]) return launchRequests[launchId];
        var launchToken = ++launchEpoch;
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        pendingLaunch = { token: launchToken, storyId: nextStoryId, sessionId: nextSessionId, activityClaimId: createId('theater_activity_') };
        // A cancelled opening must finish its retirement before the next start
        // can resume the same Session without advancing its lifecycle.
        var request = startCleanupBarrier.then(function () {
            if (launchToken !== launchEpoch) return false;
            return performStart(message, launchToken);
        }).catch(function () {
            var failedSessionId = pendingLaunch && pendingLaunch.token === launchToken
                ? pendingLaunch.sessionId : nextSessionId;
            if (isCurrentLaunch(launchToken, nextStoryId, failedSessionId)) {
                state.phase = 'ended';
                state.sessionStatus = 'ended';
                state.history = [];
                state.errorMessage = t('theater.startFailed', '启动演出失败，请重试。');
                render();
            }
            return false;
        }).then(function (result) {
            if (!result) {
                var failedMessage = postMessage({action: 'theater:start-failed', launch_id: launchId});
                postDirect(launchReplyTargets[launchId], failedMessage);
            }
            return result;
        }).finally(function () {
            delete launchReplyTargets[launchId];
            if (pendingLaunch && pendingLaunch.token === launchToken) pendingLaunch = null;
        });
        startCleanupBarrier = request;
        launchRequests[launchId] = request;
        launchRequestOrder.push(launchId);
        if (launchRequestOrder.length > 64) delete launchRequests[launchRequestOrder.shift()];
        return request;
    }
    async function submit(text, inputSource) {
        var message = String(text || '').trim();
        var normalizedInputSource = inputSource === 'reinvite' ? 'reinvite' : inputSource === 'suggestion' ? 'suggestion' : 'freeform';
        if (!state.active || state.phase !== 'awaiting_player' || !message) return false;
        var signature = state.sessionId + '\u001f' + state.revision + '\u001f' + normalizedInputSource + '\u001f' + message;
        if (!state.pendingTurn || state.pendingTurn.signature !== signature) state.pendingTurn = { signature: signature, id: createId('theater_turn_') };
        // 请求期间可能从另一个选剧页切换剧本；响应只能写回发起它的 Session。
        var submittedStoryId = state.storyId;
        var submittedSessionId = state.sessionId;
        var submittedLaunchEpoch = launchEpoch;
        var submittedTurnId = state.pendingTurn.id;
        var submittedSuggestedInputs = state.suggestedInputs.slice();
        var optimisticHistoryId = 'player-pending-' + state.pendingTurn.id;
        // 玩家行动先进入历史区，让推荐输入和手动提交都立即得到可见反馈。
        if (normalizedInputSource !== 'reinvite' && !state.history.some(function (entry) { return entry.id === optimisticHistoryId; })) {
            state.history.push(historyEntry(optimisticHistoryId, 'player_action', message, state.playerName));
        }
        state.phase = 'evaluating'; state.suggestedInputs = []; state.draftRestore = null; state.errorMessage = ''; render();
        var result;
        try {
            result = await requestJson(api.input, { method: 'POST', body: {
                story_id: state.storyId, session_id: state.sessionId, client_turn_id: state.pendingTurn.id,
                base_revision: state.revision, message: message, input_source: normalizedInputSource
            }});
        } catch (_) {
            result = { ok: false, reason: 'numeric_input_request_failed' };
        }
        if (
            !state.active
            || state.storyId !== submittedStoryId
            || state.sessionId !== submittedSessionId
        ) {
            // 服务端可能已经提交旧 Session；这里只丢弃迟到显示，恢复时仍会读到权威历史。
            return false;
        }
        if (!state.pendingTurn || state.pendingTurn.id !== submittedTurnId) return false;
        // 先过滤迟到请求，再呈现本次成功或失败的真实用量；网络断线时不能显示上一轮。
        state.tokenUsage = result.token_usage || null;
        if (!result.ok) {
            // 退出流程已经接管时，迟到失败不能重新打开输入区。
            if (state.phase !== 'evaluating') return false;
            var refreshed = null;
            if (result.reason === 'numeric_base_revision_mismatch'
                || result.reason === 'numeric_reinvitation_not_available'
                || result.reason === 'numeric_suggested_input_not_current'
                || result.reason === 'numeric_duplicate_client_turn_id'
                || result.reason === 'session_already_ended') {
                try {
                    refreshed = await requestJson(api.session + '/' + encodeURIComponent(submittedSessionId) + '?story_id=' + encodeURIComponent(submittedStoryId));
                } catch (_) {
                    // 刷新失败统一交给下方提示，不恢复已知过期的按钮。
                }
            }
            // 刷新期间继续保持忙碌；返回后再次确认交互归属，避免污染新演绎或退出阶段。
            if (!state.active || state.storyId !== submittedStoryId || state.sessionId !== submittedSessionId
                || !state.pendingTurn || state.pendingTurn.id !== submittedTurnId || state.phase !== 'evaluating') return false;
            state.history = state.history.filter(function (entry) { return entry.id !== optimisticHistoryId; });
            state.draftRestore = { id: createId('theater_draft_restore_'), text: normalizedInputSource === 'reinvite' ? '' : message };
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            // 只有当前接口明确在提交前返回的模型失败才恢复旧按钮；断网仍保留原输入与幂等编号。
            if (['numeric_v2_actor_failed', 'numeric_v2_actor_unavailable',
                'numeric_v2_evaluator_failed', 'numeric_v2_evaluator_unavailable'].indexOf(result.reason) >= 0) {
                state.suggestedInputs = submittedSuggestedInputs;
                // 推荐点击前草稿为空；回填按钮文字会再次把推荐隐藏。
                if (normalizedInputSource === 'suggestion') state.draftRestore.text = '';
            }
            // 冲突快照仍只回写原提交世代；不能用提交前按钮覆盖新的进度。
            if (isCurrentLaunch(submittedLaunchEpoch, submittedStoryId, submittedSessionId)
                && state.pendingTurn.id === submittedTurnId && refreshed && refreshed.ok) {
                applySnapshot(refreshed);
                state.history = buildCommittedHistory(refreshed);
                if (refreshed.end_receipt_id) {
                    state.pendingEnd = {
                        story_id: state.storyId, session_id: state.sessionId, revision: state.revision,
                        end_receipt_id: refreshed.end_receipt_id, archive_request_id: refreshed.archive_request_id || ''
                    };
                }
                state.errorMessage = state.sessionStatus === 'ended'
                    ? t('theater.ended', '已结束')
                    : normalizedInputSource === 'reinvite'
                        ? t('theater.numericSessionUpdatedControl', '演出状态已更新，请确认当前可用操作。')
                        : t('theater.numericSessionUpdated', '演出状态已更新，已保留你的输入，请确认后重试。');
            }
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            render();
            return false;
        }
        state.pendingTurn = null;
        // 成功回合已经推进权威 revision；此前发起的同 Session 启动快照不得再覆盖新历史。
        if (pendingLaunch && pendingLaunch.storyId === submittedStoryId
            && pendingLaunch.sessionId === submittedSessionId) launchEpoch += 1;
        applySnapshot(result);
        if (result.end_receipt_id) state.pendingEnd = {
            story_id: state.storyId,
            session_id: state.sessionId,
            revision: state.revision,
            end_receipt_id: result.end_receipt_id,
            archive_request_id: result.archive_request_id || ''
        };
        if (state.endInFlight) {
            // 玩家已确认结束：先行提交的回合只更新权威历史，不再开始播放；结束流程会按新 revision 收尾。
            state.history = buildCommittedHistory(result);
            state.draftRestore = null;
            return true;
        }
        if (result.idempotent_replay === true) {
            // 上一次请求可能已在服务端提交但响应丢失；幂等重放只返回权威快照，
            // 不会再次返回 performance。必须用快照重建历史，不能留下乐观玩家气泡或漏掉猫娘回复。
            state.history = buildCommittedHistory(result);
            state.draftRestore = null;
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            render();
            return true;
        }
        try {
            await playPerformance(result.performance, state.revision, {
                playerInput: message,
                playerAlreadyShown: true
            });
        } catch (_) {
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            state.errorMessage = t('theater.performanceFailed', '演绎播放中断，请继续输入或重新打开小剧场。');
            render();
            return false;
        }
        return true;
    }
    function releaseServerTheaterActivity(catgirlName, activityClaimId) {
        // 服务端对主动搭话和普通语音的兜底按最近一次剧场请求计时（TTL 到期自动失效）；
        // 退出而未结束演绎时显式释放本窗口演绎的角色，避免 TTL 内把已恢复的普通语音误拦，
        // 也不影响其他窗口正在演绎的角色。失败只等 TTL。
        if (!catgirlName && !activityClaimId) return;
        var payload = { catgirl_name: catgirlName || '' };
        if (activityClaimId) payload.activity_claim_id = activityClaimId;
        try {
            Promise.resolve(requestJson(api.release, {
                method: 'POST', body: payload
            })).catch(function () {});
        } catch (_) {}
    }
    function clear(reason) {
        var wasActive = state.active === true;
        var releasedCatgirlName = state.activityCatgirlName;
        var releasedClaim = state.activityClaimId;
        if (pendingLaunch && pendingLaunch.activityClaimId) {
            launchEpoch += 1;
            pendingLaunch.cancelled = true;
            releaseServerTheaterActivity(pendingLaunch.catgirlName || '', pendingLaunch.activityClaimId);
        }
        if (state.active && state.phase !== 'loading') claimAudioPlayback();
        state.queueToken += 1;
        state.active = false; state.phase = 'inactive'; state.history = []; state.suggestedInputs = [];
        state.playerName = ''; state.catgirlName = ''; state.activityCatgirlName = ''; state.activityClaimId = ''; state.windowClaimed = false;
        state.endInFlight = null;
        if (wasActive) publishSpeechAllowlist();
        restoreProactiveChatAfterTheater();
        state.pendingTurn = null; state.draftRestore = null;
        state.pendingEnd = null;
        pendingSelectorEnd = null;
        committedSnapshot = null;
        rememberPointer();
        var chatHost = host();
        restoreComposerVisibility(chatHost);
        restoreChatSurfaceMode(chatHost);
        if (chatHost && typeof chatHost.setViewProps === 'function') {
            // full/compact 恢复可能重挂 React；草稿恢复必须作为最后一次视图更新交付，
            // 否则前一步刚写回的普通聊天草稿会被后续重挂清空。
            chatHost.setViewProps({
                theaterPresentation: {
                    active: false,
                    phase: 'inactive',
                    history: [],
                    suggestedInputs: [],
                    ordinaryDraftRestore: state.ordinaryDraftRestore
                },
                // 只撤销剧场自己的禁用；首页教程等外部输入锁仍由宿主持有。
                composerDisabled: hostComposerLocked(chatHost)
            });
        }
        window.dispatchEvent(new CustomEvent('neko:theater-cleared', { detail: { reason: reason || 'clear' } }));
        if (wasActive || releasedClaim) releaseServerTheaterActivity(releasedCatgirlName, releasedClaim);
    }
    function openSelector(receipt) {
        state.pendingEnd = receipt || state.pendingEnd;
        var url = '/theater?story_id=' + encodeURIComponent(state.storyId);
        try {
            if (typeof window.openOrFocusWindow === 'function') {
                return window.openOrFocusWindow(url, 'neko_theater', 'width=1100,height=760,menubar=no,toolbar=no,location=no,status=no', { navigateOnReuse: true });
            }
            return window.open(url, 'neko_theater');
        } catch (_) {
            return null;
        }
    }
    function restoreSelectorWindow(target) {
        if (!target || target.closed) return false;
        try {
            if (typeof window.requestOpenedWindowRestore === 'function') {
                window.requestOpenedWindowRestore(target);
            } else {
                postDirect(target, { type: 'neko:restore-window' });
            }
        } catch (_) {}
        try {
            if (typeof target.focus === 'function') target.focus();
        } catch (_) {}
        return true;
    }
    function returnToSelector(receipt, clearReason, preparedSelector) {
        state.pendingEnd = receipt || state.pendingEnd;
        state.sessionStatus = 'ended';
        state.phase = 'returning_selector';
        state.errorMessage = '';
        render();
        var selectorTarget = preparedSelector || openSelector(state.pendingEnd);
        if (!selectorTarget) {
            // 已退出 Session 只能从选剧页继续；本体保留只读历史和返回按钮作为恢复入口。
            state.phase = 'ended';
            state.errorMessage = t(
                'theater.selectorReturnFailed',
                '已退出演绎，但剧本页面打开失败。请点击“返回剧本页”重试。'
            );
            render();
            return false;
        }
        // 预先打开的选剧页可能早于结束接口返回完成加载，需要在拿到回执后再主动补发一次。
        sendPendingEnd(selectorTarget);
        // 确认弹窗关闭和结束请求都会把焦点留回本体；提交成功后必须再次恢复选剧页。
        restoreSelectorWindow(selectorTarget);
        var deliveryReceipt = state.pendingEnd;
        clear(clearReason);
        pendingSelectorEnd = deliveryReceipt ? {target: selectorTarget, receipt: deliveryReceipt} : null;
        return true;
    }
    function sendPendingEnd(target) {
        var receipt = pendingSelectorEnd && pendingSelectorEnd.target === target
            ? pendingSelectorEnd.receipt : state.pendingEnd;
        if (!receipt) return;
        var content = Object.assign({ action: 'theater:post-end', message_id: createId('theater_post_end_') }, receipt);
        // 已知选剧页时只直发；直发失败才广播，避免同一回执通过两个传输通道重复到达。
        if (target) {
            var directMessage = transport.createMessage('theater-runtime', content);
            if (postDirect(target, directMessage)) return;
        }
        postMessage(content);
    }
    async function confirmEnd(onConfirmed) {
        var message = t('theater.endConfirm', '确定结束当前演绎吗？');
        if (typeof window.showConfirm === 'function') {
            return window.showConfirm(
                message,
                t('theater.endPerformance', '结束演绎'),
                {
                    okText: t('common.confirm', '确认'),
                    cancelText: t('common.cancel', '取消'),
                    danger: true,
                    skin: 'theater',
                    onResolve: function (confirmed) {
                        if (confirmed && typeof onConfirmed === 'function') onConfirmed();
                    }
                }
            );
        }
        // 极早启动阶段统一弹窗尚未加载时保留原生确认，不能静默结束演绎。
        return window.confirm(message);
    }
    function isEndRevisionConflict(result) {
        return !!result && (result.reason === 'numeric_base_revision_mismatch'
            || result.reason === 'numeric_base_lifecycle_revision_mismatch');
    }
    async function requestEnd() {
        if (!state.active || endConfirmationPending) return false;
        if (state.sessionStatus === 'ended' || state.phase === 'ended') {
            return returnToSelector(state.pendingEnd, 'natural-ending-return');
        }
        var requestedStoryId = state.storyId;
        var requestedSessionId = state.sessionId;
        // 玩家确认的是结束这一场演绎，而不是某个 revision：确认期间先行提交的输入回合
        // 只会推进 revision，结束意图仍然有效；只有切换到其他 Session 才作废本次结束。
        function isSameSession() {
            return state.active
                && state.storyId === requestedStoryId
                && state.sessionId === requestedSessionId;
        }
        endConfirmationPending = true;
        var confirmed = false;
        var preparedSelector = null;
        try {
            confirmed = await confirmEnd(function () {
                // 必须在确认按钮的原始点击事件里取得窗口句柄；等待结束接口后再打开会被桌面窗口策略拦截。
                if (isSameSession()) preparedSelector = openSelector();
            });
        } finally {
            endConfirmationPending = false;
        }
        // 取消只关闭确认框，Session、输入和演绎历史都保持原样。
        if (!confirmed || !isSameSession()) return false;
        if (state.phase === 'loading' && committedSnapshot === null) {
            // 开场生成（最长 180 s）期间允许退出：此时还没有可结束的已提交 Session。
            // 推进启动世代使迟到的开场结果只会被丢弃（并释放其服务端剧场信号），不能重新接管。
            launchEpoch += 1;
            pendingLaunch = null;
            if (preparedSelector) restoreSelectorWindow(preparedSelector);
            clear('opening-cancelled');
            return true;
        }
        if (state.sessionStatus === 'ended' || state.phase === 'ended') {
            return returnToSelector(state.pendingEnd, 'natural-ending-return', preparedSelector);
        }
        var endToken = {};
        state.endInFlight = endToken;
        function ownsEnd() { return isSameSession() && state.endInFlight === endToken; }
        state.phase = 'ending'; state.errorMessage = ''; state.queueToken += 1; publishSpeechAllowlist(); render();
        var requestedRevision = state.revision;
        var requestedLifecycleRevision = state.lifecycleRevision;
        var result;
        var endRequestFailed = false;
        for (var attempt = 0; ; attempt += 1) {
            endRequestFailed = false;
            try {
                result = await requestJson(api.end, { method: 'POST', body: {
                    story_id: requestedStoryId,
                    session_id: requestedSessionId,
                    base_revision: requestedRevision,
                    base_lifecycle_revision: requestedLifecycleRevision
                } });
            } catch (_) {
                endRequestFailed = true;
                result = { ok: false };
            }
            // 结束接口返回前也可能切换 Session；旧响应不能改变新 Session 的阶段或回执。
            if (!ownsEnd()) return false;
            if (result.ok || endRequestFailed || attempt >= 1 || !isEndRevisionConflict(result)) break;
            // 确认结束后输入回合先提交：读取权威快照，按最新 revision 重试一次，不能静默丢弃结束。
            var refreshed = null;
            try {
                refreshed = await requestJson(api.session + '/' + encodeURIComponent(requestedSessionId) + '?story_id=' + encodeURIComponent(requestedStoryId));
            } catch (_) {
                refreshed = null;
            }
            if (!ownsEnd()) return false;
            if (!refreshed || !refreshed.ok || !refreshed.session) break;
            applySnapshot(refreshed);
            state.history = buildCommittedHistory(refreshed);
            if (state.sessionStatus === 'ended') {
                state.endInFlight = null;
                var endedReceipt = refreshed.end_receipt_id ? {
                    story_id: state.storyId, session_id: state.sessionId, revision: state.revision,
                    end_receipt_id: refreshed.end_receipt_id, archive_request_id: refreshed.archive_request_id || ''
                } : state.pendingEnd;
                return returnToSelector(endedReceipt, 'user-ended', preparedSelector);
            }
            requestedRevision = state.revision;
            requestedLifecycleRevision = state.lifecycleRevision;
        }
        state.endInFlight = null;
        if (!result.ok) {
            var snapshot = committedSnapshot;
            var committedSession = snapshot && snapshot.session && typeof snapshot.session === 'object'
                ? snapshot.session
                : null;
            if (
                committedSession
                && String(committedSession.story_package_id || '') === requestedStoryId
                && String(committedSession.session_id || '') === requestedSessionId
                && Number(committedSession.revision || 0) === requestedRevision
            ) {
                // 结束动作已经取消逐字播放；失败时从已提交快照重建，不能留下截断正文和空推荐项。
                applySnapshot(snapshot);
                state.history = buildCommittedHistory(snapshot);
            }
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            // 只有请求本身未取得响应时才提示本地服务连接；后端拒绝属于业务状态错误。
            state.errorMessage = endRequestFailed
                ? t('theater.endConnectionFailed', '无法连接 N.E.K.O 本地服务，请确认程序仍在运行后重试。')
                : t('theater.endStateFailed', '当前演绎状态无法结束，请返回剧本页后重试。');
            render();
            return false;
        }
        var receipt = {
            story_id: state.storyId,
            session_id: state.sessionId,
            revision: result.session.revision,
            end_receipt_id: result.end_receipt_id,
            archive_request_id: result.archive_request_id || ''
        };
        state.revision = result.session.revision;
        return returnToSelector(receipt, 'user-ended', preparedSelector);
    }
    async function restorePointer() {
        // 桌面端只有独立紧凑胶囊拥有演绎投影；Pet 与 full 窗口不能从各自会话存储
        // 恢复并重复驱动正文或 TTS。Web 单页没有该宿主角色，仍保留原刷新恢复语义。
        var role = desktopRuntimeRole();
        if (role && role !== 'compact') return;
        var pointer = readPointer();
        if (!pointer) return;
        // 启动恢复只属于读取指针时的 launch 世代；任何更新的选剧启动都有更高优先级。
        var restoreLaunchEpoch = launchEpoch;
        // Refresh restoration is a pending launch too: end/delete notifications
        // can arrive before its GET resolves, while the runtime is still inactive.
        var restoreClaim = createId('theater_activity_');
        pendingLaunch = { token: restoreLaunchEpoch, storyId: pointer.story_id, sessionId: pointer.session_id, activityClaimId: restoreClaim };
        try {
            var snapshot;
            try {
                snapshot = await requestJson(api.session + '/' + encodeURIComponent(pointer.session_id) + '?story_id=' + encodeURIComponent(pointer.story_id), { activityClaimId: restoreClaim });
            } catch (_) {
                // 暂时性网络失败保留指针供下次恢复，但不让启动 Promise 产生未处理拒绝。
                return;
            }
            if (restoreLaunchEpoch !== launchEpoch) return;
            if (!snapshot.ok || !snapshot.session) { try { window.sessionStorage.removeItem(POINTER_KEY); } catch (_) {} return; }
            applySnapshot(snapshot);
            if (snapshot.end_receipt_id) state.pendingEnd = {
                story_id: state.storyId,
                session_id: state.sessionId,
                revision: state.revision,
                end_receipt_id: snapshot.end_receipt_id,
                archive_request_id: snapshot.archive_request_id || ''
            };
            state.active = true; state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player'; state.history = buildCommittedHistory(snapshot);
            // 刷新前记录的进入前形态优先；宿主此刻的形态可能已是剧场覆盖出的 compact。
            if (state.chatSurfaceModeRestore === null && CHAT_SURFACE_MODES.indexOf(pointer.chat_surface_mode) >= 0) {
                state.chatSurfaceModeRestore = pointer.chat_surface_mode;
            }
            // 刷新恢复的会话同样要暂停普通主动搭话，与正常启动保持一致。
            lockProactiveChatForTheater();
            stopPeerOrdinaryVoiceInput(state.activityCatgirlName);
            state.activityClaimId = restoreClaim;
            var hostReady = await waitForHost();
            if (restoreLaunchEpoch !== launchEpoch) return;
            if (!hostReady) {
                // 指针恢复同样依赖 React 胶囊；宿主不可用时清除不可见运行态和失效指针。
                clear('pointer-host-unavailable');
                return;
            }
            render();
        } finally {
            if (!state.active || state.activityClaimId !== restoreClaim) releaseServerTheaterActivity('', restoreClaim);
            if (pendingLaunch && pendingLaunch.token === restoreLaunchEpoch) pendingLaunch = null;
        }
    }
    function handleCrossWindowMessage(event) {
        if (event && event.origin && event.origin !== window.location.origin) return;
        var message = event && event.data;
        if (!message || typeof message !== 'object') return;
        if (String(message.action || '').indexOf('theater:') === 0 && message.schema !== MESSAGE_SCHEMA) return;
        // A candidate is not active yet while ordinary voice shuts down.
        // Matching lifecycle events must still revoke its right to take over.
        if (pendingLaunch && message.story_id === pendingLaunch.storyId && (
            message.action === 'theater:story-deleted'
            || (message.action === 'theater:external-end' && message.session_id === pendingLaunch.sessionId)
        )) {
            launchEpoch += 1;
            pendingLaunch = null;
            if (!state.active) rememberPointer();
        }
        if (message.action === 'theater:host-probe' && message.probe_id) {
            var candidateRole = desktopRuntimeRole();
            if (candidateRole && candidateRole !== 'compact') return;
            postMessage({action: 'theater:host-candidate', probe_id: message.probe_id,
                runtime_host_id: runtimeHostId, visible: document.visibilityState === 'visible'});
        }
        else if ((message.action === 'theater:launch-ready' || message.action === 'theater:start-ready') && message.launch_id) {
            stopDesktopLaunchRelay(message.launch_id);
        }
        else if (
            (message.action === 'theater:launch-request' || message.action === 'theater:start-request')
            && message.launch_id
            && message.story_id
            && message.session_id
            && (message.action === 'theater:start-request' || Number.isInteger(message.revision))
        ) {
            if (message.runtime_host_id && message.runtime_host_id !== runtimeHostId) return;
            var role = desktopRuntimeRole();
            if (role === 'pet') {
                if (message.runtime_host_kind) return;
                // 选剧页通常由 Pet 打开，window.opener 会把启动请求直送 Pet；必须显式转交
                // 给独立胶囊，不能在不可见的 Pet React 宿主里只播放 TTS。
                relayLaunchToDesktopChat(message);
                return;
            }
            // 桌面 full 与 compact 页面可能同时存活；小剧场固定进入本体紧凑胶囊，
            // 只允许 compact Runtime 接管，避免两个窗口重复请求正文和 TTS。
            if (role && role !== 'compact') return;
            if (message.runtime_host_kind && role && message.runtime_host_kind !== role) return;
            if (event.source && event.source !== window) launchReplyTargets[message.launch_id] = event.source;
            if (message.action === 'theater:start-request') {
                var acceptedMessage = postMessage({ action: 'theater:start-accepted', launch_id: message.launch_id,
                    story_id: message.story_id, session_id: message.session_id });
                postDirect(launchReplyTargets[message.launch_id], acceptedMessage);
                startLaunch(message);
            }
            else launch(message);
        }
        else if (message.action === 'theater:selector-ready') sendPendingEnd(event.source);
        else if (message.action === 'theater:post-end-accepted' && pendingSelectorEnd
            && message.end_receipt_id === pendingSelectorEnd.receipt.end_receipt_id) pendingSelectorEnd = null;
        else if (message.action === 'theater:ordinary-voice-stop' && !state.active) {
            // A theater started in another window: stop this window's ordinary mic or
            // voice session, exactly as the theater window stops its own before launch,
            // unless this window talks to a different character.
            if (isOrdinaryVoiceStopForThisWindow(message)) stopOrdinaryVoiceInput().catch(function () {});
        }
        else if (message.action === 'theater:speech-allowlist') applyPeerSpeechAllowlist(message);
        else if (message.action === 'theater:speech-event' && state.active && message.turn_id) {
            var relayedEvent = { type: String(message.event || ''), detail: { turnId: String(message.turn_id) } };
            speechEventRelays.slice().forEach(function (relay) { relay(relayedEvent); });
        }
        else if (
            message.action === 'theater:external-end'
            && state.active
            && message.story_id === state.storyId
            && message.session_id === state.sessionId
        ) clear('selector-ended');
        else if (
            message.action === 'theater:story-deleted'
            && state.active
            && message.story_id === state.storyId
        ) clear('story-deleted');
        else if (message.action === 'catgirl_switched') {
            // 角色切换即使发生在启动指针恢复期间，也必须立即使旧角色的异步快照失效。
            launchEpoch += 1;
            if (state.active) clear('catgirl-switched');
        }
    }

    var runtime = {
        isActive: function () { return state.active; },
        suppressesProactiveChat: suppressesProactiveChat,
        blocksOrdinaryVoice: blocksOrdinaryVoice,
        blocksOrdinaryChat: blocksOrdinaryVoice,
        allowsSpeechCorrelation: function (requestId) {
            var id = String(requestId || '');
            if (state.active) return activeSpeechRequests[id] === state.queueToken;
            // 本窗口未演绎时只接受其他窗口剧场当前仍有效的播放请求（Electron Pet 持有唯一真实音频通道）。
            return peerAllowsSpeech(id);
        },
        handleComposerSubmit: function (text) {
            if (!state.active) return false;
            submitFromHost(text);
            return true;
        },
        requestEnd: requestEnd,
        clear: clear,
        getState: function () { return Object.assign({}, state, { history: state.history.slice() }); }
    };
    window.nekoTheaterRuntime = runtime;

    if (typeof BroadcastChannel !== 'undefined') {
        try { state.channel = new BroadcastChannel('neko_page_channel'); state.channel.addEventListener('message', handleCrossWindowMessage); } catch (_) { state.channel = null; }
    }
    window.addEventListener('message', handleCrossWindowMessage);
    window.addEventListener('pagehide', withdrawSpeechAllowlistOnUnload);
    window.addEventListener('beforeunload', withdrawSpeechAllowlistOnUnload);
    if (window.document && typeof window.document.addEventListener === 'function') {
        window.document.addEventListener('visibilitychange', republishSpeechAllowlistOnVisibilityChange);
    }
    ['neko-assistant-speech-end', 'neko-assistant-speech-unavailable', 'neko-assistant-speech-cancel'].forEach(function (name) {
        window.addEventListener(name, relaySpeechEventToPeer);
    });
    window.addEventListener('localechange', function () {
        if (!state.active) return;
        // 语言切换会让聊天宿主重建基础 props；等宿主处理完成后恢复仍在进行的剧场投影。
        window.setTimeout(function () {
            if (state.active) render();
        }, 0);
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', restorePointer);
    else restorePointer();
})();
