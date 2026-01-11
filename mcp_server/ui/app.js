// MCP Server Dashboard JavaScript

const API_BASE = '';
const STATUS_ENDPOINT = '/status';
const SERVERS_ENDPOINT = '/api/servers';
const RECONNECT_ENDPOINT = '/api/reconnect';
const REFRESH_INTERVAL = 3000; // 3秒

let autoRefreshEnabled = false;
let refreshInterval = null;

// DOM 元素
const elements = {
    statusDot: document.getElementById('statusDot'),
    statusText: document.getElementById('statusText'),
    serverName: document.getElementById('serverName'),
    serverVersion: document.getElementById('serverVersion'),
    protocol: document.getElementById('protocol'),
    lastUpdate: document.getElementById('lastUpdate'),
    totalTools: document.getElementById('totalTools'),
    localTools: document.getElementById('localTools'),
    remoteTools: document.getElementById('remoteTools'),
    connectedServers: document.getElementById('connectedServers'),
    localToolsList: document.getElementById('localToolsList'),
    remoteToolsList: document.getElementById('remoteToolsList'),
    connectedServersList: document.getElementById('connectedServersList'),
    configuredServersList: document.getElementById('configuredServersList'),
    serverConfigList: document.getElementById('serverConfigList'),
    refreshBtn: document.getElementById('refreshBtn'),
    autoRefreshBtn: document.getElementById('autoRefreshBtn'),
    reconnectBtn: document.getElementById('reconnectBtn'),
    addServerBtn: document.getElementById('addServerBtn'),
    importRemoteBtn: document.getElementById('importRemoteBtn'),
    updateTime: document.getElementById('updateTime'),
    addServerModal: document.getElementById('addServerModal'),
    importRemoteModal: document.getElementById('importRemoteModal'),
    serverTypeSelect: document.getElementById('serverTypeSelect'),
    serverUrlInput: document.getElementById('serverUrlInput'),
    apiKeyInput: document.getElementById('apiKeyInput'),
    commandInput: document.getElementById('commandInput'),
    argsInput: document.getElementById('argsInput'),
    httpServerConfig: document.getElementById('httpServerConfig'),
    stdioServerConfig: document.getElementById('stdioServerConfig'),
    remoteConfigInput: document.getElementById('remoteConfigInput'),
    importResult: document.getElementById('importResult'),
    closeModalBtn: document.getElementById('closeModalBtn'),
    closeImportModalBtn: document.getElementById('closeImportModalBtn'),
    cancelBtn: document.getElementById('cancelBtn'),
    cancelImportBtn: document.getElementById('cancelImportBtn'),
    confirmAddBtn: document.getElementById('confirmAddBtn'),
    confirmImportBtn: document.getElementById('confirmImportBtn')
};

// 更新状态指示器
function updateStatusIndicator(status) {
    if (status === 'running') {
        elements.statusDot.className = 'status-dot status-online';
        elements.statusText.textContent = '运行中';
    } else {
        elements.statusDot.className = 'status-dot status-offline';
        elements.statusText.textContent = '离线';
    }
}

// HTML 转义函数，防止 XSS 攻击
function escapeHtml(value) {
    const s = String(value ?? '');
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 格式化时间
function formatTime(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleString('zh-CN');
}

// 渲染工具列表
function renderToolsList(container, tools) {
    if (!tools || tools.length === 0) {
        container.innerHTML = '<p class="empty-message">暂无工具</p>';
        return;
    }

    container.innerHTML = tools.map(tool => {
        const params = tool.inputSchema?.properties || {};
        const required = tool.inputSchema?.required || [];
        const paramList = Object.keys(params).map(key => {
            const param = params[key];
            const isRequired = required.includes(key);
            return `<span class="param ${isRequired ? 'required' : 'optional'}">${escapeHtml(key)}${isRequired ? '*' : ''}</span>`;
        }).join(' ');

        return `
            <div class="tool-item">
                <div class="tool-header">
                    <span class="tool-name">${escapeHtml(tool.name)}</span>
                    <span class="tool-source">${tool.source === 'local' ? '本地' : '远程'}</span>
                </div>
                <div class="tool-description">${escapeHtml(tool.description || 'No description')}</div>
                ${paramList ? `<div class="tool-params">参数: ${paramList}</div>` : ''}
            </div>
        `;
    }).join('');
}

// 渲染服务器列表
function renderServersList(container, servers, showStatus = false) {
    if (!servers || servers.length === 0) {
        container.innerHTML = '<p class="empty-message">无配置的服务器</p>';
        return;
    }

    container.innerHTML = servers.map(server => {
        const status = showStatus && server.initialized !== undefined
            ? (server.initialized ? '<span class="status-badge online">已连接</span>' : '<span class="status-badge offline">未连接</span>')
            : '';
        
        // 处理不同格式的服务器信息
        let serverInfo = '';
        if (typeof server === 'string') {
            serverInfo = server;
        } else if (server.type === 'stdio') {
            serverInfo = `stdio: ${server.command} ${(server.args || []).join(' ')}`;
        } else {
            serverInfo = server.url || server.identifier || JSON.stringify(server);
        }
        
        const typeBadge = server.type ? `<span class="type-badge ${server.type}">${server.type === 'stdio' ? '命令行' : 'HTTP'}</span>` : '';
        
        return `
            <div class="server-item">
                <div class="server-info">
                    ${typeBadge}
                    <div class="server-url">${escapeHtml(serverInfo)}</div>
                </div>
                ${status}
            </div>
        `;
    }).join('');
}

// 渲染服务器配置列表（带删除按钮）
function renderServerConfigList(container, servers, connectedServers = []) {
    if (!servers || servers.length === 0) {
        container.innerHTML = '<p class="empty-message">无配置的服务器</p>';
        return;
    }

    container.innerHTML = servers.map(server => {
        const identifier = server.identifier || (typeof server === 'string' ? server : JSON.stringify(server));
        const isConnected = connectedServers.includes(identifier);
        const status = isConnected 
            ? '<span class="status-badge online">已连接</span>' 
            : '<span class="status-badge offline">未连接</span>';
        
        // 格式化服务器信息显示
        let serverInfo = '';
        if (typeof server === 'string') {
            serverInfo = server;
        } else if (server.type === 'stdio') {
            serverInfo = `${server.command} ${(server.args || []).join(' ')}`;
        } else {
            serverInfo = server.url || identifier;
        }
        
        const typeBadge = server.type ? `<span class="type-badge ${server.type}">${server.type === 'stdio' ? '命令行' : 'HTTP'}</span>` : '';
        
        return `
            <div class="server-config-item">
                <div class="server-config-info">
                    ${typeBadge}
                    <div class="server-url">${escapeHtml(serverInfo)}</div>
                    ${status}
                </div>
                <button class="btn-delete" data-identifier="${escapeHtml(identifier)}" title="删除服务器">🗑️</button>
            </div>
        `;
    }).join('');
}

// 更新界面数据
function updateDashboard(data) {
    // 服务器信息
    elements.serverName.textContent = data.server?.name || '-';
    elements.serverVersion.textContent = data.server?.version || '-';
    elements.protocol.textContent = data.server?.protocol || 'MCP';
    elements.lastUpdate.textContent = formatTime(data.timestamp);

    // 统计信息
    const stats = data.statistics || {};
    elements.totalTools.textContent = stats.total_tools || 0;
    elements.localTools.textContent = stats.local_tools || 0;
    elements.remoteTools.textContent = stats.remote_tools || 0;
    elements.connectedServers.textContent = stats.connected_servers || 0;

    // 工具列表
    renderToolsList(elements.localToolsList, data.local_tools || []);
    renderToolsList(elements.remoteToolsList, data.remote_tools || []);

    // 服务器列表
    renderServersList(elements.connectedServersList, data.connected_servers || [], true);
    renderServersList(elements.configuredServersList, data.configured_remote_servers || []);

    // 更新时间
    elements.updateTime.textContent = formatTime(data.timestamp);

    // 状态指示器
    updateStatusIndicator(data.status || 'running');
    
    // 更新服务器配置列表
    updateServerConfigList();
}

// 获取状态数据
async function fetchStatus() {
    try {
        const response = await fetch(API_BASE + STATUS_ENDPOINT);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        updateDashboard(data);
        return data;
    } catch (error) {
        console.error('Failed to fetch status:', error);
        updateStatusIndicator('offline');
        elements.statusText.textContent = '连接失败';
        return null;
    }
}

// 刷新数据
function refresh() {
    fetchStatus();
}

// 切换自动刷新
function toggleAutoRefresh() {
    autoRefreshEnabled = !autoRefreshEnabled;
    
    if (autoRefreshEnabled) {
        elements.autoRefreshBtn.textContent = '⏸️ 自动刷新: 开启';
        elements.autoRefreshBtn.classList.add('active');
        refreshInterval = setInterval(refresh, REFRESH_INTERVAL);
    } else {
        elements.autoRefreshBtn.textContent = '⏸️ 自动刷新: 关闭';
        elements.autoRefreshBtn.classList.remove('active');
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = null;
        }
    }
}

// 获取服务器配置列表
async function fetchServersConfig() {
    try {
        const response = await fetch(API_BASE + SERVERS_ENDPOINT);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Failed to fetch servers config:', error);
        return { servers: [], connected: [] };
    }
}

// 更新服务器配置列表
async function updateServerConfigList() {
    const config = await fetchServersConfig();
    renderServerConfigList(elements.serverConfigList, config.servers || [], config.connected || []);
}

// 重新连接所有服务器
async function reconnectServers() {
    if (!confirm('确定要重新连接所有服务器吗？')) {
        return;
    }
    
    elements.reconnectBtn.disabled = true;
    elements.reconnectBtn.textContent = '连接中...';
    
    try {
        const response = await fetch(API_BASE + RECONNECT_ENDPOINT, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert(`重新连接成功！\n已连接服务器: ${data.connected_servers}\n工具总数: ${data.total_tools}`);
            refresh();
        } else {
            alert('重新连接失败: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Failed to reconnect:', error);
        alert('重新连接失败: ' + error.message);
    } finally {
        elements.reconnectBtn.disabled = false;
        elements.reconnectBtn.textContent = '🔄 重新连接所有服务器';
    }
}

// 切换服务器类型配置显示
function toggleServerTypeConfig() {
    const serverType = elements.serverTypeSelect.value;
    if (serverType === 'stdio') {
        elements.httpServerConfig.style.display = 'none';
        elements.stdioServerConfig.style.display = 'block';
        elements.commandInput.focus();
    } else {
        elements.httpServerConfig.style.display = 'block';
        elements.stdioServerConfig.style.display = 'none';
        elements.serverUrlInput.focus();
    }
}

// 显示添加服务器对话框
function showAddServerModal() {
    elements.addServerModal.style.display = 'block';
    elements.serverTypeSelect.value = 'http';
    elements.serverUrlInput.value = '';
    elements.apiKeyInput.value = '';
    elements.commandInput.value = '';
    elements.argsInput.value = '';
    toggleServerTypeConfig();
}

// 隐藏添加服务器对话框
function hideAddServerModal() {
    elements.addServerModal.style.display = 'none';
}

// 添加服务器
async function addServer() {
    const serverType = elements.serverTypeSelect.value;
    let requestBody = { type: serverType };
    
    if (serverType === 'stdio') {
        const command = elements.commandInput.value.trim();
        const argsText = elements.argsInput.value.trim();
        
        if (!command) {
            alert('请输入命令（如: npx, node, python 等）');
            return;
        }
        
        // 解析参数（每行一个）
        const args = argsText.split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0);
        
        requestBody.command = command;
        requestBody.args = args;
    } else {
        const url = elements.serverUrlInput.value.trim();
        
        if (!url) {
            alert('请输入服务器 URL');
            return;
        }
        
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            alert('URL 必须以 http:// 或 https:// 开头');
            return;
        }
        
        requestBody.url = url;
        
        const apiKey = elements.apiKeyInput.value.trim();
        if (apiKey) {
            requestBody.api_key = apiKey;
        }
    }
    
    elements.confirmAddBtn.disabled = true;
    elements.confirmAddBtn.textContent = '添加中...';
    
    try {
        const response = await fetch(API_BASE + SERVERS_ENDPOINT, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('服务器添加成功！\n请点击"重新连接所有服务器"来连接新服务器。');
            hideAddServerModal();
            updateServerConfigList();
        } else {
            alert('添加失败: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Failed to add server:', error);
        alert('添加失败: ' + error.message);
    } finally {
        elements.confirmAddBtn.disabled = false;
        elements.confirmAddBtn.textContent = '添加';
    }
}

// 删除服务器
async function deleteServer(identifier) {
    if (!confirm(`确定要删除服务器 ${identifier} 吗？`)) {
        return;
    }
    
    try {
        const response = await fetch(API_BASE + SERVERS_ENDPOINT, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ identifier: identifier })
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('服务器删除成功！');
            updateServerConfigList();
            refresh();
        } else {
            alert('删除失败: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Failed to delete server:', error);
        alert('删除失败: ' + error.message);
    }
}

// 显示导入 Remote 配置对话框
function showImportRemoteModal() {
    elements.importRemoteModal.style.display = 'block';
    elements.remoteConfigInput.value = '';
    elements.importResult.style.display = 'none';
    elements.remoteConfigInput.focus();
}

// 隐藏导入 Remote 配置对话框
function hideImportRemoteModal() {
    elements.importRemoteModal.style.display = 'none';
    elements.importResult.style.display = 'none';
}

// 导入 Remote 配置
async function importRemoteConfig() {
    const configJson = elements.remoteConfigInput.value.trim();
    
    if (!configJson) {
        alert('请输入 Remote 配置 JSON');
        return;
    }
    
    elements.confirmImportBtn.disabled = true;
    elements.confirmImportBtn.textContent = '导入中...';
    elements.importResult.style.display = 'none';
    
    try {
        const response = await fetch(API_BASE + '/api/servers/import', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ config: configJson })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // 显示导入结果
            let resultHtml = '<div class="import-success">';
            resultHtml += `<h4>✅ 导入成功！</h4>`;
            resultHtml += `<p>成功导入 ${data.added.length} 个服务器</p>`;
            
            if (data.added.length > 0) {
                resultHtml += '<div class="import-added"><strong>已添加:</strong><ul>';
                data.added.forEach(server => {
                    const serverInfo = server.identifier || server.url || JSON.stringify(server);
                    resultHtml += `<li>${escapeHtml(server.name)}: ${escapeHtml(serverInfo)}</li>`;
                });
                resultHtml += '</ul></div>';
            }
            
            if (data.skipped && data.skipped.length > 0) {
                resultHtml += '<div class="import-skipped"><strong>已跳过:</strong><ul>';
                data.skipped.forEach(server => {
                    resultHtml += `<li>${escapeHtml(server.name)}: ${escapeHtml(server.reason)}</li>`;
                });
                resultHtml += '</ul></div>';
            }
            
            if (data.errors && data.errors.length > 0) {
                resultHtml += '<div class="import-errors"><strong>错误:</strong><ul>';
                data.errors.forEach(error => {
                    resultHtml += `<li>${escapeHtml(error)}</li>`;
                });
                resultHtml += '</ul></div>';
            }
            
            resultHtml += '</div>';
            elements.importResult.innerHTML = resultHtml;
            elements.importResult.style.display = 'block';
            elements.importResult.className = 'import-result import-success';
            
            // 更新服务器列表
            updateServerConfigList();
            
            // 提示是否立即重新连接
            if (data.added.length > 0) {
                setTimeout(() => {
                    if (confirm('是否立即重新连接所有服务器？')) {
                        reconnectServers();
                    }
                }, 500);
            }
        } else {
            elements.importResult.innerHTML = `<div class="import-error"><strong>❌ 导入失败:</strong><p>${escapeHtml(data.error || 'Unknown error')}</p></div>`;
            elements.importResult.style.display = 'block';
            elements.importResult.className = 'import-result import-error';
        }
    } catch (error) {
        console.error('Failed to import config:', error);
        elements.importResult.innerHTML = `<div class="import-error"><strong>❌ 导入失败:</strong><p>${escapeHtml(error.message)}</p></div>`;
        elements.importResult.style.display = 'block';
        elements.importResult.className = 'import-result import-error';
    } finally {
        elements.confirmImportBtn.disabled = false;
        elements.confirmImportBtn.textContent = '导入';
    }
}

// 事件监听
elements.refreshBtn.addEventListener('click', refresh);
elements.autoRefreshBtn.addEventListener('click', toggleAutoRefresh);
elements.reconnectBtn.addEventListener('click', reconnectServers);
elements.addServerBtn.addEventListener('click', showAddServerModal);
elements.importRemoteBtn.addEventListener('click', showImportRemoteModal);
elements.serverTypeSelect.addEventListener('change', toggleServerTypeConfig);
elements.closeModalBtn.addEventListener('click', hideAddServerModal);
elements.closeImportModalBtn.addEventListener('click', hideImportRemoteModal);
elements.cancelBtn.addEventListener('click', hideAddServerModal);
elements.cancelImportBtn.addEventListener('click', hideImportRemoteModal);
elements.confirmAddBtn.addEventListener('click', addServer);
elements.confirmImportBtn.addEventListener('click', importRemoteConfig);

// 使用事件委托处理删除按钮点击（避免 inline onclick 的 XSS 风险）
elements.serverConfigList.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-delete');
    if (!btn) return;
    const identifier = btn.getAttribute('data-identifier');
    if (identifier) {
        deleteServer(identifier);
    }
});

// 点击模态框外部关闭
elements.addServerModal.addEventListener('click', (e) => {
    if (e.target === elements.addServerModal) {
        hideAddServerModal();
    }
});

elements.importRemoteModal.addEventListener('click', (e) => {
    if (e.target === elements.importRemoteModal) {
        hideImportRemoteModal();
    }
});

// 按 Enter 键添加服务器
elements.serverUrlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        addServer();
    }
});

// 按 Ctrl+Enter 导入配置
elements.remoteConfigInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        importRemoteConfig();
    }
});

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    refresh();
    // 默认开启自动刷新
    toggleAutoRefresh();
});

