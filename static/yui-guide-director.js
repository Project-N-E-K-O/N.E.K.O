(function () {
    'use strict';

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
        if (current.indexOf('ko') === 0) return 'ko';
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
        if (locale === 'ko') return 'ko-KR';
        if (locale === 'ru') return 'ru-RU';
        return 'zh-CN';
    }

    const DEFAULT_INTERRUPT_DISTANCE = 32;
    const DEFAULT_INTERRUPT_SPEED_THRESHOLD = 1.8;
    const DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD = 0.09;
    const DEFAULT_INTERRUPT_ACCELERATION_STREAK = 3;
    const DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10;
    const DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS = 140;
    const DEFAULT_USER_CURSOR_REVEAL_DISTANCE = 14;
    const DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS = 160;
    const DEFAULT_USER_CURSOR_REVEAL_MOVES = 2;
    const DEFAULT_STEP_DELAY_MS = 120;
    const DEFAULT_SCENE_SETTLE_MS = 260;
    const DEFAULT_CURSOR_DURATION_MS = 520;
    const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420;
    const INTRO_GREETING_REPLY_TEXT = '欢迎回家，喵~ 外面的世界很辛苦吧？在这个专属我们的小窝里，你可以放下所有的烦恼哦。我是林悠怡，接下来的熟悉过程请放心交给我，我会一步步牵着您的手慢慢来的。';
    const INTRO_GREETING_REPLY_TEXT_KEY = 'tutorial.yuiGuide.lines.introGreetingReply';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT = '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard';
    const PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT = '浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。';
    const PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY = 'tutorial.yuiGuide.lines.pluginDashboardPopupBlocked';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1 = '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2 = '等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！';
    const TAKEOVER_SETTINGS_DETAIL_TEXT = TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1 + TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2;
    const TAKEOVER_SETTINGS_DETAIL_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetail';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2';
    const DEFAULT_SPOTLIGHT_PADDING = 6;
    const PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X = 15;
    const PLUGIN_DASHBOARD_WINDOW_NAME = 'plugin_dashboard';
    const PLUGIN_DASHBOARD_HANDOFF_EVENT = 'neko:yui-guide:plugin-dashboard:start';
    const PLUGIN_DASHBOARD_READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready';
    const PLUGIN_DASHBOARD_DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done';
    const PLUGIN_DASHBOARD_TERMINATE_EVENT = 'neko:yui-guide:plugin-dashboard:terminate';
    const PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT = 'neko:yui-guide:plugin-dashboard:narration-finished';
    const PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request';
    const PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack';
    const PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request';
    const DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME = 'ATLS';
    const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/';
    const GUIDE_AUDIO_FILE_NAMES = Object.freeze({
        intro_basic: '这里有一个神奇的按钮.mp3',
        intro_greeting_reply: '欢迎回家，喵~ 外面.mp3',
        takeover_capture_cursor: '超级魔法按钮出现！只.mp3',
        takeover_plugin_preview_home: '还没完呢！你快看快看.mp3',
        takeover_plugin_preview_dashboard: '有了它们，我不光能看.mp3',
        takeover_settings_peek_intro: '当然啦，如果你想让本.mp3',
        takeover_settings_peek_detail: '你看，这里可以穿我的.mp3',
        interrupt_resist_light_1: '喂！不要拽我啦，还没.mp3',
        interrupt_resist_light_3: '等一下啦！还没结束呢.mp3',
        interrupt_angry_exit: '人类~~~~！你真的.mp3',
        takeover_return_control: '好啦好啦，不霸占你的.mp3'
    });
    const INTRO_ACTIVATION_HINT_KEY = 'tutorial.yuiGuide.lines.introActivationHint';
    const INTRO_ACTIVATION_HINT = '点一下这里，我就能开始说话啦～';
    const GUIDE_AUDIO_FILES_BY_KEY = Object.freeze({
        intro_basic: {
            zh: GUIDE_AUDIO_FILE_NAMES.intro_basic,
            ja: GUIDE_AUDIO_FILE_NAMES.intro_basic,
            en: GUIDE_AUDIO_FILE_NAMES.intro_basic,
            ko: GUIDE_AUDIO_FILE_NAMES.intro_basic,
            ru: GUIDE_AUDIO_FILE_NAMES.intro_basic
        },
        intro_greeting_reply: {
            zh: GUIDE_AUDIO_FILE_NAMES.intro_greeting_reply,
            ja: GUIDE_AUDIO_FILE_NAMES.intro_greeting_reply,
            en: GUIDE_AUDIO_FILE_NAMES.intro_greeting_reply,
            ko: GUIDE_AUDIO_FILE_NAMES.intro_greeting_reply,
            ru: GUIDE_AUDIO_FILE_NAMES.intro_greeting_reply
        },
        takeover_capture_cursor: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_capture_cursor,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_capture_cursor,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_capture_cursor,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_capture_cursor,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_capture_cursor
        },
        takeover_plugin_preview_home: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_home,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_home,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_home,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_home,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_home
        },
        takeover_plugin_preview_dashboard: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard
        },
        takeover_settings_peek_intro: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_intro,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_intro,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_intro,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_intro,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_intro
        },
        takeover_settings_peek_detail: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_detail,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_detail,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_detail,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_detail,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_settings_peek_detail
        },
        interrupt_resist_light_1: {
            zh: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
            ja: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
            en: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
            ko: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
            ru: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1
        },
        interrupt_resist_light_3: {
            zh: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
            ja: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
            en: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
            ko: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
            ru: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3
        },
        interrupt_angry_exit: {
            zh: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
            ja: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
            en: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
            ko: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
            ru: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit
        },
        takeover_return_control: {
            zh: GUIDE_AUDIO_FILE_NAMES.takeover_return_control,
            ja: GUIDE_AUDIO_FILE_NAMES.takeover_return_control,
            en: GUIDE_AUDIO_FILE_NAMES.takeover_return_control,
            ko: GUIDE_AUDIO_FILE_NAMES.takeover_return_control,
            ru: GUIDE_AUDIO_FILE_NAMES.takeover_return_control
        }
    });

    function guideAudioFilesForAllLocales(fileName) {
        return Object.freeze({
            zh: fileName,
            ja: fileName,
            en: fileName,
            ko: fileName,
            ru: fileName
        });
    }

    const GUIDE_AUDIO_FILE_OVERRIDES_BY_KEY = Object.freeze({});

    function guideAudioSrc(key) {
        const files = key
            ? (GUIDE_AUDIO_FILE_OVERRIDES_BY_KEY[key] || GUIDE_AUDIO_FILES_BY_KEY[key] || null)
            : null;
        if (!files) {
            return '';
        }

        // 当前 locale 没有对应语音文件时（如 es / pt 等未提供录音的语言），
        // 默认 fallback 是英文，避免回退到中文给非中文用户带来违和感。
        const locale = resolveGuideLocale();
        const fileName = files[locale] || files.en || '';
        const fileLocale = files[locale] ? locale : 'en';
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
        })
    });

    const GUIDE_AUDIO_DURATIONS_BY_KEY = Object.freeze({
        intro_basic: Object.freeze({
            zh: 16129,
            ja: 19977,
            en: 12218,
            ko: 17978,
            ru: 17018
        }),
        intro_greeting_reply: Object.freeze({
            zh: 18219,
            ja: 21818,
            en: 17097,
            ko: 18777,
            ru: 17097
        }),
        takeover_capture_cursor: Object.freeze({
            zh: 22889,
            ja: 26274,
            en: 20457,
            ko: 21497,
            ru: 25954
        }),
        takeover_plugin_preview_home: Object.freeze({
            zh: 6583,
            ja: 8297,
            en: 5738,
            ko: 6640,
            ru: 5897
        }),
        takeover_plugin_preview_dashboard: Object.freeze({
            zh: 9218,
            ja: 16538,
            en: 10857,
            ko: 10857,
            ru: 9497
        }),
        takeover_settings_peek_intro: Object.freeze({
            zh: 11877,
            ja: 17817,
            en: 9338,
            ko: 11360,
            ru: 10697
        }),
        takeover_settings_peek_detail: Object.freeze({
            zh: 13923,
            ja: 24514,
            en: 15497,
            ko: 21600,
            ru: 14937
        }),
        interrupt_resist_light_1: Object.freeze({
            zh: 3994,
            ja: 5337,
            en: 4617,
            ko: 4538,
            ru: 4538
        }),
        interrupt_resist_light_3: Object.freeze({
            zh: 4493,
            ja: 7257,
            en: 5097,
            ko: 5680,
            ru: 4937
        }),
        interrupt_angry_exit: Object.freeze({
            zh: 9160,
            ja: 13898,
            en: 9338,
            ko: 7920,
            ru: 10617
        }),
        takeover_return_control: Object.freeze({
            zh: 11761,
            ja: 19680,
            en: 10857,
            ko: 11577,
            ru: 11337
        })
    });

    const GUIDE_DEBUG_ZH_TEXT_BY_KEY = Object.freeze({
        'tutorial.yuiGuide.lines.introBasic': '这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！',
        'tutorial.yuiGuide.lines.takeoverCaptureCursor': '超级魔法按钮出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！',
        'tutorial.yuiGuide.lines.takeoverPluginPreviewHome': '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！',
        'tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard': '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～',
        'tutorial.yuiGuide.lines.pluginDashboardPopupBlocked': '浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekIntro': '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekDetail': '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1': '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2': '等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！',
        'tutorial.yuiGuide.lines.takeoverReturnControl': '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～',
        'tutorial.yuiGuide.lines.interruptResistLight1': '喂！不要拽我啦，还没轮到你的回合呢！',
        'tutorial.yuiGuide.lines.interruptResistLight3': '等一下啦！还没结束呢，不要随便打断我啦！',
        'tutorial.yuiGuide.lines.interruptAngryExit': '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！',
        'tutorial.yuiGuide.lines.introGreetingReply': '欢迎回家，喵~ 外面的世界很辛苦吧？在这个专属我们的小窝里，你可以放下所有的烦恼哦。我是林悠怡，接下来的熟悉过程请放心交给我，我会一步步牵着您的手慢慢来的。'
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

    function resolveGuideChatStreamSyncDurationMs(durationMs) {
        if (!Number.isFinite(durationMs) || durationMs <= 0) {
            return 0;
        }

        return clamp(Math.round(durationMs * 0.78), 720, 24000);
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

        async fetchPreviewAudioSrc(text) {
            const message = typeof text === 'string' ? text.trim() : '';
            if (!message) {
                return null;
            }

            const voiceId = await this.getCurrentVoiceId();
            if (!voiceId) {
                return null;
            }

            const cacheKey = voiceId + '::' + message;
            if (this.previewCache.has(cacheKey)) {
                return {
                    voiceId: voiceId,
                    audioSrc: this.previewCache.get(cacheKey)
                };
            }

            try {
                const response = await fetch(
                    '/api/characters/voice_preview?voice_id='
                    + encodeURIComponent(voiceId)
                    + '&text='
                    + encodeURIComponent(message),
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
                this.previewCache.set(cacheKey, audioSrc);
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

        apply(emotion) {
            if (!emotion) {
                return;
            }

            const activeModelType = this.getActiveModelType();
            if (activeModelType === 'live2d') {
                if (!window.live2dManager || !window.live2dManager.currentModel || typeof window.live2dManager.playMotion !== 'function') {
                    return;
                }

                try {
                    const motionPromise = window.live2dManager.playMotion(emotion);
                    this.handleAsyncFailure(motionPromise, '[YuiGuide] 播放教程动作失败:', emotion);
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

            let handled = false;

            if (typeof manager.clearEmotionEffects === 'function') {
                this.handleAsyncFailure(
                    manager.clearEmotionEffects(),
                    '[YuiGuide] 清理 Live2D 教程动作失败:'
                );
                handled = true;
            }

            if (typeof manager.clearExpression === 'function') {
                this.handleAsyncFailure(
                    manager.clearExpression(),
                    '[YuiGuide] 清理 Live2D 表情失败:'
                );
                handled = true;
            }

            const motionManager = manager.currentModel
                && manager.currentModel.internalModel
                && manager.currentModel.internalModel.motionManager;
            if (motionManager && typeof motionManager.stopAllMotions === 'function') {
                motionManager.stopAllMotions();
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

    class YuiGuideGhostCursor {
        constructor(overlay) {
            this.overlay = overlay;
            this.motionToken = 0;
            this.lastTarget = null;
            this.reactionToken = 0;
        }

        hasPosition() {
            return this.overlay.hasCursorPosition();
        }

        showAt(x, y) {
            this.overlay.showCursorAt(x, y);
        }

        moveToPoint(x, y, options) {
            const normalizedOptions = options || {};
            const token = ++this.motionToken;
            this.lastTarget = { x: x, y: y };
            return this.overlay.moveCursorTo(x, y, Object.assign({}, normalizedOptions, {
                cancelCheck: () => {
                    if (token !== this.motionToken) {
                        return true;
                    }

                    return typeof normalizedOptions.cancelCheck === 'function'
                        ? !!normalizedOptions.cancelCheck()
                        : false;
                }
            }));
        }

        moveToRect(rect, options) {
            if (!rect) {
                return Promise.resolve();
            }

            const point = {
                x: rect.left + (rect.width / 2),
                y: rect.top + (rect.height / 2)
            };

            return this.moveToPoint(point.x, point.y, options);
        }

        async resistTo(userX, userY) {
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const dx = userX - current.x;
            const dy = userY - current.y;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const pullDistance = clamp(distance * 0.22, 12, 36);
            const pullX = current.x + ((dx / distance) * pullDistance);
            const pullY = current.y + ((dy / distance) * pullDistance);
            const returnTarget = this.lastTarget || current;

            await this.overlay.moveCursorTo(pullX, pullY, { durationMs: 120 });
            this.overlay.wobbleCursor();
            await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, { durationMs: 260 });
        }

        async reactToUserMotion(userX, userY, options) {
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const normalizedOptions = options || {};
            const dx = userX - current.x;
            const dy = userY - current.y;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const reactionDistance = clamp(
                distance * (Number.isFinite(normalizedOptions.scale) ? normalizedOptions.scale : 0.12),
                6,
                18
            );
            const targetX = current.x + ((dx / distance) * reactionDistance);
            const targetY = current.y + ((dy / distance) * reactionDistance);
            const returnTarget = this.lastTarget || current;
            const token = ++this.reactionToken;

            await this.overlay.moveCursorTo(targetX, targetY, {
                durationMs: Number.isFinite(normalizedOptions.outDurationMs) ? normalizedOptions.outDurationMs : 80
            });
            if (token !== this.reactionToken) {
                return;
            }

            await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, {
                durationMs: Number.isFinite(normalizedOptions.backDurationMs) ? normalizedOptions.backDurationMs : 150
            });
        }

        click(durationMs) {
            this.overlay.clickCursor(durationMs);
        }

        wobble() {
            this.overlay.wobbleCursor();
        }

        runEllipse(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck) {
            const token = ++this.motionToken;
            return this.overlay.runEllipseAnimation(
                centerX,
                centerY,
                radiusX,
                radiusY,
                cycleMs,
                abortCheck,
                null,
                () => token !== this.motionToken
            );
        }

        runPauseAwareEllipse(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck, pauseCheck, cancelCheck) {
            const normalizedCancelCheck = typeof cancelCheck === 'function' ? cancelCheck : null;
            const token = ++this.motionToken;
            return this.overlay.runEllipseAnimation(
                centerX,
                centerY,
                radiusX,
                radiusY,
                cycleMs,
                abortCheck,
                pauseCheck,
                () => {
                    if (token !== this.motionToken) {
                        return true;
                    }

                    return normalizedCancelCheck ? !!normalizedCancelCheck() : false;
                }
            );
        }

        hide() {
            this.overlay.hideCursor();
        }

        cancel() {
            this.motionToken += 1;
            this.reactionToken += 1;
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
            this.cursor = new YuiGuideGhostCursor(this.overlay);
            this.currentSceneId = null;
            this.currentStep = null;
            this.currentContext = null;
            this.sceneRunId = 0;
            this.sceneTimers = new Set();
            this.guideChatStreamTimers = new Set();
            this.interruptsEnabled = false;
            this.interruptCount = 0;
            this.interruptAccelerationStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPassiveResistanceAt = 0;
            this.lastPointerPoint = null;
            this.angryExitTriggered = false;
            this.destroyed = false;
            this.lastTutorialEndReason = null;
            this.introFlowStarted = false;
            this.introFlowCompleted = false;
            this.awaitingIntroActivation = false;
            this._introActivationResolve = null;
            this.takeoverFlowStarted = false;
            this.takeoverFlowCompleted = false;
            this.takeoverFlowPromise = null;
            this.terminationRequested = false;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.scenePausedForResistance = false;
            this.scenePausedAt = 0;
            this.scenePauseResolvers = [];
            this.virtualSpotlights = new Map();
            this.preciseHighlightElements = new Set();
            this.spotlightVariantElements = new Set();
            this.spotlightGeometryHintElements = new Set();
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.activeGuideEmotion = '';
            this.pluginDashboardHandoff = null;
            this.pluginDashboardLastInterruptRequestId = '';
            this.pluginDashboardWindowCreatedByGuide = false;
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            this.takeoverOriginalAgentSwitches = null;
            this.customSecondarySpotlightTarget = null;
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
            this.remoteTerminationRequestHandler = this.onRemoteTerminationRequest.bind(this);
            this.messageHandler = this.onWindowMessage.bind(this);
            this.skipButtonClickHandler = this.onSkipButtonClick.bind(this);
            this.interactionGuardHandler = this.onInteractionGuard.bind(this);
            this.externalizedChatSpotlightKind = '';
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
            this.tutorialFaceForwardLockSnapshot = null;
            this.enableTutorialFaceForwardLock();

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-driver-hidden');
                this.setExternalizedChatButtonsDisabled(true);
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
            window.addEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.addEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.addEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.addEventListener('message', this.messageHandler, true);
            document.addEventListener('pointerdown', this.interactionGuardHandler, true);
            document.addEventListener('pointerup', this.interactionGuardHandler, true);
            document.addEventListener('mousedown', this.interactionGuardHandler, true);
            document.addEventListener('mouseup', this.interactionGuardHandler, true);
            document.addEventListener('touchstart', this.interactionGuardHandler, true);
            document.addEventListener('touchend', this.interactionGuardHandler, true);
            document.addEventListener('click', this.interactionGuardHandler, true);
            document.addEventListener('dblclick', this.interactionGuardHandler, true);
            document.addEventListener('contextmenu', this.interactionGuardHandler, true);
            document.addEventListener('pointerdown', this.skipButtonClickHandler, true);
            document.addEventListener('mousedown', this.skipButtonClickHandler, true);
            document.addEventListener('touchstart', this.skipButtonClickHandler, true);
            document.addEventListener('click', this.skipButtonClickHandler, true);
        }

        isStopping() {
            return !!(this.destroyed || this.angryExitTriggered || this.terminationRequested);
        }

        getPreludeSceneIds() {
            if (this.tutorialManager && typeof this.tutorialManager.getYuiGuidePreludeSceneIds === 'function') {
                return this.tutorialManager.getYuiGuidePreludeSceneIds(this.page) || [];
            }

            if (!this.registry || !this.registry.sceneOrder) {
                return [];
            }

            const pageOrder = Array.isArray(this.registry.sceneOrder[this.page]) ? this.registry.sceneOrder[this.page] : [];
            return pageOrder.filter(function (sceneId) {
                return typeof sceneId === 'string' && sceneId.indexOf('intro_') === 0;
            });
        }

        enableTutorialFaceForwardLock() {
            if (this.tutorialFaceForwardLockSnapshot) {
                this.applyTutorialFaceForwardLock();
                return;
            }

            const live2dManager = window.live2dManager || null;
            const vrmManager = window.vrmManager || null;
            const mmdManager = window.mmdManager || null;
            this.tutorialFaceForwardLockSnapshot = {
                hadWindowMouseTrackingEnabled: typeof window.mouseTrackingEnabled !== 'undefined',
                windowMouseTrackingEnabled: window.mouseTrackingEnabled,
                live2dMouseTrackingEnabled: live2dManager && typeof live2dManager.isMouseTrackingEnabled === 'function'
                    ? live2dManager.isMouseTrackingEnabled()
                    : null,
                vrmMouseTrackingEnabled: vrmManager && typeof vrmManager.isMouseTrackingEnabled === 'function'
                    ? vrmManager.isMouseTrackingEnabled()
                    : null,
                mmdCursorFollowEnabled: mmdManager && mmdManager.cursorFollow
                    ? mmdManager.cursorFollow.enabled !== false
                    : null
            };
            window.nekoYuiGuideFaceForwardLock = true;
            window.mouseTrackingEnabled = false;
            this.applyTutorialFaceForwardLock();
        }

        applyTutorialFaceForwardLock() {
            window.nekoYuiGuideFaceForwardLock = true;
            window.mouseTrackingEnabled = false;

            const live2dManager = window.live2dManager || null;
            if (live2dManager && typeof live2dManager.setMouseTrackingEnabled === 'function') {
                try {
                    live2dManager.setMouseTrackingEnabled(false);
                } catch (error) {
                    console.warn('[YuiGuide] 锁定 Live2D 正脸失败:', error);
                }
            }

            const vrmManager = window.vrmManager || null;
            if (vrmManager && typeof vrmManager.setMouseTrackingEnabled === 'function') {
                try {
                    vrmManager.setMouseTrackingEnabled(false);
                    if (vrmManager._cursorFollow && typeof vrmManager._cursorFollow._completeDisable === 'function') {
                        vrmManager._cursorFollow._completeDisable();
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 锁定 VRM 正脸失败:', error);
                }
            }

            const mmdCursorFollow = window.mmdManager && window.mmdManager.cursorFollow
                ? window.mmdManager.cursorFollow
                : null;
            if (mmdCursorFollow && typeof mmdCursorFollow.setEnabled === 'function') {
                try {
                    mmdCursorFollow.setEnabled(false);
                } catch (error) {
                    console.warn('[YuiGuide] 锁定 MMD 正脸失败:', error);
                }
            }
        }

        releaseTutorialFaceForwardLock() {
            const snapshot = this.tutorialFaceForwardLockSnapshot;
            if (!snapshot) {
                return;
            }

            this.tutorialFaceForwardLockSnapshot = null;
            window.nekoYuiGuideFaceForwardLock = false;
            if (snapshot.hadWindowMouseTrackingEnabled) {
                window.mouseTrackingEnabled = snapshot.windowMouseTrackingEnabled;
            } else {
                try {
                    delete window.mouseTrackingEnabled;
                } catch (_) {
                    window.mouseTrackingEnabled = undefined;
                }
            }
            const restoredMouseTrackingEnabled = window.mouseTrackingEnabled !== false;

            const live2dManager = window.live2dManager || null;
            if (live2dManager && typeof live2dManager.setMouseTrackingEnabled === 'function') {
                try {
                    live2dManager.setMouseTrackingEnabled(
                        snapshot.live2dMouseTrackingEnabled !== null
                            ? snapshot.live2dMouseTrackingEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[YuiGuide] 恢复 Live2D 鼠标跟踪失败:', error);
                }
            }

            const vrmManager = window.vrmManager || null;
            if (vrmManager && typeof vrmManager.setMouseTrackingEnabled === 'function') {
                try {
                    vrmManager.setMouseTrackingEnabled(
                        snapshot.vrmMouseTrackingEnabled !== null
                            ? snapshot.vrmMouseTrackingEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[YuiGuide] 恢复 VRM 鼠标跟踪失败:', error);
                }
            }

            const mmdCursorFollow = window.mmdManager && window.mmdManager.cursorFollow
                ? window.mmdManager.cursorFollow
                : null;
            if (mmdCursorFollow && typeof mmdCursorFollow.setEnabled === 'function') {
                try {
                    mmdCursorFollow.setEnabled(
                        snapshot.mmdCursorFollowEnabled !== null
                            ? snapshot.mmdCursorFollowEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[YuiGuide] 恢复 MMD 鼠标跟踪失败:', error);
                }
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

        resolveGuideCopy(textKey, fallbackText) {
            return translateGuideText(textKey, fallbackText);
        }

        applyGuideEmotion(emotion) {
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return;
            }

            this.activeGuideEmotion = normalizedEmotion;
            this.emotionBridge.apply(normalizedEmotion);
        }

        clearGuidePresentation() {
            this.activeGuideEmotion = '';
            this.emotionBridge.clear();
        }

        captureCurrentGuidePresentationSnapshot() {
            if (this.activeGuideEmotion) {
                return {
                    emotion: this.activeGuideEmotion
                };
            }

            return null;
        }

        restoreGuidePresentationSnapshot(snapshot) {
            if (!snapshot) {
                return false;
            }

            if (snapshot.emotion) {
                this.applyGuideEmotion(snapshot.emotion);
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
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            return rect;
        }

        createVirtualSpotlight(key, rect, options) {
            if (!key || !rect) {
                return null;
            }

            const normalizedOptions = options || {};
            const padding = Number.isFinite(normalizedOptions.padding) ? normalizedOptions.padding : DEFAULT_SPOTLIGHT_PADDING;
            const radius = Number.isFinite(normalizedOptions.radius) ? normalizedOptions.radius : 20;
            const elementKey = String(key);
            let element = this.virtualSpotlights.get(elementKey) || null;
            if (!element) {
                element = document.createElement('div');
                element.setAttribute('data-yui-guide-virtual-spotlight', elementKey);
                Object.assign(element.style, {
                    position: 'fixed',
                    pointerEvents: 'none',
                    opacity: '0',
                    zIndex: '-1'
                });
                document.body.appendChild(element);
                this.virtualSpotlights.set(elementKey, element);
            }

            const left = Math.max(0, Math.floor(rect.left));
            const top = Math.max(0, Math.floor(rect.top));
            const right = Math.min(window.innerWidth, Math.ceil(rect.right));
            const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom));
            element.style.left = left + 'px';
            element.style.top = top + 'px';
            element.style.width = Math.max(0, right - left) + 'px';
            element.style.height = Math.max(0, bottom - top) + 'px';
            element.style.borderRadius = radius + 'px';
            element.setAttribute('data-yui-guide-spotlight-padding', String(padding));
            element.setAttribute('data-yui-guide-spotlight-radius', String(radius));
            return element;
        }

        createPluginManagementEntrySpotlight(button) {
            const rect = this.getElementRect(button);
            if (!rect) {
                return button || null;
            }

            return this.createVirtualSpotlight('plugin-management-entry', {
                left: rect.left - PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                top: rect.top,
                right: rect.right + PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                bottom: rect.bottom
            }, {
                padding: DEFAULT_SPOTLIGHT_PADDING + 2,
                radius: 18
            }) || button;
        }

        createUnionSpotlight(key, elements, options) {
            const rect = unionRects((Array.isArray(elements) ? elements : []).map((element) => this.getElementRect(element)));
            return rect ? this.createVirtualSpotlight(key, rect, options) : null;
        }

        clearVirtualSpotlight(key) {
            if (!key) {
                return;
            }

            const element = this.virtualSpotlights.get(String(key));
            if (element && element.parentNode) {
                element.parentNode.removeChild(element);
            }
            this.virtualSpotlights.delete(String(key));
        }

        clearAllVirtualSpotlights() {
            this.virtualSpotlights.forEach((element) => {
                if (element && element.parentNode) {
                    element.parentNode.removeChild(element);
                }
            });
            this.virtualSpotlights.clear();
        }

        clearPreciseHighlights() {
            this.preciseHighlightElements.forEach((element) => {
                if (!element || !element.classList) {
                    return;
                }

                element.classList.remove('yui-guide-precise-highlight');
                element.removeAttribute('data-yui-guide-precise-highlight');
            });
            this.preciseHighlightElements.clear();
        }

        setPreciseHighlightTargets(elements) {
            const targets = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && !!element.classList);

            this.clearPreciseHighlights();
            targets.forEach((element) => {
                element.classList.add('yui-guide-precise-highlight');
                element.setAttribute('data-yui-guide-precise-highlight', 'true');
                this.preciseHighlightElements.add(element);
            });
        }

        clearSpotlightVariantHints() {
            this.spotlightVariantElements.forEach((element) => {
                if (!element || typeof element.removeAttribute !== 'function') {
                    return;
                }

                element.removeAttribute('data-yui-guide-spotlight-variant');
            });
            this.spotlightVariantElements.clear();
        }

        clearSpotlightGeometryHints() {
            this.spotlightGeometryHintElements.forEach((element) => {
                if (!element || typeof element.removeAttribute !== 'function') {
                    return;
                }

                element.removeAttribute('data-yui-guide-spotlight-padding');
                element.removeAttribute('data-yui-guide-spotlight-radius');
                element.removeAttribute('data-yui-guide-spotlight-geometry');
            });
            this.spotlightGeometryHintElements.clear();
        }

        setSpotlightGeometryHint(element, options) {
            if (!element || typeof element.setAttribute !== 'function') {
                return;
            }

            const normalizedOptions = options || {};
            const padding = Number.isFinite(normalizedOptions.padding) ? normalizedOptions.padding : null;
            const radius = Number.isFinite(normalizedOptions.radius) ? normalizedOptions.radius : null;
            const geometry = typeof normalizedOptions.geometry === 'string'
                ? normalizedOptions.geometry.trim().toLowerCase()
                : '';

            if (padding !== null) {
                element.setAttribute('data-yui-guide-spotlight-padding', String(padding));
            } else {
                element.removeAttribute('data-yui-guide-spotlight-padding');
            }

            if (radius !== null) {
                element.setAttribute('data-yui-guide-spotlight-radius', String(radius));
            } else {
                element.removeAttribute('data-yui-guide-spotlight-radius');
            }

            if (geometry) {
                element.setAttribute('data-yui-guide-spotlight-geometry', geometry);
            } else {
                element.removeAttribute('data-yui-guide-spotlight-geometry');
            }

            this.spotlightGeometryHintElements.add(element);
        }

        setSpotlightVariantHints(entries) {
            this.clearSpotlightVariantHints();
            (Array.isArray(entries) ? entries : []).forEach((entry) => {
                const element = entry && entry.element;
                const variant = entry && entry.variant;
                if (!element || typeof element.setAttribute !== 'function' || !variant) {
                    return;
                }

                element.setAttribute('data-yui-guide-spotlight-variant', String(variant));
                this.spotlightVariantElements.add(element);
            });
        }

        syncExtraSpotlights() {
            const nextElements = [];
            const seen = new Set();
            [this.retainedExtraSpotlightElements, this.sceneExtraSpotlightElements].forEach((elements) => {
                (Array.isArray(elements) ? elements : []).forEach((element) => {
                    const isVirtualSpotlight = !!(
                        element
                        && typeof element.getAttribute === 'function'
                        && element.getAttribute('data-yui-guide-virtual-spotlight')
                    );
                    if (
                        !element
                        || typeof element.getBoundingClientRect !== 'function'
                        || (!isVirtualSpotlight && element.isConnected === false)
                        || seen.has(element)
                    ) {
                        return;
                    }
                    seen.add(element);
                    this.applyCircularFloatingButtonSpotlightHint(element);
                    nextElements.push(element);
                });
            });
            this.overlay.setExtraSpotlights(nextElements);
        }

        addRetainedExtraSpotlight(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return;
            }

            if (!this.retainedExtraSpotlightElements.includes(element)) {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            const normalizedMatcher = typeof matcher === 'function'
                ? matcher
                : (candidate) => candidate === matcher;
            this.retainedExtraSpotlightElements = this.retainedExtraSpotlightElements.filter((candidate) => {
                try {
                    return !normalizedMatcher(candidate);
                } catch (_) {
                    return true;
                }
            });
            if (element && typeof element.getBoundingClientRect === 'function') {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        removeRetainedExtraSpotlight(matcher) {
            const normalizedMatcher = typeof matcher === 'function'
                ? matcher
                : (candidate) => candidate === matcher;
            this.retainedExtraSpotlightElements = this.retainedExtraSpotlightElements.filter((candidate) => {
                try {
                    return !normalizedMatcher(candidate);
                } catch (_) {
                    return true;
                }
            });
            this.syncExtraSpotlights();
        }

        clearRetainedExtraSpotlights() {
            this.retainedExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        setSceneExtraSpotlights(elements) {
            this.sceneExtraSpotlightElements = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && typeof element.getBoundingClientRect === 'function');
            this.syncExtraSpotlights();
        }

        clearSceneExtraSpotlights() {
            this.sceneExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        clearAllExtraSpotlights() {
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.overlay.clearExtraSpotlights();
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
            if (!element) {
                return null;
            }

            if (typeof element.closest === 'function') {
                const shell = element.closest(
                    '#live2d-btn-mic, #vrm-btn-mic, #mmd-btn-mic, ' +
                    '#live2d-btn-agent, #vrm-btn-agent, #mmd-btn-agent, ' +
                    '#live2d-btn-settings, #vrm-btn-settings, #mmd-btn-settings, ' +
                    '[id$="-btn-mic"], [id$="-btn-agent"], [id$="-btn-settings"]'
                );
                if (shell) {
                    return shell;
                }
            }

            return element;
        }

        isCircularFloatingButtonSpotlight(element) {
            const target = this.getFloatingButtonShell(element) || element;
            if (!target || typeof target.id !== 'string') {
                return false;
            }

            return /-btn-(mic|agent|settings)$/.test(target.id);
        }

        applyCircularFloatingButtonSpotlightHint(element) {
            if (!this.isCircularFloatingButtonSpotlight(element)) {
                return false;
            }

            const target = this.getFloatingButtonShell(element) || element;
            this.setSpotlightGeometryHint(target, {
                padding: 4,
                geometry: 'circle'
            });
            return true;
        }

        getSettingsPeekTargets() {
            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            return {
                characterMenu: this.getSettingsMenuElement('character'),
                appearanceItem: this.getCharacterMenuElement(appearanceMenuId),
                voiceCloneItem: this.getCharacterMenuElement('voice-clone')
            };
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
            if (!target) {
                return null;
            }

            if (Array.isArray(target)) {
                return this.createUnionSpotlight(fallbackKey || 'highlight-union', target, {
                    padding: DEFAULT_SPOTLIGHT_PADDING,
                    radius: 18
                });
            }

            if (typeof target === 'string') {
                return this.resolveElement(target);
            }

            if (target && typeof target === 'object') {
                if (target.element) {
                    return target.element;
                }
                if (target.selector) {
                    return this.resolveElement(target.selector);
                }
                if (Array.isArray(target.elements)) {
                    return this.createUnionSpotlight(
                        target.key || fallbackKey || 'highlight-union',
                        target.elements,
                        target.options || {}
                    );
                }
                if (target.rect) {
                    return this.createVirtualSpotlight(
                        target.key || fallbackKey || 'highlight-rect',
                        target.rect,
                        target.options || {}
                    );
                }
            }

            return target;
        }

        applyGuideHighlights(config) {
            const normalized = config || {};
            const keyBase = normalized.key || 'guide-highlight';
            const persistentTarget = Object.prototype.hasOwnProperty.call(normalized, 'persistent')
                ? this.normalizeHighlightTarget(normalized.persistent, keyBase + '-persistent')
                : null;
            const primaryTarget = Object.prototype.hasOwnProperty.call(normalized, 'primary')
                ? this.normalizeHighlightTarget(normalized.primary, keyBase + '-primary')
                : null;
            const secondaryTarget = Object.prototype.hasOwnProperty.call(normalized, 'secondary')
                ? this.normalizeHighlightTarget(normalized.secondary, keyBase + '-secondary')
                : null;

            if (Object.prototype.hasOwnProperty.call(normalized, 'persistent')) {
                if (persistentTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(persistentTarget);
                    this.overlay.setPersistentSpotlight(persistentTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
                if (primaryTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(primaryTarget);
                    this.overlay.activateSpotlight(primaryTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'secondary')) {
                this.customSecondarySpotlightTarget = secondaryTarget || null;
                if (secondaryTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(secondaryTarget);
                    this.overlay.activateSecondarySpotlight(secondaryTarget);
                } else if (!Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
                    this.overlay.clearActionSpotlight();
                }
            }

            return {
                persistent: persistentTarget,
                primary: primaryTarget,
                secondary: secondaryTarget
            };
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

        getChatWindowTarget() {
            const preferredSelectors = [
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
            const overlay = document.getElementById('react-chat-window-overlay');
            if (!overlay) {
                return false;
            }
            // CSS [hidden] 规则用 !important 控制可见性，不会写 inline style。
            // 内联 display:none 仅由外部 preload（如 preload-pet.js）设置以永久
            // 隐藏 Pet 窗口里嵌着的 React 聊天 overlay。
            return overlay.style.display === 'none';
        }

        setExternalizedChatButtonsDisabled(disabled) {
            if (this.page !== 'home' || !this.isHomeChatExternalized()) {
                return;
            }

            const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
            if (!channel || typeof channel.postMessage !== 'function') {
                return;
            }

            try {
                channel.postMessage({
                    action: 'yui_guide_set_chat_buttons_disabled',
                    disabled: disabled !== false,
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[YuiGuide] 同步独立聊天窗按钮禁用状态失败:', error);
            }
        }

        setExternalizedChatSpotlight(kind) {
            if (this.page !== 'home' || !this.isHomeChatExternalized()) {
                return;
            }

            this.externalizedChatSpotlightKind = typeof kind === 'string' ? kind : '';

            const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
            if (!channel || typeof channel.postMessage !== 'function') {
                return;
            }

            try {
                channel.postMessage({
                    action: 'yui_guide_set_chat_spotlight',
                    kind: typeof kind === 'string' ? kind : '',
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[YuiGuide] 同步独立聊天窗高亮失败:', error);
            }
        }

        clearExternalizedChatFx() {
            this.externalizedChatSpotlightKind = '';
            this.setExternalizedChatSpotlight('');
        }

        onExternalChatReady() {
            if (this.destroyed || this.page !== 'home' || !this.isHomeChatExternalized()) {
                return;
            }

            this.setExternalizedChatButtonsDisabled(true);
            if (this.externalizedChatSpotlightKind) {
                this.setExternalizedChatSpotlight(this.externalizedChatSpotlightKind);
            }
        }

        getSceneSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'intro_basic' && !this.introFlowCompleted) {
                if (this.awaitingIntroActivation) {
                    return this.getChatInputTarget() || null;
                }

                return this.getChatWindowTarget() || this.getChatInputTarget() || null;
            }

            if (stepId === 'takeover_settings_peek') {
                return this.getChatWindowTarget() || this.getChatInputTarget() || null;
            }

            if (this.shouldNarrateInChat(stepId)) {
                return this.getChatWindowTarget() || fallbackTarget;
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

            if (stepId === 'takeover_capture_cursor' || stepId === 'takeover_plugin_preview') {
                return this.getFloatingButtonShell(fallbackTarget) || fallbackTarget;
            }

            if (stepId === 'takeover_settings_peek') {
                const settingsMenuId = this.normalizeSettingsMenuId((performance && performance.settingsMenuId) || 'character');
                if (this.isManagedPanelVisible('settings')) {
                    return this.getSettingsMenuElement(settingsMenuId)
                        || this.getManagedPanelElement('settings')
                        || fallbackTarget;
                }

                return fallbackTarget;
            }

            return null;
        }

        highlightChatInput() {
            this.focusAndHighlightChatInput(this.getChatInputTarget());
        }

        highlightChatWindow() {
            if (this.isHomeChatExternalized()) {
                this.setExternalizedChatSpotlight('window');
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

            this.overlay.setPersistentSpotlight(target);
        }

        getChatIntroActivationTarget() {
            const preferredSelectors = [
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
            this.sceneTimers.forEach(function (timerId) {
                window.clearTimeout(timerId);
            });
            this.sceneTimers.clear();
        }

        clearGuideChatStreamTimers() {
            this.guideChatStreamTimers.forEach(function (timerId) {
                window.clearTimeout(timerId);
            });
            this.guideChatStreamTimers.clear();
        }

        scheduleGuideChatStream(callback, delayMs) {
            const timerId = window.setTimeout(() => {
                this.guideChatStreamTimers.delete(timerId);
                callback();
            }, delayMs);
            this.guideChatStreamTimers.add(timerId);
            return timerId;
        }

        schedule(callback, delayMs) {
            const timerId = window.setTimeout(() => {
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
            if (this.scenePausedForResistance) {
                return;
            }

            this.scenePausedForResistance = true;
            this.scenePausedAt = Date.now();
            this.cursor.cancel();
        }

        resumeCurrentSceneAfterResistance() {
            if (!this.scenePausedForResistance) {
                return;
            }

            this.scenePausedForResistance = false;
            this.scenePausedAt = 0;
            const resolvers = this.scenePauseResolvers.slice();
            this.scenePauseResolvers = [];
            resolvers.forEach((resolve) => {
                try {
                    resolve();
                } catch (_) {}
            });
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

            return {
                at: clamp(fallbackCue.at, 0, 1),
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
                    || estimateSpeechDurationMs(fallbackText || '')
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

            const normalizedLocale = normalizeGuideLocale(locale || resolveGuideLocale());
            const exactDurationMs = Number.isFinite(durationConfig[normalizedLocale])
                ? durationConfig[normalizedLocale]
                : 0;
            if (exactDurationMs > 0) {
                return exactDurationMs;
            }

            const fallbackDurationMs = Number.isFinite(durationConfig.zh) ? durationConfig.zh : 0;
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
            narration.resumeAudioOffsetMs = playbackSnapshot
                && Number.isFinite(playbackSnapshot.currentTimeMs)
                ? Math.max(0, playbackSnapshot.currentTimeMs)
                : 0;

            narration.interrupted = true;
            this.clearNarrationResumeTimer();
            this.voiceQueue.stop();
            return true;
        }

        scheduleNarrationResume(options) {
            this.clearNarrationResumeTimer();
            const resumeOptions = options || {};

            const attemptResume = () => {
                const narration = this.activeNarration;
                if (!narration || narration.cancelled || this.destroyed) {
                    this.restoreCurrentScenePresentation({
                        skipEmotion: !!resumeOptions.skipEmotion
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
                    skipEmotion: !!resumeOptions.skipEmotion
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

            const performance = this.currentStep.performance || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);
            const spotlightTarget = this.getSceneSpotlightTarget(this.currentSceneId, performance);
            if (spotlightTarget) {
                this.applyCircularFloatingButtonSpotlightHint(spotlightTarget);
                this.overlay.setPersistentSpotlight(spotlightTarget);
            } else {
                this.overlay.clearPersistentSpotlight();
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(this.currentSceneId, performance);
            if (actionSpotlightTarget) {
                this.applyCircularFloatingButtonSpotlightHint(actionSpotlightTarget);
                this.overlay.activateSpotlight(actionSpotlightTarget);
            } else {
                this.overlay.clearActionSpotlight();
            }

            if (this.customSecondarySpotlightTarget) {
                this.applyCircularFloatingButtonSpotlightHint(this.customSecondarySpotlightTarget);
                this.overlay.activateSecondarySpotlight(this.customSecondarySpotlightTarget);
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
                } else if (this.activeGuideEmotion) {
                    this.clearGuidePresentation();
                }
            }
        }

        async playManagedScene(stepId, meta) {
            const startedAt = Date.now();
            this.setCurrentScene(stepId, meta && meta.context ? meta.context : null);
            this.recordExperienceMetric('scene_start', {
                sceneId: stepId || '',
                source: meta && meta.source ? meta.source : '',
                runId: this.sceneRunId + 1
            });

            try {
                await this.playScene(stepId, meta || {});
                this.recordExperienceMetric('scene_complete', {
                    sceneId: stepId || '',
                    source: meta && meta.source ? meta.source : '',
                    durationMs: Math.max(0, Date.now() - startedAt)
                });
            } catch (error) {
                this.recordExperienceMetric('scene_failed', {
                    sceneId: stepId || '',
                    source: meta && meta.source ? meta.source : '',
                    durationMs: Math.max(0, Date.now() - startedAt),
                    reason: error && error.message ? error.message : 'unknown'
                });
                throw error;
            }
        }

        disableInterrupts() {
            if (!this.interruptsEnabled) {
                return;
            }

            window.removeEventListener('mousemove', this.pointerMoveHandler, true);
            window.removeEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = false;
            this.lastPointerPoint = null;
            this.interruptAccelerationStreak = 0;
            this.lastPassiveResistanceAt = 0;
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
            this.interruptAccelerationStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPassiveResistanceAt = 0;
            this.lastPointerPoint = null;
            window.addEventListener('mousemove', this.pointerMoveHandler, true);
            window.addEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = true;
        }

        maybePlayPassiveResistance(x, y, distance, speed, now) {
            if (!this.cursor.hasPosition()) {
                return;
            }

            if (distance < DEFAULT_PASSIVE_RESISTANCE_DISTANCE) {
                return;
            }

            if (speed < 0.2) {
                return;
            }

            if (now - this.lastPassiveResistanceAt < DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS) {
                return;
            }

            this.lastPassiveResistanceAt = now;
            this.cursor.reactToUserMotion(x, y, {
                scale: 0.16,
                outDurationMs: 90,
                backDurationMs: 180
            });
        }

        shouldAllowInterruptDuringCurrentScene() {
            if (!this.interruptsEnabled || this.destroyed || this.angryExitTriggered) {
                return false;
            }

            if (
                this.page === 'home'
                && this.currentSceneId === 'takeover_plugin_preview'
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
                }, 'takeover_plugin_preview');

                const openedWindow = await this.waitForOpenedWindow(
                    PLUGIN_DASHBOARD_WINDOW_NAME,
                    normalizedTimeoutMs
                );
                if (openedWindow && !openedWindow.closed) {
                    const createdByGuide = !!(
                        guideOpenTriggeredBeforePrompt
                        && !this.manualPluginDashboardOpenUserClicked
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

        async moveCursorToElement(element, durationMs) {
            while (!this.isStopping()) {
                await this.waitUntilSceneResumed();
                const rect = this.getElementRect(element);
                if (!rect) {
                    return false;
                }

                const moved = await this.cursor.moveToRect(rect, {
                    durationMs: Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (moved) {
                    return true;
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
            await this.waitForSceneDelay(visibleMs);
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
            this.cursor.click(clickVisibleMs);
            if (!(await this.waitForSceneDelay(clickVisibleMs))) {
                return false;
            }
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }
            if (typeof normalized.action !== 'function') {
                return true;
            }

            return !!(await normalized.action());
        }

        getVoiceControlButtonTarget() {
            return this.getFloatingButtonShell(
                this.getFallbackFloatingButton('mic')
                || this.resolveElement(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.voiceControl))
            );
        }

        async runIntroVoiceControlButtonShowcase(voiceKey, fallbackText) {
            this.highlightChatWindow();
            const voiceControlButton = this.getVoiceControlButtonTarget();
            if (!voiceControlButton) {
                return;
            }

            this.setSpotlightGeometryHint(voiceControlButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.overlay.activateSpotlight(voiceControlButton);

            if (!this.cursor.hasPosition()) {
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
                || estimateSpeechDurationMs(fallbackText || '');
            const moveDurationMs = clamp(Math.round(narrationDurationMs * 0.16), 900, 2200);
            await this.moveCursorToElement(voiceControlButton, moveDurationMs);
        }

        async runTakeoverKeyboardControlSequence(step, performance, runId) {
            const timingScale = this.getGuideVoiceTimingScale(performance && performance.voiceKey);
            const scaleSceneMs = (value, minValue, maxValue) => {
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
            const guardFailed = () => runId !== this.sceneRunId || this.isStopping();
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

            await this.waitForSceneDelay(scaleSceneMs(180, 80, 420));
            return !guardFailed();
        }

        async runPluginDashboardLaunchSequence(step, performance, runId) {
            const timingScale = this.getGuideVoiceTimingScale(performance && performance.voiceKey);
            const scaleSceneMs = (value, minValue, maxValue) => {
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
            const guardFailed = () => runId !== this.sceneRunId || this.isStopping();

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

            await this.clickCursorAndWait(scaleSceneMs(180, 90, 420));
            const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
            const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
            const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                keepMainUIVisible: true
            });
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
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            let shouldCleanupPreviewState = false;
            let pluginPreviewCleanedUp = false;
            let hoveredPluginToggle = null;
            const timingScale = this.getGuideVoiceTimingScale(performance && performance.voiceKey);
            const scaleSceneMs = (value, minValue, maxValue) => {
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

            const guardFailed = () => {
                return runId !== this.sceneRunId || this.isStopping();
            };

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

                await this.clickCursorAndWait(scaleSceneMs(420, 240, 900));
                const agentPanelOpened = await this.openAgentPanel();
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

                await this.clickCursorAndWait(scaleSceneMs(420, 240, 900));
                const agentMasterEnabled = await this.setAgentMasterEnabled(true);
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

                await this.clickCursorAndWait(scaleSceneMs(420, 240, 900));
                const pluginToggleEnabled = await this.setAgentFlagEnabled('user_plugin_enabled', true);
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
                await this.clickCursorAndWait(scaleSceneMs(180, 90, 420));
                const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                    keepMainUIVisible: true
                });
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

        async waitForPluginDashboardPerformance(windowRef, payload) {
            if (!windowRef || windowRef.closed) {
                this.recordExperienceMetric('handoff_failed', {
                    sceneId: this.currentSceneId || 'takeover_plugin_preview',
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
                const executionTimeoutMs = clamp(
                    estimateSpeechDurationMs(handoffPayload && handoffPayload.line ? handoffPayload.line : '') + 12000,
                    12000,
                    42000
                );
                const targetOrigin = this.getPluginDashboardExpectedOrigin();
                if (!targetOrigin) {
                    this.recordExperienceMetric('handoff_failed', {
                        sceneId: this.currentSceneId || 'takeover_plugin_preview',
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
                                sceneId: this.currentSceneId || 'takeover_plugin_preview',
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

        notifyPluginDashboardNarrationFinished() {
            const handoff = this.pluginDashboardHandoff;
            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!handoff || !windowRef || windowRef.closed || !handoff.sessionId) {
                return;
            }

            try {
                windowRef.postMessage({
                    type: PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
                    sessionId: handoff.sessionId
                }, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
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

        setPluginDashboardSkipBypassEnabled(enabled) {
            if (this.page !== 'home') {
                return;
            }

            window.dispatchEvent(new CustomEvent('neko:yui-guide:plugin-dashboard-skip-bypass', {
                detail: {
                    enabled: enabled === true
                }
            }));
        }

        async runPluginDashboardPreviewScene(step, runId) {
            this.highlightChatWindow();
            const stepBubbleText = this.resolvePerformanceBubbleText(step && step.performance);
            let homeNarrationPromise = Promise.resolve();
            if (stepBubbleText) {
                const homeVoiceKey = (step && step.performance && step.performance.voiceKey)
                    || 'takeover_plugin_preview_home';
                this.appendGuideChatMessage(stepBubbleText, {
                    textKey: step && step.performance ? step.performance.bubbleTextKey : '',
                    voiceKey: homeVoiceKey
                });
                homeNarrationPromise = this.speakGuideLine(stepBubbleText, {
                    voiceKey: homeVoiceKey
                }).catch(() => {});
            }
            this.pluginDashboardWindowCreatedByGuide = false;
            let agentSwitchesRolledBack = false;
            const rollbackAgentSwitches = async () => {
                if (agentSwitchesRolledBack) {
                    return true;
                }
                const restoreResults = [];
                const restoreSwitch = async (label, action) => {
                    try {
                        const restored = await action();
                        if (restored !== true) {
                            console.warn('[YuiGuide] 恢复接管前开关状态失败:', label);
                            return false;
                        }
                        return true;
                    } catch (error) {
                        console.warn('[YuiGuide] 恢复接管前开关状态异常:', label, error);
                        return false;
                    }
                };
                restoreResults.push(await restoreSwitch('agent-master', () => this.setAgentMasterEnabled(false)));
                restoreResults.push(await restoreSwitch('computer_use_enabled', () => this.setAgentFlagEnabled('computer_use_enabled', false)));
                restoreResults.push(await restoreSwitch('user_plugin_enabled', () => this.setAgentFlagEnabled('user_plugin_enabled', false)));
                const restoredAll = restoreResults.every(Boolean);
                if (restoredAll) {
                    agentSwitchesRolledBack = true;
                }
                return restoredAll;
            };

            try {
                let launchResult = await this.runPluginDashboardLaunchSequence(
                    step,
                    (step && step.performance) || {},
                    runId
                );
                await homeNarrationPromise;
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }
                if (!launchResult || !launchResult.pluginDashboardWindow) {
                    return;
                }
                let dashboardWindow = launchResult.pluginDashboardWindow;
                this.setPluginDashboardSkipBypassEnabled(true);

                this.overlay.clearActionSpotlight();
                this.overlay.clearPersistentSpotlight();

                const dashboardText = this.resolveGuideCopy(
                    TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY,
                    TAKEOVER_PLUGIN_DASHBOARD_TEXT
                );
                const homeCursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                    ? this.overlay.getCursorPosition()
                    : null;
                this.cursor.hide();
                let pluginPanelClosed = false;
                const closePluginPreviewPanel = async () => {
                    if (pluginPanelClosed || runId !== this.sceneRunId || this.isStopping()) {
                        return;
                    }

                    pluginPanelClosed = true;
                    this.collapseAgentSidePanel('agent-user-plugin');
                    this.clearVirtualSpotlight('plugin-management-entry');
                    this.stopHoverElement(launchResult.pluginToggle || null);
                    await this.closeAgentPanel().catch(() => {});
                };
                const dashboardVoiceKey = 'takeover_plugin_preview_dashboard';
                this.appendGuideChatMessage(dashboardText, {
                    textKey: TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY,
                    voiceKey: dashboardVoiceKey
                });
                const dashboardAudioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                    ? this.voiceQueue.resolveGuideAudioSrc(dashboardVoiceKey)
                    : '';
                const dashboardNarrationDurationMs = this.getGuideVoiceDurationMs(dashboardVoiceKey, resolveGuideLocale())
                    || estimateSpeechDurationMs(dashboardText);
                const dashboardNarrationStartedAtMs = Date.now();
                const dashboardNarrationPromise = this.speakGuideLine(dashboardText, {
                    voiceKey: dashboardVoiceKey
                }).catch(() => {}).finally(() => {
                    if (!this.angryExitTriggered && runId === this.sceneRunId && !this.destroyed) {
                        this.notifyPluginDashboardNarrationFinished();
                    }
                    return closePluginPreviewPanel();
                });

                const pluginDashboardPerformancePromise = this.waitForPluginDashboardPerformance(dashboardWindow, {
                    line: dashboardText,
                    closeOnDone: false,
                    narrationDurationMs: dashboardNarrationDurationMs,
                    voiceKey: dashboardVoiceKey,
                    audioUrl: dashboardAudioUrl,
                    narrationStartedAtMs: dashboardNarrationStartedAtMs
                }).catch(() => {
                    return false;
                });
                await dashboardNarrationPromise;
                const pluginDashboardCompleted = await pluginDashboardPerformancePromise;
                await this.closePluginDashboardWindowIfCreatedByGuide('插件面板预览完成');
                if (this.pluginDashboardHandoff && this.pluginDashboardHandoff.windowRef === dashboardWindow && typeof this.pluginDashboardHandoff.resolve === 'function') {
                    this.pluginDashboardHandoff.resolve(!!pluginDashboardCompleted);
                }
                this.customSecondarySpotlightTarget = null;
                this.clearSceneExtraSpotlights();
                this.clearRetainedExtraSpotlights();
                this.overlay.clearActionSpotlight();
                // 恢复猫爪总开关和用户插件开关到接管前状态
                await rollbackAgentSwitches();
                const homeReady = await this.waitForHomeMainUIReady(3600);
                if (!homeReady) {
                    console.warn('[YuiGuide] 插件面板预览后主页 UI 未恢复，终止后续接管流程');
                    this.requestTermination('home_ui_not_ready', 'skip');
                    return;
                }
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }

                if (homeCursorPosition) {
                    this.cursor.showAt(homeCursorPosition.x, homeCursorPosition.y);
                }
                this.overlay.clearActionSpotlight();
            } finally {
                this.setPluginDashboardSkipBypassEnabled(false);
                await rollbackAgentSwitches();
            }
        }

        async runSettingsPeekScene(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            const settingsButton = this.resolveElement(performance.cursorTarget || step.anchor);
            const settingsSpotlightTarget = this.getFloatingButtonShell(settingsButton) || settingsButton;
            this.setSpotlightGeometryHint(settingsSpotlightTarget, {
                padding: 4,
                geometry: 'circle'
            });
            if (settingsSpotlightTarget) {
                this.addRetainedExtraSpotlight(settingsSpotlightTarget);
            }
            const introText = this.resolvePerformanceBubbleText(performance);
            await this.closeAgentPanel();
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            this.overlay.clearActionSpotlight();
            this.highlightChatWindow();

            if (introText) {
                const introVoiceKey = performance.voiceKey || 'takeover_settings_peek_intro';
                this.appendGuideChatMessage(introText, {
                    textKey: performance.bubbleTextKey || '',
                    voiceKey: introVoiceKey
                });
            }
            if (performance.emotion) {
                this.applyGuideEmotion(performance.emotion);
            }

            const waitWithWallClockTimeout = async (promise, timeoutMs, label) => {
                let timedOut = false;
                const normalizedTimeoutMs = Math.max(1000, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 1000));
                let timeoutId = 0;
                const timeoutPromise = new Promise((resolve) => {
                    timeoutId = window.setTimeout(() => {
                        timeoutId = 0;
                        timedOut = true;
                        console.warn('[YuiGuide] settings peek 等待超时，继续后续流程:', label || 'unknown');
                        resolve(null);
                    }, normalizedTimeoutMs);
                });
                try {
                    const result = await Promise.race([
                        Promise.resolve(promise).catch((error) => {
                            console.warn('[YuiGuide] settings peek 等待失败，继续后续流程:', label || 'unknown', error);
                            return null;
                        }),
                        timeoutPromise
                    ]);
                    return timedOut ? null : result;
                } finally {
                    if (timeoutId) {
                        window.clearTimeout(timeoutId);
                    }
                }
            };
            const introVoiceKey = performance.voiceKey || 'takeover_settings_peek_intro';
            const introVoiceDurationMs = this.getGuideVoiceDurationMs(introVoiceKey, resolveGuideLocale())
                || estimateSpeechDurationMs(introText || '');
            const introNarrationPromise = this.speakGuideLine(introText || '', {
                voiceKey: introVoiceKey
            }).catch((error) => {
                console.warn('[YuiGuide] 设置一瞥首句旁白失败，继续流程:', error);
            });
            if (!(await this.waitForNarrationCue(
                introVoiceKey,
                'openSettingsPanel'
            ))) {
                this.removeRetainedExtraSpotlight(settingsSpotlightTarget);
                this.overlay.clearActionSpotlight();
                this.highlightChatWindow();
                return;
            }
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            const openedSettings = settingsButton
                ? await this.performHighlightedApiClick({
                    target: settingsButton,
                    durationMs: 900,
                    runId: runId,
                    action: () => this.openSettingsPanel()
                })
                : await this.openSettingsPanel();
            if (!openedSettings || runId !== this.sceneRunId || this.isStopping()) {
                if (!openedSettings) {
                    this.removeRetainedExtraSpotlight(settingsSpotlightTarget);
                    this.overlay.clearActionSpotlight();
                    this.highlightChatWindow();
                }
                return;
            }

            const characterMenuReadyPromise = this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().characterMenu
            ], 1000);
            await waitWithWallClockTimeout(
                introNarrationPromise,
                Math.max(4200, introVoiceDurationMs + 1400),
                'settings_intro_narration'
            );
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            let settingsPeekHighlightsCleared = false;
            let settingsPanelClosed = false;
            const clearSettingsPeekHighlights = () => {
                if (settingsPeekHighlightsCleared) {
                    return;
                }

                settingsPeekHighlightsCleared = true;
                this.clearSceneExtraSpotlights();
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.removeRetainedExtraSpotlight(settingsSpotlightTarget);
                this.clearPreciseHighlights();
                this.customSecondarySpotlightTarget = null;
                this.overlay.clearActionSpotlight();
                if (!this.isStopping()) {
                    this.highlightChatWindow();
                }
            };
            const closeSettingsPeekPanel = async () => {
                if (settingsPanelClosed || runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }

                settingsPanelClosed = true;
                this.collapseCharacterSettingsSidePanel();
                await this.closeSettingsPanel().catch(() => {});
                this.forceHideManagedPanel('settings');
            };

            let characterMenu = await characterMenuReadyPromise;
            if (!characterMenu) {
                characterMenu = await this.waitForVisibleTarget([
                    () => this.getSettingsPeekTargets().characterMenu
                ], 400);
            }
            if (!characterMenu) {
                console.warn('[YuiGuide] 设置一瞥未找到角色设置入口，跳过细节展示');
                clearSettingsPeekHighlights();
                await closeSettingsPeekPanel();
                return;
            }

            const sidePanelReady = await this.ensureCharacterSettingsSidePanelVisible();

            let appearanceItem = null;
            let voiceCloneItem = null;
            const detailTargetTimeoutMs = sidePanelReady ? 900 : 1200;
            [appearanceItem, voiceCloneItem] = await Promise.all([
                this.waitForVisibleTarget([
                    () => this.getSettingsPeekTargets().appearanceItem
                ], detailTargetTimeoutMs),
                this.waitForVisibleTarget([
                    () => this.getSettingsPeekTargets().voiceCloneItem
                ], detailTargetTimeoutMs)
            ]);
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            const detailText = this.resolveGuideCopy(
                TAKEOVER_SETTINGS_DETAIL_TEXT_KEY,
                TAKEOVER_SETTINGS_DETAIL_TEXT
            );
            const detailTextPart1 = this.resolveGuideCopy(
                TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1_KEY,
                TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1
            );
            const detailTextPart2 = this.resolveGuideCopy(
                TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2_KEY,
                TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2
            );
            const detailVoiceKey = 'takeover_settings_peek_detail';
            const detailVoiceDurationMs = this.getGuideVoiceDurationMs(detailVoiceKey, resolveGuideLocale())
                || estimateSpeechDurationMs(detailText || '');
            const detailCueConfig = getGuideAudioCueConfig(detailVoiceKey);
            const secondLineCue = detailCueConfig && detailCueConfig.showSecondLine
                && Number.isFinite(detailCueConfig.showSecondLine.at)
                ? clamp(detailCueConfig.showSecondLine.at, 0.1, 0.9)
                : 0.54;
            const detailPart1StreamDurationMs = detailTextPart2
                ? Math.max(900, Math.round(detailVoiceDurationMs * secondLineCue))
                : detailVoiceDurationMs;
            const detailPart2StreamDurationMs = detailTextPart2
                ? Math.max(900, Math.round(detailVoiceDurationMs * (1 - secondLineCue)))
                : 0;
            this.appendGuideChatMessage(detailTextPart1 || detailText, {
                textKey: detailTextPart1
                    ? TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1_KEY
                    : TAKEOVER_SETTINGS_DETAIL_TEXT_KEY,
                voiceKey: detailVoiceKey,
                streamDurationMs: detailPart1StreamDurationMs
            });

            let settingsDetailSecondLineDisplayed = false;
            const appendSettingsDetailSecondLine = () => {
                if (
                    settingsDetailSecondLineDisplayed
                    || runId !== this.sceneRunId
                    || this.isStopping()
                    || !detailTextPart2
                ) {
                    return;
                }

                settingsDetailSecondLineDisplayed = true;
                this.appendGuideChatMessage(detailTextPart2, {
                    textKey: TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2_KEY,
                    voiceKey: detailVoiceKey,
                    streamDurationMs: detailPart2StreamDurationMs
                });
            };
            const narrationPromise = this.speakGuideLine(detailText, {
                voiceKey: detailVoiceKey
            }).catch((error) => {
                console.warn('[YuiGuide] 设置一瞥细节旁白失败，继续流程:', error);
            }).finally(() => {
                appendSettingsDetailSecondLine();
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }

                this.collapseCharacterSettingsSidePanel();
                clearSettingsPeekHighlights();
                return closeSettingsPeekPanel();
            });
            const guardedNarrationPromise = waitWithWallClockTimeout(
                narrationPromise,
                Math.max(5000, detailVoiceDurationMs + 1800),
                'settings_detail_narration'
            ).finally(() => {
                appendSettingsDetailSecondLine();
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }

                this.collapseCharacterSettingsSidePanel();
                clearSettingsPeekHighlights();
                return closeSettingsPeekPanel();
            });
            const secondLineDisplayPromise = (async () => {
                if (!(await this.waitForNarrationCue(
                    detailVoiceKey,
                    'showSecondLine'
                ))) {
                    return;
                }

                appendSettingsDetailSecondLine();
            })();
            const guardedSecondLineDisplayPromise = waitWithWallClockTimeout(
                secondLineDisplayPromise,
                Math.max(1800, Math.round(detailVoiceDurationMs * secondLineCue) + 1400),
                'settings_detail_second_line'
            );

            this.overlay.clearActionSpotlight();

            if (characterMenu) {
                this.applyGuideHighlights({ primary: characterMenu });
            }

            if (characterMenu && runId === this.sceneRunId && !this.isStopping()) {
                await this.moveCursorToElement(characterMenu, 900);
            }
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            let settingsButtonTarget = null;
            let characterChildrenBundle = null;
            ({
                settingsButton: settingsButtonTarget,
                characterMenu,
                appearanceItem,
                voiceCloneItem,
                characterChildrenBundle
            } = this.refreshSettingsPeekSpotlights(settingsButton));
            if (!characterMenu) {
                console.warn('[YuiGuide] 设置一瞥角色入口消失，跳过细节展示');
                clearSettingsPeekHighlights();
                await closeSettingsPeekPanel();
                return;
            }

            const sidePanel = this.getCharacterSettingsSidePanel();
            const panelRect = sidePanel && this.isElementVisible(sidePanel) ? this.getElementRect(sidePanel) : null;
            const itemUnionRect = unionRects([
                this.getElementRect(appearanceItem),
                this.getElementRect(voiceCloneItem)
            ]);
            const fallbackRect = this.getElementRect(characterChildrenBundle)
                || this.getElementRect(characterMenu)
                || this.getElementRect(settingsButtonTarget);
            const motionRect = panelRect || itemUnionRect || fallbackRect;
            const centerX = motionRect
                ? motionRect.left + motionRect.width / 2
                : window.innerWidth / 2;
            const centerY = motionRect
                ? motionRect.top + motionRect.height / 2
                : window.innerHeight / 2;
            const radiusX = panelRect
                ? panelRect.width / 2 * 1.4
                : (itemUnionRect ? Math.max(90, itemUnionRect.width / 2 * 1.3) : 90);
            const radiusY = panelRect
                ? panelRect.height / 2 * 1.4
                : (itemUnionRect ? Math.max(60, itemUnionRect.height / 2 * 1.3) : 60);
            if (motionRect) {
                while (!this.isStopping()) {
                    const movedToCenter = await this.cursor.moveToPoint(centerX, centerY, {
                        durationMs: 700,
                        pauseCheck: () => this.scenePausedForResistance,
                        cancelCheck: () => this.isStopping()
                    });
                    if (movedToCenter) {
                        break;
                    }
                    if (!this.scenePausedForResistance) {
                        break;
                    }
                    await this.waitUntilSceneResumed();
                }
            }
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            const cycleMs = 5600;
            const ellipseAbortCheck = () => this.destroyed || this.angryExitTriggered || settingsPeekHighlightsCleared;
            const actionPromise = (async () => {
                while (runId === this.sceneRunId && !ellipseAbortCheck()) {
                    const moved = await this.cursor.runPauseAwareEllipse(
                        centerX,
                        centerY,
                        radiusX,
                        radiusY,
                        cycleMs,
                        ellipseAbortCheck,
                        () => this.scenePausedForResistance,
                        () => this.isStopping()
                    );
                    if (!moved && this.isStopping()) {
                        return;
                    }
                    if (!moved) {
                        if (ellipseAbortCheck()) {
                            return;
                        }
                        if (!this.scenePausedForResistance) {
                            return;
                        }
                        await this.waitUntilSceneResumed();
                    }
                }
            })();

            await Promise.all([guardedNarrationPromise, actionPromise, guardedSecondLineDisplayPromise]);
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }
            this.cleanupTutorialReturnButtons();
            clearSettingsPeekHighlights();
            // 恢复隐藏角色设置侧面板（通用设置 / 角色外形 / 声音克隆）
            await closeSettingsPeekPanel();
        }

        beginTerminationVisualCleanup() {
            this.sceneRunId += 1;
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
            this.clearIntroFlow();
            this.voiceQueue.stop();
            this.clearAllVirtualSpotlights();
            this.clearPreciseHighlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.overlay.setTakingOver(false);
            this.overlay.clearSpotlight();
            this.collapseCharacterSettingsSidePanel();
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

        async runTakeoverMainFlow() {
            if (this.takeoverFlowStarted || this.isStopping()) {
                return this.takeoverFlowPromise;
            }

            this.takeoverFlowStarted = true;
            this.takeoverFlowPromise = (async () => {
                this.takeoverOriginalAgentSwitches = await this.getAgentSwitchSnapshot();
                await this.playManagedScene('takeover_capture_cursor', {
                    source: 'auto-takeover'
                });
                if (this.isStopping()) {
                    return;
                }

                await wait(360);
                if (this.isStopping()) {
                    return;
                }

                await this.playManagedScene('takeover_plugin_preview', {
                    source: 'auto-takeover'
                });
                if (this.isStopping()) {
                    return;
                }

                await wait(120);
                if (this.isStopping()) {
                    return;
                }

                await this.playManagedScene('takeover_settings_peek', {
                    source: 'auto-takeover'
                });
                if (this.isStopping()) {
                    return;
                }

                await wait(120);
                if (this.isStopping()) {
                    return;
                }

                await this.playManagedScene('takeover_return_control', {
                    source: 'auto-takeover'
                });
                this.takeoverFlowCompleted = true;
                this.takeoverOriginalAgentSwitches = null;
                if (this.isStopping()) {
                    return;
                }
                this.requestTermination('complete', 'complete');
            })().catch((error) => {
                console.error('[YuiGuide] 接管主流程执行失败:', error);
                this.takeoverOriginalAgentSwitches = null;
            });

            return this.takeoverFlowPromise;
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
                const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
                if (channel && typeof channel.postMessage === 'function') {
                    try {
                        channel.postMessage({
                            action: 'yui_guide_update_chat_message',
                            messageId: messageId,
                            patch: patch,
                            timestamp: Date.now()
                        });
                    } catch (error) {
                        console.warn('[YuiGuide] 更新独立聊天窗教程消息失败:', error);
                    }
                }
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
                return resolveGuideChatStreamSyncDurationMs(voiceDurationMs);
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

            let index = 0;
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
                const nextIndex = Math.min(total, Math.ceil(progress * total));
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

            const streamingMessage = this.cloneGuideChatMessageWithText(message, '', 'streaming');
            streamingMessage.actions = undefined;

            // Electron Pet 模式下首页聊天被拆到独立 /chat 窗口，这里优先通过
            // BroadcastChannel 把教程消息转发过去；只有转发失败时才回落到 overlay。
            if (this.isHomeChatExternalized()) {
                const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
                if (channel && typeof channel.postMessage === 'function') {
                    try {
                        channel.postMessage({
                            action: 'yui_guide_append_chat_message',
                            message: streamingMessage,
                            timestamp: createdAt
                        });
                        this.streamGuideChatMessage(message, content, normalizedOptions);
                        return message;
                    } catch (error) {
                        console.warn('[YuiGuide] 转发教程消息到独立聊天窗失败:', error);
                    }
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
                this.streamGuideChatMessage(message, content, normalizedOptions);
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
                this.setExternalizedChatSpotlight('window');
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
            await this.speakGuideLine(greetingReplyText, {
                voiceKey: 'intro_greeting_reply'
            });
        }

        async playRemainingIntroPreludeScenes(completedSceneId) {
            const completed = typeof completedSceneId === 'string' ? completedSceneId : '';
            const sceneIds = this.getPreludeSceneIds();
            if (!Array.isArray(sceneIds) || sceneIds.length === 0) {
                return true;
            }

            for (let index = 0; index < sceneIds.length; index += 1) {
                const sceneId = sceneIds[index];
                if (typeof sceneId !== 'string' || !sceneId || sceneId === completed) {
                    continue;
                }
                if (this.isStopping()) {
                    return false;
                }

                await this.playManagedScene(sceneId, {
                    source: 'prelude'
                });
                if (this.isStopping()) {
                    return false;
                }
            }

            return true;
        }

        async runWakeupPrelude() {
            if (this.page !== 'home' || this.isStopping() || !this.wakeup || typeof this.wakeup.run !== 'function') {
                if (typeof document !== 'undefined' && document.body) {
                    document.body.classList.remove('yui-guide-live2d-preparing');
                }
                return;
            }

            this.applyTutorialFaceForwardLock();
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
        }

        async runChatIntroPrelude() {
            if (this.introFlowStarted || this.isStopping()) {
                return;
            }

            const introStep = this.getStep('intro_basic');
            if (!introStep || !introStep.performance) {
                return;
            }

            // Electron Pet 模式：聊天 overlay 被 preload 永久隐藏，因此跳过首页输入框激活；
            // 但后续旁白、语音控制按钮演示和接管主流程仍继续执行。
            if (this.isHomeChatExternalized()) {
                await this.runChatIntroPreludeExternalized(introStep);
                return;
            }

            this.introFlowStarted = true;
            this.setCurrentScene('intro_basic', null);
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();
            await this.ensureChatVisible();
            if (this.isStopping()) {
                return;
            }
            this.focusAndHighlightChatInput(this.getChatInputTarget());

            // Ghost cursor 出现 + 气泡引导用户点击输入框（解锁 autoplay）
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
                // 将气泡定位到输入框正上方
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
                this.awaitingIntroActivation = true;
                await new Promise((resolve) => {
                    this._introActivationResolve = resolve;
                });
                this._introActivationResolve = null;
                if (this.isStopping()) {
                    return;
                }
                this.overlay.hideBubble();
                this.overlay.setTakingOver(true);
                this.cursor.wobble();
                await wait(200);
            }
            if (this.isStopping()) {
                return;
            }

            this.enableInterrupts(introStep);
            await this.playIntroGreetingReply();
            if (this.isStopping()) {
                return;
            }

            const introText = this.resolvePerformanceBubbleText(introStep.performance);
            this.appendGuideChatMessage(introText, {
                textKey: introStep.performance.bubbleTextKey || '',
                voiceKey: introStep.performance.voiceKey
            });
            if (introStep.performance.emotion) {
                this.applyGuideEmotion(introStep.performance.emotion);
            }
            await Promise.all([
                this.speakGuideLine(introText, {
                    voiceKey: introStep.performance.voiceKey,
                    minDurationMs: 4200
                }),
                this.runIntroVoiceControlButtonShowcase(
                    introStep.performance.voiceKey,
                    introText
                ).catch(() => {})
            ]);
            if (this.isStopping()) {
                return;
            }

            const introScenesCompleted = await this.playRemainingIntroPreludeScenes('intro_basic');
            if (!introScenesCompleted) {
                return;
            }

            await wait(240);
            if (this.isStopping()) {
                return;
            }
            this.introFlowCompleted = true;
            this.overlay.clearActionSpotlight();
            await this.runTakeoverMainFlow();
        }

        // Electron Pet 模式专用 prelude：聊天输入框不在首页窗口里，
        // 因此跳过首页点击激活，但后续旁白与高亮演示照常执行。
        async runChatIntroPreludeExternalized(introStep) {
            this.introFlowStarted = true;
            this.setCurrentScene('intro_basic', null);
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();
            this.setExternalizedChatSpotlight('window');

            this.enableInterrupts(introStep);
            await this.playIntroGreetingReply();
            if (this.isStopping()) {
                return;
            }

            const introText = this.resolvePerformanceBubbleText(introStep.performance);
            if (introText) {
                this.appendGuideChatMessage(introText, {
                    textKey: introStep.performance.bubbleTextKey || '',
                    voiceKey: introStep.performance.voiceKey
                });
            }
            if (introStep.performance.emotion) {
                this.applyGuideEmotion(introStep.performance.emotion);
            }
            await Promise.all([
                this.speakGuideLine(introText || '', {
                    voiceKey: introStep.performance.voiceKey,
                    minDurationMs: 4200
                }),
                this.runIntroVoiceControlButtonShowcase(
                    introStep.performance.voiceKey,
                    introText || ''
                ).catch(() => {})
            ]);
            if (this.isStopping()) {
                return;
            }

            const introScenesCompleted = await this.playRemainingIntroPreludeScenes('intro_basic');
            if (!introScenesCompleted) {
                return;
            }

            this.introFlowCompleted = true;
            if (this.isStopping()) {
                return;
            }
            this.overlay.clearActionSpotlight();
            await this.runTakeoverMainFlow();
        }

        async startPrelude() {
            await syncGuideI18nLanguage(5000);
            const preludeSceneIds = this.getPreludeSceneIds();
            if (!Array.isArray(preludeSceneIds) || preludeSceneIds.length === 0) {
                return;
            }

            const firstSceneId = preludeSceneIds[0];
            if (firstSceneId === 'intro_basic' && this.page === 'home') {
                await this.runWakeupPrelude();
                if (this.isStopping()) {
                    return;
                }
                await this.runChatIntroPrelude();
                return;
            }

            await this.playScene(firstSceneId, {
                source: 'prelude'
            });
        }

        async enterStep(stepId, context) {
            if (this.destroyed || !stepId) {
                return;
            }

            if (this.takeoverFlowStarted && stepId.indexOf('takeover_') === 0) {
                this.setCurrentScene(stepId, context || null);
                return;
            }

            await this.playManagedScene(stepId, {
                source: (context && context.source) || 'step-enter',
                context: context || null
            });
        }

        async leaveStep(stepId) {
            if (this.destroyed) {
                return;
            }

            if (stepId && this.currentSceneId && stepId !== this.currentSceneId) {
                return;
            }

            this.clearSceneTimers();
            this.disableInterrupts();
            this.customSecondarySpotlightTarget = null;
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();

            if (stepId === 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
            }

            if (stepId === 'takeover_capture_cursor' || stepId === 'takeover_plugin_preview') {
                this.clearVirtualSpotlight('plugin-management-entry');
            }

            if (stepId === 'takeover_settings_peek') {
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
            }
        }

        async playScene(stepId, meta) {
            const step = this.getStep(stepId);
            if (!step) {
                return;
            }

            const runId = ++this.sceneRunId;
            const performance = step.performance || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);
            const anchorRect = this.resolveRect(step.anchor);
            const cursorTargetRect = this.resolveRect(performance.cursorTarget || step.anchor);
            const isTakeoverScene = stepId.indexOf('takeover_') === 0 || stepId.indexOf('interrupt_') === 0;
            const cursorSpeed = Number.isFinite(performance.cursorSpeedMultiplier) ? performance.cursorSpeedMultiplier : 1;
            const delayMs = Number.isFinite(performance.delayMs) ? performance.delayMs : DEFAULT_STEP_DELAY_MS;
            const durationMs = clamp(Math.round(DEFAULT_CURSOR_DURATION_MS / Math.max(0.35, cursorSpeed)), 160, 900);
            const spotlightElement = this.resolveElement(performance.cursorTarget || step.anchor);
            const shouldNarrateInChat = this.shouldNarrateInChat(stepId);
            const shouldNarrateAfterMove = (
                stepId === 'takeover_capture_cursor'
                || stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
            );
            const shouldNarrateDuringMove = stepId === 'takeover_capture_cursor';
            const shouldKeepInterruptsEnabled = performance.interruptible !== false && isTakeoverScene;
            const shouldOpenPanelAfterNarration = (
                stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
            );

            this.clearSceneTimers();
            this.overlay.setAngry(false);
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            this.clearVirtualSpotlight('takeover-agent-master-toggle');
            this.clearVirtualSpotlight('takeover-keyboard-toggle');
            if (stepId !== 'takeover_capture_cursor' && stepId !== 'takeover_plugin_preview') {
                this.clearRetainedExtraSpotlights();
            }

            if (isTakeoverScene) {
                this.overlay.setTakingOver(true);
            }

            const persistentSpotlightTarget = this.getSceneSpotlightTarget(stepId, performance);
            if (stepId === 'takeover_return_control') {
                this.overlay.clearPersistentSpotlight();
            } else if (persistentSpotlightTarget) {
                this.applyCircularFloatingButtonSpotlightHint(persistentSpotlightTarget);
                this.overlay.setPersistentSpotlight(persistentSpotlightTarget);
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(stepId, performance);
            if (actionSpotlightTarget) {
                this.applyCircularFloatingButtonSpotlightHint(actionSpotlightTarget);
            }
            if (actionSpotlightTarget) {
                this.overlay.activateSpotlight(actionSpotlightTarget);
            } else {
                this.overlay.clearActionSpotlight();
            }

            if (stepId !== 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
            }

            if (stepId === 'takeover_capture_cursor') {
                this.clearVirtualSpotlight('plugin-management-entry');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.overlay.hideBubble();
                this.highlightChatWindow();
                this.enableInterrupts(step);

                if (bubbleText) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || '',
                        voiceKey: performance.voiceKey
                    });
                }
                if (performance.emotion) {
                    this.applyGuideEmotion(performance.emotion);
                }

                await Promise.all([
                    this.speakGuideLine(bubbleText || '', {
                        voiceKey: performance.voiceKey,
                        minDurationMs: 4000
                    }).catch(() => {}),
                    this.runTakeoverKeyboardControlSequence(step, performance, runId)
                ]);
                this.clearRetainedExtraSpotlights();
                this.clearVirtualSpotlight('takeover-agent-master-toggle');
                this.clearVirtualSpotlight('takeover-keyboard-toggle');
                this.overlay.clearActionSpotlight();
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.waitForSceneDelay(DEFAULT_SCENE_SETTLE_MS);
                return;
            }

            if (stepId === 'takeover_plugin_preview') {
                this.clearVirtualSpotlight('plugin-management-entry');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.overlay.hideBubble();
                this.enableInterrupts(step);
                await this.runPluginDashboardPreviewScene(step, runId);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }
                return;
            }

            if (stepId === 'takeover_settings_peek') {
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.overlay.hideBubble();
                this.enableInterrupts(step);
                await this.runSettingsPeekScene(step, performance, runId);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }
                return;
            }

            if (bubbleText && !shouldNarrateAfterMove && !shouldNarrateInChat) {
                this.showGuideBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                }, stepId);
            } else if (!shouldNarrateAfterMove) {
                this.overlay.hideBubble();
            }

            if (performance.emotion && !shouldNarrateAfterMove) {
                this.applyGuideEmotion(performance.emotion);
            }

            const shouldIntroduceCursor = stepId === 'takeover_capture_cursor' && !this.cursor.hasPosition();
            if (shouldIntroduceCursor) {
                const origin = this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
                await this.waitForSceneDelay(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.wobble();
                await this.waitForSceneDelay(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.wobble();
                await this.waitForSceneDelay(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            if (cursorTargetRect && !this.cursor.hasPosition()) {
                const origin = this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
            }

            if (delayMs > 0) {
                await this.waitForSceneDelay(delayMs);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            let narrationPromise = null;
            if (shouldKeepInterruptsEnabled && shouldNarrateDuringMove) {
                this.enableInterrupts(step);
            }

            if (bubbleText && shouldNarrateDuringMove && !shouldNarrateInChat) {
                this.showGuideBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                }, stepId);
            }

            if (performance.emotion && shouldNarrateDuringMove) {
                this.applyGuideEmotion(performance.emotion);
            }

            if (shouldNarrateDuringMove) {
                if (bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || '',
                        voiceKey: performance.voiceKey
                    });
                    this.overlay.hideBubble();
                }
                narrationPromise = this.speakGuideLine(bubbleText || '', {
                    voiceKey: performance.voiceKey
                });
            }

            const shouldMoveCursor = (
                stepId === 'takeover_capture_cursor'
                || stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
                || (!shouldIntroduceCursor || stepId !== 'takeover_capture_cursor')
            );
            if (shouldMoveCursor && (performance.cursorAction === 'move' || performance.cursorAction === 'click' || performance.cursorAction === 'wobble')) {
                if (cursorTargetRect) {
                    const movePromise = this.cursor.moveToRect(cursorTargetRect, {
                        durationMs: durationMs,
                        pauseCheck: () => this.scenePausedForResistance,
                        cancelCheck: () => this.isStopping()
                    });
                    if (narrationPromise) {
                        await Promise.all([movePromise, narrationPromise]);
                    } else {
                        await movePromise;
                    }
                    if (runId !== this.sceneRunId || this.destroyed) {
                        return;
                    }
                }
            } else if (narrationPromise) {
                await narrationPromise;
            }

            if (shouldKeepInterruptsEnabled && !shouldNarrateDuringMove) {
                this.enableInterrupts(step);
            } else if (!shouldKeepInterruptsEnabled) {
                this.disableInterrupts();
            }

            if (bubbleText && shouldNarrateAfterMove && !shouldNarrateInChat) {
                this.showGuideBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                }, stepId);
            } else if (shouldNarrateAfterMove) {
                this.overlay.hideBubble();
            }

            if (performance.emotion && shouldNarrateAfterMove) {
                this.applyGuideEmotion(performance.emotion);
            }

            if (!shouldNarrateDuringMove) {
                if (bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || '',
                        voiceKey: performance.voiceKey
                    });
                    this.overlay.hideBubble();
                }
                await this.speakGuideLine(bubbleText || '', {
                    voiceKey: performance.voiceKey
                });
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            if (performance.cursorAction === 'click' && !shouldOpenPanelAfterNarration) {
                await this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            } else if (performance.cursorAction === 'wobble') {
                this.cursor.wobble();
            }

            if (stepId === 'takeover_return_control') {
                await this.closeManagedPanels();
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.overlay.clearPersistentSpotlight();
                this.overlay.clearActionSpotlight();
                const centerPoint = this.getViewportCenter();
                await this.waitForSceneDelay(140);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                if (!this.cursor.hasPosition()) {
                    this.cursor.showAt(centerPoint.x, centerPoint.y);
                } else {
                    while (!this.isStopping()) {
                        const movedToCenterPoint = await this.cursor.moveToPoint(centerPoint.x, centerPoint.y, {
                            durationMs: 360,
                            pauseCheck: () => this.scenePausedForResistance,
                            cancelCheck: () => this.isStopping()
                        });
                        if (movedToCenterPoint) {
                            break;
                        }
                        if (!this.scenePausedForResistance) {
                            break;
                        }
                        await this.waitUntilSceneResumed();
                    }
                }
                this.cursor.wobble();
                await this.waitForSceneDelay(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.hide();
                this.overlay.clearActionSpotlight();
                this.disableInterrupts();
                this.overlay.setTakingOver(false);
            }

            if (!shouldKeepInterruptsEnabled) {
                this.disableInterrupts();
            }

            if (step && step.navigation && step.navigation.openUrl) {
                const opened = await this.openPageWithHandoff(stepId, step);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }

                if (opened) {
                    this.requestTermination('complete', 'complete');
                } else {
                    console.warn('[YuiGuide] handoff 打开失败，保留当前教程上下文:', stepId, step.navigation.openUrl);
                }
                return;
            }

            await this.waitForSceneDelay(DEFAULT_SCENE_SETTLE_MS);
            if (runId !== this.sceneRunId || this.destroyed) {
                return;
            }

            if (meta && meta.source === 'prelude') {
                this.schedule(() => {
                    if (!this.currentSceneId && !this.destroyed) {
                        this.overlay.hideBubble();
                    }
                }, 2600);
            }
        }

        onPointerMove(event) {
            this.handleInterrupt(event);
        }

        onPointerDown(event) {
            if (!event || event.isTrusted === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            this.lastPointerPoint = {
                x: x,
                y: y,
                t: Date.now(),
                speed: 0
            };
            this.interruptAccelerationStreak = 0;
        }

        handleInterrupt(event) {
            if (
                this.destroyed
                || this.angryExitTriggered
                || this.scenePausedForResistance
                || !this.interruptsEnabled
                || !event
                || event.isTrusted === false
            ) {
                return;
            }

            const step = this.currentStep;
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            if (!this.shouldAllowInterruptDuringCurrentScene()) {
                return;
            }

            if (this.page === 'home' && typeof document.hasFocus === 'function' && !document.hasFocus()) {
                return;
            }

            if (event.type === 'mousemove') {
                const movementX = Number.isFinite(event.movementX) ? event.movementX : null;
                const movementY = Number.isFinite(event.movementY) ? event.movementY : null;
                if (movementX !== null && movementY !== null && Math.hypot(movementX, movementY) <= 0) {
                    return;
                }
            }

            const now = Date.now();
            const previousPoint = this.lastPointerPoint;
            if (!previousPoint || !Number.isFinite(previousPoint.t)) {
                this.lastPointerPoint = {
                    x: x,
                    y: y,
                    t: now,
                    speed: 0
                };
                this.interruptAccelerationStreak = 0;
                return;
            }

            const dx = x - previousPoint.x;
            const dy = y - previousPoint.y;
            const distance = Math.hypot(dx, dy);
            const dt = Math.max(1, now - previousPoint.t);
            const speed = distance / dt;
            const previousSpeed = Number.isFinite(previousPoint.speed) ? previousPoint.speed : 0;
            const acceleration = (speed - previousSpeed) / dt;

            this.lastPointerPoint = {
                x: x,
                y: y,
                t: now,
                speed: speed
            };

            this.noteUserCursorRevealAttempt(distance, now);
            this.maybePlayPassiveResistance(x, y, distance, speed, now);

            if (distance < DEFAULT_INTERRUPT_DISTANCE) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            if (speed < DEFAULT_INTERRUPT_SPEED_THRESHOLD) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            if (acceleration < DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            this.interruptAccelerationStreak += 1;
            if (this.interruptAccelerationStreak < DEFAULT_INTERRUPT_ACCELERATION_STREAK) {
                return;
            }
            this.interruptAccelerationStreak = 0;

            const throttleMs = Number.isFinite(interrupts.throttleMs) ? interrupts.throttleMs : 500;
            if (now - this.lastInterruptAt < throttleMs) {
                return;
            }
            this.lastInterruptAt = now;

            this.interruptCount += 1;
            const threshold = Number.isFinite(interrupts.threshold) ? interrupts.threshold : 3;

            if (this.interruptCount >= threshold) {
                this.abortAsAngryExit('pointer_interrupt');
                return;
            }

            this.playLightResistance(x, y);
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

        playLightResistance(x, y, options) {
            if (this.scenePausedForResistance) {
                return Promise.resolve();
            }

            const suppressCursorReaction = !!(options && options.suppressCursorReaction);
            const suppressCursorReveal = !!(options && options.suppressCursorReveal);

            if (!suppressCursorReveal) {
                if (this.userCursorRevealed) {
                    this.revealUserCursor();
                } else {
                    if (this.resistanceCursorTimer) {
                        window.clearTimeout(this.resistanceCursorTimer);
                    }
                    this.restoreHiddenCursorAfterResistance = document.body.classList.contains('yui-taking-over')
                        || document.documentElement.style.cursor === 'none'
                        || document.body.style.cursor === 'none';
                    document.documentElement.style.cursor = '';
                    document.body.style.cursor = '';
                    document.body.classList.add('yui-resistance-cursor-reveal');
                    this.resistanceCursorTimer = window.setTimeout(() => {
                        this.resistanceCursorTimer = null;
                        document.body.classList.remove('yui-resistance-cursor-reveal');
                        if (!this.destroyed && !this.angryExitTriggered && this.restoreHiddenCursorAfterResistance) {
                            document.documentElement.style.cursor = 'none';
                            document.body.style.cursor = 'none';
                        }
                        this.restoreHiddenCursorAfterResistance = false;
                    }, 3000);
                }
            }

            const resistanceStep = this.getStep('interrupt_resist_light');
            if (!resistanceStep) {
                return Promise.resolve();
            }

            const performance = resistanceStep.performance || {};
            const voices = this.resolvePerformanceResistanceVoices(performance);
            const resistanceVoiceKeys = ['interrupt_resist_light_1', 'interrupt_resist_light_3'];
            const resistanceVoiceIndex = Math.max(0, Math.min(resistanceVoiceKeys.length - 1, this.interruptCount - 1));
            const defaultResistanceText = this.resolvePerformanceBubbleText(performance);
            const message = voices.length > 0
                ? voices[(this.interruptCount - 1) % voices.length]
                : defaultResistanceText || '不要拽我啦，还没结束呢！';
            const presentationSnapshot = this.captureCurrentGuidePresentationSnapshot();

            this.pauseCurrentSceneForResistance();
            this.interruptNarrationForResistance();

            this.overlay.hideBubble();
            this.appendGuideChatMessage(message, {
                textKey: resistanceVoiceKeys[resistanceVoiceIndex] === 'interrupt_resist_light_3'
                    ? 'tutorial.yuiGuide.lines.interruptResistLight3'
                    : 'tutorial.yuiGuide.lines.interruptResistLight1',
                voiceKey: resistanceVoiceKeys[resistanceVoiceIndex] || '',
                streamPauseWithScene: false
            });
            this.applyGuideEmotion(performance.emotion || 'surprised');
            const cursorResistancePromise = suppressCursorReaction
                ? Promise.resolve()
                : this.cursor.resistTo(x, y);
            const resistanceVoiceKey = resistanceVoiceKeys[resistanceVoiceIndex] || '';
            return Promise.all([
                this.voiceQueue.speak(message, {
                    voiceKey: resistanceVoiceKey
                }),
                cursorResistancePromise
            ]).finally(() => {
                this.resumeCurrentSceneAfterResistance();
                const didRestorePresentationSnapshot = this.restoreGuidePresentationSnapshot(presentationSnapshot);
                const narration = this.activeNarration;
                if (narration && narration.interrupted) {
                    this.scheduleNarrationResume({
                        skipEmotion: didRestorePresentationSnapshot
                    });
                    return;
                }

                this.restoreCurrentScenePresentation({
                    skipEmotion: didRestorePresentationSnapshot
                });
            });
        }

        async abortAsAngryExit(source) {
            if (this.destroyed || this.angryExitTriggered) {
                return;
            }

            this.recordExperienceMetric('angry_exit', {
                sceneId: this.currentSceneId || 'interrupt_angry_exit',
                reason: source || 'pointer_interrupt',
                interruptCount: Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0))
            });
            this.angryExitTriggered = true;
            this.clearSceneTimers();
            this.disableInterrupts();
            this.cancelActiveNarration();

            const angryStep = this.getStep('interrupt_angry_exit');
            const performance = (angryStep && angryStep.performance) || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);

            this.overlay.setTakingOver(true);
            this.overlay.setAngry(true);
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.appendGuideChatMessage(bubbleText || '人类~~~~！你真的很没礼貌喵！', {
                textKey: performance.bubbleTextKey || '',
                voiceKey: performance.voiceKey,
                streamPauseWithScene: false,
                streamAllowDuringAngryExit: true
            });
            this.applyGuideEmotion(performance.emotion || 'angry');
            await this.speakGuideLine(bubbleText || '', {
                voiceKey: performance.voiceKey
            });
            this.notifyPluginDashboardNarrationFinished();
            if (this.destroyed) {
                return;
            }

            this.requestTermination(source || 'angry_exit', 'angry_exit');
        }

        requestTermination(reason, tutorialReason) {
            if (this.destroyed || this.terminationRequested) {
                return;
            }

            this.terminationRequested = true;
            this.beginTerminationVisualCleanup();
            const finalReason = tutorialReason || reason || 'skip';
            this.notifyPluginDashboardTerminationRequested(finalReason);
            this.closePluginDashboardWindowIfCreatedByGuide('终止请求').catch((error) => {
                console.warn('[YuiGuide] 终止请求时关闭插件面板失败:', error);
            });
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialDestroy === 'function') {
                this.tutorialManager.requestTutorialDestroy(finalReason);
            } else {
                this.destroy();
            }
        }

        skip(reason, tutorialReason) {
            this.recordExperienceMetric('skip', {
                reason: reason || 'skip',
                tutorialReason: tutorialReason || reason || 'skip'
            });
            this.requestTermination(reason, tutorialReason);
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.terminationRequested = true;
            this.releaseTutorialFaceForwardLock();
            this.resumeCurrentSceneAfterResistance();
            this.clearExternalizedChatFx();
            this.setExternalizedChatButtonsDisabled(false);
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
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
            if (this.wakeup && typeof this.wakeup.destroy === 'function') {
                this.wakeup.destroy();
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
            this.clearPreciseHighlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            this.clearGuidePresentation();
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
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.overlay.setTakingOver(false);
            this.overlay.destroy();
            window.removeEventListener('keydown', this.keydownHandler, true);
            window.removeEventListener('pagehide', this.pageHideHandler, true);
            window.removeEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.removeEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.removeEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.removeEventListener('message', this.messageHandler, true);
            document.removeEventListener('pointerdown', this.interactionGuardHandler, true);
            document.removeEventListener('pointerup', this.interactionGuardHandler, true);
            document.removeEventListener('mousedown', this.interactionGuardHandler, true);
            document.removeEventListener('mouseup', this.interactionGuardHandler, true);
            document.removeEventListener('touchstart', this.interactionGuardHandler, true);
            document.removeEventListener('touchend', this.interactionGuardHandler, true);
            document.removeEventListener('click', this.interactionGuardHandler, true);
            document.removeEventListener('dblclick', this.interactionGuardHandler, true);
            document.removeEventListener('contextmenu', this.interactionGuardHandler, true);
            document.removeEventListener('pointerdown', this.skipButtonClickHandler, true);
            document.removeEventListener('mousedown', this.skipButtonClickHandler, true);
            document.removeEventListener('touchstart', this.skipButtonClickHandler, true);
            document.removeEventListener('click', this.skipButtonClickHandler, true);
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
            this.destroy();
        }

        isAllowedTutorialInteractionTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            if (target.closest('#neko-tutorial-skip-btn')) {
                return true;
            }

            if (this.awaitingIntroActivation) {
                const chatInput = target.closest('#react-chat-window-root .composer-input')
                    || target.closest('#textInputBox');
                if (chatInput) {
                    this.awaitingIntroActivation = false;
                    if (typeof this._introActivationResolve === 'function') {
                        this._introActivationResolve();
                        this._introActivationResolve = null;
                    }
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

        onInteractionGuard(event) {
            if (this.destroyed || this.page !== 'home' || !event || event.isTrusted === false) {
                return;
            }

            if (
                this.isAllowedTutorialInteractionTarget(event.target)
                || this.isSystemDialogInteractionTarget(event.target)
            ) {
                return;
            }

            if (typeof event.preventDefault === 'function') {
                event.preventDefault();
            }
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();
        }

        onSkipButtonClick(event) {
            if (this.destroyed || !event || !event.target || typeof event.target.closest !== 'function') {
                return;
            }

            const skipButton = event.target.closest('#neko-tutorial-skip-btn');
            if (!skipButton) {
                return;
            }

            if (typeof event.preventDefault === 'function') {
                event.preventDefault();
            }
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();
            this.skip('skip', 'skip');
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
                if (!windowRef || windowRef.closed) {
                    return;
                }

                try {
                    windowRef.postMessage({
                        type: PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
                        sessionId: typeof data.sessionId === 'string' ? data.sessionId : '',
                        requestId: requestId
                    }, targetOrigin);
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
                postAck();
                await this.abortAsAngryExit('pointer_interrupt');
                return;
            }

            if (kind === 'interrupt_resist_light' && x !== null && y !== null) {
                try {
                    await this.playLightResistance(x, y, {
                        suppressCursorReaction: true,
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
                this.skip('skip', 'skip');
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
