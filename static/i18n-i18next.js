/**
 * i18next 初始化文件
 * 使用成熟的 i18next 库管理本地化文本
 * 固定使用中文 (zh-CN)
 * 
 * 使用方式：
 * 1. 在 HTML 中引入 i18next CDN：
 *    <script src="https://cdn.jsdelivr.net/npm/i18next@23.7.6/dist/umd/i18next.min.js"></script>
 *    <script src="https://cdn.jsdelivr.net/npm/i18next-http-backend@2.4.2/dist/umd/i18nextHttpBackend.min.js"></script>
 * 2. 然后引入此文件：
 *    <script src="/static/i18n-i18next.js"></script>
 */

(function() {
    'use strict';
    
    // 固定语言为中文
    const TARGET_LANGUAGE = 'zh-CN';
    
    // 诊断函数
    window.diagnoseI18n = function() {
        console.log('=== i18next 诊断信息 ===');
        console.log('1. i18next 是否存在:', typeof i18next !== 'undefined');
        console.log('2. window.t 是否存在:', typeof window.t === 'function');
        console.log('3. window.i18n 是否存在:', typeof window.i18n !== 'undefined');
        
        if (typeof i18next !== 'undefined') {
            console.log('4. i18next.isInitialized:', i18next.isInitialized);
            console.log('5. 当前语言:', i18next.language);
            console.log('6. 支持的语言:', i18next.options?.supportedLngs);
            console.log('7. 已加载的资源:', Object.keys(i18next.store?.data || {}));
        } else {
            console.error('4. i18next 未加载！请检查 CDN 是否成功加载。');
        }
        
        // 检查页面上的 data-i18n 元素
        const elements = document.querySelectorAll('[data-i18n]');
        console.log(`8. 页面上的 data-i18n 元素数量: ${elements.length}`);
        if (elements.length > 0) {
            console.log('9. 前3个元素:');
            Array.from(elements).slice(0, 3).forEach((el, i) => {
                const key = el.getAttribute('data-i18n');
                const text = el.textContent;
                console.log(`   元素 ${i+1}: key="${key}", text="${text}"`);
            });
        }
        
        console.log('=== 诊断完成 ===');
    };
    
    // 测试翻译函数
    window.testTranslation = function(key) {
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
    
    // 检查 i18next 是否已加载
    if (typeof i18next === 'undefined') {
        console.error('[i18n] ❌ i18next is not loaded. Please include i18next CDN before this script.');
        console.log('✅ 诊断工具已加载，可以使用 window.diagnoseI18n() 来诊断问题');
        exportFallbackFunctions();
        return;
    }
    
    // 检查依赖库是否已加载
    if (typeof i18nextHttpBackend === 'undefined') {
        console.warn('[i18n] ⚠️ i18nextHttpBackend is not loaded.');
        console.log('[i18n] 💡 使用手动加载翻译文件的方式');
        initWithoutHttpBackend();
        return;
    }
    
    /**
     * 不使用 HTTP Backend，手动加载翻译文件
     */
    async function initWithoutHttpBackend() {
        console.log('[i18n] 开始手动加载翻译文件...');
        
        try {
            // 只加载中文翻译文件
            const response = await fetch(`/static/locales/${TARGET_LANGUAGE}.json`);
            if (!response.ok) {
                throw new Error(`翻译文件加载失败: ${response.status}`);
            }
            
            const translations = await response.json();
            const resources = {
                [TARGET_LANGUAGE]: {
                    translation: translations
                }
            };
            
            console.log(`[i18n] ✅ ${TARGET_LANGUAGE} 翻译文件加载成功`);
            
            // 初始化 i18next
            i18next.init({
                lng: TARGET_LANGUAGE,
                fallbackLng: TARGET_LANGUAGE,
                supportedLngs: [TARGET_LANGUAGE],
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
            }, function(err, t) {
                if (err) {
                    console.error('[i18n] 初始化失败:', err);
                    exportFallbackFunctions();
                    return;
                }
                
                console.log('[i18n] ✅ 初始化成功（手动加载模式）');
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
        
        window.t = function(key, params = {}) {
            console.warn('[i18n] Fallback t() called with key:', key);
            return key;
        };
        
        window.i18n = {
            isInitialized: false,
            language: TARGET_LANGUAGE,
            store: { data: {} }
        };
        
        window.updatePageTexts = function() {
            console.warn('[i18n] Fallback updatePageTexts() called - no-op');
        };
        
        window.updateLive2DDynamicTexts = function() {
            console.warn('[i18n] Fallback updateLive2DDynamicTexts() called - no-op');
        };
    }
    
    // 初始化 i18next
    console.log('[i18n] 开始初始化 i18next...');
    console.log('[i18n] 固定语言: 中文 (zh-CN)');
    
    try {
        i18next
            .use(i18nextHttpBackend)
            .init({
                lng: TARGET_LANGUAGE,
                fallbackLng: TARGET_LANGUAGE,
                supportedLngs: [TARGET_LANGUAGE],
                ns: ['translation'],
                defaultNS: 'translation',
                backend: {
                    loadPath: '/static/locales/{{lng}}.json',
                    parse: function(data) {
                        const parsed = JSON.parse(data);
                        return { translation: parsed };
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
            }, function(err, t) {
                if (err) {
                    console.error('[i18n] Initialization failed:', err);
                    exportFallbackFunctions();
                    return;
                }
                
                console.log('[i18n] ✅ 初始化成功！');
                console.log('[i18n] 当前语言:', i18next.language);
                
                updatePageTexts();
                window.dispatchEvent(new CustomEvent('localechange'));
                exportNormalFunctions();
            });
    } catch (error) {
        console.error('[i18n] Fatal error during initialization:', error);
        exportFallbackFunctions();
    }
    
    /**
     * 导出正常函数（初始化成功后使用）
     */
    function exportNormalFunctions() {
        // 导出翻译函数
        window.t = function(key, params = {}) {
            if (!key) return '';
            
            // 处理 providerKey 参数（与现有代码兼容）
            if (params && params.providerKey) {
                const providerKey = params.providerKey;
                const resources = i18next.getResourceBundle(i18next.language, 'translation');
                const providerNames = resources?.api?.providerNames || {};
                const providerName = providerNames[providerKey];
                params.provider = providerName || providerKey;
            }
            
            return i18next.t(key, params);
        };
        
        // 导出 i18next 实例
        window.i18n = i18next;
        
        // 导出更新函数
        window.updatePageTexts = updatePageTexts;
        window.updateLive2DDynamicTexts = updateLive2DDynamicTexts;
        window.translateStatusMessage = translateStatusMessage;
        
        // 监听语言变化（仅用于更新文本，不切换语言）
        i18next.on('languageChanged', (lng) => {
            updatePageTexts();
            updateLive2DDynamicTexts();
            window.dispatchEvent(new CustomEvent('localechange'));
        });
        
        // 确保在 DOM 加载完成后更新文本
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
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
        
        // 更新所有带有 data-i18n 属性的元素
        const elements = document.querySelectorAll('[data-i18n]');
        elements.forEach(element => {
            const key = element.getAttribute('data-i18n');
            let params = {};
            
            if (element.hasAttribute('data-i18n-params')) {
                try {
                    params = JSON.parse(element.getAttribute('data-i18n-params'));
                } catch (e) {
                    console.warn(`[i18n] Failed to parse params for ${key}:`, e);
                }
            }
            
            // 处理 providerKey 参数
            if (params.providerKey) {
                const providerKey = params.providerKey;
                const resources = i18next.getResourceBundle(i18next.language, 'translation');
                const providerNames = resources?.api?.providerNames || {};
                const providerName = providerNames[providerKey];
                params.provider = providerName || providerKey;
            }
            
            const text = i18next.t(key, params);
            
            if (text === key) {
                console.warn(`[i18n] Translation key not found: ${key}`);
            }
            
            // 特殊处理 title 标签
            if (element.tagName === 'TITLE') {
                document.title = text;
                return;
            }
            
            element.textContent = text;
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
        // 更新浮动按钮的标题
        const buttons = document.querySelectorAll('.floating-btn');
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
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            if (key && element._updateLabelText) {
                element._updateLabelText();
            }
        });
    }
    
    /**
     * 翻译状态消息
     */
    function translateStatusMessage(message) {
        if (!message || typeof message !== 'string') return message;
        
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
    
    console.log('✅ i18next 诊断工具已加载！');
    console.log('使用以下命令：');
    console.log('  - window.diagnoseI18n()      // 诊断 i18next 状态');
    console.log('  - window.testTranslation("voice.title")  // 测试翻译');
})();
