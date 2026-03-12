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
                'dummyimage.com', 'music.163.com'
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
            const errMsg = window.t ? window.t(msgKey, defaultMsg) : defaultMsg;
            window.showStatusToast(errMsg, 3000);
        }
    };

    const showNowPlayingToast = (name) => {
        if (typeof window.showStatusToast === 'function') {
            const displayName = name || '未知曲目';
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

    const destroyMusicPlayer = (removeDOM = true, fullTeardown = false) => {
        // 核心：优先执行本地暂停，避免声音残留
        if (localPlayer && typeof localPlayer.pause === 'function') {
            localPlayer.pause();
        }

        if (fullTeardown) {
            if (typeof window.destroyAPlayer === 'function') {
                window.destroyAPlayer();
            }
            if (localPlayer && typeof localPlayer.destroy === 'function') {
                localPlayer.destroy();
            }
        }

        localPlayer = null;
        window.aplayer = null;
        if (window.aplayerInjected) {
            window.aplayerInjected.aplayer = null;
        }

        if (removeDOM) {
            const bar = document.getElementById(MUSIC_CONFIG.dom.barId);
            if (bar) bar.remove();
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

    // --- 5. 播放器挂载逻辑 (保留所有 DOM 安全赋值) ---
    const executePlay = async (trackInfo, currentToken, shouldAutoPlay = true) => {
        if (currentToken !== latestMusicRequestToken)
            return;

        if (getMusicPlayerInstance())
            destroyMusicPlayer(true);
        document.querySelectorAll('.music-player-bar.fading-out').forEach(el => { el.remove(); });
        currentPlayingTrack = trackInfo;

        const playerId = 'music-bar-player-' + Math.random().toString(36).slice(2, 10);
        const randomColor = MUSIC_CONFIG.themeColors[Math.floor(Math.random() * MUSIC_CONFIG.themeColors.length)];
        const hasCover = trackInfo.cover && trackInfo.cover.length > 0 && isSafeUrl(trackInfo.cover);

        const chatContainerEl = document.getElementById(MUSIC_CONFIG.dom.containerId);
        const textInputArea = document.getElementById(MUSIC_CONFIG.dom.insertBeforeId);
        if (!chatContainerEl)
            return;

        let musicBar = document.getElementById(MUSIC_CONFIG.dom.barId);
        if (!musicBar) {
            musicBar = document.createElement('div');
            musicBar.id = MUSIC_CONFIG.dom.barId;
            musicBar.className = 'music-player-bar';
            if (textInputArea)
                chatContainerEl.insertBefore(musicBar, textInputArea);
            else
                chatContainerEl.appendChild(musicBar);
        }

        musicBar.style.setProperty('--dynamic-random-color', randomColor);
        musicBar.style.setProperty('--dynamic-primary-color', MUSIC_CONFIG.primaryColor);
        musicBar.style.setProperty('--dynamic-secondary-color', MUSIC_CONFIG.secondaryColor);

        musicBar.innerHTML = `
            <div class="music-bar-cover">
                <img style="display: ${hasCover ? 'block' : 'none'};" alt="cover">
                <span class="music-bar-fallback" style="display: ${hasCover ? 'none' : 'flex'};">🎵</span>
            </div>
            <div class="music-bar-info">
                <div class="music-bar-title"></div>
                <div class="music-bar-artist"></div>
            </div>
            <button type="button" class="music-bar-play" aria-label="Play/Pause" title="Play/Pause">▶</button>
            <button type="button" class="music-bar-close" aria-label="Close" title="Close">✕</button>
            <div id="${playerId}" style="display: none;"></div>
        `;

        musicBar.querySelector('.music-bar-title').textContent = trackInfo.name || '未知曲目';
        musicBar.querySelector('.music-bar-artist').textContent = trackInfo.artist || '未知艺术家';

        const coverImg = musicBar.querySelector('img');
        const fallbackIcon = musicBar.querySelector('.music-bar-fallback');
        if (hasCover && coverImg) {
            coverImg.src = trackInfo.cover;
            coverImg.onerror = function () {
                this.style.display = 'none';
                if (fallbackIcon)
                    fallbackIcon.style.display = 'flex';
            };
        }

        const closeBtn = musicBar.querySelector('.music-bar-close');
        closeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            latestMusicRequestToken++; // 取消可能正在进行的异步加载请求
            destroyMusicPlayer(false, true); // 手动关闭时执行 full teardown
            if (musicBar.parentNode) {
                musicBar.classList.add('fading-out');
                let isRemoved = false;
                const safeRemove = () => {
                    if (!isRemoved && musicBar.parentNode) {
                        musicBar.remove();
                        isRemoved = true;
                    }
                };
                musicBar.addEventListener('animationend', safeRemove, { once: true });
                setTimeout(safeRemove, 300);
            }
        });

        const container = musicBar.querySelector(`#${playerId}`);
        const apBtn = musicBar.querySelector('.music-bar-play');
        if (!container) {
            musicBar.remove();
            currentPlayingTrack = null;
            return;
        }

        try {
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

            if (!aplayerInstance)
                throw new Error("APlayer init failed");

            if (currentToken !== latestMusicRequestToken) {
                if (typeof aplayerInstance.destroy === 'function')
                    aplayerInstance.destroy();
                return;
            }

            localPlayer = aplayerInstance;
            window.aplayer = localPlayer;

            if (!window.aplayerInjected)
                window.aplayerInjected = {};
            window.aplayerInjected.aplayer = localPlayer;

            if (apBtn) {
                apBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (typeof window.setMusicUserDriven === 'function')
                        window.setMusicUserDriven();
                    localPlayer.toggle();
                });
                const updatePlayBtnState = (isPlaying) => {
                    const icon = isPlaying ? '⏸' : '▶';
                    const text = isPlaying ? 'Pause' : 'Play';
                    const tText = window.t ? window.t(isPlaying ? 'music.pause' : 'music.play', text) : text;
                    apBtn.textContent = icon;
                    apBtn.setAttribute('title', tText);
                    apBtn.setAttribute('aria-label', tText);
                };

                updatePlayBtnState(shouldAutoPlay);
                localPlayer.on('play', () => updatePlayBtnState(true));
                localPlayer.on('pause', () => updatePlayBtnState(false));
                localPlayer.on('ended', () => updatePlayBtnState(false));

                // 处理浏览器拦截自动播放导致界面“假死”的问题
                // APlayer 的 play() 实际上是一个 Promise，如果被拦截会抛出带有 'NotAllowedError' 的 DOMException
                if (shouldAutoPlay) {
                    // 由于 APlayer 内部也会调用 audio.play()，我们为其代理错误捕获
                    const originalPlay = localPlayer.audio.play;
                    if (originalPlay) {
                        localPlayer.audio.play = function () {
                            const playPromise = originalPlay.call(this);
                            if (playPromise !== undefined) {
                                playPromise.catch(error => {
                                    if (error.name === 'NotAllowedError') {
                                        console.warn('[Music UI] 浏览器拦截了自动播放:', error);
                                        // 回退 UI 按钮状态并给用户提示
                                        apBtn.textContent = '▶';
                                        showErrorToast('music.autoplayBlocked', '浏览器限制了自动播放，请点击播放按钮');
                                    }
                                });
                            }
                            return playPromise;
                        };
                    }
                }
            }

            const apElement = container.querySelector('.aplayer');
            if (apElement)
                apElement.style.display = 'none';

        } catch (err) {
            console.error('[Music UI] 播放器出错:', err);
            musicBar.remove();
            if (currentToken === latestMusicRequestToken) {
                currentPlayingTrack = null;
                showErrorToast('music.playError', '音乐播放加载失败');
            }
        }
    };

    // --- 6. 暴露全局接口 ---
    window.sendMusicMessage = function (trackInfo, shouldAutoPlay = true) {
        if (!trackInfo) return false;

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
            console.error('[Music UI] 库加载失败:', err);
            showErrorToast('music.loadError', '音乐播放器加载失败');
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

    window.dispatchEvent(new CustomEvent('music-ui-ready'));
    console.log('[Music UI] 接口已暴露，就绪信号已发送');

})();