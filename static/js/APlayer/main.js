// APlayer 主模块
// 整合所有APlayer功能的主入口

import { 
    toggleMusicPlayback, 
    playNextTrack, 
    playPreviousTrack, 
    setMusicVolume,
    getCurrentTrackInfo,
    getMusicSources 
} from './APlayer/aplayer_controls.js';

import { 
    initializeAPlayerUI, 
    showPlayer, 
    hidePlayer, 
    showMiniPlayer, 
    hideMiniPlayer,
    setPlayerTheme,
    setPlayerPosition
} from './APlayer/ui_updates.js';

import { 
    initEventListeners, 
    setupKeyboardShortcuts,
    formatTime 
} from './APlayer/event_listeners.js';

/**
 * APlayer 配置对象
 */
const APLAYER_CONFIG = {
    // 默认配置
    defaultVolume: 0.6,
    theme: '#44b7fe',
    position: 'bottom-right',
    
    // UI配置
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
    
    // 播放器配置
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
    
    // 播放列表配置
    defaultPlaylist: []
};

/**
 * 初始化APlayer
 * @param {Object} options - 配置选项
 * @returns {APlayer|null} APlayer实例
 */
export function initializeAPlayer(options = {}) {
    // 合并配置
    const config = {
        ...APLAYER_CONFIG,
        ...options,
        ui: { ...APLAYER_CONFIG.ui, ...options.ui },
        player: { ...APLAYER_CONFIG.player, ...options.player }
    };

    // 检查APlayer库是否已加载
    if (typeof APlayer === 'undefined') {
        console.warn('[APlayer] Library not found, attempting to load...');
        loadAPlayerLibrary(() => initializeAPlayer(options));
        return null;
    }

    // 检查是否已有播放器实例，避免重复创建
    if (window.aplayer) {
        console.log('[APlayer] Already initialized, updating configuration...');
        updateAPlayerConfig(window.aplayer, config);
        return window.aplayer;
    }

    // 使用自定义容器或创建新容器
    const playerContainer = options.container || createPlayerContainer(config);
    
    // 创建APlayer实例
    try {
        const ap = new APlayer({
            container: playerContainer,
            ...config.player,
            audio: config.defaultPlaylist
        });

        // 添加错误事件监听
        ap.on('error', (e) => {
            console.error('[APlayer] Error:', e);
        });

        // 将播放器实例存储到全局变量
        window.aplayer = ap;
        console.log('[APlayer] Initialized successfully');

        // 初始化UI
        initializeAPlayerUI(ap, config.ui);

        // 设置全局控制函数
        setupGlobalControls(ap);

        // 设置键盘快捷键
        setupKeyboardShortcuts(ap);

        // 初始化事件监听器
        initEventListeners(ap);

        // 返回播放器实例
        return ap;
    } catch (e) {
        console.error('[APlayer] Failed to create instance:', e);
        return null;
    }
}

/**
 * 更新APlayer配置
 * @param {APlayer} aplayer - APlayer实例
 * @param {Object} config - 新配置
 */
function updateAPlayerConfig(aplayer, config) {
    // 更新音量
    if (config.player.volume !== undefined) {
        aplayer.volume(config.player.volume);
    }
    
    // 更新循环模式
    if (config.player.loop !== undefined) {
        aplayer.options.loop = config.player.loop;
    }
    
    // 更新播放顺序
    if (config.player.order !== undefined) {
        aplayer.options.order = config.player.order;
    }
    
    // 更新UI配置
    if (config.ui) {
        initializeAPlayerUI(aplayer, config.ui);
    }
}

/**
 * 创建播放器容器
 * @param {Object} config - 配置选项
 * @returns {HTMLElement} 播放器容器元素
 */
function createPlayerContainer(config) {
    // 检查是否已有播放器容器
    let playerContainer = document.getElementById('aplayer-container');
    if (!playerContainer) {
        playerContainer = document.createElement('div');
        playerContainer.id = 'aplayer-container';
        playerContainer.className = 'aplayer-container';
        
        // 设置容器样式
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
        
        document.body.appendChild(playerContainer);
    }
    
    return playerContainer;
}

/**
 * 设置全局控制函数
 * @param {APlayer} aplayer - APlayer实例
 */
function setupGlobalControls(aplayer) {
    // 播放/暂停
    window.toggleMusicPlayback = function() {
        return toggleMusicPlayback(aplayer);
    };

    // 上一首
    window.playNextTrack = function() {
        return playNextTrack(aplayer);
    };

    // 下一首
    window.playPreviousTrack = function() {
        return playPreviousTrack(aplayer);
    };

    // 调节音量
    window.setMusicVolume = function(volume) {
        return setMusicVolume(aplayer, volume);
    };

    // 获取当前歌曲信息
    window.getCurrentTrackInfo = function() {
        return getCurrentTrackInfo(aplayer);
    };

    // 添加控制函数到全局作用域
    window.aplayerControls = {
        play: () => aplayer.play(),
        pause: () => aplayer.pause(),
        toggle: () => aplayer.toggle(),
        stop: () => aplayer.stop(),
        seek: (time) => aplayer.seek(time),
        setVolume: (vol) => aplayer.volume(vol),
        skipForward: () => aplayer.skipForward(),
        skipBack: () => aplayer.skipBack(),
        
        // 添加歌曲到播放列表
        addAudio: (audioObj) => {
            try {
                aplayer.list.add(audioObj);
                console.log(`[APlayer] Added ${audioObj.name} to playlist`);
            } catch (e) {
                console.error('[APlayer] addAudio error:', e);
            }
        },
        
        // 设置整个播放列表
        setPlaylist: (audioList) => {
            try {
                aplayer.list.clear();
                aplayer.list.add(audioList);
                console.log(`[APlayer] Set new playlist with ${audioList.length} songs`);
            } catch (e) {
                console.error('[APlayer] setPlaylist error:', e);
            }
        },
        
        // 获取当前播放信息
        getCurrentTrack: () => {
            try {
                return aplayer.list.audios[aplayer.list.index];
            } catch (e) {
                console.error('[APlayer] getCurrentTrack error:', e);
                return null;
            }
        },
        
        // 显示/隐藏播放器
        show: () => showPlayer(aplayer),
        hide: () => hidePlayer(aplayer),
        
        // 显示迷你播放器
        showMini: () => showMiniPlayer(aplayer),
        hideMini: () => hideMiniPlayer(),
        
        // 设置主题
        setTheme: (theme) => setPlayerTheme(aplayer, theme),
        
        // 设置位置
        setPosition: (position) => setPlayerPosition(aplayer, position),
        
        // 格式化时间
        formatTime: (seconds) => formatTime(seconds)
    };
}

/**
 * 动态加载APlayer库
 * @param {Function} callback - 加载完成后的回调函数
 */
function loadAPlayerLibrary(callback) {
    try {
        // 创建CSS链接
        const cssLink = document.createElement('link');
        cssLink.rel = 'stylesheet';
        cssLink.href = 'https://cdn.jsdelivr.net/npm/aplayer/dist/APlayer.min.css';
        cssLink.onerror = () => {
            console.error('[APlayer] Failed to load CSS');
        };
        document.head.appendChild(cssLink);
        
        // 创建JS脚本
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/aplayer/dist/APlayer.min.js';
        script.onload = () => {
            console.log('[APlayer] Library loaded successfully');
            if (callback && typeof callback === 'function') {
                callback();
            }
        };
        script.onerror = () => {
            console.error('[APlayer] Failed to load library');
        };
        document.head.appendChild(script);
    } catch (e) {
        console.error('[APlayer] Error while setting up library loading:', e);
    }
}

/**
 * 延迟初始化APlayer
 * @param {Object} options - 配置选项
 */
export function delayedInitializeAPlayer(options = {}) {
    // 延迟初始化，确保其他JavaScript代码已加载
    setTimeout(() => {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => initializeAPlayer(options));
        } else {
            initializeAPlayer(options);
        }
    }, 500);
}

/**
 * 获取音乐源列表
 * @param {string} region - 用户区域（"china" 或 "international"）
 * @returns {Array} - 音乐源列表
 */
export function getAPlayerMusicSources(region) {
    return getMusicSources(region);
}

// 自动初始化APlayer（如果页面加载完成）
if (document.readyState === 'complete' || document.readyState === 'interactive') {
    delayedInitializeAPlayer();
} else {
    window.addEventListener('load', () => {
        delayedInitializeAPlayer();
    });
}
