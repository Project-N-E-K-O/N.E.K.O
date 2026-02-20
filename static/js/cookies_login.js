/**
 * N.E.K.O 填空题版凭证录入脚本
 */

const PLATFORM_CONFIG = {
    'bilibili': {
        name: 'Bilibili', icon: '📺', theme: '#4f46e5',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 bilibili.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 在左侧展开 <b>Cookies</b> 并点击 b站 网址。<br>4. 在右侧列表中找到以下对应名称的值，双击复制填入。`,
        fields: [
            { key: 'SESSDATA', label: 'SESSDATA', desc: '核心身份凭证 (必填)', required: true },
            { key: 'bili_jct', label: 'bili_jct', desc: 'CSRF Token (必填)', required: true },
            { key: 'DedeUserID', label: 'DedeUserID', desc: '你的账号UID (必填)', required: true },
            { key: 'buvid3', label: 'buvid3', desc: '设备指纹 (选填，建议填入防风控)', required: false }
        ]
    },
   'douyin': {
        name: '抖音', icon: '🎵', theme: '#000000', // 换了个更有音符感的图标
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 douyin.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 左侧展开 <b>Cookies</b> 列表，找到并复制以下字段。`,
        fields: [
            { key: 'sessionid', label: 'sessionid', desc: '核心会话凭证 (必填，登录状态关键)', required: true },
            { key: 'ttwid', label: 'ttwid', desc: '设备风控码 (必填，防止被当成爬虫)', required: true },
            { key: 'passport_csrf_token', label: 'passport_csrf_token', desc: '安全验证令牌 (选填，建议提供)', required: false },
            { key: 'odin_tt', label: 'odin_tt', desc: '设备追踪特征 (选填)', required: false }
        ]
    },
    'kuaishou': {
        name: '快手', icon: '🧡', theme: '#ff5000',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 kuaishou.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 左侧展开 <b>Cookies</b> 列表，找到并复制以下字段。`,
        fields: [
            { key: 'kuaishou.server.web_st', label: 'web_st', desc: '核心登录票据 (必填)', required: true },
            { key: 'kuaishou.server.web_ph', label: 'web_ph', desc: '辅助登录票据 (必填)', required: true },
            { key: 'userId', label: 'userId', desc: '你的用户ID (必填)', required: true },
            { key: 'did', label: 'did', desc: '设备ID (必填，防风控)', required: true }
        ]
    },
    'weibo': {
        name: '微博', icon: '🌏', theme: '#f59e0b',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 weibo.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 左侧展开 <b>Cookies</b> 列表，找到并复制以下字段。`,
        fields: [
            { key: 'SUB', label: 'SUB', desc: '核心登录凭证 (必填, 以_2A开头)', required: true },
            // 替换为 XSRF-TOKEN，配合后端的防 500 报错机制
            { key: 'XSRF-TOKEN', label: 'XSRF-TOKEN', desc: '防伪造令牌 (选填, 若未找到后端会自动伪造)', required: false }
        ]
    },
    'twitter': {
        name: 'Twitter/X', icon: '🐦', theme: '#0ea5e9',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 x.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 左侧展开 <b>Cookies</b> 列表，找到并复制以下字段。`,
        fields: [
            { key: 'auth_token', label: 'auth_token', desc: '核心身份 Token (必填)', required: true },
            { key: 'ct0', label: 'ct0', desc: '防跨站攻击校验码 (必填)', required: true }
        ]
    },
    'reddit': {
        name: 'Reddit', icon: '👽', theme: '#ff4500',
        instruction: `<b>获取途径：</b><br>1. 浏览器登录 reddit.com<br>2. 按 <b>F12</b> 打开开发者工具，点击顶部 <b>Application (应用)</b>。<br>3. 左侧展开 <b>Cookies</b> 列表，找到并复制以下字段。`,
        fields: [
            { key: 'reddit_session', label: 'reddit_session', desc: '会话凭证 (必填)', required: true }
        ]
    }
};

let currentPlatform = 'bilibili';

document.addEventListener('DOMContentLoaded', () => {
    switchTab('bilibili', document.querySelector('.tab-btn'));
    refreshStatusList();
});

// 动态渲染填空题
function switchTab(platformKey, btnElement) {
    currentPlatform = platformKey;
    const config = PLATFORM_CONFIG[platformKey];

    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    btnElement.classList.add('active');

    // 渲染说明
    const descBox = document.getElementById('panel-desc');
    descBox.style.borderColor = config.theme;
    descBox.innerHTML = config.instruction;

    // 渲染填空题表单
    const fieldsContainer = document.getElementById('dynamic-fields');
    fieldsContainer.innerHTML = ''; // 清空旧表单
    
    config.fields.forEach(f => {
        const fieldHtml = `
            <div class="field-group">
                <label for="input-${f.key}">
                    <span>${f.label} ${f.required ? '<span class="req-star">*</span>' : ''}</span>
                    <span class="desc">${f.desc}</span>
                </label>
                <input type="text" id="input-${f.key}" placeholder="在此粘贴 ${f.key} 的值..." autocomplete="off">
            </div>
        `;
        fieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);
    });

    document.getElementById('submit-text').textContent = `保存 ${config.name} 配置`;
    document.getElementById('main-alert').style.display = 'none';
}

// 收集填空题并提交
async function submitCurrentCookie() {
    const config = PLATFORM_CONFIG[currentPlatform];
    let cookiePairs = [];
    
    // 遍历抓取用户填写的值
    for (let f of config.fields) {
        const inputVal = document.getElementById(`input-${f.key}`).value.trim();
        
        // 必填项校验
        if (f.required && !inputVal) {
            showAlert(false, `⚠️ 请完整填写必填字段：<b>${f.label}</b>`);
            document.getElementById(`input-${f.key}`).focus();
            return;
        }
        
        // 只要填了，就拼装成 key=value
        if (inputVal) {
            cookiePairs.push(`${f.key}=${inputVal}`);
        }
    }

    // 将数组用分号拼装成后端熟悉的原始 Cookie 字符串
    const finalCookieString = cookiePairs.join('; ');
    const isEncrypt = document.getElementById('encrypt-toggle').checked;
    const submitBtn = document.getElementById('submit-btn');
    const originalText = document.getElementById('submit-text').textContent;

    submitBtn.disabled = true;
    document.getElementById('submit-text').textContent = '安全保存中...';

    try {
        const response = await fetch('/api/auth/cookies/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                platform: currentPlatform, 
                cookie_string: finalCookieString, 
                encrypt: isEncrypt 
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showAlert(true, `✅ ${config.name} 凭证已安全保存！`);
            // 清空所有填空框
            config.fields.forEach(f => document.getElementById(`input-${f.key}`).value = '');
            refreshStatusList();
        } else {
            showAlert(false, data.message || data.detail || "❌ 保存失败，请检查格式");
        }
    } catch (error) {
        showAlert(false, "❌ 网络异常，请检查后端服务器");
    } finally {
        submitBtn.disabled = false;
        document.getElementById('submit-text').textContent = originalText;
    }
}

function showAlert(success, message) {
    const alertEl = document.getElementById('main-alert');
    alertEl.style.display = 'block';
    alertEl.style.backgroundColor = success ? '#ecfdf5' : '#fef2f2';
    alertEl.style.color = success ? '#059669' : '#dc2626';
    alertEl.style.border = `1px solid ${success ? '#a7f3d0' : '#fecaca'}`;
    alertEl.textContent = message;
    setTimeout(() => { alertEl.style.display = 'none'; }, 4000);
}

// 状态监控
async function refreshStatusList() {
    const container = document.getElementById('platform-list-content');
    const platforms = Object.keys(PLATFORM_CONFIG);
    
    try {
        const promises = platforms.map(p => fetch(`/api/auth/cookies/${p}`).then(res => res.json()).catch(() => ({ success: false })));
        const results = await Promise.all(promises);
        
        let html = '';
        results.forEach((data, index) => {
            const platformKey = platforms[index];
            const config = PLATFORM_CONFIG[platformKey];
            const hasCookies = data.success && data.data && data.data.has_cookies;

            html += `
                <div class="status-card" style="border-left: 4px solid ${hasCookies ? '#10b981' : '#cbd5e1'}">
                    <div>
                        <div style="font-weight: 600; color: #1e293b; margin-bottom: 2px;">${config.icon} ${config.name}</div>
                        <div style="font-size: 13px; color: ${hasCookies ? '#10b981' : '#94a3b8'};">
                            ${hasCookies ? '🟢 凭证已就绪' : '⚪ 未检测到凭证'}
                        </div>
                    </div>
                    ${hasCookies ? `<button onclick="deleteCookie('${platformKey}')" style="background: #fee2e2; color: #ef4444; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer;">清空</button>` : ''}
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = '<div style="color:#ef4444; text-align:center;">状态加载失败</div>';
    }
}

async function deleteCookie(platformKey) {
    if (!confirm(`⚠️ 确定要清空 ${PLATFORM_CONFIG[platformKey].name} 的本地凭证吗？`)) return;
    try {
        const response = await fetch(`/api/auth/cookies/${platformKey}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            refreshStatusList();
            showAlert(true, `✅ 已清空`);
         }else{
            showAlert(false, data.message || '删除失败');
        }
    }catch(error){
        //加错误提示
        showAlert(false, '删除失败，请检查服务状态');
        console.error('删除出错', error);
    }
}
