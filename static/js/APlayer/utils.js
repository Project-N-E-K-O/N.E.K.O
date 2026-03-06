/**
 * APlayer 工具函数模块
 * 提取公共的翻译和格式化方法
 */

export function t(key, fallback) {
    if (window.t && typeof window.t === 'function') {
        const translated = window.t(key);
        return translated && translated !== key ? translated : fallback;
    }
    return fallback;
}

export function formatTime(seconds) {
    if (isNaN(seconds) || !isFinite(seconds)) return '00:00';
    
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}