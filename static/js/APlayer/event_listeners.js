/** 
 * APlayer事件监听器模块
 * 负责处理所有APlayer相关的事件和用户交互
 * 包含播放状态变化、音量变化、时间更新、错误处理等事件的监听
 */

function t(key, fallback) {
    if (window.t && typeof window.t === 'function') {
        return window.t(key) || fallback;
    }
    return fallback;
}

/**
 * 初始化APlayer事件监听器
 * @param {APlayer} aplayer - APlayer实例
 */
export function initEventListeners(aplayer) {
    if (!aplayer) {
        console.error('[APlayer] Cannot initialize event listeners: APlayer instance not found');
        return;
    }

    // 播放状态变化事件
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

    // 音量变化事件
    aplayer.on('volumechange', () => {
        const volume = Math.round(aplayer.audio.volume * 100);
        updateVolumeDisplay(volume);
        dispatchCustomEvent('aplayer-volume-change', { volume });
    });

    // 时间更新事件
    aplayer.on('timeupdate', () => {
        updateProgressBar(aplayer);
        updateTimeDisplay(aplayer);
    });

    // 错误处理事件
    aplayer.on('error', (e) => {
        console.error('[APlayer] Error:', e);
        showNotification(t('music.playError', '播放出错'), 'error');
        dispatchCustomEvent('aplayer-error', { error: e });
    });

    // 列表变化事件
    aplayer.on('listshow', () => {
        console.log('[APlayer] Playlist shown');
        updatePlaylistToggle(true);
    });

    aplayer.on('listhide', () => {
        console.log('[APlayer] Playlist hidden');
        updatePlaylistToggle(false);
    });

    // 切换歌曲事件
    aplayer.on('listswitch', (index) => {
        console.log('[APlayer] Switched to track index:', index);
        updateTrackInfo(aplayer);
        dispatchCustomEvent('aplayer-track-switch', { index, track: getCurrentTrackInfo(aplayer) });
    });

    // 初始化UI状态
    updateTrackInfo(aplayer);
    updatePlayButton(aplayer.playing);
    updateVolumeDisplay(Math.round(aplayer.audio.volume * 100));
    updatePlaybackStatus(aplayer.playing ? 'playing' : 'paused');
}

/**
 * 更新播放按钮状态
 * @param {boolean} isPlaying - 是否正在播放
 */
function updatePlayButton(isPlaying) {
    const playBtn = document.getElementById('aplayer-play-btn');
    if (playBtn) {
        playBtn.innerHTML = isPlaying ? 
            '<i class="fas fa-pause"></i>' : 
            '<i class="fas fa-play"></i>';
        playBtn.title = isPlaying ? t('music.paused', '暂停') : t('music.playing', '播放');
    }
}

/**
 * 更新播放状态显示
 * @param {string} status - 播放状态
 */
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

/**
 * 更新音量显示
 * @param {number} volume - 音量值 (0-100)
 */
function updateVolumeDisplay(volume) {
    const volumeSlider = document.getElementById('aplayer-volume-slider');
    const volumeValue = document.getElementById('aplayer-volume-value');
    
    if (volumeSlider) {
        volumeSlider.value = volume;
    }
    
    if (volumeValue) {
        volumeValue.textContent = `${volume}%`;
    }
    
    // 更新音量图标
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

/**
 * 更新进度条
 * @param {APlayer} aplayer - APlayer实例
 */
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

/**
 * 更新时间显示
 * @param {APlayer} aplayer - APlayer实例
 */
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

/**
 * 更新歌曲信息
 * @param {APlayer} aplayer - APlayer实例
 */
function updateTrackInfo(aplayer) {
    const currentTrack = getCurrentTrackInfo(aplayer);
    if (!currentTrack) return;
    
    const trackNameElement = document.getElementById('aplayer-track-name');
    const trackArtistElement = document.getElementById('aplayer-track-artist');
    const trackCoverElement = document.getElementById('aplayer-track-cover');
    
    if (trackNameElement) {
        trackNameElement.textContent = currentTrack.name || t('music.unknownTrack', '未知曲目');
    }
    
    if (trackArtistElement) {
        trackArtistElement.textContent = currentTrack.artist || t('music.unknownArtist', '未知艺术家');
    }
    
    if (trackCoverElement) {
        if (currentTrack.cover) {
            trackCoverElement.src = currentTrack.cover;
            trackCoverElement.alt = `${currentTrack.name} - ${currentTrack.artist}`;
            trackCoverElement.style.display = '';
            
            // 隐藏后备图标
            const coverContainer = trackCoverElement.parentElement;
            const fallbackIcon = coverContainer?.querySelector('.cover-fallback-icon');
            if (fallbackIcon) {
                fallbackIcon.style.display = 'none';
            }
        } else {
            trackCoverElement.src = '';
            trackCoverElement.alt = '';
            trackCoverElement.style.display = 'none';
            
            // 无封面时显示后备图标
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

/**
 * 更新播放列表切换按钮
 * @param {boolean} isShown - 播放列表是否显示
 */
function updatePlaylistToggle(isShown) {
    const playlistBtn = document.getElementById('aplayer-playlist-btn');
    if (playlistBtn) {
        playlistBtn.classList.toggle('active', isShown);
        playlistBtn.title = isShown ? '隐藏播放列表' : '显示播放列表';
    }
}

/**
 * 获取当前歌曲信息
 * @param {APlayer} aplayer - APlayer实例
 * @returns {Object|null} 歌曲信息对象
 */
function getCurrentTrackInfo(aplayer) {
    try {
        return aplayer.list.audios[aplayer.list.index];
    } catch (e) {
        console.error('[APlayer] Error getting current track info:', e);
        return null;
    }
}

/**
 * 格式化时间
 * @param {number} seconds - 秒数
 * @returns {string} 格式化的时间字符串 (MM:SS)
 */
export function formatTime(seconds) {
    if (isNaN(seconds) || !isFinite(seconds)) return '00:00';
    
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

/**
 * 分发自定义事件
 * @param {string} eventName - 事件名称
 * @param {Object} detail - 事件详情
 */
function dispatchCustomEvent(eventName, detail) {
    const event = new CustomEvent(eventName, { detail });
    window.dispatchEvent(event);
}

/**
 * 显示通知
 * @param {string} message - 通知消息
 * @param {string} type - 通知类型 ('info', 'success', 'warning', 'error')
 */
function showNotification(message, type = 'info') {
    // 如果项目中已有通知系统，使用项目通知
    if (window.showNotification) {
        window.showNotification(message, type);
        return;
    }
    
    // 否则创建简单的通知
    const notification = document.createElement('div');
    notification.className = `aplayer-notification aplayer-notification-${type}`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    // 3秒后自动移除
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 3000);
}

/**
 * 设置键盘快捷键
 * @param {APlayer} aplayer - APlayer实例
 */
let keyboardHandlerBound = false;

export function setupKeyboardShortcuts(aplayer) {
    if (keyboardHandlerBound) {
        console.log('[APlayer] Keyboard shortcuts already bound, skipping');
        return;
    }
    
    const keyboardHandler = (e) => {
        // 只在非输入状态下响应快捷键
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        
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
    keyboardHandlerBound = false;
}
