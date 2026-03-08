// ========== APlayer 控制器模块 ==========
// 提供统一的 APlayer 控制接口

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

function ensureAPlayerInitialized(aplayer) {
    if (!aplayer) {
        console.warn('[APlayer] APlayer not initialized');
        return false;
    }
    return true;
}

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

export function playNextTrack(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        aplayer.skipForward();
        console.log('[APlayer] playNextTrack: switched to next track');

        const list = aplayer.list;
        const currentTrack = list && list.audios ? list.audios[list.index] : null;
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

export function playPreviousTrack(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) return { success: false, error: 'APlayer not initialized' };
        
        aplayer.skipBack();
        console.log('[APlayer] playPreviousTrack: switched to previous track');

        const list = aplayer.list;
        const currentTrack = list && list.audios ? list.audios[list.index] : null;
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

export function getCurrentTrackInfo(aplayer) {
    try {
        if (!ensureAPlayerInitialized(aplayer)) {
            return { success: false, error: 'APlayer not initialized' };
        }
        
        const list = aplayer.list;
        const currentTrack = list && list.audios ? list.audios[list.index] : null;
        
        if (currentTrack) {
            return {
                name: currentTrack.name,
                artist: currentTrack.artist,
                duration: aplayer.audio ? aplayer.audio.duration : 0,
                currentTime: aplayer.audio ? aplayer.audio.currentTime : 0,
                paused: !aplayer.playing,
                success: true,
                cover: currentTrack.cover
            };
        } else {
            return { success: false, error: 'No track in playlist' };
        }
    } catch (e) {
        console.error('[APlayer] getCurrentTrackInfo error:', e);
        return { success: false, error: e.message };
    }
}