/**
 * i18next 初始化文件
 * 使用成熟的 i18next 库管理本地化文本
 * 优先使用 Steam 客户端语言设置，其次是浏览器设置
 * 包含本地文件加载、检查和容错机制
 * 
 * 使用方式：
 * 在 HTML 的 <head> 中引入：
 * <script src="/static/i18n-i18next.js"></script>
 * 
 * 此脚本会自动：
 * 1. 加载本地 i18next 库（从 /static/libs/）
 * 2. 检查依赖加载状态
 * 3. 处理加载失败容错（重新加载或降级方案）
 * 4. 从 Steam API 获取语言设置
 * 5. 初始化 i18next
 */

(function () {
    'use strict';

    // 如果已经初始化过，直接返回
    if (window.i18nInitialized) {
        return;
    }
    window.i18nInitialized = true;

    // 支持的语言列表
    const SUPPORTED_LANGUAGES = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru'];

    // locale 资源版本（用于 cache-busting，避免客户端长期缓存旧语言包导致新增 key 不生效）
    // 更新语言包内容时可以递增此值
    const LOCALE_VERSION = '2026-02-23-1';

    // 获取浏览器语言（同步，作为 fallback）
    function getBrowserLanguage() {
        // 1. 检查 localStorage
        const savedLanguage = localStorage.getItem('i18nextLng');
        if (savedLanguage && SUPPORTED_LANGUAGES.includes(savedLanguage)) {
            return savedLanguage;
        }

        // 2. 检查浏览器语言设置
        const browserLanguage = navigator.language || navigator.userLanguage;
        if (browserLanguage) {
            // 完全匹配
            if (SUPPORTED_LANGUAGES.includes(browserLanguage)) {
                return browserLanguage;
            }
            // 部分匹配（例如 'en-US' 匹配 'en'）
            const langCode = browserLanguage.split('-')[0].toLowerCase();
            if (langCode === 'en') return 'en';
            if (langCode === 'ja') return 'ja';
            if (langCode === 'ko') return 'ko';
            if (langCode === 'ru') return 'ru';
            if (langCode === 'zh') {
                // 根据地区/脚本区分简繁（如 zh-TW / zh-HK / zh-Hant）
                const upper = browserLanguage.toUpperCase();
                if (upper.includes('TW') || upper.includes('HK') || upper.includes('HANT')) {
                    return 'zh-TW';
                }
                return 'zh-CN';
            }
        }

        // 3. 默认返回中文
        return 'zh-CN';
    }

    // 从 Steam API 获取语言设置（异步）
    async function getSteamLanguage() {
        try {
            const response = await fetch('/api/config/steam_language', {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                // 设置超时，避免阻塞太久
                signal: AbortSignal.timeout(2000)
            });

            if (!response.ok) {
                console.log('[i18n] Steam 语言 API 响应异常:', response.status);
                return null;
            }

            const data = await response.json();
            console.log('[i18n] Steam API 返回语言设置:', data);

            if (data.success && data.i18n_language && SUPPORTED_LANGUAGES.includes(data.i18n_language)) {
                return data.i18n_language;
            }

            console.log('[i18n] Steam 语言 API 返回无效数据，回退到浏览器设置');
            return null;
        } catch (error) {
            // 可能是超时或网络错误，静默处理
            console.log('[i18n] 无法从 Steam 获取语言设置，使用浏览器设置:', error.message);
            return null;
        }
    }

    // 获取初始语言：优先 Steam 设置，然后 localStorage，然后浏览器设置，最后默认中文
    async function getInitialLanguage() {
        // 1. 尝试从 Steam API 获取语言
        const steamLanguage = await getSteamLanguage();
        if (steamLanguage) {
            // 保存到 localStorage，下次可以直接使用
            localStorage.setItem('i18nextLng', steamLanguage);
            return steamLanguage;
        }

        // 2. 回退到浏览器语言
        return getBrowserLanguage();
    }

    // 先使用同步方式获取初始语言（用于快速显示）
    let INITIAL_LANGUAGE = getBrowserLanguage();

    // ==================== CDN 动态加载 ====================

    /**
     * 动态加载脚本
     */
    function loadScript(src, onLoad, onError) {
        // 检查是否已经存在 script 标签
        const existingScript = document.querySelector(`script[src="${src}"]`);
        if (existingScript) {
            // 注意：不立即调用 onLoad，因为之前的加载可能失败
            // 实际的可用性由下游的依赖检查（checkDependencies）来验证
            console.log(`[i18n] Script tag for ${src} already exists, skipping duplicate load`);
            console.log(`[i18n] Note: Actual availability will be checked by checkDependencies`);
            return;
        }

        const script = document.createElement('script');
        script.src = src;
        script.onload = onLoad || function () { };
        script.onerror = onError || function () {
            console.error(`[i18n] 加载脚本失败: ${src}`);
        };
        document.head.appendChild(script);
    }

    // 加载 i18next 核心库（使用本地文件）
    loadScript(
        '/static/libs/i18next.min.js',
        null,
        function () {
            console.error('[i18n] 加载 i18next 失败');
        }
    );

    // 加载 i18next HTTP Backend（使用本地文件）
    loadScript(
        '/static/libs/i18nextHttpBackend.min.js',
        null,
        function () {
            console.error('[i18n] 加载 i18nextHttpBackend 失败');
        }
    );

    // ==================== CDN 加载检查和容错机制 ====================

    /**
     * 检查 CDN 依赖并初始化 i18next
     */
    function checkDependenciesAndInit() {
        const i18nextLoaded = typeof i18next !== 'undefined';
        const backendLoaded = typeof i18nextHttpBackend !== 'undefined';

        if (i18nextLoaded && backendLoaded) {
            console.log('[i18n] ✅ 所有依赖库已加载');
            // 依赖已加载，直接初始化
            initI18next();
        } else {
            // 依赖未加载，尝试重新加载本地文件或使用降级方案
            console.error('[i18n] ⚠️ 依赖库未完全加载，尝试重新加载本地文件...');

            // 如果 i18nextHttpBackend 未加载，尝试重新加载本地文件
            if (!backendLoaded) {
                loadScript(
                    '/static/libs/i18nextHttpBackend.min.js',
                    function () {
                        console.log('[i18n] ✅ 本地文件加载成功');
                        // 再次检查并初始化
                        setTimeout(() => {
                            if (typeof i18nextHttpBackend !== 'undefined') {
                                initI18next();
                            } else {
                                initI18nextWithoutBackend();
                            }
                        }, 100);
                    },
                    function () {
                        console.error('[i18n] ❌ 本地文件加载失败，使用降级方案');
                        initI18nextWithoutBackend();
                    }
                );
            } else if (!i18nextLoaded) {
                // i18next 未加载，无法继续
                console.error('[i18n] ❌ i18next 核心库未加载，无法初始化');
                exportFallbackFunctions();
            } else {
                // 其他情况，使用降级方案
                initI18nextWithoutBackend();
            }
        }
    }

    /**
     * 等待依赖加载并初始化
     */
    function waitForDependenciesAndInit() {
        let checkCount = 0;
        const maxChecks = 50; // 最多检查 5 秒

        function checkDependencies() {
            checkCount++;

            const i18nextLoaded = typeof i18next !== 'undefined';
            const backendLoaded = typeof i18nextHttpBackend !== 'undefined';

            if (i18nextLoaded && backendLoaded) {
                console.log('[i18n] ✅ 所有依赖库已加载');
                initI18next();
            } else if (checkCount < maxChecks) {
                // 继续等待
                setTimeout(checkDependencies, 100);
            } else {
                // 超时，使用容错机制
                checkDependenciesAndInit();
            }
        }

        // 开始检查
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', checkDependencies);
        } else {
            checkDependencies();
        }

        // 安全网：10秒后强制初始化（即使依赖未加载）
        setTimeout(function () {
            if (typeof window.t === 'undefined') {
                const i18nextAvailable = typeof i18next !== 'undefined';
                const backendAvailable = typeof i18nextHttpBackend !== 'undefined';
                console.warn('[i18n] ⚠️ 10秒后仍未初始化，强制初始化', {
                    i18next: i18nextAvailable,
                    backend: backendAvailable
                });
                if (i18nextAvailable) {
                    if (backendAvailable) {
                        initI18next();
                    } else {
                        initI18nextWithoutBackend();
                    }
                } else {
                    exportFallbackFunctions();
                }
            }
        }, 10000);
    }

    // 诊断函数
    window.diagnoseI18n = function () {
        console.log('=== i18next 诊断信息 ===');
        console.log('1. i18next 是否存在:', typeof i18next !== 'undefined');
        console.log('2. window.t 是否存在:', typeof window.t === 'function');
        console.log('3. window.i18n 是否存在:', typeof window.i18n !== 'undefined');

        if (typeof i18next !== 'undefined') {
            console.log('4. i18next.isInitialized:', i18next.isInitialized);
            console.log('5. 当前语言:', i18next.language);
            console.log('6. 支持的语言:', i18next.options?.supportedLngs);
            console.log('7. 已加载的资源:', Object.keys(i18next.store?.data || {}));

            // 检查资源内容
            const currentLang = i18next.language;
            const hasResource = i18next.hasResourceBundle(currentLang, 'translation');
            console.log('8. 资源是否存在:', hasResource);

            if (hasResource) {
                const resource = i18next.getResourceBundle(currentLang, 'translation');
                console.log('9. 资源键数量:', Object.keys(resource || {}).length);

                // 测试几个常见的翻译键
                const testKeys = ['app.title', 'voiceControl.startVoice', 'chat.title'];
                console.log('10. 测试翻译键:');
                testKeys.forEach(key => {
                    const value = resource?.[key] || (() => {
                        // 尝试嵌套访问
                        const parts = key.split('.');
                        let obj = resource;
                        for (const part of parts) {
                            if (obj && typeof obj === 'object') {
                                obj = obj[part];
                            } else {
                                obj = undefined;
                                break;
                            }
                        }
                        return obj;
                    })();
                    const translated = i18next.t(key);
                    console.log(`   "${key}": 资源中=${value !== undefined ? '存在' : '不存在'}, 翻译结果="${translated}"`);
                });
            } else {
                console.error('8. 资源不存在！');
            }
        } else {
            console.error('4. i18next 未加载！请检查 CDN 是否成功加载。');
        }

        // 检查页面上的 data-i18n 元素
        const elements = document.querySelectorAll('[data-i18n]');
        console.log(`11. 页面上的 data-i18n 元素数量: ${elements.length}`);
        if (elements.length > 0) {
            console.log('12. 前3个元素:');
            Array.from(elements).slice(0, 3).forEach((el, i) => {
                const key = el.getAttribute('data-i18n');
                const text = el.textContent;
                const translated = typeof window.t === 'function' ? window.t(key) : 'N/A';
                console.log(`   元素 ${i + 1}: key="${key}", text="${text}", 翻译="${translated}"`);
            });
        }

        console.log('=== 诊断完成 ===');
    };

    // 测试翻译函数
    window.testTranslation = function (key) {
        console.log(`测试翻译键: ${key}`);
        if (typeof window.t === 'function') {
            const result = window.t(key);
            console.log(`结果: ${result}`);
            return result;
        } else {
            console.error('window.t 函数不存在');
            return null;
        }
    };

    /**
     * 不使用 HTTP Backend，手动加载翻译文件
     */
    async function initI18nextWithoutBackend() {
        console.log('[i18n] 开始手动加载翻译文件...');

        if (typeof i18next === 'undefined') {
            console.error('[i18n] ❌ i18next 核心库未加载，无法初始化');
            exportFallbackFunctions();
            return;
        }

        try {
            // 并行执行：获取 Steam 语言设置 + 加载翻译文件
            const [steamLang, ...langResults] = await Promise.all([
                getInitialLanguage(),  // 异步获取语言（优先 Steam）
                ...SUPPORTED_LANGUAGES.map(async (lang) => {
                    try {
                        const response = await fetch(`/static/locales/${lang}.json?v=${encodeURIComponent(LOCALE_VERSION)}`);
                        if (response.ok) {
                            const translations = await response.json();
                            console.log(`[i18n] ✅ ${lang} 翻译文件加载成功`);
                            return { lang, translations };
                        } else {
                            console.warn(`[i18n] ⚠️ ${lang} 翻译文件不存在或加载失败: ${response.status}`);
                            return null;
                        }
                    } catch (error) {
                        console.warn(`[i18n] ⚠️ ${lang} 翻译文件加载出错:`, error);
                        return null;
                    }
                })
            ]);

            // 使用获取到的语言设置
            INITIAL_LANGUAGE = steamLang;
            console.log(`[i18n] 使用语言: ${INITIAL_LANGUAGE}`);

            // 构建资源对象
            const resources = {};
            langResults.forEach(result => {
                if (result) {
                    resources[result.lang] = {
                        translation: result.translations
                    };
                }
            });

            // 确保至少有一个语言资源
            if (Object.keys(resources).length === 0) {
                throw new Error('没有可用的翻译文件');
            }

            // 初始化 i18next
            i18next.init({
                lng: INITIAL_LANGUAGE,
                fallbackLng: 'zh-CN', // 默认回退到中文
                supportedLngs: SUPPORTED_LANGUAGES,
                ns: ['translation'],
                defaultNS: 'translation',
                resources: resources,
                detection: {
                    order: [],
                    caches: []
                },
                interpolation: {
                    escapeValue: false
                },
                debug: false
            }, function (err, t) {
                if (err) {
                    console.error('[i18n] 初始化失败:', err);
                    exportFallbackFunctions();
                    return;
                }

                console.log('[i18n] ✅ 初始化成功（手动加载模式）');
                // 设置 HTML lang 属性，用于 CSS 语言特定样式
                document.documentElement.lang = i18next.language;
                updatePageTexts();
                window.dispatchEvent(new CustomEvent('localechange'));
                exportNormalFunctions();
            });
        } catch (error) {
            console.error('[i18n] 手动加载翻译文件失败:', error);
            exportFallbackFunctions();
        }
    }

    /**
     * 导出降级函数（当初始化失败时使用）
     */
    function exportFallbackFunctions() {
        console.warn('[i18n] Using fallback functions due to initialization failure');

        window.t = function (key, params = {}) {
            console.warn('[i18n] Fallback t() called with key:', key);
            return key;
        };

        window.i18n = {
            isInitialized: false,
            language: INITIAL_LANGUAGE,
            store: { data: {} }
        };

        window.updatePageTexts = function () {
            console.warn('[i18n] Fallback updatePageTexts() called - no-op');
        };

        window.updateLive2DDynamicTexts = function () {
            console.warn('[i18n] Fallback updateLive2DDynamicTexts() called - no-op');
        };
    }

    /**
     * 初始化 i18next（使用 HTTP Backend）
     */
    async function initI18next() {
        if (typeof i18next === 'undefined') {
            console.error('[i18n] ❌ i18next 核心库未加载，无法初始化');
            exportFallbackFunctions();
            return;
        }

        if (typeof i18nextHttpBackend === 'undefined') {
            console.warn('[i18n] ⚠️ i18nextHttpBackend 未加载，使用手动加载方式');
            initI18nextWithoutBackend();
            return;
        }

        // 获取语言设置（优先 Steam API）
        const language = await getInitialLanguage();
        INITIAL_LANGUAGE = language;

        // 初始化 i18next
        console.log('[i18n] 开始初始化 i18next...');

        try {
            i18next
                .use(i18nextHttpBackend)
                .init({
                    lng: INITIAL_LANGUAGE,
                    fallbackLng: 'zh-CN', // 默认回退到中文
                    supportedLngs: SUPPORTED_LANGUAGES,
                    ns: ['translation'],
                    defaultNS: 'translation',
                    backend: {
                        loadPath: `/static/locales/{{lng}}.json?v=${encodeURIComponent(LOCALE_VERSION)}`,
                        parse: function (data) {
                            try {
                                return JSON.parse(data);
                            } catch (e) {
                                console.error('[i18n] 解析翻译文件失败:', e);
                                throw e;
                            }
                        }
                    },
                    detection: {
                        order: [],
                        caches: []
                    },
                    interpolation: {
                        escapeValue: false
                    },
                    debug: false
                }, function (err, t) {
                    if (err) {
                        console.error('[i18n] Initialization failed:', err);
                        exportFallbackFunctions();
                        return;
                    }

                    console.log('[i18n] ✅ 初始化成功！');
                    console.log('[i18n] 当前语言:', i18next.language);

                    // 防止重复初始化的标志
                    let initialized = false;

                    // 统一的初始化完成函数，确保只执行一次
                    const finalizeInit = () => {
                        if (initialized) return;
                        initialized = true;
                        // 设置 HTML lang 属性，用于 CSS 语言特定样式
                        document.documentElement.lang = i18next.language;
                        updatePageTexts();
                        window.dispatchEvent(new CustomEvent('localechange'));
                        exportNormalFunctions();
                    };

                    // 确保资源已经加载
                    const checkResources = () => {
                        const lang = i18next.language;
                        if (i18next.hasResourceBundle(lang, 'translation')) {
                            finalizeInit();
                        } else {
                            // 如果资源还没加载，等待一下再试
                            setTimeout(() => {
                                if (i18next.hasResourceBundle(lang, 'translation')) {
                                    finalizeInit();
                                } else {
                                    console.error('[i18n] 翻译资源加载失败，使用回退函数');
                                    exportFallbackFunctions();
                                }
                            }, 100);
                        }
                    };

                    // 监听资源加载完成事件
                    const loadedHandler = function (loaded) {
                        if (loaded && i18next.hasResourceBundle(i18next.language, 'translation')) {
                            finalizeInit();
                            // 移除事件监听器，防止内存泄漏
                            i18next.off('loaded', loadedHandler);
                        }
                    };
                    i18next.on('loaded', loadedHandler);

                    checkResources();
                });
        } catch (error) {
            console.error('[i18n] Fatal error during initialization:', error);
            exportFallbackFunctions();
        }
    }

    // ==================== 启动初始化流程 ====================

    // 等待依赖加载并初始化
    waitForDependenciesAndInit();

    /**
     * 解析 providerKey 并设置 provider 参数
     * @param {object} params - 翻译参数对象
     * @returns {object} 修改后的参数对象
     */
    function resolveProviderName(params) {
        if (!params || !params.providerKey) return params;

        try {
            const resources = i18next.getResourceBundle(i18next.language, 'translation');
            const providerNames = resources?.api?.providerNames || {};
            params.provider = providerNames[params.providerKey] || params.providerKey;
        } catch (error) {
            console.warn('[i18n] Failed to resolve providerKey:', error);
            params.provider = params.providerKey;
        }

        return params;
    }

    /**
     * 导出正常函数（初始化成功后使用）
     */
    function exportNormalFunctions() {
        // 导出翻译函数
        window.t = function (key, params = {}) {
            if (!key) return '';

            // 处理 providerKey 参数（与现有代码兼容）
            resolveProviderName(params);

            return i18next.t(key, params);
        };

        // 导出 i18next 实例
        window.i18n = i18next;

        // 导出更新函数
        window.updatePageTexts = updatePageTexts;
        window.updateLive2DDynamicTexts = updateLive2DDynamicTexts;
        window.translateStatusMessage = translateStatusMessage;

        // 监听语言变化（用于更新文本）
        i18next.on('languageChanged', (lng) => {
            // 保存语言选择到 localStorage
            localStorage.setItem('i18nextLng', lng);
            // 更新 HTML lang 属性，用于 CSS 语言特定样式
            document.documentElement.lang = lng;
            updatePageTexts();
            updateLive2DDynamicTexts();
            window.dispatchEvent(new CustomEvent('localechange'));
        });

        // 导出语言切换函数
        window.changeLanguage = function (lng) {
            if (!SUPPORTED_LANGUAGES.includes(lng)) {
                console.warn(`[i18n] 不支持的语言: ${lng}，支持的语言: ${SUPPORTED_LANGUAGES.join(', ')}`);
                return Promise.reject(new Error(`不支持的语言: ${lng}`));
            }
            return i18next.changeLanguage(lng);
        };

        // 确保在 DOM 加载完成后更新文本
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () {
                updatePageTexts();
                updateLive2DDynamicTexts();
            });
        } else {
            updatePageTexts();
            updateLive2DDynamicTexts();
        }

        console.log('[i18n] Normal functions exported successfully');
    }

    /**
     * 更新页面文本的函数
     */
    function updatePageTexts() {
        if (!i18next.isInitialized) {
            console.warn('[i18n] i18next not initialized yet, skipping updatePageTexts');
            return;
        }

        // 检查资源是否已加载
        if (!i18next.hasResourceBundle(i18next.language, 'translation')) {
            console.warn('[i18n] Translation resources not loaded yet, skipping updatePageTexts');
            return;
        }

        // 更新所有带有 data-i18n 属性的元素
        const elements = document.querySelectorAll('[data-i18n]');
        elements.forEach(element => {
            const key = element.getAttribute('data-i18n');
            let params = {};

            // 兼容两种参数属性：
            // - data-i18n-params: 当前规范
            // - data-i18n-options: 历史用法（例如创意工坊分页）
            const paramsAttr = element.hasAttribute('data-i18n-params')
                ? 'data-i18n-params'
                : (element.hasAttribute('data-i18n-options') ? 'data-i18n-options' : null);

            if (paramsAttr) {
                try {
                    params = JSON.parse(element.getAttribute(paramsAttr));
                } catch (e) {
                    console.warn(`[i18n] Failed to parse params for ${key}:`, e);
                }
            }

            // 处理 providerKey 参数
            resolveProviderName(params);

            const text = i18next.t(key, params);

            if (text === key) {
                // 只在开发模式下显示警告，避免控制台噪音
                if (i18next.options.debug) {
                    console.warn(`[i18n] Translation key not found: ${key}`);
                }
            }

            // 特殊处理 title 标签
            if (element.tagName === 'TITLE') {
                document.title = text;
                return;
            }

            // 如果元素有 data-text 属性，也更新它（用于 CSS attr() 显示）
            if (element.hasAttribute('data-text')) {
                element.setAttribute('data-text', text);
            }

            // 如果翻译文本包含 HTML 标签（如 <br>、<img> 等），使用 innerHTML，否则使用 textContent
            if (text.includes('<br>') || text.includes('<BR>') || text.includes('<br/>') || text.includes('<img>') || text.includes('<IMG>') || text.includes('<img ')) {
                // 安全过滤：仅允许 <br> 和 <img> 标签，且 <img> 仅允许安全属性
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = text;
                
                // 递归清理节点
                const sanitize = (node) => {
                    const children = Array.from(node.childNodes);
                    children.forEach(child => {
                        if (child.nodeType === 1) { // Element node
                            const tagName = child.tagName.toUpperCase();
                            if (tagName !== 'BR' && tagName !== 'IMG') {
                                // 不允许的标签，替换为其文本内容
                                const textNode = document.createTextNode(child.textContent);
                                node.replaceChild(textNode, child);
                            } else if (tagName === 'IMG') {
                                // 检查属性
                                Array.from(child.attributes).forEach(attr => {
                                    const attrName = attr.name.toLowerCase();
                                    if (attrName === 'src') {
                                        // 验证协议，防止 javascript: 和 data: 等潜在危险协议
                                        // 使用正则匹配，处理可能存在的空白字符（如制表符、换行符等）
                                        const val = attr.value.trim().toLowerCase();
                                        const blockedProtocols = /^(javascript|data|vbscript|file):/i;
                                        if (blockedProtocols.test(val.replace(/[\x00-\x20\s]/g, ''))) {
                                            child.removeAttribute(attrName);
                                        }
                                    } else if (attrName !== 'alt' && attrName !== 'class') {
                                        // 仅允许 alt, class 属性 (移除 style 属性以防注入)
                                        child.removeAttribute(attrName);
                                    }
                                    // 移除所有事件处理器
                                    if (attrName.startsWith('on')) {
                                        child.removeAttribute(attrName);
                                    }
                                });
                            }
                            sanitize(child);
                        }
                    });
                };
                
                sanitize(tempDiv);
                element.innerHTML = tempDiv.innerHTML;
            } else {
                element.textContent = text;
            }
        });

        // 更新所有带有 data-i18n-placeholder 属性的元素
        document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
            const key = element.getAttribute('data-i18n-placeholder');
            const text = i18next.t(key, {});
            if (text && text !== key) {
                element.placeholder = text;
            }
        });

        // 更新所有带有 data-i18n-title 属性的元素
        document.querySelectorAll('[data-i18n-title]').forEach(element => {
            const key = element.getAttribute('data-i18n-title');
            const text = i18next.t(key, {});
            if (text && text !== key) {
                element.title = text;
            }
        });

        // 更新所有带有 data-i18n-alt 属性的元素
        document.querySelectorAll('[data-i18n-alt]').forEach(element => {
            const key = element.getAttribute('data-i18n-alt');
            const text = i18next.t(key, {});
            if (text && text !== key) {
                element.alt = text;
            }
        });
    }

    /**
     * 更新 Live2D 动态文本
     */
    function updateLive2DDynamicTexts() {
        // 更新浮动按钮的标题（包括 .floating-btn, .live2d-floating-btn 和 .vrm-floating-btn）
        const buttons = document.querySelectorAll('.floating-btn, .live2d-floating-btn, .vrm-floating-btn');
        buttons.forEach(btn => {
            const titleKey = btn.getAttribute('data-i18n-title');
            if (titleKey) {
                btn.title = i18next.t(titleKey);
            }
        });

        // 更新设置菜单项
        const menuItems = document.querySelectorAll('[data-i18n-label]');
        menuItems.forEach(item => {
            const labelKey = item.getAttribute('data-i18n-label');
            if (labelKey) {
                const label = item.querySelector('label');
                if (label) {
                    label.textContent = i18next.t(labelKey);
                }
            }
        });

        // 更新动态创建的标签
        // _updateLabelText 是附加在父容器（toggleItem 或 menuItem）上的，不是直接在 [data-i18n] 元素上
        // 查找所有可能包含 _updateLabelText 的容器元素
        // 方法1：查找所有 live2d-popup, vrm-popup 和 shared-popup 内的直接子 div（toggleItem 和 menuItem）
        const popups = document.querySelectorAll('.live2d-popup, .vrm-popup, .shared-popup');
        popups.forEach(popup => {
            // 查找 popup 的直接子 div 元素
            Array.from(popup.children).forEach(child => {
                if (child.tagName === 'DIV' && child._updateLabelText && typeof child._updateLabelText === 'function') {
                    child._updateLabelText();
                }
            });
        });

        // 方法2：也检查是否有直接附加在元素上的 _updateLabelText（向后兼容）
        document.querySelectorAll('[data-i18n]').forEach(element => {
            if (element._updateLabelText && typeof element._updateLabelText === 'function') {
                element._updateLabelText();
            }
        });
    }

    /**
     * 翻译状态消息
     * 
     * TODO: Replace with error code-based translation when backend supports it
     * This pattern-matching approach is fragile and should be considered temporary.
     * 
     * Current limitations:
     * - If backend error messages change wording, translations fail silently
     * - Cannot handle errors that don't match the patterns
     * - Mixes presentation (translation) with error detection logic
     * - Maintenance burden: every new error requires updating regex patterns
     * 
     * Preferred future approach (when backend supports structured errors):
     * ```javascript
     * // Backend sends: { code: 'SESSION_TIMEOUT', details: {...} }
     * // Frontend translates by code:
     * if (error.code) {
     *     return i18next.t(`errors.${error.code}`, error.details);
     * }
     * ```
     * 
     * @param {string|object} message - Error message string or structured error object
     * @returns {string} Translated message
     */
    function translateStatusMessage(message) {
        // Attempt to parse JSON strings into objects
        if (typeof message === 'string') {
            try {
                const parsed = JSON.parse(message);
                if (parsed && typeof parsed === 'object') {
                    message = parsed;
                }
            } catch (e) {
                // Not valid JSON, keep as string
            }
        }

        // Support structured error objects (future-proofing)
        if (message && typeof message === 'object') {
            if (message.code && typeof message.code === 'string') {
                // Use error code for translation (preferred method)
                const translationKey = `errors.${message.code}`;
                const details = message.details || {};
                const translated = i18next.t(translationKey, details);
                
                // If translation succeeds (doesn't return the key), return it
                if (translated && translated !== translationKey) return translated;
                
                // Fallbacks if no translation available
                return message.message || details.msg || String(message);
            }
            // If object has message property, use it
            if (message.message) {
                message = message.message;
            } else {
                return String(message);
            }
        }

        if (!message || typeof message !== 'string') return message;

        // Pattern-matching fallback (temporary compatibility layer)
        // WARNING: This is fragile and will break if backend messages change
        const messageMap = [
            {
                pattern: /启动超时/i,
                translator: () => i18next.t('app.sessionTimeout')
            },
            {
                pattern: /无法连接/i,
                translator: () => i18next.t('app.websocketNotConnectedError')
            },
            {
                pattern: /Session启动失败/i,
                translator: () => i18next.t('app.sessionStartFailed')
            },
            {
                pattern: /记忆服务器.*崩溃/i,
                translator: (match) => {
                    const portMatch = match.match(/端口(\d+)/);
                    return i18next.t('app.memoryServerCrashed', { port: portMatch ? portMatch[1] : 'unknown' });
                }
            }
        ];

        for (const { pattern, translator } of messageMap) {
            if (pattern.test(message)) {
                return translator(message);
            }
        }

        return message;
    }

})();
