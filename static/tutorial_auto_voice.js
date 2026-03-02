/**
 * N.E.K.O 新手引导自动语音模块
 *
 * 播放策略：
 * 1. 教程启动时：后台预加载所有步骤的 Edge TTS 音频
 * 2. 缓存命中：直接播放高质量 Edge TTS 音频（瞬时）
 * 3. 缓存未命中：实时请求 Edge TTS 并播放
 * 4. Edge TTS 不可用时：静默跳过（不使用浏览器机器人语音）
 *
 * 音频播放使用 Web Audio API (AudioContext) 而非 new Audio()，
 * 以绕过 Chromium 自动播放策略限制。AudioContext 只需一次 resume()
 * 即可解锁后续所有播放，且在 Electron 中通常无需用户手势。
 */

class TutorialAutoVoice {
    constructor() {
        // 播放状态
        this._currentSource = null;   // AudioBufferSourceNode
        this._currentGain = null;     // GainNode
        this.isSpeaking = false;
        this.isPaused = false;
        this.queue = [];
        this._currentText = '';
        this._speakId = 0;
        this._isPlaying = false;  // 标记是否有音频正在播放（用于立即中断）

        // 配置
        this.enabled = true;
        this.rate = 1.0;
        this.pitch = 1.0;
        this.volume = 1.0;

        // 事件回调
        this.onStart = null;
        this.onEnd = null;
        this.onError = null;

        // 语言
        this._lang = this._detectLanguage();

        // Edge TTS 音频缓存：cacheKey -> ArrayBuffer（解码前的原始数据）
        this._audioCache = new Map();
        this._cacheOrder = [];
        this._MAX_CACHE_SIZE = 50;

        // 预加载状态
        this._prefetching = false;

        // Web Audio API
        this._audioContext = null;
        this._unlocked = false;

        this._init();
    }

    _init() {
        // 监听语言变化
        window.addEventListener('localechange', () => {
            this._lang = this._detectLanguage();
        });

        console.log(`[TutorialVoice] 初始化完成 (语言: ${this._lang}, AudioContext: 延迟创建)`);
    }

    /**
     * 延迟创建 AudioContext（避免触发浏览器自动播放策略警告）
     * 只在首次需要时创建，通常在用户手势后
     */
    _ensureAudioContext() {
        if (this._audioContext) return this._audioContext;

        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;

        this._audioContext = new AC();
        console.log(`[TutorialVoice] AudioContext 已创建 (state: ${this._audioContext.state})`);

        // 设置解锁监听器
        this._tryUnlock();

        return this._audioContext;
    }

    /**
     * 尝试解锁 AudioContext（绕过自动播放策略）
     * 1. 立即尝试 resume()（在 Electron 中通常直接成功）
     * 2. 如果失败，监听用户首次交互再 resume
     */
    _tryUnlock() {
        if (!this._audioContext) return;

        const ctx = this._audioContext;

        if (ctx.state === 'running') {
            this._unlocked = true;
            return;
        }

        // 等待用户手势后再 resume，避免触发浏览器自动播放策略警告
        const unlock = () => {
            if (this._unlocked) return;
            ctx.resume().then(() => {
                if (ctx.state === 'running') {
                    this._unlocked = true;
                    document.removeEventListener('pointerdown', unlock, true);
                    document.removeEventListener('keydown', unlock, true);
                    document.removeEventListener('touchstart', unlock, true);
                    console.log('[TutorialVoice] AudioContext 已解锁（用户交互）');
                }
            }).catch(() => {});
        };

        document.addEventListener('pointerdown', unlock, true);
        document.addEventListener('keydown', unlock, true);
        document.addEventListener('touchstart', unlock, true);

        // 监听 AudioContext 状态变化
        ctx.addEventListener('statechange', () => {
            if (ctx.state === 'running' && !this._unlocked) {
                this._unlocked = true;
                document.removeEventListener('pointerdown', unlock, true);
                document.removeEventListener('keydown', unlock, true);
                document.removeEventListener('touchstart', unlock, true);
                console.log('[TutorialVoice] AudioContext 状态变为 running');
            }
        });
    }

    _detectLanguage() {
        if (window.i18next && window.i18next.language) return window.i18next.language;
        const stored = localStorage.getItem('i18nextLng');
        if (stored) return stored;
        return 'zh-CN';
    }

    // ==================== 公共 API ====================

    isAvailable() {
        const AC = window.AudioContext || window.webkitAudioContext;
        return !!AC;
    }
    checkSpeaking() { return this.isSpeaking; }

    /**
     * 播放文本（仅 Edge TTS）
     */
    speak(text, options = {}) {
        if (!this.enabled) return;

        // 延迟创建 AudioContext（首次调用时创建）
        const ctx = this._ensureAudioContext();
        if (!ctx) return;

        const cleanText = this._cleanText(text);
        if (!cleanText || cleanText.trim().length === 0) return;

        const lang = options.lang || this._lang;

        // 停止当前播放（会递增 _speakId）
        this._stopAll();

        this._currentText = cleanText;
        const currentId = this._speakId;  // 使用 _stopAll 后的 ID
        const cacheKey = this._generateCacheKey(cleanText, lang);

        // 缓存命中 → 直接播放
        if (this._audioCache.has(cacheKey)) {
            console.log('[TutorialVoice] 缓存命中，播放');
            this._playFromArrayBuffer(this._audioCache.get(cacheKey), currentId);
            return;
        }

        // 缓存未命中 → 实时请求 Edge TTS
        console.log('[TutorialVoice] 请求 Edge TTS...');
        this._fetchAndPlayEdgeTTS(cleanText, lang, cacheKey, currentId);
    }

    /**
     * 预加载一组教程步骤的音频
     */
    async prefetchSteps(steps, lang) {
        if (this._prefetching) return;
        this._prefetching = true;
        console.log(`[TutorialVoice] 开始预加载 ${steps.length} 个步骤音频...`);

        let loaded = 0;
        for (const step of steps) {
            const cleanText = this._cleanText(step.text);
            if (!cleanText || cleanText.trim().length === 0) continue;

            const cacheKey = this._generateCacheKey(cleanText, lang);
            if (this._audioCache.has(cacheKey)) {
                loaded++;
                continue;
            }

            try {
                const response = await fetch('/api/tutorial-tts/synthesize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: cleanText, lang })
                });
                if (!response.ok) continue;

                const arrayBuffer = await response.arrayBuffer();
                if (arrayBuffer.byteLength === 0) continue;

                this._addToCache(cacheKey, arrayBuffer);
                loaded++;
            } catch (e) {
                console.debug('[TutorialVoice] 预加载失败:', step.text?.slice(0, 30), e.message);
            }
        }

        this._prefetching = false;
        console.log(`[TutorialVoice] 预加载完成: ${loaded}/${steps.length}`);
    }

    enqueue(text, options = {}) {
        const cleanText = this._cleanText(text);
        if (!cleanText || cleanText.trim().length === 0) return;
        this.queue.push({ text: cleanText, options });
        if (!this.isSpeaking && this.queue.length === 1) this._playNext();
    }

    clearQueue() { this.queue = []; }

    stop() {
        this._stopAll();
        this.isSpeaking = false;
        this.isPaused = false;
    }

    pause() {
        if (!this.isSpeaking) return;
        const ctx = this._ensureAudioContext();
        if (!ctx) return;
        ctx.suspend().catch(() => {});
        this.isPaused = true;
    }

    resume() {
        if (!this.isPaused) return;
        const ctx = this._ensureAudioContext();
        if (!ctx) return;
        ctx.resume().catch(() => {});
        this.isPaused = false;
    }

    setEnabled(enabled) {
        this.enabled = enabled;
        if (!enabled) { this.stop(); this.clearQueue(); }
    }

    setRate(rate) { this.rate = Math.max(0.5, Math.min(2.0, rate)); }
    setPitch(pitch) { this.pitch = Math.max(0, Math.min(2, pitch)); }

    setVolume(volume) {
        this.volume = Math.max(0, Math.min(1, volume));
        if (this._currentGain) {
            this._currentGain.gain.value = this.volume;
        }
    }

    getStatus() {
        return {
            isAvailable: this.isAvailable(),
            isEnabled: this.enabled,
            isSpeaking: this.isSpeaking,
            isPaused: this.isPaused,
            queueLength: this.queue.length,
            language: this._lang,
            cacheSize: this._audioCache.size,
            audioContextState: this._audioContext ? this._audioContext.state : 'not created',
            rate: this.rate, pitch: this.pitch, volume: this.volume
        };
    }

    destroy() {
        this.stop();
        this.clearQueue();
        this._audioCache.clear();
        this._cacheOrder = [];
        this._currentSource = null;
        this._currentGain = null;
        if (this._audioContext) {
            this._audioContext.close().catch(() => {});
            this._audioContext = null;
        }
    }

    // ==================== 内部 ====================

    _stopAll() {
        this._speakId++;
        this._isPlaying = false;  // 立即标记为非播放状态，阻止所有异步操作继续

        if (this._currentSource) {
            try { this._currentSource.stop(); } catch (e) { /* already stopped */ }
            this._currentSource = null;
        }
        this._currentGain = null;
        this.isSpeaking = false;
    }

    async _fetchAndPlayEdgeTTS(text, lang, cacheKey, speakId) {
        try {
            const response = await fetch('/api/tutorial-tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, lang })
            });

            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const arrayBuffer = await response.arrayBuffer();
            if (arrayBuffer.byteLength === 0) throw new Error('Empty audio');

            this._addToCache(cacheKey, arrayBuffer);

            if (this._speakId !== speakId) return;

            this._playFromArrayBuffer(arrayBuffer, speakId);
        } catch (e) {
            console.warn('[TutorialVoice] Edge TTS 请求失败:', e.message);
            this.isSpeaking = false;
            this._playNext();
        }
    }

    /**
     * 通过 AudioContext 解码并播放 ArrayBuffer 音频数据
     */
    async _playFromArrayBuffer(arrayBuffer, speakId) {
        // 立即检查是否已被取消（在任何异步操作之前）
        if (this._speakId !== speakId) return;

        const ctx = this._ensureAudioContext();
        if (!ctx) return;

        // 标记开始播放
        this._isPlaying = true;

        try {
            // 确保 AudioContext 处于 running 状态
            if (ctx.state !== 'running') {
                await ctx.resume();
                // resume 后检查是否已被取消
                if (this._speakId !== speakId || !this._isPlaying) return;
            }

            // decodeAudioData 需要 ArrayBuffer 的副本（因为它会 detach 原始 buffer）
            const bufferCopy = arrayBuffer.slice(0);
            const audioBuffer = await ctx.decodeAudioData(bufferCopy);

            // 检查是否已被取消
            if (this._speakId !== speakId || !this._isPlaying) return;

            // 创建音频节点
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.playbackRate.value = this.rate;

            const gainNode = ctx.createGain();
            gainNode.gain.value = this.volume;

            source.connect(gainNode);
            gainNode.connect(ctx.destination);

            this._currentSource = source;
            this._currentGain = gainNode;

            // 状态管理
            this.isSpeaking = true;
            if (typeof this.onStart === 'function') this.onStart();

            source.onended = () => {
                // 只有当前播放的 source 结束时才更新状态
                if (this._currentSource === source) {
                    this.isSpeaking = false;
                    this.isPaused = false;
                    this._currentSource = null;
                    this._currentGain = null;
                    this._isPlaying = false;
                    if (typeof this.onEnd === 'function') this.onEnd();
                    this._playNext();
                }
            };

            source.start();
            console.log('[TutorialVoice] AudioContext 播放中...');
        } catch (e) {
            console.warn('[TutorialVoice] AudioContext 播放失败:', e.message);
            this.isSpeaking = false;
            this._isPlaying = false;
            this._currentSource = null;
            this._currentGain = null;
            this._playNext();
        }
    }

    _playNext() {
        if (this.queue.length === 0) return;
        const next = this.queue.shift();
        this.speak(next.text, next.options);
    }

    // ==================== 缓存管理 ====================

    _addToCache(key, arrayBuffer) {
        if (this._audioCache.has(key)) {
            this._cacheOrder = this._cacheOrder.filter(k => k !== key);
            this._cacheOrder.push(key);
            return;
        }
        while (this._cacheOrder.length >= this._MAX_CACHE_SIZE) {
            const oldKey = this._cacheOrder.shift();
            this._audioCache.delete(oldKey);
        }
        this._audioCache.set(key, arrayBuffer);
        this._cacheOrder.push(key);
    }

    _generateCacheKey(text, lang) {
        const str = lang + ':' + text;
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const ch = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + ch;
            hash |= 0;
        }
        return hash.toString(36);
    }

    // ==================== 文本清理 ====================

    _cleanText(text) {
        if (typeof text !== 'string') text = String(text);
        let cleaned = text;

        cleaned = cleaned.replace(/<[^>]*>/g, '');

        cleaned = cleaned.replace(/[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/g, (match) => {
            if (/^[，。！？、；：""''（）【】.!?,;:'"()\[\]\d\s\-]+$/.test(match)) return match;
            return ' ';
        });

        const nekoMap = { 'zh': 'neko', 'ja': 'neko', 'en': 'neko', 'ko': 'neko', 'ru': 'neko' };
        const langKey = this._lang.startsWith('zh') ? 'zh' :
                        this._lang.startsWith('ja') ? 'ja' :
                        this._lang.startsWith('ko') ? 'ko' :
                        this._lang.startsWith('ru') ? 'ru' : 'en';
        cleaned = cleaned.replace(/N\s*\.?\s*E\s*\.?\s*K\s*\.?\s*O\s*\.?/gi, nekoMap[langKey] || 'NEKO');

        cleaned = cleaned.replace(/\s+/g, ' ').trim();
        return cleaned;
    }
}

/**
 * 全局测试函数
 */
window.testTutorialVoice = async function() {
    console.log('=== Tutorial Voice 诊断 ===');
    const mgr = window.universalTutorialManager;
    const voice = mgr && mgr.tutorialVoice;
    console.log('1. 实例:', voice ? 'OK' : 'MISSING');
    if (voice) console.log('2. 状态:', JSON.stringify(voice.getStatus(), null, 2));

    try {
        const resp = await fetch('/api/tutorial-tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: '你好，欢迎使用新手引导', lang: 'zh-CN' })
        });
        if (resp.ok && voice && voice._audioContext) {
            const arrayBuffer = await resp.arrayBuffer();
            const ctx = voice._audioContext;
            if (ctx.state !== 'running') await ctx.resume();
            const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);
            source.start();
            console.log('3. Edge TTS + AudioContext: OK - 正在播放');
        } else {
            console.log('3. Edge TTS: FAIL (' + resp.status + ')');
        }
    } catch(e) {
        console.log('3. Edge TTS: ERROR -', e.message);
    }
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = TutorialAutoVoice;
}
