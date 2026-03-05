/**
 * 负责更新播放器的 UI 状态，如当前播放曲目、播放状态等
 * 包含更新当前播放曲目、播放状态、时间显示等功能
 */

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
            trackNameEl.textContent = currentTrack.name || '未知曲目';
            trackArtistEl.textContent = currentTrack.artist || '未知艺术家';
        }

        const isPlaying = aplayer.playing;
        statusEl.textContent = isPlaying ? 'Playing' : 'Paused';
    } catch (e) {
        console.error('[APlayer] updateUI error:', e);
    }
}