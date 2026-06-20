(function () {
    'use strict';

    const YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY = 'neko_yui_guide_chat_bridge_queue_v1';
    const YUI_GUIDE_CHAT_BRIDGE_QUEUE_LIMIT = 160;
    const TutorialVisualControllers = window.TutorialVisualControllers || {};
    const TutorialResistanceControllers = window.TutorialResistanceControllers || {};
    const ResistanceController = TutorialResistanceControllers.ResistanceController;
    const SidebarPauseController = TutorialResistanceControllers.SidebarPauseController;
    const PauseCoordinator = TutorialResistanceControllers.PauseCoordinator;
    const TutorialTerminationRouter = TutorialResistanceControllers.TutorialTerminationRouter;
    const TutorialOperationRegistry = window.TutorialOperationRegistry || {};
    const OperationRegistry = TutorialOperationRegistry.OperationRegistry;
    const TutorialSceneOrchestrator = window.TutorialSceneOrchestrator || {};
    const TutorialSettingsTourFlow = window.TutorialSettingsTourFlow || {};

    function createYuiGuideChatBridgeCommandBus(options) {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialBridgeCommandBus === 'function'
        ) {
            return window.YuiGuideCommon.createTutorialBridgeCommandBus(Object.assign({
                window: window,
                storageKey: YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY,
                queueLimit: YUI_GUIDE_CHAT_BRIDGE_QUEUE_LIMIT
            }, options || {}));
        }

        return {
            readQueue: readYuiGuideChatBridgeQueue,
            enqueue: enqueueYuiGuideChatBridgeMessage,
            post(message) {
                if (!message || typeof message !== 'object' || !message.action) {
                    return false;
                }
                enqueueYuiGuideChatBridgeMessage(message);
                let posted = false;
                const channel = options && typeof options.channelProvider === 'function'
                    ? options.channelProvider()
                    : null;
                if (channel && typeof channel.postMessage === 'function') {
                    try {
                        channel.postMessage(message);
                        posted = true;
                    } catch (error) {
                        console.warn('[YuiGuide] BroadcastChannel 转发独立聊天窗消息失败:', error);
                    }
                }
                const nativeRelay = options && typeof options.nativeRelayProvider === 'function'
                    ? options.nativeRelayProvider()
                    : null;
                if (nativeRelay && typeof nativeRelay.relayToChat === 'function') {
                    try {
                        nativeRelay.relayToChat(message);
                        posted = true;
                    } catch (error) {
                        console.warn('[YuiGuide] PC 原生转发独立聊天窗消息失败:', error);
                    }
                }
                return posted;
            }
        };
    }

    function createYuiGuideTargetGeometryRegistry() {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialTargetGeometryRegistry === 'function'
        ) {
            return window.YuiGuideCommon.createTutorialTargetGeometryRegistry();
        }

        const externalKinds = {
            'chat-capsule-input': 'capsule-input',
            'chat-input': 'input',
            'chat-history-handle': 'history',
            'chat-tool-toggle': 'tool-toggle',
            'chat-avatar-tools': 'avatar-tools',
            'chat-galgame': 'galgame',
            'chat-avatar-tool-items': 'avatar-tool-items'
        };
        return {
            resolve(key) {
                const normalizedKey = typeof key === 'string' ? key.trim() : '';
                const externalKind = externalKinds[normalizedKey] || '';
                return externalKind ? {
                    key: normalizedKey,
                    externalKind,
                    localSelectors: []
                } : null;
            },
            getExternalKind(key) {
                const entry = this.resolve(key);
                return entry ? entry.externalKind : '';
            },
            getLocalSelectors(key) {
                const entry = this.resolve(key);
                return entry ? entry.localSelectors : [];
            }
        };
    }

    function createYuiGuideChatWindowAdapter(options) {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createChatWindowAdapter === 'function'
        ) {
            return window.YuiGuideCommon.createChatWindowAdapter(options || {});
        }
        return {
            isExternalized: () => false,
            getExternalKind(targetKey) {
                const registry = options && options.registry;
                return registry && typeof registry.getExternalKind === 'function'
                    ? registry.getExternalKind(targetKey)
                    : '';
            },
            resolveTarget(targetKey) {
                return options && typeof options.resolveLocalTarget === 'function'
                    ? options.resolveLocalTarget(targetKey)
                    : null;
            },
            setSpotlight: () => false,
            setCursor: () => false,
            lockInput: () => false
        };
    }

    function createYuiGuideScopedTutorialResources() {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createScopedTutorialResources === 'function'
        ) {
            return window.YuiGuideCommon.createScopedTutorialResources({ window: window });
        }

        const timers = [];
        return {
            setTimeout(callback, delayMs) {
                const timerId = window.setTimeout(callback, delayMs);
                timers.push(timerId);
                return timerId;
            },
            clearTimeout(timerId) {
                if (!timerId) {
                    return;
                }
                window.clearTimeout(timerId);
                const index = timers.indexOf(timerId);
                if (index !== -1) {
                    timers.splice(index, 1);
                }
            },
            destroy() {
                while (timers.length) {
                    window.clearTimeout(timers.pop());
                }
            }
        };
    }

    function readYuiGuideChatBridgeQueue() {
        try {
            const raw = window.localStorage && window.localStorage.getItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
        } catch (_) {
            return [];
        }
    }

    function enqueueYuiGuideChatBridgeMessage(message) {
        if (!message || typeof message !== 'object' || !message.action) {
            return;
        }
        try {
            const queue = readYuiGuideChatBridgeQueue();
            queue.push(message);
            const trimmed = queue.slice(-YUI_GUIDE_CHAT_BRIDGE_QUEUE_LIMIT);
            window.localStorage.setItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY, JSON.stringify(trimmed));
        } catch (error) {
            console.warn('[YuiGuide] 缓存教程聊天消息失败:', error);
        }
    }

    function postYuiGuideChatBridgeMessage(channel, message) {
        if (!message || typeof message !== 'object' || !message.action) {
            return false;
        }
        enqueueYuiGuideChatBridgeMessage(message);
        if (!channel || typeof channel.postMessage !== 'function') {
            return false;
        }
        channel.postMessage(message);
        return true;
    }

    function translateGuideText(textKey, fallbackText) {
        const normalizedKey = typeof textKey === 'string' ? textKey.trim() : '';
        const normalizedFallback = typeof fallbackText === 'string' ? fallbackText : '';
        if (!normalizedKey || typeof window.t !== 'function') {
            return normalizedFallback;
        }

        try {
            const translated = window.t(normalizedKey);
            if (typeof translated === 'string' && translated.trim() && translated !== normalizedKey) {
                return translated;
            }
        } catch (_) {}

        return normalizedFallback;
    }

    function normalizeGuideLocale(locale) {
        const current = String(locale || '').trim().toLowerCase();
        if (!current || current === 'auto') {
            return 'zh';
        }

        if (current.indexOf('ja') === 0) return 'ja';
        if (current.indexOf('en') === 0) return 'en';
        if (current.indexOf('es') === 0) return 'es';
        if (current.indexOf('ko') === 0) return 'ko';
        if (current.indexOf('pt') === 0) return 'pt';
        if (current.indexOf('ru') === 0) return 'ru';
        return 'zh';
    }

    function resolveGuidePreferredLanguage() {
        const candidates = [
            window.i18n && window.i18n.language,
            window.localStorage && window.localStorage.getItem('i18nextLng'),
            document && document.documentElement && document.documentElement.lang,
            navigator && navigator.language,
            window.localStorage && window.localStorage.getItem('locale')
        ];

        for (let index = 0; index < candidates.length; index += 1) {
            const candidate = String(candidates[index] || '').trim();
            if (!candidate || candidate.toLowerCase() === 'auto') {
                continue;
            }

            const lowered = candidate.toLowerCase();
            if (lowered.indexOf('ja') === 0) return 'ja';
            if (lowered.indexOf('en') === 0) return 'en';
            if (lowered.indexOf('ko') === 0) return 'ko';
            if (lowered.indexOf('ru') === 0) return 'ru';
            if (lowered.indexOf('zh-tw') === 0 || lowered.indexOf('zh-hk') === 0 || lowered.indexOf('zh-hant') === 0) {
                return 'zh-TW';
            }
            if (lowered.indexOf('zh') === 0) {
                return 'zh-CN';
            }
        }

        return '';
    }

    function isGuideI18nReady() {
        const i18nInstance = window.i18n;
        return typeof window.t === 'function' && !!(i18nInstance && i18nInstance.isInitialized);
    }

    function waitForGuideI18nReady(timeoutMs) {
        const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 5000;
        if (isGuideI18nReady()) {
            return Promise.resolve(true);
        }

        return new Promise((resolve) => {
            let settled = false;
            let timeoutId = 0;
            let pollId = 0;

            const finish = (ready) => {
                if (settled) {
                    return;
                }
                settled = true;
                if (timeoutId) {
                    window.clearTimeout(timeoutId);
                    timeoutId = 0;
                }
                if (pollId) {
                    window.clearInterval(pollId);
                    pollId = 0;
                }
                window.removeEventListener('localechange', handleLocaleReady);
                resolve(!!ready);
            };

            const handleLocaleReady = () => {
                if (isGuideI18nReady()) {
                    finish(true);
                }
            };

            pollId = window.setInterval(() => {
                if (isGuideI18nReady()) {
                    finish(true);
                }
            }, 120);
            timeoutId = window.setTimeout(() => {
                finish(isGuideI18nReady());
            }, normalizedTimeoutMs);

            window.addEventListener('localechange', handleLocaleReady);
        });
    }

    async function syncGuideI18nLanguage(timeoutMs) {
        await waitForGuideI18nReady(timeoutMs);

        const targetLanguage = resolveGuidePreferredLanguage();
        const currentLanguage = window.i18n && typeof window.i18n.language === 'string'
            ? window.i18n.language
            : '';

        if (!targetLanguage || !currentLanguage || typeof window.changeLanguage !== 'function') {
            return;
        }

        if (targetLanguage === currentLanguage) {
            return;
        }

        try {
            await window.changeLanguage(targetLanguage);
            await waitForGuideI18nReady(timeoutMs);
        } catch (error) {
            console.warn('[YuiGuide] 同步引导语言失败:', targetLanguage, error);
        }
    }

    function resolveGuideLocale() {
        const candidates = [
            window.i18n && window.i18n.language,
            window.localStorage && window.localStorage.getItem('i18nextLng'),
            document && document.documentElement && document.documentElement.lang,
            navigator && navigator.language,
            window.localStorage && window.localStorage.getItem('locale')
        ];

        for (let index = 0; index < candidates.length; index += 1) {
            const candidate = String(candidates[index] || '').trim();
            if (!candidate || candidate.toLowerCase() === 'auto') {
                continue;
            }
            return normalizeGuideLocale(candidate);
        }

        return 'zh';
    }

    function guideSpeechLang() {
        const locale = resolveGuideLocale();
        if (locale === 'ja') return 'ja-JP';
        if (locale === 'en') return 'en-US';
        if (locale === 'es') return 'es-ES';
        if (locale === 'ko') return 'ko-KR';
        if (locale === 'pt') return 'pt-PT';
        if (locale === 'ru') return 'ru-RU';
        return 'zh-CN';
    }

    function resolveGuideAudioLocale(locale) {
        const candidates = locale
            ? [locale]
            : [
                window.i18n && window.i18n.language,
                window.localStorage && window.localStorage.getItem('i18nextLng'),
                document && document.documentElement && document.documentElement.lang,
                navigator && navigator.language,
                window.localStorage && window.localStorage.getItem('locale')
            ];

        for (let index = 0; index < candidates.length; index += 1) {
            const candidate = String(candidates[index] || '').trim().toLowerCase();
            if (!candidate || candidate === 'auto') {
                continue;
            }
            if (candidate.indexOf('ja') === 0) return 'ja';
            if (candidate.indexOf('en') === 0) return 'en';
            if (candidate.indexOf('ko') === 0) return 'ko';
            if (candidate.indexOf('ru') === 0) return 'ru';
            if (candidate.indexOf('zh') === 0) return 'zh';
            return 'en';
        }

        return 'en';
    }

    const AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY = 'neko_avatar_floating_guide_usage_v1';
    const YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY = 'neko_yui_guide_external_chat_cursor_screen_point_v1';

    function readAvatarFloatingGuideUsageState() {
        try {
            const raw = window.localStorage && window.localStorage.getItem(AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY);
            return raw ? JSON.parse(raw) || {} : {};
        } catch (_) {
            return {};
        }
    }

    function writeAvatarFloatingGuideUsageState(patch) {
        if (!patch || typeof patch !== 'object') {
            return;
        }
        try {
            const next = Object.assign({}, readAvatarFloatingGuideUsageState(), patch, {
                updatedAt: Date.now()
            });
            window.localStorage.setItem(AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY, JSON.stringify(next));
        } catch (_) {}
    }

    function markAvatarFloatingGuideUsage(key) {
        const normalizedKey = typeof key === 'string' ? key.trim() : '';
        if (!normalizedKey) {
            return;
        }
        const patch = {};
        patch[normalizedKey] = true;
        patch[normalizedKey + 'At'] = Date.now();
        writeAvatarFloatingGuideUsageState(patch);
    }

    function hasAvatarFloatingGuideUsage(key) {
        const state = readAvatarFloatingGuideUsageState();
        return !!(state && state[key]);
    }

    if (!window.__avatarFloatingGuideUsageListenersInstalled) {
        window.__avatarFloatingGuideUsageListenersInstalled = true;
        window.addEventListener('live2d-mic-toggle', function (event) {
            if (event && event.detail && event.detail.active === true) {
                markAvatarFloatingGuideUsage('voiceUsed');
            }
        }, true);
        window.addEventListener('live2d-screen-toggle', function () {
            markAvatarFloatingGuideUsage('screenShareButtonUsed');
        }, true);
        window.addEventListener('click', function (event) {
            const target = event && event.target && typeof event.target.closest === 'function'
                ? event.target
                : null;
            if (!target) {
                return;
            }
            if (target.closest('[id$="-btn-agent"], [id$="-toggle-agent-master"], [id$="-toggle-agent-keyboard"], [id$="-toggle-agent-browser"], [id$="-toggle-agent-user-plugin"]')) {
                markAvatarFloatingGuideUsage('agentUsed');
            }
            if (target.closest('[class*="trigger-icon-screen"], [id$="-popup-screen"]')) {
                markAvatarFloatingGuideUsage('screenSourcePopupUsed');
            }
            if (target.closest('[id$="-toggle-proactive-chat"]')) {
                markAvatarFloatingGuideUsage('proactiveChatOpened');
            }
        }, true);
    }

    const DEFAULT_USER_CURSOR_REVEAL_DISTANCE = 14;
    const DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS = 160;
    const DEFAULT_USER_CURSOR_REVEAL_MOVES = 2;
    const DEFAULT_STEP_DELAY_MS = 120;
    const DEFAULT_SCENE_SETTLE_MS = 260;
    const DEFAULT_CURSOR_DURATION_MS = 520;
    const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420;
    const DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS = 420;
    const DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS = 320;
    const DAY6_PLUGIN_SIDE_PANEL_ACTION_TIMEOUT_MS = 1200;
    const DAY6_PLUGIN_SIDE_PANEL_DASHBOARD_WAIT_MS = 900;
    const DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS = 900;
    const AVATAR_STAND_IN_MODEL_FADE_MS = 1000;
    const INTRO_GREETING_REPLY_TEXT = '微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡，未来的日子请多关照喵！我把关于这里的一切都写进新手指南里啦！就当作是我们相遇的第一份小礼物，请查收吧！';
    const INTRO_GREETING_REPLY_TEXT_KEY = 'tutorial.yuiGuide.lines.introGreetingReply';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT = '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard';
    const PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT = '浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。';
    const PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY = 'tutorial.yuiGuide.lines.pluginDashboardPopupBlocked';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1 = '不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2 = '这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。';
    const TAKEOVER_SETTINGS_DETAIL_TEXT = TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1 + TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2;
    const TAKEOVER_SETTINGS_DETAIL_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetail';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2';
    const INTRO_ACTIVATION_HINT_KEY = 'tutorial.yuiGuide.lines.introActivationHint';
    const INTRO_ACTIVATION_HINT = '点一下这里，我就能开始说话啦～';
    const DEFAULT_SPOTLIGHT_PADDING = 6;
    const PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X = 18;
    const PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y = 10;
    const NARRATION_RESUME_BACKTRACK_MS = 320;
    const NARRATION_RESUME_MIN_REMAINING_MS = 1400;
    const PLUGIN_DASHBOARD_WINDOW_NAME = 'plugin_dashboard';
    const PLUGIN_DASHBOARD_HANDOFF_EVENT = 'neko:yui-guide:plugin-dashboard:start';
    const PLUGIN_DASHBOARD_READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready';
    const PLUGIN_DASHBOARD_DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done';
    const PLUGIN_DASHBOARD_TERMINATE_EVENT = 'neko:yui-guide:plugin-dashboard:terminate';
    const PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT = 'neko:yui-guide:plugin-dashboard:narration-finished';
    const PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request';
    const PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack';
    const DESKTOP_PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT = 'neko:yui-guide:desktop-interrupt-ack';
    const DESKTOP_PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT = 'neko:yui-guide:desktop-narration-finished';
    const DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT = 'neko:yui-guide:desktop-skip-request';
    const PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request';
    const DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME = 'ATLS';
    const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/';
    const RETURN_PETAL_SEQUENCE_URL = '/static/assets/tutorial/petals/yui-guide-petal-transition.webp';
    function getYuiGuideDailyGuide(day) {
        const normalizedDay = Number(day);
        const registry = window.YuiGuideDailyGuides || {};
        return registry[normalizedDay] || null;
    }

    function collectGuideAudioFilesByKey() {
        const registry = window.YuiGuideDailyGuides || {};
        const result = {};
        Object.keys(registry).forEach((day) => {
            const guide = registry[day];
            if (guide && guide.audioFilesByKey) {
                Object.assign(result, guide.audioFilesByKey);
            }
        });
        return result;
    }

    const DAY1_HOME_GUIDE = getYuiGuideDailyGuide(1) || {};
    const GUIDE_AUDIO_FILES_BY_KEY = Object.freeze(collectGuideAudioFilesByKey());
    const GUIDE_AUDIO_FILE_OVERRIDES_BY_KEY = Object.freeze(Object.assign({}, DAY1_HOME_GUIDE.audioFileOverridesByKey || {}));

    function guideAudioSrc(key) {
        const files = key
            ? (GUIDE_AUDIO_FILE_OVERRIDES_BY_KEY[key] || GUIDE_AUDIO_FILES_BY_KEY[key] || null)
            : null;
        if (!files) {
            return '';
        }

        // 当前 locale 没有对应语音文件时（如 es / pt 等未提供录音的语言），
        // 默认 fallback 是英文，避免回退到中文给非中文用户带来违和感。
        const locale = resolveGuideAudioLocale();
        const hasLocaleFile = Object.prototype.hasOwnProperty.call(files, locale);
        const fileName = hasLocaleFile ? files[locale] : (files.en || '');
        const fileLocale = hasLocaleFile ? locale : 'en';
        return fileName ? (GUIDE_AUDIO_BASE_URL + fileLocale + '/' + encodeURIComponent(fileName)) : '';
    }

    function shouldGuideAudioDriveMouth(voiceKey) {
        const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
        return !!normalizedKey;
    }

    const TAKEOVER_CAPTURE_SELECTORS = Object.freeze({
        voiceControl: '[alt="语音控制"]',
        catPaw: '[alt="猫爪"]',
        agentMaster: '#${p}-toggle-agent-master',
        keyboardControl: '#${p}-toggle-agent-keyboard',
        userPlugin: '#${p}-toggle-agent-user-plugin',
        managementPanel: 'div#neko-sidepanel-action-agent-user-plugin-management-panel'
    });

    const AVATAR_FLOATING_GUIDE_INTERRUPT_STEP = Object.freeze({
        id: 'avatar_floating_guide_interruptible',
        performance: Object.freeze({
            interruptible: true
        }),
        interrupts: Object.freeze({
            mode: 'theatrical_abort',
            threshold: 3,
            throttleMs: 500,
            resetOnStepAdvance: false
        })
    });

    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
    }

    function fetchWithTimeout(resource, options, timeoutMs) {
        const normalizedTimeoutMs = Math.max(1000, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 5000));
        const normalizedOptions = Object.assign({}, options || {});
        if (typeof AbortController === 'function') {
            const controller = new AbortController();
            const timeoutId = window.setTimeout(() => controller.abort(), normalizedTimeoutMs);
            normalizedOptions.signal = controller.signal;
            return fetch(resource, normalizedOptions).finally(() => {
                window.clearTimeout(timeoutId);
            });
        }

        return Promise.race([
            fetch(resource, normalizedOptions),
            new Promise((resolve, reject) => {
                window.setTimeout(() => reject(new Error('fetch_timeout')), normalizedTimeoutMs);
            })
        ]);
    }

    function resolveWithTimeout(promise, timeoutMs, fallbackValue, label) {
        const normalizedTimeoutMs = Math.max(300, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 3000));
        let timeoutId = 0;
        return Promise.race([
            Promise.resolve(promise).then(
                (value) => ({ status: 'fulfilled', value: value }),
                (error) => ({ status: 'rejected', error: error })
            ),
            new Promise((resolve) => {
                timeoutId = window.setTimeout(() => {
                    timeoutId = 0;
                    resolve({ status: 'timeout' });
                }, normalizedTimeoutMs);
            })
        ]).then((result) => {
            if (timeoutId) {
                window.clearTimeout(timeoutId);
            }
            if (result.status === 'timeout') {
                if (label) {
                    console.warn('[YuiGuide] 等待超时，使用兜底:', label);
                }
                return fallbackValue;
            }
            if (result.status === 'rejected') {
                throw result.error;
            }
            return result.value;
        });
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    const HOME_TUTORIAL_PLATFORM_PROFILES = Object.freeze({
        windows: Object.freeze({
            supportsExternalChat: true,
            supportsSystemTrayHint: true,
            supportsPluginDashboardWindow: true,
            pointerProfile: 'mouse',
            browserSkipHitPadding: 28,
            electronSkipHitPadding: 20,
            browserSkipForwardingTolerance: 10,
            electronSkipForwardingToleranceRatio: 0.2,
            electronSkipForwardingToleranceMin: 4
        }),
        macos: Object.freeze({
            supportsExternalChat: true,
            supportsSystemTrayHint: true,
            supportsPluginDashboardWindow: true,
            pointerProfile: 'trackpad',
            browserSkipHitPadding: 36,
            electronSkipHitPadding: 28,
            browserSkipForwardingTolerance: 14,
            electronSkipForwardingToleranceRatio: 0.25,
            electronSkipForwardingToleranceMin: 6
        }),
        linux: Object.freeze({
            supportsExternalChat: true,
            supportsSystemTrayHint: true,
            supportsPluginDashboardWindow: true,
            pointerProfile: 'mouse',
            browserSkipHitPadding: 44,
            electronSkipHitPadding: 32,
            browserSkipForwardingTolerance: 18,
            electronSkipForwardingToleranceRatio: 0.35,
            electronSkipForwardingToleranceMin: 8
        }),
        web: Object.freeze({
            supportsExternalChat: false,
            supportsSystemTrayHint: false,
            supportsPluginDashboardWindow: true,
            pointerProfile: 'pointer',
            browserSkipHitPadding: 18,
            electronSkipHitPadding: 18,
            browserSkipForwardingTolerance: 6,
            electronSkipForwardingToleranceRatio: 0.2,
            electronSkipForwardingToleranceMin: 4
        })
    });

    function detectHomeTutorialPlatform() {
        const rawPlatform = (
            (navigator.userAgentData && navigator.userAgentData.platform)
            || navigator.platform
            || navigator.userAgent
            || ''
        ).toString().toLowerCase();
        if (rawPlatform.indexOf('mac') >= 0) return 'macos';
        if (rawPlatform.indexOf('win') >= 0) return 'windows';
        if (rawPlatform.indexOf('linux') >= 0 || rawPlatform.indexOf('x11') >= 0) return 'linux';
        return 'web';
    }

    function createHomeTutorialPlatformCapabilities(overrides) {
        const normalizedOverrides = overrides && typeof overrides === 'object' ? overrides : {};
        const platform = typeof normalizedOverrides.platform === 'string' && normalizedOverrides.platform.trim()
            ? normalizedOverrides.platform.trim().toLowerCase()
            : detectHomeTutorialPlatform();
        const profile = HOME_TUTORIAL_PLATFORM_PROFILES[platform] || HOME_TUTORIAL_PLATFORM_PROFILES.web;
        const hasElectronBounds = !!(
            window.nekoPetDrag
            && typeof window.nekoPetDrag.getBounds === 'function'
        );
        const windowBoundsSource = hasElectronBounds ? 'electron-window-bounds' : 'browser-screen-origin';
        const preferredSkipHitPadding = windowBoundsSource === 'electron-window-bounds'
            ? profile.electronSkipHitPadding
            : profile.browserSkipHitPadding;

        return Object.freeze({
            version: 1,
            platform: HOME_TUTORIAL_PLATFORM_PROFILES[platform] ? platform : 'web',
            windowBoundsSource: windowBoundsSource,
            supportsExternalChat: normalizedOverrides.supportsExternalChat === true || (
                normalizedOverrides.supportsExternalChat !== false && profile.supportsExternalChat
            ),
            supportsSystemTrayHint: normalizedOverrides.supportsSystemTrayHint === true || (
                normalizedOverrides.supportsSystemTrayHint !== false && profile.supportsSystemTrayHint
            ),
            supportsPluginDashboardWindow: normalizedOverrides.supportsPluginDashboardWindow === true || (
                normalizedOverrides.supportsPluginDashboardWindow !== false && profile.supportsPluginDashboardWindow
            ),
            pointerProfile: typeof normalizedOverrides.pointerProfile === 'string' && normalizedOverrides.pointerProfile.trim()
                ? normalizedOverrides.pointerProfile.trim()
                : profile.pointerProfile,
            preferredSkipHitPadding: preferredSkipHitPadding,
            getSkipHitPadding: function (boundsSource) {
                return boundsSource === 'electron-window-bounds'
                    ? profile.electronSkipHitPadding
                    : profile.browserSkipHitPadding;
            },
            getSkipForwardingTolerance: function (screenRect) {
                const rect = screenRect && typeof screenRect === 'object' ? screenRect : {};
                const coordinateSpace = String(rect.coordinateSpace || windowBoundsSource || '').toLowerCase();
                const rawPadding = Number(rect.hitPadding);
                const basePadding = Number.isFinite(rawPadding) ? Math.max(0, rawPadding) : preferredSkipHitPadding;
                if (coordinateSpace === 'electron-window-bounds') {
                    return Math.max(
                        profile.electronSkipForwardingToleranceMin,
                        Math.round(basePadding * profile.electronSkipForwardingToleranceRatio)
                    );
                }
                return profile.browserSkipForwardingTolerance;
            }
        });
    }

    const HOME_TUTORIAL_PLATFORM_CAPABILITIES_API = Object.freeze({
        create: createHomeTutorialPlatformCapabilities,
        detectPlatform: detectHomeTutorialPlatform,
        profiles: HOME_TUTORIAL_PLATFORM_PROFILES
    });

    window.homeTutorialPlatformCapabilities = window.homeTutorialPlatformCapabilities || HOME_TUTORIAL_PLATFORM_CAPABILITIES_API;

    const HOME_TUTORIAL_EXPERIENCE_METRICS_STORAGE_KEY = 'neko_home_tutorial_experience_metrics_v1';
    const HOME_TUTORIAL_EXPERIENCE_METRICS_LIMIT = 300;

    function readHomeTutorialExperienceMetrics() {
        try {
            const raw = window.localStorage && window.localStorage.getItem(HOME_TUTORIAL_EXPERIENCE_METRICS_STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function writeHomeTutorialExperienceMetrics(events) {
        if (!window.localStorage) {
            return false;
        }

        try {
            const boundedEvents = (Array.isArray(events) ? events : [])
                .slice(-HOME_TUTORIAL_EXPERIENCE_METRICS_LIMIT);
            window.localStorage.setItem(
                HOME_TUTORIAL_EXPERIENCE_METRICS_STORAGE_KEY,
                JSON.stringify(boundedEvents)
            );
            return true;
        } catch (_) {
            return false;
        }
    }

    function createHomeTutorialExperienceMetrics() {
        return Object.freeze({
            storageKey: HOME_TUTORIAL_EXPERIENCE_METRICS_STORAGE_KEY,
            record: function (type, detail) {
                const eventType = typeof type === 'string' ? type.trim() : '';
                if (!eventType) {
                    return null;
                }

                const event = Object.assign({
                    type: eventType,
                    timestamp: Date.now()
                }, detail && typeof detail === 'object' ? detail : {});
                const current = readHomeTutorialExperienceMetrics();
                current.push(event);
                writeHomeTutorialExperienceMetrics(current);

                try {
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:experience-metric', {
                        detail: event
                    }));
                } catch (_) {}

                return event;
            },
            list: function () {
                return readHomeTutorialExperienceMetrics();
            },
            clear: function () {
                return writeHomeTutorialExperienceMetrics([]);
            },
            export: function () {
                return JSON.stringify(readHomeTutorialExperienceMetrics(), null, 2);
            }
        });
    }

    window.homeTutorialExperienceMetrics = window.homeTutorialExperienceMetrics || createHomeTutorialExperienceMetrics();

    const GUIDE_NARRATION_TIMELINES_BY_KEY = Object.freeze({
        intro_greeting_reply: Object.freeze({
            fallbackDurationMs: 15020,
            cues: Object.freeze({
                showIntroGiftHeart: Object.freeze({
                    at: 57 / 78,
                    atByLocale: Object.freeze({
                        zh: 57 / 78,
                        ja: 88 / 117,
                        en: 211 / 283,
                        ko: 88 / 127,
                        ru: 188 / 270
                    })
                })
            })
        }),
        takeover_settings_peek_intro: Object.freeze({
            fallbackDurationMs: 11877,
            cues: Object.freeze({
                openSettingsPanel: Object.freeze({ at: 9000 / 11877 })
            })
        }),
        takeover_settings_peek_detail: Object.freeze({
            fallbackDurationMs: 13923,
            cues: Object.freeze({
                showSecondLine: Object.freeze({ at: 7450 / 13923 })
            })
        }),
        takeover_return_control: Object.freeze({
            fallbackDurationMs: 11938,
            cues: Object.freeze({
                returnPetalTransition: Object.freeze({ at: 0.7 })
            })
        })
    });

    const GUIDE_AUDIO_DURATIONS_BY_KEY = Object.freeze({
        avatar_floating_day2_intro: Object.freeze({ zh: 12000, ja: 18286, en: 14968, ko: 17580, ru: 16196 }),
        avatar_floating_day2_intro_voice_used: Object.freeze({ zh: 20208, ja: 21682, en: 19931, ko: 25992, ru: 19566 }),
        avatar_floating_day2_wrap: Object.freeze({ zh: 8500, ja: 9874, en: 8882, ko: 9535, ru: 8934 }),
        avatar_floating_day2_wrap_companion: Object.freeze({ zh: 7920, ja: 10893, en: 10371, ko: 9404, ru: 9639 }),
        avatar_floating_day2_wrap_intro: Object.freeze({ zh: 2840, ja: 2534, en: 2664, ko: 2482, ru: 2664 }),
        avatar_floating_day3_avatar_tools_intro: Object.freeze({ zh: 4400, ja: 5904, en: 4336, ko: 6060, ru: 5120 }),
        avatar_floating_day3_avatar_tools_props: Object.freeze({ zh: 13320, ja: 14655, en: 14681, ko: 14420, ru: 14942 }),
        avatar_floating_day3_galgame_choices: Object.freeze({ zh: 9800, ja: 12382, en: 9639, ko: 11755, ru: 12931 }),
        avatar_floating_day3_galgame_intro: Object.freeze({ zh: 6640, ja: 9117, en: 7262, ko: 8803, ru: 7393 }),
        avatar_floating_day3_intro: Object.freeze({ zh: 12960, ja: 17711, en: 14054, ko: 17241, ru: 16535 }),
        avatar_floating_day3_wrap_intro: Object.freeze({ zh: 5700, ja: 6531, en: 5877, ko: 7210, ru: 6896 }),
        avatar_floating_day3_wrap_ready: Object.freeze({ zh: 5840, ja: 7993, en: 6374, ko: 7366, ru: 7210 }),
        avatar_floating_day4_chat_settings: Object.freeze({ zh: 11880, ja: 13636, en: 12382, ko: 14472, ru: 12016 }),
        avatar_floating_day4_gaze_follow: Object.freeze({ zh: 9780, ja: 13401, en: 9352, ko: 10971, ru: 10762 }),
        avatar_floating_day4_intro: Object.freeze({ zh: 8380, ja: 9456, en: 7497, ko: 9822, ru: 8699 }),
        avatar_floating_day4_model_behavior: Object.freeze({ zh: 13600, ja: 15752, en: 16144, ko: 14785, ru: 14524 }),
        avatar_floating_day4_model_lock: Object.freeze({ zh: 11544, ja: 14968, en: 13296, ko: 14602, ru: 11598 }),
        avatar_floating_day4_privacy_mode: Object.freeze({ zh: 14880, ja: 15386, en: 14263, ko: 14472, ru: 16091 }),
        avatar_floating_day4_return_home: Object.freeze({ zh: 10940, ja: 14472, en: 13949, ko: 13819, ru: 13479 }),
        avatar_floating_day4_wrap: Object.freeze({ zh: 13940, ja: 17606, en: 16326, ko: 19670, ru: 18495 }),
        avatar_floating_day5_character_panic: Object.freeze({ zh: 10760, ja: 14367, en: 13427, ko: 15438, ru: 11206 }),
        avatar_floating_day5_character_settings: Object.freeze({ zh: 11320, ja: 12591, en: 11442, ko: 14002, ru: 10919 }),
        avatar_floating_day5_memory_entry: Object.freeze({ zh: 13340, ja: 18939, en: 14968, ko: 16353, ru: 14446 }),
        avatar_floating_day5_wrap: Object.freeze({ zh: 16680, ja: 17842, en: 17528, ko: 17424, ru: 16640 }),
        avatar_floating_day6_intro: Object.freeze({ zh: 11580, ja: 15255, en: 12382, ko: 14367, ru: 10423 }),
        avatar_floating_day6_plugin_dashboard: Object.freeze({ zh: 9400, ja: 15334, en: 11807, ko: 12565, ru: 13009 }),
        avatar_floating_day6_plugin_side_panel: Object.freeze({ zh: 3780, ja: 6374, en: 6243, ko: 7131, ru: 5721 }),
        avatar_floating_day6_status_master: Object.freeze({ zh: 4020, ja: 6374, en: 5538, ko: 5904, ru: 5721 }),
        avatar_floating_day6_task_hud: Object.freeze({ zh: 8640, ja: 9717, en: 8202, ko: 8751, ru: 8934 }),
        avatar_floating_day6_task_hud_control: Object.freeze({ zh: 9540, ja: 12695, en: 11206, ko: 12042, ru: 12147 }),
        avatar_floating_day6_wrap: Object.freeze({ zh: 11340, ja: 15438, en: 13949, ko: 16326, ru: 12330 }),
        avatar_floating_day6_wrap_cleanup: Object.freeze({ zh: 4920, ja: 6740, en: 5407, ko: 7366, ru: 5538 }),
        avatar_floating_day7_memory_control: Object.freeze({ zh: 13000, ja: 17162, en: 15203, ko: 16274, ru: 16666 }),
        avatar_floating_day7_memory_review: Object.freeze({ zh: 15500, ja: 21708, en: 19095, ko: 20219, ru: 17241 }),
        avatar_floating_day7_wrap: Object.freeze({ zh: 22100, ja: 23301, en: 26958, ko: 25443, ru: 25469 }),
        day1_capsule_drag_hint: Object.freeze({ zh: 6936, ja: 11076, en: 9900, ko: 10736, ru: 10423 }),
        day1_history_handle: Object.freeze({ zh: 5580, ja: 7993, en: 5460, ko: 6792, ru: 5877 }),
        day1_screen_entry: Object.freeze({ zh: 6080, ja: 7157, en: 5172, ko: 6896, ru: 6713 }),
        day1_screen_entry_invite: Object.freeze({ zh: 7440, ja: 11259, en: 11259, ko: 10475, ru: 9587 }),
        interrupt_angry_exit: Object.freeze({ zh: 8660, ja: 11206, en: 11990, ko: 9953, ru: 10736 }),
        interrupt_resist_light_1: Object.freeze({ zh: 3920, ja: 4989, en: 3396, ko: 5460, ru: 4101 }),
        interrupt_resist_light_3: Object.freeze({ zh: 3500, ja: 6713, en: 4650, ko: 5825, ru: 5590 }),
        intro_basic: Object.freeze({ zh: 12576, ja: 19984, en: 13166, ko: 17424, ru: 17032 }),
        intro_greeting_reply: Object.freeze({ zh: 15680, ja: 22021, en: 22596, ko: 19957, ru: 18991 }),
        takeover_capture_cursor: Object.freeze({ zh: 22580, ja: 28238, en: 24712, ko: 23040, ru: 25966 }),
        takeover_return_control: Object.freeze({ zh: 7500, ja: 9822, en: 9770, ko: 11024, ru: 7993 }),
        takeover_settings_peek_detail: Object.freeze({ zh: 9540, ja: 11337, en: 12042, ko: 11206, ru: 10240 }),
        takeover_settings_peek_detail_part_2: Object.freeze({ zh: 6800, ja: 8829, en: 9169, ko: 9352, ru: 8098 }),
        takeover_settings_peek_intro: Object.freeze({ zh: 7680, ja: 8882, en: 10841, ko: 10240, ru: 11729 })
    });

    function getGuideAudioCueConfig(voiceKey) {
        const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
        if (!normalizedKey) {
            return null;
        }

        return GUIDE_NARRATION_TIMELINES_BY_KEY[normalizedKey] || null;
    }

    function getGuideAudioDurationConfig(voiceKey) {
        const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
        if (!normalizedKey) {
            return null;
        }

        return GUIDE_AUDIO_DURATIONS_BY_KEY[normalizedKey] || null;
    }

    function formatGuideDebugText(textKey, text) {
        const content = typeof text === 'string' ? text.trim() : '';
        return content;
    }

    function unionRects(rects) {
        const items = Array.isArray(rects) ? rects.filter(Boolean) : [];
        if (items.length === 0) {
            return null;
        }

        const left = Math.min.apply(null, items.map((rect) => rect.left));
        const top = Math.min.apply(null, items.map((rect) => rect.top));
        const right = Math.max.apply(null, items.map((rect) => rect.right));
        const bottom = Math.max.apply(null, items.map((rect) => rect.bottom));
        const width = Math.max(0, right - left);
        const height = Math.max(0, bottom - top);

        if (width <= 0 || height <= 0) {
            return null;
        }

        return {
            left: left,
            top: top,
            right: right,
            bottom: bottom,
            width: width,
            height: height
        };
    }

    function estimateSpeechDurationMs(text) {
        const message = typeof text === 'string' ? text.trim() : '';
        if (!message) {
            return 0;
        }

        return clamp(Math.round(message.length * 280), 2200, 24000);
    }

    function estimateGuideChatStreamDurationMs(text) {
        const units = Array.from(typeof text === 'string' ? text.trim() : '');
        if (units.length === 0) {
            return 0;
        }

        return clamp(Math.round(units.length * 40), 720, 9600);
    }

    async function resumeKnownAudioContexts() {
        const tasks = [];

        if (window.AM && typeof window.AM.unlock === 'function') {
            try {
                window.AM.unlock();
            } catch (_) {}
        }

        const playerContext = window.appState && window.appState.audioPlayerContext;
        if (playerContext && playerContext.state === 'suspended' && typeof playerContext.resume === 'function') {
            tasks.push(playerContext.resume().catch(() => {}));
        }

        if (window.lanlanAudioContext && window.lanlanAudioContext.state === 'suspended' && typeof window.lanlanAudioContext.resume === 'function') {
            tasks.push(window.lanlanAudioContext.resume().catch(() => {}));
        }

        if (tasks.length > 0) {
            await Promise.all(tasks);
        }
    }

    function normalizeVoiceLang(voice) {
        const lang = voice && typeof voice.lang === 'string' ? voice.lang.trim().toLowerCase() : '';
        return lang.replace('_', '-');
    }

    function scoreSpeechVoice(voice) {
        if (!voice) {
            return 0;
        }

        const name = typeof voice.name === 'string' ? voice.name.trim().toLowerCase() : '';
        const lang = normalizeVoiceLang(voice);
        let score = 0;

        if (lang === 'zh-cn') {
            score += 100;
        } else if (lang.indexOf('zh') === 0) {
            score += 80;
        } else if (lang === 'cmn-cn') {
            score += 90;
        }

        if (name.indexOf('chinese') >= 0 || name.indexOf('mandarin') >= 0 || name.indexOf('中文') >= 0) {
            score += 20;
        }

        if (voice.default) {
            score += 5;
        }

        return score;
    }

    class YuiGuideVoiceQueue {
        constructor() {
            this.currentUtterance = null;
            this.currentFallbackTimer = null;
            this.currentFinish = null;
            this.enabled = !!window.speechSynthesis;
            this.voicesReadyPromise = null;
            this.currentAudio = null;
            this.currentAudioMeta = null;
            this.voiceIdCache = {
                name: '',
                value: '',
                fetchedAt: 0
            };
            this.previewCache = new Map();
            this.currentMouthMotionSession = null;
            this.guideAudioContext = null;
        }

        stop() {
            const finish = this.currentFinish;
            this.stopGuideMouthMotion();

            if (this.currentFallbackTimer) {
                window.clearTimeout(this.currentFallbackTimer);
                this.currentFallbackTimer = null;
            }

            if (this.enabled && window.speechSynthesis) {
                try {
                    window.speechSynthesis.cancel();
                } catch (error) {
                    console.warn('[YuiGuide] 取消语音失败:', error);
                }
            }

            if (this.currentAudio) {
                try {
                    this.currentAudio.pause();
                    this.currentAudio.removeAttribute('src');
                    this.currentAudio.load();
                } catch (error) {
                    console.warn('[YuiGuide] 停止预览音频失败:', error);
                }
                this.currentAudio = null;
            }

            if (this.currentAudioMeta && this.currentAudioMeta.mode === 'buffer') {
                try {
                    if (this.currentAudioMeta.source) {
                        this.currentAudioMeta.source.onended = null;
                        this.currentAudioMeta.source.stop();
                        this.currentAudioMeta.source.disconnect();
                    }
                    if (this.currentAudioMeta.analyserNode) {
                        this.currentAudioMeta.analyserNode.disconnect();
                    }
                    if (this.currentAudioMeta.gainNode) {
                        this.currentAudioMeta.gainNode.disconnect();
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 停止 AudioContext 教程语音失败:', error);
                }
            }
            this.currentAudioMeta = null;

            this.currentUtterance = null;
            this.currentFinish = null;

            if (typeof finish === 'function') {
                try {
                    finish();
                } catch (_) {}
            }
        }

        destroy() {
            this.stop();
            if (this.guideAudioContext && this.guideAudioContext.state !== 'closed') {
                try {
                    const closePromise = this.guideAudioContext.close();
                    if (closePromise && typeof closePromise.catch === 'function') {
                        closePromise.catch(() => {});
                    }
                } catch (_) {}
            }
            this.guideAudioContext = null;
            if (this.previewCache && typeof this.previewCache.clear === 'function') {
                this.previewCache.clear();
            }
        }

        stopGuideMouthMotion(session) {
            const activeSession = session || this.currentMouthMotionSession;
            if (!activeSession) {
                return;
            }

            if (!session || this.currentMouthMotionSession === session) {
                this.currentMouthMotionSession = null;
            }

            try {
                if (activeSession.animationFrameId) {
                    window.cancelAnimationFrame(activeSession.animationFrameId);
                    activeSession.animationFrameId = 0;
                }
                if (activeSession.mediaSourceNode) {
                    try {
                        activeSession.mediaSourceNode.disconnect();
                    } catch (_) {}
                    activeSession.mediaSourceNode = null;
                }
                if (activeSession.analyserNode) {
                    try {
                        activeSession.analyserNode.disconnect();
                    } catch (_) {}
                    activeSession.analyserNode = null;
                }
                if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                    window.LanLan1.setMouth(0);
                }
            } catch (error) {
                console.warn('[YuiGuide] 停止教程嘴部动作失败:', error);
            }
        }

        createGuideAnalyser(context) {
            if (!context || typeof context.createAnalyser !== 'function') {
                return null;
            }

            const analyser = context.createAnalyser();
            analyser.fftSize = 2048;
            if ('smoothingTimeConstant' in analyser) {
                analyser.smoothingTimeConstant = 0.72;
            }
            return analyser;
        }

        startGuideMouthMotion(voiceKey, options) {
            if (!shouldGuideAudioDriveMouth(voiceKey)) {
                return null;
            }

            if (this.guideInterruptPresentationActive) {
                return null;
            }

            if (typeof window.requestAnimationFrame !== 'function'
                || !window.LanLan1
                || typeof window.LanLan1.setMouth !== 'function') {
                return null;
            }

            this.stopGuideMouthMotion();
            const normalizedOptions = options || {};
            const analyserNode = normalizedOptions.analyserNode || normalizedOptions.analyser || null;
            if (!analyserNode) {
                return null;
            }
            const session = {
                animationFrameId: 0,
                startedAt: performance.now(),
                lastMouthOpen: 0,
                quietFrames: 0,
                analyserNode: analyserNode,
                mediaSourceNode: normalizedOptions.mediaSourceNode || null,
                dataArray: analyserNode && Number.isFinite(analyserNode.fftSize)
                    ? new Uint8Array(analyserNode.fftSize)
                    : null
            };

            try {
                const animate = (now) => {
                    if (this.currentMouthMotionSession !== session) {
                        return;
                    }
                    session.animationFrameId = window.requestAnimationFrame(animate);
                    let target = 0;

                    if (session.analyserNode && session.dataArray) {
                        session.analyserNode.getByteTimeDomainData(session.dataArray);
                        let sum = 0;
                        for (let index = 0; index < session.dataArray.length; index += 1) {
                            const value = (session.dataArray[index] - 128) / 128;
                            sum += value * value;
                        }
                        const rms = Math.sqrt(sum / session.dataArray.length);
                        const noiseFloor = 0.022;
                        const fullOpenRms = 0.15;
                        if (rms <= noiseFloor) {
                            session.quietFrames += 1;
                            target = 0;
                        } else {
                            session.quietFrames = 0;
                            const normalizedRms = clamp((rms - noiseFloor) / (fullOpenRms - noiseFloor), 0, 1);
                            target = Math.pow(normalizedRms, 0.72) * 0.95;
                            if (target < 0.035) {
                                target = 0;
                            }
                        }
                        if (session.quietFrames >= 2) {
                            target = 0;
                        }
                    }

                    const smoothing = target > session.lastMouthOpen
                        ? 0.56
                        : (target === 0 ? 0.62 : 0.42);
                    let mouthOpen = (session.lastMouthOpen * (1 - smoothing)) + (target * smoothing);
                    if (mouthOpen < 0.025) {
                        mouthOpen = 0;
                    }
                    session.lastMouthOpen = mouthOpen;
                    window.LanLan1.setMouth(mouthOpen);
                };

                this.currentMouthMotionSession = session;
                session.animationFrameId = window.requestAnimationFrame(animate);
                return session;
            } catch (error) {
                console.warn('[YuiGuide] 启动教程嘴部动作失败:', error);
                return null;
            }
        }

        createGuideAudioElementMouthMotionNodes(audio) {
            if (!audio) {
                return null;
            }

            const context = this.getAvailableGuideAudioContext();
            if (!context || typeof context.createMediaElementSource !== 'function') {
                return null;
            }

            const analyserNode = this.createGuideAnalyser(context);
            if (!analyserNode) {
                return null;
            }

            try {
                const mediaSourceNode = context.createMediaElementSource(audio);
                mediaSourceNode.connect(analyserNode);
                analyserNode.connect(context.destination);
                return {
                    context: context,
                    analyserNode: analyserNode,
                    mediaSourceNode: mediaSourceNode
                };
            } catch (error) {
                try {
                    analyserNode.disconnect();
                } catch (_) {}
                console.warn('[YuiGuide] 创建教程音频口型分析器失败:', error);
                return null;
            }
        }

        capturePlaybackSnapshot() {
            if (this.currentAudio) {
                const currentTimeMs = Math.max(
                    0,
                    Math.round((Number.isFinite(this.currentAudio.currentTime) ? this.currentAudio.currentTime : 0) * 1000)
                );
                const durationMs = Number.isFinite(this.currentAudio.duration) && this.currentAudio.duration > 0
                    ? Math.round(this.currentAudio.duration * 1000)
                    : 0;

                return {
                    mode: 'audio',
                    voiceKey: this.currentAudioMeta && typeof this.currentAudioMeta.voiceKey === 'string'
                        ? this.currentAudioMeta.voiceKey
                        : '',
                    currentTimeMs: currentTimeMs,
                    durationMs: durationMs
                };
            }

            if (this.currentAudioMeta && this.currentAudioMeta.mode === 'buffer') {
                const context = this.currentAudioMeta.context || null;
                const startedAt = Number.isFinite(this.currentAudioMeta.startedAt)
                    ? this.currentAudioMeta.startedAt
                    : 0;
                const startOffsetMs = Number.isFinite(this.currentAudioMeta.startOffsetMs)
                    ? this.currentAudioMeta.startOffsetMs
                    : 0;
                const durationMs = Number.isFinite(this.currentAudioMeta.durationMs)
                    ? this.currentAudioMeta.durationMs
                    : 0;
                const elapsedMs = context && Number.isFinite(context.currentTime)
                    ? Math.max(0, Math.round((context.currentTime - startedAt) * 1000) + startOffsetMs)
                    : startOffsetMs;

                return {
                    mode: 'buffer',
                    voiceKey: typeof this.currentAudioMeta.voiceKey === 'string'
                        ? this.currentAudioMeta.voiceKey
                        : '',
                    currentTimeMs: durationMs > 0 ? Math.min(durationMs, elapsedMs) : elapsedMs,
                    durationMs: durationMs
                };
            }

            return null;
        }

        getAvailableGuideAudioContext() {
            const candidates = [
                this.guideAudioContext,
                window.lanlanAudioContext,
                window.appState && window.appState.audioPlayerContext,
                window.AM && window.AM.ctx
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const candidate = candidates[index];
                if (!candidate || typeof candidate.createBufferSource !== 'function') {
                    continue;
                }
                if (candidate.state === 'closed') {
                    continue;
                }
                return candidate;
            }

            const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
            if (typeof AudioContextConstructor !== 'function') {
                return null;
            }

            try {
                this.guideAudioContext = new AudioContextConstructor();
                return this.guideAudioContext;
            } catch (error) {
                console.warn('[YuiGuide] 创建教程 AudioContext 失败:', error);
                return null;
            }
        }

        decodeGuideAudioBuffer(context, arrayBuffer) {
            if (!context || !arrayBuffer) {
                return Promise.reject(new Error('missing_audio_context_or_buffer'));
            }

            try {
                const maybePromise = context.decodeAudioData(arrayBuffer.slice(0));
                if (maybePromise && typeof maybePromise.then === 'function') {
                    return maybePromise;
                }
            } catch (_) {}

            return new Promise((resolve, reject) => {
                try {
                    context.decodeAudioData(
                        arrayBuffer.slice(0),
                        (audioBuffer) => resolve(audioBuffer),
                        (error) => reject(error || new Error('decode_audio_failed'))
                    );
                } catch (error) {
                    reject(error);
                }
            });
        }

        async ensureVoicesReady() {
            if (!this.enabled || !window.speechSynthesis || typeof window.speechSynthesis.getVoices !== 'function') {
                return [];
            }

            try {
                const existingVoices = window.speechSynthesis.getVoices();
                if (Array.isArray(existingVoices) && existingVoices.length > 0) {
                    return existingVoices;
                }
            } catch (error) {
                console.warn('[YuiGuide] 读取语音列表失败:', error);
            }

            if (this.voicesReadyPromise) {
                return this.voicesReadyPromise;
            }

            this.voicesReadyPromise = new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    window.clearTimeout(timeoutId);
                    window.speechSynthesis.removeEventListener('voiceschanged', handleVoicesChanged);
                    this.voicesReadyPromise = null;
                    try {
                        resolve(window.speechSynthesis.getVoices() || []);
                    } catch (_) {
                        resolve([]);
                    }
                };
                const handleVoicesChanged = () => {
                    try {
                        const voices = window.speechSynthesis.getVoices();
                        if (Array.isArray(voices) && voices.length > 0) {
                            finish();
                        }
                    } catch (_) {}
                };
                const timeoutId = window.setTimeout(finish, 1800);

                window.speechSynthesis.addEventListener('voiceschanged', handleVoicesChanged);
                handleVoicesChanged();
            });

            return this.voicesReadyPromise;
        }

        getCurrentCatgirlName() {
            const candidates = [
                window.lanlan_config && window.lanlan_config.lanlan_name,
                window._currentCatgirl,
                window.currentCatgirl
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const candidate = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                if (candidate) {
                    return candidate;
                }
            }

            return '';
        }

        async getCurrentVoiceId() {
            const catgirlName = this.getCurrentCatgirlName();
            if (!catgirlName) {
                return '';
            }

            if (this.voiceIdCache.name === catgirlName && this.voiceIdCache.value) {
                return this.voiceIdCache.value;
            }

            try {
                const response = await fetch('/api/characters', {
                    credentials: 'same-origin'
                });
                if (!response.ok) {
                    return '';
                }

                const data = await response.json();
                const catgirlConfig = data && data['猫娘'] && data['猫娘'][catgirlName]
                    ? data['猫娘'][catgirlName]
                    : null;
                const voiceId = catgirlConfig && typeof catgirlConfig.voice_id === 'string'
                    ? catgirlConfig.voice_id.trim()
                    : '';

                this.voiceIdCache = {
                    name: catgirlName,
                    value: voiceId,
                    fetchedAt: Date.now()
                };
                return voiceId;
            } catch (error) {
                console.warn('[YuiGuide] 获取当前猫娘 voice_id 失败:', error);
                return '';
            }
        }

        async fetchPreviewAudioSrc() {
            const voiceId = await this.getCurrentVoiceId();
            if (!voiceId) {
                return null;
            }
            const previewLanguage = resolveGuidePreferredLanguage() || 'zh-CN';

            const cacheKey = voiceId;
            const cachedPreview = this.previewCache.get(cacheKey);
            if (
                cachedPreview
                && cachedPreview.language === previewLanguage
                && cachedPreview.audioSrc
            ) {
                return {
                    voiceId: voiceId,
                    audioSrc: cachedPreview.audioSrc
                };
            }

            try {
                const response = await fetch(
                    '/api/characters/voice_preview?voice_id='
                    + encodeURIComponent(voiceId)
                    + '&language='
                    + encodeURIComponent(previewLanguage),
                    {
                        credentials: 'same-origin'
                    }
                );
                if (!response.ok) {
                    return null;
                }

                const data = await response.json();
                if (!data || !data.success || !data.audio) {
                    return null;
                }

                const audioSrc = 'data:' + (data.mime_type || 'audio/mpeg') + ';base64,' + data.audio;
                this.previewCache.set(cacheKey, {
                    language: previewLanguage,
                    audioSrc: audioSrc
                });
                return {
                    voiceId: voiceId,
                    audioSrc: audioSrc
                };
            } catch (error) {
                console.warn('[YuiGuide] 获取语音预览失败:', error);
                return null;
            }
        }

        async playPreviewAudio(audioSrc, minimumDurationMs, startAtMs, meta) {
            if (!audioSrc) {
                return false;
            }

            await resumeKnownAudioContexts();
            const minDurationMs = Number.isFinite(minimumDurationMs) ? minimumDurationMs : 0;
            const initialTimeSeconds = Math.max(
                0,
                (Number.isFinite(startAtMs) ? startAtMs : 0) / 1000
            );

            return new Promise((resolve, reject) => {
                let settled = false;
                const audio = new Audio(audioSrc);
                let mouthMotionSession = null;
                let audioMouthMotionNodes = null;
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    this.stopGuideMouthMotion(mouthMotionSession);
                    mouthMotionSession = null;
                    if (audioMouthMotionNodes) {
                        try {
                            if (audioMouthMotionNodes.mediaSourceNode) {
                                audioMouthMotionNodes.mediaSourceNode.disconnect();
                            }
                            if (audioMouthMotionNodes.analyserNode) {
                                audioMouthMotionNodes.analyserNode.disconnect();
                            }
                        } catch (_) {}
                        audioMouthMotionNodes = null;
                    }
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    audio.onended = null;
                    audio.onerror = null;
                    audio.onpause = null;
                    audio.onloadedmetadata = null;
                    if (this.currentAudio === audio) {
                        this.currentAudio = null;
                    }
                    if (this.currentAudioMeta && this.currentAudioMeta.audio === audio) {
                        this.currentAudioMeta = null;
                    }
                    if (this.currentFinish === cancelPlayback) {
                        this.currentFinish = null;
                    }
                    if (success) {
                        resolve(true);
                        return;
                    }
                    reject(error || new Error('preview_audio_failed'));
                };
                const cancelPlayback = () => {
                    finish(true);
                };

                audio.preload = 'auto';
                audio.volume = 1;
                audio.onended = () => finish(true);
                audio.onerror = () => finish(false, new Error('preview_audio_error'));
                this.currentAudio = audio;
                this.currentAudioMeta = Object.assign({
                    audio: audio,
                    voiceKey: '',
                    text: ''
                }, meta || {});
                this.currentFinish = cancelPlayback;

                if (initialTimeSeconds > 0) {
                    const applyStartTime = () => {
                        try {
                            const maxSeek = Number.isFinite(audio.duration) && audio.duration > 0
                                ? Math.max(0, audio.duration - 0.05)
                                : initialTimeSeconds;
                            audio.currentTime = Math.min(initialTimeSeconds, maxSeek);
                        } catch (_) {}
                    };

                    audio.onloadedmetadata = applyStartTime;
                    if (audio.readyState >= 1) {
                        applyStartTime();
                    }
                }

                const fallbackTimerId = window.setTimeout(() => {
                    finish(true);
                }, Math.max(estimateSpeechDurationMs('x'), minDurationMs, 3000));
                this.currentFallbackTimer = fallbackTimerId;
                audioMouthMotionNodes = this.createGuideAudioElementMouthMotionNodes(audio);
                if (audioMouthMotionNodes
                    && audioMouthMotionNodes.context
                    && audioMouthMotionNodes.context.state === 'suspended'
                    && typeof audioMouthMotionNodes.context.resume === 'function') {
                    audioMouthMotionNodes.context.resume().catch(() => {});
                }
                mouthMotionSession = this.startGuideMouthMotion(
                    meta && typeof meta.voiceKey === 'string' ? meta.voiceKey : '',
                    audioMouthMotionNodes
                );

                try {
                    const playPromise = audio.play();
                    if (playPromise && typeof playPromise.then === 'function') {
                        playPromise.catch((error) => finish(false, error));
                    }
                } catch (error) {
                    finish(false, error);
                }
            });
        }

        async playPreviewAudioThroughContext(audioSrc, minimumDurationMs, startAtMs, meta) {
            const context = this.getAvailableGuideAudioContext();
            if (!context) {
                return false;
            }

            await resumeKnownAudioContexts();
            if (context.state === 'suspended' && typeof context.resume === 'function') {
                await context.resume().catch(() => {});
            }
            const response = await fetchWithTimeout(audioSrc, {
                credentials: 'same-origin'
            }, 5500);
            if (!response.ok) {
                throw new Error('guide_audio_fetch_failed');
            }

            const arrayBuffer = await response.arrayBuffer();
            const audioBuffer = await this.decodeGuideAudioBuffer(context, arrayBuffer);
            const startOffsetMs = Number.isFinite(startAtMs) ? Math.max(0, startAtMs) : 0;
            const startOffsetSeconds = Math.max(0, startOffsetMs / 1000);

            return new Promise((resolve, reject) => {
                let settled = false;
                const source = context.createBufferSource();
                const gainNode = typeof context.createGain === 'function' ? context.createGain() : null;
                const analyserNode = this.createGuideAnalyser(context);
                let mouthMotionSession = null;
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    this.stopGuideMouthMotion(mouthMotionSession);
                    mouthMotionSession = null;
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    source.onended = null;
                    try {
                        source.disconnect();
                    } catch (_) {}
                    if (analyserNode) {
                        try {
                            analyserNode.disconnect();
                        } catch (_) {}
                    }
                    if (gainNode) {
                        try {
                            gainNode.disconnect();
                        } catch (_) {}
                    }
                    if (this.currentAudioMeta && this.currentAudioMeta.source === source) {
                        this.currentAudioMeta = null;
                    }
                    if (this.currentFinish === cancelPlayback) {
                        this.currentFinish = null;
                    }
                    if (success) {
                        resolve(true);
                        return;
                    }
                    reject(error || new Error('guide_audio_context_play_failed'));
                };
                const cancelPlayback = () => {
                    finish(true);
                };

                source.buffer = audioBuffer;
                if (analyserNode && gainNode) {
                    gainNode.gain.value = 1;
                    source.connect(analyserNode);
                    analyserNode.connect(gainNode);
                    gainNode.connect(context.destination);
                } else if (analyserNode) {
                    source.connect(analyserNode);
                    analyserNode.connect(context.destination);
                } else if (gainNode) {
                    gainNode.gain.value = 1;
                    source.connect(gainNode);
                    gainNode.connect(context.destination);
                } else {
                    source.connect(context.destination);
                }
                mouthMotionSession = this.startGuideMouthMotion(
                    meta && typeof meta.voiceKey === 'string' ? meta.voiceKey : '',
                    analyserNode ? { analyserNode: analyserNode } : null
                );

                this.currentFinish = cancelPlayback;
                this.currentAudioMeta = Object.assign({
                    mode: 'buffer',
                    context: context,
                    source: source,
                    analyserNode: analyserNode,
                    gainNode: gainNode,
                    startedAt: context.currentTime,
                    startOffsetMs: startOffsetMs,
                    durationMs: Math.round(audioBuffer.duration * 1000),
                    voiceKey: '',
                    text: ''
                }, meta || {});

                source.onended = () => finish(true);

                const fallbackTimerId = window.setTimeout(() => {
                    finish(true);
                }, Math.max(
                    estimateSpeechDurationMs('x'),
                    minimumDurationMs,
                    Math.max(3000, Math.round(audioBuffer.duration * 1000))
                ) + 1200);
                this.currentFallbackTimer = fallbackTimerId;

                try {
                    source.start(0, Math.min(startOffsetSeconds, Math.max(0, audioBuffer.duration - 0.05)));
                } catch (error) {
                    finish(false, error);
                }
            });
        }

        resolveGuideAudioSrc(voiceKey) {
            const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
            if (!normalizedKey) {
                return '';
            }

            return guideAudioSrc(normalizedKey);
        }

        async speak(text, options) {
            const message = typeof text === 'string' ? text.trim() : '';
            const normalizedOptions = options || {};
            if (!message) {
                return;
            }
            this.stop();
            await wait(48);

            const minimumDurationMs = Number.isFinite(normalizedOptions.minDurationMs)
                ? normalizedOptions.minDurationMs
                : 0;
            const fallbackDurationMs = Math.max(estimateSpeechDurationMs(message), minimumDurationMs);
            const localAudioSrc = this.resolveGuideAudioSrc(normalizedOptions.voiceKey);
            const startAtMs = Number.isFinite(normalizedOptions.startAtMs)
                ? Math.max(0, normalizedOptions.startAtMs)
                : 0;

            if (localAudioSrc) {
                try {
                    const playedByContext = await this.playPreviewAudioThroughContext(
                        localAudioSrc,
                        fallbackDurationMs,
                        startAtMs,
                        {
                            voiceKey: normalizedOptions.voiceKey,
                            text: message
                        }
                    );
                    if (playedByContext) {
                        return;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] AudioContext 教程语音播放失败，尝试 HTMLAudio:', normalizedOptions.voiceKey, error);
                }

                try {
                    await this.playPreviewAudio(localAudioSrc, fallbackDurationMs, startAtMs, {
                        voiceKey: normalizedOptions.voiceKey,
                        text: message
                    });
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] 本地教程语音播放失败，回退为静默等待:', normalizedOptions.voiceKey, error);
                }
            }

            await wait(fallbackDurationMs);
        }
    }

    class YuiGuideEmotionBridge {
        constructor() {
            this.live2dApplySequence = Promise.resolve();
            this.live2dExpressionSequence = Promise.resolve();
            this.pendingLive2DEmotion = '';
            this.pendingLive2DExpressionFile = '';
            this.activeLive2DExpressionFile = '';
        }

        normalizeModelType(modelType) {
            const normalizedType = String(modelType || '').toLowerCase();
            if (normalizedType === 'vrm' || normalizedType === 'mmd') {
                return normalizedType;
            }
            if (normalizedType === 'live2d') {
                return 'live2d';
            }
            return '';
        }

        getStoredValue(key) {
            try {
                return (
                    (window.sessionStorage && window.sessionStorage.getItem(key))
                    || (window.localStorage && window.localStorage.getItem(key))
                    || ''
                );
            } catch (_) {
                return '';
            }
        }

        resolveStoredModelType() {
            const modelType = String(this.getStoredValue('modelType') || '').toLowerCase();
            if (modelType === 'live3d') {
                const subType = String(
                    this.getStoredValue('live3dSubType') || this.getStoredValue('live3d_sub_type')
                ).toLowerCase();
                if (subType === 'mmd' || subType === 'vrm') {
                    return subType;
                }
                return 'vrm';
            }
            return this.normalizeModelType(modelType);
        }

        getActiveModelType() {
            const runtimeType = this.normalizeModelType(
                typeof window.getActiveModelType === 'function' ? window.getActiveModelType() : ''
            );
            if (runtimeType) {
                return runtimeType;
            }

            const cfg = window.lanlan_config;
            if (cfg) {
                const modelType = String(cfg.model_type || '').toLowerCase();
                if (modelType === 'live3d') {
                    const subType = String(cfg.live3d_sub_type || '').toLowerCase();
                    if (subType === 'mmd' || subType === 'vrm') {
                        return subType;
                    }
                    return 'live2d';
                }

                if (modelType === 'vrm' || modelType === 'mmd') {
                    return modelType;
                }
                return 'live2d';
            }

            const storedType = this.resolveStoredModelType();
            if (storedType) {
                return storedType;
            }
            return 'live2d';
        }

        handleAsyncFailure(result, ...warningArgs) {
            if (result && typeof result.catch === 'function') {
                result.catch((error) => {
                    console.warn(...warningArgs, error);
                });
            }
        }

        async waitForLive2DMotionTail(manager, timeoutMs) {
            const maxWaitMs = Math.max(0, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 0));
            if (!manager || typeof manager.hasActiveMotionPlayback !== 'function' || maxWaitMs <= 0) {
                return;
            }

            const startedAt = Date.now();
            while ((Date.now() - startedAt) < maxWaitMs) {
                if (!manager.currentModel) {
                    return;
                }
                if (!manager.hasActiveMotionPlayback()) {
                    return;
                }
                await new Promise((resolve) => window.setTimeout(resolve, 48));
            }
        }

        async waitForLive2DMotionCompletion(manager, timeoutMs) {
            const maxWaitMs = Math.max(0, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 0));
            if (!manager || typeof manager.hasActiveMotionPlayback !== 'function' || maxWaitMs <= 0) {
                return;
            }

            const startedAt = Date.now();
            while ((Date.now() - startedAt) < maxWaitMs) {
                if (!manager.currentModel) {
                    return;
                }
                if (!manager.hasActiveMotionPlayback()) {
                    return;
                }
                await new Promise((resolve) => window.setTimeout(resolve, 48));
            }
        }

        queueLive2DEmotionApply(emotion) {
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return Promise.resolve();
            }

            this.pendingLive2DEmotion = normalizedEmotion;
            const run = async () => {
                const targetEmotion = this.pendingLive2DEmotion;
                const manager = window.live2dManager;
                if (!manager || !manager.currentModel) {
                    return;
                }

                if (this.activeLive2DExpressionFile) {
                    this.clearLive2DGuideExpression(manager);
                }

                await this.waitForLive2DMotionCompletion(manager, 2200);
                if (this.pendingLive2DEmotion !== targetEmotion) {
                    return;
                }

                if (typeof manager.setEmotion === 'function') {
                    await manager.setEmotion(targetEmotion);
                    return;
                }
                if (typeof manager.playMotion === 'function') {
                    await manager.playMotion(targetEmotion);
                }
            };

            this.live2dApplySequence = this.live2dApplySequence
                .catch(() => {})
                .then(run);
            return this.live2dApplySequence;
        }

        getActiveGuideExpressionFile() {
            return this.activeLive2DExpressionFile || '';
        }

        clearLive2DGuideExpression(managerOverride) {
            const manager = managerOverride || window.live2dManager;
            this.pendingLive2DExpressionFile = '';
            this.activeLive2DExpressionFile = '';

            if (!manager) {
                return false;
            }

            let handled = false;
            if (typeof manager._removeManualExpressionOverride === 'function') {
                try {
                    manager._removeManualExpressionOverride();
                    handled = true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理教程临时表情失败:', error);
                }
            }

            if (Object.prototype.hasOwnProperty.call(manager, '_activeExpressionParamIds')) {
                manager._activeExpressionParamIds = null;
                handled = true;
            }

            return handled;
        }

        buildLive2DExpressionCandidates(manager, expressionFile) {
            const normalizedExpressionFile = typeof expressionFile === 'string'
                ? expressionFile.trim()
                : '';
            if (!manager || !normalizedExpressionFile) {
                return [];
            }

            const candidateFiles = [];
            const pushCandidate = (filePath) => {
                if (!filePath || typeof filePath !== 'string') {
                    return;
                }
                const normalizedPath = filePath.replace(/\\/g, '/').trim();
                if (normalizedPath && !candidateFiles.includes(normalizedPath)) {
                    candidateFiles.push(normalizedPath);
                }
            };

            pushCandidate(normalizedExpressionFile);
            const resolvedRef = typeof manager.resolveExpressionReferenceByFile === 'function'
                ? manager.resolveExpressionReferenceByFile(normalizedExpressionFile)
                : null;
            if (resolvedRef && resolvedRef.file) {
                pushCandidate(resolvedRef.file);
            }

            const baseName = normalizedExpressionFile.split('/').pop() || '';
            if (baseName) {
                pushCandidate(baseName);
                pushCandidate('expressions/' + baseName);
            }

            return candidateFiles;
        }

        async loadLive2DExpressionData(manager, expressionFile) {
            const candidateFiles = this.buildLive2DExpressionCandidates(manager, expressionFile);
            if (candidateFiles.length === 0) {
                return null;
            }

            let lastFetchError = null;
            for (const candidateFile of candidateFiles) {
                try {
                    const response = await fetch(manager.resolveAssetPath(candidateFile));
                    if (!response.ok) {
                        lastFetchError = new Error('Failed to load expression: ' + response.statusText);
                        continue;
                    }

                    return {
                        expressionData: await response.json(),
                        loadedExpressionFile: candidateFile
                    };
                } catch (error) {
                    lastFetchError = error;
                }
            }

            if (typeof manager.markExpressionFileMissing === 'function') {
                candidateFiles.forEach((candidateFile) => {
                    manager.markExpressionFileMissing(candidateFile);
                });
            }

            if (lastFetchError) {
                throw lastFetchError;
            }
            return null;
        }

        queueLive2DExpressionApply(expressionFile, options) {
            const normalizedExpressionFile = typeof expressionFile === 'string'
                ? expressionFile.trim()
                : '';
            if (!normalizedExpressionFile) {
                return Promise.resolve(false);
            }

            const normalizedOptions = options || {};
            const fadeInMs = Math.max(
                60,
                Math.min(
                    1600,
                    Math.round(Number.isFinite(normalizedOptions.fadeInMs) ? normalizedOptions.fadeInMs : 220)
                )
            );
            this.pendingLive2DExpressionFile = normalizedExpressionFile;

            const previousEmotionSequence = this.live2dApplySequence;
            const previousExpressionSequence = this.live2dExpressionSequence;
            const run = async () => {
                const targetExpressionFile = this.pendingLive2DExpressionFile;
                const manager = window.live2dManager;
                if (!manager || !manager.currentModel || targetExpressionFile !== normalizedExpressionFile) {
                    return false;
                }

                const loadedExpression = await this.loadLive2DExpressionData(manager, targetExpressionFile);
                if (!loadedExpression || this.pendingLive2DExpressionFile !== targetExpressionFile) {
                    return false;
                }

                const expressionParams = Array.isArray(loadedExpression.expressionData && loadedExpression.expressionData.Parameters)
                    ? loadedExpression.expressionData.Parameters
                    : [];
                if (expressionParams.length === 0 || typeof manager._installManualExpressionOverride !== 'function') {
                    return false;
                }

                manager._activeExpressionParamIds = new Set(
                    expressionParams
                        .map((param) => param && param.Id)
                        .filter(Boolean)
                );
                manager._installManualExpressionOverride(expressionParams, fadeInMs);
                this.activeLive2DExpressionFile = loadedExpression.loadedExpressionFile;
                return true;
            };

            this.live2dExpressionSequence = Promise.all([
                previousEmotionSequence.catch(() => {}),
                previousExpressionSequence.catch(() => {})
            ]).then(run);
            return this.live2dExpressionSequence;
        }

        applyExpressionFile(expressionFile, options) {
            const activeModelType = this.getActiveModelType();
            if (activeModelType !== 'live2d') {
                return;
            }

            if (!window.live2dManager || !window.live2dManager.currentModel) {
                return;
            }

            try {
                const applyPromise = this.queueLive2DExpressionApply(expressionFile, options);
                this.handleAsyncFailure(applyPromise, '[YuiGuide] 播放教程临时表情失败:', expressionFile);
            } catch (error) {
                console.warn('[YuiGuide] 播放教程临时表情失败:', expressionFile, error);
            }
        }

        apply(emotion) {
            if (!emotion) {
                return;
            }

            const activeModelType = this.getActiveModelType();
            if (activeModelType === 'live2d') {
                if (!window.live2dManager || !window.live2dManager.currentModel) {
                    return;
                }

                try {
                    const applyPromise = this.queueLive2DEmotionApply(emotion);
                    this.handleAsyncFailure(applyPromise, '[YuiGuide] 播放教程动作失败:', emotion);
                } catch (error) {
                    console.warn('[YuiGuide] 播放教程动作失败:', emotion, error);
                }
                return;
            }

            try {
                if (activeModelType === 'mmd') {
                    if (window.mmdManager && typeof window.mmdManager.setEmotion === 'function') {
                        window.mmdManager.setEmotion(emotion);
                    } else if (
                        window.mmdManager
                        && window.mmdManager.expression
                        && typeof window.mmdManager.expression.setEmotion === 'function'
                    ) {
                        window.mmdManager.expression.setEmotion(emotion);
                    }
                    return;
                }

                if (activeModelType === 'vrm') {
                    if (window.vrmManager && typeof window.vrmManager.setEmotion === 'function') {
                        window.vrmManager.setEmotion(emotion);
                    } else if (
                        window.vrmManager
                        && window.vrmManager.expression
                        && typeof window.vrmManager.expression.setMood === 'function'
                    ) {
                        window.vrmManager.expression.setMood(emotion);
                    }
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] 设置教程情绪失败:', emotion, error);
            }
        }

        clearLive2DGuidePresentation() {
            const manager = window.live2dManager;
            if (!manager) {
                return false;
            }

            let handled = this.clearLive2DGuideExpression(manager);

            if (typeof manager.softClearEmotionEffects === 'function') {
                this.handleAsyncFailure(
                    manager.softClearEmotionEffects({
                        preserveExpression: true
                    }),
                    '[YuiGuide] 平滑清理 Live2D 教程动作失败:'
                );
                handled = true;
            } else if (typeof manager.clearEmotionEffects === 'function') {
                this.handleAsyncFailure(
                    manager.clearEmotionEffects(),
                    '[YuiGuide] 清理 Live2D 教程动作失败:'
                );
                handled = true;
            }

            if (typeof manager.smoothResetToInitialState === 'function') {
                this.handleAsyncFailure(
                    manager.smoothResetToInitialState(220),
                    '[YuiGuide] 平滑清理 Live2D 表情失败:'
                );
                handled = true;
            } else if (typeof manager.clearExpression === 'function') {
                this.handleAsyncFailure(
                    manager.clearExpression(),
                    '[YuiGuide] 清理 Live2D 表情失败:'
                );
                handled = true;
            }

            return handled;
        }

        clearMmdGuidePresentation() {
            const manager = window.mmdManager;
            if (!manager) {
                return false;
            }

            if (typeof manager.setEmotion === 'function') {
                this.handleAsyncFailure(
                    manager.setEmotion('neutral'),
                    '[YuiGuide] 清理 MMD 教程情绪失败:'
                );
                return true;
            }

            const expression = manager.expression;
            if (expression && typeof expression.setEmotion === 'function') {
                this.handleAsyncFailure(
                    expression.setEmotion('neutral'),
                    '[YuiGuide] 清理 MMD 教程情绪失败:'
                );
                return true;
            }

            if (expression && typeof expression.resetAllMorphs === 'function') {
                this.handleAsyncFailure(
                    expression.resetAllMorphs(),
                    '[YuiGuide] 清理 MMD 教程 morph 失败:'
                );
                return true;
            }

            return false;
        }

        clearVrmGuidePresentation() {
            const manager = window.vrmManager;
            if (!manager) {
                return false;
            }

            if (typeof manager.setEmotion === 'function') {
                this.handleAsyncFailure(
                    manager.setEmotion('neutral'),
                    '[YuiGuide] 清理 VRM 教程情绪失败:'
                );
                return true;
            }

            const expression = manager.expression;
            if (expression && typeof expression.setMood === 'function') {
                this.handleAsyncFailure(
                    expression.setMood('neutral'),
                    '[YuiGuide] 清理 VRM 教程情绪失败:'
                );
                return true;
            }

            return false;
        }

        clearViaActiveModelType() {
            const activeModelType = this.getActiveModelType();
            if (activeModelType === 'live2d') {
                return this.clearLive2DGuidePresentation();
            }
            if (activeModelType === 'mmd') {
                return this.clearMmdGuidePresentation();
            }
            if (activeModelType === 'vrm') {
                return this.clearVrmGuidePresentation();
            }
            return false;
        }

        clearWithLegacyBridge() {
            if (window.LanLan1 && typeof window.LanLan1.clearEmotionEffects === 'function') {
                try {
                    window.LanLan1.clearEmotionEffects();
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理情绪失败:', error);
                }
            }

            if (window.LanLan1 && typeof window.LanLan1.clearExpression === 'function') {
                try {
                    window.LanLan1.clearExpression();
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理表情失败:', error);
                }
            }

            return false;
        }

        clear() {
            try {
                if (this.clearViaActiveModelType()) {
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] 按模型类型清理教程情绪失败:', error);
            }

            this.clearWithLegacyBridge();
        }
    }

    class CursorAnchorStore {
        constructor() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }

        rememberScenePoint(sceneId, point) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            if (
                !normalizedSceneId
                || !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.scenePoints[normalizedSceneId] = {
                x: point.x,
                y: point.y
            };
            return true;
        }

        getScenePoint(sceneIds) {
            const candidates = Array.isArray(sceneIds) ? sceneIds : [sceneIds];
            for (let index = 0; index < candidates.length; index += 1) {
                const sceneId = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                const point = sceneId ? this.scenePoints[sceneId] : null;
                if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) {
                    return {
                        x: point.x,
                        y: point.y
                    };
                }
            }
            return null;
        }

        rememberLatestExternalizedPoint(point) {
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.latestExternalizedPoint = {
                x: point.x,
                y: point.y,
                at: Number(point.at) || Date.now(),
                kind: typeof point.kind === 'string' ? point.kind : '',
                effect: typeof point.effect === 'string' ? point.effect : '',
                effectDurationMs: Number.isFinite(point.effectDurationMs)
                    ? Math.max(0, Math.floor(point.effectDurationMs))
                    : 0,
                settled: point.settled === true
            };
            return true;
        }

        getLatestExternalizedPoint(maxAgeMs) {
            const point = this.latestExternalizedPoint;
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return null;
            }
            const latestAt = Number(point.at);
            const ageLimit = Number.isFinite(maxAgeMs) ? maxAgeMs : 30000;
            if (Number.isFinite(latestAt) && Date.now() - latestAt > ageLimit) {
                return null;
            }
            return {
                x: point.x,
                y: point.y
            };
        }

        clear() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }
    }

    class YuiGuideDirector {
        constructor(options) {
            this.options = options || {};
            this.tutorialManager = this.options.tutorialManager || null;
            this.page = this.options.page || 'home';
            this.registry = this.options.registry || null;
            this.overlay = new window.YuiGuideOverlay(document);
            this.voiceQueue = new YuiGuideVoiceQueue();
            this.emotionBridge = new YuiGuideEmotionBridge();
            this.currentSceneId = null;
            this.currentStep = null;
            this.currentContext = null;
            this.sceneRunId = 0;
            this.sceneTimers = new Set();
            this.guideChatStreamTimers = new Set();
            this.sceneResources = createYuiGuideScopedTutorialResources();
            this.guideChatStreamResources = createYuiGuideScopedTutorialResources();
            this.interruptsEnabled = false;
            this.interruptCount = 0;
            this.interruptQualifyingMoveStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            this.angryExitTriggered = false;
            this.destroyed = false;
            this.lastTutorialEndReason = null;
            this.introFlowStarted = false;
            this.introFlowCompleted = false;
            this.introGreetingChatHighlightCleared = false;
            this.awaitingIntroActivation = false;
            this._introActivationResolve = null;
            this.terminationRequested = false;
            this.angryExitPresentationPromise = null;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.scenePausedForResistance = false;
            this.scenePausedAt = 0;
            this.scenePauseResolvers = [];
            if (typeof TutorialVisualControllers.createHighlightController !== 'function') {
                throw new Error('TutorialVisualControllers.createHighlightController must be loaded before YuiGuideDirector');
            }
            if (typeof TutorialSettingsTourFlow.SettingsTourFlow !== 'function') {
                throw new Error('TutorialSettingsTourFlow.SettingsTourFlow must be loaded before YuiGuideDirector');
            }
            this.targetGeometryRegistry = createYuiGuideTargetGeometryRegistry();
            this.cursor = new TutorialVisualControllers.GhostCursorController(new TutorialVisualControllers.YuiGuideGhostCursor(this.overlay), {
                registry: this.targetGeometryRegistry
            });
            this.spotlightController = new TutorialVisualControllers.SpotlightController(TutorialVisualControllers.createHighlightController({
                document: document,
                window: window,
                overlay: this.overlay,
                defaultPadding: DEFAULT_SPOTLIGHT_PADDING,
                resolveElement: (selector) => this.resolveElement(selector)
            }), {
                registry: this.targetGeometryRegistry
            });
            this.pauseCoordinator = new PauseCoordinator({
                cursor: this.cursor,
                spotlightController: this.spotlightController,
                getResistancePaused: () => this.scenePausedForResistance,
                setResistancePaused: (active) => {
                    this.scenePausedForResistance = active === true;
                },
                setPausedAt: (pausedAt) => {
                    this.scenePausedAt = Number.isFinite(pausedAt) ? pausedAt : 0;
                },
                beginInterruptPresentation: () => this.beginGuideInterruptPresentation(),
                endInterruptPresentation: () => this.endGuideInterruptPresentation(),
                takeScenePauseResolvers: () => {
                    const resolvers = this.scenePauseResolvers.slice();
                    this.scenePauseResolvers = [];
                    return resolvers;
                }
            });
            this.sidebarPauseController = new SidebarPauseController({
                document: document
            });
            this.pauseCoordinator.registerPauseToken('sidebar', this.sidebarPauseController.getPauseToken());
            this.resistanceController = new ResistanceController(this);
            this.activeGuideEmotion = '';
            this.guideInterruptPresentationActive = false;
            this.pluginDashboardHandoff = null;
            this.pluginDashboardLastInterruptRequestId = '';
            this.pluginDashboardWindowCreatedByGuide = false;
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            this.customSecondarySpotlightTarget = null;
            this.persistentGhostCursorLookAtHandle = null;
            this.preTakeoverGhostCursorLookAtHandle = null;
            this.guideIdleSwayHandle = null;
            this.takeoverTopPeekHandle = null;
            this.takeoverOriginalAgentSwitches = null;
            this.takeoverAgentSwitchRestorePromise = null;
            this.returnPetalTransitionActive = false;
            this.avatarFloatingGuideSuppressionActive = false;
            this.avatarFloatingGuideTutorialModeActive = false;
            this.avatarFloatingGuidePreviousIsInTutorial = false;
            this.avatarStandInShowTimer = null;
            this.avatarStandInFadeTimer = null;
            this.avatarStandInHideTimer = null;
            this.avatarStandInOpacityRestores = null;
            this.avatarStandInActive = false;
            this.avatarStandInToken = 0;
            this.avatarStandInController = new TutorialVisualControllers.AvatarStandInController(this);
            this.petalTransitionController = new TutorialVisualControllers.PetalTransitionController(this);
            this.cursorAnchorStore = new CursorAnchorStore();
            this.operationRegistry = new OperationRegistry(this, {
                registry: this.targetGeometryRegistry,
                pluginDashboardWindowName: PLUGIN_DASHBOARD_WINDOW_NAME,
                resolveGuideLocale: resolveGuideLocale
            });
            this.settingsTourFlow = new TutorialSettingsTourFlow.SettingsTourFlow(this);
            this.sceneOrchestrator = new TutorialSceneOrchestrator.SceneOrchestrator(this);
            this.terminationRouter = new TutorialTerminationRouter(this);
            this.latestExternalizedChatCursorMoveSceneId = '';
            this.latestExternalizedChatCursorMovePromise = null;
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.latestGuideChatMessageRetainTimer = null;
            this.keydownHandler = this.onKeyDown.bind(this);
            this.pointerMoveHandler = this.onPointerMove.bind(this);
            this.pointerDownHandler = this.onPointerDown.bind(this);
            this.resistanceCursorTimer = null;
            this.userCursorRevealMoveCount = 0;
            this.userCursorRevealed = false;
            this.lastUserCursorRevealMoveAt = 0;
            this.restoreHiddenCursorAfterResistance = false;
            this.pageHideHandler = this.onPageHide.bind(this);
            this.tutorialEndHandler = this.onTutorialEndEvent.bind(this);
            this.externalChatReadyHandler = this.onExternalChatReady.bind(this);
            this.externalChatCursorAnchorHandler = this.onExternalChatCursorAnchor.bind(this);
            this.remoteTerminationRequestHandler = this.onRemoteTerminationRequest.bind(this);
            this.desktopPluginDashboardSkipHandler = this.handleDesktopYuiGuideSkipRequest.bind(this);
            this.desktopPluginDashboardInterruptHandler = this.onDesktopPluginDashboardInterruptRequest.bind(this);
            this.messageHandler = this.onWindowMessage.bind(this);
            this.guideMessageActionHandler = this.handleGuideMessageAction.bind(this);
            this.guideMessageActionHandlerInstalled = false;
            this.pendingGuideMessageAction = null;
            this.chatBridgeCommandBus = createYuiGuideChatBridgeCommandBus({
                channelProvider: () => {
                    return window.appInterpage && window.appInterpage.nekoBroadcastChannel
                        ? window.appInterpage.nekoBroadcastChannel
                        : null;
                },
                nativeRelayProvider: () => window.nekoTutorialOverlay || null
            });
            const capabilityApi = window.homeTutorialPlatformCapabilities;
            this.platformCapabilities = capabilityApi && typeof capabilityApi.create === 'function'
                ? capabilityApi.create()
                : createHomeTutorialPlatformCapabilities();
            this.experienceMetrics = window.homeTutorialExperienceMetrics || createHomeTutorialExperienceMetrics();
            this.wakeup = window.YuiGuideWakeup && typeof window.YuiGuideWakeup.create === 'function'
                ? window.YuiGuideWakeup.create({
                    metrics: this.experienceMetrics
                })
                : null;
            this.interactionTakeover = window.TutorialInteractionTakeover
                && typeof window.TutorialInteractionTakeover.createController === 'function'
                ? window.TutorialInteractionTakeover.createController({
                    page: this.page,
                    overlay: this.overlay,
                    allowTarget: (target, event) => this.isAllowedTutorialInteractionTarget(target, event),
                    isSystemDialogTarget: (target, event) => this.isSystemDialogInteractionTarget(target, event),
                    allowWindowPassthrough: true,
                    allowTouchPassthrough: (event, controller) => {
                        return !!(
                            this.mobileTouchInteractionPassthrough
                            && controller
                            && typeof controller.isTouchInteractionEvent === 'function'
                            && controller.isTouchInteractionEvent(event)
                            && !this.awaitingIntroActivation
                            && !this.manualPluginDashboardOpenAllowed
                        );
                    },
                    isDestroyed: () => this.destroyed,
                    isResistancePaused: () => this.scenePausedForResistance === true,
                    externalizedChatDetector: () => this.isHomeChatExternalized(),
                    externalChatChannelProvider: () => {
                        return window.appInterpage && window.appInterpage.nekoBroadcastChannel
                            ? window.appInterpage.nekoBroadcastChannel
                            : null;
                    }
                })
                : null;
            this.chatWindowAdapter = createYuiGuideChatWindowAdapter({
                mode: this.isHomeChatExternalized() ? 'externalized' : 'local',
                registry: this.targetGeometryRegistry,
                interactionTakeover: this.interactionTakeover,
                beforeExternalizedSpotlight: () => this.clearHomeSpotlightsForExternalizedChat(),
                resolveLocalTarget: (targetKey) => this.resolveAvatarFloatingSelector(targetKey)
            });
            if (this.interactionTakeover && typeof this.interactionTakeover.enableFaceForwardLock === 'function') {
                this.interactionTakeover.enableFaceForwardLock();
            }

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-ui-suppressed');
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatButtonsDisabled === 'function') {
                    this.interactionTakeover.setExternalizedChatButtonsDisabled(true);
                }
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
            window.addEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.addEventListener('neko:yui-guide:external-chat-cursor-anchor', this.externalChatCursorAnchorHandler, true);
            window.addEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.addEventListener(DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT, this.desktopPluginDashboardSkipHandler, true);
            window.addEventListener('neko:yui-guide:desktop-interrupt-request', this.desktopPluginDashboardInterruptHandler, true);
            window.addEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.addEventListener('message', this.messageHandler, true);
        }

        isStopping() {
            return !!(this.destroyed || this.angryExitTriggered || this.terminationRequested);
        }

        isGuardFailed(runId) {
            const hasRunId = runId !== undefined && runId !== null;
            return !!((hasRunId && runId !== this.sceneRunId) || this.isStopping());
        }

        prepareNarration(scene) {
            const text = this.resolveAvatarFloatingSceneText(scene);
            const voiceKey = scene.voiceKey || '';
            const sceneButtons = this.getAvatarFloatingSceneButtons(scene);
            const canHandleSceneButtons = sceneButtons.length > 0
                ? this.installGuideMessageActionHandler()
                : false;
            const actionWaitPromise = canHandleSceneButtons
                ? this.beginGuideMessageActionWait(sceneButtons, 0)
                : null;
            if (text) {
                this.appendGuideChatMessage(text, {
                    textKey: scene.textKey || '',
                    voiceKey: voiceKey,
                    buttons: sceneButtons
                });
            }
            const sceneEmotion = this.resolveAvatarFloatingSceneEmotion(scene);
            if (sceneEmotion) {
                this.applyGuideEmotion(sceneEmotion);
            }
            return {
                text,
                voiceKey,
                sceneButtons,
                canHandleSceneButtons,
                actionWaitPromise
            };
        }

        createSceneScaler(voiceKey) {
            const timingScale = this.getGuideVoiceTimingScale(voiceKey);
            return (value, minValue, maxValue) => {
                const baseValue = Number.isFinite(value) ? value : 0;
                const scaledValue = Math.round(baseValue * timingScale);
                return clamp(
                    scaledValue,
                    Number.isFinite(minValue) ? minValue : 40,
                    Number.isFinite(maxValue) ? maxValue : Math.max(
                        Number.isFinite(minValue) ? minValue : 40,
                        scaledValue
                    )
                );
            };
        }

        createNarrationPromise(scene, text, voiceKey, options) {
            const normalizedOptions = options || {};
            if (!text && !voiceKey) {
                return Promise.resolve();
            }
            return this.speakGuideLine(text, {
                voiceKey: voiceKey,
                minDurationMs: Number.isFinite(normalizedOptions.minDurationMs)
                    ? normalizedOptions.minDurationMs
                    : 1800
            }).catch((error) => {
                console.warn('[YuiGuide] 悬浮窗教程旁白失败，继续流程:', scene && scene.id, error);
            });
        }

        async finalizeScene(runId, options) {
            const normalizedOptions = options || {};
            if (normalizedOptions.canHandleSceneButtons && this.pendingGuideMessageAction) {
                this.armPendingGuideMessageActionTimeout(12000);
            }
            if (
                normalizedOptions.actionWaitPromise
                && !this.isGuardFailed(runId)
            ) {
                await normalizedOptions.actionWaitPromise;
            }
            if (this.isGuardFailed(runId)) {
                return false;
            }
            const index = Number.isFinite(normalizedOptions.index)
                ? normalizedOptions.index
                : 0;
            const total = Number.isFinite(normalizedOptions.total)
                ? normalizedOptions.total
                : 0;
            await this.waitForSceneDelay(index >= total - 1 ? 260 : 420);
            return !this.isGuardFailed(runId);
        }

        performFullCleanup(options) {
            const normalizedOptions = options || {};
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.setTutorialTakingOver(false);
            if (
                normalizedOptions.destroyInteractionTakeover
                && this.interactionTakeover
                && typeof this.interactionTakeover.destroy === 'function'
            ) {
                this.interactionTakeover.destroy();
            }
            if (normalizedOptions.destroyOverlay) {
                this.overlay.destroy();
            }
        }

        async withLookAt(options, run) {
            const normalizedOptions = options || {};
            const completeReason = normalizedOptions.completeReason || 'look_at_complete';
            const isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : () => this.isStopping();
            let lookAtHandle = null;
            const lookAtPromise = this.ensurePersistentGhostCursorLookAtPerformance({
                isCancelled
            }).then((handle) => {
                lookAtHandle = handle || null;
                return lookAtHandle;
            }).catch((error) => {
                console.warn(
                    normalizedOptions.startFailureMessage || '[YuiGuide] Cursor look-at startup failed:',
                    error
                );
                return null;
            });

            try {
                return await run();
            } finally {
                if (lookAtPromise && !lookAtHandle) {
                    lookAtHandle = await lookAtPromise;
                }
                if (lookAtHandle) {
                    await this.stopIntroVoiceCursorLookAtPerformance(lookAtHandle, completeReason);
                } else {
                    await this.stopPersistentGhostCursorLookAtPerformance(completeReason);
                }
            }
        }

        setTutorialTakingOver(active) {
            const isActive = active === true;
            this.setAvatarFloatingGuideTutorialMode(isActive);
            const featureController = window.NekoHomeTutorialFeatureController;
            if (
                featureController
                && typeof featureController.begin === 'function'
                && typeof featureController.end === 'function'
            ) {
                if (isActive && !this.avatarFloatingGuideSuppressionActive) {
                    featureController.begin('avatar-floating-guide');
                    this.avatarFloatingGuideSuppressionActive = true;
                } else if (!isActive && this.avatarFloatingGuideSuppressionActive) {
                    featureController.end('avatar-floating-guide');
                    this.avatarFloatingGuideSuppressionActive = false;
                }
            }
            try {
                window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
                    detail: {
                        active: isActive,
                        source: 'yui-guide-director',
                        sceneId: this.currentSceneId || ''
                    }
                }));
            } catch (error) {
                console.warn('[YuiGuide] 同步教程期功能暂停状态失败:', error);
            }
            if (this.interactionTakeover && typeof this.interactionTakeover.setActive === 'function') {
                this.interactionTakeover.setActive(isActive);
                return;
            }
            this.overlay.setTakingOver(isActive);
        }

        getAvatarStandInCue(day, sceneId) {
            return this.avatarStandInController.getCue(day, sceneId);
        }

        getAvatarStandInResourcePath(resource) {
            const api = window.YuiGuideAvatarStandIn;
            if (api && typeof api.getResourcePath === 'function') {
                try {
                    return api.getResourcePath(resource);
                } catch (_) {}
            }
            return '';
        }

        scheduleAvatarStandInForScene(scene, day, sceneRunId) {
            return this.avatarStandInController.schedule(scene, day, sceneRunId);
        }

        prepareAvatarStandInOpacityTargets() {
            const elements = this.getReturnPetalTransitionOpacityElements();
            const model = this.getReturnPetalTransitionModel();
            const restores = [];
            elements.forEach((element) => {
                const originalInlineOpacity = element.style.opacity;
                const originalInlineTransition = element.style.transition;
                restores.push(() => {
                    if (originalInlineTransition) {
                        element.style.setProperty('transition', originalInlineTransition);
                    } else {
                        element.style.removeProperty('transition');
                    }
                    if (originalInlineOpacity) {
                        element.style.setProperty('opacity', originalInlineOpacity);
                    } else {
                        element.style.removeProperty('opacity');
                    }
                });
                element.style.setProperty('transition', 'opacity ' + AVATAR_STAND_IN_MODEL_FADE_MS + 'ms ease', 'important');
                void element.offsetWidth;
                element.style.setProperty('opacity', '0', 'important');
            });
            if (model && Number.isFinite(Number(model.alpha))) {
                const originalAlpha = Number(model.alpha);
                restores.push(() => {
                    try {
                        model.alpha = originalAlpha;
                    } catch (_) {}
                });
            }
            this.avatarStandInOpacityRestores = restores;
        }

        hideAvatarStandInModelAlpha() {
            const model = this.getReturnPetalTransitionModel();
            if (!model || !Number.isFinite(Number(model.alpha))) {
                return;
            }
            try {
                model.alpha = 0;
            } catch (_) {}
        }

        showAvatarStandIn(cue, token) {
            if (!cue || token !== this.avatarStandInToken || this.isStopping() || this.destroyed) {
                return;
            }
            this.clearAvatarStandIn({ clearPending: false, restoreModel: true, preserveToken: true });
            this.avatarStandInActive = true;
            this.prepareAvatarStandInOpacityTargets();
            this.avatarStandInFadeTimer = window.setTimeout(() => {
                this.avatarStandInFadeTimer = null;
                if (token !== this.avatarStandInToken || this.isStopping() || this.destroyed) {
                    return;
                }
                this.hideAvatarStandInModelAlpha();
                if (this.overlay && typeof this.overlay.showAvatarStandIn === 'function') {
                    this.overlay.showAvatarStandIn({
                        resource: cue.resource,
                        position: cue.position,
                        durationMs: cue.durationMs,
                        url: this.getAvatarStandInResourcePath(cue.resource)
                    });
                }
                this.avatarStandInHideTimer = window.setTimeout(() => {
                    if (token === this.avatarStandInToken) {
                        this.clearAvatarStandIn({ clearPending: false, restoreModel: true });
                    }
                }, Math.max(0, Number(cue.durationMs) || 0));
            }, AVATAR_STAND_IN_MODEL_FADE_MS);
        }

        clearAvatarStandIn(options) {
            return this.avatarStandInController.clear(options);
        }

        setGuideChatInputLocked(locked, reason) {
            const isLocked = locked === true;
            const lockReason = typeof reason === 'string' && reason
                ? reason
                : 'avatar-floating-guide';
            if (this.chatWindowAdapter && typeof this.chatWindowAdapter.lockInput === 'function') {
                try {
                    this.chatWindowAdapter.lockInput(isLocked, lockReason);
                } catch (error) {
                    console.warn('[YuiGuide] 同步聊天输入锁定状态失败:', error);
                }
            }
            try {
                window.dispatchEvent(new CustomEvent('neko:yui-guide:chat-input-lock-change', {
                    detail: {
                        locked: isLocked,
                        reason: lockReason,
                        timestamp: Date.now()
                    }
                }));
            } catch (_) {}
        }

        setAvatarFloatingGuideTutorialMode(active) {
            const isActive = active === true;
            try {
                if (isActive) {
                    if (!this.avatarFloatingGuideTutorialModeActive) {
                        this.avatarFloatingGuidePreviousIsInTutorial = window.isInTutorial === true;
                        this.avatarFloatingGuideTutorialModeActive = true;
                    }
                    window.isInTutorial = true;
                    return;
                }
                if (this.avatarFloatingGuideTutorialModeActive) {
                    window.isInTutorial = this.avatarFloatingGuidePreviousIsInTutorial === true;
                    this.avatarFloatingGuideTutorialModeActive = false;
                    this.avatarFloatingGuidePreviousIsInTutorial = false;
                }
            } catch (error) {
                console.warn('[YuiGuide] 同步全局教程状态失败:', error);
            }
        }

        enforceAvatarFloatingGuideFeatureSuppression(reason) {
            const featureController = window.NekoHomeTutorialFeatureController;
            if (featureController && typeof featureController.enforce === 'function') {
                try {
                    featureController.enforce(reason || 'avatar-floating-guide');
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] failed to enforce tutorial feature suppression:', error);
                }
            }

            const reactChatHost = window.reactChatWindowHost;
            if (reactChatHost && typeof reactChatHost.setGalgameModeEnabled === 'function') {
                try {
                    reactChatHost.setGalgameModeEnabled(false, {
                        persist: false,
                        suppressRefetch: true
                    });
                } catch (error) {
                    console.warn('[YuiGuide] failed to force-disable GalGame during guide:', error);
                }
            }
            const proactiveKeys = [
                'proactiveChatEnabled',
                'proactiveVisionEnabled',
                'proactiveVisionChatEnabled',
                'proactiveNewsChatEnabled',
                'proactiveVideoChatEnabled',
                'proactivePersonalChatEnabled',
                'proactiveMusicEnabled',
                'proactiveMemeEnabled',
                'proactiveMiniGameInviteEnabled'
            ];
            const appState = window.appState || null;
            proactiveKeys.forEach((key) => {
                window[key] = false;
                if (appState && typeof appState[key] !== 'undefined') {
                    appState[key] = false;
                }
            });
            [
                'stopProactiveChatSchedule',
                'stopProactiveVisionDuringSpeech',
                'releaseProactiveVisionStream'
            ].forEach((methodName) => {
                if (typeof window[methodName] === 'function') {
                    try {
                        window[methodName]();
                    } catch (error) {
                        console.warn('[YuiGuide] failed to stop proactive feature during guide:', methodName, error);
                    }
                }
            });
            try {
                window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
                    detail: {
                        active: true,
                        enforced: true,
                        source: 'yui-guide-director',
                        reason: reason || 'avatar-floating-guide',
                        sceneId: this.currentSceneId || ''
                    }
                }));
            } catch (error) {
                console.warn('[YuiGuide] failed to broadcast enforced tutorial feature suppression:', error);
            }
        }

        async ensureAvatarFloatingGuideSurfaceReady(round) {
            try {
                await this.ensureChatVisible();
            } catch (error) {
                console.warn('[YuiGuide] failed to ensure chat window before avatar floating guide:', error);
            }
            this.enforceAvatarFloatingGuideFeatureSuppression(
                'avatar-floating-day' + Number(round) + '-surface-ready'
            );
        }

        isIntroActivationTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            return !!(
                target.closest('#react-chat-window-root .composer-input')
                || target.closest('#react-chat-window-root .composer-input-shell')
                || target.closest('#react-chat-window-root .composer-panel')
                || target.closest('#text-input-area')
                || target.closest('#textInputBox')
            );
        }

        isGuideMessageActionTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            return !!target.closest('[data-guide-message="true"] .message-action-button');
        }

        finishIntroActivation() {
            if (!this.awaitingIntroActivation) {
                return false;
            }

            this.awaitingIntroActivation = false;
            if (typeof this._introActivationResolve === 'function') {
                this._introActivationResolve();
            }
            return true;
        }

        waitForIntroActivationClick() {
            this.awaitingIntroActivation = true;
            return new Promise((resolve) => {
                let resolved = false;

                const cleanup = () => {
                    document.removeEventListener('pointerdown', onPointerLike, true);
                    document.removeEventListener('mousedown', onPointerLike, true);
                    document.removeEventListener('touchstart', onPointerLike, true);
                    document.removeEventListener('click', onPointerLike, true);
                    document.removeEventListener('keydown', onKeyDown, true);
                    this._introActivationResolve = null;
                };
                const complete = () => {
                    if (resolved) {
                        return;
                    }
                    resolved = true;
                    cleanup();
                    this.awaitingIntroActivation = false;
                    resolve();
                };
                const onPointerLike = (event) => {
                    if (!event || !this.isIntroActivationTarget(event.target)) {
                        return;
                    }
                    complete();
                };
                const onKeyDown = (event) => {
                    if (
                        !event
                        || (event.key !== 'Enter' && event.key !== ' ')
                        || !this.isIntroActivationTarget(event.target)
                    ) {
                        return;
                    }
                    complete();
                };

                this._introActivationResolve = complete;
                document.addEventListener('pointerdown', onPointerLike, true);
                document.addEventListener('mousedown', onPointerLike, true);
                document.addEventListener('touchstart', onPointerLike, true);
                document.addEventListener('click', onPointerLike, true);
                document.addEventListener('keydown', onKeyDown, true);
            });
        }

        shouldReduceTutorialMotion() {
            try {
                return !!(
                    window.matchMedia
                    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
                );
            } catch (_) {
                return false;
            }
        }

        getStep(stepId) {
            if (!stepId) {
                return null;
            }

            if (this.registry && typeof this.registry.getStep === 'function') {
                return this.registry.getStep(stepId) || null;
            }

            return null;
        }

        getHomePresentationSceneOrder() {
            if (!this.registry || !this.registry.sceneOrder || !Array.isArray(this.registry.sceneOrder.home)) {
                return [];
            }

            return this.registry.sceneOrder.home.filter(function (sceneId) {
                return (
                    typeof sceneId === 'string'
                    && sceneId.indexOf('interrupt_') !== 0
                    && sceneId.indexOf('handoff_') !== 0
                );
            });
        }

        getBubbleMetaForScene(sceneId) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            if (this.page !== 'home') {
                return '';
            }

            if (normalizedSceneId === 'intro_activation') {
                return '准备开始';
            }

            const order = this.getHomePresentationSceneOrder();
            const index = order.indexOf(normalizedSceneId);
            if (index === -1 || order.length <= 0) {
                return '';
            }

            return '主页引导 ' + (index + 1) + '/' + order.length;
        }

        showGuideBubble(text, options, sceneId) {
            const normalizedOptions = Object.assign({}, options || {});
            const bubbleVariant = typeof normalizedOptions.bubbleVariant === 'string'
                ? normalizedOptions.bubbleVariant.trim()
                : '';
            const hidesMeta = bubbleVariant === 'intro-activation' || bubbleVariant === 'plugin-manual-open';
            if (hidesMeta) {
                normalizedOptions.meta = '';
            } else if (!normalizedOptions.meta) {
                normalizedOptions.meta = this.getBubbleMetaForScene(sceneId || this.currentSceneId);
            }
            this.overlay.showBubble(text, normalizedOptions);
        }

        recordExperienceMetric(type, detail) {
            if (!this.experienceMetrics || typeof this.experienceMetrics.record !== 'function') {
                return null;
            }

            const payload = Object.assign({
                page: this.page || '',
                sceneId: this.currentSceneId || ''
            }, detail && typeof detail === 'object' ? detail : {});

            try {
                return this.experienceMetrics.record(type, payload);
            } catch (_) {
                return null;
            }
        }

        resolveModelPrefix() {
            if (this.tutorialManager && this.tutorialManager._tutorialModelPrefix) {
                return this.tutorialManager._tutorialModelPrefix;
            }

            if (this.tutorialManager && this.tutorialManager.constructor && typeof this.tutorialManager.constructor.detectModelPrefix === 'function') {
                return this.tutorialManager.constructor.detectModelPrefix();
            }

            if (window.universalTutorialManager &&
                window.universalTutorialManager.constructor &&
                typeof window.universalTutorialManager.constructor.detectModelPrefix === 'function') {
                return window.universalTutorialManager.constructor.detectModelPrefix();
            }

            return 'live2d';
        }

        getAvatarFloatingActiveModelType() {
            const normalize = (value) => {
                const modelType = String(value || '').trim().toLowerCase();
                return modelType === 'live2d' || modelType === 'vrm' || modelType === 'mmd' || modelType === 'pngtuber'
                    ? modelType
                    : '';
            };
            const runtimeType = normalize(
                typeof window.getActiveModelType === 'function' ? window.getActiveModelType() : ''
            );
            if (runtimeType) {
                return runtimeType;
            }

            const cfg = window.lanlan_config;
            if (cfg) {
                const modelType = String(cfg.model_type || '').toLowerCase();
                if (modelType === 'live3d') {
                    const subType = normalize(cfg.live3d_sub_type);
                    return subType || 'vrm';
                }
                return normalize(modelType) || 'live2d';
            }
            return '';
        }

        expandSelector(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return '';
            }

            return selector.replace(/\$\{p\}/g, this.resolveModelPrefix());
        }

        resolveElement(selector) {
            const expanded = this.expandSelector(selector);
            if (!expanded) {
                return null;
            }

            try {
                return document.querySelector(expanded);
            } catch (error) {
                console.warn('[YuiGuide] 查询元素失败:', expanded, error);
                return null;
            }
        }

        queryDocumentSelector(selector) {
            const normalizedSelector = typeof selector === 'string' ? selector.trim() : '';
            if (!normalizedSelector) {
                return null;
            }

            try {
                return document.querySelector(normalizedSelector);
            } catch (error) {
                console.warn('[YuiGuide] document.querySelector 查询失败:', normalizedSelector, error);
                return null;
            }
        }

        resolveRect(selector) {
            if (selector === 'body') {
                return {
                    left: 0,
                    top: 0,
                    right: window.innerWidth,
                    bottom: window.innerHeight,
                    width: window.innerWidth,
                    height: window.innerHeight
                };
            }

            const element = this.resolveElement(selector);
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            return element.getBoundingClientRect();
        }

        getDefaultCursorOrigin() {
            const chatInputTarget = this.getChatInputTarget();
            const chatInputRect = chatInputTarget && typeof chatInputTarget.getBoundingClientRect === 'function'
                ? chatInputTarget.getBoundingClientRect()
                : null;
            if (chatInputRect && chatInputRect.width > 0 && chatInputRect.height > 0) {
                return {
                    x: chatInputRect.left + (chatInputRect.width / 2),
                    y: chatInputRect.top + (chatInputRect.height / 2)
                };
            }

            const prefix = this.resolveModelPrefix();
            const modelRect = this.resolveRect('#' + prefix + '-container');
            if (modelRect) {
                return {
                    x: modelRect.left + (modelRect.width / 2),
                    y: modelRect.top + Math.min(modelRect.height * 0.55, modelRect.height - 16)
                };
            }

            return {
                x: Math.max(120, window.innerWidth * 0.72),
                y: Math.max(120, window.innerHeight * 0.45)
            };
        }

        getViewportCenter() {
            return {
                x: window.innerWidth / 2,
                y: window.innerHeight / 2
            };
        }

        getReturnPetalTransitionOrigin() {
            const prefix = this.resolveModelPrefix();
            const manager = prefix === 'live2d'
                ? window.live2dManager
                : (prefix === 'vrm' ? window.vrmManager : window.mmdManager);
            try {
                if (manager && typeof manager.getModelScreenBounds === 'function') {
                    const bounds = manager.getModelScreenBounds();
                    if (
                        bounds
                        && Number.isFinite(Number(bounds.centerX))
                        && Number.isFinite(Number(bounds.centerY))
                    ) {
                        return {
                            x: Number(bounds.centerX),
                            y: Number(bounds.centerY)
                        };
                    }
                }
            } catch (_) {}

            const modelRect = this.resolveRect('#' + prefix + '-container');
            if (modelRect) {
                return {
                    x: modelRect.left + modelRect.width / 2,
                    y: modelRect.top + modelRect.height / 2
                };
            }

            return this.getViewportCenter();
        }

        getReturnPetalTransitionModel() {
            return this.petalTransitionController.getReturnModel();
        }

        collectReturnPetalTransitionManagers() {
            return this.petalTransitionController.collectReturnManagers();
        }

        getReturnPetalTransitionOpacityElements() {
            return this.petalTransitionController.getReturnOpacityElements();
        }

        prepareReturnPetalTransitionOpacityTargets(model) {
            return this.petalTransitionController.prepareReturnOpacityTargets(model);
        }

        restoreReturnPetalTransitionOpacityTargets() {
            return this.petalTransitionController.restoreOpacityTargets();
        }

        getReturnPetalSequenceUrl() {
            return RETURN_PETAL_SEQUENCE_URL;
        }

        loadReturnPetalSequence() {
            return this.petalTransitionController.preloadReturnPetalSequence();
        }

        getReturnPetalTransitionRemainingMs(voiceKey, fallbackText) {
            const playbackSnapshot = this.voiceQueue && typeof this.voiceQueue.capturePlaybackSnapshot === 'function'
                ? this.voiceQueue.capturePlaybackSnapshot()
                : null;
            if (
                playbackSnapshot
                && playbackSnapshot.voiceKey === voiceKey
                && Number.isFinite(playbackSnapshot.durationMs)
                && playbackSnapshot.durationMs > 0
            ) {
                return Math.max(
                    0,
                    Math.round(playbackSnapshot.durationMs - Math.max(0, playbackSnapshot.currentTimeMs || 0))
                );
            }

            const fullDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const cueMs = this.resolveGuideVoiceCueTargetMs(
                voiceKey,
                'returnPetalTransition',
                fullDurationMs,
                fallbackText || ''
            );
            return Math.max(0, Math.round(fullDurationMs - cueMs));
        }

        fadeReturnPetalTransitionModelOut(durationMs) {
            return this.petalTransitionController.fadeReturnModelOut(durationMs);
        }

        createReturnPetalTransition(origin, options) {
            return this.petalTransitionController.createReturnPetalTransition(origin, options);
        }

        async restoreTutorialAvatarForReturnPetalTransition() {
            return this.petalTransitionController.restoreTutorialAvatarForReturn();
        }

        async playReturnPetalTransition(options) {
            return this.petalTransitionController.playReturn(options);
        }

        resolveGuideCopy(textKey, fallbackText) {
            return translateGuideText(textKey, fallbackText);
        }

        resolveAvatarFloatingSceneText(scene) {
            if (scene && scene.id === 'day2_intro_context') {
                return hasAvatarFloatingGuideUsage('voiceUsed')
                    ? this.resolveGuideCopy('tutorial.avatarFloating.day2.introVoiceUsed', scene.text || '')
                    : '昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。';
            }
            return this.resolveGuideCopy(scene.textKey || '', scene.text || '');
        }

        resolveAvatarFloatingSceneVoiceKey(scene) {
            if (scene && scene.id === 'day2_intro_context' && hasAvatarFloatingGuideUsage('voiceUsed')) {
                return 'avatar_floating_day2_intro_voice_used';
            }
            return scene && typeof scene.voiceKey === 'string' ? scene.voiceKey : '';
        }

        resolveAvatarFloatingSceneEmotion(scene) {
            if (scene && scene.id === 'day2_intro_context') {
                return hasAvatarFloatingGuideUsage('voiceUsed') ? 'happy' : 'sad';
            }
            return scene && typeof scene.emotion === 'string' ? scene.emotion : '';
        }

        getAvatarFloatingSceneButtons(scene) {
            return [];
        }

        installGuideMessageActionHandler() {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.setOnMessageAction !== 'function') {
                return false;
            }

            host.setOnMessageAction(this.guideMessageActionHandler);
            this.guideMessageActionHandlerInstalled = true;
            return true;
        }

        uninstallGuideMessageActionHandler() {
            if (!this.guideMessageActionHandlerInstalled) {
                return;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.setOnMessageAction === 'function') {
                host.setOnMessageAction(null);
            }
            this.guideMessageActionHandlerInstalled = false;
        }

        beginGuideMessageActionWait(buttons, timeoutMs) {
            const guideButtons = Array.isArray(buttons) ? buttons : [];
            if (guideButtons.length === 0) {
                return null;
            }

            this.clearPendingGuideMessageAction();
            const normalizedTimeoutMs = Number.isFinite(timeoutMs)
                ? Math.max(0, Math.round(timeoutMs))
                : 12000;
            return new Promise((resolve) => {
                const actionNames = new Set(guideButtons.map((button) => String(button.action || button.id || '')));
                const pending = {
                    actionNames: actionNames,
                    resolve: resolve,
                    timeoutId: 0
                };
                this.pendingGuideMessageAction = pending;
                if (normalizedTimeoutMs > 0) {
                    this.armPendingGuideMessageActionTimeout(normalizedTimeoutMs);
                }
            });
        }

        armPendingGuideMessageActionTimeout(timeoutMs) {
            const pending = this.pendingGuideMessageAction;
            if (!pending || pending.timeoutId) {
                return false;
            }

            const delay = Number.isFinite(timeoutMs) ? Math.max(0, Math.round(timeoutMs)) : 12000;
            pending.timeoutId = window.setTimeout(() => {
                if (this.pendingGuideMessageAction !== pending) {
                    return;
                }
                this.pendingGuideMessageAction = null;
                pending.resolve({
                    action: 'avatar-floating-guide-timeout',
                    timedOut: true
                });
            }, delay);
            return true;
        }

        clearPendingGuideMessageAction() {
            const pending = this.pendingGuideMessageAction;
            if (!pending) {
                return;
            }

            if (pending.timeoutId) {
                window.clearTimeout(pending.timeoutId);
            }
            this.pendingGuideMessageAction = null;
            if (typeof pending.resolve === 'function') {
                pending.resolve({
                    action: 'avatar-floating-guide-cancelled'
                });
            }
        }

        resolveGuideMessageAction(action) {
            const pending = this.pendingGuideMessageAction;
            if (!pending || !action) {
                return false;
            }

            const actionName = String(action.action || action.id || '');
            if (!pending.actionNames || !pending.actionNames.has(actionName)) {
                return false;
            }

            if (pending.timeoutId) {
                window.clearTimeout(pending.timeoutId);
            }
            this.pendingGuideMessageAction = null;
            pending.resolve(action);
            return true;
        }

        disableGuideMessageButtons(message) {
            if (!message || !message.id || !Array.isArray(message.blocks)) {
                return;
            }

            const blocks = message.blocks.map((block) => {
                if (!block || block.type !== 'buttons' || !Array.isArray(block.buttons)) {
                    return block;
                }

                return Object.assign({}, block, {
                    buttons: block.buttons.map((button) => Object.assign({}, button, {
                        disabled: true
                    }))
                });
            });
            this.updateGuideChatMessage(message.id, {
                blocks: blocks
            });
        }

        handleGuideMessageAction(message, action) {
            if (!this.pendingGuideMessageAction) {
                return;
            }

            this.disableGuideMessageButtons(message);
            this.resolveGuideMessageAction(action);
        }

        applyGuideEmotion(emotion, options) {
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return;
            }

            const normalizedOptions = options || {};
            const allowDuringInterrupt = !!normalizedOptions.allowDuringInterrupt;

            if (this.guideInterruptPresentationActive && !allowDuringInterrupt) {
                return;
            }

            this.activeGuideEmotion = normalizedEmotion;
            this.emotionBridge.apply(normalizedEmotion);
        }

        clearGuidePresentation() {
            if (this.guideInterruptPresentationActive) {
                return;
            }
            this.activeGuideEmotion = '';
            this.emotionBridge.clear();
        }

        clearQueuedGuideChatBridgeMessages() {
            if (
                this.chatBridgeCommandBus
                && typeof this.chatBridgeCommandBus.clearQueue === 'function'
            ) {
                this.chatBridgeCommandBus.clearQueue();
                return;
            }
            try {
                if (window.localStorage) {
                    window.localStorage.removeItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
                }
            } catch (_) {}
        }

        beginGuideInterruptPresentation() {
            this.guideInterruptPresentationActive = true;
            this.voiceQueue.stopGuideMouthMotion();
            this.activeGuideEmotion = '';
            this.emotionBridge.clear();
        }

        endGuideInterruptPresentation() {
            this.guideInterruptPresentationActive = false;
        }

        captureCurrentGuidePresentationSnapshot() {
            const activeExpressionFile = this.emotionBridge && typeof this.emotionBridge.getActiveGuideExpressionFile === 'function'
                ? this.emotionBridge.getActiveGuideExpressionFile()
                : '';

            if (this.activeGuideEmotion || activeExpressionFile) {
                return {
                    emotion: this.activeGuideEmotion,
                    expressionFile: activeExpressionFile
                };
            }

            return null;
        }

        restoreGuidePresentationSnapshot(snapshot) {
            if (!snapshot) {
                return false;
            }

            let restored = false;
            if (snapshot.emotion) {
                this.applyGuideEmotion(snapshot.emotion);
                restored = true;
            }

            if (snapshot.expressionFile && this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile(snapshot.expressionFile);
                restored = true;
            }

            if (restored) {
                return true;
            }

            this.clearGuidePresentation();
            return true;
        }

        async speakGuideLine(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';

            if (!content) {
                return;
            }

            await this.speakLineAndWait(content, options || {});
        }

        resolvePerformanceBubbleText(performance) {
            const normalizedPerformance = performance || {};
            return this.resolveGuideCopy(
                normalizedPerformance.bubbleTextKey || '',
                normalizedPerformance.bubbleText || ''
            );
        }

        resolvePerformanceResistanceVoices(performance) {
            const normalizedPerformance = performance || {};
            const fallbacks = Array.isArray(normalizedPerformance.resistanceVoices)
                ? normalizedPerformance.resistanceVoices
                : [];
            const keys = Array.isArray(normalizedPerformance.resistanceVoiceKeys)
                ? normalizedPerformance.resistanceVoiceKeys
                : [];

            return fallbacks.map((fallbackText, index) => {
                return this.resolveGuideCopy(keys[index] || '', fallbackText);
            });
        }

        getElementRect(element) {
            return this.spotlightController.getElementRect(element);
        }

        createVirtualSpotlight(key, rect, options) {
            return this.spotlightController.createVirtualSpotlight(key, rect, options);
        }

        createPluginManagementEntrySpotlight(button) {
            const rect = this.getElementRect(button);
            if (!rect) {
                return button || null;
            }

            return this.createVirtualSpotlight('plugin-management-entry', {
                left: rect.left - PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                top: rect.top - PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y,
                right: rect.right + PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                bottom: rect.bottom + PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y
            }, {
                padding: 0,
                radius: 18
            }) || button;
        }

        createUnionSpotlight(key, elements, options) {
            return this.spotlightController.createUnionSpotlight(key, elements, options);
        }

        clearVirtualSpotlight(key) {
            this.spotlightController.clearVirtualSpotlight(key);
        }

        clearAllVirtualSpotlights() {
            this.spotlightController.clearAllVirtualSpotlights();
        }

        clearSpotlightVariantHints() {
            this.spotlightController.clearSpotlightVariantHints();
        }

        clearSpotlightGeometryHints() {
            this.spotlightController.clearSpotlightGeometryHints();
        }

        setSpotlightGeometryHint(element, options) {
            this.spotlightController.setSpotlightGeometryHint(element, options);
        }

        setSpotlightVariantHints(entries) {
            this.spotlightController.setSpotlightVariantHints(entries);
        }

        syncExtraSpotlights() {
            this.spotlightController.syncExtraSpotlights();
        }

        addRetainedExtraSpotlight(element) {
            this.spotlightController.addRetainedExtraSpotlight(element);
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            this.spotlightController.replaceRetainedExtraSpotlight(matcher, element);
        }

        removeRetainedExtraSpotlight(matcher) {
            this.spotlightController.removeRetainedExtraSpotlight(matcher);
        }

        clearRetainedExtraSpotlights() {
            this.spotlightController.clearRetainedExtraSpotlights();
        }

        setSceneExtraSpotlights(elements) {
            this.spotlightController.setSceneExtraSpotlights(elements);
        }

        clearSceneExtraSpotlights() {
            this.spotlightController.clearSceneExtraSpotlights();
        }

        clearAllExtraSpotlights() {
            this.spotlightController.clearAllExtraSpotlights();
        }

        cleanupTutorialReturnButtons() {
            [
                '#live2d-btn-return',
                '#live2d-return-button-container',
                '#vrm-btn-return',
                '#vrm-return-button-container',
                '#mmd-btn-return',
                '#mmd-return-button-container'
            ].forEach((selector) => {
                document.querySelectorAll(selector).forEach((element) => {
                    if (element && typeof element.remove === 'function') {
                        element.remove();
                    }
                });
            });
        }

        getAgentToggleElement(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-toggle-' + toggleId);
        }

        getAgentToggleCheckbox(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-' + toggleId);
        }

        getAgentSidePanelButton(toggleId, actionId) {
            if (!toggleId || !actionId) {
                return null;
            }

            return document.getElementById('neko-sidepanel-action-' + toggleId + '-' + actionId);
        }

        getAgentSidePanel(toggleId) {
            if (!toggleId) {
                return null;
            }

            return document.querySelector('[data-neko-sidepanel-type="' + toggleId + '-actions"]');
        }

        isAgentSidePanelVisible(toggleId) {
            const sidePanel = this.getAgentSidePanel(toggleId);
            return !!(sidePanel && sidePanel.style.display === 'flex' && sidePanel.style.opacity !== '0');
        }

        async waitForAgentSidePanelLayoutStable(toggleId, timeoutMs) {
            const sidePanel = await this.waitForElement(() => {
                const panel = this.getAgentSidePanel(toggleId);
                return panel && this.isAgentSidePanelVisible(toggleId) ? panel : null;
            }, Number.isFinite(timeoutMs) ? Math.max(260, timeoutMs) : 900);
            if (!sidePanel) {
                return null;
            }

            // AvatarPopupUI may run an edge-overlap self-correction after the expand
            // animation starts. Wait through that correction window before sampling.
            if (!(await this.waitForSceneDelay(380))) {
                return null;
            }

            return this.waitForStableElementRect(
                sidePanel,
                Number.isFinite(timeoutMs) ? timeoutMs : 560
            );
        }

        collapseAgentSidePanel(toggleId) {
            const sidePanel = this.getAgentSidePanel(toggleId);
            if (!sidePanel) {
                return false;
            }

            if (sidePanel._hoverCollapseTimer) {
                window.clearTimeout(sidePanel._hoverCollapseTimer);
                sidePanel._hoverCollapseTimer = null;
            }

            if (sidePanel._collapseTimeout) {
                window.clearTimeout(sidePanel._collapseTimeout);
                sidePanel._collapseTimeout = null;
            }

            if (typeof sidePanel._collapse === 'function') {
                sidePanel._collapse();
                return true;
            }

            sidePanel.style.transition = 'none';
            sidePanel.style.opacity = '0';
            sidePanel.style.display = 'none';
            sidePanel.style.pointerEvents = 'none';
            sidePanel.style.transition = '';
            return true;
        }

        getCharacterAppearanceMenuId() {
            const prefix = this.resolveModelPrefix();
            if (prefix === 'vrm') {
                return 'vrm-manage';
            }
            if (prefix === 'mmd') {
                return 'mmd-manage';
            }
            return 'live2d-manage';
        }

        getTutorialModelManagerLanlanName() {
            const explicitName = typeof window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME === 'string'
                ? window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME.trim()
                : '';
            if (explicitName) {
                return explicitName;
            }

            return DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME;
        }

        getModelManagerWindowName(lanlanName, appearanceMenuId) {
            const name = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            const menuId = appearanceMenuId || this.getCharacterAppearanceMenuId();
            if (menuId === 'vrm-manage') {
                return 'vrm-manage_' + encodeURIComponent(name);
            }
            if (menuId === 'mmd-manage') {
                return 'mmd-manage_' + encodeURIComponent(name);
            }
            return 'live2d-manage_' + encodeURIComponent(name);
        }

        getCharacterMenuElement(menuId) {
            if (!menuId) {
                return null;
            }

            return this.resolveElement('#${p}-sidepanel-' + menuId);
        }

        getCharacterSettingsSidePanel() {
            return document.querySelector('[data-neko-sidepanel-type="character-settings"]');
        }

        getFloatingButtonShell(element) {
            return this.spotlightController.getFloatingButtonShell(element);
        }

        isCircularFloatingButtonSpotlight(element) {
            return this.spotlightController.isCircularFloatingButtonSpotlight(element);
        }

        applyCircularFloatingButtonSpotlightHint(element) {
            return this.spotlightController.applyCircularFloatingButtonSpotlightHint(element);
        }

        getSettingsPeekTargets() {
            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            return {
                characterMenu: this.getSettingsMenuElement('character'),
                appearanceItem: this.getCharacterMenuElement(appearanceMenuId),
                voiceCloneItem: this.getCharacterMenuElement('voice-clone')
            };
        }

        getDay4SettingsButtonSpotlightTarget() {
            return this.getFloatingButtonShell(
                this.getFallbackFloatingButton('settings')
                || this.resolveElement('#${p}-btn-settings')
            );
        }

        getDay4SettingsButtonPersistenceTarget(sceneId) {
            if ([
                'day4_chat_settings',
                'day4_model_behavior',
                'day4_gaze_follow',
                'day4_privacy_mode'
            ].includes(sceneId)) {
                return this.getDay4SettingsButtonSpotlightTarget();
            }
            if (sceneId === 'day4_model_lock' || sceneId === 'day4_return_home' || sceneId === 'day4_wrap') {
                return null;
            }
            return undefined;
        }

        getDay4MouseTrackingTarget() {
            const checkbox = this.resolveElement('#${p}-mouse-tracking-toggle');
            if (!checkbox) {
                return null;
            }
            const switchRow = typeof checkbox.closest === 'function'
                ? checkbox.closest('[role="switch"]')
                : null;
            const target = switchRow || checkbox.parentElement || checkbox;
            return target && this.isElementVisible(target) ? target : null;
        }

        getAvatarFloatingLockIconElement() {
            const prefixes = [];
            const addPrefix = (value) => {
                const prefix = typeof value === 'string' ? value.trim().toLowerCase() : '';
                if (prefix && !prefixes.includes(prefix)) {
                    prefixes.push(prefix);
                }
            };
            addPrefix(this.getAvatarFloatingActiveModelType());
            addPrefix(this.resolveModelPrefix());
            ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach(addPrefix);

            for (let index = 0; index < prefixes.length; index += 1) {
                const lockIcon = document.getElementById(prefixes[index] + '-lock-icon');
                if (lockIcon) {
                    return lockIcon;
                }
            }
            return null;
        }

        getDay4LockButtonSpotlightTarget() {
            const lockIcon = this.getAvatarFloatingLockIconElement();
            if (!lockIcon) {
                return null;
            }
            lockIcon.style.setProperty('display', 'block', 'important');
            lockIcon.style.setProperty('visibility', 'visible', 'important');
            lockIcon.style.setProperty('opacity', '1', 'important');
            return this.getFloatingButtonShell(lockIcon) || lockIcon;
        }

        getDay4PrivacyModeButtonTarget() {
            const privacyPanel = this.getAvatarFloatingSidePanel('interval-proactive-vision');
            const anchor = privacyPanel && privacyPanel._anchorElement
                ? privacyPanel._anchorElement
                : null;
            if (anchor && this.isElementVisible(anchor)) {
                return anchor;
            }

            const toggle = this.resolveElement('#${p}-toggle-proactive-vision');
            const switchRow = toggle && typeof toggle.closest === 'function'
                ? toggle.closest('[role="switch"]')
                : null;
            const target = switchRow || (toggle ? toggle.parentElement : null) || toggle;
            return target && this.isElementVisible(target) ? target : null;
        }

        getDay5CharacterSettingsButtonTarget() {
            const characterPanel = this.getAvatarFloatingSidePanel('character-settings');
            const anchor = characterPanel && characterPanel._anchorElement
                ? characterPanel._anchorElement
                : null;
            return anchor && this.isElementVisible(anchor) ? anchor : null;
        }

        getDay2CharacterSettingsPersistenceTarget(sceneId) {
            if (sceneId === 'day2_personalization_detail') {
                return this.getDay5CharacterSettingsButtonTarget()
                    || this.getSettingsMenuElement('character');
            }
            return undefined;
        }

        refreshSettingsPeekSpotlights(settingsButton) {
            const targets = this.getSettingsPeekTargets();
            const normalizeVisibleTarget = (element) => this.isElementVisible(element) ? element : null;
            const settingsButtonTarget = normalizeVisibleTarget(
                this.getFloatingButtonShell(
                    settingsButton
                    || this.getFallbackFloatingButton('settings')
                    || this.resolveElement('#${p}-btn-settings')
                )
            );
            const characterMenu = normalizeVisibleTarget(targets.characterMenu);
            const appearanceItem = normalizeVisibleTarget(targets.appearanceItem);
            const voiceCloneItem = normalizeVisibleTarget(targets.voiceCloneItem);
            const sidePanel = this.getCharacterSettingsSidePanel();
            const sidePanelVisible = sidePanel && this.isElementVisible(sidePanel) ? sidePanel : null;
            const characterChildrenBundle = sidePanelVisible
                ? this.createUnionSpotlight(
                    'settings-character-children-bundle',
                    [sidePanelVisible],
                    {
                        padding: DEFAULT_SPOTLIGHT_PADDING,
                        radius: 18
                    }
                )
                : (appearanceItem && voiceCloneItem)
                    ? this.createUnionSpotlight(
                        'settings-character-children-bundle',
                        [appearanceItem, voiceCloneItem],
                        {
                            padding: DEFAULT_SPOTLIGHT_PADDING,
                            radius: 18
                        }
                    )
                    : null;
            this.setSceneExtraSpotlights([
                settingsButtonTarget,
                characterMenu,
                characterChildrenBundle
            ].filter(Boolean));

            return {
                settingsButton: settingsButtonTarget,
                characterMenu: characterMenu,
                appearanceItem: appearanceItem,
                voiceCloneItem: voiceCloneItem,
                characterChildrenBundle: characterChildrenBundle
            };
        }

        async ensureCharacterSettingsSidePanelVisible() {
            const sidePanel = this.getCharacterSettingsSidePanel();
            const anchor = this.getSettingsMenuElement('character');
            if (!sidePanel || !anchor) {
                return false;
            }

            this.sidebarPauseController.trackPanel(sidePanel);
            this.collapseAvatarFloatingSidePanelsExcept(sidePanel);
            if (typeof sidePanel._expand === 'function') {
                sidePanel._expand();
            } else {
                anchor.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            }

            const visiblePanel = await this.waitForVisibleElement(() => this.getCharacterSettingsSidePanel(), 1600);
            return !!visiblePanel;
        }

        collapseCharacterSettingsSidePanel() {
            const sidePanel = this.getCharacterSettingsSidePanel();
            if (!sidePanel) {
                return;
            }

            this.sidebarPauseController.trackPanel(sidePanel);
            if (sidePanel._hoverCollapseTimer) {
                window.clearTimeout(sidePanel._hoverCollapseTimer);
                sidePanel._hoverCollapseTimer = null;
            }

            if (typeof sidePanel._collapse === 'function') {
                sidePanel._collapse();
            } else {
                if (sidePanel._collapseTimeout) {
                    window.clearTimeout(sidePanel._collapseTimeout);
                    sidePanel._collapseTimeout = null;
                }
                sidePanel.style.transition = 'none';
                sidePanel.style.opacity = '0';
                sidePanel.style.display = 'none';
                sidePanel.style.pointerEvents = 'none';
                sidePanel.style.transition = '';
            }
        }

        normalizeHighlightTarget(target, fallbackKey) {
            return this.spotlightController.normalizeHighlightTarget(target, fallbackKey);
        }

        applyGuideHighlights(config) {
            const highlights = this.spotlightController.applyGuideHighlights(config);
            if (Object.prototype.hasOwnProperty.call(config || {}, 'secondary')) {
                this.customSecondarySpotlightTarget = highlights.secondary || null;
            }
            return highlights;
        }

        clearIntroFlow() {
            this.overlay.clearSpotlight();
        }

        waitForElement(resolveElement, timeoutMs) {
            const resolver = typeof resolveElement === 'function' ? resolveElement : function () { return null; };
            const timeout = Number.isFinite(timeoutMs) ? timeoutMs : 4000;

            return new Promise((resolve) => {
                const startedAt = Date.now();
                let pausedAt = 0;
                let pausedTotalMs = 0;
                const tick = () => {
                    if (this.isStopping()) {
                        resolve(null);
                        return;
                    }

                    const now = Date.now();
                    if (this.scenePausedForResistance) {
                        if (!pausedAt) {
                            pausedAt = now;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, now - pausedAt);
                        pausedAt = 0;
                    }

                    const element = resolver();
                    if (element) {
                        resolve(element);
                        return;
                    }

                    if ((now - startedAt - pausedTotalMs) >= timeout) {
                        resolve(null);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        isElementVisible(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return false;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return false;
            }

            if (element.offsetParent !== null) {
                return true;
            }

            try {
                return window.getComputedStyle(element).position === 'fixed';
            } catch (_) {
                return false;
            }
        }

        waitForVisibleElement(resolveElement, timeoutMs) {
            return this.waitForElement(() => {
                const element = typeof resolveElement === 'function' ? resolveElement() : null;
                return (this.getElementRect(element) || this.isElementVisible(element)) ? element : null;
            }, timeoutMs);
        }

        waitForDocumentSelector(selector, timeoutMs, requireVisible) {
            const normalizedSelector = this.expandSelector(typeof selector === 'string' ? selector.trim() : '');
            if (!normalizedSelector) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                const element = this.queryDocumentSelector(normalizedSelector);
                if (!element) {
                    return null;
                }

                if (!shouldRequireVisible) {
                    return element;
                }

                return this.isElementVisible(element) ? element : null;
            }, timeoutMs);
        }

        waitForAnyDocumentSelector(selectors, timeoutMs, requireVisible) {
            const normalizedSelectors = (Array.isArray(selectors) ? selectors : [])
                .map((selector) => this.expandSelector(typeof selector === 'string' ? selector.trim() : ''))
                .filter(Boolean);
            if (normalizedSelectors.length === 0) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                for (let index = 0; index < normalizedSelectors.length; index += 1) {
                    const element = this.queryDocumentSelector(normalizedSelectors[index]);
                    if (!element) {
                        continue;
                    }

                    if (!shouldRequireVisible || this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForVisibleTarget(targets, timeoutMs) {
            const normalizedTargets = Array.isArray(targets) ? targets.slice() : [];
            if (normalizedTargets.length === 0) {
                return Promise.resolve(null);
            }

            return this.waitForElement(() => {
                for (let index = 0; index < normalizedTargets.length; index += 1) {
                    const target = normalizedTargets[index];
                    let element = null;

                    if (typeof target === 'function') {
                        try {
                            element = target.call(this);
                        } catch (error) {
                            console.warn('[YuiGuide] 解析目标元素失败:', error);
                            element = null;
                        }
                    } else if (typeof target === 'string') {
                        element = this.queryDocumentSelector(target);
                    }

                    if (this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForStableElementRect(element, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 900;
            if (!element) {
                return Promise.resolve(null);
            }

            return new Promise((resolve) => {
                const startedAt = Date.now();
                let pausedAt = 0;
                let pausedTotalMs = 0;
                let lastRect = null;
                let stableCount = 0;

                const tick = () => {
                    if (this.destroyed) {
                        resolve(null);
                        return;
                    }

                    const now = Date.now();
                    if (this.scenePausedForResistance) {
                        if (!pausedAt) {
                            pausedAt = now;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, now - pausedAt);
                        pausedAt = 0;
                    }

                    if (!this.isElementVisible(element)) {
                        if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    const rect = element.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) {
                        if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (lastRect) {
                        const delta = Math.max(
                            Math.abs(rect.left - lastRect.left),
                            Math.abs(rect.top - lastRect.top),
                            Math.abs(rect.width - lastRect.width),
                            Math.abs(rect.height - lastRect.height)
                        );
                        stableCount = delta <= 1 ? (stableCount + 1) : 0;
                    }
                    lastRect = {
                        left: rect.left,
                        top: rect.top,
                        width: rect.width,
                        height: rect.height
                    };

                    if (stableCount >= 2) {
                        resolve(element);
                        return;
                    }

                    if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                        resolve(element);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        getChatIntroTarget() {
            return this.getChatInputTarget() || this.getChatWindowTarget();
        }

        getChatInputTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .compact-chat-surface-frame[data-compact-chat-state="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-geometry-part="capsuleBody"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .compact-chat-surface-frame',
                '#react-chat-window-root .compact-chat-surface-shell',
                '#react-chat-window-root .composer-input',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        getChatCapsuleInputTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-part="capsuleBody"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root .composer-panel',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return this.getChatInputTarget();
        }

        getChatWindowTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .compact-chat-surface-frame',
                '#react-chat-window-root .compact-chat-surface-shell',
                '#react-chat-window-shell',
                '#react-chat-window-root .chat-window',
                '#react-chat-window-root',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        shouldNarrateInChat(stepId) {
            if (this.page !== 'home' || typeof stepId !== 'string' || !stepId) {
                return false;
            }
            return true;
        }

        isHomeChatExternalized() {
            if (typeof document === 'undefined') {
                return false;
            }
            if (window.__NEKO_MULTI_WINDOW__ === true) {
                return true;
            }
            const overlay = document.getElementById('react-chat-window-overlay');
            if (!overlay) {
                return false;
            }
            // CSS [hidden] 规则用 !important 控制可见性，不会写 inline style。
            // 内联 display:none 仅由外部 preload（如 preload-pet.js）设置以永久
            // 隐藏 Pet 窗口里嵌着的 React 聊天 overlay。
            return overlay.style.display === 'none';
        }

        getRecentExternalizedChatCursorScreenPoint(maxAgeMs) {
            try {
                const raw = window.localStorage && window.localStorage.getItem(YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY);
                const parsed = raw ? JSON.parse(raw) : null;
                if (!parsed || !Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) {
                    return null;
                }
                const at = Number(parsed.at);
                const ageLimit = Number.isFinite(maxAgeMs) ? maxAgeMs : 30000;
                if (Number.isFinite(at) && Date.now() - at > ageLimit) {
                    return null;
                }
                return { x: parsed.x, y: parsed.y };
            } catch (_) {
                return null;
            }
        }

        screenPointToLocalPoint(point) {
            if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
                return null;
            }

            let bounds = null;
            try {
                const host = window.nekoTutorialOverlay;
                const metrics = host && typeof host.getWindowMetricsSync === 'function'
                    ? host.getWindowMetricsSync()
                    : null;
                bounds = metrics && (metrics.bounds || metrics.contentBounds);
            } catch (_) {
                bounds = null;
            }
            if (!bounds) {
                bounds = {
                    x: Number.isFinite(window.screenX) ? window.screenX : 0,
                    y: Number.isFinite(window.screenY) ? window.screenY : 0
                };
            }

            const viewport = window.visualViewport || null;
            const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
            const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
            return {
                x: point.x - Number(bounds.x || 0) - offsetLeft,
                y: point.y - Number(bounds.y || 0) - offsetTop
            };
        }

        localPointToScreenPoint(point) {
            if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
                return null;
            }

            let bounds = null;
            try {
                const host = window.nekoTutorialOverlay;
                const metrics = host && typeof host.getWindowMetricsSync === 'function'
                    ? host.getWindowMetricsSync()
                    : null;
                bounds = metrics && (metrics.bounds || metrics.contentBounds);
            } catch (_) {
                bounds = null;
            }
            if (!bounds) {
                bounds = {
                    x: Number.isFinite(window.screenX) ? window.screenX : 0,
                    y: Number.isFinite(window.screenY) ? window.screenY : 0
                };
            }

            const viewport = window.visualViewport || null;
            const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
            const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
            return {
                x: Number(bounds.x || 0) + point.x + offsetLeft,
                y: Number(bounds.y || 0) + point.y + offsetTop
            };
        }

        rememberExternalizedChatCursorHandoffPoint(kind, effect) {
            const localPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const screenPoint = this.localPointToScreenPoint(localPoint);
            if (!screenPoint) {
                return false;
            }
            try {
                window.localStorage.setItem(YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY, JSON.stringify({
                    x: screenPoint.x,
                    y: screenPoint.y,
                    kind: typeof kind === 'string' ? kind : '',
                    effect: typeof effect === 'string' ? effect : '',
                    source: 'home-director-handoff',
                    at: Date.now()
                }));
                return true;
            } catch (_) {
                return false;
            }
        }

        restoreCursorFromExternalizedChatAnchor(maxAgeMs) {
            if (!this.isHomeChatExternalized() || this.cursor.hasPosition()) {
                return false;
            }
            const screenPoint = this.getRecentExternalizedChatCursorScreenPoint(maxAgeMs);
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return false;
            }
            this.cursor.showAt(localPoint.x, localPoint.y);
            return true;
        }

        getExternalizedChatCursorAnchorPoint(maxAgeMs) {
            if (!this.isHomeChatExternalized()) {
                return null;
            }
            const latestPoint = this.cursorAnchorStore.getLatestExternalizedPoint(maxAgeMs);
            if (latestPoint) {
                return latestPoint;
            }
            const screenPoint = this.getRecentExternalizedChatCursorScreenPoint(maxAgeMs);
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return null;
            }
            return {
                x: localPoint.x,
                y: localPoint.y
            };
        }

        rememberAvatarFloatingSceneCursorAnchorFromExternalizedChat(sceneId, maxAgeMs) {
            const localPoint = this.getExternalizedChatCursorAnchorPoint(maxAgeMs);
            if (!localPoint) {
                return false;
            }
            this.rememberAvatarFloatingSceneCursorAnchorPoint(sceneId, localPoint);
            return true;
        }

        getExternalizedChatAnchorMoveDurationMs(fromPoint, toPoint) {
            if (
                !fromPoint
                || !toPoint
                || !Number.isFinite(fromPoint.x)
                || !Number.isFinite(fromPoint.y)
                || !Number.isFinite(toPoint.x)
                || !Number.isFinite(toPoint.y)
            ) {
                return 0;
            }
            const distance = Math.hypot(toPoint.x - fromPoint.x, toPoint.y - fromPoint.y);
            return distance < 2 ? 0 : 760;
        }

        moveHomeCursorToExternalizedChatAnchor(localPoint, detail) {
            const anchorDetail = detail || {};
            const currentPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const hasCurrentPoint = !!(
                currentPoint
                && Number.isFinite(currentPoint.x)
                && Number.isFinite(currentPoint.y)
            );
            const hasVisibleCursor = typeof this.cursor.hasVisiblePosition === 'function'
                ? this.cursor.hasVisiblePosition()
                : this.cursor.hasPosition();
            const runCursorEffect = () => {
                if (anchorDetail.effect === 'wobble') {
                    const effectDurationMs = Number.isFinite(anchorDetail.effectDurationMs)
                        ? Math.max(0, Math.floor(anchorDetail.effectDurationMs))
                        : 0;
                    this.cursor.wobble(effectDurationMs);
                }
            };
            const rememberMovePromise = (movePromise) => {
                this.latestExternalizedChatCursorMoveSceneId = this.currentSceneId || '';
                this.latestExternalizedChatCursorMovePromise = Promise.resolve(movePromise)
                    .then(() => true)
                    .catch(() => false);
                this.resolveExternalizedChatCursorMoveWaiters(
                    this.latestExternalizedChatCursorMoveSceneId,
                    this.latestExternalizedChatCursorMovePromise
                );
                return this.latestExternalizedChatCursorMovePromise;
            };
            const isSettledPcAnchor = !!(
                anchorDetail.settled === true
                && this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
                && typeof this.overlay.syncCursorPosition === 'function'
            );

            if (isSettledPcAnchor) {
                const movePromise = Promise.resolve(
                    this.overlay.syncCursorPosition(localPoint.x, localPoint.y, true)
                );
                rememberMovePromise(movePromise);
                movePromise.then(runCursorEffect).catch(() => {});
                return;
            }

            if (!hasVisibleCursor && hasCurrentPoint) {
                this.cursor.showAt(currentPoint.x, currentPoint.y);
            }

            if (hasVisibleCursor || hasCurrentPoint) {
                const durationMs = this.getExternalizedChatAnchorMoveDurationMs(currentPoint, localPoint);
                const movePromise = durationMs > 0
                    ? this.cursor.moveToPoint(localPoint.x, localPoint.y, {
                        durationMs: durationMs,
                        cancelCheck: () => this.isStopping()
                    })
                    : Promise.resolve(true);
                rememberMovePromise(movePromise);
                movePromise
                    .then(() => {
                        if (durationMs <= 0 && !hasVisibleCursor) {
                            this.cursor.showAt(localPoint.x, localPoint.y);
                        }
                        runCursorEffect();
                    })
                    .catch(() => {});
                return;
            }

            this.cursor.showAt(localPoint.x, localPoint.y);
            rememberMovePromise(Promise.resolve(true));
            runCursorEffect();
        }

        resolveExternalizedChatCursorMoveWaiters(sceneId, movePromise) {
            const waiters = Array.isArray(this.pendingExternalizedChatCursorMoveWaiters)
                ? this.pendingExternalizedChatCursorMoveWaiters
                : [];
            if (!waiters.length) {
                return;
            }
            const actualSceneId = typeof sceneId === 'string' ? sceneId : '';
            const remaining = [];
            waiters.forEach((waiter) => {
                if (!waiter || typeof waiter.finish !== 'function') {
                    return;
                }
                if (waiter.sceneId && actualSceneId && waiter.sceneId !== actualSceneId) {
                    remaining.push(waiter);
                    return;
                }
                if (waiter.sceneId && !actualSceneId) {
                    remaining.push(waiter);
                    return;
                }
                Promise.resolve(movePromise).then(
                    () => waiter.finish(true),
                    () => waiter.finish(false)
                );
            });
            this.pendingExternalizedChatCursorMoveWaiters = remaining;
        }

        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            const movePromise = this.latestExternalizedChatCursorMovePromise;
            const expectedSceneId = typeof sceneId === 'string' ? sceneId : '';
            const actualSceneId = this.latestExternalizedChatCursorMoveSceneId || '';
            const timeoutMs = Number.isFinite(maxWaitMs)
                ? Math.max(0, Math.floor(maxWaitMs))
                : 1600;
            const waitForPromise = (promise) => new Promise((resolve) => {
                let settled = false;
                let timer = 0;
                const finish = (value) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (timer) {
                        window.clearTimeout(timer);
                    }
                    resolve(!!value);
                };
                Promise.resolve(promise).then(
                    () => finish(true),
                    () => finish(false)
                );
                if (timeoutMs > 0) {
                    timer = window.setTimeout(() => finish(false), timeoutMs);
                }
            });
            if (movePromise && !(expectedSceneId && actualSceneId && actualSceneId !== expectedSceneId)) {
                return waitForPromise(movePromise);
            }
            if (timeoutMs <= 0) {
                return Promise.resolve(false);
            }
            this.pendingExternalizedChatCursorMoveWaiters = Array.isArray(this.pendingExternalizedChatCursorMoveWaiters)
                ? this.pendingExternalizedChatCursorMoveWaiters
                : [];
            return new Promise((resolve) => {
                const waiter = {
                    sceneId: expectedSceneId,
                    timer: 0,
                    settled: false,
                    finish: (value) => {
                        if (waiter.settled) {
                            return;
                        }
                        waiter.settled = true;
                        if (waiter.timer) {
                            window.clearTimeout(waiter.timer);
                            waiter.timer = 0;
                        }
                        resolve(!!value);
                    }
                };
                waiter.timer = window.setTimeout(() => {
                    this.pendingExternalizedChatCursorMoveWaiters = (this.pendingExternalizedChatCursorMoveWaiters || [])
                        .filter((candidate) => candidate !== waiter);
                    waiter.finish(false);
                }, timeoutMs);
                this.pendingExternalizedChatCursorMoveWaiters.push(waiter);
            });
        }

        onExternalChatCursorAnchor(event) {
            if (this.destroyed || !this.isHomeChatExternalized()) {
                return;
            }
            const detail = event && event.detail ? event.detail : {};
            const screenPoint = {
                x: Number(detail.x),
                y: Number(detail.y)
            };
            if (!Number.isFinite(screenPoint.x) || !Number.isFinite(screenPoint.y)) {
                return;
            }
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return;
            }
            this.cursorAnchorStore.rememberLatestExternalizedPoint({
                x: localPoint.x,
                y: localPoint.y,
                at: Number(detail.timestamp) || Date.now(),
                kind: typeof detail.kind === 'string' ? detail.kind : '',
                effect: typeof detail.effect === 'string' ? detail.effect : '',
                effectDurationMs: Number.isFinite(detail.effectDurationMs)
                    ? Math.max(0, Math.floor(detail.effectDurationMs))
                    : 0,
                settled: detail.settled === true
            });
            if (this.currentSceneId) {
                this.rememberAvatarFloatingSceneCursorAnchorPoint(this.currentSceneId, localPoint);
            }
            if (
                detail.kind
                && this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
            ) {
                this.moveHomeCursorToExternalizedChatAnchor(localPoint, detail);
            }
        }

        onExternalChatReady() {
            if (this.destroyed) {
                return;
            }

            if (this.interactionTakeover && typeof this.interactionTakeover.onExternalChatReady === 'function') {
                this.interactionTakeover.onExternalChatReady();
            }
        }

        postExternalChatGuideMessage(message) {
            if (!message || typeof message !== 'object') {
                return false;
            }

            const outgoingMessage = Object.assign({}, message);
            return !!(
                this.chatBridgeCommandBus
                && typeof this.chatBridgeCommandBus.post === 'function'
                && this.chatBridgeCommandBus.post(outgoingMessage)
            );
        }

        getSceneSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'day1_intro_activation' || stepId === 'day1_intro_greeting' || stepId === 'day1_takeover_return_control') {
                return this.getChatInputTarget() || this.getChatWindowTarget() || null;
            }

            if (stepId === 'day1_takeover_capture_cursor') {
                return fallbackTarget;
            }

            if (this.shouldNarrateInChat(stepId)) {
                return this.introGreetingChatHighlightCleared
                    ? (this.getChatInputTarget() || this.getChatWindowTarget() || fallbackTarget)
                    : (this.getChatWindowTarget() || fallbackTarget);
            }

            return fallbackTarget;
        }

        getActionSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'day1_takeover_capture_cursor') {
                return this.getFloatingButtonShell(fallbackTarget) || fallbackTarget;
            }

            return null;
        }

        highlightChatWindow() {
            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.clearHomeSpotlightsForExternalizedChat();
                    this.interactionTakeover.setExternalizedChatSpotlight('input');
                }
                return;
            }

            const target = this.getChatWindowTarget() || this.getChatInputTarget();
            if (!target) {
                return;
            }

            if (typeof target.scrollIntoView === 'function') {
                try {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                        inline: 'nearest'
                    });
                } catch (_) {
                    target.scrollIntoView();
                }
            }

            this.setSpotlightGeometryHint(target, {
                padding: DEFAULT_SPOTLIGHT_PADDING + 3
            });
            this.overlay.setPersistentSpotlight(target);
        }

        clearIntroGreetingChatHighlight() {
            this.introGreetingChatHighlightCleared = true;

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
                return;
            }

            this.overlay.clearPersistentSpotlight();
        }

        getChatIntroActivationTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return this.getChatIntroTarget();
        }

        clearSceneTimers() {
            if (this.sceneResources && typeof this.sceneResources.destroy === 'function') {
                this.sceneResources.destroy();
            } else {
                this.sceneTimers.forEach(function (timerId) {
                    window.clearTimeout(timerId);
                });
            }
            this.sceneTimers.clear();
            this.sceneResources = createYuiGuideScopedTutorialResources();
        }

        clearGuideChatStreamTimers() {
            if (this.guideChatStreamResources && typeof this.guideChatStreamResources.destroy === 'function') {
                this.guideChatStreamResources.destroy();
            } else {
                this.guideChatStreamTimers.forEach(function (timerId) {
                    window.clearTimeout(timerId);
                });
            }
            this.guideChatStreamTimers.clear();
            this.guideChatStreamResources = createYuiGuideScopedTutorialResources();
        }

        scheduleGuideChatStream(callback, delayMs) {
            const timerId = this.guideChatStreamResources.setTimeout(() => {
                this.guideChatStreamTimers.delete(timerId);
                callback();
            }, delayMs);
            this.guideChatStreamTimers.add(timerId);
            return timerId;
        }

        schedule(callback, delayMs) {
            const timerId = this.sceneResources.setTimeout(() => {
                this.sceneTimers.delete(timerId);
                callback();
            }, delayMs);
            this.sceneTimers.add(timerId);
            return timerId;
        }

        clearNarrationResumeTimer() {
            if (this.narrationResumeTimer) {
                window.clearTimeout(this.narrationResumeTimer);
                this.narrationResumeTimer = null;
            }
        }

        pauseCurrentSceneForResistance() {
            this.pauseCoordinator.pauseForResistance();
            if (
                this.interactionTakeover
                && typeof this.interactionTakeover.preserveExternalizedChatSpotlightDuringResistance === 'function'
            ) {
                this.interactionTakeover.preserveExternalizedChatSpotlightDuringResistance();
            }
        }

        resumeCurrentSceneAfterResistance() {
            this.pauseCoordinator.resumeAfterResistance();
        }

        waitUntilSceneResumed() {
            if (!this.scenePausedForResistance) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                this.scenePauseResolvers.push(resolve);
            });
        }

        async waitForSceneDelay(delayMs) {
            const totalMs = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 0;
            if (totalMs <= 0) {
                return true;
            }

            let remainingMs = totalMs;
            let lastTickAt = Date.now();

            while (remainingMs > 0) {
                if (this.isStopping()) {
                    return false;
                }

                if (this.scenePausedForResistance) {
                    await this.waitUntilSceneResumed();
                    lastTickAt = Date.now();
                    continue;
                }

                const sliceMs = Math.min(remainingMs, 80);
                await wait(sliceMs);
                if (this.isStopping()) {
                    return false;
                }

                const now = Date.now();
                remainingMs -= Math.max(0, now - lastTickAt);
                lastTickAt = now;
            }

            return true;
        }

        getGuideTimelineCueConfig(voiceKey, cueName) {
            const normalizedVoiceKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
            const normalizedCueName = typeof cueName === 'string' ? cueName.trim() : '';
            if (!normalizedVoiceKey || !normalizedCueName) {
                return null;
            }

            const steps = this.registry && this.registry.steps && typeof this.registry.steps === 'object'
                ? this.registry.steps
                : {};
            const stepIds = Object.keys(steps);
            for (let index = 0; index < stepIds.length; index += 1) {
                const step = steps[stepIds[index]];
                const performance = step && step.performance ? step.performance : {};
                const timeline = Array.isArray(performance.timeline) ? performance.timeline : [];
                for (let timelineIndex = 0; timelineIndex < timeline.length; timelineIndex += 1) {
                    const cue = timeline[timelineIndex];
                    if (!cue || cue.action !== normalizedCueName || !Number.isFinite(cue.at)) {
                        continue;
                    }

                    const cueVoiceKey = typeof cue.voiceKey === 'string' && cue.voiceKey.trim()
                        ? cue.voiceKey.trim()
                        : (typeof performance.voiceKey === 'string' ? performance.voiceKey.trim() : '');
                    if (cueVoiceKey !== normalizedVoiceKey) {
                        continue;
                    }

                    return {
                        at: clamp(cue.at, 0, 1),
                        fallbackDurationMs: this.getGuideVoiceDurationMs(normalizedVoiceKey, 'zh')
                    };
                }
            }

            const fallbackConfig = getGuideAudioCueConfig(normalizedVoiceKey);
            const fallbackCue = fallbackConfig && fallbackConfig.cues
                ? fallbackConfig.cues[normalizedCueName]
                : null;
            if (!fallbackCue || !Number.isFinite(fallbackCue.at)) {
                return null;
            }
            const fallbackCueLocale = resolveGuideAudioLocale();
            const localeCueAt = fallbackCue.atByLocale && Number.isFinite(fallbackCue.atByLocale[fallbackCueLocale])
                ? fallbackCue.atByLocale[fallbackCueLocale]
                : fallbackCue.at;

            return {
                at: clamp(localeCueAt, 0, 1),
                fallbackDurationMs: Number.isFinite(fallbackConfig.fallbackDurationMs)
                    ? Math.max(1, fallbackConfig.fallbackDurationMs)
                    : 0
            };
        }

        resolveGuideVoiceCueTargetMs(voiceKey, cueName, playbackDurationMs, fallbackText) {
            const cueConfig = this.getGuideTimelineCueConfig(voiceKey, cueName);
            if (!cueConfig) {
                return 0;
            }

            const fallbackDurationMs = Number.isFinite(cueConfig.fallbackDurationMs)
                ? Math.max(1, cueConfig.fallbackDurationMs)
                : 0;
            if (cueConfig.at <= 0) {
                return 0;
            }

            const targetDurationMs = Number.isFinite(playbackDurationMs) && playbackDurationMs > 0
                ? playbackDurationMs
                : this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                    || fallbackDurationMs;
            return clamp(Math.round(targetDurationMs * cueConfig.at), 0, targetDurationMs);
        }

        async waitForNarrationCue(voiceKey, cueName) {
            const activeNarrationAtStart = this.activeNarration;
            const fallbackText = activeNarrationAtStart && activeNarrationAtStart.voiceKey === voiceKey
                ? activeNarrationAtStart.text
                : '';
            const fallbackTargetMs = this.resolveGuideVoiceCueTargetMs(voiceKey, cueName, 0, fallbackText);
            if (fallbackTargetMs <= 0) {
                return true;
            }

            const startedAt = Date.now();
            const maxActiveWaitMs = clamp(fallbackTargetMs + 4500, 1800, 18000);
            let fallbackElapsedMs = 0;
            let pausedAt = 0;
            let pausedTotalMs = 0;
            let lastTickAt = Date.now();
            let sawAudioPlayback = false;

            while (!this.isStopping()) {
                if (this.scenePausedForResistance) {
                    if (!pausedAt) {
                        pausedAt = Date.now();
                    }
                    await this.waitUntilSceneResumed();
                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, Date.now() - pausedAt);
                        pausedAt = 0;
                    }
                    lastTickAt = Date.now();
                    continue;
                }

                if (pausedAt) {
                    pausedTotalMs += Math.max(0, Date.now() - pausedAt);
                    pausedAt = 0;
                }

                if ((Date.now() - startedAt - pausedTotalMs) >= maxActiveWaitMs) {
                    console.warn('[YuiGuide] 旁白 cue 等待超时，继续流程:', voiceKey, cueName);
                    return true;
                }

                const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
                if (playbackSnapshot && playbackSnapshot.voiceKey === voiceKey) {
                    sawAudioPlayback = true;
                    const cueTargetMs = this.resolveGuideVoiceCueTargetMs(
                        voiceKey,
                        cueName,
                        playbackSnapshot.durationMs,
                        fallbackText
                    );
                    if (playbackSnapshot.currentTimeMs >= cueTargetMs) {
                        return true;
                    }

                    await wait(60);
                    lastTickAt = Date.now();
                    continue;
                }

                const activeNarration = this.activeNarration;
                if (sawAudioPlayback && (!activeNarration || activeNarration.voiceKey !== voiceKey)) {
                    return true;
                }

                const sliceMs = Math.min(Math.max(40, fallbackTargetMs - fallbackElapsedMs), 80);
                await wait(sliceMs);
                if (this.isStopping()) {
                    return false;
                }

                const now = Date.now();
                if (!sawAudioPlayback && (!activeNarration || !activeNarration.interrupted)) {
                    fallbackElapsedMs += Math.max(0, now - lastTickAt);
                    if (fallbackElapsedMs >= fallbackTargetMs) {
                        return true;
                    }
                }
                lastTickAt = now;
            }

            return false;
        }

        getGuideVoiceDurationMs(voiceKey, locale) {
            const durationConfig = getGuideAudioDurationConfig(voiceKey);
            if (!durationConfig) {
                return 0;
            }

            const normalizedLocale = resolveGuideAudioLocale(locale || resolveGuideLocale());
            const exactDurationMs = Number.isFinite(durationConfig[normalizedLocale])
                ? durationConfig[normalizedLocale]
                : 0;
            if (exactDurationMs > 0) {
                return exactDurationMs;
            }

            const fallbackDurationMs = Number.isFinite(durationConfig.en)
                ? durationConfig.en
                : (Number.isFinite(durationConfig.zh) ? durationConfig.zh : 0);
            return fallbackDurationMs > 0 ? fallbackDurationMs : 0;
        }

        getGuideVoiceTimingScale(voiceKey) {
            const baseDurationMs = this.getGuideVoiceDurationMs(voiceKey, 'zh');
            if (baseDurationMs <= 0) {
                return 1;
            }

            const currentDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale());
            if (currentDurationMs <= 0) {
                return 1;
            }

            return clamp(currentDurationMs / baseDurationMs, 0.75, 2.5);
        }

        cancelActiveNarration() {
            const narration = this.activeNarration;
            this.activeNarration = null;
            this.clearNarrationResumeTimer();

            if (narration) {
                narration.cancelled = true;
            }
            this.voiceQueue.stop();
            if (narration && typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async runNarration(narration) {
            if (!narration || narration.cancelled || this.destroyed) {
                return;
            }

            if (narration.running) {
                return;
            }

            const playbackStartIndex = clamp(
                Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : 0,
                0,
                narration.text.length
            );
            const playbackText = narration.text.slice(playbackStartIndex);

            if (!playbackText.trim()) {
                narration.resumeIndex = narration.text.length;
                narration.resumeAudioOffsetMs = 0;
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            narration.running = true;
            narration.playbackStartIndex = playbackStartIndex;
            narration.playbackStartAt = Date.now();
            await this.voiceQueue.speak(playbackText, {
                voiceKey: narration.voiceKey,
                startAtMs: Number.isFinite(narration.resumeAudioOffsetMs) ? narration.resumeAudioOffsetMs : 0,
                minDurationMs: Number.isFinite(narration.minDurationMs)
                    ? narration.minDurationMs
                    : 0,
                onBoundary: (event) => {
                    const charIndex = event && Number.isFinite(event.charIndex) ? event.charIndex : 0;
                    const absoluteCharIndex = clamp(
                        narration.playbackStartIndex + charIndex,
                        narration.playbackStartIndex,
                        narration.text.length
                    );
                    narration.resumeIndex = absoluteCharIndex;
                    if (typeof narration.onBoundary === 'function') {
                        try {
                            narration.onBoundary(Object.assign({}, event, {
                                absoluteCharIndex: absoluteCharIndex,
                                fullText: narration.text
                            }));
                        } catch (error) {
                            console.warn('[YuiGuide] 旁白边界扩展回调失败:', error);
                        }
                    }
                }
            });
            narration.running = false;

            if (this.destroyed || narration.cancelled) {
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            if (narration.interrupted) {
                return;
            }

            narration.resumeIndex = narration.text.length;
            narration.resumeAudioOffsetMs = 0;
            if (this.activeNarration === narration) {
                this.activeNarration = null;
            }
            if (typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async speakLineAndWait(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content || this.destroyed) {
                return;
            }

            this.cancelActiveNarration();
            const normalizedOptions = options || {};

            await new Promise((resolve) => {
                const narration = {
                    text: content,
                    voiceKey: typeof normalizedOptions.voiceKey === 'string' ? normalizedOptions.voiceKey : '',
                    resumeIndex: 0,
                    resumeAudioOffsetMs: 0,
                    playbackStartIndex: 0,
                    playbackStartAt: 0,
                    minDurationMs: Number.isFinite(normalizedOptions.minDurationMs)
                        ? normalizedOptions.minDurationMs
                        : 0,
                    onBoundary: typeof normalizedOptions.onBoundary === 'function' ? normalizedOptions.onBoundary : null,
                    resolve: resolve,
                    interrupted: false,
                    cancelled: false,
                    running: false
                };
                this.activeNarration = narration;
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 等待语音结束失败:', error);
                    if (this.activeNarration === narration) {
                        this.activeNarration = null;
                    }
                    resolve();
                });
            });
        }

        interruptNarrationForResistance() {
            const narration = this.activeNarration;
            if (!narration || narration.cancelled) {
                const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
                if (!playbackSnapshot) {
                    return false;
                }

                this.clearNarrationResumeTimer();
                this.voiceQueue.stop();
                return true;
            }

            if (narration.interrupted) {
                return true;
            }

            if (narration.running) {
                const playbackStartIndex = Number.isFinite(narration.playbackStartIndex) ? narration.playbackStartIndex : 0;
                const playbackStartAt = Number.isFinite(narration.playbackStartAt) ? narration.playbackStartAt : 0;
                const elapsedMs = playbackStartAt > 0 ? Math.max(0, Date.now() - playbackStartAt) : 0;
                const estimatedChars = Math.floor(elapsedMs / 280);
                const estimatedIndex = clamp(
                    playbackStartIndex + estimatedChars,
                    playbackStartIndex,
                    narration.text.length
                );
                narration.resumeIndex = Math.max(
                    Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : playbackStartIndex,
                    estimatedIndex
                );
            }

            const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
            this.applyNarrationResumePoint(narration, playbackSnapshot);

            narration.interrupted = true;
            this.clearNarrationResumeTimer();
            this.voiceQueue.stop();
            return true;
        }

        applyNarrationResumePoint(narration, playbackSnapshot) {
            if (!narration || !playbackSnapshot || !Number.isFinite(playbackSnapshot.currentTimeMs)) {
                if (narration) {
                    narration.resumeAudioOffsetMs = 0;
                }
                return;
            }

            const textLength = typeof narration.text === 'string' ? narration.text.length : 0;
            const configuredDurationMs = this.getGuideVoiceDurationMs(narration.voiceKey, resolveGuideLocale());
            const durationMs = Number.isFinite(playbackSnapshot.durationMs) && playbackSnapshot.durationMs > 0
                ? Math.round(playbackSnapshot.durationMs)
                : (Number.isFinite(configuredDurationMs) && configuredDurationMs > 0 ? Math.round(configuredDurationMs) : 0);
            const rawOffsetMs = Math.max(0, Math.round(playbackSnapshot.currentTimeMs));
            const maxResumeOffsetMs = durationMs > 0
                ? Math.max(0, durationMs - NARRATION_RESUME_MIN_REMAINING_MS)
                : rawOffsetMs;
            const resumeAudioOffsetMs = clamp(
                rawOffsetMs - NARRATION_RESUME_BACKTRACK_MS,
                0,
                maxResumeOffsetMs
            );
            narration.resumeAudioOffsetMs = resumeAudioOffsetMs;

            if (durationMs <= 0 || textLength <= 0) {
                return;
            }

            const audioProgressIndex = clamp(
                Math.floor((resumeAudioOffsetMs / durationMs) * textLength),
                0,
                Math.max(0, textLength - 1)
            );
            narration.resumeIndex = audioProgressIndex;
        }

        scheduleNarrationResume(options) {
            this.clearNarrationResumeTimer();
            const resumeOptions = options || {};

            const attemptResume = () => {
                const narration = this.activeNarration;
                if (!narration || narration.cancelled || this.destroyed) {
                    this.restoreCurrentScenePresentation({
                        skipEmotion: !!resumeOptions.skipEmotion,
                        preserveSpotlights: !!resumeOptions.preserveSpotlights
                    });
                    return;
                }

                if (!narration.interrupted) {
                    return;
                }

                const lastMotionAt = this.lastPointerPoint && Number.isFinite(this.lastPointerPoint.t)
                    ? this.lastPointerPoint.t
                    : 0;
                if ((Date.now() - lastMotionAt) < 720) {
                    this.narrationResumeTimer = window.setTimeout(attemptResume, 240);
                    return;
                }

                narration.interrupted = false;
                this.restoreCurrentScenePresentation({
                    skipEmotion: !!resumeOptions.skipEmotion,
                    preserveSpotlights: !!resumeOptions.preserveSpotlights
                });
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 恢复教程语音失败:', error);
                });
            };

            this.narrationResumeTimer = window.setTimeout(attemptResume, 720);
        }

        setCurrentScene(stepId, context) {
            this.currentSceneId = stepId || null;
            this.currentStep = stepId ? this.getStep(stepId) : null;
            this.currentContext = context || null;
        }

        restoreCurrentScenePresentation(options) {
            if (this.destroyed || this.angryExitTriggered || !this.currentStep) {
                return;
            }

            if (this.guideInterruptPresentationActive) {
                return;
            }

            const performance = this.currentStep.performance || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);
            if (!(options && options.preserveSpotlights)) {
                const spotlightTarget = this.getSceneSpotlightTarget(this.currentSceneId, performance);
                if (spotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(spotlightTarget);
                    this.overlay.setPersistentSpotlight(spotlightTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }

                const actionSpotlightTarget = this.getActionSpotlightTarget(this.currentSceneId, performance);
                const dedupedActionSpotlightTarget = actionSpotlightTarget === spotlightTarget
                    ? null
                    : actionSpotlightTarget;
                if (dedupedActionSpotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(dedupedActionSpotlightTarget);
                    this.overlay.activateSpotlight(dedupedActionSpotlightTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }

                if (this.customSecondarySpotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(this.customSecondarySpotlightTarget);
                    this.overlay.activateSecondarySpotlight(this.customSecondarySpotlightTarget);
                }
            }

            if (this.shouldNarrateInChat(this.currentSceneId)) {
                this.overlay.hideBubble();
            } else if (bubbleText) {
                this.showGuideBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: this.resolveRect(this.currentStep.anchor)
                }, this.currentSceneId);
            } else {
                this.overlay.hideBubble();
            }

            if (!(options && options.skipEmotion)) {
                if (performance.emotion) {
                    this.applyGuideEmotion(performance.emotion);
                }
            }
        }

        shouldUsePersistentGhostCursorLookAt(stepId) {
            return /^day[1-7]_/.test(String(stepId || ''));
        }

        async syncPersistentGhostCursorLookAtForScene(stepId, runId) {
            if (this.shouldUsePersistentGhostCursorLookAt(stepId)) {
                this.adoptPreTakeoverGhostCursorLookAtHandle();
                return this.ensurePersistentGhostCursorLookAtPerformance({
                    isCancelled: () => this.isStopping()
                });
            }
            const stopReason = stepId === 'takeover_return_control'
                ? 'handoff'
                : 'scene_follow_not_required';
            if (this.preTakeoverGhostCursorLookAtHandle) {
                await this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    stopReason
                );
            }
            await this.stopPersistentGhostCursorLookAtPerformance(
                stopReason
            );
            return null;
        }

        getAvatarFloatingRoundConfig(round) {
            const guideConfig = getYuiGuideDailyGuide(Number(round));
            return guideConfig && guideConfig.round ? guideConfig.round : null;
        }

        getAvatarFloatingInterruptStep(scene) {
            const normalizedScene = scene || {};
            return {
                id: normalizedScene.id || AVATAR_FLOATING_GUIDE_INTERRUPT_STEP.id,
                anchor: normalizedScene.target || '',
                performance: {
                    interruptible: normalizedScene.interruptible !== false,
                    bubbleText: normalizedScene.text || '',
                    bubbleTextKey: normalizedScene.textKey || '',
                    voiceKey: normalizedScene.voiceKey || '',
                    emotion: normalizedScene.emotion || '',
                    cursorTarget: normalizedScene.cursorTarget || normalizedScene.target || '',
                    cursorAction: normalizedScene.cursorAction || ''
                },
                interrupts: AVATAR_FLOATING_GUIDE_INTERRUPT_STEP.interrupts
            };
        }

        getAvatarFloatingBaseTarget(kind) {
            if (kind === 'chat-window') {
                return this.getChatWindowTarget() || this.getChatInputTarget();
            }
            if (kind === 'floating-buttons') {
                return this.resolveElement('#${p}-floating-buttons');
            }
            return null;
        }

        isAvatarFloatingInputIntroScene(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return !!(
                sceneId === 'day2_intro_context'
                || sceneId === 'day3_tool_toggle_intro'
                || sceneId === 'day4_intro_companion'
                || sceneId === 'day5_character_settings'
                || sceneId === 'day6_intro_agent'
                || sceneId === 'day7_memory_review'
            );
        }

        getAvatarFloatingIntroSpotlightTarget(scene) {
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return this.getChatCapsuleInputTarget() || this.getChatInputTarget() || this.getChatWindowTarget();
            }
            return this.getAvatarFloatingBaseTarget('chat-window');
        }

        getAvatarFloatingIntroExternalizedSpotlightKind(scene) {
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return 'capsule-input';
            }
            return 'window';
        }

        getAvatarFloatingIntroExternalizedCursorOptions(scene) {
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return {
                    effect: '',
                    durationMs: 0
                };
            }
            return {
                effect: this.getExternalizedChatCursorEffect(scene)
            };
        }

        getAvatarFloatingSidePanel(type) {
            const normalizedType = typeof type === 'string' ? type.trim() : '';
            return normalizedType
                ? document.querySelector('[data-neko-sidepanel-type="' + normalizedType + '"]')
                : null;
        }

        collapseAvatarFloatingSidePanelsExcept(currentPanel) {
            document.querySelectorAll('[data-neko-sidepanel]').forEach((panel) => {
                if (!panel || panel === currentPanel) {
                    return;
                }

                if (panel._hoverCollapseTimer) {
                    window.clearTimeout(panel._hoverCollapseTimer);
                    panel._hoverCollapseTimer = null;
                }
                if (panel._expandFrameId) {
                    window.cancelAnimationFrame(panel._expandFrameId);
                    panel._expandFrameId = null;
                }
                if (typeof panel._stopHoverPointerTracking === 'function') {
                    panel._stopHoverPointerTracking();
                }
                if (typeof panel._collapse === 'function') {
                    panel._collapse();
                    return;
                }

                if (panel._collapseTimeout) {
                    window.clearTimeout(panel._collapseTimeout);
                    panel._collapseTimeout = null;
                }
                panel.style.transition = 'none';
                panel.style.opacity = '0';
                panel.style.display = 'none';
                panel.style.pointerEvents = 'none';
                panel.style.transition = '';
            });
        }

        forceHideAvatarFloatingSidePanel(panel) {
            if (!panel) {
                return false;
            }

            if (panel._hoverCollapseTimer) {
                window.clearTimeout(panel._hoverCollapseTimer);
                panel._hoverCollapseTimer = null;
            }
            if (panel._collapseTimeout) {
                window.clearTimeout(panel._collapseTimeout);
                panel._collapseTimeout = null;
            }
            if (panel._expandFrameId) {
                window.cancelAnimationFrame(panel._expandFrameId);
                panel._expandFrameId = null;
            }
            if (typeof panel._stopHoverPointerTracking === 'function') {
                panel._stopHoverPointerTracking();
            }

            panel._visibilityRevision = (panel._visibilityRevision || 0) + 1;
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            panel.style.pointerEvents = 'none';
            panel.style.left = '';
            panel.style.right = '';
            panel.style.top = '';
            panel.style.transform = '';
            panel.style.transition = '';
            return true;
        }

        forceHideAvatarFloatingSidePanels() {
            const popupUi = window.AvatarPopupUI || null;
            if (popupUi && typeof popupUi.collapseOtherSidePanels === 'function') {
                try {
                    popupUi.collapseOtherSidePanels(null);
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 强制隐藏首页侧面板失败，回退到本地隐藏:', error);
                }
            }

            let hidden = false;
            document.querySelectorAll('[data-neko-sidepanel]').forEach((panel) => {
                hidden = this.forceHideAvatarFloatingSidePanel(panel) || hidden;
            });
            return hidden;
        }

        forceHideAvatarFloatingGuideManagedSurfaces() {
            this.forceHideManagedPanel('settings');
            this.forceHideManagedPanel('agent');
            this.forceHideAvatarFloatingSidePanels();
        }

        hideTemporaryAvatarFloatingGuideHud(reason) {
            if (
                this.avatarFloatingGuideTemporaryHudShown
                && !this.avatarFloatingGuideTemporaryHudWasVisible
                && window.AgentHUD
                && typeof window.AgentHUD.hideAgentTaskHUD === 'function'
            ) {
                try {
                    window.AgentHUD.hideAgentTaskHUD();
                } catch (error) {
                    console.warn('[YuiGuide] 隐藏教程临时任务 HUD 失败:', reason || 'cleanup', error);
                }
            }
            this.avatarFloatingGuideTemporaryHudShown = false;
            this.avatarFloatingGuideTemporaryHudWasVisible = false;
        }

        async expandAvatarFloatingSidePanel(panel, anchor) {
            if (!panel) {
                return false;
            }
            const targetAnchor = anchor || panel._anchorElement || null;
            this.collapseAvatarFloatingSidePanelsExcept(panel);
            if (typeof panel._expand === 'function') {
                if (panel._hoverCollapseTimer) {
                    window.clearTimeout(panel._hoverCollapseTimer);
                    panel._hoverCollapseTimer = null;
                }
                panel._expand();
            } else if (targetAnchor) {
                try {
                    targetAnchor.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                } catch (_) {}
            }

            return !!(await this.waitForElement(() => {
                return this.isElementVisible(panel) && panel.style.display !== 'none' && panel.style.opacity !== '0'
                    ? panel
                    : null;
            }, 1400));
        }

        async ensureAvatarFloatingSettingsSidePanel(type) {
            const opened = await this.openSettingsPanel();
            if (!opened || this.isStopping()) {
                return null;
            }
            const panel = await this.waitForElement(() => this.getAvatarFloatingSidePanel(type), 1200);
            if (!panel) {
                return null;
            }
            this.sidebarPauseController.trackPanel(panel);
            return (await this.expandAvatarFloatingSidePanel(panel, panel._anchorElement || null)) ? panel : null;
        }

        async ensureAvatarFloatingAgentSidePanel(toggleId) {
            const normalizedToggleId = toggleId === 'openclaw' ? 'agent-openclaw' : 'agent-user-plugin';
            const ready = await this.ensureAgentSidePanelVisible(normalizedToggleId);
            if (!ready || this.isStopping()) {
                return null;
            }
            return this.getAvatarFloatingSidePanel(normalizedToggleId + '-actions');
        }

        getAvatarFloatingAgentCapabilityTargets() {
            return [
                'agent-keyboard',
                'agent-browser',
                'agent-openfang',
                'agent-user-plugin',
                'agent-openclaw'
            ].map((toggleId) => this.getAgentToggleElement(toggleId)).filter(Boolean);
        }

        getAvatarFloatingVisibleChildren(panel, limit) {
            if (!panel || typeof panel.querySelectorAll !== 'function') {
                return [];
            }
            const maxItems = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : 4;
            return Array.from(panel.querySelectorAll('button, [role="button"], [role="switch"], input, a, [id]'))
                .filter((element) => element !== panel && this.isElementVisible(element))
                .slice(0, maxItems);
        }

        getAvatarFloatingCursorTourTargets(scene, primaryTarget) {
            const normalizedScene = scene || {};
            const targetKey = typeof normalizedScene.target === 'string' ? normalizedScene.target : '';
            const operation = typeof normalizedScene.operation === 'string' ? normalizedScene.operation : '';
            if (targetKey === 'agent-capabilities') {
                return this.getAvatarFloatingAgentCapabilityTargets();
            }
            if (targetKey.indexOf('settings-sidepanel:') === 0) {
                return this.getAvatarFloatingVisibleChildren(
                    this.getAvatarFloatingSidePanel(targetKey.split(':')[1] || ''),
                    4
                );
            }
            if (operation.indexOf('show-settings-sidepanel:') === 0) {
                return this.getAvatarFloatingVisibleChildren(
                    this.getAvatarFloatingSidePanel(operation.split(':')[1] || ''),
                    4
                );
            }
            if (primaryTarget && primaryTarget.hasAttribute && primaryTarget.hasAttribute('data-neko-sidepanel')) {
                return this.getAvatarFloatingVisibleChildren(primaryTarget, 4);
            }
            if (
                primaryTarget
                && primaryTarget.id === 'agent-task-hud'
            ) {
                return this.getAvatarFloatingVisibleChildren(primaryTarget, 4);
            }
            return [];
        }

        getChatAvatarToolMenuTargets(limit) {
            const popover = this.getVisibleChatAvatarToolMenuPopover();
            if (!popover || typeof popover.querySelectorAll !== 'function' || !this.isElementVisible(popover)) {
                return [];
            }
            const maxItems = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : 3;
            const targets = Array.from(popover.querySelectorAll('.composer-icon-button[data-avatar-tool-id]'));
            const fallbackTargets = targets.length
                ? targets
                : Array.from(popover.querySelectorAll('.composer-icon-button'));
            return fallbackTargets
                .filter((element) => this.isElementVisible(element))
                .slice(0, maxItems);
        }

        getVisibleChatAvatarToolMenuPopover() {
            const selectors = [
                '#composer-tool-popover',
                '#composer-tool-popover-compact',
                '#react-chat-window-root .composer-icon-popover',
                '.composer-icon-popover'
            ];
            for (let index = 0; index < selectors.length; index += 1) {
                const candidate = this.resolveElement(selectors[index]);
                if (candidate && this.isElementVisible(candidate)) {
                    return candidate;
                }
            }
            return null;
        }

        async waitForAvatarToolMenuTargets(minTargets, timeoutMs) {
            const minimum = Number.isFinite(minTargets) ? Math.max(1, Math.floor(minTargets)) : 3;
            const targets = await this.waitForElement(() => {
                const items = this.getChatAvatarToolMenuTargets(minimum);
                return items.length >= minimum ? items : null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 900);
            return Array.isArray(targets) ? targets : [];
        }

        getVisibleChatComposerElement(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return null;
            }
            const scopedCandidates = Array.from(document.querySelectorAll('#react-chat-window-root ' + selector));
            const globalCandidates = Array.from(document.querySelectorAll(selector));
            return scopedCandidates.concat(globalCandidates)
                .filter((element, index, array) => element && array.indexOf(element) === index)
                .find((element) => this.isElementVisible(element)) || null;
        }

        async ensureChatComposerOverflowMenuOpen() {
            const existingPopover = this.getVisibleChatComposerElement('.composer-overflow-popover');
            if (existingPopover) {
                return true;
            }
            const overflowButton = this.getVisibleChatComposerElement('.composer-overflow-btn');
            if (!overflowButton || typeof overflowButton.click !== 'function') {
                return false;
            }
            overflowButton.click();
            return !!(await this.waitForElement(() => this.getVisibleChatComposerElement('.composer-overflow-popover'), 900));
        }

        async getChatComposerToolButton(selector) {
            const direct = this.getVisibleChatComposerElement(selector);
            if (direct) {
                return direct;
            }
            if (await this.ensureChatComposerOverflowMenuOpen()) {
                return this.getVisibleChatComposerElement(selector);
            }
            return null;
        }

        applyCircleImageSpotlightHints(targets, padding) {
            const items = Array.isArray(targets) ? targets.filter(Boolean) : [];
            items.forEach((target) => {
                this.setSpotlightGeometryHint(target, {
                    padding: Number.isFinite(padding) ? padding : 6,
                    geometry: 'circle'
                });
            });
            this.setSpotlightVariantHints(items.map((element) => ({
                element,
                variant: 'circle-image'
            })));
            return items;
        }

        applyPlainCircularSpotlightHints(targets, padding) {
            const items = Array.isArray(targets) ? targets.filter(Boolean) : [];
            items.forEach((target) => {
                this.setSpotlightGeometryHint(target, {
                    padding: Number.isFinite(padding) ? padding : 6,
                    geometry: 'circle'
                });
            });
            this.setSpotlightVariantHints(items.map((element) => ({
                element,
                variant: 'plain-circle'
            })));
            return items;
        }

        applyChatAvatarToolButtonSpotlightHint(element) {
            if (!element) {
                return false;
            }
            this.setSpotlightGeometryHint(element, {
                padding: 4,
                geometry: 'circle'
            });
            this.setSpotlightVariantHints([{
                element,
                variant: 'circle-image'
            }]);
            return true;
        }

        keepAvatarToolButtonHighlightedAfterMenuOpen(element, scene) {
            if (!element) {
                return false;
            }
            this.applyChatAvatarToolButtonSpotlightHint(element);
            this.applyGuideHighlights({
                key: ((scene && scene.id) || 'avatar-tool-menu') + '-button-open',
                primary: element
            });
            return true;
        }

        setChatAvatarToolMenuOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setAvatarToolMenuOpen === 'function'
                && this.chatWindowAdapter.setAvatarToolMenuOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatAvatarToolMenuOpen === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatAvatarToolMenuOpen(desiredOpen, actionReason);
                return true;
            }
            const reactHost = window.reactChatWindowHost || null;
            if (
                !this.isHomeChatExternalized()
                && reactHost
                && typeof reactHost.setAvatarToolMenuOpen === 'function'
            ) {
                reactHost.setAvatarToolMenuOpen(desiredOpen, actionReason);
                return true;
            }
            return false;
        }

        clickChatAvatarToolButton(reason) {
            if (!this.isHomeChatExternalized()) {
                return false;
            }
            if (
                this.interactionTakeover
                && typeof this.interactionTakeover.clickExternalizedChatAvatarToolButton === 'function'
            ) {
                this.interactionTakeover.clickExternalizedChatAvatarToolButton(reason || 'avatar-floating-guide');
                return true;
            }
            return false;
        }

        setCompactToolFanOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactToolFanOpen === 'function'
                && this.chatWindowAdapter.setCompactToolFanOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatCompactToolFanOpen === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCompactToolFanOpen(desiredOpen, actionReason);
                return true;
            }
            if (this.isHomeChatExternalized()) {
                return false;
            }
            const toggle = this.resolveAvatarFloatingSelector('chat-tool-toggle');
            const isOpen = !!(toggle && (
                toggle.getAttribute('aria-expanded') === 'true'
                || toggle.classList.contains('is-open')
            ));
            if (toggle && typeof toggle.click === 'function' && ((open === true && !isOpen) || (open !== true && isOpen))) {
                toggle.click();
                return true;
            }
            return false;
        }

        rotateCompactToolWheelForGuide(direction, stepCount, reason) {
            const normalizedDirection = Number(direction) < 0 ? -1 : 1;
            const normalizedStepCount = Number.isFinite(Number(stepCount))
                ? Math.max(1, Math.min(7, Math.floor(Number(stepCount))))
                : 1;
            return !!(
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.rotateCompactToolWheel === 'function'
                && this.chatWindowAdapter.rotateCompactToolWheel(
                    normalizedDirection,
                    normalizedStepCount,
                    reason || 'avatar-floating-guide'
                )
            );
        }

        setCompactToolWheelIndexForGuide(index, reason) {
            const normalizedIndex = Number.isFinite(Number(index))
                ? Math.max(0, Math.min(6, Math.floor(Number(index))))
                : 0;
            return !!(
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactToolWheelIndex === 'function'
                && this.chatWindowAdapter.setCompactToolWheelIndex(
                    normalizedIndex,
                    reason || 'avatar-floating-guide'
                )
            );
        }

        setCompactHistoryOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactHistoryOpen === 'function'
                && this.chatWindowAdapter.setCompactHistoryOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (this.isHomeChatExternalized()) {
                if (
                    this.interactionTakeover
                    && typeof this.interactionTakeover.setExternalizedChatCompactHistoryOpen === 'function'
                ) {
                    this.interactionTakeover.setExternalizedChatCompactHistoryOpen(desiredOpen, actionReason);
                    return true;
                }
                return false;
            }
            const handle = this.resolveAvatarFloatingSelector('chat-history-handle');
            const isOpen = !!(handle && (
                handle.getAttribute('aria-expanded') === 'true'
                || handle.getAttribute('data-compact-history-open') === 'true'
            ));
            if (handle && typeof handle.click === 'function' && ((desiredOpen && !isOpen) || (!desiredOpen && isOpen))) {
                handle.click();
                return true;
            }
            return false;
        }

        getExternalizedChatTargetKind(targetKey, scene) {
            const registeredKind = this.spotlightController.getExternalKind(targetKey);
            if (registeredKind) {
                return registeredKind;
            }
            if (targetKey === 'chat-window') {
                return 'window';
            }
            if (targetKey === 'chat-tools') {
                return 'input';
            }
            return '';
        }

        getAvatarFloatingCursorTargetKey(scene) {
            if (!scene || typeof scene !== 'object') {
                return '';
            }
            return scene.cursorTarget || scene.target || '';
        }

        getExternalizedChatCursorTargetKind(scene) {
            const registeredKind = this.cursor.getExternalKind(this.getAvatarFloatingCursorTargetKey(scene));
            if (registeredKind) {
                return registeredKind;
            }
            return this.getExternalizedChatTargetKind(scene && scene.target || '', scene);
        }

        getExternalizedChatCursorEffect(scene) {
            if (scene && scene.id === 'day3_avatar_tools') {
                return 'move';
            }
            const action = scene && typeof scene.cursorAction === 'string'
                ? scene.cursorAction
                : '';
            if (action === 'click') {
                return 'click';
            }
            if (action === 'move') {
                return 'move';
            }
            if (scene && typeof scene.id === 'string') {
                const dayMatch = scene.id.match(/^day(\d+)_/);
                if (dayMatch && dayMatch[1] !== '1') {
                    return 'move';
                }
            }
            return 'wobble';
        }

        getExternalizedChatCursorMoveDurationMs(scene, fallbackMs) {
            if (scene && typeof scene.id === 'string' && scene.id.indexOf('day3_') === 0) {
                return 0;
            }
            if (scene && Number.isFinite(scene.cursorMoveDurationMs)) {
                return Math.max(160, Math.floor(scene.cursorMoveDurationMs));
            }
            const action = scene && typeof scene.cursorAction === 'string'
                ? scene.cursorAction
                : '';
            if (action === 'click') {
                return Number.isFinite(fallbackMs)
                    ? Math.max(160, Math.floor(fallbackMs))
                    : 760;
            }
            return 0;
        }

        setHomePcCursorOutputSuppressedForExternalizedChat(suppressed) {
            if (this.overlay && typeof this.overlay.setPcCursorOutputSuppressed === 'function') {
                this.overlay.setPcCursorOutputSuppressed(suppressed === true);
            }
        }

        clearHomeSpotlightsForExternalizedChat() {
            if (this.overlay && typeof this.overlay.clearSpotlight === 'function') {
                this.overlay.clearSpotlight({
                    preservePcOverlaySpotlights: true
                });
            }
        }

        hideHomeCursorForExternalizedChat() {
            if (
                this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
            ) {
                if (this.cursor && typeof this.cursor.clearPosition === 'function') {
                    this.cursor.cancel();
                    if (typeof this.cursor.hide === 'function') {
                        this.cursor.hide();
                    }
                    this.cursor.clearPosition();
                } else if (this.overlay && typeof this.overlay.clearCursorPosition === 'function') {
                    if (this.overlay && typeof this.overlay.hideCursor === 'function') {
                        this.overlay.hideCursor();
                    }
                    this.overlay.clearCursorPosition();
                }
                return;
            }
            this.cursor.hide();
        }

        setExternalizedChatGuideTarget(kind, options) {
            const normalizedKind = typeof kind === 'string' ? kind : '';
            if (
                !this.isHomeChatExternalized()
                || !normalizedKind
                || !this.interactionTakeover
            ) {
                return false;
            }
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.clearHomeSpotlightsForExternalizedChat();
                this.interactionTakeover.setExternalizedChatSpotlight(normalizedKind);
            }
            this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            const effect = options && typeof options.effect === 'string' ? options.effect : 'wobble';
            const effectDurationMs = options && Number.isFinite(options.effectDurationMs)
                ? Math.max(0, Math.floor(options.effectDurationMs))
                : 0;
            this.rememberExternalizedChatCursorHandoffPoint(normalizedKind, effect);
            if (typeof this.interactionTakeover.setExternalizedChatCursor === 'function') {
                const cursorOptions = {
                    effect: effect,
                    effectDurationMs: effectDurationMs,
                    targetIndex: options && Number.isFinite(options.targetIndex)
                        ? Math.max(0, Math.floor(options.targetIndex))
                        : 0
                };
                if (options && Number.isFinite(options.durationMs)) {
                    cursorOptions.durationMs = Math.max(0, Math.floor(options.durationMs));
                }
                this.interactionTakeover.setExternalizedChatCursor(normalizedKind, cursorOptions);
            }
            this.hideHomeCursorForExternalizedChat();
            return true;
        }

        isDay3AvatarToolsSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day3_avatar_tools'
                    || sceneId.indexOf('day3_avatar_tools_') === 0
                )
            );
        }

        isDay3GalgameSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day3_galgame_games'
                    || sceneId.indexOf('day3_galgame_') === 0
                )
            );
        }

        isDay3WrapSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day3_wrap'
                    || sceneId.indexOf('day3_wrap_') === 0
                )
            );
        }

        shouldPreserveExternalizedChatCursor(previousSceneId, scene) {
            const nextSceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return !!(
                (
                    previousSceneId === 'day2_intro_context'
                    && nextSceneId === 'day2_screen_entry'
                )
                || (
                    previousSceneId === 'day1_history_handle'
                    && nextSceneId === 'day1_intro_basic_voice'
                )
                || (
                    previousSceneId === 'day1_intro_basic_voice'
                    && nextSceneId === 'day1_screen_entry'
                )
                || (
                    previousSceneId === 'day1_screen_entry'
                    && nextSceneId === 'day1_screen_entry_invite'
                )
                || (
                    previousSceneId === 'day1_screen_entry_invite'
                    && nextSceneId === 'day1_takeover_capture_cursor'
                )
                || (
                    previousSceneId === 'day1_takeover_capture_cursor'
                    && nextSceneId === 'day1_takeover_return_control'
                )
                || (
                    previousSceneId === 'day3_tool_toggle_intro'
                    && this.isDay3AvatarToolsSceneId(nextSceneId)
                )
                || (
                    this.isDay3AvatarToolsSceneId(previousSceneId)
                    && this.isDay3AvatarToolsSceneId(nextSceneId)
                )
                || (
                    this.isDay3AvatarToolsSceneId(previousSceneId)
                    && this.isDay3GalgameSceneId(nextSceneId)
                )
                || (
                    this.isDay3GalgameSceneId(previousSceneId)
                    && this.isDay3GalgameSceneId(nextSceneId)
                )
                || (
                    this.isDay3WrapSceneId(previousSceneId)
                    && this.isDay3WrapSceneId(nextSceneId)
                )
            );
        }

        shouldPreserveIntroExternalizedChatCursor(scene) {
            return this.isAvatarFloatingInputIntroScene(scene);
        }

        setExternalizedChatCursorEffect(kind, effect, options) {
            if (
                !this.isHomeChatExternalized()
                || !this.interactionTakeover
                || typeof this.interactionTakeover.setExternalizedChatCursor !== 'function'
            ) {
                return false;
            }
            const normalizedKind = typeof kind === 'string' ? kind : '';
            const effectDurationMs = options && Number.isFinite(options.effectDurationMs)
                ? Math.max(0, Math.floor(options.effectDurationMs))
                : 0;
            const cursorOptions = {
                effect: effect || '',
                effectDurationMs: effectDurationMs,
                targetIndex: options && Number.isFinite(options.targetIndex)
                    ? Math.max(0, Math.floor(options.targetIndex))
                    : 0,
                freezePoint: !!(options && options.freezePoint === true)
            };
            if (options && Number.isFinite(options.durationMs)) {
                cursorOptions.durationMs = Math.max(0, Math.floor(options.durationMs));
            }
            if (normalizedKind) {
                this.rememberExternalizedChatCursorHandoffPoint(normalizedKind, cursorOptions.effect);
                this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            } else {
                this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            }
            this.interactionTakeover.setExternalizedChatCursor(normalizedKind, cursorOptions);
            return true;
        }

        clearExternalizedChatSpotlightOnly() {
            if (!this.isHomeChatExternalized() || !this.interactionTakeover) {
                return false;
            }
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.interactionTakeover.setExternalizedChatSpotlight('');
                return true;
            }
            return false;
        }

        clearExternalizedChatGuideTarget(options) {
            if (!this.isHomeChatExternalized() || !this.interactionTakeover) {
                return;
            }
            const shouldClearCursor = !!(options && options.clearCursor === true);
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.interactionTakeover.setExternalizedChatSpotlight('');
            }
            if (
                shouldClearCursor
                && typeof this.interactionTakeover.setExternalizedChatCursor === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCursor('');
                this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            }
        }

        createAvatarFloatingUnionTarget(key, elements, options) {
            const targets = Array.isArray(elements) ? elements.filter(Boolean) : [];
            if (targets.length === 0) {
                return null;
            }
            if (targets.length === 1) {
                return targets[0];
            }
            return this.createUnionSpotlight(key, targets, Object.assign({
                padding: DEFAULT_SPOTLIGHT_PADDING,
                radius: 18
            }, options || {}));
        }

        resolveRegisteredAvatarFloatingSelector(selector) {
            const localSelectors = this.spotlightController.getLocalSelectors(selector);
            if (!Array.isArray(localSelectors) || localSelectors.length === 0) {
                return null;
            }
            for (let index = 0; index < localSelectors.length; index += 1) {
                const target = this.resolveElement(localSelectors[index]);
                if (target) {
                    if (
                        selector === 'chat-tool-toggle'
                        || selector === 'chat-avatar-tools'
                        || selector === 'chat-galgame'
                    ) {
                        this.applyChatAvatarToolButtonSpotlightHint(target);
                    }
                    return target;
                }
            }
            return null;
        }

        resolveAvatarFloatingSelector(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return null;
            }
            if (selector === 'chat-window' || selector === 'floating-buttons') {
                return this.getAvatarFloatingBaseTarget(selector);
            }
            const registeredTarget = this.resolveRegisteredAvatarFloatingSelector(selector);
            if (registeredTarget) {
                return registeredTarget;
            }
            if (selector === 'chat-input') {
                return this.getChatInputTarget();
            }
            if (selector === 'chat-capsule-input') {
                return this.getChatCapsuleInputTarget();
            }
            if (selector === 'chat-history-handle') {
                return this.resolveElement('#react-chat-window-root .compact-history-visibility-handle')
                    || this.resolveElement('.compact-history-visibility-handle');
            }
            if (selector === 'chat-tool-toggle') {
                const button = this.resolveElement('#react-chat-window-root .send-button-circle.compact-input-tool-toggle')
                    || this.resolveElement('.send-button-circle.compact-input-tool-toggle');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return null;
            }
            if (selector === 'chat-tools') {
                return this.resolveElement('#react-chat-window-root .composer-bottom-tools')
                    || this.resolveElement('#react-chat-window-root .composer-panel')
                    || this.getAvatarFloatingBaseTarget('chat-window');
            }
            if (selector === 'chat-avatar-tools') {
                const button = this.getVisibleChatComposerElement('.compact-input-tool-item-avatar .composer-emoji-btn')
                    || this.getVisibleChatComposerElement('.compact-input-tool-item-avatar')
                    || this.getVisibleChatComposerElement('.composer-emoji-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.getVisibleChatComposerElement('.composer-tool-menu')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            if (selector === 'chat-avatar-tool-items') {
                return this.createAvatarFloatingUnionTarget(
                    'chat-avatar-tool-items',
                    this.getChatAvatarToolMenuTargets()
                );
            }
            if (selector === 'chat-galgame') {
                const button = this.getVisibleChatComposerElement('.compact-input-tool-item-galgame')
                    || this.getVisibleChatComposerElement('.composer-galgame-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.getVisibleChatComposerElement('.composer-tool-menu')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            if (selector === 'chat-choice-slot') {
                return this.resolveElement('#react-chat-window-root .composer-choice-slot')
                    || this.resolveElement('#react-chat-window-root .composer-galgame-slot')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            return this.resolveElement(selector);
        }

        getMiniGameChoiceTargets(limit) {
            const maxTargets = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 3;
            if (maxTargets <= 0) {
                return [];
            }
            const choiceSlot = this.resolveElement(
                '#react-chat-window-root .composer-choice-slot[data-choice-source="mini_game_invite"]'
            );
            if (!choiceSlot || !this.isElementVisible(choiceSlot)) {
                return [];
            }
            return Array.from(choiceSlot.querySelectorAll('.composer-choice-option, .composer-galgame-option'))
                .filter((element, index, array) => element && array.indexOf(element) === index)
                .filter((element) => this.isElementVisible(element))
                .slice(0, maxTargets);
        }

        async tourPlainCircularTargets(targets, options) {
            const normalizedOptions = options || {};
            const items = this.applyPlainCircularSpotlightHints(targets, normalizedOptions.padding);
            if (items.length === 0) {
                return false;
            }
            this.setSceneExtraSpotlights(items);
            for (let index = 0; index < items.length; index += 1) {
                if (this.isStopping()) {
                    return false;
                }
                const moved = await this.moveCursorToElement(
                    items[index],
                    index === 0 ? (normalizedOptions.firstMoveMs || 560) : (normalizedOptions.moveMs || 420)
                );
                if (moved && !this.isStopping()) {
                    this.cursor.wobble();
                    await this.waitForSceneDelay(normalizedOptions.pauseMs || 180);
                }
            }
            return true;
        }

        async tourExternalizedChatTargets(kind, count, options) {
            const normalizedKind = typeof kind === 'string' ? kind : '';
            if (
                !this.isHomeChatExternalized()
                || !normalizedKind
                || !this.interactionTakeover
                || typeof this.interactionTakeover.setExternalizedChatSpotlight !== 'function'
                || typeof this.interactionTakeover.setExternalizedChatCursor !== 'function'
            ) {
                return false;
            }
            const normalizedOptions = options || {};
            const total = Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 3;
            this.clearHomeSpotlightsForExternalizedChat();
            this.interactionTakeover.setExternalizedChatSpotlight(normalizedKind);
            this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            this.hideHomeCursorForExternalizedChat();
            for (let index = 0; index < total; index += 1) {
                if (this.isStopping()) {
                    return false;
                }
                this.interactionTakeover.setExternalizedChatCursor(normalizedKind, {
                    effect: typeof normalizedOptions.effect === 'string' ? normalizedOptions.effect : 'move',
                    targetIndex: index
                });
                await this.waitForSceneDelay(index === 0
                    ? (normalizedOptions.firstPauseMs || 560)
                    : (normalizedOptions.pauseMs || 420));
            }
            return true;
        }

        async tourAvatarToolMenuItems() {
            if (this.isHomeChatExternalized()) {
                return this.tourExternalizedChatTargets('avatar-tool-items', 3, {
                    effect: 'move',
                    firstPauseMs: 560,
                    pauseMs: 420
                });
            }
            return this.tourPlainCircularTargets(this.getChatAvatarToolMenuTargets(3), {
                padding: 6,
                firstMoveMs: 560,
                moveMs: 420,
                pauseMs: 180
            });
        }

        async tourMiniGameChoiceButtons() {
            if (this.isHomeChatExternalized()) {
                return false;
            }
            const targets = this.getMiniGameChoiceTargets(3);
            if (targets.length > 0) {
                this.overlay.clearActionSpotlight();
                this.overlay.clearPersistentSpotlight();
            }
            return this.tourPlainCircularTargets(targets, {
                padding: 6,
                firstMoveMs: 560,
                moveMs: 420,
                pauseMs: 180
            });
        }

        async runDay3GalgameWheelDragScene(scene, primaryTarget) {
            this.setCompactToolFanOpen(true, 'avatar-floating-guide-galgame-tool-fan-open');
            await this.waitForSceneDelay(120);
            const dragArcFraction = 1 / 5;
            const dragSettleWaitMs = 420;
            const dragRotateDirection = -1;
            const dragRotateDelayMs = Math.round(dragSettleWaitMs * 0.45);
            const rotateReason = 'avatar-floating-guide-galgame-drag';
            const buildDay3GalgameWheelArcPoints = (targetElement, fraction, direction, stepCount = 8) => {
                const targetRect = this.getElementRect(targetElement);
                const fan = this.resolveElement('#react-chat-window-root .compact-input-tool-fan')
                    || this.resolveElement('.compact-input-tool-fan');
                const fanRect = fan && typeof fan.getBoundingClientRect === 'function'
                    ? fan.getBoundingClientRect()
                    : null;
                if (!targetRect || !fanRect || fanRect.width <= 0 || fanRect.height <= 0) {
                    return [];
                }
                const fanStyle = window.getComputedStyle ? window.getComputedStyle(fan) : null;
                const readFanPixelVar = (name, fallback) => {
                    const rawValue = fanStyle ? String(fanStyle.getPropertyValue(name) || '').trim() : '';
                    const parsedValue = Number.parseFloat(rawValue);
                    return Number.isFinite(parsedValue) ? parsedValue : fallback;
                };
                const center = {
                    x: fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', 116),
                    y: fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', 116)
                };
                const start = {
                    x: targetRect.left + targetRect.width / 2,
                    y: targetRect.top + targetRect.height / 2
                };
                const radius = Math.hypot(start.x - center.x, start.y - center.y);
                if (!Number.isFinite(radius) || radius < 4) {
                    return [];
                }
                const startAngle = Math.atan2(start.y - center.y, start.x - center.x);
                const totalAngle = (direction < 0 ? -1 : 1) * Math.PI * 2 * Math.max(0, Math.min(1, fraction));
                const count = Math.max(2, Math.floor(stepCount));
                const points = [];
                for (let index = 1; index <= count; index += 1) {
                    const progress = index / count;
                    const angle = startAngle + totalAngle * progress;
                    points.push({
                        x: center.x + Math.cos(angle) * radius,
                        y: center.y + Math.sin(angle) * radius
                    });
                }
                return points;
            };
            const rotateWheelAfterDragThreshold = async () => {
                const waited = await this.waitForSceneDelay(dragRotateDelayMs);
                if (!waited || this.isStopping()) {
                    return false;
                }
                this.rotateCompactToolWheelForGuide(dragRotateDirection, 1, rotateReason);
                return true;
            };

            if (this.isHomeChatExternalized()) {
                this.setExternalizedChatCursorEffect('galgame', 'move');
                await this.waitForExternalizedChatCursorMove(
                    scene && scene.id || 'day3_galgame_entry',
                    1800
                );
                if (this.isStopping()) {
                    return false;
                }
                if (
                    this.interactionTakeover
                    && typeof this.interactionTakeover.arcExternalizedChatCursor === 'function'
                ) {
                    this.interactionTakeover.arcExternalizedChatCursor('galgame', {
                        direction: dragRotateDirection,
                        fraction: dragArcFraction,
                        durationMs: dragSettleWaitMs,
                        effect: 'click',
                        effectDurationMs: dragSettleWaitMs
                    });
                }
                const rotated = await rotateWheelAfterDragThreshold();
                if (!rotated) {
                    return false;
                }
                const remainingDragWaitMs = Math.max(0, dragSettleWaitMs - dragRotateDelayMs);
                await this.waitForSceneDelay(remainingDragWaitMs + 80);
                await this.waitForSceneDelay(260);
                this.setExternalizedChatCursorEffect('galgame', 'click', {
                    durationMs: 0,
                    effectDurationMs: DEFAULT_CURSOR_CLICK_VISIBLE_MS
                });
                await this.waitForExternalizedChatCursorMove(
                    scene && scene.id || 'day3_galgame_entry',
                    DEFAULT_CURSOR_CLICK_VISIBLE_MS + 500
                );
                return true;
            }

            const target = primaryTarget || await this.resolveAvatarFloatingTarget(scene, 'primary');
            const rect = this.getElementRect(target);
            if (!rect) {
                return false;
            }
            const arcPoints = buildDay3GalgameWheelArcPoints(
                target,
                dragArcFraction,
                dragRotateDirection
            );
            if (arcPoints.length === 0) {
                return false;
            }
            this.cursor.click(dragSettleWaitMs);
            let dragFinished = false;
            let dragSucceeded = false;
            const movePromise = this.cursor.moveCursorAlongPoints(arcPoints, {
                durationMs: dragSettleWaitMs,
                effect: 'click',
                effectDurationMs: dragSettleWaitMs,
                pauseCheck: () => this.scenePausedForResistance,
                cancelCheck: () => this.isStopping()
            }).then((moved) => {
                dragFinished = true;
                dragSucceeded = !!moved;
                return moved;
            }, (error) => {
                dragFinished = true;
                dragSucceeded = false;
                throw error;
            });
            const rotatePromise = (async () => {
                const waited = await this.waitForSceneDelay(dragRotateDelayMs);
                if (
                    !waited
                    || this.isStopping()
                    || (dragFinished && !dragSucceeded)
                ) {
                    return false;
                }
                this.rotateCompactToolWheelForGuide(dragRotateDirection, 1, rotateReason);
                return true;
            })();
            const moved = await movePromise;
            const rotated = await rotatePromise;
            if (!moved || this.isStopping()) {
                return false;
            }
            if (!rotated) {
                return false;
            }
            await this.waitForSceneDelay(260);
            const finalTarget = await this.resolveDay3GalgameWheelSlotTarget(1, 720)
                || await this.resolveAvatarFloatingTarget(scene, 'primary');
            if (finalTarget) {
                await this.moveCursorToElement(finalTarget, 0, {
                    exactDuration: true
                });
                this.cursor.click(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
            }
            return true;
        }

        async resolveDay3GalgameWheelSlotTarget(slot, timeoutMs) {
            const normalizedSlot = Number.isFinite(Number(slot)) ? String(Math.floor(Number(slot))) : '';
            const current = this.getVisibleChatComposerElement('.compact-input-tool-item-galgame')
                || this.getVisibleChatComposerElement('.composer-galgame-btn');
            if (!current || !current.hasAttribute('data-compact-tool-wheel-slot')) {
                return current || null;
            }
            if (current.getAttribute('data-compact-tool-wheel-slot') === normalizedSlot) {
                this.applyChatAvatarToolButtonSpotlightHint(current);
                return current;
            }
            const selector = '.compact-input-tool-item-galgame[data-compact-tool-wheel-slot="' + normalizedSlot + '"]';
            const target = await this.waitForElement(() => {
                const button = this.getVisibleChatComposerElement(selector);
                return button || null;
            }, Number.isFinite(timeoutMs) ? Math.max(0, Math.floor(timeoutMs)) : 720);
            if (target) {
                this.applyChatAvatarToolButtonSpotlightHint(target);
                return target;
            }
            return current;
        }

        async resolveAvatarFloatingTarget(scene, role) {
            const targetKey = role === 'secondary' ? scene.secondary : scene.target;
            if (!targetKey) {
                return null;
            }
            if (targetKey === 'agent-master') {
                return this.getAgentToggleElement('agent-master') || this.resolveElement('#${p}-toggle-agent-master');
            }
            if (targetKey === 'agent-capabilities') {
                return this.createAvatarFloatingUnionTarget(
                    scene.id + '-capabilities',
                    this.getAvatarFloatingAgentCapabilityTargets()
                );
            }
            if (targetKey === 'chat-avatar-tools') {
                const button = await this.getChatComposerToolButton('.compact-input-tool-item-avatar .composer-emoji-btn')
                    || await this.getChatComposerToolButton('.compact-input-tool-item-avatar')
                    || await this.getChatComposerToolButton('.composer-emoji-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.resolveAvatarFloatingSelector(targetKey);
            }
            if (targetKey === 'chat-galgame') {
                const button = await this.getChatComposerToolButton('.compact-input-tool-item-galgame')
                    || await this.getChatComposerToolButton('.composer-galgame-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.resolveAvatarFloatingSelector(targetKey);
            }
            if (typeof targetKey === 'string' && targetKey.indexOf('settings-sidepanel:') === 0) {
                const type = targetKey.split(':')[1] || '';
                if (scene && scene.deferSettingsSidePanelUntilCursorClick === true) {
                    return this.getAvatarFloatingSidePanel(type);
                }
                return this.getAvatarFloatingSidePanel(type) || await this.ensureAvatarFloatingSettingsSidePanel(type);
            }
            if (scene && scene.id === 'day4_model_lock' && targetKey === '#${p}-lock-icon') {
                return this.getDay4LockButtonSpotlightTarget();
            }
            if (targetKey === '.mic-option') {
                return this.resolveElement('.mic-option') || this.resolveElement('#${p}-popup-mic');
            }
            return this.resolveAvatarFloatingSelector(targetKey);
        }

        async resolveAvatarFloatingPersistent(scene, options) {
            const normalizedOptions = options || {};
            if (scene && scene.id === 'day6_agent_status_master') {
                return null;
            }
            const persistent = typeof scene.persistent === 'string' ? scene.persistent : '';
            if (persistent) {
                const target = this.resolveAvatarFloatingSelector(persistent);
                if (target) {
                    return target;
                }
            }
            if (normalizedOptions.fallbackToChatWindow === true) {
                return this.getAvatarFloatingBaseTarget('chat-window');
            }
            return null;
        }

        applyAvatarFloatingSceneSpotlightVariant(scene, target) {
            const variant = scene && typeof scene.spotlightVariant === 'string'
                ? scene.spotlightVariant.trim()
                : '';
            if (!variant || !target) {
                return;
            }
            this.setSpotlightVariantHints([{
                element: target,
                variant
            }]);
        }

        async prepareAvatarFloatingScene(scene, options) {
            const operation = typeof scene.operation === 'string' ? scene.operation : '';
            const deferSettingsSidePanelUntilCursorClick = !!(
                scene
                && scene.deferSettingsSidePanelUntilCursorClick === true
            );
            const preserveExternalizedChatGuideTarget = !!(
                (options && options.preserveExternalizedChatGuideTarget)
                || (scene && scene.preserveExternalizedChatGuideTarget === true)
            );
            if (scene.cleanupBefore) {
                if (preserveExternalizedChatGuideTarget) {
                    this.closeChatToolPopover();
                } else {
                    await this.closeAvatarFloatingGuidePanels();
                }
            }
            if (operation === 'show-task-hud') {
                const existingHud = document.getElementById('agent-task-hud');
                this.avatarFloatingGuideTemporaryHudWasVisible = !!(
                    existingHud && existingHud.style.display !== 'none' && this.isElementVisible(existingHud)
                );
                if (window.AgentHUD && typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                    window.AgentHUD.showAgentTaskHUD();
                    this.avatarFloatingGuideTemporaryHudShown = true;
                } else if (window.AgentHUD && typeof window.AgentHUD.createAgentTaskHUD === 'function') {
                    const hud = window.AgentHUD.createAgentTaskHUD();
                    if (hud) {
                        hud.style.display = 'flex';
                        hud.style.opacity = '1';
                        this.avatarFloatingGuideTemporaryHudShown = true;
                    }
                }
                await this.waitForElement(() => {
                    const hud = document.getElementById('agent-task-hud');
                    return hud && this.isElementVisible(hud) ? hud : null;
                }, 1200);
                return;
            }
            if (operation.indexOf('show-agent-sidepanel:') === 0) {
                const parts = operation.split(':');
                await this.openAgentPanel();
                await this.ensureAvatarFloatingAgentSidePanel(parts[1] || 'user-plugin');
                return;
            }
            if (
                operation.indexOf('show-settings-sidepanel:') === 0
                && !deferSettingsSidePanelUntilCursorClick
            ) {
                await this.ensureAvatarFloatingSettingsSidePanel(operation.split(':')[1] || '');
            }
            if (operation === 'day4-animation-distance-showcase') {
                await this.ensureAvatarFloatingSettingsSidePanel('animation-settings');
            }
            if (operation.indexOf('show-settings-menu:') === 0) {
                await this.ensureSettingsMenuVisible(operation.split(':')[1] || '');
            }
            if (operation === 'open-avatar-tool-menu') {
                this.setCompactToolFanOpen(true, 'avatar-floating-guide-prepare-avatar-tools');
                this.setChatAvatarToolMenuOpen(false, 'avatar-floating-guide-prepare');
            }
            if (
                operation === 'toggle-avatar-tool-after-narration'
                || operation === 'show-avatar-tools-then-hide-after-narration'
            ) {
                this.setCompactToolFanOpen(true, 'avatar-floating-guide-prepare-tool-fan');
            }
            if (operation === 'day2-settings-detail') {
                await this.closeAgentPanel().catch(() => {});
                await this.openSettingsPanel();
            }
            if (operation === 'cleanup') {
                await this.closeAvatarFloatingGuidePanels({
                    preserveExternalizedChatGuideTarget
                });
            }
        }

        async runDay6PluginOpenAgentPanelFlow(scene) {
            const sceneId = scene && scene.id ? scene.id : 'day6_agent_status_master';
            const guardFailed = () => this.isStopping();
            const catPawButton = this.getFloatingButtonShell(this.getFallbackFloatingButton('agent'))
                || this.getFallbackFloatingButton('agent')
                || this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw));
            if (!catPawButton || guardFailed()) {
                return false;
            }

            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.applyGuideHighlights({
                key: sceneId + '-cat-paw',
                primary: catPawButton
            });
            if (!(await this.moveCursorToElement(catPawButton, 760)) || guardFailed()) {
                return false;
            }
            const opened = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                () => this.openAgentPanel()
            );
            if (!opened || guardFailed()) {
                return false;
            }
            this.day6PluginDashboardPreview = Object.assign({}, this.day6PluginDashboardPreview || {}, {
                catPawButton: catPawButton
            });
            return true;
        }

        async runDay6PluginOpenManagementPanelFlow(scene) {
            const sceneId = scene && scene.id ? scene.id : 'day6_plugin_side_panel';
            const guardFailed = () => this.isStopping();
            const userPluginToggle = await this.waitForElement(() => {
                const toggle = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggle) ? toggle : null;
            }, 1800);
            if (!userPluginToggle || guardFailed()) {
                return false;
            }
            this.applyGuideHighlights({
                key: sceneId + '-user-plugin',
                primary: userPluginToggle
            });
            if (!(await this.moveCursorToElement(userPluginToggle, DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS, {
                exactDuration: true
            })) || guardFailed()) {
                return false;
            }
            const sidePanelShown = await this.runActionWithCursorClickExact(
                DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS,
                () => this.ensureAvatarFloatingAgentSidePanel('user-plugin')
            );
            if (!sidePanelShown || guardFailed()) {
                return false;
            }

            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                DAY6_PLUGIN_SIDE_PANEL_ACTION_TIMEOUT_MS
            );
            if (!managementButton || guardFailed()) {
                return false;
            }
            const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;
            this.applyGuideHighlights({
                key: sceneId + '-management-panel',
                primary: managementSpotlightTarget
            });
            if (!(await this.moveCursorToElement(managementButton, DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS, {
                exactDuration: true
            })) || guardFailed()) {
                return false;
            }
            const managementOpenResult = await this.runActionWithCursorClickExact(
                DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS,
                async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true,
                        source: 'avatar-floating-guide',
                        sceneId: sceneId
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                }
            );
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            if (!agentPanelActionOpened || guardFailed()) {
                return false;
            }

            const pluginDashboardWindow = hadPluginDashboard
                ? existingPluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, DAY6_PLUGIN_SIDE_PANEL_DASHBOARD_WAIT_MS);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed || guardFailed()) {
                return true;
            }
            this.day6PluginDashboardPreview = Object.assign({}, this.day6PluginDashboardPreview || {}, {
                pluginDashboardWindow: pluginDashboardWindow,
                pluginDashboardWindowCreatedByGuide: !hadPluginDashboard,
                userPluginToggle: userPluginToggle,
                managementButton: managementButton
            });
            return true;
        }

        async runDay6PluginDashboardHandoffFlow(scene, narrationStartedAt) {
            const guardFailed = () => this.isStopping();
            const previewState = this.day6PluginDashboardPreview || {};
            const pluginDashboardWindow = (
                previewState.pluginDashboardWindow
                && !previewState.pluginDashboardWindow.closed
            )
                ? previewState.pluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 1800);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed || guardFailed()) {
                return true;
            }
            this.pluginDashboardWindowCreatedByGuide = previewState.pluginDashboardWindowCreatedByGuide !== false;

            const homeCursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            this.hideHomeCursorForExternalizedChat();

            const voiceKey = scene && scene.voiceKey ? scene.voiceKey : '';
            const text = scene && scene.text ? scene.text : '';
            const audioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                ? this.voiceQueue.resolveGuideAudioSrc(voiceKey)
                : '';
            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const dashboardNarrationStartedAtMs = Number.isFinite(narrationStartedAt) ? narrationStartedAt : Date.now();
            const elapsedNarrationMs = Math.max(0, Date.now() - dashboardNarrationStartedAtMs);
            await this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow, {
                line: text,
                closeOnDone: false,
                narrationDurationMs: narrationDurationMs,
                voiceKey: voiceKey,
                audioUrl: audioUrl,
                narrationStartedAtMs: dashboardNarrationStartedAtMs
            }, {
                narrationDurationMs,
                elapsedNarrationMs
            }).catch(() => false);

            await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');
            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            this.stopHoverElement(previewState.userPluginToggle || null);
            await this.closeAgentPanel().catch(() => {});
            const homeReady = await this.waitForHomeMainUIReady(3600);
            if (homeCursorPosition) {
                this.cursor.showAt(homeCursorPosition.x, homeCursorPosition.y);
            }
            this.day6PluginDashboardPreview = null;
            if (!homeReady || guardFailed()) {
                return false;
            }
            return true;
        }

        async runDay6PluginSidePanelFlow(scene, narrationStartedAt) {
            const sceneId = scene && scene.id ? scene.id : 'day6_plugin_side_panel';
            const guardFailed = () => this.isStopping();
            const agentPanel = () => this.resolveAvatarFloatingSelector('#${p}-popup-agent');
            const catPawButton = this.getFloatingButtonShell(this.getFallbackFloatingButton('agent'))
                || this.getFallbackFloatingButton('agent')
                || this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw));
            if (!catPawButton || guardFailed()) {
                return false;
            }

            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.applyGuideHighlights({
                key: sceneId + '-cat-paw',
                primary: catPawButton
            });
            if (!(await this.moveCursorToElement(catPawButton, 760)) || guardFailed()) {
                return false;
            }
            const opened = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                () => this.openAgentPanel()
            );
            if (!opened || guardFailed()) {
                return false;
            }

            const userPluginToggle = await this.waitForElement(() => {
                const toggle = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggle) ? toggle : null;
            }, 1800);
            if (!userPluginToggle || guardFailed()) {
                return false;
            }
            this.applyGuideHighlights({
                key: sceneId + '-user-plugin',
                persistent: agentPanel(),
                primary: userPluginToggle
            });
            if (!(await this.moveCursorToElement(userPluginToggle, 420)) || guardFailed()) {
                return false;
            }
            const sidePanelShown = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                () => this.ensureAvatarFloatingAgentSidePanel('user-plugin')
            );
            if (!sidePanelShown || guardFailed()) {
                return false;
            }

            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                2600
            );
            if (!managementButton || guardFailed()) {
                return false;
            }
            const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;
            this.applyGuideHighlights({
                key: sceneId + '-management-panel',
                persistent: agentPanel(),
                primary: managementSpotlightTarget
            });
            if (!(await this.moveCursorToElement(managementButton, 420)) || guardFailed()) {
                return false;
            }
            const managementOpenResult = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true,
                        source: 'avatar-floating-guide',
                        sceneId: sceneId
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                }
            );
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            if (!agentPanelActionOpened || guardFailed()) {
                return false;
            }

            const pluginDashboardWindow = hadPluginDashboard
                ? existingPluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 1800);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed || guardFailed()) {
                return true;
            }
            this.pluginDashboardWindowCreatedByGuide = !hadPluginDashboard;

            const homeCursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            this.hideHomeCursorForExternalizedChat();

            const voiceKey = scene && scene.voiceKey ? scene.voiceKey : '';
            const text = scene && scene.text ? scene.text : '';
            const audioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                ? this.voiceQueue.resolveGuideAudioSrc(voiceKey)
                : '';
            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const dashboardNarrationStartedAtMs = Number.isFinite(narrationStartedAt) ? narrationStartedAt : Date.now();
            const elapsedNarrationMs = Math.max(0, Date.now() - dashboardNarrationStartedAtMs);
            await this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow, {
                line: text,
                closeOnDone: false,
                narrationDurationMs: narrationDurationMs,
                voiceKey: voiceKey,
                audioUrl: audioUrl,
                narrationStartedAtMs: dashboardNarrationStartedAtMs
            }, {
                narrationDurationMs,
                elapsedNarrationMs
            }).catch(() => false);

            await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');
            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            this.stopHoverElement(userPluginToggle);
            await this.closeAgentPanel().catch(() => {});
            const homeReady = await this.waitForHomeMainUIReady(3600);
            if (!homeReady || guardFailed()) {
                return false;
            }
            if (homeCursorPosition) {
                this.cursor.showAt(homeCursorPosition.x, homeCursorPosition.y);
            }
            return true;
        }

        async runDay4AnimationDistanceShowcase(scene, narrationStartedAt) {
            const durationMs = this.getAvatarFloatingNarrationDurationMs(scene.voiceKey || '', scene.text || '');
            const cueMs = clamp(Math.round(durationMs * 0.48), 2600, Math.max(2600, durationMs - 1800));
            const elapsedMs = Number.isFinite(narrationStartedAt)
                ? Math.max(0, Date.now() - narrationStartedAt)
                : 0;
            const waitMs = Math.max(0, cueMs - elapsedMs);
            if (!(await this.waitForSceneDelay(waitMs))) {
                return false;
            }
            if (this.isStopping()) {
                return false;
            }

            await this.closeAvatarFloatingGuidePanels();
            if (this.isStopping()) {
                return false;
            }

            const lockButton = this.resolveElement('#${p}-lock-icon');
            if (lockButton && this.isElementVisible(lockButton)) {
                this.applyGuideHighlights({
                    key: 'day4-animation-distance-lock',
                    primary: lockButton
                });
                await this.moveCursorToElement(lockButton, 680);
                this.cursor.wobble();
                await this.waitForSceneDelay(620);
            }
            if (this.isStopping()) {
                return false;
            }

            const goodbyeButton = this.resolveElement('#${p}-btn-goodbye');
            const returnButton = this.resolveElement('#${p}-btn-return');
            if (goodbyeButton && this.isElementVisible(goodbyeButton)) {
                this.applyGuideHighlights({
                    key: 'day4-animation-distance-goodbye',
                    primary: goodbyeButton,
                    secondary: returnButton && this.isElementVisible(returnButton) ? returnButton : null
                });
                await this.moveCursorToElement(goodbyeButton, 720);
                this.cursor.wobble();
                await this.waitForSceneDelay(720);
            }
            return true;
        }

        async playDay4ChatSettingsScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4ChatSettingsScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async playDay4ModelBehaviorScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4ModelBehaviorScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async playDay4GazeFollowScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4GazeFollowScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async playDay4PrivacyModeScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4PrivacyModeScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async playDay5CharacterSettingsScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay5CharacterSettingsScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
	        }

        async playDay2PersonalizationDetailScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay2PersonalizationDetailScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async playDay5CharacterPanicScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay5CharacterPanicScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        }

        async runAvatarFloatingSceneOperation(scene, primaryTarget, narrationStartedAt, narrationPromise) {
            return this.operationRegistry.run(scene, primaryTarget, narrationStartedAt, narrationPromise);
        }

        closeChatToolPopover() {
            this.setCompactToolFanOpen(false, 'avatar-floating-guide-close-tool-fan');
            let closed = this.setChatAvatarToolMenuOpen(false, 'avatar-floating-guide-close-avatar-tool-menu');
            if (!closed) {
                const activeToolButton = this.resolveElement('#react-chat-window-root .composer-emoji-btn.is-active');
                if (activeToolButton && typeof activeToolButton.click === 'function') {
                    activeToolButton.click();
                    closed = true;
                }
                const activeOverflowButton = this.resolveElement('#react-chat-window-root .composer-overflow-btn.is-active');
                if (activeOverflowButton && typeof activeOverflowButton.click === 'function') {
                    activeOverflowButton.click();
                    closed = true;
                }
            }
            const popover = this.resolveElement('#composer-tool-popover');
            const overflowPopover = this.resolveElement('#react-chat-window-root .composer-overflow-popover');
            if (!popover && !overflowPopover) {
                return closed;
            }
            return closed;
        }

        getAvatarFloatingNarrationDurationMs(voiceKey, text) {
            const configuredDurationMs = this.getGuideVoiceDurationMs(voiceKey || '', resolveGuideLocale());
            if (configuredDurationMs > 0) {
                return configuredDurationMs;
            }
            return 0;
        }

        async playAvatarFloatingPetalTransitionAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt) {
            return this.petalTransitionController.playAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt);
        }

        rememberAvatarFloatingSceneCursorAnchor(sceneId, element) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            const rect = this.getElementRect(element);
            if (!normalizedSceneId || !rect) {
                return;
            }
            this.rememberAvatarFloatingSceneCursorAnchorPoint(normalizedSceneId, {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            });
        }

        rememberAvatarFloatingSceneCursorAnchorPoint(sceneId, point) {
            this.cursorAnchorStore.rememberScenePoint(sceneId, point);
        }

        getAvatarFloatingSceneCursorAnchor(sceneIds) {
            return this.cursorAnchorStore.getScenePoint(sceneIds);
        }

        resolveManagedSceneCursorAnchorPoint(previousSceneId) {
            return this.getAvatarFloatingSceneCursorAnchor(previousSceneId);
        }

        resolveAvatarFloatingCursorStartPoint(scene, targets, previousSceneId) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            const explicitStartTargets = [];
            if (sceneId === 'day2_screen_entry') {
                explicitStartTargets.push(this.getAvatarFloatingIntroSpotlightTarget({ id: 'day2_intro_context' }));
            } else if (sceneId === 'day2_wrap_intro') {
                const previousScreenAnchor = this.getAvatarFloatingSceneCursorAnchor([
                    'day2_screen_entry_invite',
                    'day2_screen_entry'
                ]);
                if (previousScreenAnchor) {
                    return previousScreenAnchor;
                }
                explicitStartTargets.push(this.resolveAvatarFloatingSelector('#${p}-btn-screen'));
            } else if (sceneId === 'day3_avatar_tools') {
                explicitStartTargets.push(this.resolveAvatarFloatingSelector('chat-tool-toggle'));
            }

            if (sceneId === 'day1_takeover_return_control') {
                const keyboardControlAnchor = this.getAvatarFloatingSceneCursorAnchor('day1_takeover_capture_cursor');
                if (keyboardControlAnchor) {
                    return keyboardControlAnchor;
                }
            }

            for (let index = 0; index < explicitStartTargets.length; index += 1) {
                const rect = this.getElementRect(explicitStartTargets[index]);
                if (rect) {
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    };
                }
            }

            const previousSceneAnchor = this.getAvatarFloatingSceneCursorAnchor(previousSceneId);
            if (previousSceneAnchor) {
                return previousSceneAnchor;
            }

            if (sceneId === 'day2_screen_entry') {
                const externalizedChatAnchor = this.getExternalizedChatCursorAnchorPoint(30000);
                if (externalizedChatAnchor) {
                    return externalizedChatAnchor;
                }
                const chatProxyAnchor = this.getAvatarFloatingChatProxyAnchorPoint();
                if (chatProxyAnchor) {
                    return chatProxyAnchor;
                }
            }

            const targetList = Array.isArray(targets) ? targets : [];
            for (let index = 0; index < targetList.length; index += 1) {
                const rect = this.getElementRect(targetList[index]);
                if (rect) {
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    };
                }
            }
            return null;
        }

        getAvatarFloatingChatProxyAnchorPoint() {
            const chatTarget = this.getChatIntroActivationTarget()
                || this.getChatWindowTarget()
                || this.getChatInputTarget();
            const chatRect = this.getElementRect(chatTarget);
            if (chatRect) {
                return {
                    x: chatRect.left + chatRect.width / 2,
                    y: chatRect.top + chatRect.height / 2
                };
            }

            return null;
        }

        async moveAvatarFloatingCursor(scene, primaryTarget, secondaryTarget, previousSceneId, options) {
            const normalizedOptions = options || {};
            const action = scene.cursorAction || 'move';
            const targets = [primaryTarget, secondaryTarget].filter(Boolean);
            if (action === 'tour') {
                targets.push.apply(targets, this.getAvatarFloatingCursorTourTargets(scene, primaryTarget));
            }
            const uniqueTargets = Array.from(new Set(targets));
            if (uniqueTargets.length === 0) {
                return;
            }
            const configuredFirstMoveMs = Number.isFinite(scene.cursorMoveDurationMs)
                ? Math.max(160, Math.floor(scene.cursorMoveDurationMs))
                : 0;
            if (!this.cursor.hasPosition()) {
                const origin = this.resolveAvatarFloatingCursorStartPoint(scene, uniqueTargets, previousSceneId)
                    || this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
                await this.waitForSceneDelay(120);
            }
            for (let index = 0; index < uniqueTargets.length; index += 1) {
                if (this.isStopping()) {
                    return;
                }
                const moved = await this.moveCursorToElement(
                    uniqueTargets[index],
                    index === 0 ? (configuredFirstMoveMs || 760) : 520
                );
                if (!moved) {
                    continue;
                }
                if (action === 'click' && index === 0) {
                    const clickPromise = this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
                    if (typeof normalizedOptions.onClickStart === 'function') {
                        normalizedOptions.onClickStart({
                            scene,
                            target: uniqueTargets[index]
                        });
                    } else if (scene && scene.operation === 'open-avatar-tool-menu') {
                        this.setChatAvatarToolMenuOpen(true, 'avatar-floating-guide-open-avatar-tool-menu');
                    }
                    await clickPromise;
                } else if (action === 'wobble' || action === 'tour') {
                    this.cursor.wobble(Number.isFinite(scene.cursorWobbleDurationMs)
                        ? Math.max(0, Math.floor(scene.cursorWobbleDurationMs))
                        : 0);
                    await this.waitForSceneDelay(action === 'tour'
                        ? 220
                        : (Number.isFinite(scene.cursorWobbleDurationMs)
                            ? Math.max(0, Math.floor(scene.cursorWobbleDurationMs))
                            : 360));
                }
            }
        }

        async moveExternalizedChatCursor(scene, options) {
            const normalizedOptions = options || {};
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            const moveWaitMs = this.getExternalizedChatCursorMoveDurationMs(scene, 760);
            await this.waitForExternalizedChatCursorMove(
                sceneId,
                moveWaitMs > 0 ? moveWaitMs : undefined
            );
            if (this.isStopping()) {
                return false;
            }
            const cursorKind = this.getExternalizedChatCursorTargetKind(scene);
            const useHomeOwnedClick = sceneId.indexOf('day3_') === 0;
            const externalizedClickStarted = !useHomeOwnedClick && !!(
                cursorKind
                && this.setExternalizedChatCursorEffect(cursorKind, 'click', {
                    effectDurationMs: DEFAULT_CURSOR_CLICK_VISIBLE_MS
                })
            );
            const clickPromise = useHomeOwnedClick || !externalizedClickStarted
                ? this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)
                : this.waitForSceneDelay(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
            if (typeof normalizedOptions.onClickStart === 'function') {
                normalizedOptions.onClickStart({
                    scene: scene,
                    kind: cursorKind
                });
            }
            await clickPromise;
            return true;
        }

        async closeAvatarFloatingGuidePanels(options) {
            const shouldClearCursor = !!(options && options.clearCursor === true);
            const preserveExternalizedChatGuideTarget = !shouldClearCursor && !!(
                options && options.preserveExternalizedChatGuideTarget === true
            );
            this.closeChatToolPopover();
            if (!preserveExternalizedChatGuideTarget) {
                this.clearExternalizedChatGuideTarget({
                    clearCursor: shouldClearCursor
                });
            }
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            if (
                this.avatarFloatingGuideTemporaryHudShown
                || this.avatarFloatingGuideTemporaryHudWasVisible
            ) {
                this.hideTemporaryAvatarFloatingGuideHud('close-panels');
            }
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            this.clearSpotlightGeometryHints();
            this.clearSpotlightVariantHints();
            this.overlay.clearActionSpotlight();
            await this.closeManagedPanels().catch(() => {});
            ['agent-user-plugin', 'agent-openclaw'].forEach((toggleId) => this.collapseAgentSidePanel(toggleId));
            this.collapseCharacterSettingsSidePanel();
        }

        isDay1AvatarFloatingScene(scene) {
            return !!(
                scene
                && typeof scene.id === 'string'
                && scene.id.indexOf('day1_') === 0
            );
        }

        async playDay1IntroActivationRoundScene(sceneRunId) {
            if (!this.day1RoundWakeupCompleted) {
                await this.runWakeupPrelude();
                this.day1RoundWakeupCompleted = true;
            }
            if (this.isStopping()) {
                return false;
            }

            if (this.introFlowStarted) {
                return sceneRunId === this.sceneRunId && !this.isStopping();
            }

            this.introFlowStarted = true;
            await this.ensureGuideIdleSwayPerformance();
            this.setCurrentScene('day1_intro_activation', null);
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
                this.hideHomeCursorForExternalizedChat();
                return sceneRunId === this.sceneRunId && !this.isStopping();
            }

            await this.ensureChatVisible();
            if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            const inputTarget = this.getChatInputTarget();
            const inputRect = this.getElementRect(inputTarget);
            if (inputRect) {
                const cx = inputRect.left + inputRect.width / 2;
                const cy = inputRect.top + inputRect.height / 2;
                this.cursor.showAt(cx, cy);
                this.cursor.wobble();
                const activationHint = this.resolveGuideCopy(INTRO_ACTIVATION_HINT_KEY, INTRO_ACTIVATION_HINT);
                this.showGuideBubble(activationHint, {
                    anchorRect: inputRect,
                    bubbleVariant: 'intro-activation'
                }, 'intro_activation');
                const bubbleEl = this.overlay.bubble;
                if (bubbleEl) {
                    const bubbleW = Math.min(bubbleEl.offsetWidth || 380, window.innerWidth - 32);
                    const bubbleH = bubbleEl.offsetHeight || 60;
                    const bLeft = Math.max(16, Math.min(
                        inputRect.left + inputRect.width / 2 - bubbleW / 2,
                        window.innerWidth - bubbleW - 16
                    ));
                    const bTop = Math.max(16, inputRect.top - bubbleH - 14);
                    bubbleEl.style.left = Math.round(bLeft) + 'px';
                    bubbleEl.style.top = Math.round(bTop) + 'px';
                }
                await this.waitForIntroActivationClick();
                if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                    return false;
                }
                this.overlay.hideBubble();
                this.cursor.wobble();
                await wait(280);
            }

            return sceneRunId === this.sceneRunId && !this.isStopping();
        }

        async playDay1IntroGreetingRoundScene(sceneRunId) {
            const introStep = this.getStep('intro_basic') || {
                performance: {
                    interruptible: true
                },
                interrupts: {}
            };
            if (!this.introFlowStarted) {
                const activated = await this.playDay1IntroActivationRoundScene(sceneRunId);
                if (!activated) {
                    return false;
                }
            }
            this.setCurrentScene('day1_intro_greeting', null);
            if (!this.isHomeChatExternalized()) {
                await this.waitForSceneDelay(140);
            }
            if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                return false;
            }
            this.enableInterrupts(introStep);
            if (this.isHomeChatExternalized()) {
                this.setExternalizedChatGuideTarget('capsule-input', {
                    effect: '',
                    durationMs: 0
                });
            } else {
                const inputTarget = this.getChatInputTarget();
                if (inputTarget) {
                    this.setSpotlightGeometryHint(inputTarget, {
                        padding: DEFAULT_SPOTLIGHT_PADDING + 3
                    });
                    this.overlay.setPersistentSpotlight(inputTarget);
                }
            }
            await this.playIntroGreetingReply();
            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
            }
            return sceneRunId === this.sceneRunId && !this.isStopping();
        }

        async playAvatarFloatingScene(scene, day, index, total) {
            return this.sceneOrchestrator.playScene(scene, day, index, total);
        }

        async playAvatarFloatingRound(round, options) {
            return this.sceneOrchestrator.playRound(round, options);
        }

        disableInterrupts() {
            if (!this.interruptsEnabled) {
                return;
            }

            window.removeEventListener('mousemove', this.pointerMoveHandler, true);
            window.removeEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = false;
            this.lastPointerPoint = null;
            this.interruptQualifyingMoveStreak = 0;
        }

        enableInterrupts(step) {
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                this.disableInterrupts();
                return;
            }

            this.disableInterrupts();
            if (interrupts.resetOnStepAdvance !== false) {
                this.interruptCount = 0;
            }
            this.interruptQualifyingMoveStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            window.addEventListener('mousemove', this.pointerMoveHandler, true);
            window.addEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = true;
        }

        playCursorResistanceToUserMotion(x, y, distance, motionDx, motionDy) {
            let hasVisibleCursor = typeof this.cursor.hasVisiblePosition === 'function'
                ? this.cursor.hasVisiblePosition()
                : this.cursor.hasPosition();
            if (!hasVisibleCursor && this.isHomeChatExternalized()) {
                const currentPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                    ? this.overlay.getCursorPosition()
                    : null;
                if (
                    currentPoint
                    && Number.isFinite(currentPoint.x)
                    && Number.isFinite(currentPoint.y)
                    && typeof this.cursor.showAt === 'function'
                ) {
                    this.cursor.showAt(currentPoint.x, currentPoint.y);
                    hasVisibleCursor = true;
                } else if (
                    typeof this.restoreCursorFromExternalizedChatAnchor === 'function'
                    && this.restoreCursorFromExternalizedChatAnchor(30000)
                ) {
                    hasVisibleCursor = true;
                }
            }
            if (!hasVisibleCursor) {
                return;
            }

            if (!Number.isFinite(distance) || distance <= 0) {
                return;
            }

            this.cursor.reactToUserMotion(x, y, {
                motionDx: motionDx,
                motionDy: motionDy,
                scale: 0.4,
                outDurationMs: 140,
                backDurationMs: 240,
                forcePcOverlay: true
            });
        }

        isCursorTransientMotionActive() {
            return !!(
                this.cursor
                && typeof this.cursor.isTransientMotionActive === 'function'
                && this.cursor.isTransientMotionActive()
            );
        }

        async waitForCursorTransientMotion() {
            if (
                this.cursor
                && typeof this.cursor.waitForTransientMotion === 'function'
                && this.isCursorTransientMotionActive()
            ) {
                await this.cursor.waitForTransientMotion();
            }
        }

        shouldAllowInterruptDuringCurrentScene() {
            if (!this.interruptsEnabled || this.destroyed || this.angryExitTriggered) {
                return false;
            }

            if (
                this.page === 'home'
                && this.pluginDashboardHandoff
                && this.pluginDashboardHandoff.windowRef
                && !this.pluginDashboardHandoff.windowRef.closed
            ) {
                return false;
            }

            if (this.page !== 'home') {
                return true;
            }

            if (this.currentSceneId === 'intro_basic') {
                return this.introFlowStarted && !this.isStopping();
            }

            return !!this.currentSceneId;
        }

        // Dev B boundary: Director only talks to this API surface.
        // Dev C can later provide a real implementation via options.homeInteractionApi,
        // window.getYuiGuideHomeInteractionApi(), window.YuiGuideHomeInteractionApi,
        // or the broader window.YuiGuidePageHandoff module.
        getHomeInteractionApi() {
            if (this.options && this.options.homeInteractionApi) {
                return this.options.homeInteractionApi;
            }

            if (typeof window.getYuiGuideHomeInteractionApi === 'function') {
                try {
                    return window.getYuiGuideHomeInteractionApi() || null;
                } catch (error) {
                    console.warn('[YuiGuide] 获取首页交互 API 失败:', error);
                }
            }

            return window.YuiGuideHomeInteractionApi || window.YuiGuidePageHandoff || null;
        }

        async callHomeInteractionApi(methodName, args, fallback) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api[methodName] === 'function') {
                try {
                    const apiTimeoutMs = methodName === 'openPageWithHandoff' ? 6000 : 4200;
                    const apiResult = await resolveWithTimeout(
                        api[methodName].apply(api, Array.isArray(args) ? args : []),
                        apiTimeoutMs,
                        false,
                        'home api ' + methodName
                    );
                    if (apiResult) {
                        return true;
                    }
                    if (typeof fallback === 'function') {
                        return !!(await fallback());
                    }
                    return false;
                } catch (error) {
                    console.warn('[YuiGuide] 首页交互 API 调用失败，回退到本地实现:', methodName, error);
                }
            }

            if (typeof fallback === 'function') {
                return !!(await fallback());
            }

            return false;
        }

        getManagedPanelElement(panelId) {
            if (!panelId) {
                return null;
            }

            return document.getElementById(this.resolveModelPrefix() + '-popup-' + panelId);
        }

        isManagedPanelVisible(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            return !!(popup && popup.style.display === 'flex' && popup.style.opacity !== '0');
        }

        positionManagedPanelNow(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            const popupUi = window.AvatarPopupUI || null;
            const prefix = this.resolveModelPrefix();
            if (!popup || !popupUi || typeof popupUi.positionPopup !== 'function') {
                return false;
            }

            try {
                const pos = popupUi.positionPopup(popup, {
                    buttonId: panelId,
                    buttonPrefix: prefix + '-btn-',
                    triggerPrefix: prefix + '-trigger-icon-',
                    rightMargin: 20,
                    bottomMargin: 60,
                    topMargin: 8,
                    gap: 8,
                    sidePanelWidth: (panelId === 'settings' || panelId === 'agent') ? 320 : 0
                });
                popup.dataset.opensLeft = String(!!(pos && pos.opensLeft));
                return true;
            } catch (error) {
                console.warn('[YuiGuide] positionManagedPanelNow 失败:', panelId, error);
                return false;
            }
        }

        async waitForManagedPanelPositioned(panelId, timeoutMs) {
            const popup = this.getManagedPanelElement(panelId);
            if (!popup) {
                return false;
            }

            const positioned = await this.waitForElement(() => {
                if (
                    popup.style.display === 'flex'
                    && !popup.classList.contains('is-positioning')
                    && typeof popup.dataset.opensLeft === 'string'
                    && popup.dataset.opensLeft !== ''
                ) {
                    return popup;
                }
                return null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 1100);

            if (positioned) {
                this.positionManagedPanelNow(panelId);
                return true;
            }

            return this.positionManagedPanelNow(panelId);
        }

        forceHideManagedPanel(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            if (!popup) {
                return false;
            }

            popup.style.transition = 'none';
            popup.style.opacity = '0';
            popup.style.display = 'none';
            popup.style.pointerEvents = 'none';
            popup.style.transition = '';
            return true;
        }

        getFallbackFloatingButton(buttonId) {
            if (!buttonId) {
                return null;
            }

            return this.resolveElement('#${p}-btn-' + buttonId);
        }

        async setFallbackFloatingPopupVisible(buttonId, visible) {
            const desiredVisible = !!visible;
            if (this.isManagedPanelVisible(buttonId) === desiredVisible) {
                return !desiredVisible || await this.waitForManagedPanelPositioned(buttonId);
            }

            const button = this.getFallbackFloatingButton(buttonId);
            if (!button || typeof button.click !== 'function') {
                return this.isManagedPanelVisible(buttonId) === desiredVisible;
            }

            button.click();

            const result = await this.waitForElement(() => {
                const popup = this.getManagedPanelElement(buttonId);
                const isVisible = this.isManagedPanelVisible(buttonId);
                return isVisible === desiredVisible ? (popup || button) : null;
            }, 1200);

            if (!(!!result && this.isManagedPanelVisible(buttonId) === desiredVisible)) {
                return false;
            }

            return !desiredVisible || await this.waitForManagedPanelPositioned(buttonId);
        }

        async openAgentPanel() {
            return this.callHomeInteractionApi('openAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', true);
            });
        }

        async closeAgentPanel() {
            const closed = await this.callHomeInteractionApi('closeAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', false);
            });
            this.collapseAgentSidePanel('agent-user-plugin');
            this.collapseAgentSidePanel('agent-openclaw');
            return closed;
        }

        async ensureAgentToggleChecked(toggleId, checked) {
            return this.callHomeInteractionApi('ensureAgentToggleChecked', [toggleId, checked], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const checkbox = await this.waitForElement(() => {
                    const input = this.getAgentToggleCheckbox(toggleId);
                    return input && !input.disabled ? input : null;
                }, 5000);
                const toggleItem = this.getAgentToggleElement(toggleId);
                if (!checkbox || !toggleItem) {
                    return false;
                }

                const desiredChecked = checked !== false;
                if (!!checkbox.checked === desiredChecked) {
                    return true;
                }

                toggleItem.click();
                const result = await this.waitForElement(() => {
                    return !!checkbox.checked === desiredChecked ? checkbox : null;
                }, 1500);
                return !!result;
            });
        }

        async ensureAgentSidePanelVisible(toggleId) {
            return this.callHomeInteractionApi('ensureAgentSidePanelVisible', [toggleId], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const toggleItem = this.getAgentToggleElement(toggleId);
                const sidePanel = this.getAgentSidePanel(toggleId);
                if (!toggleItem || !sidePanel) {
                    return false;
                }

                this.collapseAvatarFloatingSidePanelsExcept(sidePanel);
                if (typeof sidePanel._expand === 'function') {
                    if (sidePanel._hoverCollapseTimer) {
                        window.clearTimeout(sidePanel._hoverCollapseTimer);
                        sidePanel._hoverCollapseTimer = null;
                    }
                    sidePanel._expand();
                } else {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                }

                try {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                    sidePanel.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                } catch (_) {}

                const result = await this.waitForElement(() => {
                    return this.isAgentSidePanelVisible(toggleId) ? sidePanel : null;
                }, 1500);
                return !!result;
            });
        }

        async waitForAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const sidePanelReady = await this.ensureAgentSidePanelVisible(toggleId);
            if (!sidePanelReady) {
                return null;
            }

            await this.waitForAgentSidePanelLayoutStable(toggleId, 620);

            return this.waitForVisibleElement(() => {
                const button = this.getAgentSidePanelButton(toggleId, actionId);
                if (!button || !this.isAgentSidePanelVisible(toggleId)) {
                    return null;
                }
                return button;
            }, normalizedTimeoutMs);
        }

        async ensureAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const api = this.getHomeInteractionApi();
            if (api && typeof api.ensureAgentSidePanelActionVisible === 'function') {
                try {
                    const actionElement = await resolveWithTimeout(
                        api.ensureAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs),
                        normalizedTimeoutMs + 900,
                        null,
                        'ensureAgentSidePanelActionVisible'
                    );
                    if (actionElement) {
                        await this.waitForAgentSidePanelLayoutStable(toggleId, 620);
                    }
                    if (actionElement) {
                        return actionElement;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] ensureAgentSidePanelActionVisible 调用失败，改用本地兜底:', error);
                }
            }

            return this.waitForAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs);
        }

        async waitForAgentToggleState(toggleId, checked, timeoutMs) {
            const desiredChecked = checked !== false;
            return this.waitForElement(() => {
                const checkbox = this.getAgentToggleCheckbox(toggleId);
                if (!checkbox) {
                    return null;
                }
                return !!checkbox.checked === desiredChecked ? checkbox : null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 1800);
        }

        readAgentToggleChecked(toggleId) {
            const checkbox = this.getAgentToggleCheckbox(toggleId);
            return checkbox && typeof checkbox.checked === 'boolean'
                ? !!checkbox.checked
                : null;
        }

        async getAgentSwitchSnapshot() {
            const fallbackSnapshot = {
                agentMaster: this.readAgentToggleChecked('agent-master'),
                keyboardControl: this.readAgentToggleChecked('agent-keyboard'),
                userPlugin: this.readAgentToggleChecked('agent-user-plugin')
            };
            const controller = typeof AbortController === 'function'
                ? new AbortController()
                : null;
            const timeoutId = controller
                ? window.setTimeout(() => controller.abort(), 800)
                : 0;

            try {
                const response = await fetch('/api/agent/flags', {
                    signal: controller ? controller.signal : undefined
                });
                if (!response.ok) {
                    return fallbackSnapshot;
                }

                const data = await response.json();
                if (!data || data.success === false) {
                    return fallbackSnapshot;
                }

                const flags = data.agent_flags && typeof data.agent_flags === 'object'
                    ? data.agent_flags
                    : {};
                return {
                    agentMaster: typeof data.analyzer_enabled === 'boolean'
                        ? data.analyzer_enabled
                        : (typeof flags.agent_enabled === 'boolean' ? flags.agent_enabled : fallbackSnapshot.agentMaster),
                    keyboardControl: typeof flags.computer_use_enabled === 'boolean'
                        ? flags.computer_use_enabled
                        : fallbackSnapshot.keyboardControl,
                    userPlugin: typeof flags.user_plugin_enabled === 'boolean'
                        ? flags.user_plugin_enabled
                        : fallbackSnapshot.userPlugin
                };
            } catch (_) {
                return fallbackSnapshot;
            } finally {
                if (timeoutId) {
                    window.clearTimeout(timeoutId);
                }
            }
        }

        async captureDay1TakeoverAgentSwitches() {
            if (this.takeoverOriginalAgentSwitches) {
                return this.takeoverOriginalAgentSwitches;
            }
            const snapshot = await this.getAgentSwitchSnapshot();
            this.takeoverOriginalAgentSwitches = snapshot || {
                agentMaster: null,
                keyboardControl: null,
                userPlugin: null
            };
            return this.takeoverOriginalAgentSwitches;
        }

        async restoreDay1TakeoverAgentSwitches(reason) {
            const snapshot = this.takeoverOriginalAgentSwitches;
            if (!snapshot) {
                return true;
            }
            if (this.takeoverAgentSwitchRestorePromise) {
                return this.takeoverAgentSwitchRestorePromise;
            }

            this.takeoverAgentSwitchRestorePromise = (async () => {
                const originalAgentMaster = typeof snapshot.agentMaster === 'boolean'
                    ? snapshot.agentMaster
                    : null;
                const originalKeyboardControl = typeof snapshot.keyboardControl === 'boolean'
                    ? snapshot.keyboardControl
                    : null;
                let restored = true;

                try {
                    if (originalAgentMaster === true) {
                        restored = (await this.setAgentMasterEnabled(true)) && restored;
                    }
                    if (typeof originalKeyboardControl === 'boolean') {
                        restored = (await this.setAgentFlagEnabled('computer_use_enabled', originalKeyboardControl)) && restored;
                    }
                    if (originalAgentMaster === false) {
                        restored = (await this.setAgentMasterEnabled(false)) && restored;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 恢复 Day1 接管前 Agent 开关失败:', reason || 'restore', error);
                    restored = false;
                }

                if (restored) {
                    this.takeoverOriginalAgentSwitches = null;
                }
                return restored;
            })();

            try {
                return await this.takeoverAgentSwitchRestorePromise;
            } finally {
                if (this.takeoverAgentSwitchRestorePromise) {
                    this.takeoverAgentSwitchRestorePromise = null;
                }
            }
        }

        async clickAgentSidePanelAction(toggleId, actionId, options) {
            const fallbackClick = async () => {
                const button = await this.waitForAgentSidePanelActionVisible(toggleId, actionId, 1800);
                if (!button || typeof button.click !== 'function') {
                    return false;
                }

                button.click();
                return true;
            };

            if (toggleId === 'agent-user-plugin' && actionId === 'management-panel') {
                const api = this.getHomeInteractionApi();
                if (api && typeof api.clickAgentSidePanelAction === 'function') {
                    try {
                        const clicked = await resolveWithTimeout(
                            api.clickAgentSidePanelAction(toggleId, actionId, options || null),
                            2600,
                            false,
                            'clickAgentSidePanelAction'
                        );
                        if (clicked) {
                            return true;
                        }
                        return fallbackClick();
                    } catch (error) {
                        console.warn('[YuiGuide] 插件管理面板 API 点击失败，回退到本地实现:', error);
                    }
                }
                return fallbackClick();
            }

            return this.callHomeInteractionApi(
                'clickAgentSidePanelAction',
                [toggleId, actionId, options || null],
                fallbackClick
            );
        }

        async openSettingsPanel() {
            return this.callHomeInteractionApi('openSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', true);
            });
        }

        async closeSettingsPanel() {
            return this.callHomeInteractionApi('closeSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', false);
            });
        }

        normalizeSettingsMenuId(menuId) {
            const normalized = typeof menuId === 'string'
                ? menuId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
                : '';
            return normalized || '';
        }

        getSettingsMenuSelector(menuId) {
            const normalizedMenuId = this.normalizeSettingsMenuId(menuId);
            if (!normalizedMenuId) {
                return '';
            }

            return '#' + this.resolveModelPrefix() + '-menu-' + normalizedMenuId;
        }

        getSettingsMenuElement(menuId) {
            const selector = this.getSettingsMenuSelector(menuId);
            if (!selector) {
                return null;
            }

            return this.resolveElement(selector);
        }

        async ensureSettingsMenuVisible(menuId) {
            return this.callHomeInteractionApi('ensureSettingsMenuVisible', [menuId], async () => {
                const panelReady = await this.openSettingsPanel();
                if (!panelReady) {
                    return false;
                }

                if (!menuId) {
                    return true;
                }

                this.collapseCharacterSettingsSidePanel();

                const selector = this.getSettingsMenuSelector(menuId);
                if (!selector) {
                    return false;
                }

                const menuLabel = await this.waitForElement(() => this.resolveElement(selector), 1200);
                if (!menuLabel) {
                    return false;
                }

                const menuItem = menuLabel.closest('.' + this.resolveModelPrefix() + '-settings-menu-item') || menuLabel.parentElement;
                if (menuItem && typeof menuItem.scrollIntoView === 'function') {
                    try {
                        menuItem.scrollIntoView({
                            behavior: 'smooth',
                            block: 'nearest',
                            inline: 'nearest'
                        });
                    } catch (_) {
                        menuItem.scrollIntoView();
                    }
                }

                return true;
            });
        }

        async closeManagedPanels() {
            const results = await Promise.all([
                this.closeAgentPanel(),
                this.closeSettingsPanel()
            ]);

            return results.every(Boolean);
        }

        async openPageWithHandoff(stepId, step) {
            const navigation = step && step.navigation ? step.navigation : null;
            if (!navigation || !navigation.openUrl || !navigation.windowName) {
                return false;
            }

            const targetPage = navigation.targetPage || navigation.windowName || stepId || '';
            const resumeScene = navigation.resumeScene || null;

            return this.callHomeInteractionApi('openPageWithHandoff', [
                targetPage,
                resumeScene,
                navigation.openUrl,
                navigation.windowName,
                navigation.features || ''
            ], async () => {
                const api = this.getHomeInteractionApi();
                if (targetPage === 'plugin_dashboard' && api && typeof api.openPluginDashboard === 'function') {
                    const childWin = await resolveWithTimeout(
                        api.openPluginDashboard(),
                        3600,
                        null,
                        'openPluginDashboard fallback'
                    );
                    return !!childWin;
                }
                if (api && typeof api.openPage === 'function') {
                    const childWin = await resolveWithTimeout(
                        api.openPage(
                            navigation.openUrl,
                            navigation.windowName,
                            navigation.features || ''
                        ),
                        3600,
                        null,
                        'openPage fallback'
                    );
                    return !!childWin;
                }

                return false;
            });
        }

        async waitForOpenedWindow(windowName, timeoutMs) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.waitForWindowOpen === 'function') {
                try {
                    const apiTimeoutMs = Math.max(1000, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 6000) + 800);
                    const openedWindow = await resolveWithTimeout(
                        api.waitForWindowOpen(windowName, timeoutMs),
                        apiTimeoutMs,
                        null,
                        'waitForWindowOpen'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 等待子窗口打开失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            return this.waitForElement(() => {
                if (!normalizedName) {
                    return null;
                }

                const tracked = window._openedWindows && window._openedWindows[normalizedName];
                return tracked && !tracked.closed ? tracked : null;
            }, timeoutMs || 6000);
        }

        async closeNamedWindow(windowName) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.closeWindow === 'function') {
                try {
                    const apiClosed = !!(await resolveWithTimeout(
                        api.closeWindow(windowName),
                        2200,
                        false,
                        'closeWindow'
                    ));
                    if (apiClosed) {
                        return true;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 关闭子窗口失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            const target = normalizedName && window._openedWindows
                ? window._openedWindows[normalizedName]
                : null;
            if (!target) {
                return true;
            }

            try {
                target.close();
                delete window._openedWindows[normalizedName];
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 本地关闭子窗口失败:', error);
                return false;
            }
        }

        async closePluginDashboardWindowIfCreatedByGuide(context) {
            if (!this.pluginDashboardWindowCreatedByGuide) {
                return true;
            }

            try {
                const closed = await this.closeNamedWindow(PLUGIN_DASHBOARD_WINDOW_NAME);
                if (closed) {
                    this.pluginDashboardWindowCreatedByGuide = false;
                    return true;
                }
                console.warn('[YuiGuide] ' + (context || '清理') + '时关闭插件面板失败');
                return false;
            } catch (error) {
                console.warn('[YuiGuide] ' + (context || '清理') + '时关闭插件面板失败:', error);
                return false;
            }
        }

        async setAgentMasterEnabled(enabled) {
            return this.callHomeInteractionApi('setAgentMasterEnabled', [enabled], async () => {
                try {
                    const response = await fetchWithTimeout('/api/agent/command', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                            command: 'set_agent_enabled',
                            enabled: !!enabled
                        })
                    }, 3600);
                    if (!response.ok) {
                        return false;
                    }

                    const data = await response.json();
                    return !!(data && data.success === true);
                } catch (error) {
                    console.warn('[YuiGuide] 设置 Agent 总开关超时或失败:', error);
                    return false;
                }
            });
        }

        async setAgentFlagEnabled(flagKey, enabled) {
            return this.callHomeInteractionApi('setAgentFlagEnabled', [flagKey, enabled], async () => {
                try {
                    const response = await fetchWithTimeout('/api/agent/command', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                            command: 'set_flag',
                            key: flagKey,
                            value: !!enabled
                        })
                    }, 3600);
                    if (!response.ok) {
                        return false;
                    }

                    const data = await response.json();
                    return !!(data && data.success === true);
                } catch (error) {
                    console.warn('[YuiGuide] 设置 Agent 标志超时或失败:', flagKey, error);
                    return false;
                }
            });
        }

        async openPluginDashboardWindow(options) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.openPluginDashboard === 'function') {
                try {
                    const openedWindow = await resolveWithTimeout(
                        api.openPluginDashboard(options || null),
                        3600,
                        null,
                        'openPluginDashboard'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] openPluginDashboard 失败，改用本地兜底:', error);
                }
            }

            if (api && typeof api.openPage === 'function') {
                try {
                    const fallbackUrl = new URL('/api/agent/user_plugin/dashboard', window.location.origin);
                    if (window.location && window.location.origin) {
                        fallbackUrl.searchParams.set('yui_opener_origin', window.location.origin);
                    }
                    return await resolveWithTimeout(
                        api.openPage(
                            fallbackUrl.toString(),
                            'plugin_dashboard',
                            '',
                            options || null
                        ),
                        3600,
                        null,
                        'openPage(plugin_dashboard)'
                    );
                } catch (error) {
                    console.warn('[YuiGuide] openPage(plugin_dashboard) 失败:', error);
                }
            }

            return null;
        }

        async waitForManualPluginDashboardOpen(managementButton, spotlightTarget, runId, timeoutMs, guideOpenTriggeredBeforePrompt) {
            if (!managementButton || runId !== this.sceneRunId || this.isStopping()) {
                return {
                    window: null,
                    createdByGuide: false
                };
            }

            const normalizedTimeoutMs = clamp(
                Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 18000),
                6000,
                30000
            );
            const target = spotlightTarget || managementButton;
            this.manualPluginDashboardOpenAllowed = true;
            this.manualPluginDashboardOpenTarget = managementButton;
            this.manualPluginDashboardOpenUserClicked = false;
            if (this.overlay && typeof this.overlay.setInteractionShieldEnabled === 'function') {
                this.overlay.setInteractionShieldEnabled(false);
            }
            this.recordExperienceMetric('plugin_dashboard_popup_blocked_prompt', {
                targetPage: 'plugin_dashboard'
            });

            try {
                this.revealUserCursor();
                this.overlay.activateSpotlight(target);
                this.cursor.wobble();
                const targetRect = this.getElementRect(target) || this.getElementRect(managementButton);
                const promptText = this.resolveGuideCopy(
                    PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY,
                    PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT
                );
                this.showGuideBubble(promptText, {
                    anchorRect: targetRect || null,
                    emotion: 'surprised',
                    bubbleVariant: 'plugin-manual-open'
                }, this.currentSceneId || 'plugin_dashboard_manual_open');

                const openedWindow = await this.waitForOpenedWindow(
                    PLUGIN_DASHBOARD_WINDOW_NAME,
                    normalizedTimeoutMs
                );
                if (openedWindow && !openedWindow.closed) {
                    // If the popup was opened by the user clicking the highlighted tutorial
                    // target, it still belongs to this tutorial step and should be closed
                    // after the dashboard preview. Pre-existing dashboard windows are
                    // handled before this manual prompt path and remain user-owned.
                    const createdByGuide = !!(
                        this.manualPluginDashboardOpenUserClicked
                        || (
                            guideOpenTriggeredBeforePrompt
                            && !this.manualPluginDashboardOpenUserClicked
                        )
                    );
                    this.recordExperienceMetric('plugin_dashboard_popup_manual_opened', {
                        targetPage: 'plugin_dashboard',
                        createdByGuide: createdByGuide
                    });
                    return {
                        window: openedWindow,
                        createdByGuide: createdByGuide
                    };
                }

                this.recordExperienceMetric('plugin_dashboard_popup_manual_open_timeout', {
                    targetPage: 'plugin_dashboard'
                });
                return {
                    window: null,
                    createdByGuide: false
                };
            } finally {
                this.manualPluginDashboardOpenAllowed = false;
                this.manualPluginDashboardOpenTarget = null;
                this.manualPluginDashboardOpenUserClicked = false;
                if (this.overlay && typeof this.overlay.setInteractionShieldEnabled === 'function') {
                    this.overlay.setInteractionShieldEnabled(
                        !!(document.body && document.body.classList.contains('yui-taking-over'))
                    );
                }
                if (runId === this.sceneRunId && !this.isStopping()) {
                    this.overlay.hideBubble();
                }
            }
        }

        getPluginDashboardExpectedOrigin() {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.getPluginDashboardExpectedOrigin === 'function') {
                try {
                    const apiOrigin = api.getPluginDashboardExpectedOrigin();
                    if (typeof apiOrigin === 'string' && apiOrigin.trim() !== '') {
                        const trimmedOrigin = apiOrigin.trim();
                        try {
                            return new URL(trimmedOrigin).origin;
                        } catch (_) {}
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 获取插件面板 origin 失败:', error);
                }
            }
            if (window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN) {
                try {
                    return new URL(String(window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN), window.location.href).origin;
                } catch (_) {}
            }
            if (window.NEKO_USER_PLUGIN_BASE) {
                try {
                    return new URL(String(window.NEKO_USER_PLUGIN_BASE), window.location.href).origin;
                } catch (_) {}
            }
            return 'http://127.0.0.1:48916';
        }

        isTrustedPluginDashboardOrigin(origin) {
            if (typeof origin !== 'string' || origin.trim() === '') {
                return false;
            }
            try {
                const url = new URL(origin);
                const hostname = String(url.hostname || '').toLowerCase();
                return (
                    (url.protocol === 'http:' || url.protocol === 'https:')
                    && (
                        hostname === '127.0.0.1'
                        || hostname === 'localhost'
                        || hostname === '::1'
                    )
                );
            } catch (_) {
                return false;
            }
        }

        async openModelManagerPage(lanlanName) {
            const api = this.getHomeInteractionApi();
            const targetLanlanName = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            if (api && typeof api.openModelManagerPage === 'function') {
                try {
                    const openedWindow = await resolveWithTimeout(
                        api.openModelManagerPage(targetLanlanName),
                        3600,
                        null,
                        'openModelManagerPage'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] openModelManagerPage 失败，改用本地兜底:', error);
                }
            }

            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            const windowName = this.getModelManagerWindowName(targetLanlanName, appearanceMenuId);
            if (api && typeof api.openPage === 'function') {
                try {
                    return await resolveWithTimeout(
                        api.openPage(
                            '/model_manager?lanlan_name=' + encodeURIComponent(targetLanlanName),
                            windowName
                        ),
                        3600,
                        null,
                        'openPage(model_manager)'
                    );
                } catch (error) {
                    console.warn('[YuiGuide] openPage(model_manager) 失败:', error);
                }
            }

            return null;
        }

        async performCaptureCursorPrelude(durationMs) {
            const totalDurationMs = Number.isFinite(durationMs) ? Math.max(600, durationMs) : 2000;
            const origin = this.cursor.hasPosition()
                ? this.overlay.getCursorPosition()
                : this.getDefaultCursorOrigin();
            if (!origin) {
                return;
            }

            if (!this.cursor.hasPosition()) {
                this.cursor.showAt(origin.x, origin.y);
                if (!(await this.waitForSceneDelay(120))) {
                    return;
                }
            }

            const points = [
                { x: origin.x - 60, y: origin.y - 36 },
                { x: origin.x + 54, y: origin.y - 24 },
                { x: origin.x + 42, y: origin.y + 48 },
                { x: origin.x - 48, y: origin.y + 36 },
                { x: origin.x, y: origin.y }
            ];
            const segmentDurationMs = Math.max(180, Math.round(totalDurationMs / points.length));

            for (let index = 0; index < points.length; index += 1) {
                const point = points[index];
                const moved = await this.cursor.moveToPoint(point.x, point.y, {
                    durationMs: segmentDurationMs,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (!moved && this.isStopping()) {
                    return;
                }
                if (!moved) {
                    if (!this.scenePausedForResistance) {
                        if (this.isCursorTransientMotionActive()) {
                            await this.waitForCursorTransientMotion();
                            index -= 1;
                            continue;
                        }
                        return;
                    }
                    await this.waitUntilSceneResumed();
                    index -= 1;
                    continue;
                }
                if (this.scenePausedForResistance) {
                    await this.waitUntilSceneResumed();
                }
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }
                this.cursor.wobble();
                if (!(await this.waitForSceneDelay(60))) {
                    return;
                }
            }
        }

        async moveCursorToElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            while (!this.isStopping()) {
                await this.waitUntilSceneResumed();
                const rect = this.getElementRect(element);
                if (!rect) {
                    return false;
                }

                const moved = await this.cursor.moveToRect(rect, {
                    durationMs: Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS,
                    exactDuration: normalizedOptions.exactDuration === true,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (moved) {
                    this.rememberAvatarFloatingSceneCursorAnchor(this.currentSceneId, element);
                    return true;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
            }

            return false;
        }

        async resolveElementCenterPoint(element, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 800;
            const startedAt = Date.now();
            let pausedAt = 0;
            let pausedTotalMs = 0;

            while ((Date.now() - startedAt - pausedTotalMs) < normalizedTimeoutMs) {
                if (this.destroyed || this.angryExitTriggered) {
                    return null;
                }

                const now = Date.now();
                if (this.scenePausedForResistance) {
                    if (!pausedAt) {
                        pausedAt = now;
                    }
                    await wait(80);
                    continue;
                }

                if (pausedAt) {
                    pausedTotalMs += Math.max(0, now - pausedAt);
                    pausedAt = 0;
                }

                const rect = this.getElementRect(element);
                if (rect) {
                    return {
                        x: rect.left + (rect.width / 2),
                        y: rect.top + (rect.height / 2),
                        rect: rect
                    };
                }

                await this.waitForSceneDelay(80);
            }

            const finalRect = this.getElementRect(element);
            if (!finalRect) {
                return null;
            }

            return {
                x: finalRect.left + (finalRect.width / 2),
                y: finalRect.top + (finalRect.height / 2),
                rect: finalRect
            };
        }

        async moveCursorToTrackedElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            const totalDurationMs = Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS;
            const firstLegMs = Math.max(180, Math.round(totalDurationMs * 0.7));
            const secondLegMs = Math.max(140, totalDurationMs - firstLegMs);
            const recheckDelayMs = Number.isFinite(normalizedOptions.recheckDelayMs)
                ? normalizedOptions.recheckDelayMs
                : 320;
            const settleDelayMs = Number.isFinite(normalizedOptions.settleDelayMs)
                ? normalizedOptions.settleDelayMs
                : 0;

            const initialPoint = await this.resolveElementCenterPoint(element, 420);
            if (!initialPoint) {
                return false;
            }
            while (!this.isStopping()) {
                const movedToInitialPoint = await this.cursor.moveToPoint(initialPoint.x, initialPoint.y, {
                    durationMs: firstLegMs,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (movedToInitialPoint) {
                    break;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
                await this.waitUntilSceneResumed();
            }
            if (this.isStopping()) {
                return false;
            }

            if (settleDelayMs > 0) {
                if (!(await this.waitForSceneDelay(settleDelayMs))) {
                    return false;
                }
            }
            if (recheckDelayMs > 0) {
                if (!(await this.waitForSceneDelay(recheckDelayMs))) {
                    return false;
                }
            }
            if (this.destroyed || this.angryExitTriggered) {
                return false;
            }

            const finalPoint = await this.resolveElementCenterPoint(element, 420);
            if (!finalPoint) {
                return false;
            }

            while (!this.isStopping()) {
                const movedToFinalPoint = await this.cursor.moveToPoint(finalPoint.x, finalPoint.y, {
                    durationMs: secondLegMs,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (movedToFinalPoint) {
                    return true;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
                await this.waitUntilSceneResumed();
            }

            return false;
        }

        isCursorAlignedWithElement(element, tolerancePx) {
            const cursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const rect = this.getElementRect(element);
            if (!cursorPosition || !rect) {
                return false;
            }

            const tolerance = Number.isFinite(tolerancePx) ? Math.max(0, tolerancePx) : 6;
            return cursorPosition.x >= rect.left - tolerance
                && cursorPosition.x <= rect.right + tolerance
                && cursorPosition.y >= rect.top - tolerance
                && cursorPosition.y <= rect.bottom + tolerance;
        }

        async realignCursorToAgentSidePanelAction(toggleId, actionId, durationMs) {
            const stablePanel = await this.waitForAgentSidePanelLayoutStable(toggleId, 980);
            if (!stablePanel || this.isStopping()) {
                return false;
            }

            const button = await this.waitForVisibleElement(() => {
                const actionButton = this.getAgentSidePanelButton(toggleId, actionId);
                if (!actionButton || !this.isAgentSidePanelVisible(toggleId)) {
                    return null;
                }
                return this.getElementRect(actionButton) ? actionButton : null;
            }, 900);
            if (!button || this.isStopping()) {
                return false;
            }

            this.clearVirtualSpotlight('plugin-management-entry');
            const spotlightTarget = this.createPluginManagementEntrySpotlight(button) || button;
            this.replaceRetainedExtraSpotlight(
                (candidate) => candidate
                    && (
                        candidate === button
                        || (
                            typeof candidate.getAttribute === 'function'
                            && candidate.getAttribute('data-yui-guide-virtual-spotlight') === 'plugin-management-entry'
                        )
                    ),
                spotlightTarget
            );
            this.overlay.activateSpotlight(spotlightTarget);

            if (this.isCursorAlignedWithElement(button, 5)) {
                return true;
            }

            return this.moveCursorToElement(
                button,
                Number.isFinite(durationMs) ? durationMs : 360
            );
        }

        async clickCursorAndWait(holdMs) {
            const visibleMs = clamp(
                Math.round(Number.isFinite(holdMs) ? holdMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                900
            );
            this.cursor.click(visibleMs);
            return await this.waitForSceneDelay(visibleMs);
        }

        async clickCursorAndWaitExact(holdMs) {
            const visibleMs = clamp(
                Math.round(Number.isFinite(holdMs) ? holdMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                120,
                900
            );
            this.cursor.click(visibleMs);
            return await this.waitForSceneDelay(visibleMs);
        }

        async runActionWithCursorClick(holdMs, action) {
            const clickPromise = this.clickCursorAndWait(holdMs);
            let actionPromise = Promise.resolve(true);
            if (typeof action === 'function') {
                try {
                    actionPromise = Promise.resolve(action());
                } catch (error) {
                    actionPromise = Promise.reject(error);
                }
                actionPromise.catch(() => {});
            }
            const clickCompleted = await clickPromise;
            if (!clickCompleted) {
                return false;
            }
            return await actionPromise;
        }

        async runActionWithCursorClickExact(holdMs, action) {
            const clickPromise = this.clickCursorAndWaitExact(holdMs);
            let actionPromise = Promise.resolve(true);
            if (typeof action === 'function') {
                try {
                    actionPromise = Promise.resolve(action());
                } catch (error) {
                    actionPromise = Promise.reject(error);
                }
                actionPromise.catch(() => {});
            }
            const clickCompleted = await clickPromise;
            if (!clickCompleted) {
                return false;
            }
            return await actionPromise;
        }

        hoverElement(element) {
            if (!element) {
                return;
            }

            try {
                element.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
                element.dispatchEvent(new MouseEvent('mouseover', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            } catch (_) {}
        }

        stopHoverElement(element) {
            if (!element) {
                return;
            }

            try {
                element.dispatchEvent(new MouseEvent('mouseleave', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
                element.dispatchEvent(new MouseEvent('mouseout', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            } catch (_) {}
        }

        getVisibleHomeModelElement() {
            const candidates = [
                document.getElementById('live2d-container'),
                document.getElementById('vrm-container'),
                document.getElementById('mmd-container')
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const element = candidates[index];
                if (this.isElementVisible(element)) {
                    return element;
                }
            }

            return null;
        }

        async waitForHomeMainUIReady(timeoutMs) {
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 恢复主界面失败:', error);
                }
            }

            return this.waitForElement(() => {
                const settingsButton = this.getFallbackFloatingButton('settings');
                const modelElement = this.getVisibleHomeModelElement();
                if (this.isElementVisible(settingsButton) && modelElement) {
                    return settingsButton;
                }

                return null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 3200);
        }

        async performHighlightedApiClick(options) {
            const normalized = options || {};
            const target = normalized.target || null;
            if (!target) {
                return false;
            }

            this.applyGuideHighlights({
                primary: target,
                secondary: normalized.secondary || null
            });
            const moved = await this.moveCursorToElement(target, normalized.durationMs);
            if (!moved) {
                return false;
            }
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            const clickVisibleMs = clamp(
                Math.round(Number.isFinite(normalized.clickVisibleMs) ? normalized.clickVisibleMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                900
            );
            const actionResultPromise = this.runActionWithCursorClick(clickVisibleMs, normalized.action);
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }
            const actionResult = await actionResultPromise;
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            return !!actionResult;
        }

        getVoiceControlButtonTarget() {
            return this.getFloatingButtonShell(
                this.getFallbackFloatingButton('mic')
                || this.resolveElement(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.voiceControl))
            );
        }

        async runIntroVoiceControlButtonShowcase(voiceKey, fallbackText) {
            const voiceControlButton = this.getVoiceControlButtonTarget();
            if (!voiceControlButton) {
                return;
            }

            this.setSpotlightGeometryHint(voiceControlButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.overlay.activateSpotlight(voiceControlButton);

            await this.waitForExternalizedChatCursorMove('day1_history_handle', 1800);

            if (!this.cursor.hasPosition()) {
                const historyHandleAnchor = this.getAvatarFloatingSceneCursorAnchor('day1_history_handle');
                if (historyHandleAnchor) {
                    this.cursor.showAt(historyHandleAnchor.x, historyHandleAnchor.y);
                }
            }

            if (!this.cursor.hasPosition() && !this.restoreCursorFromExternalizedChatAnchor(30000)) {
                const introTarget = this.getChatInputTarget() || this.getChatWindowTarget();
                const introRect = this.getElementRect(introTarget);
                if (introRect) {
                    this.cursor.showAt(
                        introRect.left + introRect.width / 2,
                        introRect.top + introRect.height / 2
                    );
                } else {
                    const origin = this.getDefaultCursorOrigin();
                    this.cursor.showAt(origin.x, origin.y);
                }
            }

            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const moveDurationMs = clamp(Math.round(narrationDurationMs * 0.16), 900, 2200);
            await this.moveCursorToElement(voiceControlButton, moveDurationMs);
        }

        async runTakeoverKeyboardControlSequence(step, performance, runId) {
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);
            const createToggleSpotlightTarget = (key, element) => {
                const rect = this.getElementRect(element);
                if (!rect) {
                    return element;
                }

                return this.createVirtualSpotlight(key, {
                    left: Math.max(0, rect.left - 8),
                    top: Math.max(0, rect.top - 4),
                    right: Math.min(window.innerWidth, rect.right + 8),
                    bottom: Math.min(window.innerHeight, rect.bottom + 4)
                }, {
                    padding: 4,
                    radius: 18
                });
            };
            const catPawButton = await this.waitForVisibleTarget([
                () => this.getFloatingButtonShell(this.getFallbackFloatingButton('agent')),
                () => this.getFloatingButtonShell(this.resolveElement((performance && performance.cursorTarget) || '')),
                () => this.getFloatingButtonShell(this.resolveElement(step && step.anchor ? step.anchor : '')),
                () => this.getFloatingButtonShell(this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw)))
            ], 2200);
            if (!catPawButton || guardFailed()) {
                return false;
            }
            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.addRetainedExtraSpotlight(catPawButton);

            const openedAgentPanel = await this.performHighlightedApiClick({
                target: catPawButton,
                durationMs: scaleSceneMs(1500, 900, 2600),
                runId: runId,
                action: () => this.openAgentPanel()
            });
            if (!openedAgentPanel || guardFailed()) {
                return false;
            }

            if (this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile('expressions/xxy.exp3.json');
            }

            const agentMasterToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-master');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 4000);
            if (!agentMasterToggle || guardFailed()) {
                return false;
            }
            const agentMasterSpotlight = createToggleSpotlightTarget('takeover-agent-master-toggle', agentMasterToggle);
            this.addRetainedExtraSpotlight(agentMasterSpotlight);

            const enabledAgentMaster = await this.performHighlightedApiClick({
                target: agentMasterSpotlight,
                durationMs: scaleSceneMs(1200, 760, 2200),
                runId: runId,
                action: async () => {
                    const enabled = await this.setAgentMasterEnabled(true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-master', true, 1800));
                }
            });
            if (!enabledAgentMaster || guardFailed()) {
                return false;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(240, 120, 600))) || guardFailed()) {
                return false;
            }

            const keyboardToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-keyboard');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 2400);
            if (!keyboardToggle || guardFailed()) {
                return false;
            }
            const keyboardToggleSpotlight = createToggleSpotlightTarget('takeover-keyboard-toggle', keyboardToggle);
            this.addRetainedExtraSpotlight(keyboardToggleSpotlight);
            this.removeRetainedExtraSpotlight(agentMasterSpotlight);

            const enabledKeyboardControl = await this.performHighlightedApiClick({
                target: keyboardToggleSpotlight,
                durationMs: scaleSceneMs(520, 320, 950),
                runId: runId,
                action: async () => {
                    const enabled = await this.setAgentFlagEnabled('computer_use_enabled', true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-keyboard', true, 1800));
                }
            });
            if (!enabledKeyboardControl || guardFailed()) {
                return false;
            }
            this.rememberAvatarFloatingSceneCursorAnchor('day1_takeover_capture_cursor', keyboardToggleSpotlight);

            const ghostCursorLookAtHandle = await this.startGhostCursorLookAtPerformance({
                isCancelled: () => guardFailed()
            });
            await this.stopIntroVoiceCursorLookAtPerformance(
                    ghostCursorLookAtHandle,
                    'takeover_keyboard_control_complete'
                );
            await this.stopPersistentGhostCursorLookAtPerformance('takeover_top_peek');
            if (guardFailed()) {
                return false;
            }
            this.takeoverTopPeekHandle = await this.startPluginDashboardCornerPeekPerformance(runId, {
                targetPreset: 'top_flipped'
            });
            if (guardFailed()) {
                return false;
            }

            if (this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile('expressions/slh.exp3.json');
            }

            await this.waitForSceneDelay(scaleSceneMs(180, 80, 420));
            return !guardFailed();
        }

        async runPluginDashboardLaunchSequence(step, performance, runId) {
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);

            if (!(await this.openAgentPanel()) || guardFailed()) {
                return null;
            }

            const pluginToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 2200);
            if (!pluginToggle || guardFailed()) {
                return null;
            }

            const enabledUserPlugin = await this.performHighlightedApiClick({
                target: pluginToggle,
                durationMs: scaleSceneMs(1300, 820, 2300),
                runId: runId,
                action: async () => {
                    const enabled = await this.setAgentFlagEnabled('user_plugin_enabled', true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-user-plugin', true, 1800));
                }
            });
            if (!enabledUserPlugin || guardFailed()) {
                return null;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(180, 80, 420))) || guardFailed()) {
                return null;
            }

            this.hoverElement(pluginToggle);
            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                2600
            );
            if (!managementButton || guardFailed()) {
                return null;
            }

            const stableManagementButton = await this.waitForStableElementRect(
                managementButton,
                scaleSceneMs(320, 160, 760)
            );
            const managementMovementTarget = stableManagementButton || managementButton;
            if (!managementMovementTarget || guardFailed()) {
                return null;
            }

            this.clearVirtualSpotlight('plugin-management-entry');
            const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;

            this.overlay.activateSpotlight(managementSpotlightTarget);
            if (!(await this.waitForSceneDelay(scaleSceneMs(60, 40, 180))) || guardFailed()) {
                return null;
            }

            const movedToManagementButton = await this.moveCursorToTrackedElement(
                managementMovementTarget,
                scaleSceneMs(1900, 1200, 3200),
                {
                    recheckDelayMs: scaleSceneMs(180, 80, 420)
                }
            );
            if (!movedToManagementButton || guardFailed()) {
                return null;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(90, 40, 220))) || guardFailed()) {
                return null;
            }

            const realignedToManagementButton = await this.realignCursorToAgentSidePanelAction(
                'agent-user-plugin',
                'management-panel',
                scaleSceneMs(420, 180, 760)
            );
            if (!realignedToManagementButton || guardFailed()) {
                return null;
            }

            const managementOpenResult = await this.runActionWithCursorClick(scaleSceneMs(180, 90, 420), async () => {
                const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                    keepMainUIVisible: true
                });
                return {
                    existingPluginDashboardWindow,
                    hadPluginDashboard,
                    agentPanelActionOpened
                };
            });
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            const guideTriggeredPluginDashboardOpen = !!agentPanelActionOpened;

            let pluginDashboardWindow = null;
            if (hadPluginDashboard) {
                try {
                    existingPluginDashboardWindow.location.reload();
                    pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                    this.pluginDashboardWindowCreatedByGuide = false;
                } catch (error) {
                    console.warn('[YuiGuide] 刷新已有插件面板失败:', error);
                    pluginDashboardWindow = await this.openPluginDashboardWindow({
                        keepMainUIVisible: true
                    });
                    if (!pluginDashboardWindow || pluginDashboardWindow.closed) {
                        pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                    }
                    this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
                    if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                        try {
                            existingPluginDashboardWindow.close();
                        } catch (closeError) {
                            console.warn('[YuiGuide] 关闭旧插件面板失败:', closeError);
                        }
                    }
                }
            } else if (agentPanelActionOpened) {
                pluginDashboardWindow = await this.waitForOpenedWindow(
                    PLUGIN_DASHBOARD_WINDOW_NAME,
                    scaleSceneMs(1200, 700, 1800)
                );
                this.pluginDashboardWindowCreatedByGuide = !!(
                    guideTriggeredPluginDashboardOpen
                    && pluginDashboardWindow
                    && !pluginDashboardWindow.closed
                );
            }

            if (
                (!pluginDashboardWindow || pluginDashboardWindow.closed)
                && runId === this.sceneRunId
                && !this.destroyed
                && !this.angryExitTriggered
            ) {
                const manualPluginDashboardOpen = await this.waitForManualPluginDashboardOpen(
                    managementButton,
                    managementSpotlightTarget,
                    runId,
                    scaleSceneMs(18000, 9000, 26000),
                    guideTriggeredPluginDashboardOpen
                );
                pluginDashboardWindow = manualPluginDashboardOpen && manualPluginDashboardOpen.window;
                this.pluginDashboardWindowCreatedByGuide = !!(
                    manualPluginDashboardOpen
                    && manualPluginDashboardOpen.createdByGuide
                    && pluginDashboardWindow
                    && !pluginDashboardWindow.closed
                );
            }

            return {
                pluginDashboardWindow: pluginDashboardWindow,
                pluginToggle: pluginToggle,
                managementSpotlightTarget: managementSpotlightTarget
            };
        }

        async runPluginPreviewHomeExitSequence(targets, runId, scaleSceneMs) {
            const normalizedTargets = targets || {};
            const delay = async (value, minValue, maxValue) => {
                const waitMs = typeof scaleSceneMs === 'function'
                    ? scaleSceneMs(value, minValue, maxValue)
                    : value;
                return this.waitForSceneDelay(waitMs);
            };
            const guardFailed = () => runId !== this.sceneRunId || this.isStopping();
            const removeHighlight = async (element) => {
                if (!element || guardFailed()) {
                    return;
                }
                this.removeRetainedExtraSpotlight(element);
                await delay(140, 80, 260);
            };

            await removeHighlight(normalizedTargets.managementButton);
            await removeHighlight(normalizedTargets.pluginToggle);
            await removeHighlight(normalizedTargets.agentMasterToggle);
            if (guardFailed()) {
                return;
            }

            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            await delay(180, 100, 360);
            if (guardFailed()) {
                return;
            }

            await this.closeAgentPanel().catch(() => {});
            await removeHighlight(normalizedTargets.catPawButton);
        }

        async cleanupPluginPreviewState(targets) {
            const normalizedTargets = targets || {};
            this.stopHoverElement(normalizedTargets.hoverTarget || normalizedTargets.pluginToggle || null);
            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            this.overlay.clearActionSpotlight();
            await this.closePluginDashboardWindowIfCreatedByGuide('插件预览中途清理');
            await this.closeAgentPanel().catch(() => {});
        }

        async runTakeoverCaptureActionSequence(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            let shouldCleanupPreviewState = false;
            let pluginPreviewCleanedUp = false;
            let hoveredPluginToggle = null;
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);

            const catPawButton = await this.waitForVisibleTarget([
                () => this.getFloatingButtonShell(this.getFallbackFloatingButton('agent')),
                () => this.getFloatingButtonShell(this.resolveElement((performance && performance.cursorTarget) || '')),
                () => this.getFloatingButtonShell(this.resolveElement(step && step.anchor ? step.anchor : '')),
                () => this.getFloatingButtonShell(this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw)))
            ], 2200);
            if (!catPawButton || guardFailed()) {
                return null;
            }
            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });

            try {
                // 1-3. 高亮猫爪 -> 平滑移动 -> 点击并打开猫爪面板
                shouldCleanupPreviewState = true;
                this.addRetainedExtraSpotlight(catPawButton);
                this.overlay.clearActionSpotlight();
                const movedToCatPaw = await this.moveCursorToElement(catPawButton, scaleSceneMs(1500, 900, 2600));
                if (!movedToCatPaw || guardFailed()) {
                    return null;
                }

                const agentPanelOpened = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.openAgentPanel()
                );
                if (!agentPanelOpened || guardFailed()) {
                    return null;
                }

                const agentMasterToggle = await this.waitForElement(() => {
                    const toggleItem = this.getAgentToggleElement('agent-master');
                    return this.getElementRect(toggleItem) ? toggleItem : null;
                }, 4000);
                if (!agentMasterToggle || guardFailed()) {
                    return null;
                }

                // 4-6. 高亮猫爪总开关 -> 平滑移动 -> 点击并同步打开
                this.addRetainedExtraSpotlight(agentMasterToggle);
                const movedToAgentMaster = await this.moveCursorToElement(agentMasterToggle, scaleSceneMs(1200, 760, 2200));
                if (!movedToAgentMaster || guardFailed()) {
                    return null;
                }

                const agentMasterEnabled = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.setAgentMasterEnabled(true)
                );
                if (!agentMasterEnabled || guardFailed()) {
                    return null;
                }

                const agentMasterState = await this.waitForAgentToggleState('agent-master', true, 1800);
                if (!agentMasterState || guardFailed()) {
                    return null;
                }
                if (!(await this.waitForSceneDelay(scaleSceneMs(420, 180, 900)))) {
                    return null;
                }
                if (guardFailed()) {
                    return null;
                }

                const pluginToggle = await this.waitForElement(() => {
                    const toggleItem = this.getAgentToggleElement('agent-user-plugin');
                    return this.getElementRect(toggleItem) ? toggleItem : null;
                }, 2200);
                if (!pluginToggle || guardFailed()) {
                    return null;
                }

                // 7-9. 高亮用户插件 -> 平滑移动 -> 点击并同步打开
                this.addRetainedExtraSpotlight(pluginToggle);
                const movedToPluginToggle = await this.moveCursorToElement(pluginToggle, scaleSceneMs(1300, 820, 2300));
                if (!movedToPluginToggle || guardFailed()) {
                    return null;
                }

                const pluginToggleEnabled = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.setAgentFlagEnabled('user_plugin_enabled', true)
                );
                if (!pluginToggleEnabled || guardFailed()) {
                    return null;
                }

                const pluginToggleState = await this.waitForAgentToggleState('agent-user-plugin', true, 1800);
                if (!pluginToggleState || guardFailed()) {
                    return null;
                }

                if (!(await this.waitForSceneDelay(scaleSceneMs(180, 80, 420)))) {
                    return null;
                }

                // 10. 通过悬停让管理面板显现
                hoveredPluginToggle = pluginToggle;
                this.hoverElement(pluginToggle);

                const managementButton = await this.ensureAgentSidePanelActionVisible(
                    'agent-user-plugin',
                    'management-panel',
                    2600
                );
                if (!managementButton || guardFailed()) {
                    return null;
                }

                const stableManagementButton = await this.waitForStableElementRect(
                    managementButton,
                    scaleSceneMs(320, 160, 760)
                );
                const managementMovementTarget = stableManagementButton || managementButton;
                if (!managementMovementTarget || guardFailed()) {
                    return null;
                }
                this.clearVirtualSpotlight('plugin-management-entry');
                const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;

                // 11-13. 高亮管理面板 -> 移动到高亮中心点 -> 点击并同步打开真实页面
                this.addRetainedExtraSpotlight(managementSpotlightTarget);
                if (!(await this.waitForSceneDelay(scaleSceneMs(60, 40, 180)))) {
                    return null;
                }
                const movedToManagementButton = await this.moveCursorToTrackedElement(
                    managementMovementTarget,
                    scaleSceneMs(1900, 1200, 3200),
                    {
                        recheckDelayMs: scaleSceneMs(180, 80, 420)
                    }
                );
                if (!movedToManagementButton || guardFailed()) {
                    return null;
                }

                if (!(await this.waitForSceneDelay(scaleSceneMs(90, 40, 220)))) {
                    return null;
                }
                const realignedToManagementButton = await this.realignCursorToAgentSidePanelAction(
                    'agent-user-plugin',
                    'management-panel',
                    scaleSceneMs(420, 180, 760)
                );
                if (!realignedToManagementButton || guardFailed()) {
                    return null;
                }
                const managementOpenResult = await this.runActionWithCursorClick(scaleSceneMs(180, 90, 420), async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                });
                const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
                const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
                const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
                const guideTriggeredPluginDashboardOpen = !!agentPanelActionOpened;
                let pluginDashboardWindow = null;
                if (hadPluginDashboard) {
                    try {
                        existingPluginDashboardWindow.location.reload();
                        pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                        this.pluginDashboardWindowCreatedByGuide = false;
                    } catch (error) {
                        console.warn('[YuiGuide] 刷新已有插件面板失败:', error);
                        pluginDashboardWindow = await this.openPluginDashboardWindow({
                            keepMainUIVisible: true
                        });
                        if (!pluginDashboardWindow || pluginDashboardWindow.closed) {
                            pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                        }
                        this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
                        if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                            try {
                                existingPluginDashboardWindow.close();
                            } catch (closeError) {
                                console.warn('[YuiGuide] 关闭旧插件面板失败:', closeError);
                            }
                        }
                    }
                } else if (agentPanelActionOpened) {
                    pluginDashboardWindow = await this.waitForOpenedWindow(
                        PLUGIN_DASHBOARD_WINDOW_NAME,
                        scaleSceneMs(1200, 700, 1800)
                    );
                    this.pluginDashboardWindowCreatedByGuide = !!(
                        guideTriggeredPluginDashboardOpen
                        && pluginDashboardWindow
                        && !pluginDashboardWindow.closed
                    );
                }
                if (
                    (!pluginDashboardWindow || pluginDashboardWindow.closed)
                    && runId === this.sceneRunId
                    && !this.destroyed
                    && !this.angryExitTriggered
                ) {
                    const manualPluginDashboardOpen = await this.waitForManualPluginDashboardOpen(
                        managementButton,
                        managementSpotlightTarget,
                        runId,
                        scaleSceneMs(18000, 9000, 26000),
                        guideTriggeredPluginDashboardOpen
                    );
                    pluginDashboardWindow = manualPluginDashboardOpen && manualPluginDashboardOpen.window;
                    this.pluginDashboardWindowCreatedByGuide = !!(
                        manualPluginDashboardOpen
                        && manualPluginDashboardOpen.createdByGuide
                        && pluginDashboardWindow
                        && !pluginDashboardWindow.closed
                    );
                }

                if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                    await this.runPluginPreviewHomeExitSequence({
                        managementButton: managementSpotlightTarget,
                        pluginToggle: pluginToggle,
                        agentMasterToggle: agentMasterToggle,
                        catPawButton: catPawButton
                    }, runId, scaleSceneMs);
                    pluginPreviewCleanedUp = true;
                    shouldCleanupPreviewState = false;
                }
                return pluginDashboardWindow;
            } finally {
                if (shouldCleanupPreviewState && !pluginPreviewCleanedUp) {
                    await this.cleanupPluginPreviewState({
                        catPawButton: catPawButton,
                        hoverTarget: hoveredPluginToggle
                    }).catch(() => {});
                }
            }
        }

        finishPluginDashboardHandoff(reason) {
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || typeof handoff.resolve !== 'function') {
                return false;
            }
            handoff.failureReason = typeof reason === 'string' && reason
                ? reason
                : 'plugin_dashboard_finished_by_home';
            handoff.resolve(false);
            return true;
        }

        async waitForPluginDashboardPerformanceUntilNarrationBoundary(windowRef, payload, options) {
            const normalizedOptions = options && typeof options === 'object' ? options : {};
            const narrationDurationMs = Number.isFinite(normalizedOptions.narrationDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.narrationDurationMs))
                : 0;
            const elapsedNarrationMs = Number.isFinite(normalizedOptions.elapsedNarrationMs)
                ? Math.max(0, Math.round(normalizedOptions.elapsedNarrationMs))
                : 0;
            const remainingNarrationMs = Math.max(0, narrationDurationMs - elapsedNarrationMs);
            const performancePromise = this.waitForPluginDashboardPerformance(windowRef, payload).catch(() => false);
            if (narrationDurationMs <= 0) {
                return await performancePromise;
            }

            let settled = false;
            let boundaryTimer = 0;
            let graceTimer = 0;
            const boundaryPromise = new Promise((resolve) => {
                boundaryTimer = window.setTimeout(() => {
                    boundaryTimer = 0;
                    if (settled || this.angryExitTriggered || this.destroyed) {
                        resolve(false);
                        return;
                    }
                    this.notifyPluginDashboardNarrationFinished();
                    graceTimer = window.setTimeout(() => {
                        graceTimer = 0;
                        if (!settled) {
                            this.finishPluginDashboardHandoff('plugin_dashboard_done_grace_timeout');
                        }
                        resolve(false);
                    }, DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS);
                }, remainingNarrationMs);
            });

            try {
                return await Promise.race([performancePromise, boundaryPromise]);
            } finally {
                settled = true;
                if (boundaryTimer) {
                    window.clearTimeout(boundaryTimer);
                    boundaryTimer = 0;
                }
                if (graceTimer) {
                    window.clearTimeout(graceTimer);
                    graceTimer = 0;
                }
            }
        }

        async waitForPluginDashboardPerformance(windowRef, payload) {
            if (!windowRef || windowRef.closed) {
                this.recordExperienceMetric('handoff_failed', {
                    sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                    targetPage: 'plugin_dashboard',
                    reason: 'plugin_dashboard_window_missing'
                });
                return Promise.resolve(false);
            }

            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.reject === 'function') {
                this.pluginDashboardHandoff.reject(new Error('plugin-dashboard handoff superseded'));
            }

            const skipButtonScreenRect = await this.getSkipButtonScreenRect();

            return new Promise((resolve, reject) => {
                this.pluginDashboardLastInterruptRequestId = '';
                const sessionId = 'plugin-dashboard-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                const startedAt = Date.now();
                const handoffPayload = Object.assign({}, payload || {}, {
                    interruptCount: Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0)),
                    skipButtonScreenRect: skipButtonScreenRect,
                    platformCapabilities: {
                        version: 1,
                        platform: this.platformCapabilities && this.platformCapabilities.platform
                            ? this.platformCapabilities.platform
                            : 'web',
                        windowBoundsSource: this.platformCapabilities && this.platformCapabilities.windowBoundsSource
                            ? this.platformCapabilities.windowBoundsSource
                            : 'browser-screen-origin',
                        supportsExternalChat: !!(this.platformCapabilities && this.platformCapabilities.supportsExternalChat),
                        supportsSystemTrayHint: !!(this.platformCapabilities && this.platformCapabilities.supportsSystemTrayHint),
                        supportsPluginDashboardWindow: !!(this.platformCapabilities && this.platformCapabilities.supportsPluginDashboardWindow),
                        pointerProfile: this.platformCapabilities && this.platformCapabilities.pointerProfile
                            ? this.platformCapabilities.pointerProfile
                            : 'pointer',
                        preferredSkipHitPadding: this.platformCapabilities && Number.isFinite(this.platformCapabilities.preferredSkipHitPadding)
                            ? this.platformCapabilities.preferredSkipHitPadding
                            : 18
                    }
                });
                const preloadTimeoutMs = 15000;
                const handoffVoiceDurationMs = this.getGuideVoiceDurationMs(
                    handoffPayload && handoffPayload.voiceKey,
                    resolveGuideLocale()
                );
                const executionTimeoutMs = clamp(
                    (handoffVoiceDurationMs > 0 ? handoffVoiceDurationMs : 0) + 12000,
                    12000,
                    42000
                );
                const targetOrigin = this.getPluginDashboardExpectedOrigin();
                if (!targetOrigin) {
                    this.recordExperienceMetric('handoff_failed', {
                        sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                        targetPage: 'plugin_dashboard',
                        reason: 'target_origin_missing'
                    });
                    resolve(false);
                    return;
                }
                const handoff = {
                    sessionId: sessionId,
                    windowRef: windowRef,
                    targetOrigin: targetOrigin,
                    ready: false,
                    readyAt: 0,
                    failureReason: '',
                    resolve: (result) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        if (!result) {
                            this.recordExperienceMetric('handoff_failed', {
                                sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                                targetPage: 'plugin_dashboard',
                                reason: handoff.failureReason || 'unknown'
                            });
                        }
                        resolve(result);
                    },
                    reject: (error) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        reject(error);
                    },
                    post: () => {
                        if (!windowRef || windowRef.closed) {
                            handoff.failureReason = 'plugin_dashboard_window_closed';
                            handoff.resolve(false);
                            return;
                        }
                        try {
                            windowRef.postMessage({
                                type: PLUGIN_DASHBOARD_HANDOFF_EVENT,
                                sessionId: sessionId,
                                payload: handoffPayload
                            }, handoff.ready ? handoff.targetOrigin : '*');
                        } catch (error) {
                            console.warn('[YuiGuide] 向插件面板发送 handoff 消息失败:', error);
                        }
                    }
                };

                handoff.intervalId = window.setInterval(() => {
                    if (!windowRef || windowRef.closed) {
                        handoff.failureReason = 'plugin_dashboard_window_closed';
                        handoff.resolve(false);
                        return;
                    }

                    if (!handoff.ready && (Date.now() - startedAt) >= preloadTimeoutMs) {
                        handoff.failureReason = 'plugin_dashboard_ready_timeout';
                        handoff.resolve(false);
                        return;
                    }

                    if (handoff.ready && handoff.readyAt > 0 && (Date.now() - handoff.readyAt) >= executionTimeoutMs) {
                        handoff.failureReason = 'plugin_dashboard_execution_timeout';
                        handoff.resolve(false);
                        return;
                    }
                    if (!handoff.ready) {
                        handoff.post();
                    }
                }, 450);
                handoff.timeoutId = window.setTimeout(() => {
                    handoff.failureReason = handoff.ready ? 'plugin_dashboard_execution_timeout' : 'plugin_dashboard_ready_timeout';
                    handoff.resolve(false);
                }, preloadTimeoutMs + executionTimeoutMs);

                this.pluginDashboardHandoff = handoff;
                handoff.post();
            });
        }

        dispatchDesktopPluginDashboardInterruptAck(payload) {
            try {
                window.dispatchEvent(new CustomEvent(DESKTOP_PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT, {
                    detail: payload && typeof payload === 'object' ? payload : {}
                }));
            } catch (error) {
                console.warn('[YuiGuide] 发送桌面插件面板 interrupt ack 失败:', error);
            }
        }

        dispatchDesktopPluginDashboardNarrationFinished(payload) {
            try {
                window.dispatchEvent(new CustomEvent(DESKTOP_PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT, {
                    detail: payload && typeof payload === 'object' ? payload : {}
                }));
            } catch (error) {
                console.warn('[YuiGuide] 发送桌面插件面板 narration finished 失败:', error);
            }
        }

        notifyPluginDashboardNarrationFinished() {
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const payload = {
                type: PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
                sessionId: handoff.sessionId
            };
            this.dispatchDesktopPluginDashboardNarrationFinished(payload);

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!windowRef || windowRef.closed) {
                return;
            }

            try {
                windowRef.postMessage(payload, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
            } catch (error) {
                console.warn('[YuiGuide] 向插件面板发送 narration finished 失败:', error);
            }
        }

        notifyPluginDashboardTerminationRequested(reason) {
            const handoff = this.pluginDashboardHandoff;
            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!handoff || !windowRef || windowRef.closed || !handoff.sessionId) {
                return false;
            }

            try {
                windowRef.postMessage({
                    type: PLUGIN_DASHBOARD_TERMINATE_EVENT,
                    sessionId: handoff.sessionId,
                    reason: typeof reason === 'string' && reason.trim() ? reason.trim() : 'skip',
                    closeWindow: true
                }, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 向插件面板发送 terminate 失败:', error);
                return false;
            }
        }

        async getGuideHostWindowBounds() {
            const bridge = window.nekoPetDrag;
            if (!bridge || typeof bridge.getBounds !== 'function') {
                return null;
            }

            try {
                const bounds = await Promise.race([
                    Promise.resolve(bridge.getBounds()),
                    new Promise((resolve) => window.setTimeout(() => resolve(null), 180))
                ]);
                if (!bounds || typeof bounds !== 'object') {
                    return null;
                }

                const x = Number(bounds.x);
                const y = Number(bounds.y);
                const width = Number(bounds.width);
                const height = Number(bounds.height);
                if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
                    return null;
                }

                return {
                    x: Math.round(x),
                    y: Math.round(y),
                    width: Math.round(width),
                    height: Math.round(height),
                    source: 'electron-window-bounds'
                };
            } catch (_) {
                return null;
            }
        }

        async getSkipButtonScreenRect() {
            const skipButton = document.getElementById('neko-tutorial-skip-btn');
            if (!skipButton || typeof skipButton.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = skipButton.getBoundingClientRect();
            if (!(rect.width > 0) || !(rect.height > 0)) {
                return null;
            }

            const hostBounds = await this.getGuideHostWindowBounds();
            const rawScreenLeft = hostBounds && Number.isFinite(hostBounds.x)
                ? hostBounds.x
                : Number.isFinite(Number(window.screenX))
                ? Number(window.screenX)
                : Number(window.screenLeft);
            const rawScreenTop = hostBounds && Number.isFinite(hostBounds.y)
                ? hostBounds.y
                : Number.isFinite(Number(window.screenY))
                ? Number(window.screenY)
                : Number(window.screenTop);
            const screenLeft = Number.isFinite(rawScreenLeft) ? rawScreenLeft : 0;
            const screenTop = Number.isFinite(rawScreenTop) ? rawScreenTop : 0;
            const boundsSource = hostBounds && hostBounds.source
                ? hostBounds.source
                : (this.platformCapabilities && this.platformCapabilities.windowBoundsSource) || 'browser-screen-origin';
            const hitPadding = this.platformCapabilities && typeof this.platformCapabilities.getSkipHitPadding === 'function'
                ? this.platformCapabilities.getSkipHitPadding(boundsSource)
                : 18;

            return {
                left: Math.round(screenLeft + rect.left - hitPadding),
                top: Math.round(screenTop + rect.top - hitPadding),
                right: Math.round(screenLeft + rect.right + hitPadding),
                bottom: Math.round(screenTop + rect.bottom + hitPadding),
                coordinateSpace: boundsSource,
                platform: this.platformCapabilities && this.platformCapabilities.platform
                    ? this.platformCapabilities.platform
                    : 'web',
                devicePixelRatio: Number.isFinite(Number(window.devicePixelRatio)) ? Number(window.devicePixelRatio) : 1,
                hitPadding: hitPadding,
                forwardingTolerance: this.platformCapabilities && typeof this.platformCapabilities.getSkipForwardingTolerance === 'function'
                    ? this.platformCapabilities.getSkipForwardingTolerance({
                        coordinateSpace: boundsSource,
                        hitPadding: hitPadding
                    })
                    : 6,
                pointerProfile: this.platformCapabilities && this.platformCapabilities.pointerProfile
                    ? this.platformCapabilities.pointerProfile
                    : 'pointer'
            };
        }

        beginTerminationVisualCleanup() {
            this.sceneRunId += 1;
            this.restoreDay1TakeoverAgentSwitches('termination_cleanup').catch((error) => {
                console.warn('[YuiGuide] 终止时恢复 Day1 Agent 开关失败:', error);
            });
            this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'termination_cleanup').catch(() => {});
            this.takeoverTopPeekHandle = null;
            this.stopGuideIdleSwayPerformance('termination_cleanup').catch(() => {});
            if (this.preTakeoverGhostCursorLookAtHandle) {
                this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    'termination_cleanup'
                ).catch(() => {});
            }
            this.stopPersistentGhostCursorLookAtPerformance('termination_cleanup').catch(() => {});
            this.resumeCurrentSceneAfterResistance();
            this.setCurrentScene(null, null);
            this.clearSceneTimers();
            this.disableInterrupts();
            this.cancelActiveNarration();
            this.clearUserCursorReveal(true);
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            this.awaitingIntroActivation = false;
            if (typeof this._introActivationResolve === 'function') {
                this._introActivationResolve();
                this._introActivationResolve = null;
            }
            if (this.wakeup && typeof this.wakeup.cancel === 'function') {
                this.wakeup.cancel('termination');
            }
            if (this.resistanceController && typeof this.resistanceController.destroy === 'function') {
                this.resistanceController.destroy();
            }
            if (this.interactionTakeover && typeof this.interactionTakeover.clearExternalizedChatFx === 'function') {
                this.interactionTakeover.clearExternalizedChatFx();
            }
            if (this.latestGuideChatMessageRetainTimer) {
                window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                this.latestGuideChatMessageRetainTimer = null;
            }
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.clearGuideChatStreamTimers();
            this.clearGuideChatMessages();
            this.clearQueuedGuideChatBridgeMessages();
            if (this.overlay && typeof this.overlay.setSpotlightSuppressed === 'function') {
                this.overlay.setSpotlightSuppressed(true);
            }
            this.clearIntroFlow();
            this.voiceQueue.stop();
            this.clearAllVirtualSpotlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            if (this.spotlightController && typeof this.spotlightController.destroy === 'function') {
                this.spotlightController.destroy();
            }
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-ui-suppressed');
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.performFullCleanup({
                destroyInteractionTakeover: true,
                destroyOverlay: true
            });
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            this.hideTemporaryAvatarFloatingGuideHud('termination-cleanup');
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 终止时关闭首页面板失败:', error);
            });
            this.closePluginDashboardWindowIfCreatedByGuide('终止');
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 终止时恢复主界面失败:', error);
                }
            }
        }

        async ensureChatVisible() {
            const chatContainer = document.getElementById('chat-container');
            const chatContentWrapper = document.getElementById('chat-content-wrapper');
            const chatHeader = document.getElementById('chat-header');
            const inputArea = document.getElementById('text-input-area');
            const reactChatOverlay = document.getElementById('react-chat-window-overlay');
            const reactChatHost = window.reactChatWindowHost;

            if (reactChatHost && typeof reactChatHost.ensureBundleLoaded === 'function') {
                try {
                    await reactChatHost.ensureBundleLoaded();
                } catch (error) {
                    console.warn('[YuiGuide] 预加载聊天窗失败:', error);
                }
            }

            if (reactChatHost && typeof reactChatHost.openWindow === 'function') {
                try {
                    reactChatHost.openWindow();
                } catch (error) {
                    console.warn('[YuiGuide] 打开聊天窗失败:', error);
                }
            }

            if (chatContainer) {
                chatContainer.classList.remove('minimized');
                chatContainer.classList.remove('mobile-collapsed');
            }
            if (chatContentWrapper) {
                chatContentWrapper.style.display = '';
            }
            if (chatHeader) {
                chatHeader.style.display = '';
            }
            if (inputArea) {
                inputArea.style.display = '';
                inputArea.classList.remove('hidden');
            }
            if (reactChatOverlay) {
                reactChatOverlay.hidden = false;
            }

            const inputTarget = await this.waitForElement(() => this.getChatInputTarget(), 5000);
            if (inputTarget) {
                return inputTarget;
            }

            return this.waitForElement(() => this.getChatWindowTarget(), 1200);
        }

        getGuideAssistantName() {
            const candidates = [
                window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__,
                window.lanlan_config && window.lanlan_config.lanlan_name,
                window._currentCatgirl,
                window.currentCatgirl
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const candidate = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                if (candidate) {
                    return candidate;
                }
            }

            return 'Neko';
        }

        getGuideAssistantAvatarUrl() {
            if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                const avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl();
                if (typeof avatarUrl === 'string' && avatarUrl.trim()) {
                    return avatarUrl.trim();
                }
            }

            const host = window.reactChatWindowHost;
            if (!host || typeof host.getState !== 'function') {
                return undefined;
            }

            try {
                const snapshot = host.getState();
                const messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
                for (let index = messages.length - 1; index >= 0; index -= 1) {
                    const message = messages[index];
                    if (!message || message.role !== 'assistant') {
                        continue;
                    }

                    const avatarUrl = typeof message.avatarUrl === 'string' ? message.avatarUrl.trim() : '';
                    if (avatarUrl) {
                        return avatarUrl;
                    }
                }
            } catch (error) {
                console.warn('[YuiGuide] 读取聊天头像失败:', error);
            }

            return undefined;
        }

        scrollChatToBottom(options) {
            const messageList = this.resolveElement('#react-chat-window-root .message-list');
            if (!messageList) {
                return;
            }

            const normalizedOptions = options || {};
            const useSmoothScroll = normalizedOptions.behavior === 'smooth';
            const scroll = () => {
                try {
                    if (useSmoothScroll) {
                        messageList.scrollTo({
                            top: messageList.scrollHeight,
                            behavior: 'smooth'
                        });
                    } else {
                        messageList.scrollTop = messageList.scrollHeight;
                    }
                } catch (_) {
                    messageList.scrollTop = messageList.scrollHeight;
                }
            };

            scroll();
            window.requestAnimationFrame(scroll);
            if (useSmoothScroll) {
                this.schedule(scroll, 160);
            }
        }

        cloneGuideChatMessageWithText(message, text, status) {
            const cloned = Object.assign({}, message || {});
            cloned.blocks = [{ type: 'text', text: text }];
            cloned.status = status;
            return cloned;
        }

        updateGuideChatMessage(messageId, patch) {
            if (!messageId || !patch || typeof patch !== 'object') {
                return null;
            }

            if (this.isHomeChatExternalized()) {
                this.postExternalChatGuideMessage({
                    action: 'yui_guide_update_chat_message',
                    messageId: messageId,
                    patch: patch,
                    timestamp: Date.now()
                });
                return null;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.updateMessage === 'function') {
                const updatedMessage = host.updateMessage(messageId, patch);
                this.scrollChatToBottom();
                return updatedMessage;
            }

            return null;
        }

        clearGuideChatMessages() {
            const now = Date.now();
            if (
                !this.destroyed
                && !this.terminationRequested
                && !this.angryExitTriggered
                && this.latestGuideChatMessageRetainId
                && this.latestGuideChatMessageRetainUntilMs > now
            ) {
                const retainedMessageId = this.latestGuideChatMessageRetainId;
                const delayMs = Math.max(0, this.latestGuideChatMessageRetainUntilMs - now);
                if (this.latestGuideChatMessageRetainTimer) {
                    window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                    this.latestGuideChatMessageRetainTimer = null;
                }
                this.latestGuideChatMessageRetainTimer = window.setTimeout(() => {
                    this.latestGuideChatMessageRetainTimer = null;
                    if (
                        !this.destroyed
                        && this.latestGuideChatMessageRetainId === retainedMessageId
                        && Date.now() >= this.latestGuideChatMessageRetainUntilMs
                    ) {
                        this.latestGuideChatMessageRetainId = '';
                        this.latestGuideChatMessageRetainUntilMs = 0;
                        this.clearGuideChatMessages();
                    }
                }, delayMs);
                return false;
            }

            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            if (this.isHomeChatExternalized()) {
                this.postExternalChatGuideMessage({
                    action: 'yui_guide_clear_chat_messages',
                    timestamp: Date.now()
                });
                return true;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.clearGuideMessages === 'function') {
                return !!host.clearGuideMessages();
            }

            return false;
        }

        resolveGuideChatStreamDurationMs(content, options) {
            const normalizedOptions = options || {};
            if (Number.isFinite(normalizedOptions.streamDurationMs)) {
                const explicitDurationMs = Math.round(normalizedOptions.streamDurationMs);
                return explicitDurationMs > 0 ? clamp(explicitDurationMs, 720, 24000) : 0;
            }

            const voiceDurationMs = this.getGuideVoiceDurationMs(
                normalizedOptions.voiceKey,
                resolveGuideLocale()
            );
            if (voiceDurationMs > 0) {
                return voiceDurationMs;
            }

            return estimateGuideChatStreamDurationMs(content);
        }

        streamGuideChatMessage(message, content, options) {
            const fullText = typeof content === 'string' ? content : '';
            const textUnits = Array.from(fullText);
            const total = textUnits.length;
            if (!message || !message.id || total <= 0) {
                return;
            }

            let index = Math.min(
                total,
                Math.max(0, Math.round((options && options.initialVisibleTextLength) || 0))
            );
            const durationMs = Math.max(0, Math.round(
                this.resolveGuideChatStreamDurationMs(fullText, options)
            ));
            if (durationMs <= 0) {
                this.updateGuideChatMessage(message.id, {
                    blocks: message.blocks,
                    actions: message.actions,
                    status: 'sent'
                });
                return;
            }

            let elapsedActiveMs = 0;
            let lastTickAt = Date.now();
            let waitingForResume = false;
            const pauseWithScene = !(options && options.streamPauseWithScene === false);
            const allowDuringAngryExit = !!(options && options.streamAllowDuringAngryExit);
            const tickMs = clamp(Math.round(durationMs / Math.max(total, 1)), 28, 90);
            const step = () => {
                if (
                    this.destroyed
                    || this.terminationRequested
                    || (this.angryExitTriggered && !allowDuringAngryExit)
                ) {
                    return;
                }

                if (pauseWithScene && this.scenePausedForResistance) {
                    if (!waitingForResume) {
                        const pauseStartedAt = Number.isFinite(this.scenePausedAt) && this.scenePausedAt > 0
                            ? this.scenePausedAt
                            : Date.now();
                        elapsedActiveMs += Math.max(0, pauseStartedAt - lastTickAt);
                        waitingForResume = true;
                        this.waitUntilSceneResumed().then(() => {
                            waitingForResume = false;
                            lastTickAt = Date.now();
                            if (
                                !this.destroyed
                                && !this.terminationRequested
                                && (!this.angryExitTriggered || allowDuringAngryExit)
                            ) {
                                this.scheduleGuideChatStream(step, Math.min(80, tickMs));
                            }
                        });
                    }
                    return;
                }

                const now = Date.now();
                elapsedActiveMs += Math.max(0, now - lastTickAt);
                lastTickAt = now;
                if (elapsedActiveMs >= durationMs) {
                    this.updateGuideChatMessage(message.id, {
                        blocks: message.blocks,
                        actions: message.actions,
                        status: 'sent'
                    });
                    return;
                }

                const progress = clamp(elapsedActiveMs / durationMs, 0, 1);
                const nextIndex = Math.max(index, Math.min(total, Math.ceil(progress * total)));
                if (nextIndex > index) {
                    index = nextIndex;
                    this.updateGuideChatMessage(message.id, {
                        blocks: [{
                            type: 'text',
                            text: textUnits.slice(0, index).join('')
                        }],
                        actions: undefined,
                        status: 'streaming'
                    });
                }

                this.scheduleGuideChatStream(step, Math.min(tickMs, durationMs - elapsedActiveMs));
            };

            this.scheduleGuideChatStream(step, Math.min(80, tickMs));
        }

        appendGuideChatMessage(text, options) {
            const normalizedOptions = options || {};
            const content = formatGuideDebugText(
                normalizedOptions.textKey || '',
                typeof text === 'string' ? text.trim() : ''
            );
            if (!content) {
                return null;
            }

            const createdAt = Date.now();
            let time = '';

            try {
                time = new Date(createdAt).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit'
                });
            } catch (_) {}

            const message = {
                id: 'yui-guide-' + createdAt + '-' + Math.random().toString(36).slice(2, 8),
                role: 'assistant',
                author: this.getGuideAssistantName(),
                time: time,
                createdAt: createdAt,
                avatarUrl: this.getGuideAssistantAvatarUrl(),
                blocks: [{
                    type: 'text',
                    text: content
                }],
                status: 'sent'
            };

            if (Array.isArray(normalizedOptions.buttons) && normalizedOptions.buttons.length > 0) {
                message.blocks.push({
                    type: 'buttons',
                    buttons: normalizedOptions.buttons.map(function (button) {
                        if (!button || typeof button !== 'object') {
                            return null;
                        }

                        return {
                            id: button.id,
                            label: button.label,
                            action: button.action,
                            variant: button.variant,
                            disabled: !!button.disabled,
                            payload: button.payload || undefined
                        };
                    }).filter(Boolean)
                });
            }

            if (Array.isArray(normalizedOptions.actions) && normalizedOptions.actions.length > 0) {
                message.actions = normalizedOptions.actions.map(function (action) {
                    if (!action || typeof action !== 'object') {
                        return null;
                    }

                    return {
                        id: action.id,
                        label: action.label,
                        action: action.action,
                        variant: action.variant,
                        disabled: !!action.disabled,
                        payload: action.payload || undefined
                    };
                }).filter(Boolean);
            }

            const initialVisibleText = Array.from(content).slice(0, 1).join('');
            const streamOptions = Object.assign({}, normalizedOptions, {
                initialVisibleTextLength: Array.from(initialVisibleText).length
            });
            const streamingMessage = this.cloneGuideChatMessageWithText(message, initialVisibleText, 'streaming');
            streamingMessage.actions = undefined;
            const retainDurationMs = this.getGuideVoiceDurationMs(
                normalizedOptions.voiceKey || '',
                resolveGuideLocale()
            );
            if (retainDurationMs > 0) {
                this.latestGuideChatMessageRetainId = message.id;
                this.latestGuideChatMessageRetainUntilMs = createdAt + retainDurationMs;
            } else {
                this.latestGuideChatMessageRetainId = '';
                this.latestGuideChatMessageRetainUntilMs = 0;
            }

            // Electron Pet 模式下首页聊天被拆到独立 /chat 窗口，这里优先通过
            // BroadcastChannel 把教程消息转发过去；只有转发失败时才回落到 overlay。
            if (this.isHomeChatExternalized()) {
                if (this.postExternalChatGuideMessage({
                    action: 'yui_guide_append_chat_message',
                    message: streamingMessage,
                    timestamp: createdAt
                })) {
                    this.streamGuideChatMessage(message, content, streamOptions);
                    return message;
                }

                try {
                    this.showGuideBubble(content, {
                        title: this.getGuideAssistantName(),
                        emotion: 'neutral'
                    }, this.currentSceneId);
                } catch (error) {
                    console.warn('[YuiGuide] 兜底气泡展示失败:', error);
                }
                return null;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.appendMessage === 'function') {
                const appendedMessage = host.appendMessage(streamingMessage);
                this.scrollChatToBottom();
                this.streamGuideChatMessage(message, content, streamOptions);
                return appendedMessage;
            }

            if (typeof window.appendMessage === 'function') {
                window.appendMessage(content, 'gemini', true);
                this.scrollChatToBottom();
            }

            return null;
        }

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatInputTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.clearHomeSpotlightsForExternalizedChat();
                    this.interactionTakeover.setExternalizedChatSpotlight('input');
                }
                return;
            }

            if (!target) {
                return;
            }

            if (target && typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({
                    behavior: 'auto',
                    block: 'center',
                    inline: 'nearest'
                });
            }

            if (target) {
                this.setSpotlightGeometryHint(target, {
                    padding: DEFAULT_SPOTLIGHT_PADDING + 3
                });
                this.overlay.setPersistentSpotlight(target);
            }

            if (inputBox && typeof inputBox.focus === 'function') {
                this.schedule(() => {
                    try {
                        inputBox.focus({ preventScroll: true });
                    } catch (_) {
                        inputBox.focus();
                    }
                }, 180);
            }
        }

        async playIntroGreetingReply() {
            const greetingReplyText = this.resolveGuideCopy(
                INTRO_GREETING_REPLY_TEXT_KEY,
                INTRO_GREETING_REPLY_TEXT
            );
            if (!greetingReplyText) {
                return;
            }

            this.appendGuideChatMessage(greetingReplyText, {
                textKey: INTRO_GREETING_REPLY_TEXT_KEY,
                voiceKey: 'intro_greeting_reply'
            });
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatCursor === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCursor('capsule-input', {
                    effect: '',
                    durationMs: 0
                });
            }
            await Promise.all([
                this.speakGuideLine(greetingReplyText, {
                    voiceKey: 'intro_greeting_reply'
                }),
                this.runIntroGreetingHugPerformance().catch(() => {}),
                this.runIntroGiftHeartPerformance().catch(() => {})
            ]);
            this.clearIntroGreetingChatHighlight();
        }

        async runIntroGreetingHugPerformance() {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playIntroGreetingHug !== 'function') {
                return null;
            }
            return api.playIntroGreetingHug({
                approachMs: 2200,
                settleMs: 1250,
                reducedMotion: this.shouldReduceTutorialMotion(),
                isCancelled: () => this.isStopping()
            });
        }

        async runIntroGiftHeartPerformance() {
            if (!(await this.waitForNarrationCue(
                'intro_greeting_reply',
                'showIntroGiftHeart'
            ))) {
                return null;
            }
            if (this.isStopping()) {
                return null;
            }

            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playIntroGiftHeart !== 'function') {
                return null;
            }
            return api.playIntroGiftHeart({
                durationMs: 2600,
                releaseMs: 420,
                reducedMotion: this.shouldReduceTutorialMotion(),
                isCancelled: () => this.isStopping()
            });
        }

        async runReturnControlCueWavePerformance() {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playReturnControlCueWave !== 'function') {
                return null;
            }
            return api.playReturnControlCueWave({
                durationMs: 4200,
                reducedMotion: this.shouldReduceTutorialMotion(),
                isCancelled: () => this.isStopping()
            });
        }

        async startIntroVoiceCursorLookAtPerformance() {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startIntroVoiceCursorLookAt !== 'function') {
                return null;
            }
            try {
                return await api.startIntroVoiceCursorLookAt({
                    getPoint: () => this.overlay && typeof this.overlay.getCursorPosition === 'function'
                        ? this.overlay.getCursorPosition()
                        : null,
                    isCancelled: () => this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] 语音入口目光跟随动作启动失败:', error);
                return null;
            }
        }

        async startGhostCursorLookAtPerformance(options) {
            const normalizedOptions = options || {};
            if (normalizedOptions.preferExistingHandle !== false) {
                const existingHandle = this.persistentGhostCursorLookAtHandle || this.preTakeoverGhostCursorLookAtHandle;
                if (existingHandle && typeof existingHandle.stop === 'function') {
                    return existingHandle;
                }
            }
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startIntroVoiceCursorLookAt !== 'function') {
                return null;
            }
            const cancelCheck = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : () => this.isStopping();
            try {
                return await api.startIntroVoiceCursorLookAt({
                    getPoint: () => this.overlay && typeof this.overlay.getCursorPosition === 'function'
                        ? this.overlay.getCursorPosition()
                        : null,
                    isCancelled: cancelCheck
                });
            } catch (error) {
                console.warn('[YuiGuide] Ghost cursor 目光跟随动作启动失败:', error);
                return null;
            }
        }

        async ensurePreTakeoverGhostCursorLookAtPerformance(options) {
            const existingHandle = this.preTakeoverGhostCursorLookAtHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const createdHandle = await this.startGhostCursorLookAtPerformance(options || {});
            if (createdHandle && typeof createdHandle.stop === 'function') {
                this.preTakeoverGhostCursorLookAtHandle = createdHandle;
            }
            return this.preTakeoverGhostCursorLookAtHandle;
        }

        async ensurePersistentGhostCursorLookAtPerformance(options) {
            const existingHandle = this.persistentGhostCursorLookAtHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const createdHandle = await this.startGhostCursorLookAtPerformance(options || {});
            if (createdHandle && typeof createdHandle.stop === 'function') {
                this.persistentGhostCursorLookAtHandle = createdHandle;
            }
            return this.persistentGhostCursorLookAtHandle;
        }

        async stopIntroVoiceCursorLookAtPerformance(handle, reason) {
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            if (this.preTakeoverGhostCursorLookAtHandle === handle) {
                this.preTakeoverGhostCursorLookAtHandle = null;
            }
            if (this.persistentGhostCursorLookAtHandle === handle) {
                this.persistentGhostCursorLookAtHandle = null;
            }
            try {
                await handle.stop(reason || 'intro_voice_showcase_complete');
            } catch (_) {}
        }

        adoptPreTakeoverGhostCursorLookAtHandle() {
            if (
                !this.persistentGhostCursorLookAtHandle
                && this.preTakeoverGhostCursorLookAtHandle
                && typeof this.preTakeoverGhostCursorLookAtHandle.stop === 'function'
            ) {
                this.persistentGhostCursorLookAtHandle = this.preTakeoverGhostCursorLookAtHandle;
            }
            this.preTakeoverGhostCursorLookAtHandle = null;
        }

        async stopPersistentGhostCursorLookAtPerformance(reason) {
            const handle = this.persistentGhostCursorLookAtHandle;
            this.persistentGhostCursorLookAtHandle = null;
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'ghost_cursor_look_at_complete');
            } catch (_) {}
        }

        async ensureGuideIdleSwayPerformance() {
            const existingHandle = this.guideIdleSwayHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startGuideIdleSway !== 'function') {
                return null;
            }
            try {
                const handle = await api.startGuideIdleSway({
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
                if (handle && typeof handle.stop === 'function') {
                    this.guideIdleSwayHandle = handle;
                }
                return this.guideIdleSwayHandle;
            } catch (error) {
                console.warn('[YuiGuide] 教程常驻轻微晃动启动失败:', error);
                return null;
            }
        }

        async stopGuideIdleSwayPerformance(reason) {
            const handle = this.guideIdleSwayHandle;
            this.guideIdleSwayHandle = null;
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'guide_idle_sway_complete');
            } catch (_) {}
        }

        async startPluginDashboardCornerPeekPerformance(runId, options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startPluginDashboardCornerPeek !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            try {
                return await api.startPluginDashboardCornerPeek({
                    targetPreset: normalizedOptions.targetPreset,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => runId !== this.sceneRunId || this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] 插件面板角落动作启动失败:', error);
                return null;
            }
        }

        async runSettingsPeekPanicPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playSettingsPeekPanic !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            try {
                return await api.playSettingsPeekPanic({
                    targetRect: normalizedOptions.targetRect || null,
                    totalDurationMs: normalizedOptions.totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => (
                        (Number.isFinite(normalizedOptions.runId) && normalizedOptions.runId !== this.sceneRunId)
                        || this.isStopping()
                    )
                });
            } catch (error) {
                console.warn('[YuiGuide] 设置一瞥慌乱动作启动失败:', error);
                return null;
            }
        }

        async runInterruptResistPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playInterruptResist !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            const voiceDurationMs = normalizedOptions.voiceKey
                ? this.getGuideVoiceDurationMs(normalizedOptions.voiceKey, resolveGuideLocale())
                : 0;
            const totalDurationMs = Number.isFinite(normalizedOptions.totalDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.totalDurationMs))
                : (voiceDurationMs > 0 ? clamp(Math.round(voiceDurationMs), 960, 7600) : undefined);
            try {
                return await api.playInterruptResist({
                    pointerX: normalizedOptions.x,
                    pointerY: normalizedOptions.y,
                    totalDurationMs: totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] 轻微打断动作启动失败:', error);
                return null;
            }
        }

        applyAngryExitEmotionFallback() {
            this.applyGuideEmotion('angry', {
                allowDuringInterrupt: true
            });
        }

        async runAngryExitPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playAngryExit !== 'function') {
                this.applyAngryExitEmotionFallback();
                return null;
            }
            const normalizedOptions = options || {};
            const voiceDurationMs = normalizedOptions.voiceKey
                ? this.getGuideVoiceDurationMs(normalizedOptions.voiceKey, resolveGuideLocale())
                : 0;
            const totalDurationMs = Number.isFinite(normalizedOptions.totalDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.totalDurationMs))
                : (voiceDurationMs > 0 ? clamp(Math.round(voiceDurationMs), 1200, 16000) : undefined);
            try {
                const result = await api.playAngryExit({
                    pointerX: normalizedOptions.x,
                    pointerY: normalizedOptions.y,
                    totalDurationMs: totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
                if (result && result.result !== 'played') {
                    this.applyAngryExitEmotionFallback();
                }
                return result;
            } catch (error) {
                console.warn('[YuiGuide] 生气退出动作启动失败:', error);
                this.applyAngryExitEmotionFallback();
                return null;
            }
        }

        async stopPluginDashboardCornerPeekPerformance(handle, reason) {
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'plugin_dashboard_closed');
            } catch (_) {}
        }

        async runWakeupPrelude() {
            if (this.page !== 'home' || this.isStopping() || !this.wakeup || typeof this.wakeup.run !== 'function') {
                if (typeof document !== 'undefined' && document.body) {
                    document.body.classList.remove('yui-guide-live2d-preparing');
                }
                await this.ensureGuideIdleSwayPerformance();
                return;
            }

            if (this.interactionTakeover && typeof this.interactionTakeover.applyFaceForwardLock === 'function') {
                this.interactionTakeover.applyFaceForwardLock();
            }
            try {
                const result = await this.wakeup.run();
                this.recordExperienceMetric('wakeup_result', {
                    result: result && result.result ? result.result : '',
                    reason: result && result.reason ? result.reason : ''
                });
            } catch (error) {
                console.warn('[YuiGuide] 入场苏醒播放失败，继续教程:', error);
                this.recordExperienceMetric('wakeup_result', {
                    result: 'fallback',
                    reason: 'exception'
                });
            }
            await this.ensureGuideIdleSwayPerformance();
        }

        // Electron Pet 模式专用 prelude：聊天输入框不在首页窗口里，
        // 因此跳过首页点击激活，但后续旁白与高亮演示照常执行。
        onPointerMove(event) {
            this.handleInterrupt(event);
        }

        onPointerDown(event) {
            this.resistanceController.recordPointerDown(event);
        }

        handleInterrupt(event) {
            return this.resistanceController.handleInterrupt(event);
        }

        noteUserCursorRevealAttempt(distance, now) {
            if (
                this.userCursorRevealed
                || !Number.isFinite(distance)
                || distance < DEFAULT_USER_CURSOR_REVEAL_DISTANCE
                || !document.body.classList.contains('yui-taking-over')
            ) {
                return;
            }

            if (now - this.lastUserCursorRevealMoveAt < DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS) {
                return;
            }

            this.lastUserCursorRevealMoveAt = now;
            this.userCursorRevealMoveCount += 1;
            if (this.userCursorRevealMoveCount >= DEFAULT_USER_CURSOR_REVEAL_MOVES) {
                this.revealUserCursor();
            }
        }

        revealUserCursor() {
            if (this.destroyed || !document.body) {
                return;
            }

            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }

            this.userCursorRevealed = true;
            this.restoreHiddenCursorAfterResistance = false;
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.documentElement.classList.add('yui-user-cursor-revealed');
            document.documentElement.classList.add('yui-resistance-cursor-reveal');
            document.body.classList.add('yui-user-cursor-revealed');
            document.body.classList.add('yui-resistance-cursor-reveal');
        }

        clearUserCursorReveal(resetCursor) {
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }

            this.userCursorRevealed = false;
            this.userCursorRevealMoveCount = 0;
            this.lastUserCursorRevealMoveAt = 0;
            this.restoreHiddenCursorAfterResistance = false;

            if (document.body) {
                document.documentElement.classList.remove('yui-user-cursor-revealed');
                document.documentElement.classList.remove('yui-resistance-cursor-reveal');
                document.body.classList.remove('yui-user-cursor-revealed');
                document.body.classList.remove('yui-resistance-cursor-reveal');
            }

            if (resetCursor) {
                document.documentElement.style.cursor = '';
                if (document.body) {
                    document.body.style.cursor = '';
                }
            }
        }

        prepareResistanceCursorReveal() {
            if (this.userCursorRevealed) {
                this.revealUserCursor();
                return;
            }

            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
            }
            this.restoreHiddenCursorAfterResistance = false;
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.body.classList.add('yui-resistance-cursor-reveal');
            this.resistanceCursorTimer = window.setTimeout(() => {
                this.resistanceCursorTimer = null;
                document.body.classList.remove('yui-resistance-cursor-reveal');
                this.restoreHiddenCursorAfterResistance = false;
            }, 3000);
        }

        playLightResistance(x, y, options) {
            return this.resistanceController.playLightResistance(x, y, options);
        }

        async abortAsAngryExit(source) {
            return this.resistanceController.abortAsAngryExit(source);
        }

        waitForAngryExitPresentationCompletion() {
            const promise = this.angryExitPresentationPromise;
            if (promise && typeof promise.then === 'function') {
                return promise.catch(() => {});
            }
            return Promise.resolve();
        }

        requestTermination(reason, tutorialReason) {
            return this.terminationRouter.requestTermination(reason, tutorialReason);
        }

        skip(reason, tutorialReason) {
            return this.terminationRouter.skip(reason, tutorialReason);
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.terminationRequested = true;
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            this.restoreDay1TakeoverAgentSwitches('destroy').catch((error) => {
                console.warn('[YuiGuide] 销毁时恢复 Day1 Agent 开关失败:', error);
            });
            this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'destroy').catch(() => {});
            this.takeoverTopPeekHandle = null;
            this.stopGuideIdleSwayPerformance('destroy').catch(() => {});
            if (this.preTakeoverGhostCursorLookAtHandle) {
                this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    'destroy'
                ).catch(() => {});
            }
            this.stopPersistentGhostCursorLookAtPerformance('destroy').catch(() => {});
            if (this.interactionTakeover && typeof this.interactionTakeover.releaseFaceForwardLock === 'function') {
                this.interactionTakeover.releaseFaceForwardLock();
            }
            this.resumeCurrentSceneAfterResistance();
            if (this.interactionTakeover && typeof this.interactionTakeover.clearExternalizedChatFx === 'function') {
                this.interactionTakeover.clearExternalizedChatFx();
            }
            if (this.latestGuideChatMessageRetainTimer) {
                window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                this.latestGuideChatMessageRetainTimer = null;
            }
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.clearGuideChatStreamTimers();
            this.clearGuideChatMessages();
            this.clearQueuedGuideChatBridgeMessages();
            if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatButtonsDisabled === 'function') {
                this.interactionTakeover.setExternalizedChatButtonsDisabled(false);
            }
            this.setGuideChatInputLocked(false, 'avatar-floating-guide-destroy');
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-ui-suppressed');
            }
            this.clearUserCursorReveal(true);
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.resolve === 'function') {
                this.pluginDashboardHandoff.resolve(false);
            }
            this.cancelActiveNarration();
	            this.clearIntroFlow();
	            this.clearSceneTimers();
	            this.clearGuideChatStreamTimers();
            this.clearAvatarStandIn({ clearPending: true, restoreModel: true });
            this.clearPendingGuideMessageAction();
            this.uninstallGuideMessageActionHandler();
            if (this.wakeup && typeof this.wakeup.destroy === 'function') {
                this.wakeup.destroy();
            }
            if (this.resistanceController && typeof this.resistanceController.destroy === 'function') {
                this.resistanceController.destroy();
            }
            if (this.overlay && typeof this.overlay.setSpotlightSuppressed === 'function') {
                this.overlay.setSpotlightSuppressed(true);
            }
            this.disableInterrupts();
            if (this.voiceQueue && typeof this.voiceQueue.destroy === 'function') {
                this.voiceQueue.destroy();
            } else {
                this.voiceQueue.stop();
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.clearAllVirtualSpotlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            if (this.spotlightController && typeof this.spotlightController.destroy === 'function') {
                this.spotlightController.destroy();
            }
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            this.clearGuidePresentation();
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            this.hideTemporaryAvatarFloatingGuideHud('destroy');
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 销毁时关闭首页面板失败:', error);
            });
            this.notifyPluginDashboardTerminationRequested(this.lastTutorialEndReason || 'destroy');
            this.closePluginDashboardWindowIfCreatedByGuide('销毁');
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 销毁时恢复主界面失败:', error);
                }
            }
            this.performFullCleanup({
                destroyInteractionTakeover: true,
                destroyOverlay: true
            });
            window.removeEventListener('keydown', this.keydownHandler, true);
            window.removeEventListener('pagehide', this.pageHideHandler, true);
            window.removeEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.removeEventListener('neko:yui-guide:external-chat-cursor-anchor', this.externalChatCursorAnchorHandler, true);
            window.removeEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.removeEventListener(DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT, this.desktopPluginDashboardSkipHandler, true);
            window.removeEventListener('neko:yui-guide:desktop-interrupt-request', this.desktopPluginDashboardInterruptHandler, true);
            window.removeEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.removeEventListener('message', this.messageHandler, true);
        }

        onKeyDown(event) {
            if (this.destroyed || !event || event.key !== 'Escape') {
                return;
            }

            if (this.hasOpenSystemDialog()) {
                return;
            }

            event.stopPropagation();
            this.skip('escape', 'skip');
        }

        onPageHide() {
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialEnd === 'function') {
                try {
                    Promise.resolve(this.tutorialManager.requestTutorialEnd('pagehide')).catch((error) => {
                        console.warn('[YuiGuide] pagehide tutorial end failed, falling back to destroy:', error);
                        this.destroy();
                    });
                } catch (error) {
                    console.warn('[YuiGuide] pagehide tutorial end threw, falling back to destroy:', error);
                    this.destroy();
                }
                return;
            }
            this.destroy();
        }

        get mobileTouchInteractionPassthrough() {
            return this.shouldUseMobileTouchInteractionPassthrough();
        }

        shouldUseMobileTouchInteractionPassthrough() {
            const coarsePointer = !!(
                window.matchMedia
                && window.matchMedia('(hover: none), (pointer: coarse)').matches
            );
            const narrowViewport = Math.max(
                window.innerWidth || 0,
                document.documentElement ? document.documentElement.clientWidth || 0 : 0
            ) <= 768;
            const touchCapable = !!(
                'ontouchstart' in window
                || (navigator && Number(navigator.maxTouchPoints || 0) > 0)
            );

            // 移动触控端没有幽灵鼠标接管语义，不能用全局捕获守卫吞掉页面点击。
            return !!((coarsePointer || touchCapable) && narrowViewport);
        }

        isAllowedTutorialInteractionTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            if (target.closest('#neko-tutorial-skip-btn')) {
                return true;
            }

            if (this.isGuideMessageActionTarget(target)) {
                return true;
            }

            if (this.awaitingIntroActivation) {
                if (this.isIntroActivationTarget(target)) {
                    this.finishIntroActivation();
                    return true;
                }
            }

            if (this.manualPluginDashboardOpenAllowed && this.manualPluginDashboardOpenTarget) {
                const manualTarget = this.manualPluginDashboardOpenTarget;
                if (
                    target === manualTarget
                    || (manualTarget.contains && manualTarget.contains(target))
                    || (
                        target.closest
                        && target.closest('#neko-sidepanel-action-agent-user-plugin-management-panel') === manualTarget
                    )
                ) {
                    this.manualPluginDashboardOpenUserClicked = true;
                    return true;
                }
            }

            return false;
        }

        isSystemDialogInteractionTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            return !!target.closest([
                '#prominent-notice-overlay',
                '.modal-overlay',
                '.modal-dialog',
                '.storage-location-completion-card',
                '#storage-location-overlay',
                '.storage-location-modal'
            ].join(', '));
        }

        hasOpenSystemDialog() {
            return !!document.querySelector([
                '#prominent-notice-overlay',
                '.modal-overlay',
                '.storage-location-completion-card:not([hidden])',
                '#storage-location-overlay:not([hidden])'
            ].join(', '));
        }

        onTutorialEndEvent(event) {
            const detail = event && event.detail ? event.detail : null;
            if (!detail || detail.page !== this.page) {
                return;
            }

            this.lastTutorialEndReason = detail.reason || null;
            this.destroy();
        }

        onRemoteTerminationRequest(event) {
            if (this.destroyed) {
                return;
            }

            const detail = event && event.detail ? event.detail : null;
            if (!detail) {
                return;
            }

            const targetPage = typeof detail.targetPage === 'string' ? detail.targetPage.trim() : '';
            if (targetPage && targetPage !== this.page) {
                return;
            }

            this.requestTermination(detail.reason || 'skip', detail.tutorialReason || 'skip');
        }

        async handlePluginDashboardInterruptRequest(event, handoff, data) {
            const requestId = typeof data.requestId === 'string' ? data.requestId : '';
            if (!requestId) {
                return;
            }

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            const targetOrigin = handoff && handoff.targetOrigin
                ? handoff.targetOrigin
                : this.getPluginDashboardExpectedOrigin();
            const postAck = () => {
                const ackPayload = {
                    type: PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
                    sessionId: typeof data.sessionId === 'string' ? data.sessionId : '',
                    requestId: requestId
                };
                this.dispatchDesktopPluginDashboardInterruptAck(ackPayload);

                if (!windowRef || windowRef.closed) {
                    return;
                }

                try {
                    windowRef.postMessage(ackPayload, targetOrigin);
                } catch (error) {
                    console.warn('[YuiGuide] 向插件面板发送 interrupt ack 失败:', error);
                }
            };

            if (this.pluginDashboardLastInterruptRequestId === requestId) {
                postAck();
                return;
            }
            this.pluginDashboardLastInterruptRequestId = requestId;

            const detail = data.detail && typeof data.detail === 'object' ? data.detail : {};
            const kind = typeof detail.kind === 'string' ? detail.kind : '';
            const text = typeof detail.text === 'string' ? detail.text : '';
            const textKey = typeof detail.textKey === 'string' ? detail.textKey : '';
            const voiceKey = typeof detail.voiceKey === 'string' ? detail.voiceKey : '';
            const resolvedText = this.resolveGuideCopy(textKey, text);
            const interruptCount = Number.isFinite(detail.interruptCount) ? Math.max(0, Math.floor(detail.interruptCount)) : null;
            const x = Number.isFinite(detail.x) ? detail.x : null;
            const y = Number.isFinite(detail.y) ? detail.y : null;

            if (interruptCount !== null) {
                this.interruptCount = Math.max(
                    Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0)),
                    interruptCount
                );
            }

            if (kind === 'interrupt_angry_exit') {
                await this.abortAsAngryExit('pointer_interrupt');
                postAck();
                return;
            }

            if (kind === 'interrupt_resist_light' && x !== null && y !== null) {
                try {
                    await this.playLightResistance(x, y, {
                        suppressCursorReveal: true
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 执行插件面板轻微抵抗失败:', error);
                }
                postAck();
                return;
            }

            if (resolvedText) {
                this.appendGuideChatMessage(resolvedText, {
                    textKey: textKey,
                    voiceKey: voiceKey
                });
            }

            if (resolvedText) {
                try {
                    await this.speakGuideLine(resolvedText, {
                        voiceKey: voiceKey
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 播放插件面板打断语音失败:', error);
                }
            }

            postAck();
        }

        handleDesktopYuiGuideSkipRequest(event) {
            if (this.destroyed) {
                return;
            }

            const payload = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
            if (sessionId && handoff.sessionId && sessionId !== handoff.sessionId) {
                return;
            }

            const detail = payload.detail && typeof payload.detail === 'object'
                ? Object.assign({}, payload.detail)
                : {};
            if (!detail.source && typeof payload.source === 'string') {
                detail.source = payload.source;
            }

            void this.handlePluginDashboardSkipRequest({
                type: PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
                sessionId: sessionId,
                detail: detail
            });
        }

        onDesktopPluginDashboardInterruptRequest(event) {
            if (this.destroyed) {
                return;
            }

            const payload = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            const requestId = typeof payload.requestId === 'string'
                ? payload.requestId
                : (payload.detail && typeof payload.detail.requestId === 'string' ? payload.detail.requestId : '');
            if (!requestId) {
                return;
            }

            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
            if (sessionId && handoff.sessionId && sessionId !== handoff.sessionId) {
                return;
            }

            const detail = payload.detail && typeof payload.detail === 'object'
                ? Object.assign({}, payload.detail)
                : {};
            delete detail.requestId;
            void this.handlePluginDashboardInterruptRequest(null, handoff, {
                type: PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT,
                sessionId: sessionId,
                requestId: requestId,
                detail: detail
            });
        }

        isPointInsideScreenRect(point, rect) {
            if (!point || !rect) {
                return false;
            }

            const screenX = Number(point.screenX);
            const screenY = Number(point.screenY);
            if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                return false;
            }

            return (
                screenX >= Number(rect.left)
                && screenX <= Number(rect.right)
                && screenY >= Number(rect.top)
                && screenY <= Number(rect.bottom)
            );
        }

        async forwardPluginDashboardSkipRequestToButton(detail) {
            const skipButton = document.getElementById('neko-tutorial-skip-btn');
            if (!skipButton || typeof skipButton.click !== 'function') {
                return 'unavailable';
            }

            if (!detail || typeof detail !== 'object') {
                return 'rejected';
            }

            const point = {
                screenX: Number(detail.screenX),
                screenY: Number(detail.screenY)
            };
            if (!Number.isFinite(point.screenX) || !Number.isFinite(point.screenY)) {
                return 'rejected';
            }

            const currentRect = await this.getSkipButtonScreenRect();
            if (!currentRect || !this.isPointInsideScreenRect(point, currentRect)) {
                return 'rejected';
            }

            skipButton.click();
            return 'forwarded';
        }

        isPluginDashboardDirectSkipRequest(data) {
            const detail = data && data.detail && typeof data.detail === 'object'
                ? data.detail
                : {};
            const source = typeof detail.source === 'string'
                ? detail.source
                : (data && typeof data.source === 'string' ? data.source : '');
            return source === 'plugin_dashboard_button' || source === 'plugin_dashboard_angry_exit';
        }

        async handlePluginDashboardSkipRequest(data) {
            return this.terminationRouter.handlePluginDashboardSkipRequest(data);
        }

        onWindowMessage(event) {
            const data = event && event.data ? event.data : null;
            if (!data || typeof data !== 'object') {
                return;
            }

            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.windowRef || event.source !== handoff.windowRef) {
                return;
            }
            const expectedOrigin = handoff.targetOrigin || this.getPluginDashboardExpectedOrigin();
            if (expectedOrigin && event.origin !== expectedOrigin) {
                if (!handoff.ready && this.isTrustedPluginDashboardOrigin(event.origin)) {
                    handoff.targetOrigin = event.origin;
                } else {
                    return;
                }
            }

            if (data.type === PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT) {
                void this.handlePluginDashboardInterruptRequest(event, handoff, data);
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT) {
                if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                    return;
                }
                void this.handlePluginDashboardSkipRequest(data);
                return;
            }

            if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_READY_EVENT) {
                handoff.ready = true;
                handoff.readyAt = Date.now();
                if (this.isTrustedPluginDashboardOrigin(event.origin)) {
                    handoff.targetOrigin = event.origin;
                }
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_DONE_EVENT) {
                handoff.resolve(true);
            }
        }
    }

    window.createYuiGuideDirector = function createYuiGuideDirector(options) {
        return new YuiGuideDirector(options);
    };
})();
