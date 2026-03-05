/**
 * 负责更新播放器的 UI 状态，如当前播放曲目、播放状态等
 * 包含更新当前播放曲目、播放状态、时间显示等功能
 */

/**
 * 更新播放器的 UI 状态
 * @param {APlayer} aplayer - APlayer 实例
 */
export function updateUI(aplayer) {
    const currentTrack = aplayer.list.audios[aplayer.list.index];
    if (currentTrack) {
        document.getElementById('track-name').textContent = currentTrack.name;
        document.getElementById('track-artist').textContent = currentTrack.artist;
    }

    const isPlaying = aplayer.playing;
    document.getElementById('playback-status').textContent = isPlaying ? 'Playing' : 'Paused';
}