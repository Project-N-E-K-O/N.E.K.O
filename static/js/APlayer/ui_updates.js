/**
 * 负责更新播放器的 UI 状态，如当前播放曲目、播放状态等
 */

import { t } from './utils.js';
import { getCurrentTrackInfo } from './aplayer_controls.js';

export function initializeAPlayerUI(aplayer, config = {}) {
    if (!aplayer) {
        console.warn('[APlayer] initializeAPlayerUI: aplayer is null');
        return;
    }
    
    const container = aplayer.container;
    if (!container) return;
    
    if (config.theme) {
        setPlayerTheme(aplayer, config.theme);
    }
    
    updateUI(aplayer);
    console.log('[APlayer] UI initialized');
}

export function showPlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    
    const wrapper = document.getElementById('aplayer-container');
    if (wrapper && wrapper.contains(aplayer.container)) {
        wrapper.style.display = 'block';
    } else {
        aplayer.container.style.display = 'block';
    }
}

export function hidePlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    
    const wrapper = document.getElementById('aplayer-container');
    if (wrapper && wrapper.contains(aplayer.container)) {
        wrapper.style.display = 'none';
    } else {
        aplayer.container.style.display = 'none';
    }
}

export function showMiniPlayer(aplayer) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.classList.add('aplayer-mini');
}

export function hideMiniPlayer() {
    const miniPlayers = document.querySelectorAll('.aplayer-mini');
    miniPlayers.forEach(player => {
        player.classList.remove('aplayer-mini');
    });
}

export function setPlayerTheme(aplayer, theme) {
    if (!aplayer || !aplayer.container) return;
    aplayer.container.classList.remove('aplayer-theme-dark', 'aplayer-theme-light');
    aplayer.container.classList.add(`aplayer-theme-${theme}`);
}

export function setPlayerPosition(aplayer, position) {
    if (!aplayer || !aplayer.container) return;
    const container = aplayer.container;
    container.classList.remove('aplayer-position-bottom-left', 'aplayer-position-bottom-right', 
                               'aplayer-position-top-left', 'aplayer-position-top-right');
    container.classList.add(`aplayer-position-${position}`);
}

export function updateUI(aplayer) {
    if (!aplayer) return;
    
    const trackNameEl = document.getElementById('aplayer-track-name');
    const trackArtistEl = document.getElementById('aplayer-track-artist');
    const statusEl = document.getElementById('aplayer-status');
    
    if (!trackNameEl || !trackArtistEl || !statusEl) {
        return;
    }
    
    try {
        const trackInfo = getCurrentTrackInfo(aplayer);
        if (trackInfo && trackInfo.success) {
            trackNameEl.textContent = trackInfo.name || t('music.unknownTrack', '未知曲目');
            trackArtistEl.textContent = trackInfo.artist || t('music.unknownArtist', '未知艺术家');
        } else {
            trackNameEl.textContent = t('music.unknownTrack', '未知曲目');
            trackArtistEl.textContent = t('music.unknownArtist', '未知艺术家');
        }

        const isPlaying = aplayer.playing;
        statusEl.textContent = isPlaying ? t('music.playing', 'Playing') : t('music.paused', 'Paused');
    } catch (e) {
        console.error('[APlayer] updateUI error:', e);
    }
}