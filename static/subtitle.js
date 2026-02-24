// 字幕提示框功能
// 从 app.js 中抽离的字幕子系统模块

// 归一化语言代码：将 BCP-47 格式（如 'zh-CN', 'en-US'）归一化为简单代码（'zh', 'en', 'ja', 'ko'）
// 与 detectLanguage() 返回的格式保持一致，避免误判
function normalizeLanguageCode(lang) {
    if (!lang) return 'zh'; // 默认中文
    const langLower = lang.toLowerCase();
    if (langLower.startsWith('zh')) {
        return 'zh';
    } else if (langLower.startsWith('ja')) {
        return 'ja';
    } else if (langLower.startsWith('en')) {
        return 'en';
    } else if (langLower.startsWith('ko')) {
        return 'ko';
    } else if (langLower.startsWith('ru')) {
        return 'ru';
    }
    return 'zh'; // 默认中文
}

// 字幕开关状态
let subtitleEnabled = localStorage.getItem('subtitleEnabled') === 'true';
// 用户语言（延迟初始化，避免使用 localStorage 旧值）
// 初始化为 null，确保在使用前从 API 获取最新值
let userLanguage = null;
// Google 翻译失败标记（会话级，页面刷新后重置）
let googleTranslateFailed = false;
// 用户语言初始化 Promise（用于确保只初始化一次）
let userLanguageInitPromise = null;

// 获取用户语言（支持语言代码归一化，延迟初始化）
async function getUserLanguage() {
    // 如果已经初始化过，直接返回
    if (userLanguage !== null) {
        return userLanguage;
    }
    
    // 如果正在初始化，等待初始化完成
    if (userLanguageInitPromise) {
        return await userLanguageInitPromise;
    }
    
    // 开始初始化
    userLanguageInitPromise = (async () => {
        try {
            // 优先从API获取最新值
            const response = await fetch('/api/config/user_language');
            const data = await response.json();
            if (data.success && data.language) {
                // 归一化语言代码：将 BCP-47 格式（如 'zh-CN', 'en-US'）归一化为简单代码（'zh', 'en', 'ja', 'ko'）
                // 与 detectLanguage() 返回的格式保持一致，避免误判
                userLanguage = normalizeLanguageCode(data.language);
                localStorage.setItem('userLanguage', userLanguage);
                return userLanguage;
            }
        } catch (error) {
            console.warn('从API获取用户语言失败，尝试使用缓存或浏览器语言:', error);
        }
        
        // API失败时，尝试从localStorage获取（作为回退）
        const cachedLang = localStorage.getItem('userLanguage');
        if (cachedLang) {
            userLanguage = normalizeLanguageCode(cachedLang);
            return userLanguage;
        }
        
        // 最后回退到浏览器语言
        const browserLang = navigator.language || navigator.userLanguage;
        userLanguage = normalizeLanguageCode(browserLang);
        localStorage.setItem('userLanguage', userLanguage);
        return userLanguage;
    })();
    
    return await userLanguageInitPromise;
}

// 简单的语言检测函数（客户端）
function detectLanguage(text) {
    if (!text || !text.trim()) {
        return 'unknown';
    }
    
    // 中文检测
    const chinesePattern = /[\u4e00-\u9fff]/g;
    // 日文检测（平假名、片假名）
    const japanesePattern = /[\u3040-\u309f\u30a0-\u30ff]/g;
    // 韩文检测（谚文）
    const koreanPattern = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/g;
    // 俄文检测（西里尔字母）
    const russianPattern = /[\u0400-\u04ff]/g;
    // 英文检测
    const englishPattern = /[a-zA-Z]/g;

    const chineseCount = (text.match(chinesePattern) || []).length;
    const japaneseCount = (text.match(japanesePattern) || []).length;
    const koreanCount = (text.match(koreanPattern) || []).length;
    const russianCount = (text.match(russianPattern) || []).length;
    const englishCount = (text.match(englishPattern) || []).length;

    // 如果包含日文假名，优先判断为日语
    if (japaneseCount > 0) {
        return 'ja';
    }

    // 如果包含韩文，优先判断为韩语
    if (koreanCount > 0) {
        return 'ko';
    }

    // 如果包含俄文西里尔字母，判断为俄语
    if (russianCount >= englishCount && russianCount >= chineseCount && russianCount > 0) {
        return 'ru';
    }

    // 判断主要语言
    if (chineseCount > englishCount && chineseCount > 0) {
        return 'zh';
    } else if (englishCount > 0) {
        return 'en';
    } else {
        return 'unknown';
    }
}

// 字幕显示相关变量
let subtitleTimeout = null;
let currentTranslateAbortController = null;
let pendingTranslation = null;
// 流式输出时字幕语言检测的防抖计时器
let subtitleCheckDebounceTimer = null;

// 翻译消息气泡（如果用户语言不是中文）
async function translateMessageBubble(text, messageElement) {
    if (!text || !text.trim() || !messageElement) {
        return;
    }
    
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    if (!userLanguage || userLanguage === 'zh') {
        return;
    }
    
    try {
        const response = await fetch('/api/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'),
                source_lang: 'zh',
                skip_google: googleTranslateFailed
            })
        });
        
        if (!response.ok) {
            console.warn('翻译消息气泡失败:', response.status);
            return;
        }
        
        const result = await response.json();
        
        if (result.google_failed === true) {
            googleTranslateFailed = true;
            console.log('Google 翻译失败，本次会话中将跳过 Google 翻译');
        }
        
        if (result.success && result.translated_text && result.translated_text !== text) {
            const timestampMatch = messageElement.textContent.match(/^\[(\d{2}:\d{2}:\d{2})\] 🎀 /);
            if (timestampMatch) {
                messageElement.textContent = `[${timestampMatch[1]}] 🎀 ${result.translated_text}`;
                console.log('消息气泡已翻译:', result.translated_text.substring(0, 50) + '...');
            }
        }
    } catch (error) {
        console.error('翻译消息气泡异常:', error);
    }
}

// 检查并显示字幕提示框
async function checkAndShowSubtitlePrompt(text) {
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    const allGeminiMessages = document.querySelectorAll('.message.gemini');
    let hasNonUserLanguage = false;
    let latestNonUserLanguageText = '';
    
    if (allGeminiMessages.length > 0) {
        for (const msg of allGeminiMessages) {
            const msgText = msg.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
            if (msgText && msgText.trim()) {
                const detectedLang = detectLanguage(msgText);
                if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                    hasNonUserLanguage = true;
                    latestNonUserLanguageText = msgText;
                }
            }
        }
    }
    
    if (hasNonUserLanguage) {
        showSubtitlePrompt();
    } else {
        hideSubtitlePrompt();
        hideSubtitle();
    }
}

// 翻译并显示字幕
async function translateAndShowSubtitle(text) {
    if (!text || !text.trim()) {
        return;
    }
    
    // 即使开关关闭，也需要检测语言来决定是否隐藏提示
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    const currentTranslationText = text;
    pendingTranslation = currentTranslationText;
    
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
    }
    
    currentTranslateAbortController = new AbortController();
    
    try {
        const subtitleDisplay = document.getElementById('subtitle-display');
        if (!subtitleDisplay) {
            console.warn('字幕显示元素不存在');
            return;
        }
        
        // 调用翻译API
        const response = await fetch('/api/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'), // 确保已初始化
                source_lang: null, // 自动检测
                skip_google: googleTranslateFailed // 如果 Google 翻译失败过，跳过它
            }),
            signal: currentTranslateAbortController.signal
        });
        
        if (!response.ok) {
            console.warn('翻译请求失败:', response.status);
            if (pendingTranslation === currentTranslationText) {
                pendingTranslation = null;
            }
            console.error('字幕翻译API请求失败:', {
                status: response.status,
                statusText: response.statusText,
                text: text.substring(0, 50) + '...',
                userLanguage: userLanguage
            });
            return;
        }
        
        const result = await response.json();
        
        if (pendingTranslation !== currentTranslationText) {
            console.log('检测到更新的翻译请求，忽略旧的翻译结果');
            return;
        }
        pendingTranslation = null;
        
        if (result.google_failed === true) {
            googleTranslateFailed = true;
            console.log('Google 翻译失败，本次会话中将跳过 Google 翻译');
        }
        
        const frontendDetectedLang = detectLanguage(text);
        const isNonUserLanguage = frontendDetectedLang !== 'unknown' && frontendDetectedLang !== userLanguage;
        
        const subtitleDisplayAfter = document.getElementById('subtitle-display');
        if (!subtitleDisplayAfter) {
            console.warn('字幕显示元素在异步操作后不存在，可能已被移除');
            return;
        }
        
        if (result.success && result.translated_text && 
            result.source_lang && result.target_lang && 
            result.source_lang !== result.target_lang && 
            result.source_lang !== 'unknown') {
            showSubtitlePrompt();
            
            if (subtitleEnabled) {
                subtitleDisplayAfter.textContent = result.translated_text;
                subtitleDisplayAfter.classList.add('show');
                subtitleDisplayAfter.classList.remove('hidden');
                subtitleDisplayAfter.style.opacity = '1';
                console.log('字幕已更新（已翻译）:', result.translated_text.substring(0, 50) + '...');
                
                if (subtitleTimeout) {
                    clearTimeout(subtitleTimeout);
                    subtitleTimeout = null;
                }
                
                subtitleTimeout = setTimeout(() => {
                    const subtitleDisplayForTimeout = document.getElementById('subtitle-display');
                    if (subtitleDisplayForTimeout && subtitleDisplayForTimeout.classList.contains('show')) {
                        hideSubtitle();
                        console.log('字幕30秒后自动隐藏');
                    }
                }, 30000);
            } else {
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('开关已关闭，不显示字幕');
            }
        } else {
            if (isNonUserLanguage) {
                showSubtitlePrompt();
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('前端检测到非用户语言，显示提示框');
            } else {
                hideSubtitlePrompt();
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('对话已是用户语言，自动隐藏字幕提示');
            }
            if (subtitleTimeout) {
                clearTimeout(subtitleTimeout);
                subtitleTimeout = null;
            }
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            if (pendingTranslation === currentTranslationText) {
                pendingTranslation = null;
            }
            return;
        }
        
        console.error('字幕翻译异常:', {
            error: error.message,
            stack: error.stack,
            name: error.name,
            text: text.substring(0, 50) + '...',
            userLanguage: userLanguage
        });
        
        if (pendingTranslation === currentTranslationText) {
            pendingTranslation = null;
        }
        
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            console.warn('提示：字幕翻译功能暂时不可用，但对话可以正常进行');
        }
    } finally {
        currentTranslateAbortController = null;
    }
}

// 隐藏字幕
function hideSubtitle() {
    const subtitleDisplay = document.getElementById('subtitle-display');
    if (!subtitleDisplay) return;
    
    // 清除定时器
    if (subtitleTimeout) {
        clearTimeout(subtitleTimeout);
        subtitleTimeout = null;
    }
    
    subtitleDisplay.classList.remove('show');
    subtitleDisplay.style.opacity = '0';
    
    // 延迟隐藏，让淡出动画完成
    setTimeout(() => {
        const subtitleDisplayForTimeout = document.getElementById('subtitle-display');
        if (subtitleDisplayForTimeout && subtitleDisplayForTimeout.style.opacity === '0') {
            subtitleDisplayForTimeout.classList.add('hidden');
        }
    }, 300);
}

// 显示字幕提示框（参考Xiao8项目，改为系统消息形式）
function showSubtitlePrompt() {
    // 检查是否已经显示过提示（避免重复显示）
    const existingPrompt = document.getElementById('subtitle-prompt-message');
    if (existingPrompt) {
        return;
    }
    
    const textInputArea = document.getElementById('text-input-area');
    const chatContainer = document.getElementById('chat-container');
    
    // 检测是否处于语音模式（text-input-area 被隐藏）
    const isVoiceMode = textInputArea && textInputArea.classList.contains('hidden');
    
    // 确定父容器：语音模式下使用 chat-container，否则使用 text-input-area
    let parentContainer;
    if (isVoiceMode) {
        parentContainer = chatContainer;
    } else {
        parentContainer = textInputArea;
    }
    
    if (!parentContainer) {
        return;
    }
    
    // 创建提示消息（放在输入框区域中）
    const promptDiv = document.createElement('div');
    promptDiv.id = 'subtitle-prompt-message';
    promptDiv.classList.add('subtitle-prompt-message');
    
    // 如果是语音模式，添加特殊样式类
    if (isVoiceMode) {
        promptDiv.classList.add('voice-mode');
    }
    
    // 创建提示内容
    const promptContent = document.createElement('div');
    promptContent.classList.add('subtitle-prompt-content');
    
    // 创建开关容器
    const toggleWrapper = document.createElement('div');
    toggleWrapper.classList.add('subtitle-toggle-wrapper');
    
    // 创建圆形指示器
    const indicator = document.createElement('div');
    indicator.classList.add('subtitle-toggle-indicator');
    if (subtitleEnabled) {
        indicator.classList.add('active');
    }
    
    // 创建标签文本
    const labelText = document.createElement('span');
    labelText.classList.add('subtitle-toggle-label');
    labelText.setAttribute('data-i18n', 'subtitle.enable');
    // 使用i18n翻译，如果i18n未加载或翻译不存在则根据浏览器语言提供fallback
    const browserLang = normalizeLanguageCode(navigator.language);
    const fallbacks = {
        'zh': '开启字幕翻译',
        'en': 'Enable Subtitle Translation',
        'ja': '字幕翻訳を有効にする',
        'ko': '자막 번역 켜기',
        'ru': 'Включить перевод субтитров'
    };
    if (window.t) {
        const translated = window.t('subtitle.enable');
        // 如果翻译返回的是key本身（说明翻译不存在），使用浏览器语言的fallback
        labelText.textContent = (translated && translated !== 'subtitle.enable') ? translated : (fallbacks[browserLang] || fallbacks['en']);
    } else {
        // i18n未加载时，使用浏览器语言的fallback
        labelText.textContent = fallbacks[browserLang] || fallbacks['en'];
    }
    
    toggleWrapper.appendChild(indicator);
    toggleWrapper.appendChild(labelText);
    
    promptContent.appendChild(toggleWrapper);
    promptDiv.appendChild(promptContent);
    
    // 根据模式插入到不同位置
    if (isVoiceMode) {
        // 语音模式：插入到 chat-container 底部（在 text-input-area 之前）
        if (textInputArea) {
            chatContainer.insertBefore(promptDiv, textInputArea);
        } else {
            chatContainer.appendChild(promptDiv);
        }
    } else {
        // 文本模式：插入到输入框区域的最后（在text-input-row之后）
        const textInputRow = textInputArea.querySelector('#text-input-row');
        if (textInputRow && textInputRow.nextSibling) {
            textInputArea.insertBefore(promptDiv, textInputRow.nextSibling);
        } else {
            textInputArea.appendChild(promptDiv);
        }
    }

    
    // 如果i18next已加载，监听语言变化事件
    if (window.i18next) {
        window.i18next.on('languageChanged', () => {
            if (labelText && window.t) {
                const translated = window.t('subtitle.enable');
                // 如果翻译返回的是key本身（说明翻译不存在），使用当前语言的fallback
                if (translated && translated !== 'subtitle.enable') {
                    labelText.textContent = translated;
                } else {
                    // 使用与初始渲染相同的fallback逻辑
                    const currentLang = normalizeLanguageCode(window.i18next.language || navigator.language);
                    labelText.textContent = fallbacks[currentLang] || fallbacks['en'];
                }
            }
        });
    }
    
    // 更新指示器状态
    const updateIndicator = () => {
        if (subtitleEnabled) {
            indicator.classList.add('active');
        } else {
            indicator.classList.remove('active');
        }
    };
    
    // 切换开关的函数
    const handleToggle = (e) => {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }
        subtitleEnabled = !subtitleEnabled;
        localStorage.setItem('subtitleEnabled', subtitleEnabled.toString());
        updateIndicator();
        console.log('字幕开关:', subtitleEnabled ? '开启' : '关闭');
        
        if (!subtitleEnabled) {
            const subtitleDisplay = document.getElementById('subtitle-display');
            if (subtitleDisplay) {
                subtitleDisplay.textContent = '';
                subtitleDisplay.classList.remove('show');
                subtitleDisplay.classList.add('hidden');
                subtitleDisplay.style.opacity = '0';
            }
            if (subtitleTimeout) {
                clearTimeout(subtitleTimeout);
                subtitleTimeout = null;
            }
        } else {
            // 如果开启，重新翻译并显示字幕
            if (currentTranslateAbortController) {
                currentTranslateAbortController.abort();
                currentTranslateAbortController = null;
            }
            pendingTranslation = null;
            
            if (window.currentGeminiMessage && 
                window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                window.currentGeminiMessage.isConnected &&
                typeof window.currentGeminiMessage.textContent === 'string') {
                const fullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
                if (fullText && fullText.trim()) {
                    const subtitleDisplay = document.getElementById('subtitle-display');
                    if (!subtitleDisplay) {
                        console.error('字幕显示元素不存在，无法显示字幕');
                        return;
                    }
                    subtitleDisplay.classList.remove('hidden');
                    translateAndShowSubtitle(fullText);
                }
            } else {
                if (window.currentGeminiMessage) {
                    console.warn('currentGeminiMessage存在但不是有效的DOM元素，无法翻译字幕');
                }
            }
        }
    };
    
    // 绑定点击事件
    toggleWrapper.addEventListener('click', handleToggle);
    indicator.addEventListener('click', handleToggle);
    labelText.addEventListener('click', handleToggle);
}

// 隐藏字幕提示框
function hideSubtitlePrompt() {
    const existingPrompt = document.getElementById('subtitle-prompt-message');
    if (existingPrompt) {
        existingPrompt.remove();
        console.log('已隐藏字幕提示消息');
    }
}

// 初始化字幕开关（DOM加载完成后）
document.addEventListener('DOMContentLoaded', async function() {
    // 初始化用户语言（等待完成，确保使用最新值）
    await getUserLanguage();

    // 检查当前消息中是否有非用户语言
    // 增强null安全检查：确保currentGeminiMessage是有效的DOM元素
    if (window.currentGeminiMessage &&
        window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
        window.currentGeminiMessage.isConnected &&
        typeof window.currentGeminiMessage.textContent === 'string') {
        const fullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
        if (fullText && fullText.trim()) {
            checkAndShowSubtitlePrompt(fullText);
        }
    }

    // 初始化通用引导管理器（幂等性保护）
    if (!window.__universalTutorialManagerInitialized && typeof initUniversalTutorialManager === 'function') {
        try {
            initUniversalTutorialManager();
            window.__universalTutorialManagerInitialized = true;
            console.log('[App] 通用引导管理器已初始化');
        } catch (error) {
            console.error('[App] 通用引导管理器初始化失败:', error);
        }
    }
});
