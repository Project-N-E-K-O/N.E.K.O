/**
 * N.E.K.O 新手引导自动语音模块
 *
 * 播放策略：
 * 1. 教程启动时：后台预加载所有步骤的 Edge TTS 音频
 * 2. 缓存命中：直接播放高质量 Edge TTS 音频（瞬时）
 * 3. 缓存未命中：实时请求 Edge TTS 并播放
 * 4. Edge TTS 失败时：回退到浏览器 speechSynthesis
 */

class TutorialAutoVoice {
    constructor() {
        // 播放状态
        this.currentAudio = null;
        this.isSpeaking = false;
        this.isPaused = false;
        this.queue = [];
        this._currentText = '';
        this._speakId = 0;          // 用于取消过期的异步播放

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

        // 预加载状态
        this._prefetching = false;

        // 浏览器 speechSynthesis（仅作为回退）
        this._synth = window.speechSynthesis || null;
        this._synthVoices = [];
        this._synthVoice = null;

        // Audio 是否已被用户手势解锁
        this._audioUnlocked = false;

        this._init();
    }

    _init() {
        // 加载浏览器语音列表（回退用）
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
     * 播放文本（Edge TTS 优先，speechSynthesis 回退）
     */
    speak(text, options = {}) {
        if (!this.enabled) return;

        const cleanText = this._cleanText(text);
        if (!cleanText || cleanText.trim().length === 0) return;

        const lang = options.lang || this._lang;

        // 停止当前播放
        this._stopAll();

        this._currentText = cleanText;
        this._speakId++;
        const currentId = this._speakId;
        const cacheKey = this._generateCacheKey(cleanText, lang);

        // 策略 1：Edge TTS 缓存命中 → 直接高质量播放
        if (this._audioCache.has(cacheKey)) {
            console.log('[TutorialVoice] Edge TTS 缓存命中，高质量播放');
            this._playAudioBlob(this._audioCache.get(cacheKey));
            return;
        }

        // 策略 2：实时请求 Edge TTS 并播放
        console.log('[TutorialVoice] 请求 Edge TTS 合成...');
        this._fetchAndPlayEdgeTTS(cleanText, lang, cacheKey, currentId);
    }

    /**
     * 预加载一组教程步骤的音频
     * @param {Array<{text: string}>} steps - 步骤文本数组
     * @param {string} lang - 语言
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

                const blob = await response.blob();
                if (blob.size === 0) continue;

                const blobUrl = URL.createObjectURL(blob);
                this._addToCache(cacheKey, blobUrl);
                loaded++;
            } catch (e) {
                // 预加载失败，静默跳过
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

    clearQueue() {
        this.queue = [];
    }

    stop() {
        this._stopAll();
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

    // ==================== 内部：停止所有播放 ====================

    _stopAll() {
        this._speakId++;
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
        }
        if (this._synth) this._synth.cancel();
    }

    // ==================== 内部：Edge TTS 实时请求播放 ====================

    async _fetchAndPlayEdgeTTS(text, lang, cacheKey, speakId) {
        try {
            const response = await fetch('/api/tutorial-tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, lang })
            });

            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const blob = await response.blob();
            if (blob.size === 0) throw new Error('Empty audio');

            const blobUrl = URL.createObjectURL(blob);
            this._addToCache(cacheKey, blobUrl);

            // 检查是否仍然需要播放（用户可能已切换到下一步）
            if (this._speakId !== speakId) {
                console.log('[TutorialVoice] 播放请求已过期，跳过');
                return;
            }

            console.log('[TutorialVoice] Edge TTS 合成完成，播放');
            this._playAudioBlob(blobUrl);
        } catch (e) {
            console.warn('[TutorialVoice] Edge TTS 失败，回退到 speechSynthesis:', e.message);
            if (this._speakId === speakId) {
                this._synthSpeak(text);
            }
        }
    }

    // ==================== 内部：Audio 元素播放 ====================

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
            console.warn('[TutorialVoice] Audio 播放失败，回退到 speechSynthesis');
            this.isSpeaking = false;
            this.currentAudio = null;
            this._synthSpeak(this._currentText);
        };

        audio.play().catch(() => {
            this.currentAudio = null;
            console.warn('[TutorialVoice] Audio.play() 被阻止，回退到 speechSynthesis');
            this._synthSpeak(this._currentText);
        });
    }

    _playNext() {
        if (this.queue.length === 0) return;
        const next = this.queue.shift();
        this.speak(next.text, next.options);
    }

    // ==================== 内部：speechSynthesis 回退 ====================

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

    _synthSpeak(text) {
        if (!this._synth || !text) {
            this._playNext();
            return;
        }

        const utterance = new SpeechSynthesisUtterance(text);
        if (this._synthVoice) utterance.voice = this._synthVoice;
        utterance.rate = this.rate;
        utterance.pitch = this.pitch;
        utterance.volume = this.volume;
        utterance.lang = (this._synthVoice && this._synthVoice.lang) || this._lang || 'zh-CN';

        utterance.onstart = () => {
            this.isSpeaking = true;
            if (typeof this.onStart === 'function') this.onStart();
        };
        utterance.onend = () => {
            this.isSpeaking = false;
            if (typeof this.onEnd === 'function') this.onEnd();
            this._playNext();
        };
        utterance.onerror = () => {
            this.isSpeaking = false;
            this._playNext();
        };

        this._synth.cancel();
        this._synth.speak(utterance);
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

        cleaned = cleaned.replace(/<[^>]*>/g, '');

        cleaned = cleaned.replace(/[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/g, (match) => {
            if (/^[，。！？、；：""''（）【】.!?,;:'"()\[\]\d\s\-]+$/.test(match)) return match;
            return ' ';
        });

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

    // 测试后端 Edge TTS API
    try {
        console.log('4. 测试 Edge TTS 后端...');
        const resp = await fetch('/api/tutorial-tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: '你好，欢迎使用新手引导', lang: 'zh-CN' })
        });
        if (resp.ok) {
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.play();
            console.log('   Edge TTS: OK - 正在播放高质量语音');
        } else {
            console.log('   Edge TTS: FAIL (' + resp.status + ')');
        }
    } catch(e) {
        console.log('   Edge TTS: ERROR -', e.message);
    }
    console.log('=== 诊断完成 ===');
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = TutorialAutoVoice;
}
