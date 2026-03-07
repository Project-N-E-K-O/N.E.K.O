/** * APlayer 主模块
 * 整合所有APlayer功能的主入口
 */
import { 
    toggleMusicPlayback, 
    playNextTrack, 
    playPreviousTrack, 
    setMusicVolume,
    getCurrentTrackInfo
} from './aplayer_controls.js';

import { 
    initializeAPlayerUI, 
    showPlayer, 
    hidePlayer, 
    showMiniPlayer, 
    hideMiniPlayer,
    setPlayerTheme,
    setPlayerPosition
} from './ui_updates.js';

import { 
    initEventListeners, 
    setupKeyboardShortcuts,
    removeKeyboardShortcuts
} from './event_listeners.js';

import { formatTime } from './utils.js';

const APLAYER_CONFIG = {
    defaultVolume: 0.6,
    theme: '#44b7fe',
    position: 'bottom-right',
    ui: {
        theme: 'dark',
        showPlaylist: false,
        showVolume: true,
        showProgress: true,
        showTime: true,
        showCover: true,
        autoHide: false,
        position: 'bottom-right'
    },
    player: {
        mini: false,
        autoplay: false,
        loop: 'none',
        order: 'random',
        preload: 'metadata',
        volume: 0.6,
        mutex: true,
        listFolded: true,
        listMaxHeight: 200,
        lrcType: 0
    },
    defaultPlaylist: []
};

// 使用单例 Promise 避免多次调用引起重复加载
let aplayerLoadPromise = null;

function loadAPlayerLibrary() {
    if (typeof APlayer !== 'undefined') {
        return Promise.resolve();
    }
    
    if (aplayerLoadPromise) {
        return aplayerLoadPromise;
    }

    aplayerLoadPromise = new Promise((resolve, reject) => {
        const cssLink = document.createElement('link');
        cssLink.rel = 'stylesheet';
        // 【修改】锁定稳定版本号 1.10.1
        cssLink.href = 'https://cdn.jsdelivr.net/npm/aplayer@1.10.1/dist/APlayer.min.css';
        document.head.appendChild(cssLink);
        
        const script = document.createElement('script');
        // 【修改】锁定稳定版本号 1.10.1 并添加跨域安全配置
        script.src = 'https://cdn.jsdelivr.net/npm/aplayer@1.10.1/dist/APlayer.min.js';
        script.crossOrigin = 'anonymous';
        script.onload = () => {
            console.log('[APlayer] Library loaded successfully');
            resolve();
        };
        script.onerror = (e) => {
            console.error('[APlayer] Failed to load library');
            aplayerLoadPromise = null; // 失败后清空，允许重试
            reject(e);
        };
        document.head.appendChild(script);
    });

    return aplayerLoadPromise;
}

export async function initializeAPlayer(options = {}, onReady = null) {
    const config = {
        ...APLAYER_CONFIG,
        ...options,
        ui: { ...APLAYER_CONFIG.ui, ...options.ui },
        player: { ...APLAYER_CONFIG.player, ...options.player },
        defaultPlaylist: options.audio || APLAYER_CONFIG.defaultPlaylist
    };

    try {
        await loadAPlayerLibrary();
    } catch (e) {
        console.error('[APlayer] Cannot initialize, library load failed.', e);
        return null;
    }

    if (window.aplayer) {
        const existingContainer = window.aplayer.container;
        const newContainer = options.container || document.getElementById('aplayer-core');
        
        if (newContainer && newContainer !== existingContainer) {
            console.log('[APlayer] Container changed, recreating player...');
            destroyAPlayer();
        } else {
            console.log('[APlayer] Already initialized, updating configuration...');
            updateAPlayerConfig(window.aplayer, config);
            if (onReady) onReady(window.aplayer);
            return window.aplayer;
        }
    }

    const playerContainer = options.container || createPlayerContainer(config);
    let mountPoint = playerContainer;
    
    if (playerContainer.id === 'aplayer-container' && document.getElementById('aplayer-core')) {
        mountPoint = document.getElementById('aplayer-core');
    }

    try {
        const ap = new APlayer({
            container: mountPoint,
            ...config.player,
            audio: config.defaultPlaylist
        });

        ap.on('error', (e) => {
            console.error('[APlayer] Error:', e);
        });

        window.aplayer = ap;
        console.log('[APlayer] Initialized successfully');

        initializeAPlayerUI(ap, config.ui);
        setupGlobalControls(ap);
        setupKeyboardShortcuts(ap);
        initEventListeners(ap);

        if (onReady) onReady(ap);
        return ap;
    } catch (e) {
        console.error('[APlayer] Failed to create instance:', e);
        return null;
    }
}

export function destroyAPlayer() {
    if (!window.aplayer) return true;
    
    try {
        if (typeof window.aplayer.pause === 'function') {
            window.aplayer.pause();
        }
        if (typeof window.aplayer.destroy === 'function') {
            window.aplayer.destroy();
        }
        
        const container = window.aplayer.container;
        if (container && container.parentNode) {
            const wrapper = document.getElementById('aplayer-container');
            if (wrapper && wrapper.contains(container)) {
                wrapper.parentNode.removeChild(wrapper);
            } else {
                container.parentNode.removeChild(container);
            }
        }
        
       window.aplayer = null;
        // 【新增】同步清理镜像引用
        if (window.aplayerInjected) {
            window.aplayerInjected.aplayer = null;
        }
        removeKeyboardShortcuts();

        console.log('[APlayer] Destroyed successfully');
        return true;
    } catch (e) {
        console.error('[APlayer] Failed to destroy:', e);
        window.aplayer = null;
        // 【新增】失败分支也同步清理镜像引用
        if (window.aplayerInjected) {
            window.aplayerInjected.aplayer = null;
        }
        return false;
    }
}

function updateAPlayerConfig(aplayer, config) {
    if (config.player.volume !== undefined) {
        aplayer.volume(config.player.volume);
    }
    if (config.player.loop !== undefined) {
        aplayer.options.loop = config.player.loop;
    }
    if (config.player.order !== undefined) {
        aplayer.options.order = config.player.order;
    }
    if (config.ui) {
        initializeAPlayerUI(aplayer, config.ui);
    }
    if (config.defaultPlaylist && config.defaultPlaylist.length > 0) {
        aplayer.list.clear(); // 清空旧歌单
        aplayer.list.add(config.defaultPlaylist); // 注入新歌单
    }
}

function createPlayerContainer(config) {
    let playerContainer = document.getElementById('aplayer-container');
    if (!playerContainer) {
        playerContainer = document.createElement('div');
        playerContainer.id = 'aplayer-container';
        playerContainer.className = 'aplayer-container';
        
        playerContainer.style.cssText = `
            position: fixed;
            bottom: 100px;
            right: 20px;
            width: 300px;
            z-index: 9999;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            border-radius: 10px;
            background: white;
        `;
        
        const aplayerCore = document.createElement('div');
        aplayerCore.id = 'aplayer-core';
        playerContainer.appendChild(aplayerCore);

        const customUIWrapper = document.createElement('div');
        customUIWrapper.id = 'aplayer-custom-ui';
        
        const trackNameEl = document.createElement('div');
        trackNameEl.id = 'aplayer-track-name';
        trackNameEl.style.display = 'none';
        customUIWrapper.appendChild(trackNameEl);
        
        const trackArtistEl = document.createElement('div');
        trackArtistEl.id = 'aplayer-track-artist';
        trackArtistEl.style.display = 'none';
        customUIWrapper.appendChild(trackArtistEl);
        
        const statusEl = document.createElement('div');
        statusEl.id = 'aplayer-status';
        statusEl.style.display = 'none';
        customUIWrapper.appendChild(statusEl);
        
        const coverWrapper = document.createElement('div');
        coverWrapper.id = 'aplayer-cover-wrapper';
        coverWrapper.style.display = 'none';
        
        const trackCoverEl = document.createElement('img');
        trackCoverEl.id = 'aplayer-track-cover';
        trackCoverEl.alt = '';
        coverWrapper.appendChild(trackCoverEl);
        customUIWrapper.appendChild(coverWrapper);
        
        playerContainer.appendChild(customUIWrapper);
        document.body.appendChild(playerContainer);
    }
    
    return playerContainer;
}

function setupGlobalControls(aplayer) {
    window.toggleMusicPlayback = () => toggleMusicPlayback(aplayer);
    window.playNextTrack = () => playNextTrack(aplayer);
    window.playPreviousTrack = () => playPreviousTrack(aplayer);
    window.setMusicVolume = (volume) => setMusicVolume(aplayer, volume);
    window.getCurrentTrackInfo = () => getCurrentTrackInfo(aplayer);

    window.aplayerControls = {
        play: () => aplayer.play(),
        pause: () => aplayer.pause(),
        toggle: () => aplayer.toggle(),
        stop: () => {               // ✅ 组合使用暂停和归零来模拟停止
            aplayer.pause();
            aplayer.seek(0);
        },
        seek: (time) => aplayer.seek(time),
        setVolume: (vol) => aplayer.volume(vol),
        skipForward: () => aplayer.skipForward(),
        skipBack: () => aplayer.skipBack(),
        addAudio: (audioObj) => {
            try {
                aplayer.list.add(audioObj);
                console.log(`[APlayer] Added ${audioObj.name} to playlist`);
            } catch (e) {
                console.error('[APlayer] addAudio error:', e);
            }
        },
        setPlaylist: (audioList) => {
            try {
                aplayer.list.clear();
                aplayer.list.add(audioList);
                console.log(`[APlayer] Set new playlist with ${audioList.length} songs`);
            } catch (e) {
                console.error('[APlayer] setPlaylist error:', e);
            }
        },
        getCurrentTrack: () => {
            const list = aplayer.list;
            return list && list.audios ? list.audios[list.index] : null;
        },
        show: () => showPlayer(aplayer),
        hide: () => hidePlayer(aplayer),
        showMini: () => showMiniPlayer(aplayer),
        hideMini: () => hideMiniPlayer(),
        setTheme: (theme) => setPlayerTheme(aplayer, theme),
        setPosition: (position) => setPlayerPosition(aplayer, position),
        formatTime: (seconds) => formatTime(seconds)
    };
}
window.initializeAPlayer = initializeAPlayer;