/**
 * 负责更新播放器的 UI 状态，如当前播放曲目、播放状态等
 * 包含更新当前播放曲目、播放状态、时间显示等功能
 */

/**
 * 获取 i18n 翻译文本
 * @param {string} key - 翻译键
 * @param {string} fallback - 回退文本
 * @returns {string} 翻译后的文本
 */
function t(key, fallback) {
    if (window.t && typeof window.t === 'function') {
        return window.t(key) || fallback;
    }
    return fallback;
}

/**
 * 初始化 APlayer UI
 * @param {APlayer} aplayer - APlayer 实例
 * @param {Object} config - UI 配置
 */
export function initializeAPlayerUI(aplayer, config = {}) {
    if (!aplayer) {
        console.warn('[APlayer] initializeAPlayerUI: aplayer is null');
        return;
    }
    
    const container = aplayer.container;
    if (!container) return;
    
    if (config.theme) {
        container.classList.add(`aplayer-theme-${config.theme}`);
    }
    
    updateUI(aplayer);
    console.log('[APlayer] UI initialized');
}

/**
 * 显示播放器
 * @param {APlayer} aplayer - APlayer 实例
 */
export function showPlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.style.display = 'block';
}

/**
 * 隐藏播放器
 * @param {APlayer} aplayer - APlayer 实例
 */
export function hidePlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.style.display = 'none';
}

/**
 * 显示迷你播放器
 * @param {APlayer} aplayer - APlayer 实例
 */
export function showMiniPlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.classList.add('aplayer-mini');
}

/**
 * 隐藏迷你播放器
 */
export function hideMiniPlayer() {
    const miniPlayers = document.querySelectorAll('.aplayer-mini');
    miniPlayers.forEach(player => player.classList.remove('aplayer-mini'));
}

/**
 * 设置播放器主题
 * @param {APlayer} aplayer - APlayer 实例
 * @param {string} theme - 主题名称 ('dark' 或 'light')
 */
export function setPlayerTheme(aplayer, theme) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.classList.remove('aplayer-theme-dark', 'aplayer-theme-light');
    aplayer.container.classList.add(`aplayer-theme-${theme}`);
}

/**
 * 设置播放器位置
 * @param {APlayer} aplayer - APlayer 实例
 * @param {string} position - 位置 ('bottom-left', 'bottom-right', 'top-left', 'top-right')
 */
export function setPlayerPosition(aplayer, position) {
    if (!aplayer || !aplayer.container) return;
    const container = aplayer.container;
    container.classList.remove('aplayer-position-bottom-left', 'aplayer-position-bottom-right', 
                               'aplayer-position-top-left', 'aplayer-position-top-right');
    container.classList.add(`aplayer-position-${position}`);
}

/**
 * 更新播放器的 UI 状态
 * @param {APlayer} aplayer - APlayer 实例
 */
export function updateUI(aplayer) {
    if (!aplayer) {
        console.warn('[APlayer] updateUI: aplayer is null or undefined');
        return;
    }
    
    const trackNameEl = document.getElementById('aplayer-track-name');
    const trackArtistEl = document.getElementById('aplayer-track-artist');
    const statusEl = document.getElementById('aplayer-status');
    
    if (!trackNameEl || !trackArtistEl || !statusEl) {
        console.warn('[APlayer] updateUI: DOM elements not found');
        return;
    }
    
    try {
        const currentTrack = aplayer.list && aplayer.list.audios && aplayer.list.audios[aplayer.list.index];
        if (currentTrack) {
            trackNameEl.textContent = currentTrack.name || t('music.unknownTrack', '未知曲目');
            trackArtistEl.textContent = currentTrack.artist || t('music.unknownArtist', '未知艺术家');
        }

        const isPlaying = aplayer.playing;
        statusEl.textContent = isPlaying ? t('music.playing', 'Playing') : t('music.paused', 'Paused');
    } catch (e) {
        console.error('[APlayer] updateUI error:', e);
    }
}