/**
 * N.E.K.O 凭证录入脚本 - 企业级加固版
 * 修复：DOM 节点依赖、HTML 标签转义、ID 规范化、异步竞争过滤
 */

// 使用t函数前的配置数据
const PLATFORM_CONFIG_DATA = {
    'bilibili': {
        name: 'Bilibili', icon: '📺', theme: '#4f46e5',
        instructionKey: 'cookiesLogin.instructions.bilibili',
        fields: [
            { key: 'SESSDATA', labelKey: 'cookiesLogin.fields.SESSDATA.label', descKey: 'cookiesLogin.fields.SESSDATA.desc', required: true },
            { key: 'bili_jct', labelKey: 'cookiesLogin.fields.bili_jct.label', descKey: 'cookiesLogin.fields.bili_jct.desc', required: true },
            { key: 'DedeUserID', labelKey: 'cookiesLogin.fields.DedeUserID.label', descKey: 'cookiesLogin.fields.DedeUserID.desc', required: true },
            { key: 'buvid3', labelKey: 'cookiesLogin.fields.buvid3.label', descKey: 'cookiesLogin.fields.buvid3.desc', required: false }
        ]
    },
    'douyin': {
        name: '抖音', icon: '🎵', theme: '#000000',
        instructionKey: 'cookiesLogin.instructions.douyin',
        fields: [
            { key: 'sessionid', labelKey: 'cookiesLogin.fields.sessionid.label', descKey: 'cookiesLogin.fields.sessionid.desc', required: true },
            { key: 'ttwid', labelKey: 'cookiesLogin.fields.ttwid.label', descKey: 'cookiesLogin.fields.ttwid.desc', required: true },
            { key: 'passport_csrf_token', labelKey: 'cookiesLogin.fields.passport_csrf_token.label', descKey: 'cookiesLogin.fields.passport_csrf_token.desc', required: false },
            { key: 'odin_tt', labelKey: 'cookiesLogin.fields.odin_tt.label', descKey: 'cookiesLogin.fields.odin_tt.desc', required: false }
        ]
    },
    'kuaishou': {
        name: '快手', icon: '🧡', theme: '#ff5000',
        instructionKey: 'cookiesLogin.instructions.kuaishou',
        fields: [
            // 修复点：后端 key 包含点号，通过 mapKey 处理 DOM ID
            { key: 'kuaishou.server.web_st', mapKey: 'ks_web_st', labelKey: 'cookiesLogin.fields.ks_web_st.label', descKey: 'cookiesLogin.fields.ks_web_st.desc', required: true },
            { key: 'kuaishou.server.web_ph', mapKey: 'ks_web_ph', labelKey: 'cookiesLogin.fields.ks_web_ph.label', descKey: 'cookiesLogin.fields.ks_web_ph.desc', required: true },
            { key: 'userId', labelKey: 'cookiesLogin.fields.userId.label', descKey: 'cookiesLogin.fields.userId.desc', required: true },
            { key: 'did', labelKey: 'cookiesLogin.fields.did.label', descKey: 'cookiesLogin.fields.did.desc', required: true }
        ]
    },
    'weibo': {
        name: '微博', icon: '🌏', theme: '#f59e0b',
        instructionKey: 'cookiesLogin.instructions.weibo',
        fields: [
            { key: 'SUB', labelKey: 'cookiesLogin.fields.SUB.label', descKey: 'cookiesLogin.fields.SUB.desc', required: true },
            { key: 'XSRF-TOKEN', labelKey: 'cookiesLogin.fields.XSRF-TOKEN.label', descKey: 'cookiesLogin.fields.XSRF-TOKEN.desc', required: false }
        ]
    },
    'twitter': {
        name: 'Twitter/X', icon: '🐦', theme: '#0ea5e9',
        instructionKey: 'cookiesLogin.instructions.twitter',
        fields: [
            { key: 'auth_token', labelKey: 'cookiesLogin.fields.auth_token.label', descKey: 'cookiesLogin.fields.auth_token.desc', required: true },
            { key: 'ct0', labelKey: 'cookiesLogin.fields.ct0.label', descKey: 'cookiesLogin.fields.ct0.desc', required: true }
        ]
    },
    'reddit': {
        name: 'Reddit', icon: '👽', theme: '#ff4500',
        instructionKey: 'cookiesLogin.instructions.reddit',
        fields: [
            { key: 'reddit_session', labelKey: 'cookiesLogin.fields.reddit_session.label', descKey: 'cookiesLogin.fields.reddit_session.desc', required: true },
            { key: 'csrftoken', labelKey: 'cookiesLogin.fields.csrftoken.label', descKey: 'cookiesLogin.fields.csrftoken.desc', required: false }
        ]
    }
};

// 动态生成配置对象，支持国际化
let PLATFORM_CONFIG = {};

function initPlatformConfig() {
    PLATFORM_CONFIG = {};
    for (const [key, data] of Object.entries(PLATFORM_CONFIG_DATA)) {
        PLATFORM_CONFIG[key] = {
            name: data.name,
            icon: data.icon,
            theme: data.theme,
            instruction: data.instructionKey ? t(data.instructionKey) : '',
            fields: data.fields.map(field => ({
                key: field.key,
                mapKey: field.mapKey,
                label: field.labelKey ? t(field.labelKey) : field.label,
                desc: field.descKey ? t(field.descKey) : field.desc,
                required: field.required
            }))
        };
    }
}

// 确保在i18n初始化完成后更新配置
if (typeof window.t === 'function' && i18next.isInitialized) {
    initPlatformConfig();
} else {
    // 如果i18n还未初始化，等待localechange事件
    window.addEventListener('localechange', initPlatformConfig);
    // 或者等待DOM加载完成后尝试
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof window.t === 'function') {
            initPlatformConfig();
        }
    });
}

let currentPlatform = 'bilibili';
let alertTimeout = null;

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    const firstTab = document.querySelector('.tab-btn');
    if (firstTab) switchTab('bilibili', firstTab);
    refreshStatusList();
});

/**
 * 切换平台标签
 */
function switchTab(platformKey, btnElement) {
    if (!PLATFORM_CONFIG[platformKey]) return;
    currentPlatform = platformKey;
    const config = PLATFORM_CONFIG[platformKey];

    // UI 状态切换
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    btnElement?.classList.add('active');

    // 渲染说明
    const descBox = document.getElementById('panel-desc');
    if (descBox) {
        descBox.style.borderColor = config.theme;
        descBox.innerHTML = DOMPurify.sanitize(config.instruction);
    }

    // 渲染动态字段
    const fieldsContainer = document.getElementById('dynamic-fields');
    if (fieldsContainer) {
        fieldsContainer.innerHTML = config.fields.map(f => `
            <div class="field-group">
                <label for="input-${f.mapKey || f.key}">
                    <span>${DOMPurify.sanitize(f.label)} ${f.required ? '<span class="req-star">*</span>' : ''}</span>
                    <span class="desc">${DOMPurify.sanitize(f.desc)}</span>
                </label>
                <input type="text" id="input-${f.mapKey || f.key}" 
                         placeholder="在此粘贴 ${DOMPurify.sanitize(f.key)}..." 
                       autocomplete="off" 
                       class="credential-input">
            </div>
        `).join('');
    }

    const submitText = document.getElementById('submit-text');
    if (submitText) {
        const translatedText = t('cookiesLogin.saveConfig');
        submitText.textContent = `${config.name} ${translatedText}`;
    }}

/**
 * 提交当前表单
 */
async function submitCurrentCookie() {
    const config = PLATFORM_CONFIG[currentPlatform];
    const cookiePairs = [];
    
    // 1. 数据收集与校验
    for (const f of config.fields) {
        const fieldId = `input-${f.mapKey || f.key}`;
        const inputEl = document.getElementById(fieldId);
        const val = inputEl ? inputEl.value.trim() : '';

        if (f.required && !val) {
            const fieldName = f.label;
            const message = t('cookiesLogin.requiredField', { fieldName: fieldName });
            showAlert(false, message);
            inputEl?.focus();
            return;
        }

        if (val) {
            // 简单的防注入处理：分步骤检查并清理
            let sanitizedVal = val;
            
            if (/[\r\n\t<>'";]/.test(sanitizedVal)) {
                sanitizedVal = sanitizedVal
                    .replace(/[\r\n\t]/g, '')       // 清理控制字符
                    .replace(/[<>'"]/g, '')         // 清理潜在 XSS 字符
                    .replace(/;/g, '');             // 清理所有分号
                    
                const fieldName = f.label;
                const message = t('cookiesLogin.invalidChars', { fieldName: fieldName });
                showAlert(false, message);
            }
            
            const prevVal = sanitizedVal;
            sanitizedVal = sanitizedVal.trim();
            if (sanitizedVal !== prevVal) {
                const fieldName = f.label;
            const message = t('cookiesLogin.whitespaceTrimmed', { fieldName: fieldName });
            showAlert(false, message);
            }
            
            cookiePairs.push(`${f.key}=${sanitizedVal}`);
        }
    }

    // 2. 状态更新
    const submitBtn = document.getElementById('submit-btn');
    const submitText = document.getElementById('submit-text');
    const encryptToggle = document.getElementById('encrypt-toggle');
    const originalBtnText = submitText?.textContent;

    if (submitBtn) submitBtn.disabled = true;
    if (submitText) submitText.textContent = '安全加密传输中...';

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

        const result = await response.json();

        if (result.success) {
            const message = t('cookiesLogin.credentialsSaved', { platformName: config.name });
            showAlert(true, `✅ ${message}`);
            // 重置当前输入框
            document.querySelectorAll('.credential-input').forEach(i => i.value = '');
            refreshStatusList();
        } else {
            let errMsg = result.message;
            if(!errMsg && result.detail) {
                errMsg = Array.isArray(result.detail)
                    ? result.detail.map(e => e.msg || JSON.stringify(e)).join('; ')
                    : String(result.detail);
            }
            const message = errMsg || t('cookiesLogin.saveFailed');
            showAlert(false, message);
        }
    } catch (err) {
        const message = t('cookiesLogin.networkError');
        showAlert(false, message);
        console.error("Submit error:", err);
    } finally {
        if (submitBtn) submitBtn.disabled = false;
        if (submitText) submitText.textContent = originalBtnText;
    }
}

// 状态监控
async function refreshStatusList() {
    const container = document.getElementById('platform-list-content');
    if (!container) return;

    const platforms = Object.keys(PLATFORM_CONFIG);
    
    try {
        const results = await Promise.all(
            platforms.map(p => 
                fetch(`/api/auth/cookies/${p}`)
                    .then(r => r.json())
                    .catch(() => ({ success: false }))
            )
        );
        
        container.textContent = '';

        results.forEach((res, idx) => {
            const key = platforms[idx];
            const cfg = PLATFORM_CONFIG[key];
            const active = res.success && res.data?.has_cookies;

            // 1. 创建卡片外层容器
            const statusCard = document.createElement('div');
            statusCard.className = 'status-card';
            // 设置左侧边框样式（安全设置内联样式，避免字符串拼接）
            statusCard.style.borderLeft = `4px solid ${active ? '#10b981' : '#cbd5e1'}`;

            // 2. 创建状态信息容器
            const statusInfo = document.createElement('div');
            statusInfo.className = 'status-info';

            // 3. 创建状态名称元素
            const statusName = document.createElement('div');
            statusName.className = 'status-name';
            // 使用textContent设置文本（核心：避免XSS，仅渲染纯文本）
            statusName.textContent = `${cfg.icon} ${cfg.name}`;

            // 4. 创建状态标签元素
            const statusTag = document.createElement('div');
            statusTag.className = 'status-tag';
            statusTag.style.color = active ? '#10b981' : '#94a3b8';
            const statusText = active ? t('cookiesLogin.status.active') : t('cookiesLogin.status.inactive');
            statusTag.textContent = statusText;

            // 5. 组装状态信息容器
            statusInfo.appendChild(statusName);
            statusInfo.appendChild(statusTag);

            // 6. 创建删除按钮（仅在active为true时创建）
            if (active) {
                const delBtn = document.createElement('button');
                delBtn.className = 'del-btn';
                delBtn.textContent = t('cookiesLogin.removeCredentials');
                // 使用addEventListener绑定事件（替代onclick属性，避免XSS）
                delBtn.addEventListener('click', () => {
                    deleteCookie(key);
                });
                statusCard.appendChild(delBtn);
            }

            // 7. 组装完整卡片并添加到容器
            statusCard.appendChild(statusInfo);
            container.appendChild(statusCard);
        });
    } catch (e) {
        // 错误提示也使用DOM创建，避免innerHTML
        container.textContent = ''; // 先清空
        const errorText = document.createElement('div');
        errorText.className = 'error-text';
        errorText.textContent = '状态加载失败';
        container.appendChild(errorText);
    }
}

/**
 * 删除凭证
 */
async function deleteCookie(platformKey) {
    const platformName = PLATFORM_CONFIG[platformKey]?.name || '该平台';
    const message = t('cookiesLogin.confirmRemove', { platformName: platformName });
    if (!confirm(message)) return;

    try {
        const res = await fetch(`/api/auth/cookies/${platformKey}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            const message = t('cookiesLogin.credentialsRemoved');
            showAlert(true, message);
            refreshStatusList();
        } else {
            const message = data.message || t('cookiesLogin.credentialsRemovedFailed');
            showAlert(false, message);
        }
    } catch (e) {
        const message = t('cookiesLogin.removeFailed');
        showAlert(false, message);
    }
}

/**
 * 统一弹窗提醒
 * 修复：使用 textContent 修改文本以避免XSS风险，并处理计时器竞争
 */
function showAlert(success, message) {
    const alertEl = document.getElementById('main-alert');
    if (!alertEl) return;

    clearTimeout(alertTimeout);
    
    alertEl.style.display = 'block';
    alertEl.style.backgroundColor = success ? '#ecfdf5' : '#fef2f2';
    alertEl.style.color = success ? '#059669' : '#dc2626';
    alertEl.style.borderColor = success ? '#a7f3d0' : '#fecaca';
    alertEl.textContent = message; 

    alertTimeout = setTimeout(() => {
        alertEl.style.display = 'none';
    }, 4000);
}