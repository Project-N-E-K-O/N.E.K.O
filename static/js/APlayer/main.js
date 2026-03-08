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
        cssLink.href = 'https://cdnjs.cloudflare.com/ajax/libs/aplayer/1.10.1/APlayer.min.css';
        cssLink.crossOrigin = 'anonymous';
        cssLink.integrity = 'sha512-CIwaY5DuGKTXq24u2Zf1kS//4E//rS42RruwB2O1kH1S5/5512w/aM7w8jQ2J0X7r5E1pMwJIf73H1mH1E6XmA==';
        document.head.appendChild(cssLink);
        
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/aplayer/1.10.1/APlayer.min.js';
        script.crossOrigin = 'anonymous';
        script.integrity = 'sha512-RWosNn0G8icq6F8yJUzFj4A8qH/I8KzU0KzQ0F2X8xY756S1E3yJmC0RkH66g7O2/5wM9B2GjK/RXYtA==';
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
    // 提取属于 player 配置的顶层参数，防止调用方扁平传参时被丢弃
    const extractedPlayerOptions = {};
    const playerKeys = ['mini', 'autoplay', 'theme', 'loop', 'order', 'preload', 'volume', 'mutex', 'listFolded', 'listMaxHeight', 'lrcType'];
    
    playerKeys.forEach(key => {
        if (options[key] !== undefined) {
            extractedPlayerOptions[key] = options[key];
        }
    });

    const config = {
        ...APLAYER_CONFIG,
        ...options,
        ui: { ...APLAYER_CONFIG.ui, ...options.ui },
        // 按照优先级合并：默认配置 < 传入的顶层参数 < 传入的 options.player 对象
        player: { 
            ...APLAYER_CONFIG.player, 
            ...extractedPlayerOptions,
            ...(options.player || {}) 
        },
        defaultPlaylist: options.audio !== undefined ? options.audio : APLAYER_CONFIG.defaultPlaylist
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

    let playerContainer = options.container;
    let mountPoint;

    if (playerContainer) {
        // 外部容器模式：如果内部没有 aplayer-core，说明缺乏我们的自定义 UI 结构，需主动补齐
        if (!playerContainer.querySelector('#aplayer-core')) {
            const generatedUI = createPlayerContainer(config);
            while (generatedUI.firstChild) {
                playerContainer.appendChild(generatedUI.firstChild);
            }
        }
        mountPoint = playerContainer.querySelector('#aplayer-core') || playerContainer;
    } else {
        // 默认模式
        playerContainer = createPlayerContainer(config);
        mountPoint = playerContainer.id === 'aplayer-container' && document.getElementById('aplayer-core') 
            ? document.getElementById('aplayer-core') 
            : playerContainer;
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
    
    let success = true;
    try {
        // 尝试正常暂停并销毁实例
        if (typeof window.aplayer.pause === 'function') {
            window.aplayer.pause();
        }
        if (typeof window.aplayer.destroy === 'function') {
            window.aplayer.destroy();
        }
        
        // 尝试清理 DOM
        const container = window.aplayer.container;
        if (container) {
            const wrapper = document.getElementById('aplayer-container');
            if (wrapper && (wrapper === container || wrapper.contains(container))) {
                // 如果是本模块自己创建的全局浮动窗，安全连根拔起
                if (wrapper.parentNode) {
                    wrapper.parentNode.removeChild(wrapper);
                }
            } else {
                // 【核心修复】如果是外部传入的宿主容器（如聊天气泡），仅清空内部渲染残留，绝不删除宿主本身
                container.innerHTML = '';
            }
        }
        console.log('[APlayer] Destroyed successfully');
    } catch (e) {
        console.error('[APlayer] Failed to destroy:', e);
        success = false;
    } finally {
        // 【核心修复】无论 try 中是否报错，finally 块都会确保执行彻底的清理
        
        // 1. 清理实例引用
        window.aplayer = null;
        if (window.aplayerInjected) {
            window.aplayerInjected.aplayer = null;
        }
        
        // 2. 确保移除键盘快捷键，防止报错分支漏调导致重复绑定
        if (typeof removeKeyboardShortcuts === 'function') {
            removeKeyboardShortcuts();
        }
        
        // 3. 清理 setupGlobalControls 挂载的所有全局闭包，杜绝“幽灵控制”
        delete window.toggleMusicPlayback;
        delete window.playNextTrack;
        delete window.playPreviousTrack;
        delete window.setMusicVolume;
        delete window.getCurrentTrackInfo;
        if (window.aplayerControls) {
            delete window.aplayerControls;
        }
    }
    
    return success;
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