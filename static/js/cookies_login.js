/**
 * N.E.K.O 凭证录入脚本
 * 功能：
 * 1. 支持多个平台的凭证录入
 * 2. 提供详细的操作说明
 * 3. 支持自定义字段配置
 * 4. 自动检测并刷新状态
 */
const PLATFORM_CONFIG_DATA = {
    'netease': {
        name: '网易云音乐',
        nameKey: 'cookiesLogin.netease',
        icon: '🎶', theme: '#c20c0c',
        instructionKey: 'cookiesLogin.instructions.netease',
        fields: [
            { key: 'MUSIC_U', labelKey: 'cookiesLogin.fields.MUSIC_U.label', descKey: 'cookiesLogin.fields.MUSIC_U.desc', required: true },
            { key: 'NMTID', labelKey: 'cookiesLogin.fields.NMTID.label', descKey: 'cookiesLogin.fields.NMTID.desc', required: false }
        ]
    },
    'bilibili': {
        name: 'Bilibili', 
        nameKey: 'cookiesLogin.bilibili',
        icon: '📺', theme: '#4f46e5',
        instructionKey: 'cookiesLogin.instructions.bilibili',
        fields: [
            { key: 'SESSDATA', labelKey: 'cookiesLogin.fields.SESSDATA.label', descKey: 'cookiesLogin.fields.SESSDATA.desc', required: true },
            { key: 'bili_jct', labelKey: 'cookiesLogin.fields.bili_jct.label', descKey: 'cookiesLogin.fields.bili_jct.desc', required: true },
            { key: 'DedeUserID', labelKey: 'cookiesLogin.fields.DedeUserID.label', descKey: 'cookiesLogin.fields.DedeUserID.desc', required: true },
            { key: 'buvid3', labelKey: 'cookiesLogin.fields.buvid3.label', descKey: 'cookiesLogin.fields.buvid3.desc', required: false }
        ]
    },
    'douyin': {
        name: '抖音', 
        nameKey: 'cookiesLogin.douyin', 
        icon: '🎵', theme: '#000000',
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
        icon: '🧡', theme: '#ff5000',
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
        icon: '🌏', theme: '#f59e0b',
        instructionKey: 'cookiesLogin.instructions.weibo',
        fields: [
            { key: 'SUB', labelKey: 'cookiesLogin.fields.SUB.label', descKey: 'cookiesLogin.fields.SUB.desc', required: true },
            { key: 'XSRF-TOKEN', labelKey: 'cookiesLogin.fields.XSRF-TOKEN.label', descKey: 'cookiesLogin.fields.XSRF-TOKEN.desc', required: false }
        ]
    },
    'twitter': {
        name: 'Twitter/X', 
        nameKey: 'cookiesLogin.twitter', 
        icon: '🐦', theme: '#0ea5e9',
        instructionKey: 'cookiesLogin.instructions.twitter',
        fields: [
            { key: 'auth_token', labelKey: 'cookiesLogin.fields.auth_token.label', descKey: 'cookiesLogin.fields.auth_token.desc', required: true },
            { key: 'ct0', labelKey: 'cookiesLogin.fields.ct0.label', descKey: 'cookiesLogin.fields.ct0.desc', required: true }
        ]
    },
    'reddit': {
        name: 'Reddit', 
        nameKey: 'cookiesLogin.reddit', 
        icon: '👽', theme: '#ff4500',
        instructionKey: 'cookiesLogin.instructions.reddit',
        fields: [
            { key: 'reddit_session', labelKey: 'cookiesLogin.fields.reddit_session.label', descKey: 'cookiesLogin.fields.reddit_session.desc', required: true },
            { key: 'csrftoken', labelKey: 'cookiesLogin.fields.csrftoken.label', descKey: 'cookiesLogin.fields.csrftoken.desc', required: false }
        ]
    }
};

// 如果字典还没加载好，坚决返回传入的中文后备(Fallback)
const safeT = (key, fallback = '') => {
    if (typeof window.t !== 'function') return fallback;
    const result = window.t(key);
    // 如果返回的翻译和键名一样，或者为空，说明字典处于未就绪状态
    return (result === key || !result) ? fallback : result;
};

let PLATFORM_CONFIG = {};
let currentPlatform = 'netease';

// 当语言切换时，重新初始化平台配置
function initPlatformConfig() {
    PLATFORM_CONFIG = {};
    for (const [key, data] of Object.entries(PLATFORM_CONFIG_DATA)) {
        
        // 优先尝试翻译平台名称，如果翻译失败则回退到默认中文名
        const translatedName = data.nameKey ? safeT(data.nameKey, data.name) : data.name;

        // 如果是微博，教程里的目标网址显示为 m.weibo.cn
        // 如果是其他平台，教程里的目标名称使用翻译后的名字 (例如 "TikTok")
        const targetDisplay = key === 'weibo' ? 'm.weibo.cn' : translatedName;

        PLATFORM_CONFIG[key] = {
            name: translatedName, // 界面上显示的名称 (Tabs, 列表) 现在支持多语言了！
            icon: data.icon,
            theme: data.theme,
            
            // 附带默认中文提示，自动填入正确的域名或名称
            // 如果字典里有 instructionKey，直接用字典的（字典通常自带了网址）
            // 如果字典没有，则使用这里的模板，并填入 m.weibo.cn 或 翻译后的平台名
            instruction: data.instructionKey ? safeT(data.instructionKey, `📌 <b>目标：</b> 请前往 <b>${targetDisplay}</b> 获取这些 Cookies。`) : '',
            
            fields: data.fields.map(field => ({
                key: field.key,
                mapKey: field.mapKey,
                label: field.labelKey ? safeT(field.labelKey, field.key) : field.key,
                desc: field.descKey ? safeT(field.descKey) : '',
                required: field.required
            }))
        };
    }
}

// 安全渲染带标签的教程步骤，并提供完善的中文回退
function renderStaticHtmlI18n() {
    const htmlSteps = {
        'guide-step1': { key: 'cookiesLogin.guide.step1', fallback: '在浏览器打开对应平台网页并<span class="highlight-text">完成登录</span>。' },
        'guide-step3': { key: 'cookiesLogin.guide.step3', fallback: '在顶部找到并点击 <span class="highlight-text">Application (应用程序)</span>。' },
        'guide-step4': { key: 'cookiesLogin.guide.step4', fallback: '左侧找到 <span class="highlight-text">Cookies</span>，点击域名后在右侧复制对应的值。' }
    };
    // 遍历所有需要翻译的元素 ID
    for (const [id, data] of Object.entries(htmlSteps)) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = DOMPurify.sanitize(safeT(data.key, data.fallback));
    }
    // 更新步骤2的前缀和后缀文本
    const step2Prefix = document.getElementById('guide-step2-prefix');
    const step2Suffix = document.getElementById('guide-step2-suffix');
    if (step2Prefix) step2Prefix.textContent = safeT('cookiesLogin.guide.step2_prefix', '按下键盘');
    if (step2Suffix) step2Suffix.textContent = safeT('cookiesLogin.guide.step2_suffix', '打开开发者工具。');
    // 更新关闭按钮的标题和图片 alt 文本
    const closeBtn = document.querySelector('.close-btn');
    if (closeBtn) {
        const closeText = safeT('common.close', '关闭');
        closeBtn.title = closeText;
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
    switchTab(currentPlatform, document.querySelector('.tab-btn.active'), true);
    refreshStatusList();
}
// DOM 加载完成后，初始化平台配置、渲染静态 HTML 翻译并监听语言变化事件
document.addEventListener('DOMContentLoaded', () => {
    // 初次加载无论如何都渲染一次（带兜底中文），然后监听语言就绪事件
    initPlatformConfig();
    renderStaticHtmlI18n();
    window.addEventListener('localechange', handleLocaleChange);
    
    const firstTab = document.querySelector('.tab-btn');
    if (firstTab) switchTab('netease', firstTab);
    refreshStatusList();
});

/**
 * 降低十六进制颜色的明度
 * @param {string} hexColor - 输入的十六进制颜色，如 #fff 或 #ffffff
 * @param {number} lightnessPercent - 降低明度的百分比（0-100），100 表示完全变黑
 * @returns {string} 调整后的十六进制颜色
 */
function decreaseColorLightness(hexColor, lightnessPercent) {
    // 验证输入的明度值
    const percent = Math.max(0, Math.min(100, Number(lightnessPercent)));
    const decreaseRatio = 1 - percent / 100;

    // 清洗并验证十六进制颜色
    let hex = hexColor.replace(/^#/, '');
    // 处理简写形式 (#fff -> #ffffff)
    if (hex.length === 3) {
        hex = hex.split('').map(char => char + char).join('');
    }

    // 验证十六进制格式
    if (!/^[0-9A-Fa-f]{6}$/.test(hex)) {
        throw new Error('请输入有效的十六进制颜色，如 #fff 或 #ffffff');
    }

    // 十六进制转 RGB
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);

    // 降低明度（按比例减少每个通道的值）
    const newR = Math.max(0, Math.round(r * decreaseRatio));
    const newG = Math.max(0, Math.round(g * decreaseRatio));
    const newB = Math.max(0, Math.round(b * decreaseRatio));

    // RGB 转回十六进制（确保两位，不足补0）
    const toHex = (num) => num.toString(16).padStart(2, '0');
    const newHex = `#${toHex(newR)}${toHex(newG)}${toHex(newB)}`;

    return newHex.toUpperCase(); // 统一返回大写格式，也可以改为 toLowerCase()
}


async function showQRLogin(config, platformKey) {
    let qrSupportedPlatforms = [];
    const qrLoginBox = document.getElementById('QRLogin');
    if (!qrLoginBox) return;

    // 清理之前的状态
    qrLoginBox.innerHTML = "";
    qrLoginBox.classList.remove('collapsed');
    qrLoginBox.style.display = 'block';
    const resp = await fetch('/api/auth/get_CanQRLoginList');
    if (currentPlatform !== platformKey) return;
    qrSupportedPlatforms = await resp.json();
    if (currentPlatform !== platformKey) return;

    // 采用多重匹配：优先转换后台返回的列表为全小写比对 platformKey，同时兼容已有的原始比对以防止破坏遗留代码
    const isSupported = qrSupportedPlatforms.map(k => k.toLowerCase()).includes(platformKey.toLowerCase()) || qrSupportedPlatforms.includes(config["name"]);

    if (isSupported){
        const QRinfo =  document.createElement("div");
        const butt = document.createElement("button");
        QRinfo.innerHTML = safeT('cookiesLogin.qrLogin.tryQR', '或者...试试扫码登陆?');
        QRinfo.style = 'margin-bottom: 10px;color: #64748b;font-size: 14px';
        butt.innerHTML = safeT('cookiesLogin.qrLogin.openQR', '📱 打开扫码登陆');
        butt.style.cssText = `width: 100%; padding: 12px; margin-top: 10px; font-size: 14px; font-weight: 600; border-radius: 10px; border: 2px dashed #4f46e5; background: ${config["theme"]} ; color: #f8fafc; cursor: pointer; transition: all 0.2s;`;
        butt.onmouseover = function() { butt.style.background = decreaseColorLightness(config["theme"],20); };
        butt.onmouseout = function() { butt.style.background = config["theme"]; };
        butt.onclick = function(){requestQR(config, platformKey)};
        qrLoginBox.appendChild(QRinfo);
        qrLoginBox.appendChild(butt);
    }else{
        // let a = 1;希望这里可以空着不会报错 报错了就肘喵老师
        // 当前只做了"Bilibili"扫码登录,其他平台再说吧
    }
}

let qrPollTimeout = null;
let qrPollInFlight = false;
let qrRefreshTimeout = null;
let currentQrKey = null;

async function requestQR(config, platformKey) {
    if (qrRefreshTimeout) {
        clearTimeout(qrRefreshTimeout);
        qrRefreshTimeout = null;
    }
    const qrLoginBox = document.getElementById('QRLogin');
    qrLoginBox.innerHTML = `
        <div style="text-align: center; padding: 20px;">
            <div style="color: #64748b; margin-bottom: 10px;">${safeT('cookiesLogin.qrLogin.loading', '正在获取二维码...')}</div>
        </div>
    `;
    
    try {
        const response = await fetch('/api/auth/get_QR', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platform: platformKey })
        });
        

        
        if (currentPlatform !== platformKey) return;
        const result = await response.json();
        if (currentPlatform !== platformKey) return;
        if (!response.ok) {
            throw new Error(
                typeof result?.detail === 'string' && result.detail
                    ? result.detail
                    : safeT('cookiesLogin.qrLogin.fetchFailed', '获取二维码失败，请稍后重试')
            );
        }
        if (result.success && result.data) {
            currentQrKey = result.data.qrcode_key;
            const timeout = result.data.timeout || 180;
            
            qrLoginBox.innerHTML = `
                <div style="text-align: center; padding: 15px; background: #f8fafc; border-radius: 12px; border: 1px solid #e2e8f0; position: relative;">
                    <button id="qr-collapse-action" class="qr-collapse-btn">
                        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        ${safeT('common.collapse', '收起')}
                    </button>
                    <div style="font-weight: 600; color: #334155; margin-bottom: 12px; margin-top: 5px;">${safeT('cookiesLogin.qrLogin.scanTitle', '📱 扫码登录 {{platform}}').replace('{{platform}}', PLATFORM_CONFIG[platformKey]?.name || config["name"])}</div>
                    <img src="${result.data.qrcode_image}" alt="QR Code" style="width: 200px; height: 200px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <div id="qr-status" style="margin-top: 12px; font-size: 13px; color: #64748b;">${safeT('cookiesLogin.qrLogin.waiting', '等待扫码...')}</div>
                    <div style="margin-top: 10px; font-size: 12px; color: #94a3b8;">${safeT('cookiesLogin.qrLogin.validFor', '二维码有效期: {{seconds}}秒').replace('{{seconds}}', timeout)}</div>
                    <button id="qr-refresh-btn" style="margin-top: 12px; padding: 8px 16px; font-size: 13px; border-radius: 8px; border: 1px solid #e2e8f0; background: white; color: #475569; cursor: pointer;">${safeT('cookiesLogin.qrLogin.refreshQR', '刷新二维码')}</button>
                </div>
            `;
            
            document.getElementById('qr-collapse-action').onclick = function() {
                stopQrPoll();
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
                <div style="text-align: center; padding: 20px; color: #ef4444;">
                    ${safeT('cookiesLogin.qrLogin.fetchFailed', '获取二维码失败，请稍后重试')}
                    <button id="qr-retry-btn" style="display: block; margin: 10px auto 0; padding: 8px 16px; border-radius: 8px; border: 1px solid #ef4444; background: white; color: #ef4444; cursor: pointer;">${safeT('cookiesLogin.qrLogin.retry', '重试')}</button>
                </div>
            `;
            document.getElementById('qr-retry-btn').onclick = function() {
                requestQR(config, platformKey);
            };
        }
    } catch (err) {
        console.error("Request QR error:", err);
        if (currentPlatform !== platformKey) return;
        const errorMessage =
            typeof err?.message === 'string' && err.message
                ? err.message
                : safeT('cookiesLogin.qrLogin.networkError', '网络请求失败，请检查连接');
        qrLoginBox.innerHTML = `
            <div style="text-align: center; padding: 20px; color: #ef4444;">
                ${errorMessage}
                <button id="qr-retry-btn-err" style="display: block; margin: 10px auto 0; padding: 8px 16px; border-radius: 8px; border: 1px solid #ef4444; background: white; color: #ef4444; cursor: pointer;">${safeT('cookiesLogin.qrLogin.retry', '重试')}</button>
            </div>
        `;
        document.getElementById('qr-retry-btn-err').onclick = function() {
            requestQR(config, platformKey);
        };
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
                throw new Error(
                    typeof result?.detail === 'string'
                        ? result.detail
                        : safeT('cookiesLogin.qrLogin.networkError', '网络请求失败，请检查连接')
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
                    safeT('cookiesLogin.qrLogin.status.success', '✅ {{message}}').replace('{{message}}', data.message),
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
                let customAlert = safeT('cookiesLogin.qrLogin.successAlert', '扫码登录成功！Cookie 已自动填入，请点击保存配置');
                if (capturedCount === 0 && cookieFields.length > 0) {
                  customAlert = safeT('cookiesLogin.qrLogin.extractFailed', '⚠️ 扫码成功但未能自动提取到字段，请手动检查。');
                }

                showAlert(capturedCount > 0, customAlert);

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
                            safeT('cookiesLogin.qrLogin.status.expired', '❌ {{message}}，请刷新').replace('{{message}}', message)
                        );
                        stopQrPoll();
                    } else if (status === 'scanned') {
                        setStatusSpan(
                            '#f59e0b',
                            safeT('cookiesLogin.qrLogin.status.scanned', '📱 {{message}}').replace('{{message}}', message)
                        );
                    } else if (status === 'waiting') {
                        statusEl.textContent = safeT('cookiesLogin.qrLogin.status.waiting', '{{message}}...').replace('{{message}}', message);
                    } else {
                        statusEl.textContent = message;
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
                    statusEl.textContent = typeof err?.message === 'string' && err.message ? err.message : safeT('cookiesLogin.qrLogin.networkError', '网络请求失败，请检查连接');
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

    stopQrPoll();
    currentQrKey = null;
    if (qrRefreshTimeout) {
        clearTimeout(qrRefreshTimeout);
        qrRefreshTimeout = null;
    }
    currentPlatform = platformKey;
    const config = PLATFORM_CONFIG[platformKey];
    // 更新选项卡文本
    if (btnElement) {
        document.querySelectorAll('.tab-btn').forEach(btn =>{
             btn.classList.remove('active');
        });
        btnElement.classList.add('active');
    }
    // 更新面板描述
    const descBox = document.getElementById('panel-desc');
    if (descBox) {
        if (config.instruction && config.instruction.trim() !== '') {
            descBox.style.display = 'block'; 
            descBox.style.borderColor = config.theme;
            descBox.innerHTML = DOMPurify.sanitize(config.instruction);
        } else {
            descBox.style.display = 'none'; 
        }
    }
    showQRLogin(PLATFORM_CONFIG_DATA[platformKey], platformKey)
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
        
        if (isReRender) {
            Object.entries(existingValues).forEach(([id, preservedValue]) => {
                const input = document.getElementById(id);
                if (input) input.value = preservedValue;
           });
        }
    }

    // 更新提交按钮文本
    const submitText = document.getElementById('submit-text');
    if (submitText) {
        const translatedText = safeT('cookiesLogin.saveConfig', '保存配置');
        submitText.textContent = `${config.name} ${translatedText}`;
    }
}

// 提交当前平台的 Cookies 配置
async function submitCurrentCookie() {
    const config = PLATFORM_CONFIG[currentPlatform];
    const cookiePairs = [];
    // 遍历配置字段，收集 Cookies 配置
    for (const f of config.fields) {
        const fieldId = `input-${f.mapKey || f.key}`;
        const inputEl = document.getElementById(fieldId);
        const rawVal = inputEl ? inputEl.value : '';
        const val = rawVal;
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
    const submitBtn = document.getElementById('submit-btn');
    const submitText = document.getElementById('submit-text');
    const encryptToggle = document.getElementById('encrypt-toggle');
    const originalBtnText = submitText?.textContent;
    // 禁用提交按钮，防止重复点击
    if (submitBtn) submitBtn.disabled = true;
    if (submitText) submitText.textContent = safeT('cookiesLogin.submitting', '安全加密传输中...');
    // 发送 POST 请求保存 Cookies
    try {
        const response = await fetch('/api/auth/cookies/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                platform: currentPlatform,
                cookie_string: cookiePairs.join('; '),
                encrypt: encryptToggle ? encryptToggle.checked : false
            })
        });
        // 检查响应状态
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const result = await response.json();
        // 检查是否成功保存
        if (result.success) {
            const message = safeT('cookiesLogin.credentialsSaved', '{{platformName}} 凭证已保存').replace('{{platformName}}', config.name);
            showAlert(true, message);
            document.querySelectorAll('.credential-input').forEach(i => i.value = '');
            refreshStatusList();
        } else {
            let errMsg = result.message;
            if(!errMsg && result.detail) {
                errMsg = Array.isArray(result.detail)
                    ? result.detail.map(e => e.msg || JSON.stringify(e)).join('; ')
                    : String(result.detail);
            }
            const message = errMsg || safeT('cookiesLogin.saveFailed', '保存失败');
            showAlert(false, message);
        }
    } catch (err) {
        const message = safeT('cookiesLogin.networkError', '网络请求失败，请检查连接');
        showAlert(false, message);
        console.error("Submit error:", err);
    } finally {
        if (submitBtn) submitBtn.disabled = false;
        if (submitText) submitText.textContent = originalBtnText;
    }
}

// 刷新当前平台的状态列表
// 重新设计的状态监控列表渲染引擎 (修复缓存与状态判定问题)
async function refreshStatusList() {
    const container = document.getElementById('platform-list-content');
    if (!container) return;
    const platforms = Object.keys(PLATFORM_CONFIG);
    try {
        const results = await Promise.all(
            // 强制禁用 GET 缓存，保证每次拉取的都是最新状态！
            platforms.map(p => fetch(`/api/auth/cookies/${p}`, { cache: 'no-store' })
                .then(r => r.json())
                .catch(() => ({ success: false })))
        );
        container.textContent = '';
        results.forEach((res, idx) => {
            const key = platforms[idx];
            const cfg = PLATFORM_CONFIG[key];
            
            // 兼容多种后端返回的数据结构
            // 无论后端是 { success: true, data: { has_cookies: true } } 
            // 还是 { success: true, has_cookies: true } 
            // 都能被正确识别为 true
            const active = res.success === true && (
                res.has_cookies === true || 
                res.data?.has_cookies === true || 
                res.data === true
            );

            // 1. 卡片主容器
            const statusCard = document.createElement('div');
            statusCard.className = 'status-card';

            // 2. 左侧：图标与名称
            const statusInfo = document.createElement('div');
            statusInfo.className = 'status-info';

            const iconWrapper = document.createElement('div');
            iconWrapper.className = 'status-icon-wrapper';
            iconWrapper.textContent = cfg.icon;

            const statusName = document.createElement('div');
            statusName.className = 'status-name';
            statusName.textContent = cfg.name;

            statusInfo.appendChild(iconWrapper);
            statusInfo.appendChild(statusName);

            // 3. 右侧：操作区（状态徽章 + 删除按钮）
            const actionsWrapper = document.createElement('div');
            actionsWrapper.className = 'status-actions';

            // 获取翻译文本并过滤掉旧字典里的特殊符号（如 ○, ●）
            let statusRawText = active ? safeT('cookiesLogin.status.active', '生效中') : safeT('cookiesLogin.status.inactive', '未配置');
            
            const statusTag = document.createElement('div');
            statusTag.className = `status-tag ${active ? 'active' : 'inactive'}`;
            statusTag.textContent = statusRawText.replace(/^[○●⚪🟢🔴]\s*/u, '');
            actionsWrapper.appendChild(statusTag);

            // 若处于生效状态，添加红色的垃圾桶按钮
            if (active) {
                const delBtn = document.createElement('button');
                delBtn.className = 'del-btn';
                delBtn.title = safeT('cookiesLogin.removeCredentials', '清除凭证');
                delBtn.innerHTML = `<svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>`;
                delBtn.addEventListener('click', () => deleteCookie(key));
                actionsWrapper.appendChild(delBtn);
            }

            statusCard.appendChild(statusInfo);
            statusCard.appendChild(actionsWrapper);
            container.appendChild(statusCard);
        });
    } catch (e) {
        container.textContent = ''; 
        const errorText = document.createElement('div');
        errorText.className = 'error-text';
        errorText.style.textAlign = 'center';
        errorText.style.color = '#ef4444';
        errorText.textContent = safeT('cookiesLogin.statusLoadFailed', '状态加载失败');
        container.appendChild(errorText);
    }
}

// 删除指定平台的 Cookies 配置
async function deleteCookie(platformKey) {
    const fallbackPlatformName = safeT('cookiesLogin.thisPlatform', '该平台');
    const platformName = PLATFORM_CONFIG[platformKey]?.name || fallbackPlatformName;
    const message = safeT('cookiesLogin.confirmRemove', '确定要清除 {{platformName}} 的凭证吗？').replace('{{platformName}}', platformName);
    if (!confirm(message)) return;
    try {
        const res = await fetch(`/api/auth/cookies/${platformKey}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showAlert(true, safeT('cookiesLogin.credentialsRemoved', '凭证已清除'));
            refreshStatusList();
        } else {
            showAlert(false, data.message || safeT('cookiesLogin.credentialsRemovedFailed', '清除失败'));
        }
    } catch (e) {
        showAlert(false, safeT('cookiesLogin.removeFailed', '操作异常失败'));
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
    alertEl.style.display = 'block';
    alertEl.style.backgroundColor = success ? '#ecfdf5' : '#fef2f2';
    alertEl.style.color = success ? '#059669' : '#dc2626';
    alertEl.style.borderColor = success ? '#a7f3d0' : '#fecaca';
    alertEl.textContent = message; 

    // 3. 开启新的定时器
    alertTimeout = setTimeout(() => {
        // 再次检查 DOM 是否存在 (防止 4秒内 页面被销毁导致报错)
        if (alertEl) {
            alertEl.style.display = 'none';
        }
        alertTimeout = null; // 倒计时结束，重置变量状态
    }, 4000);
}

// 内存泄漏防护：当窗口关闭或页面卸载前，强制清理所有挂起的定时器
window.addEventListener('beforeunload', () => {
    clearAlertTimer();
});