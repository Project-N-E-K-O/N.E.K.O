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
        defaultVolume: 0.5
    };

    let currentPlayingTrack = null;
    let localPlayer = null;
    let aplayerLoadPromise = null;
    let latestMusicRequestToken = 0;

    // --- 状态追踪：用于 5 秒去重 ---
    let lastPlayedMusicUrl = null;
    let lastMusicPlayTime = 0;

    // --- 2. 原始工具函数 (完全保留所有域名白名单) ---
    const isSafeUrl = (url) => {
        if (!url) return false;
        try {
            const parsed = new URL(url);
            if (!['http:', 'https:'].includes(parsed.protocol)) return false;
            const allowedDomains = [
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
            ];
            return allowedDomains.some(d => parsed.hostname === d || parsed.hostname.endsWith('.' + d));
        } catch { return false; }
    };

    const getMusicPlayerInstance = () => localPlayer;

    const isPlayerInDOM = () => !!document.getElementById(MUSIC_CONFIG.dom.barId);

    const isSameTrack = (info) => {
        return currentPlayingTrack &&
            currentPlayingTrack.name === info.name &&
            currentPlayingTrack.artist === info.artist &&
            currentPlayingTrack.url === info.url;
    };

    const showErrorToast = (msgKey, defaultMsg) => {
        if (typeof window.showStatusToast === 'function') {
            const errMsg = window.t ? window.t(msgKey, { defaultValue: defaultMsg }) : defaultMsg;
            window.showStatusToast(errMsg, 3000);
        }
    };

    const showNowPlayingToast = (name) => {
        if (typeof window.showStatusToast === 'function') {
            const unknownTrack = window.t ? window.t('music.unknownTrack', { defaultValue: '未知曲目' }) : '未知曲目';
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

        // 统一清理：即使不是 fullTeardown，切歌时也应该销毁旧实例释放资源
        if (localPlayer && typeof localPlayer.destroy === 'function') {
            try {
                localPlayer.destroy();
            } catch (e) {
                console.warn('[Music UI] Error during player destroy:', e);
            }
        }

        if (fullTeardown) {
            if (typeof window.destroyAPlayer === 'function') {
                window.destroyAPlayer();
            }
        }

        localPlayer = null;
        window.aplayer = null;
        if (window.aplayerInjected) {
            window.aplayerInjected.aplayer = null;
        }

        if (removeDOM) {
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
        }
        currentPlayingTrack = null;
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
    const executePlay = async (trackInfo, currentToken, shouldAutoPlay = true) => {
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
            const chatContainerEl = document.getElementById(MUSIC_CONFIG.dom.containerId);
            const textInputArea = document.getElementById(MUSIC_CONFIG.dom.insertBeforeId);
            if (!chatContainerEl) return;

            musicBar = document.createElement('div');
            musicBar.id = MUSIC_CONFIG.dom.barId;
            musicBar.className = 'music-player-bar';
            if (textInputArea) chatContainerEl.insertBefore(musicBar, textInputArea);
            else chatContainerEl.appendChild(musicBar);

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
                    <div class="music-bar-title"></div>
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
                <button type="button" class="music-bar-close" aria-label="Close" title="Close">✕</button>
                <div class="aplayer-internal-container" style="display: none;"></div>
            `;
        } else {
            musicBar.classList.remove('fading-out');
        }

        // --- 2. 原地更新 UI 文本/封面 (始终执行) ---
        currentPlayingTrack = trackInfo;
        musicBar.querySelector('.music-bar-title').textContent = trackInfo.name || '未知曲目';
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
        if (timeTotal) timeTotal.textContent = '00:00';

        // --- 3. APlayer 实例管理 (复用或创建) ---
        try {
            const apBtn = musicBar.querySelector('.music-bar-play');
            const updatePlayBtnState = (isPlaying) => {
                const icon = isPlaying ? '⏸' : '▶';
                const text = isPlaying ? 'Pause' : 'Play';
                const tText = window.t ? window.t(isPlaying ? 'music.pause' : 'music.play', { defaultValue: text }) : text;
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
                localPlayer.on('play', () => {
                    if (autoDestroyTimer) { clearTimeout(autoDestroyTimer); autoDestroyTimer = null; }
                    updatePlayBtnState(true);
                    autoplayBlocked = false;
                });
                localPlayer.on('pause', () => updatePlayBtnState(false));
                localPlayer.on('ended', () => {
                    updatePlayBtnState(false);
                    autoDestroyTimer = setTimeout(() => destroyMusicPlayer(true, true, true), 3000);
                });
                localPlayer.on('error', (err) => {
                    console.error('[Music UI] APlayer error:', err);
                    // 使用 latestMusicRequestToken 校验，确保是当前正在尝试的播放才有权弹窗
                    setTimeout(() => {
                        if (autoplayBlocked) return;
                        showErrorToast('music.playError', '播放失败，音频源可能已失效');
                        updatePlayBtnState(false);
                    }, 200);
                });

                // 进度条与播放按钮点击
                musicBar.querySelector('.music-bar-close').onclick = (e) => {
                    e.preventDefault();
                    destroyMusicPlayer(true, true, true);
                };
                apBtn.onclick = (e) => {
                    e.preventDefault();
                    if (autoDestroyTimer) clearTimeout(autoDestroyTimer);
                    if (typeof window.setMusicUserDriven === 'function') window.setMusicUserDriven();
                    if (localPlayer.audio.ended) localPlayer.seek(0);
                    localPlayer.toggle();
                };

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
                    let x = (e.clientX || (e.touches && e.touches[0].clientX)) - rect.left;
                    x = Math.max(0, Math.min(x, rect.width));
                    const per = x / rect.width;
                    if (progressFill) progressFill.style.width = (per * 100) + '%';
                    if (timeCurrent && localPlayer.audio.duration) timeCurrent.textContent = formatTime(per * localPlayer.audio.duration);
                };
                const stopDrag = (e) => {
                    if (!isDragging) return; isDragging = false;
                    const rect = progressContainer.getBoundingClientRect();
                    let x = (e.clientX || (e.changedTouches && e.changedTouches[0].clientX) || 0) - rect.left;
                    const per = Math.max(0, Math.min(x, rect.width)) / rect.width;
                    if (localPlayer.audio.duration) localPlayer.seek(per * localPlayer.audio.duration);
                    window.removeEventListener('mousemove', handleProgressMove); window.removeEventListener('mouseup', stopDrag);
                    window.removeEventListener('touchmove', handleProgressMove); window.removeEventListener('touchend', stopDrag);
                };
                progressContainer.addEventListener('mousedown', (e) => {
                    isDragging = true; handleProgressMove(e);
                    window.addEventListener('mousemove', handleProgressMove); window.addEventListener('mouseup', stopDrag);
                    window.addEventListener('touchmove', handleProgressMove); window.addEventListener('touchend', stopDrag);
                });
                progressContainer.addEventListener('touchstart', (e) => {
                    isDragging = true; handleProgressMove(e);
                    window.addEventListener('mousemove', handleProgressMove); window.addEventListener('mouseup', stopDrag);
                    window.addEventListener('touchmove', handleProgressMove); window.addEventListener('touchend', stopDrag);
                });

                // 自动播放拦截器：精确区分“被拦截”与“加载失败”
                if (localPlayer.audio && typeof localPlayer.audio.play === 'function') {
                    const originalPlay = localPlayer.audio.play;
                    localPlayer.audio.play = function () {
                        const pp = originalPlay.call(this);
                        if (pp && pp.catch) {
                            pp.catch(err => {
                                if (err.name === 'NotAllowedError') {
                                    autoplayBlocked = true;
                                    updatePlayBtnState(false);
                                    showErrorToast('music.autoplayBlocked', '由于浏览器限制，已拦截自动播放。请点击页面任意位置恢复，或点击此处。');
                                    
                                    // 交互式代理：一旦被拦截，监听全局下一次点击并尝试自动播放
                                    setupAutoplayProxy();
                                }
                            });
                        }
                        return pp;
                    };
                }

                function setupAutoplayProxy() {
                    const startOnInteraction = () => {
                        if (localPlayer && localPlayer.audio && localPlayer.audio.paused) {
                            console.log('[Music UI] 检测到用户交互，正在尝试通过代理触发延迟播放');
                            localPlayer.play();
                        }
                        window.removeEventListener('mousedown', startOnInteraction);
                        window.removeEventListener('touchstart', startOnInteraction);
                    };
                    window.addEventListener('mousedown', startOnInteraction, { once: true });
                    window.addEventListener('touchstart', startOnInteraction, { once: true });
                }
            } else {
                // --- 复用模式下的切歌逻辑 ---
                if (localPlayer.list) {
                    localPlayer.list.clear();
                    localPlayer.list.add([{ name: trackInfo.name, artist: trackInfo.artist, url: trackInfo.url, cover: hasCover ? trackInfo.cover : '' }]);
                    localPlayer.list.switch(0);
                }
                updatePlayBtnState(false);
            }

            // 执行播放
            if (shouldAutoPlay) {
                setTimeout(() => {
                    if (localPlayer && typeof localPlayer.play === 'function') {
                        localPlayer.play();
                    }
                }, 100);
            }
        } catch (err) {
            if (currentToken !== latestMusicRequestToken) return;
            console.error('[Music UI] 播放器处理异常:', err);
            if (isFirstRender && musicBar) musicBar.remove();
            showErrorToast('music.playError', '音乐播放加载失败');
        }
    };

    // --- 6. 暴露全局接口 ---
    window.sendMusicMessage = function (trackInfo, shouldAutoPlay = true) {
        if (!trackInfo) return false;

        // --- 核心修复：更鲁棒的 URL 预清理 ---
        // 递归处理多次转义或复杂编码的 HTML 实体
        if (trackInfo.url && typeof trackInfo.url === 'string') {
            try {
                // 处理常见的 &amp; 变体
                let cleanedUrl = trackInfo.url
                    .replace(/&amp;/g, '&')
                    .replace(/&amp%3B/g, '&')
                    .replace(/%26amp%3B/g, '&')
                    .replace(/&amp;/g, '&'); // 再次处理可能残留的层级

                trackInfo.url = cleanedUrl;
            } catch (e) {
                console.warn('[Music UI] URL sanitization failed:', e);
            }
        }

        const now = Date.now();
        // 如果是 5 秒内相同的 URL 且播放器已在界面中，视为重复触发并略过（去重交回组件层处理）
        if (lastPlayedMusicUrl === trackInfo.url && (now - lastMusicPlayTime) < 5000 && isPlayerInDOM()) {
            console.log('[Music UI] 5秒内相同音乐且已在播放中，跳过播发请求:', trackInfo.name);
            return true; // 视为已接受处理
        }

        // 如果是同一首歌，但音乐条已经被关掉了（DOM里找不到了）
        if (isSameTrack(trackInfo) && !isPlayerInDOM()) {
            currentPlayingTrack = null;
        }
        if (!trackInfo.url || !isSafeUrl(trackInfo.url)) {
            console.warn('[Music UI] 音频 URL 未通过安全校验:', trackInfo.url);
            return false;
        }

        const currentToken = ++latestMusicRequestToken;
        lastPlayedMusicUrl = trackInfo.url;
        lastMusicPlayTime = now;

        if (isSameTrack(trackInfo) && isPlayerInDOM()) {
            const player = getMusicPlayerInstance();
            if (shouldAutoPlay && player && player.audio && player.audio.paused) {
                if (typeof window.setMusicUserDriven === 'function')
                    window.setMusicUserDriven();
                player.play();
                showNowPlayingToast(trackInfo.name);
            }
            return true;
        }

        showNowPlayingToast(trackInfo.name);

        loadAPlayerLibrary().then(() => {
            executePlay(trackInfo, currentToken, shouldAutoPlay);
        }).catch(err => {
            // 库加载失败同样需要校验 token，防止关闭后弹出报错
            if (currentToken === latestMusicRequestToken) {
                console.error('[Music UI] 库加载失败:', err);
                showErrorToast('music.loadError', '音乐播放器加载失败');
            } else {
                console.log('[Music UI] 库加载失败，但请求已取消，忽略报错');
            }
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

    // --- 暴露接口 ---
    window.destroyMusicPlayer = destroyMusicPlayer;
    window.getMusicPlayerInstance = getMusicPlayerInstance;
    window.isMusicPlaying = isMusicPlaying;
    window.getMusicCurrentTrack = getMusicCurrentTrack;

    window.dispatchEvent(new CustomEvent('music-ui-ready'));
    console.log('[Music UI] 接口已暴露，就绪信号已发送');

})();