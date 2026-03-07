/** * APlayer事件监听器模块
 * 负责处理所有APlayer相关的事件和用户交互
 */

import { t, formatTime } from './utils.js';
import { getCurrentTrackInfo } from './aplayer_controls.js';

export function initEventListeners(aplayer) {
    if (!aplayer) {
        console.error('[APlayer] Cannot initialize event listeners: APlayer instance not found');
        return;
    }

    aplayer.on('play', () => {
        console.log('[APlayer] Playback started');
        updatePlayButton(true);
        updatePlaybackStatus('playing');
        dispatchCustomEvent('aplayer-play', { playing: true });
    });

    aplayer.on('pause', () => {
        console.log('[APlayer] Playback paused');
        updatePlayButton(false);
        updatePlaybackStatus('paused');
        dispatchCustomEvent('aplayer-pause', { playing: false });
    });

    aplayer.on('ended', () => {
        console.log('[APlayer] Track ended');
        updatePlayButton(false);
        updatePlaybackStatus('ended');
        dispatchCustomEvent('aplayer-ended', { track: getCurrentTrackInfo(aplayer) });
    });

    aplayer.on('volumechange', () => {
        const volume = Math.round(aplayer.audio.volume * 100);
        updateVolumeDisplay(volume);
        dispatchCustomEvent('aplayer-volume-change', { volume });
    });

    aplayer.on('timeupdate', () => {
        updateProgressBar(aplayer);
        updateTimeDisplay(aplayer);
    });

    aplayer.on('error', (e) => {
        console.error('[APlayer] Error:', e);
        showNotification(t('music.playError', '播放出错'), 'error');
        dispatchCustomEvent('aplayer-error', { error: e });
    });

    aplayer.on('listshow', () => {
        updatePlaylistToggle(true);
    });

    aplayer.on('listhide', () => {
        updatePlaylistToggle(false);
    });

    aplayer.on('listswitch', (index) => {
        console.log('[APlayer] Switched to track index:', index);
        updateTrackInfo(aplayer);
        dispatchCustomEvent('aplayer-track-switch', { index, track: getCurrentTrackInfo(aplayer) });
    });

    updateTrackInfo(aplayer);
    updatePlayButton(aplayer.playing);
    updateVolumeDisplay(Math.round(aplayer.audio.volume * 100));
    updatePlaybackStatus(aplayer.playing ? 'playing' : 'paused');
}

function updatePlayButton(isPlaying) {
    const playBtn = document.getElementById('aplayer-play-btn');
    if (playBtn) {
        playBtn.innerHTML = isPlaying ? 
            '<i class="fas fa-pause"></i>' : 
            '<i class="fas fa-play"></i>';
        
        // 修正语义反转：播放中 title 显示“暂停”，暂停中 title 显示“播放”
        // 同时改用动作词 key（如 music.pause/play）并使用 safeT 确保安全
        playBtn.title = isPlaying ? 
            t('music.pause', '暂停') : 
            t('music.play', '播放');
    }
}

function updatePlaybackStatus(status) {
    const statusElement = document.getElementById('aplayer-status');
    if (statusElement) {
        const statusText = status === 'playing' ? t('music.playing', '播放中') :
                          status === 'paused' ? t('music.paused', '已暂停') :
                          status === 'ended' ? t('music.ended', '已结束') : status;
        statusElement.textContent = statusText;
        statusElement.className = `aplayer-status aplayer-status-${status}`;
    }
}

function updateVolumeDisplay(volume) {
    const volumeSlider = document.getElementById('aplayer-volume-slider');
    const volumeValue = document.getElementById('aplayer-volume-value');
    
    if (volumeSlider) {
        volumeSlider.value = volume;
    }
    
    if (volumeValue) {
        volumeValue.textContent = `${volume}%`;
    }
    
    const volumeIcon = document.getElementById('aplayer-volume-icon');
    if (volumeIcon) {
        if (volume === 0) {
            volumeIcon.className = 'fas fa-volume-mute';
        } else if (volume < 50) {
            volumeIcon.className = 'fas fa-volume-down';
        } else {
            volumeIcon.className = 'fas fa-volume-up';
        }
    }
}

function updateProgressBar(aplayer) {
    const progressBar = document.getElementById('aplayer-progress');
    const progressFill = document.getElementById('aplayer-progress-fill');
    
    if (progressBar && progressFill) {
        const currentTime = aplayer.audio.currentTime;
        const duration = aplayer.audio.duration;
        const percentage = duration > 0 ? (currentTime / duration) * 100 : 0;
        
        progressFill.style.width = `${percentage}%`;
        progressBar.setAttribute('aria-valuenow', percentage);
    }
}

function updateTimeDisplay(aplayer) {
    const currentTimeElement = document.getElementById('aplayer-current-time');
    const durationElement = document.getElementById('aplayer-duration');
    
    if (currentTimeElement) {
        currentTimeElement.textContent = formatTime(aplayer.audio.currentTime);
    }
    
    if (durationElement) {
        durationElement.textContent = formatTime(aplayer.audio.duration);
    }
}

function updateTrackInfo(aplayer) {
    const trackInfo = getCurrentTrackInfo(aplayer);
    if (!trackInfo || !trackInfo.success) return;
    
    const trackNameElement = document.getElementById('aplayer-track-name');
    const trackArtistElement = document.getElementById('aplayer-track-artist');
    const trackCoverElement = document.getElementById('aplayer-track-cover');
    
    if (trackNameElement) {
        trackNameElement.textContent = trackInfo.name || t('music.unknownTrack', '未知曲目');
    }
    
    if (trackArtistElement) {
        trackArtistElement.textContent = trackInfo.artist || t('music.unknownArtist', '未知艺术家');
    }
    
    if (trackCoverElement) {
        if (trackInfo.cover) {
            trackCoverElement.src = trackInfo.cover;
            trackCoverElement.alt = `${trackInfo.name} - ${trackInfo.artist}`;
            trackCoverElement.style.display = '';
            
            const coverContainer = trackCoverElement.parentElement;
            const fallbackIcon = coverContainer?.querySelector('.cover-fallback-icon');
            if (fallbackIcon) {
                fallbackIcon.style.display = 'none';
            }
        } else {
            trackCoverElement.src = '';
            trackCoverElement.alt = '';
            trackCoverElement.style.display = 'none';
            
            const coverContainer = trackCoverElement.parentElement;
            let fallbackIcon = coverContainer?.querySelector('.cover-fallback-icon');
            if (!fallbackIcon) {
                fallbackIcon = document.createElement('span');
                fallbackIcon.className = 'cover-fallback-icon';
                fallbackIcon.textContent = '🎵';
                fallbackIcon.style.cssText = `
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    font-size: 32px;
                    color: rgba(255,255,255,0.5);
                    pointer-events: none;
                `;
                coverContainer?.appendChild(fallbackIcon);
            }
            fallbackIcon.style.display = 'flex';
        }
    }
}

function updatePlaylistToggle(isShown) {
    const playlistBtn = document.getElementById('aplayer-playlist-btn');
    if (playlistBtn) {
        playlistBtn.classList.toggle('active', isShown);
        // 【核心修复】使用模块内导入的 t 函数替代直接调用 window.safeT，防止初始化顺序导致的报错
        playlistBtn.title = isShown ? 
            t('music.hidePlaylist', '隐藏播放列表') : 
            t('music.showPlaylist', '显示播放列表');
    }
}

function dispatchCustomEvent(eventName, detail) {
    const event = new CustomEvent(eventName, { detail });
    window.dispatchEvent(event);
}

function showNotification(message, type = 'info') {
    if (window.showNotification) {
        window.showNotification(message, type);
        return;
    }
    
    const notification = document.createElement('div');
    notification.className = `aplayer-notification aplayer-notification-${type}`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 3000);
}

let keyboardHandlerBound = false;
let keyboardHandler = null;

export function setupKeyboardShortcuts(aplayer) {
    if (keyboardHandlerBound) return;
    
    keyboardHandler = (e) => {
        // 增加 isComposing 判断，防止输入法打字触发快捷键
        // 【修复】排除所有可交互元素和富文本编辑区，防止全局空格键拦截正常的按钮点击/下拉框激活操作
        const targetTag = e.target.tagName;
        if (
            ['INPUT', 'TEXTAREA', 'BUTTON', 'SELECT', 'A'].includes(targetTag) || 
            e.target.isContentEditable || 
            e.isComposing
        ) {
            return;
        }
        
        switch (e.code) {
            case 'Space':
                e.preventDefault();
                aplayer.toggle();
                break;
            case 'ArrowRight':
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    aplayer.skipForward();
                }
                break;
            case 'ArrowLeft':
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    aplayer.skipBack();
                }
                break;
            case 'ArrowUp':
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    const currentVolume = aplayer.audio.volume;
                    aplayer.volume(Math.min(1, currentVolume + 0.1));
                }
                break;
            case 'ArrowDown':
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    const currentVolume = aplayer.audio.volume;
                    aplayer.volume(Math.max(0, currentVolume - 0.1));
                }
                break;
        }
    };
    
    document.addEventListener('keydown', keyboardHandler);
    keyboardHandlerBound = true;
}

export function removeKeyboardShortcuts() {
    if (keyboardHandler && keyboardHandlerBound) {
        document.removeEventListener('keydown', keyboardHandler);
        keyboardHandler = null;
        keyboardHandlerBound = false;
        console.log('[APlayer] Keyboard shortcuts removed');
    }
}