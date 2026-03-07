/**
 * APlayer 工具函数模块
 * 提取公共的翻译和格式化方法
 */

export function t(key, fallback) {
    if (typeof window === 'undefined') return fallback || key;

    // 优先使用 app.js 中定义的全局安全翻译函数（它完美处理了字符串兜底）
    if (typeof window.safeT === 'function') {
        return window.safeT(key, fallback);
    }
    
    // 作为最后一道防线：如果 safeT 还没就绪，按 i18next 的标准格式传入 defaultValue
    if (typeof window.t === 'function') {
        const res = window.t(key, { defaultValue: fallback });
        return typeof res === 'string' ? res : (fallback || key);
    }
    
    return fallback || key;
}

export function formatTime(seconds) {
    if (isNaN(seconds) || !isFinite(seconds)) return '00:00';
    
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}