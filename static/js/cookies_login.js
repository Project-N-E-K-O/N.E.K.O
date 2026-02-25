/**
 * N.E.K.O 凭证录入脚本 - 企业级加固版
 * 修复：DOM 节点依赖、HTML 标签转义、ID 规范化、异步竞争过滤
 */

const PLATFORM_CONFIG = {
    'bilibili': {
        name: 'Bilibili', icon: '📺', theme: '#4f46e5',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 <b>bilibili.com</b><br>2. 按 <b>F12</b> 选 <b>Application (应用)</b> -> <b>Cookies</b>。<br>3. 复制下方字段对应的值。`,
        fields: [
            { key: 'SESSDATA', label: 'SESSDATA', desc: '核心身份凭证 (必填)', required: true },
            { key: 'bili_jct', label: 'bili_jct', desc: 'CSRF Token (必填)', required: true },
            { key: 'DedeUserID', label: 'DedeUserID', desc: '账号 UID (必填)', required: true },
            { key: 'buvid3', label: 'buvid3', desc: '设备指纹 (选填)', required: false }
        ]
    },
    'douyin': {
        name: '抖音', icon: '🎵', theme: '#000000',
        instruction: `<b>获取途径：</b>在 <b>douyin.com</b> 的 Cookies 中查找。`,
        fields: [
            { key: 'sessionid', label: 'sessionid', desc: '核心会话凭证 (必填)', required: true },
            { key: 'ttwid', label: 'ttwid', desc: '设备风控码 (必填)', required: true },
            { key: 'passport_csrf_token', label: 'csrf_token', desc: '安全验证令牌', required: false },
            { key: 'odin_tt', label: 'odin_tt', desc: '设备特征', required: false }
        ]
    },
    'kuaishou': {
        name: '快手', icon: '🧡', theme: '#ff5000',
        instruction: `<b>获取途径：</b>在 <b>kuaishou.com</b> 的 Cookies 中查找。`,
        fields: [
            // 修复点：后端 key 包含点号，通过 mapKey 处理 DOM ID
            { key: 'kuaishou.server.web_st', mapKey: 'ks_web_st', label: 'web_st', desc: '核心票据 (必填)', required: true },
            { key: 'kuaishou.server.web_ph', mapKey: 'ks_web_ph', label: 'web_ph', desc: '辅助票据 (必填)', required: true },
            { key: 'userId', label: 'userId', desc: '用户ID', required: true },
            { key: 'did', label: 'did', desc: '设备ID', required: true }
        ]
    },
    'weibo': {
        name: '微博', icon: '🌏', theme: '#f59e0b',
        instruction: `<b>获取途径：</b>在 <b>weibo.com</b> 的 Cookies 中查找。`,
        fields: [
            { key: 'SUB', label: 'SUB', desc: '核心凭证 (以 _2A 开头)', required: true },
            { key: 'XSRF-TOKEN', label: 'XSRF-TOKEN', desc: '防伪造令牌', required: false }
        ]
    },
    'twitter': {
        name: 'Twitter/X', icon: '🐦', theme: '#0ea5e9',
        instruction: `<b>获取途径：</b>在 <b>x.com</b> 的 Cookies 中查找。`,
        fields: [
            { key: 'auth_token', label: 'auth_token', desc: '核心身份 Token', required: true },
            { key: 'ct0', label: 'ct0', desc: 'CSRF 校验码', required: true }
        ]
    }
};

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
    if (submitText) submitText.textContent = `保存 ${config.name} 配置`;
}

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
            showAlert(false, `⚠️ 请填写必填项: [${f.label}]`);
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
                    
                showAlert(false, `⚠️ [${f.label}] 包含无效字符...`);
            }
            
            const prevVal = sanitizedVal;
            sanitizedVal = sanitizedVal.trim();
            if (sanitizedVal !== prevVal) {
                showAlert(false, `⚠️ [${f.label}] 包含前/后空白，已自动修剪，请确认后重新提交`);
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
            showAlert(true, `✅ ${config.name} 凭证保存成功！`);
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
            showAlert(false, errMsg || "保存失败，请检查格式是否正确");
        }
    } catch (err) {
        showAlert(false, "❌ 网络异常: 无法连接至后端服务");
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
            statusTag.textContent = active ? '● 已就绪' : '○ 未配置';

            // 5. 组装状态信息容器
            statusInfo.appendChild(statusName);
            statusInfo.appendChild(statusTag);

            // 6. 创建删除按钮（仅在active为true时创建）
            if (active) {
                const delBtn = document.createElement('button');
                delBtn.className = 'del-btn';
                delBtn.textContent = '移除';
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
    if (!confirm(`确定要清空 ${PLATFORM_CONFIG[platformKey]?.name || '该平台'} 的登录凭证吗？`)) return;

    try {
        const res = await fetch(`/api/auth/cookies/${platformKey}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showAlert(true, "✅ 已成功移除凭证");
            refreshStatusList();
        } else {
            showAlert(false, data.message || "删除失败");
        }
    } catch (e) {
        showAlert(false, "❌ 删除失败，请检查网络");
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
