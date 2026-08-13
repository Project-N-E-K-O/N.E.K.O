/**
 * N.E.K.O 凭证录入脚本
 * 功能：
 * 1. 支持多个平台的凭证录入
 * 2. 提供详细的操作说明
 * 3. 支持自定义字段配置
 * 4. 自动检测并刷新状态
 */
if (!window.nekoWindowControl) {
    window.nekoWindowControl = {
        minimize() {
            document.body.classList.toggle('neko-page-minimized');
            return { ok: true };
        },
        async maximize() {
            if (document.fullscreenElement) {
                await document.exitFullscreen();
            } else {
                await document.documentElement.requestFullscreen();
            }
            return { ok: true, isMaximized: !!document.fullscreenElement };
        },
        isMaximized() {
            return !!document.fullscreenElement;
        }
    };
}

const PLATFORM_CONFIG_DATA = {
    'netease': {
        name: '网易云音乐',
        nameKey: 'cookiesLogin.netease',
        theme: '#c20c0c',
        instructionKey: 'cookiesLogin.instructions.netease',
        fields: [
            { key: 'MUSIC_U', labelKey: 'cookiesLogin.fields.MUSIC_U.label', descKey: 'cookiesLogin.fields.MUSIC_U.desc', required: true },
            { key: 'NMTID', labelKey: 'cookiesLogin.fields.NMTID.label', descKey: 'cookiesLogin.fields.NMTID.desc', required: false }
        ]
    },
    'qqmusic': {
        name: 'QQ音乐',
        nameKey: 'cookiesLogin.qqmusic',
        theme: '#31c27c',
        instructionKey: 'cookiesLogin.instructions.qqmusic',
        fields: [
            { key: 'uin', labelKey: 'cookiesLogin.fields.qqmusicUin.label', descKey: 'cookiesLogin.fields.qqmusicUin.desc', required: true },
            { key: 'qqmusic_key', labelKey: 'cookiesLogin.fields.qqmusicKey.label', descKey: 'cookiesLogin.fields.qqmusicKey.desc', required: true }
        ]
    },
    'bilibili': {
        name: 'Bilibili', 
        nameKey: 'cookiesLogin.bilibili',
        theme: '#40c5f1',
        instructionKey: 'cookiesLogin.instructions.bilibili',
        fields: [
            { key: 'SESSDATA', labelKey: 'cookiesLogin.fields.SESSDATA.label', descKey: 'cookiesLogin.fields.SESSDATA.desc', required: true },
            { key: 'bili_jct', labelKey: 'cookiesLogin.fields.bili_jct.label', descKey: 'cookiesLogin.fields.bili_jct.desc', required: true },
            { key: 'DedeUserID', labelKey: 'cookiesLogin.fields.DedeUserID.label', descKey: 'cookiesLogin.fields.DedeUserID.desc', required: true },
            { key: 'buvid3', labelKey: 'cookiesLogin.fields.buvid3.label', descKey: 'cookiesLogin.fields.buvid3.desc', required: false }
        ]
    },
    'xhh': {
        name: '小黑盒',
        nameKey: 'cookiesLogin.xhh',
        theme: '#222222',
        instructionKey: 'cookiesLogin.instructions.xhh',
        fields: [
            { key: 'user_heybox_id', labelKey: 'cookiesLogin.fields.user_heybox_id.label', descKey: 'cookiesLogin.fields.user_heybox_id.desc', required: true },
            { key: 'user_pkey', labelKey: 'cookiesLogin.fields.user_pkey.label', descKey: 'cookiesLogin.fields.user_pkey.desc', required: true }
        ]
    },
    'youtube': {
        name: 'YouTube',
        nameKey: 'cookiesLogin.youtube',
        theme: '#ff0000',
        instructionKey: 'cookiesLogin.instructions.youtube',
        cookieStringMode: true,
        cookieStringLabelKey: 'cookiesLogin.fields.youtubeCookie.label',
        cookieStringDescKey: 'cookiesLogin.fields.youtubeCookie.desc',
        fields: []
    },
    'twitch': {
        name: 'Twitch',
        nameKey: 'cookiesLogin.twitch',
        theme: '#9146ff',
        instructionKey: 'cookiesLogin.instructions.twitch',
        authMode: 'deviceCode',
        fields: []
    },
    'douyin': {
        name: '抖音', 
        nameKey: 'cookiesLogin.douyin', 
        theme: '#000000',
        instructionKey: 'cookiesLogin.instructions.douyin',
        fields: [
            { key: 'sessionid', labelKey: 'cookiesLogin.fields.sessionid.label', descKey: 'cookiesLogin.fields.sessionid.desc', required: true },
            { key: 'ttwid', labelKey: 'cookiesLogin.fields.ttwid.label', descKey: 'cookiesLogin.fields.ttwid.desc', required: true },
            { key: 'passport_csrf_token', labelKey: 'cookiesLogin.fields.passport_csrf_token.label', descKey: 'cookiesLogin.fields.passport_csrf_token.desc', required: false },
            { key: 'odin_tt', labelKey: 'cookiesLogin.fields.odin_tt.label', descKey: 'cookiesLogin.fields.odin_tt.desc', required: false }
        ]
    },
    'kuaishou': {
        name: '快手', 
        nameKey: 'cookiesLogin.kuaishou', 
        theme: '#ff5000',
        instructionKey: 'cookiesLogin.instructions.kuaishou',
        fields: [
            { key: 'kuaishou.server.web_st', mapKey: 'ks_web_st', labelKey: 'cookiesLogin.fields.ks_web_st.label', descKey: 'cookiesLogin.fields.ks_web_st.desc', required: true },
            { key: 'kuaishou.server.web_ph', mapKey: 'ks_web_ph', labelKey: 'cookiesLogin.fields.ks_web_ph.label', descKey: 'cookiesLogin.fields.ks_web_ph.desc', required: true },
            { key: 'userId', labelKey: 'cookiesLogin.fields.userId.label', descKey: 'cookiesLogin.fields.userId.desc', required: true },
            { key: 'did', labelKey: 'cookiesLogin.fields.did.label', descKey: 'cookiesLogin.fields.did.desc', required: true }
        ]
    },
    'weibo': {
        name: '微博', 
        nameKey: 'cookiesLogin.weibo', 
        theme: '#f59e0b',
        instructionKey: 'cookiesLogin.instructions.weibo',
        fields: [
            { key: 'SUB', labelKey: 'cookiesLogin.fields.SUB.label', descKey: 'cookiesLogin.fields.SUB.desc', required: true },
            { key: 'XSRF-TOKEN', labelKey: 'cookiesLogin.fields.XSRF-TOKEN.label', descKey: 'cookiesLogin.fields.XSRF-TOKEN.desc', required: false }
        ]
    },
    'twitter': {
        name: 'Twitter/X', 
        nameKey: 'cookiesLogin.twitter', 
        theme: '#0ea5e9',
        instructionKey: 'cookiesLogin.instructions.twitter',
        fields: [
            { key: 'auth_token', labelKey: 'cookiesLogin.fields.auth_token.label', descKey: 'cookiesLogin.fields.auth_token.desc', required: true },
            { key: 'ct0', labelKey: 'cookiesLogin.fields.ct0.label', descKey: 'cookiesLogin.fields.ct0.desc', required: true }
        ]
    },
    'reddit': {
        name: 'Reddit', 
        nameKey: 'cookiesLogin.reddit', 
        theme: '#ff4500',
        instructionKey: 'cookiesLogin.instructions.reddit',
        fields: [
            { key: 'reddit_session', labelKey: 'cookiesLogin.fields.reddit_session.label', descKey: 'cookiesLogin.fields.reddit_session.desc', required: true },
            { key: 'csrftoken', labelKey: 'cookiesLogin.fields.csrftoken.label', descKey: 'cookiesLogin.fields.csrftoken.desc', required: false }
        ]
    }
};

function getTranslator() {
    if (typeof window.t === 'function') return window.t;
    if (window.i18n && typeof window.i18n.t === 'function') return window.i18n.t.bind(window.i18n);
    if (window.i18next && typeof window.i18next.t === 'function') return window.i18next.t.bind(window.i18next);
    if (typeof i18next !== 'undefined' && typeof i18next.t === 'function') return i18next.t.bind(i18next);
    return null;
}

// 如果字典还没加载好，坚决返回传入的中文后备(Fallback)
const safeT = (key, fallback = '') => {
    const translator = getTranslator();
    if (!translator) return fallback;
    const result = translator(key);
    // 如果返回的翻译和键名一样，或者为空，说明字典处于未就绪状态
    return (result === key || !result) ? fallback : result;
};

function openCredentialGuide(event) {
    if (event) event.preventDefault();

    const width = Math.min(1120, Math.round(screen.availWidth * 0.8));
    const height = Math.min(860, Math.round(screen.availHeight * 0.82));
    const left = Math.max(0, Math.floor((screen.availWidth - width) / 2));
    const top = Math.max(0, Math.floor((screen.availHeight - height) / 2));
    const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
    const guideUrl = new URL('/api/auth/guide', document.baseURI || window.location.href);
    const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    guideUrl.searchParams.set('theme', theme);
    guideUrl.searchParams.set('v', Date.now().toString());

    const windowName = 'neko_credential_guide';
    const existingWindow = window._openedWindows && window._openedWindows[windowName];
    let openedWindow = null;
    if (existingWindow && !existingWindow.closed) {
        try {
            existingWindow.location.replace(guideUrl.toString());
        } catch (_) {
            existingWindow.location.href = guideUrl.toString();
        }
        openedWindow = existingWindow;
    } else if (typeof window.openOrFocusWindow === 'function') {
        openedWindow = window.openOrFocusWindow(guideUrl.toString(), windowName, features, { navigateOnReuse: true });
    } else {
        openedWindow = window.open(guideUrl.toString(), windowName, features);
    }

    if (openedWindow) {
        window._openedWindows = window._openedWindows || {};
        window._openedWindows[windowName] = openedWindow;
        try { openedWindow.focus(); } catch (_) {}
    }
    return false;
}

const CJK_CHAR_RE = /[\u3400-\u9fff]/u;

const createLocalizedError = (message) => {
    const error = new Error(message);
    error.localized = true;
    return error;
};

function shouldUseRawApiMessage(message) {
    if (typeof message !== 'string') return false;

    const trimmedMessage = message.trim();
    if (!trimmedMessage) return false;

    const currentLanguage = (window.i18next && window.i18next.language) || document.documentElement.lang || '';
    return /^zh(?:-|$)/i.test(currentLanguage) || !CJK_CHAR_RE.test(trimmedMessage);
}

function getLocalizedApiMessage(message, key, fallback) {
    if (shouldUseRawApiMessage(message)) {
        return message.trim();
    }

    return safeT(key, fallback);
}

function getQrStatusMessage(status, message) {
    const fallbackMessages = {
        success: '登录成功',
        expired: '二维码已过期',
        scanned: '已扫码，请在手机上确认',
        waiting: '等待扫码'
    };
    const fallbackTemplates = {
        success: '{{message}}',
        expired: '{{message}}，请刷新',
        scanned: '{{message}}',
        waiting: '{{message}}...'
    };
    const statusMessage = getLocalizedApiMessage(
        message,
        `cookiesLogin.qrLogin.statusMessage.${status}`,
        fallbackMessages[status] || fallbackMessages.waiting
    );

    return safeT(
        `cookiesLogin.qrLogin.status.${status}`,
        fallbackTemplates[status] || fallbackTemplates.waiting
    ).replace('{{message}}', statusMessage);
}

let PLATFORM_CONFIG = {};
let currentPlatform = 'netease';
let twitchDevicePollTimeout = null;
let twitchDevicePollInFlight = false;
let twitchDevicePollActive = false;
let twitchDevicePollIntervalMs = 5000;
let statusDisclosureInitialized = false;
let statusRefreshGeneration = 0;
const deletingPlatforms = new Set();

// 当语言切换时，重新初始化平台配置
function initPlatformConfig() {
    PLATFORM_CONFIG = {};
    for (const [key, data] of Object.entries(PLATFORM_CONFIG_DATA)) {
        
        // 优先尝试翻译平台名称，如果翻译失败则回退到默认中文名
        const translatedName = data.nameKey ? safeT(data.nameKey, data.name) : data.name;

        // 如果是微博，教程里的目标网址显示为 m.weibo.cn
        // 如果是其他平台，教程里的目标名称使用翻译后的名字 (例如 "TikTok")
        const targetDisplay = data.targetDisplay || (key === 'weibo' ? 'm.weibo.cn' : translatedName);

        PLATFORM_CONFIG[key] = {
            name: translatedName, // 界面上显示的名称 (Tabs, 列表) 现在支持多语言了！
            theme: data.theme,
            
            // 附带默认中文提示，自动填入正确的域名或名称
            // 如果字典里有 instructionKey，直接用字典的（字典通常自带了网址）
            // 如果字典没有，则使用这里的模板，并填入 m.weibo.cn 或 翻译后的平台名
            instruction: data.instructionKey
                ? safeT(data.instructionKey, data.instruction || `<b>目标：</b> 请前往 <code>${targetDisplay}</code> 获取这些 Cookies。`)
                : (data.instruction || ''),
            cookieStringMode: data.cookieStringMode === true,
            cookieStringLabel: data.cookieStringLabelKey ? safeT(data.cookieStringLabelKey, '完整 Cookie') : '',
            cookieStringDesc: data.cookieStringDescKey ? safeT(data.cookieStringDescKey, '粘贴 Request Headers 中完整的 Cookie 值') : '',
            authMode: data.authMode || '',
            fields: data.fields.map(field => ({
                key: field.key,
                mapKey: field.mapKey,
                label: field.labelKey ? safeT(field.labelKey, field.label || field.key) : (field.label || field.key),
                desc: field.descKey ? safeT(field.descKey, field.desc || '') : (field.desc || ''),
                required: field.required
            }))
        };
    }
}

// 安全渲染带标签的静态文案，并提供完善的中文回退
function renderStaticHtmlI18n() {
    const htmlSteps = {
        'credential-hero-description': { key: 'cookiesLogin.heroDescription', fallback: '在各平台粘贴 <span class="highlight">Cookie</span> 并加密存储，供后续调用。' }
    };
    // 遍历所有需要翻译的元素 ID
    for (const [id, data] of Object.entries(htmlSteps)) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = DOMPurify.sanitize(safeT(data.key, data.fallback));
    }
    const platformList = document.getElementById('platform-list-content');
    if (platformList) {
        platformList.dataset.emptyText = safeT(
            'cookiesLogin.emptyStatus',
            '还没有保存的凭证呢，选个平台开始添加吧'
        );
    }
    // 更新关闭按钮的标题和图片 alt 文本
    const closeBtn = document.querySelector('[data-neko-window-control="close"]');
    if (closeBtn) {
        const closeText = safeT('common.close', '关闭');
        closeBtn.title = closeText;
        closeBtn.setAttribute('aria-label', closeText);
        const img = closeBtn.querySelector('img');
        if (img) img.alt = closeText;
    }
}

// 当语言切换时，动态更新 HTML 的 lang 属性
function handleLocaleChange() {
    // [新增] 动态更新页面语言标识
    if (window.i18next && window.i18next.language) {
        document.documentElement.lang = window.i18next.language;
    }

    initPlatformConfig();
    renderStaticHtmlI18n(); 
    switchTab(currentPlatform, document.querySelector('.tab-btn.active') || document.querySelector('.tab-btn'), true);
    refreshQrSessionCopy(currentPlatform);
    refreshStatusList();
}
// DOM 加载完成后，初始化平台配置、渲染静态 HTML 翻译并监听语言变化事件
document.addEventListener('DOMContentLoaded', () => {
    window.addEventListener('localechange', handleLocaleChange);

    const characterBanner = document.querySelector('.character-banner');
    const isCredentialInput = (element) =>
        element instanceof Element && element.classList.contains('credential-input');
    let credentialPrivacyRestoreTimer = null;

    document.addEventListener('focusin', (event) => {
        if (isCredentialInput(event.target)) {
            window.clearTimeout(credentialPrivacyRestoreTimer);
            clearMascotReaction();
            window.clearTimeout(mascotBubbleTimer);
            mascotBubble?.classList.remove('visible');
            if (mascotBubble) mascotBubble.textContent = '';
            characterBanner?.classList.add('credential-privacy-active');
        }
    });

    document.addEventListener('focusout', (event) => {
        if (!isCredentialInput(event.target)) return;

        window.clearTimeout(credentialPrivacyRestoreTimer);
        credentialPrivacyRestoreTimer = window.setTimeout(() => {
            if (!isCredentialInput(document.activeElement)) {
                characterBanner?.classList.remove('credential-privacy-active');
            }
        }, 450);
    });

    const mascotButton = document.querySelector('.char-avatar-wrap');
    const mascotBubble = document.querySelector('.mascot-bubble');
    const reactionClasses = ['mascot-angry', 'mascot-success', 'mascot-failure', 'mascot-curious'];
    const MASCOT_DEFERRED_RETRY_MS = 500;
    const MASCOT_DEFERRED_MAX_MS = 4000;
    let mascotReactionTimer = null;
    let mascotDeferredTimer = null;
    let mascotBubbleTimer = null;
    let mascotClicks = [];

    const clearMascotReaction = () => {
        window.clearTimeout(mascotReactionTimer);
        reactionClasses.forEach(className => characterBanner?.classList.remove(className));
    };

    const showMascotReaction = (type, duration) => {
        clearMascotReaction();
        characterBanner.classList.add(`mascot-${type}`);
        mascotReactionTimer = window.setTimeout(clearMascotReaction, duration);
    };

    // 隐私遮罩期间把成功/失败反应顺延到失焦之后，但最多顺延 MASCOT_DEFERRED_MAX_MS：
    // 不设上限的话，只要用户一直聚焦凭证输入框，定时器就会无限自我重排，
    // 等失焦时弹出的是几分钟前那次保存的表情。
    const deferMascotReaction = (type, duration, deadline) => {
        window.clearTimeout(mascotDeferredTimer);
        mascotDeferredTimer = null;
        if (Date.now() >= deadline) return;
        mascotDeferredTimer = window.setTimeout(() => {
            mascotDeferredTimer = null;
            if (!characterBanner) return;
            if (characterBanner.classList.contains('credential-privacy-active')) {
                deferMascotReaction(type, duration, deadline);
                return;
            }
            showMascotReaction(type, duration);
        }, MASCOT_DEFERRED_RETRY_MS);
    };

    window.triggerMascotReaction = (type, duration = 900) => {
        if (!characterBanner) return;
        if (characterBanner.classList.contains('credential-privacy-active')) {
            if (type === 'success' || type === 'failure') {
                deferMascotReaction(type, duration, Date.now() + MASCOT_DEFERRED_MAX_MS);
            }
            return;
        }
        window.clearTimeout(mascotDeferredTimer);
        mascotDeferredTimer = null;
        showMascotReaction(type, duration);
    };

    mascotButton?.addEventListener('click', (event) => {
        if (characterBanner?.classList.contains('credential-privacy-active')) return;

        if (event.target instanceof Element && event.target.closest('.char-status-dot')) {
            window.triggerMascotReaction('curious', 650);
            return;
        }

        const now = Date.now();
        mascotClicks = mascotClicks.filter(clickedAt => now - clickedAt < 850);
        mascotClicks.push(now);
        window.triggerMascotReaction('angry', mascotClicks.length >= 3 ? 1350 : 900);

        if (mascotClicks.length >= 3 && mascotBubble) {
            mascotClicks = [];
            window.clearTimeout(mascotBubbleTimer);
            mascotBubble.textContent = safeT('cookiesLogin.mascotPokeMessage', '不要一直戳啦！');
            mascotBubble.classList.add('visible');
            mascotBubbleTimer = window.setTimeout(() => {
                mascotBubble.classList.remove('visible');
                mascotBubble.textContent = '';
            }, 1350);
        }
    });

    if (getTranslator()) {
        handleLocaleChange();
        return;
    }

    // 初次加载无论如何都渲染一次（带兜底中文），然后等待语言就绪事件刷新
    initPlatformConfig();
    renderStaticHtmlI18n();
    
    const firstTab = document.querySelector('.tab-btn');
    if (firstTab) switchTab('netease', firstTab, true);
    refreshStatusList();
});

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        clearTwitchDevicePollTimer();
    } else if (twitchDevicePollActive) {
        scheduleTwitchDevicePoll();
    }
});

let qrSupportedPlatforms = null;
let qrSupportedPlatformsRequest = null;
let qrEntryGeneration = 0;

async function getQrSupportedPlatforms() {
    if (qrSupportedPlatforms) return qrSupportedPlatforms;
    if (!qrSupportedPlatformsRequest) {
        qrSupportedPlatformsRequest = fetch('/api/auth/get_CanQRLoginList')
            .then(resp => {
                if (!resp.ok) throw new Error(`QR support list request failed: ${resp.status}`);
                return resp.json();
            })
            .then(platforms => {
                qrSupportedPlatforms = Array.isArray(platforms) ? platforms : [];
                return qrSupportedPlatforms;
            })
            .catch(error => {
                qrSupportedPlatformsRequest = null;
                throw error;
            });
    }
    return qrSupportedPlatformsRequest;
}

async function showQRLogin(config, platformKey) {
    const entryGeneration = ++qrEntryGeneration;
    const qrLoginBox = document.getElementById('QRLogin');
    if (!qrLoginBox) return;

    // 清理之前的状态
    qrLoginBox.replaceChildren();
    qrLoginBox.classList.remove('collapsed');
    qrLoginBox.style.display = 'none';

    let supportedPlatforms;
    try {
        supportedPlatforms = await getQrSupportedPlatforms();
    } catch (error) {
        if (entryGeneration !== qrEntryGeneration) return;
        console.error('Unable to load QR-supported platforms:', error);
        return;
    }
    if (entryGeneration !== qrEntryGeneration || currentPlatform !== platformKey) return;

    // 采用多重匹配：优先转换后台返回的列表为全小写比对 platformKey，同时兼容已有的原始比对以防止破坏遗留代码
    const isSupported = supportedPlatforms.map(k => k.toLowerCase()).includes(platformKey.toLowerCase()) || supportedPlatforms.includes(config["name"]);

    if (isSupported){
        const QRinfo =  document.createElement("div");
        const butt = document.createElement("button");
        // Keep QR entry buttons on each platform's own color; inherited app variables
        // can resolve to light values and previously left a white button in dark mode.
        const buttonTheme = config["theme"];
        const buttonBorder = buttonTheme;
        QRinfo.dataset.i18n = 'cookiesLogin.qrLogin.tryQR';
        QRinfo.textContent = safeT('cookiesLogin.qrLogin.tryQR', '或者...试试扫码登录?');
        QRinfo.className = 'qr-login-hint';
        butt.dataset.i18n = 'cookiesLogin.qrLogin.openQR';
        butt.textContent = safeT('cookiesLogin.qrLogin.openQR', '打开扫码登录');
        butt.className = 'qr-open-btn';
        butt.style.setProperty('--qr-platform-color', buttonTheme);
        butt.style.setProperty('--qr-platform-border', buttonBorder);
        butt.onclick = function(){requestQR(config, platformKey)};
        qrLoginBox.appendChild(QRinfo);
        qrLoginBox.appendChild(butt);
        qrLoginBox.style.removeProperty('display');
    }else{
        // let a = 1;希望这里可以空着不会报错 报错了就肘喵老师
        // 当前只做了"Bilibili"扫码登录,其他平台再说吧
    }
}

let qrPollTimeout = null;
let qrPollInFlight = false;
let qrRefreshTimeout = null;
let currentQrKey = null;
let qrRequestGeneration = 0;
let qrRequestAbortController = null;

// #submit-btn 是所有平台共用的一个按钮：保存凭证和启动 Twitch 授权都会禁用它。
// 用代数把「谁禁用的」和「谁有资格恢复」绑起来——旧请求晚到的 finally 不许恢复
// 按钮（否则会解开新请求刚上的锁），切平台则作废当前代数并直接放行新平台。
let submitButtonGeneration = 0;

function beginSubmitButtonLock() {
    const generation = ++submitButtonGeneration;
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.disabled = true;
    return generation;
}

function releaseSubmitButtonLock(generation) {
    if (generation !== submitButtonGeneration) return;
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.disabled = false;
}

function cancelQrRequest() {
    qrRequestGeneration++;
    qrRequestAbortController?.abort();
    qrRequestAbortController = null;
}

function refreshQrSessionCopy(platformKey) {
    document.querySelectorAll('#QRLogin [data-i18n]').forEach(element => {
        const key = element.dataset.i18n;
        element.textContent = safeT(key, element.textContent);
    });
    const config = PLATFORM_CONFIG[platformKey];
    const title = document.getElementById('qr-scan-title');
    if (title && config) {
        title.textContent = safeT('cookiesLogin.qrLogin.scanTitle', '扫码登录 {{platform}}')
            .replace('{{platform}}', config.name);
    }
    const validity = document.getElementById('qr-valid-for');
    if (validity?.dataset.seconds) {
        validity.textContent = safeT('cookiesLogin.qrLogin.validFor', '二维码有效期: {{seconds}}秒')
            .replace('{{seconds}}', validity.dataset.seconds);
    }
    const image = document.querySelector('#QRLogin img');
    if (image) image.alt = safeT('cookiesLogin.qrLogin.qrCodeAlt', 'QR code');
}

async function requestQR(config, platformKey) {
    if (qrRefreshTimeout) {
        clearTimeout(qrRefreshTimeout);
        qrRefreshTimeout = null;
    }
    cancelQrRequest();
    qrEntryGeneration++;
    stopQrPoll();
    currentQrKey = null;
    const requestGeneration = qrRequestGeneration;
    const abortController = new AbortController();
    qrRequestAbortController = abortController;
    const qrLoginBox = document.getElementById('QRLogin');
    qrLoginBox.innerHTML = `
        <div class="qr-session-loading">
            <div data-i18n="cookiesLogin.qrLogin.loading">${safeT('cookiesLogin.qrLogin.loading', '正在获取二维码...')}</div>
        </div>
    `;
    
    try {
        const response = await fetch('/api/auth/get_QR', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platform: platformKey }),
            signal: abortController.signal
        });

        if (requestGeneration !== qrRequestGeneration || currentPlatform !== platformKey) return;
        const result = await response.json();
        if (requestGeneration !== qrRequestGeneration || currentPlatform !== platformKey) return;
        if (!response.ok) {
            throw createLocalizedError(
                getLocalizedApiMessage(
                    result?.detail || result?.message,
                    'cookiesLogin.qrLogin.fetchFailed',
                    '获取二维码失败，请稍后重试'
                )
            );
        }
        if (result.success && result.data) {
            currentQrKey = result.data.qrcode_key;
            const timeout = result.data.timeout || 180;
            
            qrLoginBox.innerHTML = `
                <div class="qr-session">
                    <button id="qr-collapse-action" class="qr-collapse-btn">
                        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        <span data-i18n="common.collapse">${safeT('common.collapse', '收起')}</span>
                    </button>
                    <div id="qr-scan-title" class="qr-session-title">${safeT('cookiesLogin.qrLogin.scanTitle', '扫码登录 {{platform}}').replace('{{platform}}', PLATFORM_CONFIG[platformKey]?.name || config["name"])}</div>
                    <img class="qr-session-image" src="${result.data.qrcode_image}" alt="${safeT('cookiesLogin.qrLogin.qrCodeAlt', 'QR code')}">
                    <div id="qr-status" class="qr-session-status">${safeT('cookiesLogin.qrLogin.waiting', '等待扫码...')}</div>
                    <div id="qr-valid-for" class="qr-session-validity" data-seconds="${timeout}">${safeT('cookiesLogin.qrLogin.validFor', '二维码有效期: {{seconds}}秒').replace('{{seconds}}', timeout)}</div>
                    <button id="qr-refresh-btn" class="qr-session-refresh" data-i18n="cookiesLogin.qrLogin.refreshQR">${safeT('cookiesLogin.qrLogin.refreshQR', '刷新二维码')}</button>
                </div>
            `;
            
            document.getElementById('qr-collapse-action').onclick = function() {
                stopQrPoll();
                currentQrKey = null;
                showQRLogin(config, platformKey);
            };
            
            document.getElementById('qr-refresh-btn').onclick = function() {
                currentQrKey = null;
                stopQrPoll();
                requestQR(config, platformKey);
            };
            
            startQrPoll(config, platformKey);
        } else {
            qrLoginBox.innerHTML = `
                <div class="qr-session-error">
                    ${safeT('cookiesLogin.qrLogin.fetchFailed', '获取二维码失败，请稍后重试')}
                    <button id="qr-retry-btn" class="qr-session-retry">${safeT('cookiesLogin.qrLogin.retry', '重试')}</button>
                </div>
            `;
            document.getElementById('qr-retry-btn').onclick = function() {
                requestQR(config, platformKey);
            };
        }
    } catch (err) {
        if (err?.name === 'AbortError' || requestGeneration !== qrRequestGeneration) return;
        console.error("Request QR error:", err);
        if (currentPlatform !== platformKey) return;
        const errorMessage = err?.localized === true
            ? err.message
            : safeT('cookiesLogin.qrLogin.networkError', '网络请求失败，请检查连接');
        qrLoginBox.innerHTML = `
            <div class="qr-session-error">
                ${errorMessage}
                <button id="qr-retry-btn-err" class="qr-session-retry">${safeT('cookiesLogin.qrLogin.retry', '重试')}</button>
            </div>
        `;
        document.getElementById('qr-retry-btn-err').onclick = function() {
            requestQR(config, platformKey);
        };
    } finally {
        if (requestGeneration === qrRequestGeneration) {
            qrRequestAbortController = null;
        }
    }
}

function startQrPoll(config, platformKey) {
    stopQrPoll();

    const pollOnce = async () => {
        let shouldContinuePolling = true;
        const expectedQrKey = currentQrKey;

        if (!expectedQrKey) {
            stopQrPoll();
            return;
        }

        if (qrPollInFlight) return;
        qrPollInFlight = true;

        try {
            const response = await fetch('/api/auth/QRLogin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    platform: platformKey, 
                    qrcode_key: expectedQrKey 
                })
            });

            const result = await response.json();
            if (!response.ok) {
                throw createLocalizedError(
                    getLocalizedApiMessage(
                        result?.detail || result?.message,
                        'cookiesLogin.qrLogin.networkError',
                        '网络请求失败，请检查连接'
                    )
                );
            }

            if (currentPlatform !== platformKey || currentQrKey !== expectedQrKey) {
                shouldContinuePolling = false;
                return;
            }
            
            const statusEl = document.getElementById('qr-status');
            const data = result.data;
            const setStatusSpan = (color, text, fontWeight = 'normal') => {
                if (!statusEl) return;
                const span = document.createElement('span');
                span.style.color = color;
                span.style.fontWeight = fontWeight;
                span.textContent = text;
                statusEl.replaceChildren(span);
            };

            if (result.success && data?.status === 'success') {
                shouldContinuePolling = false;
                stopQrPoll();
                setStatusSpan(
                    '#22c55e',
                    getQrStatusMessage('success', data.message),
                    '600'
                );

                const cookies = data.cookies;
                const cookieFields = data.cookie_fields || [];
                let capturedCount = 0;

                cookieFields.forEach(field => {
                    if (cookies && cookies[field]) {
                        const input = document.getElementById('input-' + field);
                        if (input) {
                            input.value = cookies[field];
                            capturedCount++;
                        }
                    }
                });

                // 统一成功提醒
                let customAlert = data.local_save_failed
                    ? safeT('cookiesLogin.qrLogin.localSaveFailed', '扫码登录成功，但自动保存失败。凭证已填入，请手动点击保存配置。')
                    : safeT('cookiesLogin.qrLogin.successAlert', '扫码登录成功！Cookie 已自动填入，请点击保存配置');
                if (capturedCount === 0 && cookieFields.length > 0) {
                  customAlert = safeT('cookiesLogin.qrLogin.extractFailed', '扫码成功但未能自动提取到字段，请手动检查。');
                }

                showAlert(capturedCount > 0 && !data.local_save_failed, customAlert);

                if (qrRefreshTimeout) {
                    clearTimeout(qrRefreshTimeout);
                }
                qrRefreshTimeout = setTimeout(() => {
                    if (currentPlatform !== platformKey) return;
                    showQRLogin(config, platformKey);
                    qrRefreshTimeout = null;
                }, 3000); // 延长一秒显示成功状态防止闪烁

            } else if (data) {
                const status = data.status;
                const message = data.message;

                if (statusEl) {
                    if (status === 'expired') {
                        shouldContinuePolling = false;
                        setStatusSpan(
                            '#ef4444',
                            getQrStatusMessage('expired', message)
                        );
                        stopQrPoll();
                    } else if (status === 'scanned') {
                        setStatusSpan(
                            '#f59e0b',
                            getQrStatusMessage('scanned', message)
                        );
                    } else if (status === 'waiting') {
                        statusEl.textContent = getQrStatusMessage('waiting', message);
                    } else {
                        statusEl.textContent = getLocalizedApiMessage(message, 'cookiesLogin.qrLogin.waiting', '等待扫码...');
                    }
                }
            } else {
                shouldContinuePolling = false;
                stopQrPoll();
            }
        } catch (err) {
            console.error("Poll error:", err);
            shouldContinuePolling = false;
            if (currentPlatform === platformKey && currentQrKey === expectedQrKey) {
                const statusEl = document.getElementById('qr-status');
                if (statusEl) {
                    statusEl.textContent = err?.localized === true
                        ? err.message
                        : safeT('cookiesLogin.qrLogin.networkError', '网络请求失败，请检查连接');
                }
            }
            stopQrPoll();
        } finally {
            if (currentPlatform === platformKey && currentQrKey === expectedQrKey) {
                qrPollInFlight = false;
                if (shouldContinuePolling && currentQrKey === expectedQrKey) {
                    qrPollTimeout = setTimeout(pollOnce, 1500);
                }
            }
        }
    };

    pollOnce();
}

function stopQrPoll() {
    if (qrPollTimeout) {
        clearTimeout(qrPollTimeout);
        qrPollTimeout = null;
    }
    qrPollInFlight = false;
}



// 切换选项卡时，更新当前平台配置
function switchTab(platformKey, btnElement, isReRender = false) {
    if (!PLATFORM_CONFIG[platformKey]) {
        console.error(`PLATFORM_CONFIG is missing for ${platformKey}`);
        return;
    }

    if (!isReRender && currentPlatform === platformKey && btnElement && btnElement.classList.contains('active')) {
        return;
    }

    const previousPlatform = currentPlatform;
    const existingQrBox = document.getElementById('QRLogin');
    const preserveQrState = isReRender && previousPlatform === platformKey && Boolean(
        existingQrBox?.childElementCount || currentQrKey || qrRequestAbortController
    );
    const existingTwitchResult = isReRender && previousPlatform === 'twitch'
        ? document.getElementById('twitch-device-result')
        : null;
    if (!isReRender && previousPlatform !== platformKey) {
        stopTwitchDevicePoll();
    }

    if (!preserveQrState) {
        qrEntryGeneration++;
        cancelQrRequest();
        stopQrPoll();
        currentQrKey = null;
        if (qrRefreshTimeout) {
            clearTimeout(qrRefreshTimeout);
            qrRefreshTimeout = null;
        }
    }
    currentPlatform = platformKey;
    const config = PLATFORM_CONFIG[platformKey];
    const tutorialBanner = document.querySelector('#main-panel > .tutorial-link');
    // 通用教程教的是「在 Application 面板逐个字段复制 Cookie Value」，
    // 对 cookieStringMode 平台（YouTube 要整条 Cookie 请求头）是错的流程，
    // 这些平台改由自己的 instruction 文案指路，所以这里一并隐藏。
    const hideTutorial = Boolean(config.authMode || config.cookieStringMode);
    if (tutorialBanner) tutorialBanner.style.display = hideTutorial ? 'none' : '';
    const encryptRow = document.getElementById('encrypt-toggle')?.parentElement;
    if (encryptRow) encryptRow.style.display = config.authMode ? 'none' : '';
    // 更新选项卡文本
    if (btnElement) {
        document.querySelectorAll('.tab-btn').forEach(btn =>{
            const active = btn === btnElement;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-selected', String(active));
        });
    }
    // 更新面板描述
    const descBox = document.getElementById('panel-desc');
    if (descBox) {
        if (config.instruction && config.instruction.trim() !== '') {
            descBox.style.display = 'block'; 
            descBox.style.borderColor = '';
            descBox.innerHTML = DOMPurify.sanitize(config.instruction);
        } else {
            descBox.style.display = 'none'; 
        }
    }
    if (config.authMode === 'deviceCode') {
        const qrLoginBox = document.getElementById('QRLogin');
        if (qrLoginBox) {
            qrLoginBox.replaceChildren();
            qrLoginBox.style.display = 'none';
        }
    } else if (!preserveQrState) {
        showQRLogin(PLATFORM_CONFIG_DATA[platformKey], platformKey);
    }
    // 更新动态 Cookies 配置字段
    const fieldsContainer = document.getElementById('dynamic-fields');
    if (fieldsContainer) {
        const existingValues = {};
        if (isReRender) {
            document.querySelectorAll('.credential-input').forEach(input => {
                existingValues[input.id] = input.value;
            });
        }

        const placeholderBase = safeT('cookiesLogin.pasteHere', '在此粘贴');
        if (config.authMode === 'deviceCode') {
            fieldsContainer.innerHTML = `
            <div class="field-group">
                <label for="input-twitch-client-id">
                    <span>${DOMPurify.sanitize(safeT('cookiesLogin.twitchAuth.clientId', 'Twitch Developer Client ID'))} <span class="req-star">*</span></span>
                    <span class="desc">${DOMPurify.sanitize(safeT('cookiesLogin.twitchAuth.clientIdDesc', 'Create a public app in the Twitch Developer Console and paste its Client ID.'))}</span>
                </label>
                <input type="text" id="input-twitch-client-id" autocomplete="off" autocapitalize="off" spellcheck="false" class="credential-input">
            </div>
            <div id="twitch-device-result" aria-live="polite"></div>`;
            const freshTwitchResult = fieldsContainer.querySelector('#twitch-device-result');
            if (existingTwitchResult && freshTwitchResult) {
                freshTwitchResult.replaceWith(existingTwitchResult);
            }
        } else if (config.cookieStringMode) {
            fieldsContainer.innerHTML = `
            <div class="field-group">
                <label for="input-cookie-string">
                    <span>${DOMPurify.sanitize(config.cookieStringLabel)} <span class="req-star">*</span></span>
                    <span class="desc">${DOMPurify.sanitize(config.cookieStringDesc)}</span>
                </label>
                <textarea id="input-cookie-string"
                          rows="6"
                          autocomplete="off"
                          autocapitalize="off"
                          spellcheck="false"
                          class="credential-input"></textarea>
            </div>`;
            const cookieInput = document.getElementById('input-cookie-string');
            if (cookieInput) cookieInput.placeholder = `${placeholderBase} Cookie...`;
        } else {
            // 渲染动态 Cookies 配置字段
            fieldsContainer.innerHTML = config.fields.map((f, index) => {
                const inputId = `input-${f.mapKey || f.key}`;

                return `
            <div class="field-group">
                <label for="${inputId}">
                    <span>${DOMPurify.sanitize(f.label)} ${f.required ? '<span class="req-star">*</span>' : ''}</span>
                    <span class="desc">${DOMPurify.sanitize(f.desc)}</span>
                </label>
                <input type="text" id="${inputId}" 
                       data-field-index="${index}"
                       autocomplete="off" 
                       class="credential-input">
            </div>
            `}).join('');

            fieldsContainer.querySelectorAll('.credential-input').forEach((inputEl) => {
                const idx = Number(inputEl.getAttribute('data-field-index'));
                const field = config.fields[idx];
                if (field) {
                    inputEl.placeholder = `${placeholderBase} ${field.key}...`;
                }
            });
        }
        
        if (isReRender) {
            Object.entries(existingValues).forEach(([id, preservedValue]) => {
                const input = document.getElementById(id);
                if (input) input.value = preservedValue;
           });
        }
    }

    // 换平台就作废上一轮的按钮锁：旧请求还在飞不该拖住新平台提交，
    // 它晚到的 finally 也会因为代数对不上而不再动这个共用按钮。
    // 只在真正换平台时放行——语言切换走的是同平台重渲染，那时候在飞的请求
    // 还是当前平台的，放开按钮等于允许重复提交。
    if (!isReRender && previousPlatform !== platformKey) {
        submitButtonGeneration++;
        const sharedSubmitBtn = document.getElementById('submit-btn');
        if (sharedSubmitBtn) sharedSubmitBtn.disabled = false;
    }

    // 更新提交按钮文本
    const submitText = document.getElementById('submit-text');
    if (submitText) {
        const translatedText = config.authMode === 'deviceCode'
            ? safeT('cookiesLogin.twitchAuth.start', '开始 Twitch 授权')
            : safeT('cookiesLogin.saveConfig', '保存配置');
        submitText.textContent = `${config.name} ${translatedText}`;
    }
}

function twitchClientId() {
    return document.getElementById('input-twitch-client-id')?.value.trim() || '';
}

function clearTwitchDevicePollTimer() {
    if (twitchDevicePollTimeout) {
        clearTimeout(twitchDevicePollTimeout);
        twitchDevicePollTimeout = null;
    }
}

function stopTwitchDevicePoll() {
    twitchDevicePollActive = false;
    clearTwitchDevicePollTimer();
}

function scheduleTwitchDevicePoll() {
    clearTwitchDevicePollTimer();
    if (!twitchDevicePollActive || currentPlatform !== 'twitch' || document.hidden) return;
    twitchDevicePollTimeout = setTimeout(async () => {
        twitchDevicePollTimeout = null;
        const outcome = await checkTwitchDeviceCode(null, true);
        if (outcome === 'pending' || outcome === 'in_flight') {
            scheduleTwitchDevicePoll();
        }
    }, twitchDevicePollIntervalMs);
}

function startTwitchDevicePoll(intervalSeconds) {
    stopTwitchDevicePoll();
    const seconds = Number(intervalSeconds);
    twitchDevicePollIntervalMs = Math.max(1, Math.min(Number.isFinite(seconds) ? seconds : 5, 60)) * 1000;
    twitchDevicePollActive = true;
    scheduleTwitchDevicePoll();
}

function renderTwitchDeviceCode(result) {
    const container = document.getElementById('twitch-device-result');
    if (!container) return;
    container.textContent = '';
    const card = document.createElement('div');
    card.className = 'twitch-auth-card';
    card.style.marginTop = '18px';
    const instruction = document.createElement('div');
    instruction.textContent = safeT('cookiesLogin.twitchAuth.authorizeHint', 'Open the Twitch activation page and enter this code:');
    const link = document.createElement('a');
    link.href = result.verification_uri;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = result.verification_uri;
    link.style.display = 'block';
    link.style.margin = '8px 0';
    const code = document.createElement('strong');
    code.textContent = result.user_code;
    code.style.fontSize = '20px';
    code.style.letterSpacing = '0.08em';
    const checkButton = document.createElement('button');
    checkButton.type = 'button';
    checkButton.className = 'submit-btn';
    checkButton.style.marginTop = '14px';
    checkButton.textContent = safeT('cookiesLogin.twitchAuth.check', '我已授权，检查状态');
    checkButton.addEventListener('click', () => checkTwitchDeviceCode(checkButton));
    card.append(instruction, link, code, checkButton);
    container.appendChild(card);
}

async function startTwitchDeviceCode() {
    const clientId = twitchClientId();
    const platformAtStart = currentPlatform;
    if (!/^[A-Za-z0-9]{8,80}$/.test(clientId)) {
        showAlert(false, safeT('cookiesLogin.twitchAuth.invalidClientId', '请输入有效的 Twitch Client ID'));
        document.getElementById('input-twitch-client-id')?.focus();
        return;
    }
    stopTwitchDevicePoll();
    const submitLock = beginSubmitButtonLock();
    try {
        const response = await fetch('/api/auth/twitch/device/start', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_id: clientId })
        });
        const result = await response.json();
        if (currentPlatform !== platformAtStart || twitchClientId() !== clientId) return;
        if (response.ok && result.success) {
            renderTwitchDeviceCode(result);
            startTwitchDevicePoll(result.interval);
            showAlert(true, safeT('cookiesLogin.twitchAuth.started', 'Twitch 授权已启动，请在浏览器完成确认'));
        } else {
            showAlert(false, safeT('cookiesLogin.twitchAuth.startFailed', '无法启动 Twitch 授权，请检查 Client ID 和网络'));
        }
    } catch (_) {
        if (currentPlatform === platformAtStart) {
            showAlert(false, safeT('cookiesLogin.networkError', '网络请求失败，请检查连接'));
        }
    } finally {
        releaseSubmitButtonLock(submitLock);
    }
}

async function checkTwitchDeviceCode(button, automatic = false) {
    if (twitchDevicePollInFlight) return 'in_flight';
    twitchDevicePollInFlight = true;
    const clientId = twitchClientId();
    const platformAtStart = currentPlatform;
    if (button) button.disabled = true;
    try {
        const response = await fetch('/api/auth/twitch/device/check', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_id: clientId })
        });
        const result = await response.json();
        if (currentPlatform !== platformAtStart || twitchClientId() !== clientId) return 'cancelled';
        if (response.ok && result.success && result.logged_in) {
            stopTwitchDevicePoll();
            showAlert(true, safeT('cookiesLogin.twitchAuth.authorized', 'Twitch 凭证已加密保存'));
            document.getElementById('twitch-device-result')?.replaceChildren();
            refreshStatusList({ reveal: true });
            return 'authorized';
        } else if (response.ok && result.pending) {
            if (!automatic) {
                showAlert(true, safeT('cookiesLogin.twitchAuth.pending', '授权尚未完成，请在 Twitch 页面确认后重试'));
            }
            return 'pending';
        } else {
            stopTwitchDevicePoll();
            showAlert(false, safeT('cookiesLogin.twitchAuth.checkFailed', '授权检查失败，请重新开始授权'));
            return 'failed';
        }
    } catch (_) {
        if (currentPlatform !== platformAtStart || twitchClientId() !== clientId) return 'cancelled';
        stopTwitchDevicePoll();
        showAlert(false, safeT('cookiesLogin.networkError', '网络请求失败，请检查连接'));
        return 'failed';
    } finally {
        twitchDevicePollInFlight = false;
        if (button) button.disabled = false;
    }
}

// 提交当前平台的 Cookies 配置
async function submitCurrentCookie() {
    const submittedPlatform = currentPlatform;
    const config = PLATFORM_CONFIG[submittedPlatform];
    if (config.authMode === 'deviceCode') {
        await startTwitchDeviceCode();
        return;
    }
    let cookieString = '';

    if (config.cookieStringMode) {
        const cookieInput = document.getElementById('input-cookie-string');
        const rawCookieString = cookieInput ? cookieInput.value : '';
        cookieString = rawCookieString.trim();
        if (!cookieString) {
            const message = safeT('cookiesLogin.requiredField', '请填写必填项: {{fieldName}}')
                .replace('{{fieldName}}', config.cookieStringLabel);
            showAlert(false, message);
            cookieInput?.focus();
            return;
        }
    } else {
        const cookiePairs = [];
        // 遍历配置字段，收集 Cookies 配置
        for (const f of config.fields) {
            const fieldId = `input-${f.mapKey || f.key}`;
            const inputEl = document.getElementById(fieldId);
            const rawVal = inputEl ? inputEl.value : '';
            // 检查必填项
            if (f.required && !rawVal.trim()) {
                const message = safeT('cookiesLogin.requiredField', '请填写必填项: {{fieldName}}').replace('{{fieldName}}', f.label);
                showAlert(false, message);
                inputEl?.focus();
                return;
            }
            // 过滤非法字符
            if (rawVal !== '') {
                let sanitizedVal = rawVal;
                if (/[\r\n\t<>'";]/.test(sanitizedVal)) {
                    sanitizedVal = sanitizedVal.replace(/[\r\n\t]/g, '').replace(/[<>'"]/g, '').replace(/;/g, '');
                    const message = safeT('cookiesLogin.invalidChars', '{{fieldName}} 包含非法字符，已自动过滤').replace('{{fieldName}}', f.label);
                    showAlert(false, message);
                }
                // 检查是否有首尾空格
                const prevVal = sanitizedVal;
                sanitizedVal = sanitizedVal.trim();
                if (sanitizedVal !== prevVal) {
                    const message = safeT('cookiesLogin.whitespaceTrimmed', '{{fieldName}} 已自动去除首尾空格').replace('{{fieldName}}', f.label);
                    showAlert(false, message);
                }
                if (!sanitizedVal) {
                    if (f.required) {
                        const message = safeT('cookiesLogin.requiredField', '请填写必填项: {{fieldName}}')
                            .replace('{{fieldName}}', f.label);
                        showAlert(false, message);
                        inputEl?.focus();
                        return;
                    }
                    continue;
                }
                cookiePairs.push(`${f.key}=${sanitizedVal}`);
            }
        }
        // 检查是否有 Cookies 配置
        if (cookiePairs.length === 0) {
            showAlert(false, safeT('cookiesLogin.noCookies', '请先配置 Cookies'));
            return;
        }
        cookieString = cookiePairs.join('; ');
    }
    const submitText = document.getElementById('submit-text');
    const encryptToggle = document.getElementById('encrypt-toggle');
    // 禁用提交按钮，防止重复点击
    const submitLock = beginSubmitButtonLock();
    if (submitText) submitText.textContent = safeT('cookiesLogin.submitting', '安全加密传输中...');
    // 发送 POST 请求保存 Cookies
    try {
        const response = await fetch('/api/auth/cookies/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                platform: submittedPlatform,
                cookie_string: cookieString,
                encrypt: encryptToggle ? encryptToggle.checked : false
            })
        });
        const result = await response.json();
        // 检查是否成功保存
        if (response.ok && result.success) {
            const message = safeT('cookiesLogin.credentialsSaved', '{{platformName}} 凭证已保存').replace('{{platformName}}', config.name);
            showAlert(true, message);
            window.triggerMascotReaction?.('success', 1100);
            if (currentPlatform === submittedPlatform) {
                document.querySelectorAll('.credential-input').forEach(i => i.value = '');
            }
            refreshStatusList({ reveal: true });
        } else {
            const rawMessage = Array.isArray(result?.detail)
                ? result.detail.map(e => e.msg || JSON.stringify(e)).join('; ')
                : (result?.detail || result?.message);
            const message = getLocalizedApiMessage(rawMessage, 'cookiesLogin.saveFailed', '保存失败，请检查格式是否正确');
            showAlert(false, message);
            window.triggerMascotReaction?.('failure', 1050);
        }
    } catch (err) {
        const message = safeT('cookiesLogin.networkError', '网络请求失败，请检查连接');
        showAlert(false, message);
        window.triggerMascotReaction?.('failure', 1050);
        console.error("Submit error:", err);
    } finally {
        releaseSubmitButtonLock(submitLock);
        if (submitText && currentPlatform === submittedPlatform) {
            const activeConfig = PLATFORM_CONFIG[submittedPlatform];
            const translatedText = activeConfig.authMode === 'deviceCode'
                ? safeT('cookiesLogin.twitchAuth.start', '开始 Twitch 授权')
                : safeT('cookiesLogin.saveConfig', '保存配置');
            submitText.textContent = `${activeConfig.name} ${translatedText}`;
        }
    }
}

// 刷新当前平台的状态列表
// 重新设计的状态监控列表渲染引擎 (修复缓存与状态判定问题)
async function refreshStatusList({ reveal = false } = {}) {
    const container = document.getElementById('platform-list-content');
    if (!container) return;
    const refreshGeneration = ++statusRefreshGeneration;
    const platforms = Object.keys(PLATFORM_CONFIG);
    try {
        const entries = await Promise.all(
            // 强制禁用 GET 缓存，保证每次拉取的都是最新状态！
            platforms.map(async platform => {
                try {
                    const response = await fetch(`/api/auth/cookies/${platform}`, { cache: 'no-store' });
                    if (!response.ok) throw new Error(`Status request failed: ${response.status}`);
                    return { loaded: true, data: await response.json() };
                } catch (_) {
                    return { loaded: false, data: null };
                }
            })
        );
        if (refreshGeneration !== statusRefreshGeneration) return;

        const results = entries.map(entry => entry.data || { success: false });
        const hasLoadFailure = entries.some(entry => !entry.loaded);
        container.textContent = '';
        const isActiveResponse = res => res.success === true && (
            res.has_cookies === true ||
            res.data?.has_cookies === true ||
            res.data === true
        );
        const hasActiveCredentials = results.some(isActiveResponse);
        const hasVisibleStatus = hasActiveCredentials || hasLoadFailure;
        const statusArea = document.getElementById('status-area');
        if (statusArea) {
            if (reveal || hasLoadFailure) {
                statusArea.open = true;
            } else if (!statusDisclosureInitialized || !hasVisibleStatus) {
                statusArea.open = hasVisibleStatus;
            }
            statusDisclosureInitialized = true;
        }
        results.forEach((res, idx) => {
            const key = platforms[idx];
            const cfg = PLATFORM_CONFIG[key];
            
            // 兼容多种后端返回的数据结构
            // 无论后端是 { success: true, data: { has_cookies: true } } 
            // 还是 { success: true, has_cookies: true } 
            // 都能被正确识别为 true
            const active = isActiveResponse(res);
            if (!active) return;

            // 1. 卡片主容器
            const statusCard = document.createElement('div');
            statusCard.className = 'status-card';
            statusCard.dataset.platform = key;

            // 2. 左侧：平台名称
            const statusInfo = document.createElement('div');
            statusInfo.className = 'status-info';

            const statusName = document.createElement('div');
            statusName.className = 'status-name';
            statusName.textContent = cfg.name;

            statusInfo.appendChild(statusName);

            // 3. 右侧：操作区（状态徽章 + 删除按钮）
            const actionsWrapper = document.createElement('div');
            actionsWrapper.className = 'status-actions';

            // 获取翻译文本并过滤掉旧字典里的状态符号
            let statusRawText = active ? safeT('cookiesLogin.status.active', '生效中') : safeT('cookiesLogin.status.inactive', '未配置');
            
            const statusTag = document.createElement('div');
            statusTag.className = `status-tag ${active ? 'active' : 'inactive'}`;
            statusTag.textContent = statusRawText.replace(/^[○●]\s*/u, '');
            actionsWrapper.appendChild(statusTag);

            // 若处于生效状态，添加红色的垃圾桶按钮
            if (active) {
                const delBtn = document.createElement('button');
                delBtn.className = 'del-btn';
                delBtn.title = safeT('cookiesLogin.removeCredentials', '清除凭证');
                delBtn.setAttribute('aria-label', safeT('cookiesLogin.removeCredentials', '清除凭证'));
                delBtn.innerHTML = `<svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>`;
                delBtn.addEventListener('click', () => deleteCookie(key));
                actionsWrapper.appendChild(delBtn);
            }

            statusCard.appendChild(statusInfo);
            statusCard.appendChild(actionsWrapper);
            container.appendChild(statusCard);
        });
        if (hasLoadFailure) {
            const errorText = document.createElement('div');
            errorText.className = 'error-text';
            errorText.style.textAlign = 'center';
            errorText.style.color = '#ef4444';
            errorText.style.gridColumn = '1 / -1';
            errorText.textContent = safeT('cookiesLogin.statusLoadFailed', '状态加载失败');
            container.appendChild(errorText);
        }
    } catch (e) {
        if (refreshGeneration !== statusRefreshGeneration) return;
        container.textContent = ''; 
        const errorText = document.createElement('div');
        errorText.className = 'error-text';
        errorText.style.textAlign = 'center';
        errorText.style.color = '#ef4444';
        errorText.style.gridColumn = '1 / -1';
        errorText.textContent = safeT('cookiesLogin.statusLoadFailed', '状态加载失败');
        container.appendChild(errorText);
    }
}

// 删除指定平台的 Cookies 配置
function showCredentialDeleteConfirm(message) {
    const dialog = document.getElementById('credential-confirm-dialog');
    const title = dialog?.querySelector('[data-confirm-title]');
    const messageElement = document.getElementById('credential-confirm-message');
    const cancelButton = dialog?.querySelector('[data-confirm-cancel]');
    const acceptButton = dialog?.querySelector('[data-confirm-accept]');
    if (!dialog || !title || !messageElement || !cancelButton || !acceptButton) {
        return Promise.resolve(false);
    }
    if (dialog.open) return Promise.resolve(false);

    title.textContent = safeT('cookiesLogin.removeCredentials', '清除凭证');
    messageElement.textContent = message;
    cancelButton.textContent = safeT('common.cancel', '取消');
    acceptButton.textContent = safeT('cookiesLogin.removeCredentials', '清除凭证');

    return new Promise(resolve => {
        let settled = false;
        const finish = confirmed => {
            if (settled) return;
            settled = true;
            dialog.close();
            cancelButton.removeEventListener('click', onCancel);
            acceptButton.removeEventListener('click', onAccept);
            dialog.removeEventListener('cancel', onCancel);
            dialog.removeEventListener('click', onBackdropClick);
            resolve(confirmed);
        };
        const onCancel = event => {
            event?.preventDefault();
            finish(false);
        };
        const onAccept = () => finish(true);
        const onBackdropClick = event => {
            if (event.target === dialog) finish(false);
        };

        cancelButton.addEventListener('click', onCancel);
        acceptButton.addEventListener('click', onAccept);
        dialog.addEventListener('cancel', onCancel);
        dialog.addEventListener('click', onBackdropClick);
        dialog.showModal();
        cancelButton.focus();
    });
}

async function deleteCookie(platformKey) {
    if (deletingPlatforms.has(platformKey)) return;
    const fallbackPlatformName = safeT('cookiesLogin.thisPlatform', '该平台');
    const platformName = PLATFORM_CONFIG[platformKey]?.name || fallbackPlatformName;
    const message = safeT('cookiesLogin.confirmRemove', '确定要清除 {{platformName}} 的凭证吗？').replace('{{platformName}}', platformName);
    const confirmed = await showCredentialDeleteConfirm(message);
    if (!confirmed) return;
    deletingPlatforms.add(platformKey);
    try {
        const res = await fetch(`/api/auth/cookies/${platformKey}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok && data.success) {
            showAlert(true, safeT('cookiesLogin.credentialsRemoved', '凭证已清除'));
            refreshStatusList();
        } else {
            const message = getLocalizedApiMessage(
                data?.message || data?.detail,
                'cookiesLogin.credentialsRemovedFailed',
                '清除失败'
            );
            showAlert(false, message);
        }
    } catch (e) {
        showAlert(false, safeT('cookiesLogin.removeFailed', '操作异常失败'));
    } finally {
        deletingPlatforms.delete(platformKey);
    }
}

// ==========================================
// 弹窗控制 (带内存泄漏防护)
// ==========================================
// 设置弹窗显示时间
let alertTimeout = null;

/**
 * 安全清理定时器的辅助函数
 * 作用：确保旧的倒计时被彻底销毁，防止逻辑冲突
 */
function clearAlertTimer() {
    if (alertTimeout) {
        clearTimeout(alertTimeout);
        alertTimeout = null;
    }
}

function showAlert(success, message) {
    const alertEl = document.getElementById('main-alert');
    // 防御性编程：如果 DOM 元素不存在（比如页面已切换），直接终止，防止报错
    if (!alertEl) return;

    // 1. 立即清理上一次的定时器
    // 这解决了 "用户连续点击保存，导致提示框闪烁或提前消失" 的问题
    clearAlertTimer();
    
    // 2. 设置样式与内容
    alertEl.style.removeProperty('display');
    alertEl.classList.toggle('success', success);
    alertEl.classList.toggle('error', !success);
    alertEl.textContent = message; 

    // 3. 开启新的定时器
    alertTimeout = setTimeout(() => {
        // 再次检查 DOM 是否存在 (防止 4秒内 页面被销毁导致报错)
        if (alertEl) {
            alertEl.style.display = 'none';
            alertEl.classList.remove('success', 'error');
        }
        alertTimeout = null; // 倒计时结束，重置变量状态
    }, 4000);
}

// 内存泄漏防护：当窗口关闭或页面卸载前，强制清理所有挂起的定时器
window.addEventListener('beforeunload', () => {
    clearAlertTimer();
    cancelQrRequest();
    stopQrPoll();
    stopTwitchDevicePoll();
});
