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
    const DEFAULT_STEP_DELAY_MS = 120;
    const DEFAULT_SCENE_SETTLE_MS = 260;
    const DEFAULT_CURSOR_DURATION_MS = 520;
    const INTRO_PRACTICE_TEXT = '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～';
    const INTRO_PRACTICE_TEXT_KEY = 'tutorial.yuiGuide.lines.introPractice';
    const INTRO_HELLO_ACTION_ID = 'yui-guide-intro-hello';
    const INTRO_GREETING_REPLY_TEXT = '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！';
    const INTRO_GREETING_REPLY_TEXT_KEY = 'tutorial.yuiGuide.lines.introGreetingReply';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT = '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～';
    const TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard';
    const TAKEOVER_SETTINGS_DETAIL_TEXT = '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！';
    const TAKEOVER_SETTINGS_DETAIL_TEXT_KEY = 'tutorial.yuiGuide.lines.takeoverSettingsPeekDetail';
    const INTRO_SKIP_ACTION_ID = 'yui-guide-intro-skip-chat';
    const DEFAULT_SPOTLIGHT_PADDING = 6;
    const INTRO_SKIP_LABEL_KEY = 'tutorial.yuiGuide.buttons.skipChat';
    const INTRO_HELLO_LABEL_KEY = 'tutorial.yuiGuide.buttons.sayHello';
    const REACT_CHAT_ACTION_EVENT = 'react-chat-window:action';
    const REACT_CHAT_SUBMIT_EVENT = 'react-chat-window:submit';
    const PLUGIN_DASHBOARD_WINDOW_NAME = 'plugin_dashboard';
    const PLUGIN_DASHBOARD_HANDOFF_EVENT = 'neko:yui-guide:plugin-dashboard:start';
    const PLUGIN_DASHBOARD_READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready';
    const PLUGIN_DASHBOARD_DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done';
    const PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request';
    const PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack';
    const DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME = 'ATLS';
    const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/';
    const INTRO_ACTIVATION_HINT_KEY = 'tutorial.yuiGuide.lines.introActivationHint';
    const INTRO_ACTIVATION_HINT = '点一下这里，我就能开始说话啦～';
    const GUIDE_AUDIO_FILES_BY_KEY = Object.freeze({
        intro_basic: {
            zh: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！.mp3',
            ja: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！.mp3',
            en: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！.mp3',
            ko: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！.mp3',
            ru: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！.mp3'
        },
        intro_practice: {
            zh: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～.mp3',
            ja: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～.mp3',
            en: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～.mp3',
            ko: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～.mp3',
            ru: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～.mp3'
        },
        intro_proactive: {
            zh: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）.mp3',
            ja: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）.mp3',
            en: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）.mp3',
            ko: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）.mp3',
            ru: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）.mp3'
        },
        intro_greeting_reply: {
            zh: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！.mp3',
            ja: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！.mp3',
            en: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！.mp3',
            ko: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！.mp3',
            ru: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！.mp3'
        },
        intro_cat_paw: {
            zh: '好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！.mp3',
            ja: '好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！.mp3',
            en: '好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！.mp3',
            ko: '好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！.mp3',
            ru: '好啦！不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！.mp3'
        },
        takeover_capture_cursor: {
            zh: '嘿咻！可算逮住你的鼠标了喵～.mp3',
            ja: '嘿咻！可算逮住你的鼠标了喵～.mp3',
            en: '嘿咻！可算逮住你的鼠标了喵～.mp3',
            ko: '嘿咻！可算逮住你的鼠标了喵～.mp3',
            ru: '嘿咻！可算逮住你的鼠标了喵～.mp3'
        },
        takeover_plugin_preview_home: {
            zh: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！.mp3',
            ja: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！.mp3',
            en: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！.mp3',
            ko: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！.mp3',
            ru: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！.mp3'
        },
        takeover_plugin_preview_dashboard: {
            zh: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
            ja: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
            en: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
            ko: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
            ru: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3'
        },
        takeover_settings_peek_intro: {
            zh: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。.mp3',
            ja: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。.mp3',
            en: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。.mp3',
            ko: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。.mp3',
            ru: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。.mp3'
        },
        takeover_settings_peek_detail: {
            zh: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！.mp3',
            ja: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！.mp3',
            en: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！.mp3',
            ko: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！.mp3',
            ru: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！.mp3'
        },
        interrupt_resist_light_1: {
            zh: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
            ja: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
            en: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
            ko: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
            ru: '喂！不要拽我啦，还没轮到你的回合呢！.mp3'
        },
        interrupt_resist_light_3: {
            zh: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
            ja: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
            en: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
            ko: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
            ru: '等一下啦！还没结束呢，不要随便打断我啦！.mp3'
        },
        interrupt_angry_exit: {
            zh: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
            ja: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
            en: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
            ko: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
            ru: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3'
        },
        takeover_return_control: {
            zh: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～.mp3',
            ja: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～.mp3',
            en: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～.mp3',
            ko: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～.mp3',
            ru: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～.mp3'
        }
    });

    function guideAudioSrc(key) {
        const files = key && GUIDE_AUDIO_FILES_BY_KEY[key] ? GUIDE_AUDIO_FILES_BY_KEY[key] : null;
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

    const TAKEOVER_CAPTURE_SELECTORS = Object.freeze({
        catPaw: '[alt="猫爪"]',
        agentMaster: '#${p}-toggle-agent-master',
        userPlugin: '#${p}-toggle-agent-user-plugin',
        managementPanel: 'div#neko-sidepanel-action-agent-user-plugin-management-panel'
    });
    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    const GUIDE_AUDIO_CUES_BY_KEY = Object.freeze({
        intro_cat_paw: Object.freeze({
            baseDurationMs: 9102,
            captureCursorPrelude: 6000
        }),
        takeover_settings_peek_intro: Object.freeze({
            baseDurationMs: 11877,
            openSettingsPanel: 9000
        })
    });

    const GUIDE_AUDIO_DURATIONS_BY_KEY = Object.freeze({
        intro_cat_paw: Object.freeze({
            zh: 9102,
            ja: 10697,
            en: 7737,
            ko: 10217,
            ru: 9497
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
        })
    });

    const GUIDE_DEBUG_ZH_TEXT_BY_KEY = Object.freeze({
        'tutorial.yuiGuide.lines.introBasic': '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！',
        'tutorial.yuiGuide.lines.introProactive': '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）',
        'tutorial.yuiGuide.lines.introCatPaw': '好啦不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！',
        'tutorial.yuiGuide.lines.takeoverCaptureCursor': '嘿咻！可算逮住你的鼠标了喵～',
        'tutorial.yuiGuide.lines.takeoverPluginPreviewHome': '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！',
        'tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard': '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekIntro': '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。',
        'tutorial.yuiGuide.lines.takeoverSettingsPeekDetail': '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！',
        'tutorial.yuiGuide.lines.takeoverReturnControl': '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～',
        'tutorial.yuiGuide.lines.interruptResistLight1': '喂！不要拽我啦，还没轮到你的回合呢！',
        'tutorial.yuiGuide.lines.interruptResistLight3': '等一下啦！还没结束呢，不要随便打断我啦！',
        'tutorial.yuiGuide.lines.interruptAngryExit': '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！',
        'tutorial.yuiGuide.lines.introGreetingReply': '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！',
        'tutorial.yuiGuide.lines.introPractice': '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～'
    });

    function getGuideAudioCueConfig(voiceKey) {
        const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
        if (!normalizedKey) {
            return null;
        }

        return GUIDE_AUDIO_CUES_BY_KEY[normalizedKey] || null;
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
        }

        stop() {
            const finish = this.currentFinish;

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

            return null;
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

            const minDurationMs = Number.isFinite(minimumDurationMs) ? minimumDurationMs : 0;
            const initialTimeSeconds = Math.max(
                0,
                (Number.isFinite(startAtMs) ? startAtMs : 0) / 1000
            );

            return new Promise((resolve, reject) => {
                let settled = false;
                const audio = new Audio(audioSrc);
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
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
            const response = await fetch(audioSrc, {
                credentials: 'same-origin'
            });
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
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    source.onended = null;
                    try {
                        source.disconnect();
                    } catch (_) {}
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
                if (gainNode) {
                    gainNode.gain.value = 1;
                    source.connect(gainNode);
                    gainNode.connect(context.destination);
                } else {
                    source.connect(context.destination);
                }

                this.currentFinish = cancelPlayback;
                this.currentAudioMeta = Object.assign({
                    mode: 'buffer',
                    context: context,
                    source: source,
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
                    await this.playPreviewAudio(localAudioSrc, fallbackDurationMs, startAtMs, {
                        voiceKey: normalizedOptions.voiceKey,
                        text: message
                    });
                    return;
                } catch (error) {
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
                    } catch (_) {}

                    console.warn('[YuiGuide] 本地教程语音播放失败，回退 TTS:', normalizedOptions.voiceKey, error);
                }
            }

            if (!this.enabled || typeof SpeechSynthesisUtterance === 'undefined' || !window.speechSynthesis) {
                await wait(fallbackDurationMs);
                return;
            }

            await this.ensureVoicesReady();

            return new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (this.currentFallbackTimer) {
                        window.clearTimeout(this.currentFallbackTimer);
                        this.currentFallbackTimer = null;
                    }
                    if (this.currentUtterance === utterance) {
                        this.currentUtterance = null;
                    }
                    if (this.currentFinish === finish) {
                        this.currentFinish = null;
                    }
                    resolve();
                };

                const utterance = new SpeechSynthesisUtterance(message);
                utterance.lang = guideSpeechLang();
                utterance.rate = 1.02;
                utterance.pitch = 1.05;
                utterance.volume = 0.9;

                try {
                    const voices = window.speechSynthesis.getVoices();
                    if (Array.isArray(voices) && voices.length > 0) {
                        let bestVoice = null;
                        let bestScore = -1;
                        voices.forEach((voice) => {
                            const score = scoreSpeechVoice(voice);
                            if (score > bestScore) {
                                bestScore = score;
                                bestVoice = voice;
                            }
                        });
                        if (bestVoice) {
                            utterance.voice = bestVoice;
                        }
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 选择系统语音失败:', error);
                }

                utterance.onboundary = (event) => {
                    if (typeof normalizedOptions.onBoundary === 'function') {
                        try {
                            normalizedOptions.onBoundary(event);
                        } catch (error) {
                            console.warn('[YuiGuide] 语音边界回调失败:', error);
                        }
                    }
                };
                utterance.onend = finish;
                utterance.onerror = finish;
                this.currentUtterance = utterance;
                this.currentFinish = finish;
                this.currentFallbackTimer = window.setTimeout(
                    finish,
                    Math.max(fallbackDurationMs, 3000)
                );

                try {
                    window.speechSynthesis.cancel();
                    window.speechSynthesis.speak(utterance);
                } catch (error) {
                    console.warn('[YuiGuide] 播放系统语音失败，回退为静默等待:', error);
                    finish();
                }
            });
        }
    }

    class YuiGuideEmotionBridge {
        apply(emotion) {
            if (!emotion || !window.LanLan1 || typeof window.LanLan1.setEmotion !== 'function') {
                return;
            }

            try {
                window.LanLan1.setEmotion(emotion);
            } catch (error) {
                console.warn('[YuiGuide] 设置情绪失败:', error);
            }
        }

        clear() {
            if (window.LanLan1 && typeof window.LanLan1.clearEmotionEffects === 'function') {
                try {
                    window.LanLan1.clearEmotionEffects();
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] 清理情绪失败:', error);
                }
            }

            if (window.LanLan1 && typeof window.LanLan1.clearExpression === 'function') {
                try {
                    window.LanLan1.clearExpression();
                } catch (error) {
                    console.warn('[YuiGuide] 清理表情失败:', error);
                }
            }
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

        click() {
            this.overlay.clickCursor();
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
            this.introClickActivated = false;
            this.awaitingIntroActivation = false;
            this._introActivationResolve = null;
            this.introChoicePending = false;
            this.introPracticeMessageId = null;
            this.introThirdMessageTimer = null;
            this.introReplyPollTimer = null;
            this.takeoverFlowStarted = false;
            this.takeoverFlowCompleted = false;
            this.takeoverFlowPromise = null;
            this.terminationRequested = false;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.scenePausedForResistance = false;
            this.scenePauseResolvers = [];
            this.chatIntroCleanupFns = [];
            this.virtualSpotlights = new Map();
            this.preciseHighlightElements = new Set();
            this.spotlightVariantElements = new Set();
            this.spotlightGeometryHintElements = new Set();
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.pluginDashboardHandoff = null;
            this.pluginDashboardLastInterruptRequestId = '';
            this.pluginDashboardWindowCreatedByGuide = false;
            this.customSecondarySpotlightTarget = null;
            this.keydownHandler = this.onKeyDown.bind(this);
            this.pointerMoveHandler = this.onPointerMove.bind(this);
            this.pointerDownHandler = this.onPointerDown.bind(this);
            this.resistanceCursorTimer = null;
            this.pageHideHandler = this.onPageHide.bind(this);
            this.tutorialEndHandler = this.onTutorialEndEvent.bind(this);
            this.messageHandler = this.onWindowMessage.bind(this);
            this.skipButtonClickHandler = this.onSkipButtonClick.bind(this);
            this.interactionGuardHandler = this.onInteractionGuard.bind(this);

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-driver-hidden');
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
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

        getStep(stepId) {
            if (!stepId) {
                return null;
            }

            if (this.registry && typeof this.registry.getStep === 'function') {
                return this.registry.getStep(stepId) || null;
            }

            return null;
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

        getIntroChoiceLabels() {
            return {
                skipChat: this.resolveGuideCopy(INTRO_SKIP_LABEL_KEY, '暂时不聊天'),
                sayHello: this.resolveGuideCopy(INTRO_HELLO_LABEL_KEY, '你好')
            };
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
                    '#live2d-btn-agent, #vrm-btn-agent, #mmd-btn-agent, ' +
                    '#live2d-btn-settings, #vrm-btn-settings, #mmd-btn-settings, ' +
                    '[id$="-btn-agent"], [id$="-btn-settings"]'
                );
                if (shell) {
                    return shell;
                }
            }

            return element;
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
                    this.overlay.setPersistentSpotlight(persistentTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
                if (primaryTarget) {
                    this.overlay.activateSpotlight(primaryTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'secondary')) {
                this.customSecondarySpotlightTarget = secondaryTarget || null;
                if (secondaryTarget) {
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

        clearIntroFlow(preserveSpotlight) {
            if (this.introThirdMessageTimer) {
                window.clearTimeout(this.introThirdMessageTimer);
                this.introThirdMessageTimer = null;
            }

            if (this.introReplyPollTimer) {
                window.clearTimeout(this.introReplyPollTimer);
                this.introReplyPollTimer = null;
            }

            this.introChoicePending = false;
            this.introPracticeMessageId = null;

            while (this.chatIntroCleanupFns.length > 0) {
                const cleanup = this.chatIntroCleanupFns.pop();
                try {
                    cleanup();
                } catch (error) {
                    console.warn('[YuiGuide] 清理 intro flow 监听器失败:', error);
                }
            }

            if (!preserveSpotlight) {
                this.overlay.clearSpotlight();
            }
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
            // Electron Pet 模式下聊天被拆到独立 BrowserWindow，preload-pet.js 把
            // #react-chat-window-overlay 强制 inline display:none。这种环境下推到
            // React 聊天窗的引导文案永不可见，必须回落到 overlay bubble 叙事。
            if (this.isHomeChatExternalized()) {
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

        getSceneSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'intro_basic' && !this.introFlowCompleted) {
                return this.getChatInputTarget() || null;
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
            this.cursor.cancel();
        }

        resumeCurrentSceneAfterResistance() {
            if (!this.scenePausedForResistance) {
                return;
            }

            this.scenePausedForResistance = false;
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

        resolveGuideVoiceCueTargetMs(voiceKey, cueName, playbackDurationMs) {
            const cueConfig = getGuideAudioCueConfig(voiceKey);
            const normalizedCueName = typeof cueName === 'string' ? cueName.trim() : '';
            if (!cueConfig || !normalizedCueName) {
                return 0;
            }

            const baseDurationMs = Number.isFinite(cueConfig.baseDurationMs)
                ? Math.max(1, cueConfig.baseDurationMs)
                : 0;
            const baseCueMs = Number.isFinite(cueConfig[normalizedCueName])
                ? Math.max(0, cueConfig[normalizedCueName])
                : 0;
            if (baseDurationMs <= 0 || baseCueMs <= 0) {
                return 0;
            }

            const progress = clamp(baseCueMs / baseDurationMs, 0, 1);
            const targetDurationMs = Number.isFinite(playbackDurationMs) && playbackDurationMs > 0
                ? playbackDurationMs
                : baseDurationMs;
            return clamp(Math.round(targetDurationMs * progress), 0, targetDurationMs);
        }

        async waitForNarrationCue(voiceKey, cueName) {
            const fallbackTargetMs = this.resolveGuideVoiceCueTargetMs(voiceKey, cueName, 0);
            if (fallbackTargetMs <= 0) {
                return true;
            }

            let fallbackElapsedMs = 0;
            let lastTickAt = Date.now();
            let sawAudioPlayback = false;

            while (!this.isStopping()) {
                if (this.scenePausedForResistance) {
                    await this.waitUntilSceneResumed();
                    lastTickAt = Date.now();
                    continue;
                }

                const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
                if (playbackSnapshot && playbackSnapshot.voiceKey === voiceKey) {
                    sawAudioPlayback = true;
                    const cueTargetMs = this.resolveGuideVoiceCueTargetMs(
                        voiceKey,
                        cueName,
                        playbackSnapshot.durationMs
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

            if (!narration) {
                return;
            }

            narration.cancelled = true;
            this.voiceQueue.stop();
            if (typeof narration.resolve === 'function') {
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
                return false;
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
            narration.resumeAudioOffsetMs = playbackSnapshot && playbackSnapshot.mode === 'audio'
                ? playbackSnapshot.currentTimeMs
                : 0;

            narration.interrupted = true;
            this.clearNarrationResumeTimer();
            this.voiceQueue.stop();
            return true;
        }

        scheduleNarrationResume() {
            this.clearNarrationResumeTimer();

            const attemptResume = () => {
                const narration = this.activeNarration;
                if (!narration || narration.cancelled || this.destroyed) {
                    this.restoreCurrentScenePresentation();
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
                this.restoreCurrentScenePresentation();
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

        restoreCurrentScenePresentation() {
            if (this.destroyed || this.angryExitTriggered || !this.currentStep) {
                return;
            }

            const performance = this.currentStep.performance || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);
            const spotlightTarget = this.getSceneSpotlightTarget(this.currentSceneId, performance);
            if (spotlightTarget) {
                this.overlay.setPersistentSpotlight(spotlightTarget);
            } else {
                this.overlay.clearPersistentSpotlight();
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(this.currentSceneId, performance);
            if (actionSpotlightTarget) {
                this.overlay.activateSpotlight(actionSpotlightTarget);
            } else {
                this.overlay.clearActionSpotlight();
            }

            if (this.customSecondarySpotlightTarget) {
                this.overlay.activateSecondarySpotlight(this.customSecondarySpotlightTarget);
            }

            if (this.shouldNarrateInChat(this.currentSceneId)) {
                this.overlay.hideBubble();
            } else if (bubbleText) {
                this.overlay.showBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: this.resolveRect(this.currentStep.anchor)
                });
            } else {
                this.overlay.hideBubble();
            }

            if (performance.emotion) {
                this.emotionBridge.apply(performance.emotion);
            }
        }

        async playManagedScene(stepId, meta) {
            this.setCurrentScene(stepId, meta && meta.context ? meta.context : null);
            await this.playScene(stepId, meta || {});
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
                    const apiResult = await api[methodName].apply(api, Array.isArray(args) ? args : []);
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
                return desiredVisible;
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

            return !!result && this.isManagedPanelVisible(buttonId) === desiredVisible;
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
                    return await api.ensureAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs);
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
            return this.callHomeInteractionApi('clickAgentSidePanelAction', [toggleId, actionId, options || null], async () => {
                const button = await this.waitForAgentSidePanelActionVisible(toggleId, actionId, 1800);
                if (!button || typeof button.click !== 'function') {
                    return false;
                }

                button.click();
                return true;
            });
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
                    const childWin = await api.openPluginDashboard();
                    return !!childWin;
                }
                if (api && typeof api.openPage === 'function') {
                    const childWin = await api.openPage(
                        navigation.openUrl,
                        navigation.windowName,
                        navigation.features || ''
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
                    return await api.waitForWindowOpen(windowName, timeoutMs);
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
                    return !!(await api.closeWindow(windowName));
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
                const response = await fetch('/api/agent/command', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                        command: 'set_agent_enabled',
                        enabled: !!enabled
                    })
                });
                if (!response.ok) {
                    return false;
                }

                const data = await response.json();
                return !!(data && data.success === true);
            });
        }

        async setAgentFlagEnabled(flagKey, enabled) {
            return this.callHomeInteractionApi('setAgentFlagEnabled', [flagKey, enabled], async () => {
                const response = await fetch('/api/agent/command', {
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
                });
                if (!response.ok) {
                    return false;
                }

                const data = await response.json();
                return !!(data && data.success === true);
            });
        }

        async openPluginDashboardWindow(options) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.openPluginDashboard === 'function') {
                try {
                    return await api.openPluginDashboard(options || null);
                } catch (error) {
                    console.warn('[YuiGuide] openPluginDashboard 失败，改用本地兜底:', error);
                }
            }

            if (api && typeof api.openPage === 'function') {
                try {
                    return await api.openPage('http://127.0.0.1:48916/ui/', 'plugin_dashboard', '', options || null);
                } catch (error) {
                    console.warn('[YuiGuide] openPage(plugin_dashboard) 失败:', error);
                }
            }

            return null;
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

        async openModelManagerPage(lanlanName) {
            const api = this.getHomeInteractionApi();
            const targetLanlanName = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            if (api && typeof api.openModelManagerPage === 'function') {
                try {
                    return await api.openModelManagerPage(targetLanlanName);
                } catch (error) {
                    console.warn('[YuiGuide] openModelManagerPage 失败，改用本地兜底:', error);
                }
            }

            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            const windowName = this.getModelManagerWindowName(targetLanlanName, appearanceMenuId);
            if (api && typeof api.openPage === 'function') {
                try {
                    return await api.openPage(
                        '/model_manager?lanlan_name=' + encodeURIComponent(targetLanlanName),
                        windowName
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
                    await this.waitUntilSceneResumed();
                    index -= 1;
                    continue;
                }
                await this.waitUntilSceneResumed();
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
                await this.waitUntilSceneResumed();
            }

            return false;
        }

        async clickCursorAndWait(holdMs) {
            this.cursor.click();
            await this.waitForSceneDelay(Number.isFinite(holdMs) ? holdMs : 180);
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

            this.cursor.click();
            if (typeof normalized.action !== 'function') {
                return true;
            }

            return !!(await normalized.action());
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
                padding: 4
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

                this.cursor.click();
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

                this.cursor.click();
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

                this.cursor.click();
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
                const managementButtonRect = this.getElementRect(managementButton);
                const managementSpotlightTarget = managementButtonRect
                    ? this.createVirtualSpotlight('plugin-management-entry', {
                        left: Math.max(0, managementButtonRect.left - 14),
                        top: managementButtonRect.top,
                        right: Math.min(window.innerWidth, managementButtonRect.right + 14),
                        bottom: managementButtonRect.bottom
                    }, {
                        padding: DEFAULT_SPOTLIGHT_PADDING,
                        radius: 18
                    })
                    : managementButton;

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
                await this.clickCursorAndWait(scaleSceneMs(180, 90, 420));
                const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                    keepMainUIVisible: true
                });
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
                } else {
                    pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                    this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
                }
                if (
                    (!pluginDashboardWindow || pluginDashboardWindow.closed)
                    && runId === this.sceneRunId
                    && !this.destroyed
                    && !this.angryExitTriggered
                ) {
                    pluginDashboardWindow = await this.openPluginDashboardWindow({
                        keepMainUIVisible: true
                    });
                    this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
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

        waitForPluginDashboardPerformance(windowRef, payload) {
            if (!windowRef || windowRef.closed) {
                return Promise.resolve(false);
            }

            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.reject === 'function') {
                this.pluginDashboardHandoff.reject(new Error('plugin-dashboard handoff superseded'));
            }

            return new Promise((resolve, reject) => {
                this.pluginDashboardLastInterruptRequestId = '';
                const sessionId = 'plugin-dashboard-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                const startedAt = Date.now();
                const handoffPayload = Object.assign({}, payload || {}, {
                    interruptCount: Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0))
                });
                const preloadTimeoutMs = 15000;
                const executionTimeoutMs = clamp(
                    estimateSpeechDurationMs(handoffPayload && handoffPayload.line ? handoffPayload.line : '') + 12000,
                    12000,
                    42000
                );
                const targetOrigin = '*';
                const handoff = {
                    sessionId: sessionId,
                    windowRef: windowRef,
                    ready: false,
                    readyAt: 0,
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
                            handoff.resolve(false);
                            return;
                        }
                        try {
                            windowRef.postMessage({
                                type: PLUGIN_DASHBOARD_HANDOFF_EVENT,
                                sessionId: sessionId,
                                payload: handoffPayload
                            }, targetOrigin);
                        } catch (error) {
                            console.warn('[YuiGuide] 向插件面板发送 handoff 消息失败:', error);
                        }
                    }
                };

                handoff.intervalId = window.setInterval(() => {
                    if (!windowRef || windowRef.closed) {
                        handoff.resolve(false);
                        return;
                    }

                    if (!handoff.ready && (Date.now() - startedAt) >= preloadTimeoutMs) {
                        handoff.resolve(false);
                        return;
                    }

                    if (handoff.ready && handoff.readyAt > 0 && (Date.now() - handoff.readyAt) >= executionTimeoutMs) {
                        handoff.resolve(false);
                        return;
                    }
                    if (!handoff.ready) {
                        handoff.post();
                    }
                }, 450);
                handoff.timeoutId = window.setTimeout(() => {
                    handoff.resolve(false);
                }, preloadTimeoutMs + executionTimeoutMs);

                this.pluginDashboardHandoff = handoff;
                handoff.post();
            });
        }

        async runPluginDashboardPreviewScene(step, runId) {
            this.highlightChatWindow();
            const stepBubbleText = this.resolvePerformanceBubbleText(step && step.performance);
            let homeNarrationPromise = Promise.resolve();
            if (stepBubbleText) {
                this.appendGuideChatMessage(stepBubbleText, {
                    textKey: step && step.performance ? step.performance.bubbleTextKey : ''
                });
                homeNarrationPromise = this.speakLineAndWait(stepBubbleText, {
                    voiceKey: step.performance.voiceKey || 'takeover_plugin_preview_home'
                }).catch(() => {});
            }
            const originalAgentSwitches = await this.getAgentSwitchSnapshot();
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
                if (typeof originalAgentSwitches.agentMaster === 'boolean') {
                    restoreResults.push(await restoreSwitch('agent-master', () => this.setAgentMasterEnabled(originalAgentSwitches.agentMaster)));
                }
                if (typeof originalAgentSwitches.userPlugin === 'boolean') {
                    restoreResults.push(await restoreSwitch('user_plugin_enabled', () => this.setAgentFlagEnabled('user_plugin_enabled', originalAgentSwitches.userPlugin)));
                }
                const restoredAll = restoreResults.every(Boolean);
                if (restoredAll) {
                    agentSwitchesRolledBack = true;
                }
                return restoredAll;
            };

            try {
                let dashboardWindow = await this.runTakeoverCaptureActionSequence(
                    step,
                    (step && step.performance) || {},
                    runId
                );
                await homeNarrationPromise;
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }
                if (!dashboardWindow) {
                    return;
                }

                const dashboardText = this.resolveGuideCopy(
                    TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY,
                    TAKEOVER_PLUGIN_DASHBOARD_TEXT
                );
                this.appendGuideChatMessage(dashboardText, {
                    textKey: TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY
                });
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
                    await this.closeAgentPanel().catch(() => {});
                };
                const dashboardVoiceKey = 'takeover_plugin_preview_dashboard';
                const dashboardAudioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                    ? this.voiceQueue.resolveGuideAudioSrc(dashboardVoiceKey)
                    : '';
                const dashboardNarrationDurationMs = this.getGuideVoiceDurationMs(dashboardVoiceKey, resolveGuideLocale())
                    || estimateSpeechDurationMs(dashboardText);
                const dashboardNarrationStartedAtMs = Date.now();
                const dashboardNarrationPromise = this.speakLineAndWait(dashboardText, {
                    voiceKey: dashboardVoiceKey
                }).catch(() => {}).finally(() => closePluginPreviewPanel());

                const pluginDashboardPerformancePromise = this.waitForPluginDashboardPerformance(dashboardWindow, {
                    line: dashboardText,
                    closeOnDone: true,
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
                await rollbackAgentSwitches();
            }
        }

        async runSettingsPeekScene(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            const settingsButton = this.resolveElement(performance.cursorTarget || step.anchor);
            this.setSpotlightGeometryHint(settingsButton, {
                padding: 4
            });
            const introText = this.resolvePerformanceBubbleText(performance);
            await this.closeAgentPanel();
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            this.overlay.clearActionSpotlight();
            this.highlightChatWindow();

            if (introText) {
                this.appendGuideChatMessage(introText, {
                    textKey: performance.bubbleTextKey || ''
                });
            }
            if (performance.emotion) {
                this.emotionBridge.apply(performance.emotion);
            }

            const introNarrationPromise = this.speakLineAndWait(introText || '', {
                voiceKey: performance.voiceKey || 'takeover_settings_peek_intro'
            });
            if (!(await this.waitForNarrationCue(
                performance.voiceKey || 'takeover_settings_peek_intro',
                'openSettingsPanel'
            ))) {
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
                return;
            }

            await introNarrationPromise;
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            let characterMenu = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().characterMenu
            ], 1600);
            if (!characterMenu) {
                return;
            }

            await this.ensureCharacterSettingsSidePanelVisible();

            let appearanceItem = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().appearanceItem
            ], 2200);
            let voiceCloneItem = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().voiceCloneItem
            ], 2200);
            if (!appearanceItem || !voiceCloneItem || runId !== this.sceneRunId || this.isStopping()) {
                return;
            }
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
            ({
                settingsButton: settingsButtonTarget,
                characterMenu,
                appearanceItem,
                voiceCloneItem
            } = this.refreshSettingsPeekSpotlights(settingsButton));
            if (!characterMenu || !appearanceItem || !voiceCloneItem) {
                return;
            }

            const sidePanel = this.getCharacterSettingsSidePanel();
            const panelRect = sidePanel ? this.getElementRect(sidePanel) : null;
            const centerX = panelRect
                ? panelRect.left + panelRect.width / 2
                : (this.getElementRect(appearanceItem).left + this.getElementRect(voiceCloneItem).left) / 2;
            const centerY = panelRect
                ? panelRect.top + panelRect.height / 2
                : (this.getElementRect(appearanceItem).top + this.getElementRect(voiceCloneItem).bottom) / 2;
            const radiusX = panelRect
                ? panelRect.width / 2 * 1.4
                : 120;
            const radiusY = panelRect
                ? panelRect.height / 2 * 1.4
                : 80;
            if (panelRect) {
                while (!this.isStopping()) {
                    const movedToCenter = await this.cursor.moveToPoint(centerX, centerY, {
                        durationMs: 900,
                        pauseCheck: () => this.scenePausedForResistance,
                        cancelCheck: () => this.isStopping()
                    });
                    if (movedToCenter) {
                        break;
                    }
                    await this.waitUntilSceneResumed();
                }
            }
            if (runId !== this.sceneRunId || this.isStopping()) {
                return;
            }

            const detailText = this.resolveGuideCopy(
                TAKEOVER_SETTINGS_DETAIL_TEXT_KEY,
                TAKEOVER_SETTINGS_DETAIL_TEXT
            );
            this.appendGuideChatMessage(detailText, {
                textKey: TAKEOVER_SETTINGS_DETAIL_TEXT_KEY
            });

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
            const narrationPromise = this.speakLineAndWait(detailText, {
                voiceKey: 'takeover_settings_peek_detail'
            }).finally(() => {
                if (runId !== this.sceneRunId || this.isStopping()) {
                    return;
                }

                this.collapseCharacterSettingsSidePanel();
                clearSettingsPeekHighlights();
                return closeSettingsPeekPanel();
            });

            const cycleMs = 7000;
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
                        await this.waitUntilSceneResumed();
                    }
                }
            })();

            await Promise.all([narrationPromise, actionPromise]);
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
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }
            document.body.classList.remove('yui-resistance-cursor-reveal');
            this.awaitingIntroActivation = false;
            if (typeof this._introActivationResolve === 'function') {
                this._introActivationResolve();
                this._introActivationResolve = null;
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
                if (this.isStopping()) {
                    return;
                }
                this.requestTermination('complete', 'complete');
            })().catch((error) => {
                console.error('[YuiGuide] 接管主流程执行失败:', error);
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

        scrollChatToBottom() {
            const messageList = this.resolveElement('#react-chat-window-root .message-list');
            if (!messageList) {
                return;
            }

            const scroll = () => {
                try {
                    messageList.scrollTo({
                        top: messageList.scrollHeight,
                        behavior: 'smooth'
                    });
                } catch (_) {
                    messageList.scrollTop = messageList.scrollHeight;
                }
            };

            scroll();
            window.requestAnimationFrame(scroll);
            this.schedule(scroll, 160);
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

            // Electron Pet 模式下聊天 overlay 被永久隐藏，推到 React 聊天的消息
            // 用户看不到。回落到 overlay bubble 显示文本（按钮选项不渲染——
            // runChatIntroPrelude 里需要按钮的练习问候步骤已单独短路）。
            if (this.isHomeChatExternalized()) {
                try {
                    this.overlay.showBubble(content, {
                        title: this.getGuideAssistantName(),
                        emotion: 'neutral'
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 兜底气泡展示失败:', error);
                }
                return null;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.appendMessage === 'function') {
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

                const appendedMessage = host.appendMessage(message);
                this.scrollChatToBottom();
                return appendedMessage;
            }

            if (typeof window.appendMessage === 'function') {
                window.appendMessage(content, 'gemini', true);
                this.scrollChatToBottom();
            }

            return null;
        }

        getGuideChatMessage(messageId) {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.getState !== 'function' || !messageId) {
                return null;
            }

            try {
                const snapshot = host.getState();
                const messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
                return messages.find(function (message) {
                    return message && String(message.id) === String(messageId);
                }) || null;
            } catch (error) {
                console.warn('[YuiGuide] 读取引导消息失败:', error);
                return null;
            }
        }

        updateGuideChatMessage(messageId, patch) {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.updateMessage !== 'function' || !messageId) {
                return null;
            }

            try {
                return host.updateMessage(messageId, patch || {});
            } catch (error) {
                console.warn('[YuiGuide] 更新引导消息失败:', error);
                return null;
            }
        }

        clearGuideChatMessageActions(messageId) {
            if (!messageId) {
                return null;
            }

            const existingMessage = this.getGuideChatMessage(messageId);
            const nextBlocks = existingMessage && Array.isArray(existingMessage.blocks)
                ? existingMessage.blocks.filter(function (block) {
                    return block && block.type !== 'buttons';
                })
                : undefined;

            return this.updateGuideChatMessage(messageId, {
                blocks: nextBlocks,
                actions: []
            });
        }

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatInputTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');

            if (!target) {
                return;
            }

            if (target && typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'center',
                    inline: 'nearest'
                });
            }

            if (target) {
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

        attachChatIntroActivation() {
            const actionHandler = (event) => {
                if (this.destroyed || !this.introChoicePending) {
                    return;
                }

                const detail = event && event.detail ? event.detail : null;
                const message = detail && detail.message ? detail.message : null;
                const action = detail && detail.action ? detail.action : null;
                const messageId = message && message.id ? String(message.id) : '';
                const actionId = action && action.id ? String(action.id) : '';
                const actionName = action && action.action ? String(action.action) : '';

                if (!messageId || messageId !== this.introPracticeMessageId) {
                    return;
                }

                if (actionId !== INTRO_SKIP_ACTION_ID && actionName !== INTRO_SKIP_ACTION_ID) {
                    if (actionId === INTRO_HELLO_ACTION_ID || actionName === INTRO_HELLO_ACTION_ID) {
                        this.resolveChatIntroChoice('chat');
                    }
                    return;
                }

                this.resolveChatIntroChoice('skip');
            };

            const submitHandler = (event) => {
                if (this.destroyed || !this.introChoicePending) {
                    return;
                }

                const detail = event && event.detail ? event.detail : null;
                const text = detail && typeof detail.text === 'string' ? detail.text : '';
                if (!text.trim()) {
                    return;
                }

                this.resolveChatIntroChoice('chat', {
                    submittedAt: Date.now()
                });
            };

            window.addEventListener(REACT_CHAT_ACTION_EVENT, actionHandler, true);
            window.addEventListener(REACT_CHAT_SUBMIT_EVENT, submitHandler, true);
            this.chatIntroCleanupFns.push(() => {
                window.removeEventListener(REACT_CHAT_ACTION_EVENT, actionHandler, true);
            });
            this.chatIntroCleanupFns.push(() => {
                window.removeEventListener(REACT_CHAT_SUBMIT_EVENT, submitHandler, true);
            });
        }

        waitForFirstAssistantReplyAfter(submittedAt) {
            const replyStartAt = Number.isFinite(submittedAt) ? submittedAt : Date.now();
            const maxWaitMs = 120000;

            return new Promise((resolve) => {
                const startedAt = Date.now();
                const initialHost = window.reactChatWindowHost;
                const initialSnapshot = initialHost && typeof initialHost.getState === 'function'
                    ? initialHost.getState()
                    : null;
                const knownAssistantMessageIds = new Set(
                    initialSnapshot && Array.isArray(initialSnapshot.messages)
                        ? initialSnapshot.messages.reduce((ids, message) => {
                            if (!message || message.role !== 'assistant') {
                                return ids;
                            }
                            const messageId = typeof message.id === 'string' ? message.id : '';
                            if (messageId && messageId.indexOf('yui-guide-') !== 0) {
                                ids.push(messageId);
                            }
                            return ids;
                        }, [])
                        : []
                );
                let seenReplyMessage = null;
                let settled = false;
                let replyTurnId = null;
                let replyTurnStartedAt = 0;
                let replyTurnEnded = false;
                let replySpeechStarted = false;

                const finish = (replyMessage) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    window.removeEventListener('neko-assistant-turn-start', handleAssistantTurnStart, true);
                    window.removeEventListener('neko-assistant-speech-start', handleAssistantSpeechStart, true);
                    window.removeEventListener('neko-assistant-speech-end', handleAssistantSpeechEnd, true);
                    window.removeEventListener('neko-assistant-turn-end', handleAssistantTurnEnd, true);
                    if (this.introReplyPollTimer) {
                        window.clearTimeout(this.introReplyPollTimer);
                        this.introReplyPollTimer = null;
                    }
                    resolve(replyMessage || seenReplyMessage || null);
                };

                const handleAssistantTurnStart = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId) {
                        return;
                    }
                    if (timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && replyTurnId !== turnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    replyTurnStartedAt = timestamp;
                };

                const handleAssistantSpeechStart = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && replyTurnId !== turnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    replySpeechStarted = true;
                };

                const handleAssistantSpeechEnd = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && turnId !== replyTurnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    finish(seenReplyMessage || null);
                };

                const handleAssistantTurnEnd = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && turnId !== replyTurnId) {
                        return;
                    }
                    if (replyTurnStartedAt && timestamp < replyTurnStartedAt) {
                        return;
                    }
                    replyTurnId = turnId;
                    replyTurnEnded = true;
                    if (!replySpeechStarted && seenReplyMessage && seenReplyMessage.status === 'sent') {
                        finish(seenReplyMessage);
                    }
                };

                window.addEventListener('neko-assistant-turn-start', handleAssistantTurnStart, true);
                window.addEventListener('neko-assistant-speech-start', handleAssistantSpeechStart, true);
                window.addEventListener('neko-assistant-speech-end', handleAssistantSpeechEnd, true);
                window.addEventListener('neko-assistant-turn-end', handleAssistantTurnEnd, true);

                const poll = () => {
                    if (this.isStopping()) {
                        finish(null);
                        return;
                    }

                    const host = window.reactChatWindowHost;
                    const snapshot = host && typeof host.getState === 'function'
                        ? host.getState()
                        : null;
                    const messages = snapshot && Array.isArray(snapshot.messages)
                        ? snapshot.messages
                        : [];

                    const replyMessage = messages.find((message) => {
                        if (!message || message.role !== 'assistant') {
                            return false;
                        }

                        const messageId = typeof message.id === 'string' ? message.id : '';
                        if (messageId.indexOf('yui-guide-') === 0) {
                            return false;
                        }

                        if (knownAssistantMessageIds.has(messageId)) {
                            return false;
                        }

                        const createdAt = Number.isFinite(message.createdAt) ? message.createdAt : 0;
                        if (createdAt < replyStartAt) {
                            return false;
                        }

                        return true;
                    }) || null;

                    if (replyMessage) {
                        seenReplyMessage = replyMessage;
                        if (replyTurnEnded && !replySpeechStarted && replyMessage.status === 'sent') {
                            finish(replyMessage);
                            return;
                        }
                    }

                    if ((Date.now() - startedAt) >= maxWaitMs) {
                        finish(seenReplyMessage);
                        return;
                    }

                    this.introReplyPollTimer = window.setTimeout(poll, 280);
                };

                poll();
            });
        }

        resolveChatIntroChoice(mode, options) {
            if (this.isStopping() || !this.introChoicePending) {
                return;
            }

            const normalizedMode = mode === 'chat' ? 'chat' : 'skip';
            const promptMessageId = this.introPracticeMessageId;

            this.introChoicePending = false;
            this.introClickActivated = true;
            this.introFlowCompleted = true;

            if (promptMessageId) {
                this.clearGuideChatMessageActions(promptMessageId);
            }

            this.cancelActiveNarration();
            this.clearIntroFlow(true);
            this.highlightChatWindow();

            if (normalizedMode === 'chat') {
                (async () => {
                    const greetingReplyText = this.resolveGuideCopy(
                        INTRO_GREETING_REPLY_TEXT_KEY,
                        INTRO_GREETING_REPLY_TEXT
                    );
                    this.appendGuideChatMessage(greetingReplyText, {
                        textKey: INTRO_GREETING_REPLY_TEXT_KEY
                    });
                    this.emotionBridge.apply('happy');
                    await this.speakLineAndWait(greetingReplyText, {
                        voiceKey: 'intro_greeting_reply'
                    });
                    if (this.isStopping()) {
                        return;
                    }

                    if (!this.cursor.hasPosition()) {
                        const origin = this.getDefaultCursorOrigin();
                        this.cursor.showAt(origin.x, origin.y);
                    }

                    this.sendIntroFollowups({
                        includeProactive: false
                    });
                })().catch((error) => {
                    console.warn('[YuiGuide] 播放问候分支失败:', error);
                });
                return;
            }

            this.sendIntroFollowups({
                includeProactive: true
            });
        }

        sendIntroFollowups(options) {
            const proactiveStep = this.getStep('intro_proactive');
            const catPawStep = this.getStep('intro_cat_paw');
            const normalizedOptions = options || {};
            const includeProactive = normalizedOptions.includeProactive !== false;
            const catPawDelayMs = includeProactive ? 0 : 280;

            (async () => {
                if (includeProactive && proactiveStep && proactiveStep.performance) {
                    const proactiveText = this.resolvePerformanceBubbleText(proactiveStep.performance);
                    this.appendGuideChatMessage(proactiveText, {
                        textKey: proactiveStep.performance.bubbleTextKey || ''
                    });
                    if (proactiveStep.performance.emotion) {
                        this.emotionBridge.apply(proactiveStep.performance.emotion);
                    }
                    await this.speakLineAndWait(proactiveText, {
                        voiceKey: proactiveStep.performance.voiceKey
                    });
                    if (this.isStopping()) {
                        return;
                    }
                    if (!this.cursor.hasPosition()) {
                        const origin = this.getDefaultCursorOrigin();
                        this.cursor.showAt(origin.x, origin.y);
                    }
                }
                if (this.isStopping()) {
                    return;
                }

                if (catPawDelayMs > 0) {
                    await new Promise((resolve) => {
                        this.introThirdMessageTimer = window.setTimeout(() => {
                            this.introThirdMessageTimer = null;
                            resolve();
                        }, catPawDelayMs);
                    });
                }

                if (this.isStopping()) {
                    return;
                }

                if (catPawStep && catPawStep.performance) {
                    const catPawText = this.resolvePerformanceBubbleText(catPawStep.performance);
                    this.appendGuideChatMessage(catPawText, {
                        textKey: catPawStep.performance.bubbleTextKey || ''
                    });
                    if (catPawStep.performance.emotion) {
                        this.emotionBridge.apply(catPawStep.performance.emotion);
                    }
                    await Promise.all([
                        this.speakLineAndWait(catPawText, {
                            voiceKey: catPawStep.performance.voiceKey
                        }),
                        (async () => {
                            if (!(await this.waitForNarrationCue(
                                catPawStep.performance.voiceKey,
                                'captureCursorPrelude'
                            ))) {
                                return;
                            }
                            if (this.isStopping()) {
                                return;
                            }
                            await this.performCaptureCursorPrelude(3000);
                        })()
                    ]);
                }

                if (this.isStopping()) {
                    return;
                }
                this.introFlowCompleted = true;
                await wait(320);
                if (this.isStopping()) {
                    return;
                }
                await this.runTakeoverMainFlow();
            })();
        }

        async runChatIntroPrelude() {
            if (this.introFlowStarted || this.isStopping()) {
                return;
            }

            const introStep = this.getStep('intro_basic');
            if (!introStep || !introStep.performance) {
                return;
            }

            // Electron Pet 模式：跳过 ensureChatVisible（聊天 overlay 被 preload 永久隐藏）、
            // ghost cursor + 等输入框激活（无 autoplay 限制 + 输入框不可见无法点），
            // 跳过练习问候按钮选择，直接进 sendIntroFollowups → takeover。
            if (this.isHomeChatExternalized()) {
                await this.runChatIntroPreludeExternalized(introStep);
                return;
            }

            this.introFlowStarted = true;
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();
            await this.ensureChatVisible();
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
                this.overlay.showBubble(activationHint, {
                    anchorRect: inputRect
                });
                // 将气泡定位到输入框正上方
                const bubbleEl = this.overlay.bubble;
                if (bubbleEl) {
                    const bubbleW = Math.min(320, window.innerWidth - 32);
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
                this.overlay.hideBubble();
                this.cursor.wobble();
                await wait(200);
            }
            if (this.isStopping()) {
                return;
            }

            const introText = this.resolvePerformanceBubbleText(introStep.performance);
            this.appendGuideChatMessage(introText, {
                textKey: introStep.performance.bubbleTextKey || ''
            });
            if (introStep.performance.emotion) {
                this.emotionBridge.apply(introStep.performance.emotion);
            }
            await this.speakLineAndWait(introText, {
                voiceKey: introStep.performance.voiceKey,
                minDurationMs: 4200
            });
            if (this.isStopping()) {
                return;
            }

            this.highlightChatWindow();
            await wait(240);
            if (this.isStopping()) {
                return;
            }

            const introChoiceLabels = this.getIntroChoiceLabels();
            const practiceText = this.resolveGuideCopy(INTRO_PRACTICE_TEXT_KEY, INTRO_PRACTICE_TEXT);
            const practiceMessage = this.appendGuideChatMessage(practiceText, {
                textKey: INTRO_PRACTICE_TEXT_KEY,
                buttons: [{
                    id: INTRO_SKIP_ACTION_ID,
                    label: introChoiceLabels.skipChat,
                    action: INTRO_SKIP_ACTION_ID,
                    variant: 'secondary',
                    disabled: false
                }, {
                    id: INTRO_HELLO_ACTION_ID,
                    label: introChoiceLabels.sayHello,
                    action: INTRO_HELLO_ACTION_ID,
                    variant: 'primary',
                    disabled: false
                }]
            });
            this.introPracticeMessageId = practiceMessage && practiceMessage.id
                ? String(practiceMessage.id)
                : null;
            this.introChoicePending = true;
            this.attachChatIntroActivation();
            await this.speakLineAndWait(practiceText, {
                voiceKey: 'intro_practice',
                minDurationMs: 3600
            });
            if (this.isStopping()) {
                return;
            }
            this.introFlowCompleted = true;
        }

        // Electron Pet 模式专用 prelude：聊天 overlay 被永久隐藏，无法走"输入框激活 +
        // 聊天叙事 + 按钮选择"的标准路径。改为：仅用 overlay bubble 朗读 intro，
        // 然后等同用户选择"暂时不聊天"，直接进 proactive + cat-paw 串场再交给 takeover。
        async runChatIntroPreludeExternalized(introStep) {
            this.introFlowStarted = true;
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();

            const introText = this.resolvePerformanceBubbleText(introStep.performance);
            if (introText) {
                this.overlay.showBubble(introText, {
                    title: this.getGuideAssistantName(),
                    emotion: introStep.performance.emotion || 'neutral'
                });
            }
            if (introStep.performance.emotion) {
                this.emotionBridge.apply(introStep.performance.emotion);
            }
            await this.speakLineAndWait(introText || '', {
                voiceKey: introStep.performance.voiceKey,
                minDurationMs: 4200
            });
            if (this.isStopping()) {
                return;
            }

            this.introChoicePending = false;
            this.introClickActivated = true;
            this.introFlowCompleted = true;
            this.sendIntroFollowups({ includeProactive: true });
        }

        async startPrelude() {
            await syncGuideI18nLanguage(5000);
            const preludeSceneIds = this.getPreludeSceneIds();
            if (!Array.isArray(preludeSceneIds) || preludeSceneIds.length === 0) {
                return;
            }

            const firstSceneId = preludeSceneIds[0];
            if (firstSceneId === 'intro_basic' && this.page === 'home') {
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

            if ((stepId === 'intro_proactive' || stepId === 'intro_cat_paw') && this.introFlowStarted) {
                this.currentSceneId = stepId;
                this.currentStep = this.getStep(stepId);
                this.currentContext = context || null;
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
            const shouldKeepInterruptsEnabled = (
                performance.interruptible !== false
                && (isTakeoverScene || stepId === 'intro_cat_paw')
            );
            const shouldOpenPanelAfterNarration = (
                stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
            );

            this.clearSceneTimers();
            this.overlay.setAngry(false);
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            if (stepId !== 'takeover_capture_cursor' && stepId !== 'takeover_plugin_preview') {
                this.clearRetainedExtraSpotlights();
            }

            if (isTakeoverScene) {
                this.overlay.setTakingOver(true);
            }

            const persistentSpotlightTarget = this.getSceneSpotlightTarget(stepId, performance);
            if (persistentSpotlightTarget) {
                this.overlay.setPersistentSpotlight(persistentSpotlightTarget);
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(stepId, performance);
            if (
                actionSpotlightTarget
                && (stepId === 'takeover_capture_cursor' || stepId === 'takeover_plugin_preview')
            ) {
                this.setSpotlightGeometryHint(actionSpotlightTarget, {
                    padding: 4
                });
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

                if (bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || ''
                    });
                } else if (bubbleText) {
                    // Electron Pet 模式下 shouldNarrateInChat=false，否则文案只剩语音、无可见气泡。
                    this.overlay.showBubble(bubbleText, {
                        title: this.getGuideAssistantName(),
                        emotion: performance.emotion || 'neutral',
                        anchorRect: anchorRect
                    });
                }
                if (performance.emotion) {
                    this.emotionBridge.apply(performance.emotion);
                }

                await this.speakLineAndWait(bubbleText || '', {
                    voiceKey: performance.voiceKey,
                    minDurationMs: 4000
                });
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
                this.overlay.showBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            } else if (!shouldNarrateAfterMove) {
                this.overlay.hideBubble();
            }

            if (performance.emotion && !shouldNarrateAfterMove) {
                this.emotionBridge.apply(performance.emotion);
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
                this.overlay.showBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            }

            if (performance.emotion && shouldNarrateDuringMove) {
                this.emotionBridge.apply(performance.emotion);
            }

            if (shouldNarrateDuringMove) {
                if (bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || ''
                    });
                    this.overlay.hideBubble();
                }
                narrationPromise = this.speakLineAndWait(bubbleText || '', {
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
                this.overlay.showBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            } else if (shouldNarrateAfterMove) {
                this.overlay.hideBubble();
            }

            if (performance.emotion && shouldNarrateAfterMove) {
                this.emotionBridge.apply(performance.emotion);
            }

            if (!shouldNarrateDuringMove) {
                if (bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(bubbleText, {
                        textKey: performance.bubbleTextKey || ''
                    });
                    this.overlay.hideBubble();
                }
                await this.speakLineAndWait(bubbleText || '', {
                    voiceKey: performance.voiceKey
                });
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            if (performance.cursorAction === 'click' && !shouldOpenPanelAfterNarration) {
                this.cursor.click();
            } else if (performance.cursorAction === 'wobble') {
                this.cursor.wobble();
            }

            if (stepId === 'takeover_return_control') {
                await this.closeManagedPanels();
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.highlightChatWindow();
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

            if (!document.body.classList.contains('yui-taking-over')) {
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

        playLightResistance(x, y) {
            if (this.scenePausedForResistance) {
                return;
            }

            // 对抗机制触发时真实鼠标显示 3 秒
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
            }
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.body.classList.add('yui-resistance-cursor-reveal');
            this.resistanceCursorTimer = window.setTimeout(() => {
                this.resistanceCursorTimer = null;
                document.body.classList.remove('yui-resistance-cursor-reveal');
                if (!this.destroyed && !this.angryExitTriggered) {
                    document.documentElement.style.cursor = 'none';
                    document.body.style.cursor = 'none';
                }
            }, 3000);

            const resistanceStep = this.getStep('interrupt_resist_light');
            if (!resistanceStep) {
                return;
            }

            const performance = resistanceStep.performance || {};
            const voices = this.resolvePerformanceResistanceVoices(performance);
            const resistanceVoiceKeys = ['interrupt_resist_light_1', 'interrupt_resist_light_3'];
            const resistanceVoiceIndex = Math.max(0, Math.min(resistanceVoiceKeys.length - 1, this.interruptCount - 1));
            const defaultResistanceText = this.resolvePerformanceBubbleText(performance);
            const message = voices.length > 0
                ? voices[(this.interruptCount - 1) % voices.length]
                : defaultResistanceText || '不要拽我啦，还没结束呢！';

            this.pauseCurrentSceneForResistance();
            this.interruptNarrationForResistance();

            this.overlay.hideBubble();
            this.appendGuideChatMessage(message, {
                textKey: resistanceVoiceKeys[resistanceVoiceIndex] === 'interrupt_resist_light_3'
                    ? 'tutorial.yuiGuide.lines.interruptResistLight3'
                    : 'tutorial.yuiGuide.lines.interruptResistLight1'
            });
            this.emotionBridge.apply(performance.emotion || 'surprised');
            Promise.all([
                this.voiceQueue.speak(message, {
                    voiceKey: resistanceVoiceKeys[resistanceVoiceIndex] || ''
                }),
                this.cursor.resistTo(x, y)
            ]).finally(() => {
                this.resumeCurrentSceneAfterResistance();
                const narration = this.activeNarration;
                if (narration && narration.interrupted) {
                    this.scheduleNarrationResume();
                    return;
                }

                this.restoreCurrentScenePresentation();
            });
        }

        async abortAsAngryExit(source) {
            if (this.destroyed || this.angryExitTriggered) {
                return;
            }

            this.angryExitTriggered = true;
            this.clearSceneTimers();
            this.disableInterrupts();

            const angryStep = this.getStep('interrupt_angry_exit');
            const performance = (angryStep && angryStep.performance) || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);

            this.overlay.setTakingOver(true);
            this.overlay.setAngry(true);
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.appendGuideChatMessage(bubbleText || '人类~~~~！你真的很没礼貌喵！', {
                textKey: performance.bubbleTextKey || ''
            });
            this.emotionBridge.apply(performance.emotion || 'angry');
            await this.speakLineAndWait(bubbleText || '', {
                voiceKey: performance.voiceKey
            });
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
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialDestroy === 'function') {
                this.tutorialManager.requestTutorialDestroy(finalReason);
            } else {
                this.destroy();
            }
        }

        skip(reason, tutorialReason) {
            this.requestTermination(reason, tutorialReason);
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.terminationRequested = true;
            this.resumeCurrentSceneAfterResistance();
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
            }
            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.resolve === 'function') {
                this.pluginDashboardHandoff.resolve(false);
            }
            this.cancelActiveNarration();
            this.clearIntroFlow();
            this.clearSceneTimers();
            this.disableInterrupts();
            this.voiceQueue.stop();
            this.cursor.cancel();
            this.cursor.hide();
            this.clearAllVirtualSpotlights();
            this.clearPreciseHighlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            this.emotionBridge.clear();
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 销毁时关闭首页面板失败:', error);
            });
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
            document.removeEventListener('click', this.skipButtonClickHandler, true);
        }

        onKeyDown(event) {
            if (this.destroyed || !event || event.key !== 'Escape') {
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

            if (!this.introChoicePending) {
                return false;
            }

            const button = target.closest('button.message-action-button');
            if (!button) {
                return false;
            }

            const buttonLabel = typeof button.textContent === 'string'
                ? button.textContent.replace(/\s+/g, '').trim()
                : '';
            const introChoiceLabels = this.getIntroChoiceLabels();
            return [introChoiceLabels.skipChat, introChoiceLabels.sayHello].some((label) => {
                return label.replace(/\s+/g, '').trim() === buttonLabel;
            });
        }

        onInteractionGuard(event) {
            if (this.destroyed || this.page !== 'home' || !event || event.isTrusted === false) {
                return;
            }

            if (this.isAllowedTutorialInteractionTarget(event.target)) {
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

        async handlePluginDashboardInterruptRequest(event, handoff, data) {
            const requestId = typeof data.requestId === 'string' ? data.requestId : '';
            if (!requestId) {
                return;
            }

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            const targetOrigin = event && typeof event.origin === 'string' && event.origin
                ? event.origin
                : '*';
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
            const text = typeof detail.text === 'string' ? detail.text : '';
            const textKey = typeof detail.textKey === 'string' ? detail.textKey : '';
            const voiceKey = typeof detail.voiceKey === 'string' ? detail.voiceKey : '';
            const resolvedText = this.resolveGuideCopy(textKey, text);
            const interruptCount = Number.isFinite(detail.interruptCount) ? Math.max(0, Math.floor(detail.interruptCount)) : null;

            if (interruptCount !== null) {
                this.interruptCount = Math.max(
                    Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0)),
                    interruptCount
                );
            }

            if (resolvedText) {
                this.appendGuideChatMessage(resolvedText, {
                    textKey: textKey
                });
            }

            if (resolvedText) {
                try {
                    await this.speakLineAndWait(resolvedText, {
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
            const expectedOrigin = this.getPluginDashboardExpectedOrigin();
            if (expectedOrigin && event.origin !== expectedOrigin) {
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT) {
                void this.handlePluginDashboardInterruptRequest(event, handoff, data);
                return;
            }

            if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_READY_EVENT) {
                handoff.ready = true;
                handoff.readyAt = Date.now();
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
