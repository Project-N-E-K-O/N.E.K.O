/**
 * N.E.K.O 新手引导自动语音模块
 *
 * 播放策略（解决浏览器自动播放限制）：
 * 1. 首次播放：使用 speechSynthesis 立即朗读（无需用户手势，与浏览器兼容）
 * 2. 后台预取：同时从后端 Edge TTS API 获取高质量音频并缓存
 * 3. 缓存命中：后续相同文本使用 Edge TTS 缓存播放（高质量语音）
 *
 * 这样保证了：
 * - 语音始终能播放（speechSynthesis 不受自动播放策略限制）
 * - 重复播放时自动升级为 Edge TTS 高质量语音
 */

class TutorialAutoVoice {
    constructor() {
        // 播放状态
        this.currentAudio = null;       // HTMLAudioElement（Edge TTS 模式）
        this.isSpeaking = false;
        this.isPaused = false;
        this.queue = [];
        this._currentText = '';

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

        // Edge TTS 音频缓存：cacheKey -> blobURL
        this._audioCache = new Map();
        this._cacheOrder = [];
        this._MAX_CACHE_SIZE = 50;

        // 浏览器 speechSynthesis
        this._synth = window.speechSynthesis || null;
        this._synthVoices = [];
        this._synthVoice = null;

        // Audio 是否已被用户手势解锁
        this._audioUnlocked = false;

        this._init();
    }

    _init() {
        // 加载浏览器语音列表
        if (this._synth) {
            this._loadSynthVoices();
            if (this._synth.onvoiceschanged !== undefined) {
                this._synth.onvoiceschanged = () => this._loadSynthVoices();
            }
        }

        // 监听用户手势以解锁 Audio 播放
        const unlockAudio = () => {
            this._audioUnlocked = true;
            document.removeEventListener('click', unlockAudio);
            document.removeEventListener('keydown', unlockAudio);
            console.log('[TutorialVoice] Audio 已通过用户手势解锁');
        };
        document.addEventListener('click', unlockAudio);
        document.addEventListener('keydown', unlockAudio);

        // 监听语言变化
        window.addEventListener('localechange', () => {
            this._lang = this._detectLanguage();
        });

        console.log(`[TutorialVoice] 初始化完成 (语言: ${this._lang})`);
    }

    _detectLanguage() {
        if (window.i18next && window.i18next.language) return window.i18next.language;
        const stored = localStorage.getItem('i18nextLng');
        if (stored) return stored;
        return 'zh-CN';
    }

    // ==================== 公共 API ====================

    isAvailable() { return true; }
    checkSpeaking() { return this.isSpeaking; }

    /**
     * 播放文本
     *
     * 策略：
     * - 缓存命中 + Audio 已解锁 → Edge TTS 高质量播放
     * - 否则 → speechSynthesis 立即播放 + 后台预取 Edge TTS
     */
    speak(text, options = {}) {
        if (!this.enabled) return;

        const cleanText = this._cleanText(text);
        if (!cleanText || cleanText.trim().length === 0) return;

        const lang = options.lang || this._lang;

        // 停止当前播放（不包括 synth.cancel，稍后统一处理）
        this._stopAudio();

        this._currentText = cleanText;
        const cacheKey = this._generateCacheKey(cleanText, lang);

        // 策略 1：Edge TTS 缓存命中 + Audio 已解锁 → 高质量播放
        if (this._audioCache.has(cacheKey) && this._audioUnlocked) {
            console.log('[TutorialVoice] Edge TTS 缓存命中，高质量播放');
            // 先停 synth（如果还在播放之前的内容）
            if (this._synth) this._synth.cancel();
            this._playAudioBlob(this._audioCache.get(cacheKey));
            return;
        }

        // 策略 2：speechSynthesis 立即播放（可靠，不受自动播放限制）
        console.log('[TutorialVoice] 使用 speechSynthesis 播放');
        this._synthSpeak(cleanText);

        // 后台预取 Edge TTS 音频（缓存供下次使用）
        if (!this._audioCache.has(cacheKey)) {
            this._prefetchEdgeTTS(cleanText, lang, cacheKey);
        }
    }

    enqueue(text, options = {}) {
        const cleanText = this._cleanText(text);
        if (!cleanText || cleanText.trim().length === 0) return;
        this.queue.push({ text: cleanText, options });
        if (!this.isSpeaking && this.queue.length === 1) this._playNext();
    }

    clearQueue() {
        this.queue = [];
    }

    stop() {
        this._stopAudio();
        if (this._synth) this._synth.cancel();
        this.isSpeaking = false;
        this.isPaused = false;
    }

    pause() {
        if (!this.isSpeaking) return;
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.isPaused = true;
        } else if (this._synth) {
            this._synth.pause();
            this.isPaused = true;
        }
    }

    resume() {
        if (!this.isPaused) return;
        if (this.currentAudio) {
            this.currentAudio.play().catch(() => {});
            this.isPaused = false;
        } else if (this._synth) {
            this._synth.resume();
            this.isPaused = false;
        }
    }

    setEnabled(enabled) {
        this.enabled = enabled;
        if (!enabled) { this.stop(); this.clearQueue(); }
    }

    setRate(rate) { this.rate = Math.max(0.5, Math.min(2.0, rate)); }
    setPitch(pitch) { this.pitch = Math.max(0, Math.min(2, pitch)); }

    setVolume(volume) {
        this.volume = Math.max(0, Math.min(1, volume));
        if (this.currentAudio) this.currentAudio.volume = this.volume;
    }

    getStatus() {
        return {
            isAvailable: true,
            isEnabled: this.enabled,
            isSpeaking: this.isSpeaking,
            isPaused: this.isPaused,
            queueLength: this.queue.length,
            audioUnlocked: this._audioUnlocked,
            language: this._lang,
            cacheSize: this._audioCache.size,
            rate: this.rate, pitch: this.pitch, volume: this.volume
        };
    }

    destroy() {
        this.stop();
        this.clearQueue();
        for (const blobUrl of this._audioCache.values()) URL.revokeObjectURL(blobUrl);
        this._audioCache.clear();
        this._cacheOrder = [];
        this.currentAudio = null;
    }

    // ==================== 内部：Audio 元素控制 ====================

    /** 停止 Audio 元素（不影响 speechSynthesis） */
    _stopAudio() {
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
        }
    }

    /** 使用 HTMLAudioElement 播放缓存的 Edge TTS 音频 */
    _playAudioBlob(blobUrl) {
        const audio = new Audio(blobUrl);
        audio.volume = this.volume;
        audio.playbackRate = this.rate;
        this.currentAudio = audio;

        audio.onplay = () => {
            this.isSpeaking = true;
            if (typeof this.onStart === 'function') this.onStart();
        };
        audio.onended = () => {
            this.isSpeaking = false;
            this.isPaused = false;
            this.currentAudio = null;
            if (typeof this.onEnd === 'function') this.onEnd();
            this._playNext();
        };
        audio.onerror = () => {
            this.isSpeaking = false;
            this.currentAudio = null;
            // Audio 播放失败，回退到 speechSynthesis
            this._synthSpeak(this._currentText);
        };

        audio.play().catch(() => {
            this.currentAudio = null;
            // 自动播放被阻止，回退到 speechSynthesis
            this._synthSpeak(this._currentText);
        });
    }

    _playNext() {
        if (this.queue.length === 0) return;
        const next = this.queue.shift();
        this.speak(next.text, next.options);
    }

    // ==================== 内部：speechSynthesis 播放 ====================

    _loadSynthVoices() {
        this._synthVoices = this._synth.getVoices() || [];
        this._selectSynthVoice();
    }

    _selectSynthVoice() {
        if (this._synthVoices.length === 0) { this._synthVoice = null; return; }

        const priorities = [
            'Microsoft Huihui Desktop', 'Microsoft Xiaoxiao Desktop',
            'Google 普通话', 'Huihui', 'Xiaoxiao'
        ];
        for (const name of priorities) {
            const v = this._synthVoices.find(v => v.name.includes(name));
            if (v) { this._synthVoice = v; return; }
        }

        const langPrefix = this._lang.startsWith('zh') ? 'zh' :
                           this._lang.startsWith('ja') ? 'ja' :
                           this._lang.startsWith('en') ? 'en' : '';
        if (langPrefix) {
            const v = this._synthVoices.find(v => v.lang && v.lang.toLowerCase().startsWith(langPrefix));
            if (v) { this._synthVoice = v; return; }
        }
        this._synthVoice = this._synthVoices[0];
    }

    /**
     * 使用 speechSynthesis 立即播放
     * 关键：cancel() 和 speak() 必须在同一个同步调用栈中，避免 Chrome bug
     */
    _synthSpeak(text) {
        if (!this._synth || !text) {
            this._playNext();
            return;
        }

        const utterance = new SpeechSynthesisUtterance(text);
        if (this._synthVoice) {
            utterance.voice = this._synthVoice;
        }
        utterance.rate = this.rate;
        utterance.pitch = this.pitch;
        utterance.volume = this.volume;
        if (this._synthVoice && this._synthVoice.lang) {
            utterance.lang = this._synthVoice.lang;
        } else {
            utterance.lang = this._lang || 'zh-CN';
        }

        utterance.onstart = () => {
            this.isSpeaking = true;
            if (typeof this.onStart === 'function') this.onStart();
        };
        utterance.onend = () => {
            this.isSpeaking = false;
            if (typeof this.onEnd === 'function') this.onEnd();
            this._playNext();
        };
        utterance.onerror = (e) => {
            console.warn('[TutorialVoice] speechSynthesis 错误:', e.error || e);
            this.isSpeaking = false;
            this._playNext();
        };

        // 关键：cancel + speak 同步调用，避免 Chrome speechSynthesis 延迟 bug
        this._synth.cancel();
        this._synth.speak(utterance);
    }

    // ==================== 内部：Edge TTS 后台预取 ====================

    /** 后台从后端获取 Edge TTS 音频并缓存（不播放） */
    async _prefetchEdgeTTS(text, lang, cacheKey) {
        try {
            const response = await fetch('/api/tutorial-tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, lang })
            });

            if (!response.ok) return;

            const blob = await response.blob();
            if (blob.size === 0) return;

            const blobUrl = URL.createObjectURL(blob);
            this._addToCache(cacheKey, blobUrl);
            console.log('[TutorialVoice] Edge TTS 已预取并缓存');
        } catch (e) {
            // 预取失败静默忽略，不影响当前播放
        }
    }

    // ==================== 缓存管理 ====================

    _addToCache(key, blobUrl) {
        if (this._audioCache.has(key)) {
            this._cacheOrder = this._cacheOrder.filter(k => k !== key);
            this._cacheOrder.push(key);
            return;
        }
        while (this._cacheOrder.length >= this._MAX_CACHE_SIZE) {
            const oldKey = this._cacheOrder.shift();
            const oldUrl = this._audioCache.get(oldKey);
            if (oldUrl) URL.revokeObjectURL(oldUrl);
            this._audioCache.delete(oldKey);
        }
        this._audioCache.set(key, blobUrl);
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

        // 移除 HTML 标签
        cleaned = cleaned.replace(/<[^>]*>/g, '');

        // 移除表情符号，保留常用标点和 CJK 字符
        cleaned = cleaned.replace(/[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/g, (match) => {
            if (/^[，。！？、；：""''（）【】.!?,;:'"()\[\]\d\s\-]+$/.test(match)) return match;
            return ' ';
        });

        // 语言感知的 N.E.K.O 替换
        const nekoMap = { 'zh': '恩艾科', 'ja': 'ネコ', 'en': 'NEKO', 'ko': '네코', 'ru': 'НЕКО' };
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
 * 全局测试函数 - 在浏览器控制台调用 testTutorialVoice() 进行诊断
 */
window.testTutorialVoice = async function() {
    console.log('=== Tutorial Voice 诊断测试 ===');
    const mgr = window.universalTutorialManager;
    const voice = mgr && mgr.tutorialVoice;
    console.log('1. TutorialAutoVoice 类:', typeof TutorialAutoVoice !== 'undefined' ? 'OK' : 'MISSING');
    console.log('2. tutorialVoice 实例:', voice ? 'OK' : 'MISSING');
    if (voice) console.log('3. 状态:', JSON.stringify(voice.getStatus(), null, 2));
    console.log('4. speechSynthesis:', window.speechSynthesis ? 'OK' : 'MISSING');

    // 直接测试 speechSynthesis
    if (window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance('测试语音系统');
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
        console.log('5. speechSynthesis 播放: 已触发（应该听到声音）');
    }

    // 测试后端 API
    try {
        const resp = await fetch('/api/tutorial-tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: '测试', lang: 'zh-CN' })
        });
        console.log('6. Edge TTS API:', resp.ok ? 'OK (' + resp.status + ')' : 'FAIL (' + resp.status + ')');
    } catch(e) {
        console.log('6. Edge TTS API: ERROR -', e.message);
    }
    console.log('=== 诊断完成 ===');
};

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = TutorialAutoVoice;
}
