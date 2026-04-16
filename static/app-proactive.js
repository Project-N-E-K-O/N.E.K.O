/**
 * app-proactive.js — 主动搭话（Proactive Chat）模块
 *
 * 包含：
 *   - syncProactiveFlags (no-op, 由 app-state.js defineProperty 桥接代替)
 *   - hasAnyChatModeEnabled / canTriggerProactively
 *   - scheduleProactiveChat / stopProactiveChatSchedule
 *   - triggerProactiveChat / _showProactiveChatSourceLinks
 *   - resetProactiveChatBackoff
 *   - getAvailablePersonalPlatforms
 *   - sendOneProactiveVisionFrame
 *   - startProactiveVisionDuringSpeech / stopProactiveVisionDuringSpeech
 *   - captureProactiveChatScreenshot / acquireProactiveVisionStream / releaseProactiveVisionStream
 *   - isWindowsOS (helper)
 *   - captureCanvasFrame / captureFrameFromStream / acquireOrReuseCachedStream (screen-capture helpers)
 *   - fetchBackendScreenshot / scheduleScreenCaptureIdleCheck (screen-capture helpers)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;

    // ======================== proactive leader election ========================
    //
    // 背景：index.html（Pet 主窗口）和 chat.html（聊天浮窗）共用 app-proactive.js，
    // 各自跑 setTimeout 调度，会同时发 /api/proactive_chat 请求 / 推屏幕帧。
    // 后端把它们当两次独立请求处理，结果双倍 LLM 调用、双倍音乐推荐、双倍 vision 帧。
    //
    // 约定：Pet (index.html) 为主，chat.html 为从。同时存活时只有 Pet 跑调度；
    // Pet 关闭后 chat.html 通过 TTL 自动接班。
    //
    // 协议：广播 'neko_proactive_leader'。每 5s 心跳，15s TTL。
    // rank 越小越优先：Pet=0, chat.html=1, 其它页面=99（不参与）。
    //
    const PROACTIVE_LEADER_CHANNEL = 'neko_proactive_leader';
    const PROACTIVE_LEADER_HEARTBEAT_MS = 5000;
    const PROACTIVE_LEADER_TTL_MS = 15000;
    const PROACTIVE_LEADER_RECHECK_MS = 8000; // 非 leader 的自检周期

    const PROACTIVE_SELF_ID = (Date.now().toString(36) + Math.random().toString(36).slice(2, 10));

    function _computeSelfRank() {
        try {
            const path = (window.location && window.location.pathname) || '';
            // chat.html 浮窗 → 从节点
            if (path === '/chat') return 1;
            // 不参与 proactive 的页面（model_manager / jukebox / subtitle / agenthud / toast / cookies_login 等）
            // 它们本来就不加载 app-proactive.js，但保险起见显式归类为不参与
            if (
                path === '/model_manager' || path === '/l2d' ||
                path === '/live2d_parameter_editor' || path === '/jukebox' ||
                path === '/jukebox/manager' || path === '/subtitle' ||
                path === '/agenthud' || path === '/toast'
            ) return 99;
            // 其它（/、/{lanlan_name}）一律视为 Pet 主窗口
            return 0;
        } catch (_) {
            return 0;
        }
    }
    const PROACTIVE_SELF_RANK = _computeSelfRank();

    // peer_id -> { rank, expireAt }
    const _proactivePeers = new Map();
    let _proactiveLeaderChannel = null;
    let _proactiveLeaderHeartbeatTimer = null;
    let _wasLeaderLastTick = null; // 用于 leader 状态切换时主动 reschedule

    try {
        if (typeof BroadcastChannel !== 'undefined' && PROACTIVE_SELF_RANK !== 99) {
            _proactiveLeaderChannel = new BroadcastChannel(PROACTIVE_LEADER_CHANNEL);
            _proactiveLeaderChannel.onmessage = function (event) {
                const data = event && event.data;
                if (!data || typeof data !== 'object') return;
                if (!data.id || data.id === PROACTIVE_SELF_ID) return;
                if (data.type === 'announce' || data.type === 'heartbeat') {
                    const isNewPeer = !_proactivePeers.has(data.id);
                    _proactivePeers.set(data.id, {
                        rank: typeof data.rank === 'number' ? data.rank : 99,
                        expireAt: Date.now() + PROACTIVE_LEADER_TTL_MS
                    });
                    // 新 peer 上线：立即回一个 heartbeat，让它在第一次决策前就能感知到我，
                    // 避免新窗口在 announce 后的"无人响应"窗口里误以为只有自己。
                    if (data.type === 'announce') {
                        _proactiveBroadcast('heartbeat');
                    }
                    // 拓扑变化（新 peer 或 announce）时重新评估自己的角色
                    if (isNewPeer || data.type === 'announce') {
                        _onProactiveLeadershipMaybeChanged();
                    }
                } else if (data.type === 'goodbye') {
                    _proactivePeers.delete(data.id);
                    _onProactiveLeadershipMaybeChanged();
                } else if (data.type === 'user_input_reset') {
                    // 分发环境（Electron）下 chat.html 承担文本输入，但 proactive 计时器
                    // 只在 index.html (leader) 运行。chat.html 本地调 resetProactiveChatBackoff
                    // 对 leader 的 S.proactiveChatBackoffLevel 不可见，因此转成 IPC 转发到
                    // 所有窗口，由 leader 真正重置退避级别 + 重排 timer。
                    // { _fromIpc: true } 阻止二次广播，靠 data.id !== SELF_ID 已避免回环。
                    try {
                        resetProactiveChatBackoff({ _fromIpc: true });
                    } catch (e) {
                        console.warn('[Proactive] 处理 user_input_reset IPC 失败:', e);
                    }
                }
            };
        }
    } catch (e) {
        console.log('[Proactive] BroadcastChannel 不可用，主备协调失效:', e);
    }

    function _proactiveBroadcast(type) {
        if (!_proactiveLeaderChannel) return;
        try {
            _proactiveLeaderChannel.postMessage({
                type: type || 'heartbeat',
                id: PROACTIVE_SELF_ID,
                rank: PROACTIVE_SELF_RANK,
                ts: Date.now()
            });
        } catch (_) { /* ignore */ }
    }

    function _purgeStaleProactivePeers() {
        const now = Date.now();
        let removed = false;
        for (const [id, info] of _proactivePeers) {
            if (now > info.expireAt) {
                _proactivePeers.delete(id);
                removed = true;
            }
        }
        return removed;
    }

    function isProactiveLeader() {
        if (PROACTIVE_SELF_RANK === 99) return false; // 不参与的页面永远不是
        _purgeStaleProactivePeers();
        // 找出存活节点中最优 rank（含自己），同 rank 时 ID 字典序小者胜
        let bestRank = PROACTIVE_SELF_RANK;
        let bestId = PROACTIVE_SELF_ID;
        for (const [id, info] of _proactivePeers) {
            if (info.rank < bestRank || (info.rank === bestRank && id < bestId)) {
                bestRank = info.rank;
                bestId = id;
            }
        }
        return bestId === PROACTIVE_SELF_ID;
    }
    mod.isProactiveLeader = isProactiveLeader;

    function _onProactiveLeadershipMaybeChanged() {
        const nowLeader = isProactiveLeader();
        if (_wasLeaderLastTick === nowLeader) return;
        _wasLeaderLastTick = nowLeader;
        console.log('[Proactive] 主备状态切换：自己现在' + (nowLeader ? '是 leader（开始调度 proactive_chat / vision）' : '是 follower（停止调度，等待 leader 失联）'));
        if (nowLeader) {
            // 接班：立刻安排一次 proactive_chat
            try { scheduleProactiveChat(); } catch (e) {
                console.warn('[Proactive] 接班时调度 proactive_chat 失败:', e);
            }
            // 接班：如果当前正在录音，启动 vision-during-speech
            try {
                if (S.isRecording) startProactiveVisionDuringSpeech();
            } catch (e) {
                console.warn('[Proactive] 接班时启动 vision-during-speech 失败:', e);
            }
        } else {
            // 让位：清掉本地 proactive 定时器和 vision 心跳
            if (S.proactiveChatTimer) {
                clearTimeout(S.proactiveChatTimer);
                S.proactiveChatTimer = null;
            }
            try { stopProactiveVisionDuringSpeech(); } catch (e) {
                console.warn('[Proactive] 让位时停止 vision-during-speech 失败:', e);
            }
        }
    }

    // 启动：先 announce 一下，再周期性 heartbeat
    if (_proactiveLeaderChannel) {
        _proactiveBroadcast('announce');
        _proactiveLeaderHeartbeatTimer = setInterval(function () {
            _proactiveBroadcast('heartbeat');
            // 心跳节奏顺手扫一下过期 peer，防止 leader 被关掉后 follower 不知情
            if (_purgeStaleProactivePeers()) {
                _onProactiveLeadershipMaybeChanged();
            }
        }, PROACTIVE_LEADER_HEARTBEAT_MS);
        // 窗口关闭前广播 goodbye，让对端立即接班
        window.addEventListener('beforeunload', function () {
            _proactiveBroadcast('goodbye');
            if (_proactiveLeaderHeartbeatTimer) {
                clearInterval(_proactiveLeaderHeartbeatTimer);
                _proactiveLeaderHeartbeatTimer = null;
            }
        });
    }

    // ======================== screen-capture helpers (delegate to app-screen.js) ========================

    function captureCanvasFrame(video, jpegQuality, detectBlack) {
        return window.appScreen.captureCanvasFrame(video, jpegQuality, detectBlack);
    }

    function captureFrameFromStream(stream, jpegQuality) {
        return window.appScreen.captureFrameFromStream(stream, jpegQuality);
    }

    function acquireOrReuseCachedStream(opts) {
        return window.appScreen.acquireOrReuseCachedStream(opts);
    }

    function fetchBackendScreenshot() {
        return window.appScreen.fetchBackendScreenshot();
    }

    function scheduleScreenCaptureIdleCheck() {
        return window.appScreen.scheduleScreenCaptureIdleCheck();
    }

    // ======================== syncProactiveFlags (no-op) ========================
    // app-state.js 使用 Object.defineProperty 进行双向绑定，
    // 因此不再需要手动同步 window.xxx <-> 本地变量。
    function syncProactiveFlags() {
        // no-op: bridged by app-state.js defineProperty
    }

    // ======================== proactive chat core ========================

    /**
     * 检查是否处于「请她离开」状态
     */
    function isGoodbyeActive() {
        return (window.live2dManager && window.live2dManager._goodbyeClicked) ||
            (window.vrmManager && window.vrmManager._goodbyeClicked) ||
            (window.mmdManager && window.mmdManager._goodbyeClicked);
    }

    /**
     * 检查是否有任何搭话方式被选中
     */
    function hasAnyChatModeEnabled() {
        return S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled ||
            S.proactiveVideoChatEnabled || S.proactivePersonalChatEnabled ||
            S.proactiveMusicEnabled || S.proactiveMemeEnabled;
    }
    mod.hasAnyChatModeEnabled = hasAnyChatModeEnabled;

    /**
     * 检查主动搭话前置条件是否满足
     */
    // AI 是否正在播放语音：proactive timer 到点时如果还在播，就跳过本次 nudge
    // 并继续按固定间隔 poll（见下面 scheduleProactiveChat 的两处 speaking 分支）。
    // S.isPlaying：audio chunks 入队到 drain 完这段期间为 true；
    // S.assistantSpeechActiveTurnId：active turn 有音频在跑时非空。
    // 两者任一为真都视为在播，避免打断自己。
    function _isAssistantSpeaking() {
        try {
            return !!(S && (S.isPlaying || S.assistantSpeechActiveTurnId));
        } catch (_) {
            return false;
        }
    }
    mod._isAssistantSpeaking = _isAssistantSpeaking;

    function canTriggerProactively() {
        // 「请她离开」状态下禁止一切主动搭话
        if (isGoodbyeActive()) {
            return false;
        }

        // 必须开启主动搭话
        if (!S.proactiveChatEnabled) {
            return false;
        }

        // 必须选择至少一种搭话方式
        if (!S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && !S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled && !S.proactiveMemeEnabled) {
            return false;
        }

        // 如果只选择了视觉搭话，需要同时开启自主视觉
        if (S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && !S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled && !S.proactiveMemeEnabled) {
            return S.proactiveVisionEnabled;
        }

        // 如果只选择了个人动态搭话，需要同时开启个人动态
        if (!S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled && !S.proactiveMemeEnabled) {
            return S.proactivePersonalChatEnabled;
        }

        // 音乐搭话和meme搭话不需要额外条件，总是允许
        return true;
    }
    mod.canTriggerProactively = canTriggerProactively;

    /**
     * 主动搭话定时触发功能
     */
    function scheduleProactiveChat() {
        // 清除现有定时器
        if (S.proactiveChatTimer) {
            clearTimeout(S.proactiveChatTimer);
            S.proactiveChatTimer = null;
        }

        // 主备协调：非 leader 不调度，只挂一个轻量的 recheck，
        // 一旦 leader 失联（peer 过期）就自动接班。
        if (!isProactiveLeader()) {
            console.log('[Proactive] 当前不是 leader，跳过调度，等待接班 (rank=' + PROACTIVE_SELF_RANK + ')');
            S.proactiveChatTimer = setTimeout(scheduleProactiveChat, PROACTIVE_LEADER_RECHECK_MS);
            return;
        }
        _wasLeaderLastTick = true;

        // 必须开启主动搭话且选择至少一种搭话方式才启动调度
        if (!S.proactiveChatEnabled || !hasAnyChatModeEnabled()) {
            S.proactiveChatBackoffLevel = 0;
            return;
        }

        // 前置条件检查：如果不满足触发条件，不启动调度器并重置退避
        if (!canTriggerProactively()) {
            console.log('主动搭话前置条件不满足，不启动调度器');
            S.proactiveChatBackoffLevel = 0;
            return;
        }

        // 如果主动搭话正在执行中，不安排新的定时器（等当前执行完成后自动安排）
        if (S.isProactiveChatRunning) {
            console.log('主动搭话正在执行中，延迟安排下一次');
            return;
        }

        // 语音模式：固定间隔（不退避），连续5轮无回复则停止
        if (S.isRecording) {
            if (S._voiceProactiveNoResponseCount >= 10) {
                console.log('[ProactiveChat] 语音模式连续5轮无回复，停止主动搭话');
                return;
            }
            var delay = S.proactiveChatInterval * 1000;
            console.log('[ProactiveChat] 语音模式：' + (delay / 1000) + '秒后触发（无退避，无回复计数：' + (S._voiceProactiveNoResponseCount || 0) + '/10）');

            S.proactiveChatTimer = setTimeout(async function () {
                if (S.isProactiveChatRunning) return;
                // 设计说明（by 用户意图）：
                // 这里不"rearm-after-playback"——那样每句话说完都要严格等满一个固定间隔
                // 才能接下一句，节奏太死板。改为"继续按固定间隔轮询"：
                // 轮询到时 AI 还在说 → 跳过本次 nudge，不累加 _voiceProactiveNoResponseCount
                // （没真发请求就不算无回复），但仍然 scheduleProactiveChat() 推进下一 tick。
                // 结果：播放完成到下一次 nudge 的等待 ∈ [0, interval)，带随机感，更自然。
                if (_isAssistantSpeaking()) {
                    console.log('[ProactiveChat] 语音模式：AI 正在播放语音，本次 nudge 跳过（不计数），继续下一 tick');
                    scheduleProactiveChat();
                    return;
                }
                S.isProactiveChatRunning = true;
                try {
                    await triggerProactiveChat();
                } finally {
                    S.isProactiveChatRunning = false;
                }
                S._voiceProactiveNoResponseCount = (S._voiceProactiveNoResponseCount || 0) + 1;
                // 不在这里 scheduleProactiveChat()——等 AI turn end 后再调度下一次，
                // 避免 AI 还在说话就被下一次 nudge 打断。
                // turn end handler 中会对语音模式调用 scheduleProactiveChat()。
                // 如果本次 nudge 被 guard 跳过（pass），AI 不会响应也不会有 turn end，
                // 所以 pass 时仍需自行调度。
                if (S._voiceProactiveLastResult === 'pass') {
                    scheduleProactiveChat();
                }
            }, delay);
            return;
        }

        // 文本模式：指数退避（带小幅随机指数浮动，避免节奏过于机械）
        var baseInterval = S.proactiveChatInterval;

        // 在指数上叠加 ±0.125 的随机漂移 → 实际倍率波动约 [0.89x, 1.12x]，幅度很小但有变化
        var expJitter = (Math.random() - 0.5) * 0.25;
        var effectiveExp = S.proactiveChatBackoffLevel + expJitter;
        var delay = (baseInterval * 1000) * Math.pow(1.8, effectiveExp);

        // 首次启动时额外等待 6 秒，避免程序刚启动就触发音乐推荐。
        // 用一次性 flag 而非 backoffLevel === 0 —— 后者在 user_input reset 或
        // speaking-skip 重排时也会命中，导致每次都重新叠 6s，把 skip 路径期望的
        // "等待 ∈ [0, interval)" 变成 "interval + 6s"。
        var startupDelay = 0;
        if (!S._proactiveStartupDelayApplied) {
            startupDelay = 6000;
            S._proactiveStartupDelayApplied = true;
        }
        delay += startupDelay;

        // Clamp：level 长期上爬后 (level ≥ ~25 @ base=15s) `1.8^level` 会把 delay
        // 顶到超过 setTimeout 的 int32 上限 0x7fffffff ≈ 24.8 天，实际被截断成
        // "1ms 后立刻 fire"。加个硬上限保险，实际封顶在 ~24 天，已足够长。
        delay = Math.min(delay, 0x7fffffff);

        console.log('主动搭话：' + (delay / 1000).toFixed(1) + '秒后触发（基础间隔：' + S.proactiveChatInterval + '秒，退避级别：' + S.proactiveChatBackoffLevel + '，指数漂移：' + expJitter.toFixed(2) + '，启动延迟：' + (startupDelay / 1000) + '秒）');

        S.proactiveChatTimer = setTimeout(async function () {
            // 双重检查锁：定时器触发时再次检查是否正在执行
            if (S.isProactiveChatRunning) {
                console.log('主动搭话定时器触发时发现正在执行中，跳过本次');
                return;
            }

            // 设计说明（by 用户意图）：
            // 不 rearm-after-playback —— 那样每句话说完都要等满一个固定间隔，节奏太死。
            // 改为"继续按间隔轮询"：轮询到时 AI 还在说 → 跳过本次，不累加 backoffLevel
            // （没真发请求就不算一次尝试），但仍然 scheduleProactiveChat() 推进下一 tick。
            // 结果：播放完成到下一次 nudge 的等待 ∈ [0, interval)，带随机感，更自然。
            if (_isAssistantSpeaking()) {
                console.log('[ProactiveChat] 文本模式：AI 正在播放语音，本次跳过（不累加退避），继续下一 tick');
                scheduleProactiveChat();
                return;
            }

            console.log('触发主动搭话...');
            S.isProactiveChatRunning = true; // 加锁

            var triggered = false;
            try {
                triggered = await triggerProactiveChat();
            } finally {
                S.isProactiveChatRunning = false; // 解锁
            }

            // 增加退避级别（仅在实际发送了请求时才累加，冷却期/前置条件未满足的跳过不计入）：
            //   level < 3 时每次必升（15s → 27s → 49s → 87s ≈ 1.5min），温和拉开间隔；
            //   level ≥ 3 后改为 30% 概率升级，让"长期无人搭理"的情况间隔能继续慢慢变长，
            //     但大多数轮次仍停在当前档位，避免一次跳太远。
            if (triggered) {
                if (S.proactiveChatBackoffLevel < 3) {
                    S.proactiveChatBackoffLevel++;
                } else if (Math.random() < 0.3) {
                    S.proactiveChatBackoffLevel++;
                    console.log('[ProactiveChat] 高档位概率升级命中，退避级别升至 ' + S.proactiveChatBackoffLevel);
                }
            }

            // 安排下一次
            scheduleProactiveChat();
        }, delay);
    }
    mod.scheduleProactiveChat = scheduleProactiveChat;

    // ======================== getAvailablePersonalPlatforms ========================

    /**
     * 获取个人媒体cookies所有可用平台的函数
     */
    async function getAvailablePersonalPlatforms() {
        try {
            var response = await fetch('/api/auth/cookies/status');
            if (!response.ok) return [];

            var result = await response.json();
            var availablePlatforms = [];

            if (result.success && result.data) {
                for (var _ref of Object.entries(result.data)) {
                    var platform = _ref[0];
                    var info = _ref[1];
                    if (platform !== 'platforms' && info.has_cookies) {
                        availablePlatforms.push(platform);
                    }
                }
            }
            return availablePlatforms;
        } catch (error) {
            console.error('获取可用平台列表失败:', error);
            return [];
        }
    }
    mod.getAvailablePersonalPlatforms = getAvailablePersonalPlatforms;

    // ======================== triggerProactiveChat ========================

    async function triggerProactiveChat() {
        var requestSent = false;
        try {
            // 主备协调：本窗口非 leader 时不触发，避免和 Pet 主窗口重复发请求。
            // 这里再 guard 一次是为了防止 leader 切换后旧定时器仍然触发。
            if (!isProactiveLeader()) {
                console.log('[ProactiveChat] 当前不是 leader，跳过触发');
                return;
            }
            // 「请她离开」状态下不触发
            if (isGoodbyeActive()) {
                console.log('[ProactiveChat] goodbye 状态，跳过本次触发');
                return;
            }
            // ── 语音模式快速路径：直接发 voice_mode 请求，后端注入预录音频 ──
            if (S.isRecording) {
                var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                var voiceModes = [];
                if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled && S.proactiveVisionEnabled) {
                    voiceModes.push('vision');
                }
                console.log('[ProactiveChat] 语音模式快速路径，modes: [' + voiceModes.join(', ') + ']');
                var resp = await fetch('/api/proactive_chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        lanlan_name: lanlanName,
                        enabled_modes: voiceModes,
                        voice_mode: true
                    })
                });
                requestSent = true;
                var result = await resp.json();
                S._voiceProactiveLastResult = result.action || 'unknown';
                console.log('[ProactiveChat] 语音模式结果:', S._voiceProactiveLastResult);
                return true;
            }

            var availableModes = [];
            // 收集所有启用的搭话方式
            // 视觉搭话：需要同时开启主动搭话和自主视觉
            // 同时触发 vision 和 window 模式
            if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled && S.proactiveVisionEnabled) {
                availableModes.push('vision');
                availableModes.push('window');
            }

            // 新闻搭话：使用微博热议话题
            if (S.proactiveNewsChatEnabled && S.proactiveChatEnabled) {
                availableModes.push('news');
            }

            // 视频搭话：使用B站首页视频
            if (S.proactiveVideoChatEnabled && S.proactiveChatEnabled) {
                availableModes.push('video');
            }

            // 个人动态搭话：使用B站和微博个人动态
            if (S.proactivePersonalChatEnabled && S.proactiveChatEnabled) {
                // 检查是否有可用的 Cookie 凭证
                var platforms = await getAvailablePersonalPlatforms();
                if (platforms.length > 0) {
                    availableModes.push('personal');
                    console.log('[个人动态] 模式已启用，平台: ' + platforms.join(', '));
                } else {
                    // 如果开关开了但没登录，不把 personal 发给后端，避免后端抓取失败报错
                    console.warn('[个人动态] 开关已开启但未检测到登录凭证，已忽略此模式');
                }
            }

            // 音乐搭话（正在播放或冷却期内不发送 music 模式，避免后端搜歌浪费 + 污染模型上下文）
            console.log('[ProactiveChat] 检查音乐模式: proactiveMusicEnabled=' + S.proactiveMusicEnabled + ', proactiveChatEnabled=' + S.proactiveChatEnabled);
            if (S.proactiveMusicEnabled && S.proactiveChatEnabled) {
                var musicPlaying = (typeof window.isMusicPlaying === 'function') && window.isMusicPlaying();
                var musicCooldown = (typeof window.isMusicCooldown === 'function') && window.isMusicCooldown();
                if (musicPlaying || musicCooldown) {
                    console.log('[ProactiveChat] 音乐模式跳过: playing=' + musicPlaying + ', cooldown=' + musicCooldown);
                } else {
                    console.log('[ProactiveChat] 音乐模式已启用');
                    availableModes.push('music');
                }
            }

            // Meme搭话
            if (S.proactiveMemeEnabled && S.proactiveChatEnabled) {
                console.log('[ProactiveChat] Meme模式已启用');
                availableModes.push('meme');
            }

            // 如果没有选择任何搭话方式，跳过本次搭话
            if (availableModes.length === 0) {
                console.log('未选择任何搭话方式，跳过本次搭话');
                return;
            }

            console.log('主动搭话：启用模式 [' + availableModes.join(', ') + ']，将并行获取所有信息源');

            var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
            var requestBody = {
                lanlan_name: lanlanName,
                enabled_modes: availableModes,
                is_playing_music: (typeof window.isMusicPlaying === 'function') ? window.isMusicPlaying() : false,
                current_track: (typeof window.getMusicCurrentTrack === 'function') ? window.getMusicCurrentTrack() : null,
                music_cooldown: (typeof window.isMusicCooldown === 'function') ? window.isMusicCooldown() : false
            };

            // 独立计时器：确保 vision/window 模式的屏幕感知间隔不低于 proactiveVisionInterval
            if (availableModes.includes('vision') || availableModes.includes('window')) {
                var now = Date.now();
                var minIntervalMs = S.proactiveVisionInterval * 1000;
                var elapsed = now - S._lastProactiveChatScreenTime;
                if (elapsed < minIntervalMs) {
                    console.log('[ProactiveChat] 屏幕感知间隔不足（已过 ' + Math.round(elapsed / 1000) + '秒，最低 ' + S.proactiveVisionInterval + '秒），本轮跳过 vision/window');
                    availableModes = availableModes.filter(function (m) { return m !== 'vision' && m !== 'window'; });
                    requestBody.enabled_modes = availableModes;
                    if (availableModes.length === 0) {
                        console.log('跳过屏幕感知后无其他可用模式，取消本次搭话');
                        return;
                    }
                }
            }

            // 如果包含 vision 模式，需要在前端获取截图和窗口标题
            if (availableModes.includes('vision') || availableModes.includes('window')) {
                var fetchTasks = [];
                var screenshotIndex = -1;
                var windowTitleIndex = -1;

                if (availableModes.includes('vision')) {
                    screenshotIndex = fetchTasks.length;
                    fetchTasks.push(captureProactiveChatScreenshot());
                }

                if (availableModes.includes('window')) {
                    windowTitleIndex = fetchTasks.length;
                    fetchTasks.push(fetch('/api/get_window_title')
                        .then(function (r) { return r.json(); })
                        .catch(function () { return { success: false }; }));
                }

                var results = await Promise.all(fetchTasks);

                // await 期间检查状态
                if (!canTriggerProactively()) {
                    console.log('功能已关闭或前置条件不满足，取消本次搭话');
                    return;
                }

                // await 期间用户可能切换模式，重新过滤可用模式
                var latestModes = [];
                if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled && S.proactiveVisionEnabled) {
                    latestModes.push('vision', 'window');
                }
                if (S.proactiveNewsChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('news');
                }
                if (S.proactiveVideoChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('video');
                }
                // 个人动态搭话：需要同时开启个人动态
                if (S.proactivePersonalChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('personal');
                }
                // 音乐搭话（重新检查冷却状态，await 期间可能变化）
                if (S.proactiveMusicEnabled && S.proactiveChatEnabled) {
                    var musicPlayingNow = (typeof window.isMusicPlaying === 'function') && window.isMusicPlaying();
                    var musicCooldownNow = (typeof window.isMusicCooldown === 'function') && window.isMusicCooldown();
                    if (!musicPlayingNow && !musicCooldownNow) {
                        latestModes.push('music');
                    }
                }
                // Meme搭话
                if (S.proactiveMemeEnabled && S.proactiveChatEnabled) {
                    latestModes.push('meme');
                }
                availableModes = availableModes.filter(function (m) { return latestModes.includes(m); });
                requestBody.enabled_modes = availableModes;
                if (availableModes.length === 0) {
                    console.log('await后无可用模式，取消本次搭话');
                    return;
                }

                if (screenshotIndex !== -1 && availableModes.includes('vision')) {
                    var screenshotDataUrl = results[screenshotIndex];
                    if (screenshotDataUrl) {
                        requestBody.screenshot_data = screenshotDataUrl;
                        if (window.unlockAchievement) {
                            window.unlockAchievement('ACH_SEND_IMAGE').catch(function (err) {
                                console.error('解锁发送图片成就失败:', err);
                            });
                        }
                    } else {
                        // 截图失败，从 enabled_modes 中移除 vision
                        console.log('截图失败，移除 vision 模式');
                        availableModes = availableModes.filter(function (m) { return m !== 'vision'; });
                        requestBody.enabled_modes = availableModes;
                    }
                }

                if (windowTitleIndex !== -1 && availableModes.includes('window')) {
                    var windowTitleResult = results[windowTitleIndex];
                    if (windowTitleResult && windowTitleResult.success && windowTitleResult.window_title) {
                        requestBody.window_title = windowTitleResult.window_title;
                        console.log('视觉搭话附加窗口标题:', windowTitleResult.window_title);
                    } else {
                        // 窗口标题获取失败，从 enabled_modes 中移除 window
                        console.log('窗口标题获取失败，移除 window 模式');
                        availableModes = availableModes.filter(function (m) { return m !== 'window'; });
                        requestBody.enabled_modes = availableModes;
                    }
                }

                if (availableModes.length === 0) {
                    console.log('所有附加模式均失败，移除后无其他可用模式，跳过本次搭话');
                    return;
                }

                // 更新屏幕感知时间戳（仅当 vision/window 实际保留时才消耗冷却）
                if (availableModes.includes('vision') || availableModes.includes('window')) {
                    S._lastProactiveChatScreenTime = Date.now();
                }
            }

            // 发送请求前最终检查：确保功能状态未在 await 期间改变
            if (!canTriggerProactively()) {
                console.log('发送请求前检查失败，取消本次搭话');
                return;
            }

            // 检测用户是否在20秒内有过输入，有过输入则作废本次主动搭话
            var timeSinceLastInput = Date.now() - (window.lastUserInputTime || 0);
            if (timeSinceLastInput < 20000) {
                console.log('主动搭话作废：用户在' + Math.round(timeSinceLastInput / 1000) + '秒前有过输入');
                return;
            }

            var response = await fetch('/api/proactive_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });
            requestSent = true;

            var result = await response.json();

            if (result.success) {
                if (result.action === 'chat') {
                    console.log('主动搭话已发送:', result.message, result.source_mode ? '(来源: ' + result.source_mode + ')' : '');

                    var dispatchedTrackUrl = null;

                    // 如果模式包含音乐信号，尝试播放第一条音轨
                    if ((result.source_mode === 'music' || result.source_mode === 'both') && result.source_links && Array.isArray(result.source_links)) {
                        // 优先寻找有 artist 字段或标记为音乐推荐的真实音轨
                        var normalizedLinks = result.source_links.filter(Boolean);
                        var musicLink = normalizedLinks.find(function (link) { return link && (link.artist || link.source === '音乐推荐'); }) || normalizedLinks[0];

                        if (musicLink && musicLink.url) {
                            console.log('[ProactiveChat] 收到音乐链接:', musicLink);
                            var track = {
                                name: musicLink.title || '未知曲目',
                                artist: musicLink.artist || '未知艺术家',
                                url: musicLink.url,
                                cover: musicLink.cover
                            };
                            console.log('[ProactiveChat] 发送音乐消息:', track);
                            var dispatchResult = await window.dispatchMusicPlay(track, { source: 'proactive' });

                            // 仅在明确成功派发时标记；'queued' 仍是等待态，不应提前隐藏链接
                            if (dispatchResult === true) {
                                dispatchedTrackUrl = musicLink.url;
                            }
                        } else if (musicLink) {
                            console.warn('[ProactiveChat] 音乐链接缺少URL:', musicLink);
                        }
                    }

                    // 【重构】统一处理链接，使用服务端返回的 turn_id 绑定，解决 HTTP/WS 竞态
                    var captureTurnId = result.turn_id || 'fallback';
                    var processed = _processProactiveLinks(result.source_links || [], dispatchedTrackUrl);

                    // 暂存待展示附件，等待对应的 turn_id 建立后统一 flush
                    if (!window._proactiveAttachmentBuffer) {
                        window._proactiveAttachmentBuffer = {};
                    }
                    if (!window._proactiveAttachmentBuffer[captureTurnId]) {
                        window._proactiveAttachmentBuffer[captureTurnId] = { memes: [], links: [] };
                    }
                    
                    if (processed.memeLinks.length > 0) {
                        var MAX_MEME_BUBBLES = 2;
                        window._proactiveAttachmentBuffer[captureTurnId].memes = processed.memeLinks.slice(0, MAX_MEME_BUBBLES);
                    }
                    
                    if (processed.otherLinks.length > 0) {
                        window._proactiveAttachmentBuffer[captureTurnId].links = processed.otherLinks;
                    }

                    // 如果当前 turn 已经就绪（例如主动搭话回复极快），直接 flush
                    if (window.realisticGeminiCurrentTurnId === captureTurnId) {
                        _flushProactiveAttachments(captureTurnId);
                    }

                    // 后端会直接通过session发送消息和TTS，前端无需处理显示
                } else if (result.action === 'pass') {
                    console.log('AI选择不搭话');
                }
            } else {
                console.warn('主动搭话失败:', result.error);
            }
            return true;
        } catch (error) {
            console.error('主动搭话触发失败:', error);
            return requestSent;
        }
    }
    mod.triggerProactiveChat = triggerProactiveChat;

    // ======================== attachment buffering ========================

    /**
     * 统一 flush 对应 turn_id 的主动搭话附件（表情包、来源卡片）
     */
    function _flushProactiveAttachments(turnId) {
        if (!window._proactiveAttachmentBuffer || !window._proactiveAttachmentBuffer[turnId]) {
            return; // 没有待展示的附件
        }
        
        var attachments = window._proactiveAttachmentBuffer[turnId];
        
        if (attachments.memes && attachments.memes.length > 0) {
            _showMemeBubbles(attachments.memes, turnId);
        }
        
        if (attachments.links && attachments.links.length > 0) {
            setTimeout(function () {
                _showProactiveSourceCards(attachments.links, turnId);
            }, 3000);
        }
        
        // flush 后清理 buffer
        delete window._proactiveAttachmentBuffer[turnId];
    }
    mod._flushProactiveAttachments = _flushProactiveAttachments;

    // ======================== source link card ========================

    /**
     * 在聊天区域临时显示来源链接卡片（旁路，不进入 AI 记忆）
     */
    /**
     * 将原始链接处理为分类好的安全链接对象
     */
    function _processProactiveLinks(links, dispatchedUrl) {
        var isSameUrl = function (u1, u2) {
            if (!u1 || !u2) return false;
            if (u1 === u2) return true;
            try {
                var url1 = new URL(u1, window.location.origin);
                var url2 = new URL(u2, window.location.origin);
                var getRef = function (u) { return (u.hostname + u.pathname.replace(/\/$/, '') + u.search).toLowerCase(); };
                return getRef(url1) === getRef(url2);
            } catch (e) { return u1 === u2; }
        };

        var memeLinks = [];
        var otherLinks = [];

        for (var i = 0; i < links.length; i++) {
            var link = links[i];
            if (!link) continue;

            var isMusicLink = link.artist || link.source === '音乐推荐' || (dispatchedUrl && isSameUrl(link.url, dispatchedUrl));
            if (isMusicLink) continue;

            var isMemeLink = link.type === 'meme' || link.type === 'gif';
            if (!isMemeLink) {
                var memeSourceKeywords = ['表情包', '斗图吧', '发表情', 'Imgflip', 'meme', 'sticker'];
                var linkSource = String(link.source || '').toLowerCase();
                for (var k = 0; k < memeSourceKeywords.length; k++) {
                    if (linkSource.indexOf(memeSourceKeywords[k].toLowerCase()) !== -1) {
                        isMemeLink = true;
                        break;
                    }
                }
            }
            if (!isMemeLink && link.url) {
                var memeDomains = ['qn.doutub.com', 'img.soutula.com', 'i.imgflip.com', 'doutub.com', 'fabiaoqing.com', 'soutula.com'];
                var linkHost = '';
                try {
                    var tempUrl = new URL(String(link.url), window.location.origin);
                    linkHost = tempUrl.hostname.toLowerCase();
                } catch (e) {}
                for (var m = 0; m < memeDomains.length; m++) {
                    if (linkHost === memeDomains[m] || linkHost.endsWith('.' + memeDomains[m])) {
                        isMemeLink = true;
                        break;
                    }
                }
            }

            var safeUrl = null;
            var rawUrl = String(link.url || '').trim();
            if (rawUrl && (rawUrl.startsWith('http://') || rawUrl.startsWith('https://'))) {
                try {
                    var u = new URL(rawUrl);
                    if (u.protocol === 'http:' || u.protocol === 'https:') {
                        safeUrl = u.href;
                    }
                } catch (e) {}
            }

            if (safeUrl) {
                if (isMemeLink) {
                    memeLinks.push(Object.assign({}, link, { safeUrl: safeUrl }));
                } else {
                    otherLinks.push(Object.assign({}, link, { safeUrl: safeUrl }));
                }
            }
        }
        return { memeLinks: memeLinks, otherLinks: otherLinks };
    }

    /**
     * 在聊天区域临时显示来源链接卡片
     */
    function _showProactiveSourceCards(otherLinks, targetTurnId) {
        try {
            if (window.realisticGeminiCurrentTurnId !== targetTurnId) return;
            var chatContent = document.getElementById('chat-content-wrapper');
            if (!chatContent || otherLinks.length === 0) return;

            var MAX_LINK_CARDS = 3;
            var existingCards = chatContent.querySelectorAll('.proactive-source-link-card');
            var overflow = existingCards.length - MAX_LINK_CARDS + 1;
            if (overflow > 0) {
                for (var j = 0; j < overflow; j++) {
                    existingCards[j].remove();
                }
            }

            var linkCard = document.createElement('div');
            linkCard.className = 'proactive-source-link-card';
            linkCard.style.cssText =
                'margin: 6px 12px; padding: 8px 14px; background: var(--bg-secondary, rgba(255,255,255,0.08));' +
                'border-left: 3px solid var(--accent-color, #6c8cff); border-radius: 8px;' +
                'font-size: 12px; opacity: 0; transition: opacity 0.4s ease; max-width: 320px; position: relative;';

            var closeBtn = document.createElement('span');
            closeBtn.textContent = '\u2715';
            closeBtn.style.cssText = 'position: absolute; top: 6px; right: 6px; cursor: pointer; color: var(--text-secondary, rgba(200,200,200,0.8)); font-size: 14px; font-weight: bold; line-height: 1; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: rgba(255,255,255,0.08); transition: color 0.2s, background 0.2s; z-index: 1;';
            closeBtn.addEventListener('click', function () { linkCard.style.opacity = '0'; setTimeout(function () { linkCard.remove(); }, 300); });
            linkCard.appendChild(closeBtn);

            for (var k = 0; k < otherLinks.length; k++) {
                (function (vl) {
                    var a = document.createElement('a');
                    a.href = vl.safeUrl;
                    a.textContent = '\uD83D\uDD17 ' + (vl.source ? '[' + vl.source + '] ' : '') + (vl.title || vl.url);
                    a.style.cssText = 'display: block; color: var(--accent-color, #6c8cff); text-decoration: none; padding: 3px 0; padding-right: 20px; word-break: break-all; font-size: 12px; cursor: pointer;';
                    a.addEventListener('click', function (e) {
                        e.preventDefault();
                        if (window.electronShell && window.electronShell.openExternal) { window.electronShell.openExternal(vl.safeUrl); }
                        else { window.open(vl.safeUrl, '_blank', 'noopener,noreferrer'); }
                    });
                    linkCard.appendChild(a);
                })(otherLinks[k]);
            }

            chatContent.appendChild(linkCard);
            chatContent.scrollTop = chatContent.scrollHeight;

            if (window.currentTurnGeminiAttachments) {
                window.currentTurnGeminiAttachments.push(linkCard);
            }

            requestAnimationFrame(function () { linkCard.style.opacity = '1'; });
            setTimeout(function () { linkCard.style.opacity = '0'; setTimeout(function () { linkCard.remove(); }, 500); }, 5 * 60 * 1000);
        } catch (e) {
            console.warn('显示来源链接失败:', e);
        }
    }

    function _showMemeBubbles(memeLinks, targetTurnId) {
        if (window.realisticGeminiCurrentTurnId !== targetTurnId) return;
        // [优化] 不再此处手动 addToHistory，因为正向的对话流(response_text) 已经由 finish_proactive_delivery 记录。
        // 表情包作为 UI 侧挂件展示，无需单独污染 LLM 上下文。
        if (!memeLinks || !Array.isArray(memeLinks) || memeLinks.length === 0) {
            return;
        }

        // 优先通过 React 聊天窗口 API 显示表情包
        var host = window.reactChatWindowHost;
        if (host && typeof host.appendMessage === 'function') {
            // PR #780 之后 proactive 只在 leader 触发，meme 只会暂存在 leader 的
            // _proactiveAttachmentBuffer 里，flush 到 host.appendMessage 也只写
            // 进 leader 的 React chat。用 music_ui 暴露的镜像 helper 同步到
            // 所有窗口，保证 chat.html（follower）也能看到表情包气泡。
            var mirrorAppend = window.__nekoMirrorChatAppend;
            for (var i = 0; i < memeLinks.length; i++) {
                (function (meme) {
                    if (!meme || !meme.safeUrl) return;
                    var proxyUrl = '/api/meme/proxy-image?url=' + encodeURIComponent(meme.safeUrl);
                    var now = new Date();
                    var timeStr = now.getHours().toString().padStart(2, '0') + ':' +
                        now.getMinutes().toString().padStart(2, '0');
                    var assistantName = '';
                    if (window.lanlan_config && window.lanlan_config.lanlan_name) assistantName = window.lanlan_config.lanlan_name;
                    else if (window._currentCatgirl) assistantName = window._currentCatgirl;
                    else if (window.currentCatgirl) assistantName = window.currentCatgirl;
                    assistantName = assistantName || 'Neko';
                    var avatarUrl = '';
                    if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                        avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl() || '';
                    }
                    var msg = {
                        id: 'meme-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                        role: 'assistant',
                        author: assistantName,
                        time: timeStr,
                        createdAt: Date.now(),
                        avatarLabel: assistantName.trim().slice(0, 1).toUpperCase(),
                        avatarUrl: avatarUrl || undefined,
                        blocks: [{ type: 'image', url: proxyUrl, alt: meme.title || 'Meme' }],
                        status: 'sent'
                    };
                    if (typeof mirrorAppend === 'function') {
                        // 本地 append + 广播镜像（music_ui.js 已装好监听器）
                        mirrorAppend(host, msg);
                    } else {
                        // 兜底：music_ui.js 未就绪时退化为只在本窗口显示
                        host.appendMessage(msg);
                    }
                    console.log('[Meme] 已展示图片气泡 (React):', meme.title);
                })(memeLinks[i]);
            }
            return;
        }

        // 回退：旧 DOM 方式（chatContainer 可见时）
        var chatContainer = S.dom.chatContainer || document.getElementById('chatContainer');
        if (!chatContainer) {
            console.warn('[Meme] chatContainer not found, cannot show meme bubbles');
            return;
        }

        for (var i = 0; i < memeLinks.length; i++) {
            (function (meme) {
                if (!meme || !meme.safeUrl) return;

                // 创建包含时间戳、表情和图片的统一气泡
                var imgBubble = document.createElement('div');
                imgBubble.classList.add('message', 'gemini', 'attachment');
                imgBubble.style.padding = '12px';
                imgBubble.style.textAlign = 'left';

                // 添加时间戳和 🎀 (复刻 createGeminiBubble 的头部)
                var now = new Date();
                var timestamp = now.getHours().toString().padStart(2, '0') + ':' +
                    now.getMinutes().toString().padStart(2, '0') + ':' +
                    now.getSeconds().toString().padStart(2, '0');

                var headerSpan = document.createElement('span');
                headerSpan.textContent = "[" + (window.appChat ? window.appChat.getCurrentTimeString() : timestamp) + "] \uD83C\uDF80 ";
                imgBubble.appendChild(headerSpan);

                // 添加图片容器（为了间距）
                var imgOuter = document.createElement('div');
                imgOuter.style.marginTop = '8px';
                imgOuter.style.textAlign = 'center';

                var proxyUrl = '/api/meme/proxy-image?url=' + encodeURIComponent(meme.safeUrl);
                var img = document.createElement('img');
                img.src = proxyUrl;
                img.alt = meme.title || 'Meme';
                img.style.cssText = 'max-width: 100%; max-height: 350px; border-radius: 8px; cursor: pointer; display: inline-block;';

                // 【修复】添加重试机制，最多重试 2 次
                var retryCount = 0;
                var maxRetries = 2;

                img.addEventListener('load', function () {
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                });
                img.addEventListener('click', function (e) {
                    if (img.dataset.failed === 'true') return;
                    e.preventDefault();
                    if (window.electronShell && window.electronShell.openExternal) {
                        window.electronShell.openExternal(meme.safeUrl);
                    } else {
                        window.open(meme.safeUrl, '_blank', 'noopener,noreferrer');
                    }
                });
                img.addEventListener('error', function () {
                    if (img.dataset.failed) return;
                    retryCount++;
                    if (retryCount <= maxRetries) {
                        console.log('[Meme] 加载失败，重试第', retryCount, '次:', meme.title);
                        // 添加随机参数避免缓存（proxyUrl 已包含 ?url=，所以用 &）
                        img.src = proxyUrl + '&retry=' + retryCount + '&t=' + Date.now();
                    } else {
                        img.dataset.failed = "true";
                        img.src = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iMTIwIiB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzg4OCIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxyZWN0IHg9IjMiIHk9IjMiIHdpZHRoPSIxOCIgaGVpZ2h0PSIxOCIgcng9IjIiIHJ5PSIyIjPjwvcmVjdD48Y2lyY2xlIGN4PSI4LjUiIGN5PSI4LjUiIHI9IjEuNSI+PC9jaXJjbGU+PHBvbHlsaW5lIHBvaW50cz0iMjEgMTUgMTYgMTAgNSAyMSI+PC9wb2x5bGluZT48bGluZSB4MT0iNCIgeTE9IjQiIHgyPSIyMCIgeTI9IjIwIiBzdHJva2U9IiNmNDQzMzYiIG9wYWNpdHk9IjAuOCI+PC9saW5lPjwvc3ZnPg==";
                        img.style.objectFit = "none";
                        img.style.backgroundColor = "rgba(128,128,128,0.05)";
                        img.style.border = "1px dashed var(--border-color, rgba(128,128,128,0.3))";
                        img.style.minWidth = "120px";
                        img.style.minHeight = "120px";
                        img.style.cursor = "default";
                        
                        var errSpan = document.createElement('div');
                        errSpan.textContent = '[' + (window.t ? window.t('proactive.meme.loadError') : '表情包加载失败') + ']';
                        errSpan.style.cssText = 'color: var(--text-secondary, rgba(200,200,200,0.6)); font-size: 12px; margin-top: 4px;';
                        imgOuter.appendChild(errSpan);
                    }
                });

                // 【修复】拦截图片失效后的点击事件
                img.addEventListener('click', function (e) {
                    if (img.dataset.failed === "true") {
                        e.preventDefault();
                        e.stopPropagation();
                        return false;
                    }
                });

                imgOuter.appendChild(img);
                imgBubble.appendChild(imgOuter);
                chatContainer.appendChild(imgBubble);

                if (window.currentTurnGeminiAttachments) {
                    window.currentTurnGeminiAttachments.push(imgBubble);
                }

                chatContainer.scrollTop = chatContainer.scrollHeight;
                console.log('[Meme] 已展示图片气泡:', meme.title);
            })(memeLinks[i]);
        }
    }
    mod._showMemeBubbles = _showMemeBubbles;

    // ======================== backoff reset ========================

    /**
     * 重置主动搭话退避级别 + 语音无回复计数，并 reschedule timer；
     * 同时通过 BroadcastChannel 广播，让所有窗口（包括 leader）同步 reset。
     * @param {Object} [opts]
     * @param {boolean} [opts._fromIpc] 标记本次调用源自 IPC 消息，避免回环广播。
     */
    function resetProactiveChatBackoff(opts) {
        // 重置退避级别
        S.proactiveChatBackoffLevel = 0;
        // 语音模式：用户说话了，重置无回复计数
        S._voiceProactiveNoResponseCount = 0;
        // 重新安排定时器
        scheduleProactiveChat();
        // 跨窗口同步：分发环境下 chat.html 输入只会 reset 它自己这份无用的 state，
        // proactive 真正的计时器在 index.html (leader)。广播 user_input_reset，
        // 让所有窗口（包括 leader）本地再跑一次 reset。_fromIpc 表示本次调用源自
        // IPC 消息，不再回广播，避免回环。
        if (!opts || !opts._fromIpc) {
            _proactiveBroadcast('user_input_reset');
        }
    }
    mod.resetProactiveChatBackoff = resetProactiveChatBackoff;

    // ======================== proactive vision during speech ========================

    /**
     * 发送单帧屏幕数据（统一使用 acquireOrReuseCachedStream → captureFrameFromStream → 后端兜底）
     */
    async function sendOneProactiveVisionFrame() {
        try {
            if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return;

            var dataUrl = null;

            // 优先前端流（缓存流 → Electron源 → 不弹窗）
            var stream = await acquireOrReuseCachedStream({ allowPrompt: false });
            if (stream) {
                var frame = await captureFrameFromStream(stream, 0.8);
                if (frame && frame.dataUrl) {
                    dataUrl = frame.dataUrl;
                } else if (S.screenCaptureStream === stream) {
                    // 空帧（黑帧或空壳流），废弃缓存流
                    console.warn('[ProactiveVision] 缓存流提取帧失败，废弃该流');
                    try { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { } }); } catch (e) { }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;
                }
            }

            // 后端 pyautogui 兜底
            if (!dataUrl) {
                var backendResult = await fetchBackendScreenshot();
                dataUrl = backendResult.dataUrl;
                // macOS 403 权限提示
                if (backendResult.status === 403 && !S.screenRecordingPermissionHintShown) {
                    S.screenRecordingPermissionHintShown = true;
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(window.t ? window.t('app.screenRecordingPermissionDenied') : '\u26A0\uFE0F 屏幕录制权限未授权，请在系统设置中允许屏幕录制', 6000);
                    }
                    console.warn('[ProactiveVision] 后端截图返回 403，请在"系统设置 → 隐私与安全性 → 屏幕录制"中授权 N.E.K.O');
                }
            }

            if (dataUrl && S.socket && S.socket.readyState === WebSocket.OPEN) {
                S.socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: dataUrl,
                    input_type: (window.appUtils && window.appUtils.isMobile) ? (window.appUtils.isMobile() ? 'camera' : 'screen') : 'screen'
                }));
                console.log('[ProactiveVision] 发送单帧屏幕数据');
            }
        } catch (e) {
            console.error('sendOneProactiveVisionFrame 失败:', e);
        }
    }
    mod.sendOneProactiveVisionFrame = sendOneProactiveVisionFrame;

    function startProactiveVisionDuringSpeech() {
        // 如果已有定时器先清理
        if (S.proactiveVisionFrameTimer) {
            clearInterval(S.proactiveVisionFrameTimer);
            S.proactiveVisionFrameTimer = null;
        }

        // 主备协调：proactive vision 也由 Pet 主窗口负责，chat.html 不参与。
        // 否则两个窗口都会向后端推屏幕帧，带宽和 LLM 调用翻倍。
        if (!isProactiveLeader()) {
            console.log('[ProactiveVision] 当前不是 leader，跳过启动');
            return;
        }

        // 「请她离开」状态下禁止启动
        if (isGoodbyeActive()) {
            return;
        }

        // 仅在条件满足时启动：已开启主动视觉 && 正在录音 && 未手动屏幕共享
        if (!S.proactiveVisionEnabled || !S.isRecording) return;
        var screenButton = document.getElementById('screenButton');
        if (screenButton && screenButton.classList.contains('active')) return; // 手动共享时不启动

        S.proactiveVisionFrameTimer = setInterval(async function () {
            // 在每次执行前再做一次检查，避免竞态
            if (!S.proactiveVisionEnabled || !S.isRecording || isGoodbyeActive()) {
                stopProactiveVisionDuringSpeech();
                return;
            }
            // leader 切换的兜底：发帧前再核对一次
            if (!isProactiveLeader()) {
                stopProactiveVisionDuringSpeech();
                return;
            }

            // 如果手动开启了屏幕共享，重置计数器（即跳过发送）
            var sb = document.getElementById('screenButton');
            if (sb && sb.classList.contains('active')) {
                // do nothing this tick, just wait for next interval
                return;
            }

            await sendOneProactiveVisionFrame();
        }, S.proactiveVisionInterval * 1000);
    }
    mod.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;

    function stopProactiveVisionDuringSpeech() {
        if (S.proactiveVisionFrameTimer) {
            clearInterval(S.proactiveVisionFrameTimer);
            S.proactiveVisionFrameTimer = null;
        }
    }
    mod.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;

    function stopProactiveChatSchedule() {
        if (S.proactiveChatTimer) {
            clearTimeout(S.proactiveChatTimer);
            S.proactiveChatTimer = null;
        }
        if (S._voiceSessionInitialTimer) {
            clearTimeout(S._voiceSessionInitialTimer);
            S._voiceSessionInitialTimer = null;
        }
    }
    mod.stopProactiveChatSchedule = stopProactiveChatSchedule;

    // ======================== isWindowsOS ========================

    /**
     * 安全的Windows系统检测函数
     * 优先使用 navigator.userAgentData，然后 fallback 到 navigator.userAgent，最后才用已弃用的 navigator.platform
     * @returns {boolean} 是否为Windows系统
     */
    function isWindowsOS() {
        try {
            // 优先使用现代 API（如果支持）
            if (navigator.userAgentData && navigator.userAgentData.platform) {
                var platform = navigator.userAgentData.platform.toLowerCase();
                return platform.includes('win');
            }

            // Fallback 到 userAgent 字符串检测
            if (navigator.userAgent) {
                var ua = navigator.userAgent.toLowerCase();
                return ua.includes('win');
            }

            // 最后的兼容方案：使用已弃用的 platform API
            if (navigator.platform) {
                var plat = navigator.platform.toLowerCase();
                return plat.includes('win');
            }

            // 如果所有方法都不可用，默认返回false
            return false;
        } catch (error) {
            console.error('Windows检测失败:', error);
            return false;
        }
    }
    mod.isWindowsOS = isWindowsOS;

    // ======================== captureProactiveChatScreenshot ========================

    /**
     * 主动搭话截图函数
     * 优先级：
     *   0a. 复用有效缓存流（屏幕共享活跃时零成本）
     *   0b. 主进程 desktopCapturer 直接对选中源做快照（Electron 桌面 + 用户已选源；最可靠）
     *   1.  acquireOrReuseCachedStream（创建新流：Electron chromeMediaSourceId / getDisplayMedia）
     *   2.  后端 pyautogui 兜底
     *
     * 0b 解决聊天框截图按钮在 Electron 41/Win11 + useSystemPicker 下对窗口源总是
     * 返回整屏的问题；同时也改善此函数走 WS_HOOK / CHAT_CHANNELS.REQUEST_SCREENSHOT
     * 路径时的准确性。
     */
    async function captureProactiveChatScreenshot() {
        // 策略 0a: 复用有效缓存流（避免打扰正在进行的屏幕共享）
        if (S.screenCaptureStream && S.screenCaptureStream.active) {
            try {
                var tracks = S.screenCaptureStream.getVideoTracks();
                if (tracks.length > 0 && tracks.some(function (t) { return t.readyState === 'live'; })) {
                    var cachedFrame = await captureFrameFromStream(S.screenCaptureStream, 0.85);
                    if (cachedFrame && cachedFrame.dataUrl) {
                        S.screenCaptureStreamLastUsed = Date.now();
                        if (window.scheduleScreenCaptureIdleCheck) window.scheduleScreenCaptureIdleCheck();
                        console.log('[主动搭话截图] 缓存流截图成功');
                        return cachedFrame.dataUrl;
                    }
                }
            } catch (e) { console.warn('[主动搭话截图] 缓存流截图失败，继续:', e); }
        }

        // 策略 0b: 主进程直接捕获选中源（Electron 桌面环境）
        if (S.selectedScreenSourceId && window.electronDesktopCapturer
            && typeof window.electronDesktopCapturer.captureSourceAsDataUrl === 'function') {
            try {
                var direct = await window.electronDesktopCapturer.captureSourceAsDataUrl(S.selectedScreenSourceId);
                if (direct && direct.success && direct.dataUrl) {
                    console.log('[主动搭话截图] 主进程直接捕获成功:', S.selectedScreenSourceId);
                    return direct.dataUrl;
                } else if (direct && direct.error) {
                    console.warn('[主动搭话截图] 主进程直接捕获失败，将回退到流路径:', direct.error);
                }
            } catch (e) { console.warn('[主动搭话截图] 主进程直接捕获抛错，将回退到流路径:', e); }
        }

        // 策略1: 缓存流 / Electron窗口ID / getDisplayMedia（非user gesture不弹窗）
        var stream = await acquireOrReuseCachedStream({ allowPrompt: false });
        if (stream) {
            var frame = await captureFrameFromStream(stream, 0.85);
            if (frame && frame.dataUrl) {
                console.log('[主动搭话截图] 前端截图成功');
                return frame.dataUrl;
            }
            // 黑帧或抓帧失败 → 废弃流，重试一次
            console.warn('[主动搭话截图] 帧提取失败或纯黑帧，废弃缓存流并重试');
            if (S.screenCaptureStream === stream) {
                try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
                S.screenCaptureStream = null;
                S.screenCaptureStreamLastUsed = null;
            }
            // 重试：会走 Electron sourceId 路径
            stream = await acquireOrReuseCachedStream({ allowPrompt: false });
            if (stream) {
                frame = await captureFrameFromStream(stream, 0.85);
                if (frame && frame.dataUrl) return frame.dataUrl;
                // 二次重试仍然失败，废弃这个流
                console.warn('[主动搭话截图] 二次重试仍失败，废弃流');
                if (S.screenCaptureStream === stream) {
                    try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;
                }
            }
        }

        // 策略2: 后端 pyautogui 兜底
        var backendResult = await fetchBackendScreenshot();
        if (backendResult.dataUrl) {
            console.log('[主动搭话截图] 后端截图成功');
            return backendResult.dataUrl;
        }

        console.warn('[主动搭话截图] 所有截图方式均失败');
        return null;
    }
    mod.captureProactiveChatScreenshot = captureProactiveChatScreenshot;

    // ======================== acquireProactiveVisionStream ========================

    /**
     * 主动视觉开关切换时的流生命周期管理
     * 开启时：优先测试后端 pyautogui（静默无弹窗），不可用则通过前端流获取（用户手势上下文可弹 getDisplayMedia）
     */
    async function acquireProactiveVisionStream() {
        // 策略1: 测试后端 pyautogui 是否可用（静默，无弹窗）
        var backendResult = await fetchBackendScreenshot();
        if (backendResult.dataUrl) {
            console.log('[主动视觉] 后端 pyautogui 可用，无需前端流');
            return true;
        }

        // 策略2: 后端不可用，尝试前端流（用户手势上下文，可弹 getDisplayMedia）
        var stream = await acquireOrReuseCachedStream({ allowPrompt: true });
        if (stream) {
            console.log('[主动视觉] 前端流获取/复用成功');
            return true;
        }

        console.warn('[主动视觉] 无可用的截图方式');
        return false;
    }
    mod.acquireProactiveVisionStream = acquireProactiveVisionStream;

    // ======================== releaseProactiveVisionStream ========================

    function releaseProactiveVisionStream() {
        // 如果用户手动开启了屏幕共享，不要释放流
        var screenButton = document.getElementById('screenButton');
        if (screenButton && screenButton.classList.contains('active')) {
            console.log('[主动视觉] 手动屏幕共享活跃中，不释放流');
            return;
        }

        // 如果正在录音（语音模式），流可能正在被使用，不释放
        if (S.isRecording) {
            console.log('[主动视觉] 语音模式活跃中，不释放流');
            return;
        }

        // 如果主动搭话+主动视觉Chat仍活跃，保留流
        if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled) {
            console.log('[主动视觉] 主动搭话视觉仍活跃，不释放流');
            return;
        }

        if (S.screenCaptureStream) {
            try {
                if (typeof S.screenCaptureStream.getTracks === 'function') {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn('[主动视觉] 停止 tracks 失败:', e);
            }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
            console.log('[主动视觉] 屏幕流已释放');
        }
    }
    mod.releaseProactiveVisionStream = releaseProactiveVisionStream;

    // ======================== backward-compat window exports ========================

    window.hasAnyChatModeEnabled = hasAnyChatModeEnabled;
    window.resetProactiveChatBackoff = resetProactiveChatBackoff;
    window.stopProactiveChatSchedule = stopProactiveChatSchedule;
    window.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;
    window.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;
    window.acquireProactiveVisionStream = acquireProactiveVisionStream;
    window.releaseProactiveVisionStream = releaseProactiveVisionStream;
    window.scheduleProactiveChat = scheduleProactiveChat;
    window.isProactiveLeader = isProactiveLeader;
    window.captureCanvasFrame = captureCanvasFrame;
    window.fetchBackendScreenshot = fetchBackendScreenshot;
    window.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;
    window.captureProactiveChatScreenshot = captureProactiveChatScreenshot;
    window.isWindowsOS = isWindowsOS;
    window.getAvailablePersonalPlatforms = getAvailablePersonalPlatforms;

    // ======================== module export ========================

    window.appProactive = mod;
})();
