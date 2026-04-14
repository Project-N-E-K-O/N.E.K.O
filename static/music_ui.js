/**
 * Music UI Module
 * 职责：从 common-ui 分离出的所有音乐相关代码
 */
(function () {
    'use strict';

    // --- 集中配置中心 ---
    const MUSIC_CONFIG = {
        dom: {
            containerId: 'chat-container',
            insertBeforeId: 'text-input-area',
            barId: 'music-player-bar'
        },
        assets: {
            cssPath: '/static/libs/APlayer.min.css',
            jsPath: '/static/libs/APlayer.min.js',
            uiCssPath: '/static/css/music_ui.css'
        },
        themeColors: ['#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe', '#00f2fe', '#a8edea', '#fed6e3'],
        primaryColor: '#667eea',
        secondaryColor: '#764ba2',
        defaultVolume: 0.5,
        volumeStep: 0.05,
        // 自动销毁时长配置 (ms)
        timeouts: {
            ended: 21000,  // 自然播放结束
            idle: 24000,   // AI推荐未播放 (或被拦截)
            paused: 71000  // 用户点击暂停
        },
        // 标题超过「容器宽 × 该比例」时启用横向滚动（0.9~1 可调，便于微调观感）
        titleOverflowRatio: 1,
        // 域名白名单
        allowlist: [
            'i.scdn.co', 'p.scdn.co', 'a.scdn.co', 'i.imgur.com', 'y.qq.com',
            'music.126.net', 'p1.music.126.net', 'p2.music.126.net', 'p3.music.126.net',
            'm7.music.126.net', 'm8.music.126.net', 'm9.music.126.net',
            'mmusic.spriteapp.cn', 'gg.spriteapp.cn',
            'freemusicarchive.org', 'musopen.org', 'bandcamp.com',
            'bcbits.com', 'soundcloud.com', 'sndcdn.com',
            'playback.media-streaming.soundcloud.cloud', 'api.soundcloud.com',
            'itunes.apple.com', 'audio-ssl.itunes.apple.com',
            'dummyimage.com', 'music.163.com',
            'hdslb.com', 'bilivideo.com'
        ]
    };

    let currentPlayingTrack = null;
    let localPlayer = null;
    let musicCardMessageId = null;
    let aplayerLoadPromise = null;
    let latestMusicRequestToken = 0;

    // --- 竞态保护：dispatch 入口的"加载中"标记 ---
    // sendMusicMessage 的 URL 校验/库加载阶段对外暴露，避免并发 dispatch 在
    // 真正的 audio 还未启动时绕过 isMusicPlaying() 拦截。
    //
    // 用计数器而非 boolean：并发的 sendMusicMessage 调用各自 +1 / -1，
    // 谁先走早退分支都不会把尚在库加载中的兄弟调用误清为 idle。
    let musicDispatchPendingCount = 0;

    // --- 竞态保护：executePlay 串行化 ---
    // 两个并发 executePlay 在 await initializeAPlayer 期间会同时把
    // currentPlayingTrack / musicCardMessageId 覆盖一次，第一个实例还会
    // 残留一个未受控的 <audio>。用 Promise 链把它们排成单线。
    let executePlayChain = Promise.resolve();

    // --- 跨窗口协调：当多个窗口（index.html + chat.html）同时开了主动搭话时，
    // 它们各自的播放器都会响应自己的 proactive_chat 响应。即使本地不在播，
    // 远程窗口可能正在播；用一个独立 BroadcastChannel 互相通报，
    // dispatchMusicPlay 在 source==='proactive' 时把远程视作"已占用"。 ---
    const MUSIC_COORD_SENDER_ID = (Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
    const REMOTE_MUSIC_TTL_MS = 30 * 1000; // 心跳超时
    // sender_id -> expireAt
    const remoteMusicSenders = new Map();
    let musicCoordChannel = null;
    try {
        if (typeof BroadcastChannel !== 'undefined') {
            musicCoordChannel = new BroadcastChannel('neko_music_coord');
            musicCoordChannel.onmessage = (event) => {
                const data = event && event.data;
                if (!data || typeof data !== 'object') return;
                const sid = data.sender;
                if (!sid || sid === MUSIC_COORD_SENDER_ID) return;
                if (data.type === 'music_started' || data.type === 'music_heartbeat') {
                    remoteMusicSenders.set(sid, Date.now() + REMOTE_MUSIC_TTL_MS);
                } else if (data.type === 'music_ended') {
                    remoteMusicSenders.delete(sid);
                }
            };
            // 窗口关闭时通告一声 music_ended 并关闭 channel，避免对端等 30s TTL 才意识到我退出
            window.addEventListener('beforeunload', () => {
                try {
                    if (musicCoordChannel) {
                        musicCoordChannel.postMessage({
                            type: 'music_ended',
                            sender: MUSIC_COORD_SENDER_ID,
                            ts: Date.now()
                        });
                        musicCoordChannel.close();
                        musicCoordChannel = null;
                    }
                } catch (_) { /* ignore */ }
            });
        }
    } catch (e) {
        console.log('[Music UI] BroadcastChannel 不可用，跨窗口协调失效:', e);
    }

    const broadcastMusicCoord = (type) => {
        if (!musicCoordChannel) return;
        try {
            musicCoordChannel.postMessage({ type, sender: MUSIC_COORD_SENDER_ID, ts: Date.now() });
        } catch (_) { /* ignore */ }
    };

    // 心跳：当本地正在播时定期广播，防止其他窗口误以为对方已退出
    let musicHeartbeatTimer = null;
    const startMusicHeartbeat = () => {
        if (musicHeartbeatTimer) return;
        musicHeartbeatTimer = setInterval(() => {
            try {
                if (localPlayer && localPlayer.audio && !localPlayer.audio.paused) {
                    broadcastMusicCoord('music_heartbeat');
                } else {
                    stopMusicHeartbeat();
                }
            } catch (_) {
                stopMusicHeartbeat();
            }
        }, 10 * 1000);
    };
    const stopMusicHeartbeat = () => {
        if (musicHeartbeatTimer) {
            clearInterval(musicHeartbeatTimer);
            musicHeartbeatTimer = null;
        }
    };

    const isRemoteMusicActive = () => {
        if (remoteMusicSenders.size === 0) return false;
        const now = Date.now();
        // 顺手清理已过期的 sender
        for (const [sid, exp] of remoteMusicSenders) {
            if (now > exp) remoteMusicSenders.delete(sid);
        }
        return remoteMusicSenders.size > 0;
    };

    // --- 更新 React 聊天窗口音乐卡片 ---
    const updateMusicCard = (state, track) => {
        const host = window.reactChatWindowHost;
        if (!host || typeof host.updateMessage !== 'function' || !musicCardMessageId) return;

        let prefix = '❓';
        let text = (window.t && window.t('music.unknownState')) || '未知状态';
        if (state === 'playing') { prefix = '🎵'; text = (window.t && window.t('music.playing')) || '播放中'; }
        else if (state === 'paused') { prefix = '⏸'; text = (window.t && window.t('music.paused')) || '已暂停'; }
        else if (state === 'ended') { prefix = '✅'; text = (window.t && window.t('music.ended')) || '已播完'; }
        else if (state === 'error') { prefix = '❌'; text = (window.t && window.t('music.playError')) || '播放失败'; }
        else { prefix = '❓'; text = (window.t && window.t('music.unknownState')) || '未知状态'; }

        host.updateMessage(musicCardMessageId, {
            blocks: [{
                type: 'link',
                url: track?.url || '#',
                title: track?.name || '未知曲目',
                description: track?.artist || '未知艺术家',
                siteName: prefix + ' ' + text,
                thumbnailUrl: track?.cover || undefined
            }]
        });

        if (state === 'error') {
            musicCardMessageId = null;
        }
    };

    // --- 状态追踪：用于 5 秒去重 与 进度条清理 ---
    let lastPlayedMusicUrl = null;
    let lastMusicPlayTime = 0;

    // --- 音乐秒关检测 & 自动冷却 ---
    const SKIP_CONFIG = {
        skipThresholdMs: 10000,              // < 10 秒关闭 = 视为"秒关"
        consecutiveSkipsToTrigger: 2,        // 连续秒关 2 次触发冷却
        cooldownDurationMs: 20 * 60 * 1000   // 冷却 20 分钟
    };

    // --- 主动推荐频率限流 ---
    // 用户反馈"推荐太频繁"，加一层硬性最小间隔：任意一次 proactive 推荐
    // 成功派发后，接下来 RECOMMEND_COOLDOWN_MS 内不再放行新的 proactive 推荐。
    // 非 proactive 来源（用户主动点播、插件直推、[play_music:] 指令）不受影响。
    const RECOMMEND_COOLDOWN_MS = 18000;
    let lastProactiveRecommendAt = 0;

    const isMusicRecommendRateLimited = () => {
        if (lastProactiveRecommendAt <= 0) return false;
        return (Date.now() - lastProactiveRecommendAt) < RECOMMEND_COOLDOWN_MS;
    };
    const markProactiveMusicRecommended = () => {
        lastProactiveRecommendAt = Date.now();
    };
    let accumulatedPlaySeconds = 0;   // actual playback seconds (from player.currentTime)
    let lastPlayPosition = 0;         // player.currentTime snapshot at last play/resume
    let consecutiveSkipCount = 0;
    let musicCooldownUntil = 0;

    // 从 localStorage 恢复冷却状态
    try {
        const stored = localStorage.getItem('music_cooldown_until');
        if (stored) {
            const val = parseInt(stored, 10);
            if (val > Date.now()) {
                musicCooldownUntil = val;
                console.log('[Music UI] 恢复冷却状态，截止', new Date(val).toLocaleTimeString());
            } else {
                localStorage.removeItem('music_cooldown_until');
            }
        }
    } catch (e) { /* localStorage 不可用 */ }

    function enterMusicCooldown() {
        musicCooldownUntil = Date.now() + SKIP_CONFIG.cooldownDurationMs;
        consecutiveSkipCount = 0;
        try { localStorage.setItem('music_cooldown_until', String(musicCooldownUntil)); } catch (e) {}
        console.log('[Music UI] 连续秒关触发冷却，音乐推荐暂停至', new Date(musicCooldownUntil).toLocaleTimeString());
    }

    function isInMusicCooldown() {
        if (musicCooldownUntil <= 0) return false;
        if (Date.now() >= musicCooldownUntil) {
            musicCooldownUntil = 0;
            try { localStorage.removeItem('music_cooldown_until'); } catch (e) {}
            return false;
        }
        return true;
    }

    function recordMusicSkip() {
        consecutiveSkipCount++;
        console.log('[Music UI] 秒关 #' + consecutiveSkipCount);
        if (consecutiveSkipCount >= SKIP_CONFIG.consecutiveSkipsToTrigger) {
            enterMusicCooldown();
        }
    }

    function resetSkipCounter() {
        if (consecutiveSkipCount > 0) {
            console.log('[Music UI] 用户正常收听，重置秒关计数');
        }
        consecutiveSkipCount = 0;
    }

    // 全局监听管理
    let managedWindowListeners = [];
    const addManagedListener = (type, listener, options) => {
        window.addEventListener(type, listener, options);
        managedWindowListeners.push({ type, listener, options });
    };
    const clearManagedListeners = () => {
        managedWindowListeners.forEach(({ type, listener, options }) => {
            window.removeEventListener(type, listener, options);
        });
        managedWindowListeners = [];
    };

    // 全局拖拽清理引用
    let currentDragHandlers = null;
    let currentVolumeDragHandlers = null;

    // --- 2. 原始工具函数 ---
    /**
     * 安全提取域名/IP
     */
    const extractHostname = (input) => {
        if (!input || typeof input !== 'string') return null;
        let target = input.trim();
        if (!target.startsWith('http://') && !target.startsWith('https://')) {
            target = 'https://' + target;
        }
        try {
            const url = new URL(target);
            return url.hostname;
        } catch (e) {
            return null;
        }
    };

    const isSafeUrl = (url) => {
        if (!url) return false;
        try {
            // 对内部代理路径直接放行（后端已做安全检查）
            if (url.startsWith('/api/')) return true;
            const parsed = new URL(url);
            if (!['http:', 'https:'].includes(parsed.protocol)) return false;
            const hostname = parsed.hostname;
            return MUSIC_CONFIG.allowlist.some(d => hostname === d || hostname.endsWith('.' + d));
        } catch { return false; }
    };

    const getMusicPlayerInstance = () => localPlayer;

    const isPlayerInDOM = () => {
        const bar = document.getElementById(MUSIC_CONFIG.dom.barId);
        // 如果正在淡出，视为已经不在 DOM 中，允许后续逻辑重用/创建新条
        return !!(bar && !bar.classList.contains('fading-out'));
    };

    const isSameTrack = (info) => {
        return currentPlayingTrack &&
            currentPlayingTrack.name === info.name &&
            currentPlayingTrack.artist === info.artist &&
            currentPlayingTrack.url === info.url;
    };

    const showErrorToast = (msgKey, defaultMsg) => {
        if (typeof window.showStatusToast === 'function') {
            const errMsg = window.t ? window.t(msgKey, defaultMsg) : defaultMsg;
            window.showStatusToast(errMsg, 3000);
        }
    };

    const showNowPlayingToast = (name) => {
        if (typeof window.showStatusToast === 'function') {
            const unknownTrack = window.t ? window.t('music.unknownTrack', '未知曲目') : '未知曲目';
            const displayName = name || unknownTrack;
            const defaultText = '为您播放: ' + displayName;
            let playMsg = window.t ? window.t('music.nowPlaying', {
                name: displayName,
                defaultValue: defaultText
            }) : defaultText;

            // 鲁棒性检查：如果 i18n 返回了非字符串，回退到默认文案
            if (typeof playMsg !== 'string') playMsg = defaultText;

            window.showStatusToast(playMsg, 3000);
        }
    };

    let autoDestroyTimer = null;
    let domRemovalTimer = null;
    let titleMarqueeObserver = null;

    const disconnectTitleMarqueeObserver = () => {
        if (titleMarqueeObserver) {
            titleMarqueeObserver.disconnect();
            titleMarqueeObserver = null;
        }
    };

    const syncMusicBarTitleLayout = (musicBar) => {
        const wrap = musicBar && musicBar.querySelector('.music-bar-title-wrap');
        const track = wrap && wrap.querySelector('.music-bar-title-track');
        const segPrimary = wrap && wrap.querySelector('.music-bar-title-seg-primary');
        if (!wrap || !track || !segPrimary) return;

        wrap.classList.remove('is-marquee');
        track.style.removeProperty('--marquee-duration');

        const ratio = typeof MUSIC_CONFIG.titleOverflowRatio === 'number' ? MUSIC_CONFIG.titleOverflowRatio : 1;
        const maxW = Math.max(0, wrap.clientWidth * ratio);
        const textW = segPrimary.offsetWidth;

        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            return;
        }

        if (textW > maxW) {
            wrap.classList.add('is-marquee');
            requestAnimationFrame(() => {
                const bar = document.getElementById(MUSIC_CONFIG.dom.barId);
                if (!bar) return;
                const w = bar.querySelector('.music-bar-title-wrap');
                const t = w && w.querySelector('.music-bar-title-track');
                if (!w || !t || !w.classList.contains('is-marquee')) return;
                const loopPx = t.scrollWidth / 2;
                const duration = Math.min(50, Math.max(6, loopPx / 45));
                t.style.setProperty('--marquee-duration', duration + 's');
            });
        }
    };

    const setMusicBarTitle = (musicBar, text) => {
        const wrap = musicBar.querySelector('.music-bar-title-wrap');
        const segPrimary = musicBar.querySelector('.music-bar-title-seg-primary');
        const segDup = musicBar.querySelector('.music-bar-title-seg-dup');
        const display = text || (window.t ? window.t('music.unknownTrack', '未知曲目') : '未知曲目');
        if (segPrimary) segPrimary.textContent = display;
        if (segDup) segDup.textContent = display;
        if (wrap) {
            wrap.setAttribute('title', display);
            wrap.setAttribute('aria-label', display);
        }
        requestAnimationFrame(() => {
            requestAnimationFrame(() => syncMusicBarTitleLayout(musicBar));
        });
    };

    const ensureTitleMarqueeObserver = (musicBar) => {
        const wrap = musicBar.querySelector('.music-bar-title-wrap');
        if (!wrap || typeof ResizeObserver === 'undefined') return;
        disconnectTitleMarqueeObserver();
        titleMarqueeObserver = new ResizeObserver(() => {
            const bar = document.getElementById(MUSIC_CONFIG.dom.barId);
            if (bar) syncMusicBarTitleLayout(bar);
        });
        titleMarqueeObserver.observe(wrap);
    };

    const formatTime = (seconds) => {
        if (isNaN(seconds) || !isFinite(seconds)) return '00:00';
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    };


    const destroyMusicPlayer = (removeDOM = true, fullTeardown = false, updateToken = false) => {
        // 重要：销毁播放器意味着取消所有正在进行的异步加载令牌
        // 只有在 fullTeardown (手动关闭) 或明确要求时才更新 token
        if (updateToken || fullTeardown) {
            latestMusicRequestToken++;
        }

        // 清除可能的自动销毁定时器
        if (autoDestroyTimer) {
            clearTimeout(autoDestroyTimer);
            autoDestroyTimer = null;
        }

        // 重要：清除正在进行的 DOM 移除定时器，防止在切换歌曲时播放条被意外删除
        if (domRemovalTimer) {
            clearTimeout(domRemovalTimer);
            domRemovalTimer = null;
        }

        // 核心：优先执行本地暂停，避免声音残留
        if (localPlayer && typeof localPlayer.pause === 'function') {
            localPlayer.pause();
        }
        if (currentDragHandlers && typeof currentDragHandlers.cleanup === 'function') {
            currentDragHandlers.cleanup();
            currentDragHandlers = null;
        }
        if (currentVolumeDragHandlers && typeof currentVolumeDragHandlers.cleanup === 'function') {
            currentVolumeDragHandlers.cleanup();
            currentVolumeDragHandlers = null;
        }
        if (fullTeardown) {
            // 【核心修复】调整顺序：先调用外部销毁逻辑，再清理本地引用
            // 理由：APlayer/main.js 的 destroyAPlayer 依赖 window.aplayer 进行清理
            // 且此处理由 window.destroyAPlayer 统一完成实例销毁，不再本地重复销毁
            if (typeof window.destroyAPlayer === 'function') {
                window.destroyAPlayer();
            } else if (localPlayer && typeof localPlayer.destroy === 'function') {
                localPlayer.destroy();
            }
            localPlayer = null;
            window.aplayer = null;
            if (window.aplayerInjected) window.aplayerInjected.aplayer = null;
        } else {
            // 切歌模式下，手动销毁旧实例以防泄露
            if (localPlayer && typeof localPlayer.destroy === 'function') {
                try {
                    localPlayer._destroying = true;
                    clearManagedListeners();
                    localPlayer.destroy();
                } catch (e) {
                    console.warn('[Music UI] Error during local player destroy:', e);
                }
            }
            localPlayer = null;
            window.aplayer = null;
            if (window.aplayerInjected) window.aplayerInjected.aplayer = null;
        }

        if (removeDOM) {
            disconnectTitleMarqueeObserver();
            const bar = document.getElementById(MUSIC_CONFIG.dom.barId);
            if (bar) {
                // 如果是手动关闭，执行动画
                if (fullTeardown) {
                    bar.classList.add('fading-out');
                    domRemovalTimer = setTimeout(() => {
                        bar.remove();
                        domRemovalTimer = null;
                    }, 300);
                } else {
                    bar.remove();
                }
            }
            clearManagedListeners();
        }
        // 手动关闭时更新卡片状态为"已结束"，必须在清空 musicCardMessageId 之前
        if (fullTeardown && musicCardMessageId) {
            updateMusicCard('ended', currentPlayingTrack);
        }
        currentPlayingTrack = null;
        musicCardMessageId = null;

        // 跨窗口协调：通知其他窗口本地音乐已停
        stopMusicHeartbeat();
        broadcastMusicCoord('music_ended');
    };

    // --- 查找并替换整个 loadAPlayerLibrary 函数 ---
    const loadAPlayerLibrary = () => {
        if (aplayerLoadPromise) return aplayerLoadPromise;

        aplayerLoadPromise = new Promise((resolve, reject) => {
            // 核心修复：定义一个真正的函数来加载 CSS
            const injectCSS = (path) => new Promise((res) => {
                if (!path) return res();
                if (document.querySelector(`link[href*="${path}"]`)) return res();

                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = path;
                link.onload = () => {
                    console.log('[Music UI] 样式加载成功:', path);
                    res();
                };
                link.onerror = () => {
                    console.error('[Music UI] 样式加载失败，请检查路径:', path);
                    res(); // 失败也要继续，不能卡死
                };
                document.head.appendChild(link);
            });

            const cssPromises = [
                injectCSS(MUSIC_CONFIG.assets.cssPath),
                injectCSS(MUSIC_CONFIG.assets.uiCssPath)
            ];

            if (typeof window.APlayer !== 'undefined') {
                Promise.all(cssPromises).then(() => resolve());
                return;
            }

            // 同时并行加载：官方CSS、自定义CSS、APlayer脚本
            Promise.all([
                ...cssPromises,
                new Promise((resJS, rejJS) => {
                    const script = document.createElement('script');
                    script.src = MUSIC_CONFIG.assets.jsPath;
                    script.onload = () => (typeof window.APlayer !== 'undefined' ? resJS() : rejJS());
                    script.onerror = rejJS;
                    document.head.appendChild(script);
                })
            ]).then(() => {
                console.log('[Music UI] 所有资源（包括自定义CSS）已就绪');
                resolve();
            }).catch((err) => {
                aplayerLoadPromise = null;
                reject(err);
            });
        });
        return aplayerLoadPromise;
    };

    // --- 5. 播放器挂载逻辑 (支持原地更新与实例复用) ---
    // 核心逻辑：复用 APlayer 实例可以保留浏览器的“音频解锁”状态，极大提高自动播放成功率
    //
    // 【串行化】两次并发调用如果同时进入 needsInit 分支，会同时 await initializeAPlayer，
    // 都拿到自己的实例后写 currentPlayingTrack/musicCardMessageId，第一份卡片
    // 会被第二份盖掉，被覆盖的实例如果 destroy 不及时还会残留 <audio>。
    // 用 executePlayChain 把所有 executePlay 排成单线，保证内部 await 不会被抢跑。
    const executePlay = (trackInfo, currentToken, shouldAutoPlay = true) => {
        const run = () => executePlayCore(trackInfo, currentToken, shouldAutoPlay);
        const next = executePlayChain.then(run, run); // 即使前一次 reject 也继续
        executePlayChain = next.catch(() => { /* 链路自愈，避免 rejection 阻断后续 */ });
        return next;
    };

    const executePlayCore = async (trackInfo, currentToken, shouldAutoPlay = true) => {
        if (currentToken !== latestMusicRequestToken) return;

        // 清除可能的自动销毁与 DOM 移除定时器
        if (autoDestroyTimer) {
            clearTimeout(autoDestroyTimer);
            autoDestroyTimer = null;
        }
        if (domRemovalTimer) {
            clearTimeout(domRemovalTimer);
            domRemovalTimer = null;
        }

        const hasCover = trackInfo.cover && trackInfo.cover.length > 0 && isSafeUrl(trackInfo.cover);
        let musicBar = document.getElementById(MUSIC_CONFIG.dom.barId);
        let isFirstRender = !musicBar;

        // --- 1. DOM 基础架构 ---
        if (isFirstRender) {
            // 优先挂载到 React 聊天窗口 composer-panel 内的专用挂载点，回退到旧 chat-container
            let mountTarget = document.getElementById('music-player-mount');
            let insertBeforeEl = null;
            if (!mountTarget) {
                mountTarget = document.getElementById(MUSIC_CONFIG.dom.containerId);
                insertBeforeEl = document.getElementById(MUSIC_CONFIG.dom.insertBeforeId);
            }
            if (!mountTarget) return;

            musicBar = document.createElement('div');
            musicBar.id = MUSIC_CONFIG.dom.barId;
            musicBar.className = 'music-player-bar';
            if (insertBeforeEl) mountTarget.insertBefore(musicBar, insertBeforeEl);
            else mountTarget.appendChild(musicBar);

            const randomColor = MUSIC_CONFIG.themeColors[Math.floor(Math.random() * MUSIC_CONFIG.themeColors.length)];
            musicBar.style.setProperty('--dynamic-random-color', randomColor);
            musicBar.style.setProperty('--dynamic-primary-color', MUSIC_CONFIG.primaryColor);
            musicBar.style.setProperty('--dynamic-secondary-color', MUSIC_CONFIG.secondaryColor);

            musicBar.innerHTML = `
                <div class="music-bar-cover">
                    <img>
                    <span class="music-bar-fallback">🎵</span>
                </div>
                <div class="music-bar-info">
                    <div class="music-bar-title-wrap">
                        <div class="music-bar-title-track">
                            <span class="music-bar-title-seg music-bar-title-seg-primary"></span><span class="music-bar-title-seg music-bar-title-seg-dup" aria-hidden="true"></span>
                        </div>
                    </div>
                    <div class="music-bar-progress-container">
                        <div class="music-bar-progress-fill"></div>
                    </div>
                    <div class="music-bar-time">
                        <span class="music-bar-time-current">00:00</span>
                        <span class="music-bar-time-total">00:00</span>
                    </div>
                    <div class="music-bar-artist"></div>
                </div>
                <button type="button" class="music-bar-play" aria-label="Play/Pause" title="Play/Pause">▶</button>
                <div class="music-bar-volume-container">
                    <button type="button" class="music-bar-volume-btn" aria-label="Volume" title="Volume">🔊</button>
                    <div class="music-bar-volume-slider-wrapper">
                        <div class="music-bar-volume-slider">
                            <div class="music-bar-volume-slider-fill"></div>
                            <div class="music-bar-volume-slider-handle"></div>
                        </div>
                    </div>
                </div>
                <button type="button" class="music-bar-close" aria-label="Close" title="Close">✕</button>
                <div class="aplayer-internal-container" style="display: none;"></div>
            `;
            ensureTitleMarqueeObserver(musicBar);
        } else {
            musicBar.classList.remove('fading-out');

            // Relocate musicBar if a new mount target has appeared
            let newMountTarget = document.getElementById('music-player-mount');
            let newInsertBeforeEl = null;
            if (!newMountTarget) {
                newMountTarget = document.getElementById(MUSIC_CONFIG.dom.containerId);
                newInsertBeforeEl = document.getElementById(MUSIC_CONFIG.dom.insertBeforeId);
            }
            if (newMountTarget && musicBar.parentNode !== newMountTarget) {
                if (newInsertBeforeEl) newMountTarget.insertBefore(musicBar, newInsertBeforeEl);
                else newMountTarget.appendChild(musicBar);
            }
        }

        // 切歌前，先把上一首卡片标记为"已结束"。必须在 currentPlayingTrack
        // 被覆盖之前用旧值更新，否则旧卡片会被改写成新曲目信息。
        const previousTrackForCard = currentPlayingTrack;
        const previousCardId = musicCardMessageId;

        // --- 2. 原地更新 UI 文本/封面 (始终执行) ---
        currentPlayingTrack = trackInfo;
        setMusicBarTitle(musicBar, trackInfo.name || '');
        musicBar.querySelector('.music-bar-artist').textContent = trackInfo.artist || '未知艺术家';

        const coverImg = musicBar.querySelector('img');
        const fallbackIcon = musicBar.querySelector('.music-bar-fallback');
        if (hasCover && coverImg) {
            coverImg.src = trackInfo.cover;
            coverImg.style.display = 'block';
            fallbackIcon.style.display = 'none';
            coverImg.onerror = function () {
                this.style.display = 'none';
                fallbackIcon.style.display = 'flex';
            };
        } else {
            coverImg.style.display = 'none';
            fallbackIcon.style.display = 'flex';
        }

        const progressFill = musicBar.querySelector('.music-bar-progress-fill');
        const timeCurrent = musicBar.querySelector('.music-bar-time-current');
        const timeTotal = musicBar.querySelector('.music-bar-time-total');
        if (progressFill) progressFill.style.width = '0%';
        if (timeCurrent) timeCurrent.textContent = '00:00';

        // --- 2b. 向 React 聊天窗口推送音乐卡片消息 ---
        {
            const host = window.reactChatWindowHost;
            if (host && typeof host.appendMessage === 'function') {
                // 切歌时，先把上一首的卡片标记为"已结束"，避免覆盖 musicCardMessageId
                // 之后旧卡片永远停在"播放中"。注意要用旧 id + 旧 track。
                if (previousCardId && typeof host.updateMessage === 'function') {
                    try {
                        host.updateMessage(previousCardId, {
                            blocks: [{
                                type: 'link',
                                url: (previousTrackForCard && previousTrackForCard.url) || '#',
                                title: (previousTrackForCard && previousTrackForCard.name) || '未知曲目',
                                description: (previousTrackForCard && previousTrackForCard.artist) || '未知艺术家',
                                siteName: '✅ ' + ((window.t && window.t('music.ended')) || '已播完'),
                                thumbnailUrl: (previousTrackForCard && previousTrackForCard.cover) || undefined
                            }]
                        });
                    } catch (_) { /* ignore */ }
                }
                let assistantName = '';
                if (window.lanlan_config && window.lanlan_config.lanlan_name) assistantName = window.lanlan_config.lanlan_name;
                else if (window._currentCatgirl) assistantName = window._currentCatgirl;
                else if (window.currentCatgirl) assistantName = window.currentCatgirl;
                assistantName = assistantName || 'Neko';
                let avatarUrl = '';
                if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                    avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl() || '';
                }
                const now = new Date();
                const timeStr = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');
                const msgId = 'music-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                musicCardMessageId = msgId;
                host.appendMessage({
                    id: msgId,
                    role: 'assistant',
                    author: assistantName,
                    time: timeStr,
                    createdAt: Date.now(),
                    avatarLabel: assistantName.trim().slice(0, 1).toUpperCase(),
                    avatarUrl: avatarUrl || undefined,
                    blocks: [{
                        type: 'link',
                        url: trackInfo.url || '#',
                        title: trackInfo.name || '未知曲目',
                        description: trackInfo.artist || '未知艺术家',
                        siteName: '🎵 ' + ((window.t && window.t('music.playing')) || '播放中'),
                        thumbnailUrl: hasCover ? trackInfo.cover : undefined
                    }],
                    status: 'sent'
                });
            }
        }
        if (timeTotal) timeTotal.textContent = '00:00';

        // --- 3. APlayer 实例管理 (复用或创建) ---
        try {
            const apBtn = musicBar.querySelector('.music-bar-play');
            const updatePlayBtnState = (isPlaying) => {
                const icon = isPlaying ? '⏸' : '▶';
                const text = isPlaying ? 'Pause' : 'Play';
                const tText = window.t ? window.t(isPlaying ? 'music.pause' : 'music.play', text) : text;
                apBtn.textContent = icon;
                apBtn.setAttribute('title', tText);
                apBtn.setAttribute('aria-label', tText);
            };

            let needsInit = isFirstRender || !localPlayer;
            let autoplayBlocked = false;

            if (needsInit) {
                const container = musicBar.querySelector('.aplayer-internal-container');
                const playerConfig = {
                    container: container,
                    theme: MUSIC_CONFIG.primaryColor,
                    loop: 'none',
                    preload: shouldAutoPlay ? 'auto' : 'metadata',
                    autoplay: shouldAutoPlay,
                    mutex: true, volume: MUSIC_CONFIG.defaultVolume,
                    listFolded: true, order: 'normal',
                    audio: [{ name: trackInfo.name, artist: trackInfo.artist, url: trackInfo.url, cover: hasCover ? trackInfo.cover : '' }]
                };

                let aplayerInstance = null;
                if (typeof window.initializeAPlayer === 'function')
                    aplayerInstance = await window.initializeAPlayer(playerConfig);
                else
                    aplayerInstance = new window.APlayer(playerConfig);

                if (!aplayerInstance) throw new Error("APlayer init failed");
                if (currentToken !== latestMusicRequestToken) {
                    if (aplayerInstance.destroy) aplayerInstance.destroy();
                    return;
                }

                localPlayer = aplayerInstance;
                window.aplayer = localPlayer;
                if (!window.aplayerInjected) window.aplayerInjected = {};
                window.aplayerInjected.aplayer = localPlayer;

                // --- 绑定核心事件 (仅在初始化时绑定一次) ---
                // 【核心修复】使用闭包固定当前的播放器实例
                const boundPlayer = localPlayer;

                boundPlayer.on('play', () => {
                    if (autoDestroyTimer) { clearTimeout(autoDestroyTimer); autoDestroyTimer = null; }
                    updatePlayBtnState(true);
                    autoplayBlocked = false;
                    lastPlayPosition = (boundPlayer.audio && boundPlayer.audio.currentTime) || 0;
                    updateMusicCard('playing', currentPlayingTrack);
                    // 跨窗口协调：本地真正开始放歌后通知其他窗口
                    broadcastMusicCoord('music_started');
                    startMusicHeartbeat();
                });
                boundPlayer.on('pause', () => {
                    updatePlayBtnState(false);
                    const cur = (boundPlayer.audio && boundPlayer.audio.currentTime) || 0;
                    accumulatedPlaySeconds += (cur - lastPlayPosition);
                    lastPlayPosition = cur;
                    const tokenAtEvent = boundPlayer._latestToken;
                    if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                    autoDestroyTimer = setTimeout(() => {
                        if (latestMusicRequestToken === tokenAtEvent) destroyMusicPlayer(true, true, true);
                    }, MUSIC_CONFIG.timeouts.paused);
                    updateMusicCard('paused', currentPlayingTrack);
                });
                boundPlayer.on('ended', () => {
                    updatePlayBtnState(false);
                    resetSkipCounter();
                    accumulatedPlaySeconds = 0;
                    lastPlayPosition = 0;
                    const tokenAtEvent = boundPlayer._latestToken;
                    if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                    autoDestroyTimer = setTimeout(() => {
                        if (latestMusicRequestToken === tokenAtEvent) destroyMusicPlayer(true, true, true);
                    }, MUSIC_CONFIG.timeouts.ended);
                    updateMusicCard('ended', currentPlayingTrack);
                });
                boundPlayer.on('error', (err) => {
                    if (boundPlayer._destroying) return;
                    console.error('[Music UI] APlayer error:', err);
                    accumulatedPlaySeconds = 0;
                    lastPlayPosition = 0;

                    const tokenAtEvent = boundPlayer._latestToken;
                    boundPlayer._loadError = true;

                    setTimeout(() => {
                        if (tokenAtEvent !== latestMusicRequestToken) return;
                        if (autoplayBlocked) return;
                        if (boundPlayer._destroying) return;

                        let errorDetail = '播放失败，音频源可能已失效';
                        if (err && err.message) errorDetail = err.message;

                        showErrorToast('music.playError', errorDetail);
                        updatePlayBtnState(false);

                        if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                        autoDestroyTimer = setTimeout(() => {
                            if (tokenAtEvent === latestMusicRequestToken) {
                                destroyMusicPlayer(true, true, true);
                            }
                        }, 3000);

                        updateMusicCard('error', currentPlayingTrack);
                    }, 200);
                });

                // 进度条与播放按钮点击 (使用直接赋值防止重复挂载)
                musicBar.querySelector('.music-bar-close').onclick = (e) => {
                    e.preventDefault();
                    // Accumulate any in-progress playback before checking
                    if (boundPlayer && boundPlayer.audio) {
                        const cur = boundPlayer.audio.currentTime || 0;
                        accumulatedPlaySeconds += (cur - lastPlayPosition);
                    }
                    const playedMs = accumulatedPlaySeconds * 1000;
                    if (playedMs > 0 && playedMs < SKIP_CONFIG.skipThresholdMs) {
                        recordMusicSkip();
                    } else if (playedMs >= SKIP_CONFIG.skipThresholdMs) {
                        resetSkipCounter();
                    }
                    accumulatedPlaySeconds = 0;
                    lastPlayPosition = 0;
                    destroyMusicPlayer(true, true, true);
                };
                apBtn.onclick = (e) => {
                    e.preventDefault();
                    if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                    if (typeof window.setMusicUserDriven === 'function') window.setMusicUserDriven();

                    if (boundPlayer._loadError) {
                        destroyMusicPlayer(true, true, true);
                        return;
                    }

                    if (boundPlayer.audio.ended) boundPlayer.seek(0);
                    boundPlayer.toggle();
                };

                // --- 音量控制逻辑 ---
                const volumeContainer = musicBar.querySelector('.music-bar-volume-container');
                const volumeBtn = musicBar.querySelector('.music-bar-volume-btn');
                const volumeSliderWrapper = musicBar.querySelector('.music-bar-volume-slider-wrapper');
                const volumeSlider = musicBar.querySelector('.music-bar-volume-slider');
                const volumeFill = musicBar.querySelector('.music-bar-volume-slider-fill');
                const volumeHandle = musicBar.querySelector('.music-bar-volume-slider-handle');

                const updateVolumeUI = (vol) => {
                    const percent = vol * 100;
                    volumeFill.style.height = percent + '%';
                    volumeHandle.style.bottom = percent + '%';

                    if (vol === 0) volumeBtn.textContent = '🔇';
                    else if (vol < 0.5) volumeBtn.textContent = '🔉';
                    else volumeBtn.textContent = '🔊';

                    const volText = window.t ? window.t('music.volume', { defaultValue: '音量: ' }) + Math.round(percent) + '%' : '音量: ' + Math.round(percent) + '%';
                    volumeBtn.setAttribute('title', volText);
                };

                // 初始化音量 UI
                updateVolumeUI(boundPlayer.volume());

                volumeBtn.onclick = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    volumeContainer.classList.toggle('expanded');
                };

                let isDraggingVolume = false;
                const adjustVolume = (e) => {
                    const rect = volumeSlider.getBoundingClientRect();

                    let clientY;
                    if (e.clientY !== undefined) {
                        clientY = e.clientY;
                    } else if (e.touches && e.touches.length > 0) {
                        clientY = e.touches[0].clientY;
                    } else {
                        boundPlayer.volume(MUSIC_CONFIG.defaultVolume);
                        updateVolumeUI(MUSIC_CONFIG.defaultVolume);
                        return;
                    }

                    let y = rect.bottom - clientY;
                    let per = Math.max(0, Math.min(y, rect.height)) / rect.height;
                    boundPlayer.volume(per);
                    updateVolumeUI(per);
                };

                currentVolumeDragHandlers = {
                    cleanup: () => {
                        window.removeEventListener('mousemove', adjustVolume);
                        window.removeEventListener('mouseup', stopVolumeDrag);
                        window.removeEventListener('touchmove', adjustVolume);
                        window.removeEventListener('touchend', stopVolumeDrag);
                        window.removeEventListener('touchcancel', stopVolumeDrag);
                        isDraggingVolume = false;
                    }
                };

                const stopVolumeDrag = (e) => {
                    if (!isDraggingVolume) return;
                    // 拖拽结束时，直接调用清理工具
                    if (currentVolumeDragHandlers) currentVolumeDragHandlers.cleanup();
                };

                volumeSliderWrapper.onmousedown = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    isDraggingVolume = true;
                    adjustVolume(e);
                    // 直接使用原生绑定
                    window.addEventListener('mousemove', adjustVolume);
                    window.addEventListener('mouseup', stopVolumeDrag);
                };

                volumeSliderWrapper.ontouchstart = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    isDraggingVolume = true;
                    adjustVolume(e);
                    window.addEventListener('touchmove', adjustVolume);
                    window.addEventListener('touchend', stopVolumeDrag);
                    window.addEventListener('touchcancel', stopVolumeDrag);
                };

                // 点击外部收起音量
                const closeVolumeOnOutsideClick = (e) => {
                    if (volumeContainer.classList.contains('expanded') && !volumeContainer.contains(e.target)) {
                        volumeContainer.classList.remove('expanded');
                    }
                };
                addManagedListener('mousedown', closeVolumeOnOutsideClick);

                // 同步 APlayer 的音量变化
                boundPlayer.on('volumechange', () => {
                    updateVolumeUI(boundPlayer.volume());
                });

                // 进度更新与拖拽 (保持原有逻辑)
                let isDragging = false;
                const progressContainer = musicBar.querySelector('.music-bar-progress-container');
                localPlayer.on('timeupdate', () => {
                    if (!localPlayer || !localPlayer.audio || isDragging) return;
                    const cur = localPlayer.audio.currentTime, dur = localPlayer.audio.duration;
                    if (dur > 0) {
                        if (progressFill) progressFill.style.width = (cur / dur * 100) + '%';
                        if (timeCurrent) timeCurrent.textContent = formatTime(cur);
                        if (timeTotal) timeTotal.textContent = formatTime(dur);
                    }
                });

                const handleProgressMove = (e) => {
                    if (!isDragging) return;
                    const rect = progressContainer.getBoundingClientRect();
                    if (!rect.width) return;

                    // 修复细节：判断 0 的写法，防止 clientX 为 0 时误触 fallback
                    const clientX = e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
                    let x = clientX - rect.left;

                    x = Math.max(0, Math.min(x, rect.width));
                    const per = x / rect.width;
                    if (progressFill) progressFill.style.width = (per * 100) + '%';
                    if (timeCurrent && localPlayer.audio.duration) timeCurrent.textContent = formatTime(per * localPlayer.audio.duration);
                };
                const stopDrag = (e) => {
                    if (!isDragging) return;
                    isDragging = false;
                    // 【核心修复】必须先移除全局监听，然后再执行可能的 early return (CodeRabbit 建议)
                    window.removeEventListener('mousemove', handleProgressMove);
                    window.removeEventListener('mouseup', stopDrag);
                    window.removeEventListener('touchmove', handleProgressMove);
                    window.removeEventListener('touchend', stopDrag);

                    const rect = progressContainer.getBoundingClientRect();
                    if (!rect.width) return;

                    const clientX = e.clientX !== undefined ? e.clientX : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0);
                    let x = clientX - rect.left;

                    const per = Math.max(0, Math.min(x, rect.width)) / rect.width;
                    if (boundPlayer.audio.duration) boundPlayer.seek(per * boundPlayer.audio.duration);
                };

                // 记录全局引用以便销毁时清理
                currentDragHandlers = {
                    cleanup: () => {
                        window.removeEventListener('mousemove', handleProgressMove);
                        window.removeEventListener('mouseup', stopDrag);
                        window.removeEventListener('touchmove', handleProgressMove);
                        window.removeEventListener('touchend', stopDrag);
                        window.removeEventListener('touchcancel', stopDrag);
                        isDragging = false;
                    }
                };

                // 【核心修复】使用直接赋值绑定，防止 DOM 复用时监听器叠加 (CodeRabbit 建议)
                progressContainer.onmousedown = (e) => {
                    isDragging = true; handleProgressMove(e);
                    window.addEventListener('mousemove', handleProgressMove); window.addEventListener('mouseup', stopDrag);
                    window.addEventListener('touchmove', handleProgressMove); window.addEventListener('touchend', stopDrag);
                };
                progressContainer.ontouchstart = (e) => {
                    isDragging = true; handleProgressMove(e);
                    window.addEventListener('mousemove', handleProgressMove); window.addEventListener('mouseup', stopDrag);
                    window.addEventListener('touchmove', handleProgressMove); window.addEventListener('touchend', stopDrag);
                    window.addEventListener('touchcancel', stopDrag);
                };

                // 自动播放拦截器：精确区分“被拦截”与“加载失败”
                if (localPlayer.audio && typeof localPlayer.audio.play === 'function') {
                    const originalPlay = localPlayer.audio.play;
                    // 使用闭包捕获当前的播放器引用
                    const boundPlayerForProxy = localPlayer;
                    localPlayer.audio.play = function () {
                        // 捕获触发播放时的 token
                        const tokenAtPlay = latestMusicRequestToken;
                        const pp = originalPlay.call(this);
                        if (pp && pp.catch) {
                            pp.catch(err => {
                                // 逻辑漏洞修复：如果 play 失败的回调执行时，用户已经切换了下一首歌，则不应再为旧歌曲设置销毁定时器
                                if (tokenAtPlay !== latestMusicRequestToken) {
                                    console.log('[Music UI] Observed rejected play promise from obsolete token, ignoring.');
                                    return;
                                }

                                if (err.name === 'NotAllowedError') {
                                    autoplayBlocked = true;
                                    updatePlayBtnState(false);
                                    showErrorToast('music.autoplayBlocked', '由于浏览器限制，已拦截自动播放。请点击页面任意位置恢复，或点击此处。');

                                    // 自动播放被拦截视为“未播放”，保持 24 秒销毁计时
                                    if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                                    autoDestroyTimer = setTimeout(() => {
                                        if (tokenAtPlay === latestMusicRequestToken) {
                                            destroyMusicPlayer(true, true, true);
                                        }
                                    }, MUSIC_CONFIG.timeouts.idle);

                                    // 交互式代理：一旦被拦截，监听全局下一次点击并尝试自动播放
                                    setupAutoplayProxy(tokenAtPlay, boundPlayerForProxy);
                                }
                            });
                        }
                        return pp;
                    };
                }

                function setupAutoplayProxy(tokenAtProxy, bPlayer) {
                    const startOnInteraction = () => {
                        // 【核心修复】增加 token 校验，且交互后移除监听
                        // 如果在点击之前，用户已经切换到了新的请求，或者播放器已销毁，则不执行旧的播放操作
                        if (tokenAtProxy === latestMusicRequestToken && bPlayer && bPlayer.audio && bPlayer.audio.paused) {
                            console.log('[Music UI] 检测到用户交互，正在尝试通过代理触发延迟播放');
                            bPlayer.play();
                        }
                        // 使用一旦触发即移除的特性，手动解绑所有潜在的代理监听
                        window.removeEventListener('mousedown', startOnInteraction);
                        window.removeEventListener('touchstart', startOnInteraction);
                    };
                    window.addEventListener('mousedown', startOnInteraction, { once: true });
                    window.addEventListener('touchstart', startOnInteraction, { once: true });
                    // 这些 once 监听器也会被管理，虽然它们会自动移除
                    managedWindowListeners.push({ type: 'mousedown', listener: startOnInteraction, options: { once: true } });
                    managedWindowListeners.push({ type: 'touchstart', listener: startOnInteraction, options: { once: true } });
                }
            } else {
                // --- 复用模式下的切歌逻辑 ---
                // Reset skip-tracking counters so the previous track's time doesn't carry over
                accumulatedPlaySeconds = 0;
                lastPlayPosition = 0;
                if (localPlayer.list) {
                    localPlayer.list.clear();
                    localPlayer.list.add([{ name: trackInfo.name, artist: trackInfo.artist, url: trackInfo.url, cover: hasCover ? trackInfo.cover : '' }]);
                    localPlayer.list.switch(0);
                }
                updatePlayBtnState(false);
            }

            // 【核心修复】同步更新实例的最新 Token，确保复用模式下事件回调中的 Token 校验依然有效
            localPlayer._latestToken = currentToken;

            // 执行播放
            if (shouldAutoPlay) {
                setTimeout(() => {
                    // 【核心修复】延迟播放校验 Token，防止旧请求误触发新曲播放 (CodeRabbit 建议)
                    if (currentToken === latestMusicRequestToken && localPlayer && typeof localPlayer.play === 'function') {
                        localPlayer.play();
                    }
                }, 100);
            } else {
                // AI 推荐但是未点击自动播放，启动 24 秒销毁计时
                if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                autoDestroyTimer = setTimeout(() => destroyMusicPlayer(true, true, true), MUSIC_CONFIG.timeouts.idle);
            }
        } catch (err) {
            if (currentToken !== latestMusicRequestToken) return;
            console.error('[Music UI] 播放器处理异常:', err);
            if (isFirstRender && musicBar) musicBar.remove();
            showErrorToast('music.playError', '音乐播放加载失败');
        }
    };

    // --- 6. 暴露全局接口 ---
    /**
     * 向播放器发送播放请求 [Async Ready]
     * 如果 URL 暂时不在白名单中，会等待最多 500ms 以响应并行的插件注册
     */
    window.sendMusicMessage = async function (trackInfo, shouldAutoPlay = true) {
        if (!trackInfo) return false;

        // 进入 dispatch 流水线就立即 +1 —— 让并发的 dispatchMusicPlay
        // 能在 isMusicPlaying() 还未变成 true 的"加载中"窗口里也识别到占用。
        // 用本地 pendingReleased 防止重复释放。
        musicDispatchPendingCount += 1;
        let pendingReleased = false;
        const releasePending = () => {
            if (pendingReleased) return;
            pendingReleased = true;
            musicDispatchPendingCount = Math.max(0, musicDispatchPendingCount - 1);
        };

        // --- 核心修复：更鲁棒的 URL 预清理 ---
        if (trackInfo.url && typeof trackInfo.url === 'string') {
            try {
                let lastUrl = '';
                while (trackInfo.url !== lastUrl) {
                    lastUrl = trackInfo.url;
                    trackInfo.url = trackInfo.url
                        .replace(/&amp;/g, '&')
                        .replace(/&amp%3B/g, '&')
                        .replace(/%26amp%3B/g, '&');
                }
            } catch (e) {
                console.warn('[Music UI] URL sanitization failed:', e);
            }
        }

        // --- 网易云音乐代理：如果检测到网易云外链，替换为后端代理接口 ---
        // 统一使用 /api/music/proxy 路由
        if (trackInfo.url && trackInfo.url.includes('music.163.com') && !trackInfo.url.startsWith('/api/music/proxy')) {
            const originalUrl = trackInfo.url;
            const encodedUrl = encodeURIComponent(trackInfo.url);
            trackInfo.url = `/api/music/proxy?url=${encodedUrl}`;
            console.log('[Music UI] 网易云URL已代理:', originalUrl, '->', trackInfo.url);
        }

        const now = Date.now();
        // 5秒去重逻辑
        if (lastPlayedMusicUrl === trackInfo.url && (now - lastMusicPlayTime) < 5000 && isPlayerInDOM()) {
            console.log('[Music UI] 5秒内相同音乐且已在播放中，跳过播发请求:', trackInfo.name);
            releasePending();
            return true;
        }

        if (isSameTrack(trackInfo) && !isPlayerInDOM()) {
            currentPlayingTrack = null;
        }

        // 竞态保护：如果 URL 不在白名单，原地等待 500ms 看看是否会有插件注册进来
        if (trackInfo.url && !isSafeUrl(trackInfo.url)) {
            console.log('[Music UI] URL 暂未加入白名单，等待加白信号...', trackInfo.url);
            try {
                await new Promise((resolve) => {
                    const timeout = setTimeout(() => {
                        window.removeEventListener('music-allowlist-updated', onUpdate);
                        resolve();
                    }, 500);

                    function onUpdate() {
                        if (isSafeUrl(trackInfo.url)) {
                            console.log('[Music UI] 收到加白信号，URL 已加白名单。');
                            clearTimeout(timeout);
                            window.removeEventListener('music-allowlist-updated', onUpdate);
                            resolve();
                        }
                    }
                    window.addEventListener('music-allowlist-updated', onUpdate);
                });
            } catch (e) {
                console.warn('[Music UI] 竞态等待异常:', e);
            }
        }

        if (!trackInfo.url || !isSafeUrl(trackInfo.url)) {
            console.warn('[Music UI] 音频 URL 未通过安全校验:', trackInfo.url);
            if (window.showStatusToast) {
                var domain = extractHostname(trackInfo.url) || '未知源';
                var msg = window.t ? window.t('music.unsafeSource', { domain: domain }) : ('已拦截不安全音源: ' + domain);
                window.showStatusToast(msg, 5000);
            }
            releasePending();
            return false;
        }

        const currentToken = ++latestMusicRequestToken;
        lastPlayedMusicUrl = trackInfo.url;
        lastMusicPlayTime = now;

        // 特殊优化：如果是一模一样的歌曲且播放器已存在，直接播放而不是重载整个库
        if (isSameTrack(trackInfo) && isPlayerInDOM()) {
            const player = getMusicPlayerInstance();
            if (shouldAutoPlay && player && player.audio && player.audio.paused) {
                if (typeof window.setMusicUserDriven === 'function')
                    window.setMusicUserDriven();
                player.play();
                showNowPlayingToast(trackInfo.name);
            }
            releasePending();
            return true;
        }

        showNowPlayingToast(trackInfo.name);

        loadAPlayerLibrary().then(function () {
            return executePlay(trackInfo, currentToken, shouldAutoPlay);
        }).catch(function (err) {
            // 库加载失败同样需要校验 token，防止关闭后弹出报错
            if (currentToken === latestMusicRequestToken) {
                console.error('[Music UI] 库加载失败:', err);
                showErrorToast('music.loadError', '音乐播放器加载失败');
            } else {
                console.log('[Music UI] 库加载失败，但请求已取消，忽略报错');
            }
        }).finally(function () {
            // 每次调用独立释放：不用 token 判断，本次引用计数 -1 就好。
            releasePending();
        });

        return true;
    };
    // 全局解锁函数
    const unlockAudio = () => {
        console.log('[Audio] 检测到交互，尝试激活音频环境...');

        // 1. 解锁 Web Audio API
        if (window.lanlanAudioContext && window.lanlanAudioContext.state === 'suspended') {
            window.lanlanAudioContext.resume();
        }

        // 2. 解锁 APlayer 实例 (如果有的话)
        const player = window.aplayer || (window.aplayerInjected && window.aplayerInjected.aplayer);
        if (player && player.audio && player.audio.paused) {
            // 如果当前有排队中的音乐，尝试播放
            const playPromise = player.play();
            if (playPromise !== undefined && typeof playPromise.catch === 'function') {
                playPromise.catch(() => { });
            }
        }

        // 移除监听器，只需触发一次
        document.removeEventListener('click', unlockAudio);
        document.removeEventListener('keydown', unlockAudio);
    };

    // 监听任何点击或按键
    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('keydown', unlockAudio, { once: true });

    const isMusicPlaying = () => {
        try {
            return !!(localPlayer && localPlayer.audio && !localPlayer.audio.paused && isPlayerInDOM());
        } catch (e) {
            console.error('[Music UI] Error checking if music is playing:', e);
            return false;
        }
    };

    const getMusicCurrentTrack = () => {
        try {
            return currentPlayingTrack || null;
        } catch (e) {
            console.error('[Music UI] Error getting current track:', e);
            return null;
        }
    };

    // --- 自动从后端同步音乐源域名到白名单 ---
    const syncDomainsFromBackend = async () => {
        try {
            const response = await fetch('/api/music/domains');
            if (response.ok) {
                const data = await response.json();
                if (data.success && data.domains) {
                    const newDomains = data.domains.filter(d => !MUSIC_CONFIG.allowlist.includes(d));
                    if (newDomains.length > 0) {
                        MUSIC_CONFIG.allowlist.push(...newDomains);
                        console.log('[Music UI] 已同步后端域名到白名单', newDomains);
                        window.dispatchEvent(new CustomEvent('music-allowlist-updated'));
                    }
                }
            }
        } catch (e) {
            console.warn('[Music UI] 从后端同步域名失败:', e);
        }
    };

    const MusicPluginAPI = {
        getAllowlist: () => [...MUSIC_CONFIG.allowlist],
        addAllowlist: (input) => {
            const inputs = Array.isArray(input) ? input : [input];
            const newDomains = inputs
                .map(extractHostname)
                .filter(d => d && !MUSIC_CONFIG.allowlist.includes(d));

            if (newDomains.length > 0) {
                MUSIC_CONFIG.allowlist.push(...newDomains);
                console.log('[Music UI] Allowlist updated:', newDomains);
                window.dispatchEvent(new CustomEvent('music-allowlist-updated'));
            }
        }
    };

    // --- 暴露接口 ---
    window.destroyMusicPlayer = destroyMusicPlayer;
    window.getMusicPlayerInstance = getMusicPlayerInstance;
    window.isMusicPlaying = isMusicPlaying;
    window.isMusicCooldown = isInMusicCooldown;
    window.getMusicCurrentTrack = getMusicCurrentTrack;
    window.MusicPluginAPI = MusicPluginAPI;

    // 竞态拦截辅助：dispatch 流水线中（URL 校验/库加载/init）的占位标记
    window.isMusicPending = () => musicDispatchPendingCount > 0;
    // 跨窗口协调：其他窗口正在播歌（基于 BroadcastChannel 通报）
    window.isRemoteMusicActive = isRemoteMusicActive;
    // 推荐频率限流：最近是否刚派发过 proactive 推荐
    window.isMusicRecommendRateLimited = isMusicRecommendRateLimited;
    window.markProactiveMusicRecommended = markProactiveMusicRecommended;

    // 派发就绪事件，通知提前加载的插件可以开始注册域名了
    window.dispatchEvent(new CustomEvent('music-ui-ready'));
    console.log('[Music UI] 接口已暴露，就绪信号已发送');

    // 自动从后端同步音乐源域名到白名单
    syncDomainsFromBackend();

})();