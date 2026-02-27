/**
 * N.E.K.O 凭证录入脚本
 * 功能：
 * 1. 支持多个平台的凭证录入
 * 2. 提供详细的操作说明
 * 3. 支持自定义字段配置
 * 4. 自动检测并刷新状态
 */
const PLATFORM_CONFIG_DATA = {
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
let currentPlatform = 'bilibili';

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
    if (firstTab) switchTab('bilibili', firstTab);
    refreshStatusList();
});

// 切换选项卡时，更新当前平台配置
function switchTab(platformKey, btnElement, isReRender = false) {
    if (!PLATFORM_CONFIG[platformKey]) return;
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