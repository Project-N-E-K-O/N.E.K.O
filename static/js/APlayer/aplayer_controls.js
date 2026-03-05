// ========== APlayer 控制器模块 ==========
// 提供统一的 APlayer 控制接口

/**
 * 音乐源配置
 * 包含中国和国际两个区域的音乐源列表
 * 与后端 GLOBAL_CRAWLERS 保持一致
 */
const musicSources = {
    china: [
        { name: '网易云音乐', url: 'https://music.163.com', id: 'netease' },
        { name: 'FMA', url: 'https://freemusicarchive.org', id: 'fma' },
        { name: 'Musopen', url: 'https://musopen.org', id: 'musopen' },
        { name: 'SoundCloud', url: 'https://soundcloud.com', id: 'soundcloud' },
        { name: 'iTunes', url: 'https://music.apple.com', id: 'itunes' },
        { name: 'Bandcamp', url: 'https://bandcamp.com', id: 'bandcamp' }
    ],
    international: [
        { name: 'Musopen', url: 'https://musopen.org', id: 'musopen' },
        { name: 'FMA', url: 'https://freemusicarchive.org', id: 'fma' },
        { name: 'SoundCloud', url: 'https://soundcloud.com', id: 'soundcloud' },
        { name: 'iTunes', url: 'https://music.apple.com', id: 'itunes' },
        { name: 'Bandcamp', url: 'https://bandcamp.com', id: 'bandcamp' },
        { name: '网易云音乐', url: 'https://music.163.com', id: 'netease' }
    ]
};

/**
 * 校验 APlayer 实例是否存在
 * @param {APlayer} aplayer - APlayer 实例
 * @returns {boolean} - 如果 APlayer 存在则返回 true，否则返回 false
 */
function ensureAPlayerInitialized(aplayer) {
    if (!aplayer) {
        console.warn('[APlayer] APlayer not initialized');
        return false;
    }
    return true;
}

/**
 * 获取音乐源列表
 * @param {string} region - 用户区域（"china" 或 "international"）
 * @returns {Array} - 音乐源列表
 */
export function getMusicSources(region) {
    if (region === 'china') {
        return musicSources.china;
    } else if (region === 'international') {
        return musicSources.international;
    } else {
        console.error('[APlayer] Invalid region specified:', region);
        return [];
    }
}

/**
 * 播放/暂停音乐
 * @param {APlayer} aplayer - APlayer 实例
 * @returns {Object} - 操作结果
 */
export function toggleMusicPlayback(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        aplayer.toggle();
        const isPlaying = aplayer.playing;
        console.log('[APlayer] toggleMusicPlayback:', isPlaying ? 'playing' : 'paused');
        
        return { success: true, playing: isPlaying };
    } catch (e) {
        console.error('[APlayer] toggleMusicPlayback error:', e);
        return { success: false, error: e.message };
    }
}

/**
 * 播放下一首歌曲
 * @param {APlayer} aplayer - APlayer 实例
 * @returns {Object} - 操作结果
 */
export function playNextTrack(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        aplayer.skipForward();
        console.log('[APlayer] playNextTrack: switched to next track');

        const currentTrack = aplayer.list.audios[aplayer.list.index];
        if (currentTrack) {
            return {
                name: currentTrack.name,
                artist: currentTrack.artist,
                success: true
            };
        }
        return { success: false, error: 'No track information available' };
    } catch (e) {
        console.error('[APlayer] playNextTrack error:', e);
        return { success: false, error: e.message };
    }
}

/**
 * 播放上一首歌曲
 * @param {APlayer} aplayer - APlayer 实例
 * @returns {Object} - 操作结果
 */
export function playPreviousTrack(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        aplayer.skipBack();
        console.log('[APlayer] playPreviousTrack: switched to previous track');

        const currentTrack = aplayer.list.audios[aplayer.list.index];
        if (currentTrack) {
            return {
                name: currentTrack.name,
                artist: currentTrack.artist,
                success: true
            };
        }
        return { success: false, error: 'No track information available' };
    } catch (e) {
        console.error('[APlayer] playPreviousTrack error:', e);
        return { success: false, error: e.message };
    }
}

/**
 * 调节音乐音量
 * @param {APlayer} aplayer - APlayer 实例
 * @param {number} volume - 音量值 (0-1)
 * @returns {Object} - 操作结果
 */
export function setMusicVolume(aplayer, volume) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        const parsed = Number(volume);
        if (!Number.isFinite(parsed)) {
            return { success: false, error: 'Invalid volume' };
        }
        const normalizedVolume = Math.max(0, Math.min(1, parsed));
        aplayer.volume(normalizedVolume);
        console.log('[APlayer] setMusicVolume:', normalizedVolume);
        
        return { success: true, volume: normalizedVolume };
    } catch (e) {
        console.error('[APlayer] setMusicVolume error:', e);
        return { success: false, error: e.message };
    }
}

/**
 * 获取当前播放信息
 * @param {APlayer} aplayer - APlayer 实例
 * @returns {Object} - 播放信息
 */
export function getCurrentTrackInfo(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) {
            return { success: false, error: 'APlayer not initialized' };
        }
        
        const currentTrack = aplayer.list.audios[aplayer.list.index];
        if (currentTrack) {
            return {
                name: currentTrack.name,
                artist: currentTrack.artist,
                duration: aplayer.duration,
                currentTime: aplayer.currentTime,
                paused: !aplayer.playing,
                success: true
            };
        } else {
            return { success: false, error: 'No track in playlist' };
        }
    } catch (e) {
        console.error('[APlayer] getCurrentTrackInfo error:', e);
        return { success: false, error: e.message };
    }
}
