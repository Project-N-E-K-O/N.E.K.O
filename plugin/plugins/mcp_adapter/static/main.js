/**
 * MCP Adapter Frontend
 */

const API_BASE = '';

// 获取插件 ID（从 URL 中解析）
function getPluginId() {
  const path = window.location.pathname;
  const match = path.match(/\/plugin\/([^/]+)\/ui/);
  return match ? match[1] : 'mcp_adapter';
}

const PLUGIN_ID = getPluginId();

// 防抖状态
let isLoading = false;
let loadDebounceTimer = null;

// 防抖的 loadData
function debouncedLoadData(delay = 300) {
  if (loadDebounceTimer) {
    clearTimeout(loadDebounceTimer);
  }
  loadDebounceTimer = setTimeout(() => {
    loadData();
  }, delay);
}

// 显示消息（替代 alert，因为 iframe 沙箱可能阻止 alert）
function showMessage(msg, type = 'info') {
  try {
    console.log(`[${type.toUpperCase()}] ${msg}`);
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    toast.style.cssText = `
      position: fixed;
      top: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: ${type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#3b82f6'};
      color: white;
      padding: 12px 24px;
      border-radius: 8px;
      z-index: 9999;
      font-size: 14px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      animation: fadeIn 0.3s ease;
      white-space: pre-wrap;
      max-width: 80%;
      text-align: center;
    `;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  } catch (e) {
    console.log(msg);
  }
}

// 显示确认对话框（替代 confirm，因为 iframe 沙箱可能阻止 confirm）
function showConfirm(msg) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0,0,0,0.5);
      z-index: 9998;
      display: flex;
      align-items: center;
      justify-content: center;
    `;
    
    const dialog = document.createElement('div');
    dialog.style.cssText = `
      background: #1e293b;
      border: 1px solid rgba(255,255,255,0.2);
      border-radius: 12px;
      padding: 24px;
      max-width: 400px;
      color: white;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    `;
    dialog.innerHTML = `
      <div style="margin-bottom: 20px; white-space: pre-wrap;">${escapeHtml(msg)}</div>
      <div style="display: flex; gap: 12px; justify-content: flex-end;">
        <button id="confirm-cancel" style="padding: 8px 16px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.2); background: transparent; color: white; cursor: pointer;">取消</button>
        <button id="confirm-ok" style="padding: 8px 16px; border-radius: 6px; border: none; background: #ef4444; color: white; cursor: pointer;">确定</button>
      </div>
    `;
    
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    
    dialog.querySelector('#confirm-cancel').onclick = () => {
      overlay.remove();
      resolve(false);
    };
    dialog.querySelector('#confirm-ok').onclick = () => {
      overlay.remove();
      resolve(true);
    };
  });
}

// API 调用封装
async function callEntry(entryId, params = {}) {
  // 从 localStorage 获取认证 token
  const token = localStorage.getItem('auth_token') || '';
  
  const response = await fetch(`${API_BASE}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': token ? `Bearer ${token}` : '',
    },
    body: JSON.stringify({
      plugin_id: PLUGIN_ID,
      entry_id: entryId,
      args: params,
    }),
  });
  
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  
  const data = await response.json();
  // 获取运行结果
  if (data.run_id) {
    return await getRunResult(data.run_id);
  }
  return data;
}

// 获取运行结果
async function getRunResult(runId, maxRetries = 30) {
  const token = localStorage.getItem('auth_token') || '';
  
  for (let i = 0; i < maxRetries; i++) {
    const response = await fetch(`${API_BASE}/runs/${runId}`, {
      headers: {
        'Authorization': token ? `Bearer ${token}` : '',
      },
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    
    if (data.status === 'succeeded' || data.status === 'failed') {
      // 获取 export items 来获取实际结果
      try {
        const exportResponse = await fetch(`${API_BASE}/runs/${runId}/export`, {
          headers: {
            'Authorization': token ? `Bearer ${token}` : '',
          },
        });
        
        if (exportResponse.ok) {
          const exportData = await exportResponse.json();
          const items = exportData.items || [];
          
          // Find the trigger_response export item (json or text type)
          for (const item of items) {
            let pluginResponse = null;

            if (item.type === 'json' && (item.json != null || item.json_data != null)) {
              // New format: structured JSON export (from manager.py)
              const raw = item.json ?? item.json_data;
              pluginResponse = raw.plugin_response || raw;
            } else if (item.type === 'text' && item.text) {
              // Legacy format: JSON-encoded text export
              try {
                const parsed = JSON.parse(item.text);
                pluginResponse = parsed.plugin_response || parsed;
              } catch (e) {
                console.warn('Failed to parse export item:', e);
                continue;
              }
            }

            if (pluginResponse) {
              if (data.status === 'succeeded') {
                return { 
                  success: pluginResponse.success !== false, 
                  data: pluginResponse.data || {},
                  error: pluginResponse.error?.message || pluginResponse.error
                };
              } else {
                return { 
                  success: false, 
                  error: pluginResponse.error?.message || data.error?.message || 'Unknown error' 
                };
              }
            }
          }
        }
      } catch (e) {
        console.warn('Failed to fetch export:', e);
      }
      
      // 回退到旧逻辑
      if (data.status === 'succeeded') {
        return { success: true, data: data.result || {} };
      } else {
        return { success: false, error: data.error?.message || 'Unknown error' };
      }
    }
    
    // 等待 200ms 后重试（减少轮询频率）
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  
  return { success: false, error: 'Timeout waiting for result' };
}

// HTML 转义
function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// JS 字符串转义（用于 onclick 等内联事件）
function escapeJsString(text) {
  if (!text) return '';
  return text.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// 加载数据
async function loadData() {
  // 防止重复加载
  if (isLoading) {
    console.log('loadData: already loading, skipping');
    return;
  }
  isLoading = true;
  
  try {
    // 只加载服务器列表
    const serversResult = await callEntry('list_servers');
    if (serversResult.success && serversResult.data) {
      renderManageServersList(serversResult.data.servers || []);
    } else {
      document.getElementById('manage-servers-list').innerHTML = 
        `<div class="error">${escapeHtml(serversResult.error || '加载失败')}</div>`;
    }
  } catch (error) {
    console.error('Failed to load data:', error);
    document.getElementById('manage-servers-list').innerHTML = 
      `<div class="error">加载失败: ${escapeHtml(error.message)}</div>`;
  } finally {
    isLoading = false;
  }
}

// 连接服务器
async function connectServer(serverName) {
  try {
    const result = await callEntry('connect_server', { server_name: serverName });
    if (result.success) {
      showMessage(`已连接到 ${serverName}`);
      debouncedLoadData();
    } else {
      showMessage(`连接失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    showMessage(`连接失败: ${error.message}`);
  }
}

// 断开服务器
async function disconnectServer(serverName) {
  if (!await showConfirm(`确定要断开 ${serverName} 吗？`)) {
    return;
  }
  
  try {
    const result = await callEntry('disconnect_server', { server_name: serverName });
    if (result.success) {
      showMessage(`已断开 ${serverName}`);
      debouncedLoadData();
    } else {
      showMessage(`断开失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    showMessage(`断开失败: ${error.message}`);
  }
}

// 视图切换
function switchView(viewName) {
  // 隐藏所有视图
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  // 显示目标视图
  const targetView = document.getElementById(`view-${viewName}`);
  if (targetView) {
    targetView.classList.add('active');
  }
  
  // 更新导航栏状态
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.view === viewName);
  });
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  loadData();
  
  // 刷新按钮
  document.getElementById('refresh-btn').addEventListener('click', () => debouncedLoadData(0));
  
  // 导航栏点击
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      switchView(item.dataset.view);
    });
  });
  
  // 服务器管理事件
  const addServerBtn = document.getElementById('add-server-btn');
  if (addServerBtn) {
    addServerBtn.addEventListener('click', () => toggleAddServerForm(true));
  }
  
  const cancelAddBtn = document.getElementById('cancel-add-btn');
  if (cancelAddBtn) {
    cancelAddBtn.addEventListener('click', () => {
      toggleAddServerForm(false);
      clearAddServerForm();
    });
  }
  
  const confirmAddBtn = document.getElementById('confirm-add-btn');
  if (confirmAddBtn) {
    confirmAddBtn.addEventListener('click', addServer);
  }
  
  const removeSelectedBtn = document.getElementById('remove-selected-btn');
  if (removeSelectedBtn) {
    removeSelectedBtn.addEventListener('click', removeSelectedServers);
  }
  
  const transportSelect = document.getElementById('server-transport');
  if (transportSelect) {
    transportSelect.addEventListener('change', updateFormFields);
  }
  
  // JSON 导入事件
  const importJsonBtn = document.getElementById('import-json-btn');
  if (importJsonBtn) {
    importJsonBtn.addEventListener('click', () => toggleImportJsonForm(true));
  }
  
  const cancelImportBtn = document.getElementById('cancel-import-btn');
  if (cancelImportBtn) {
    cancelImportBtn.addEventListener('click', () => {
      toggleImportJsonForm(false);
      document.getElementById('json-input').value = '';
    });
  }
  
  const confirmImportBtn = document.getElementById('confirm-import-btn');
  if (confirmImportBtn) {
    confirmImportBtn.addEventListener('click', importJsonConfig);
  }
  
  // 与主应用通信
  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'neko-host-message') {
      console.log('Received message from host:', event.data.payload);
      if (event.data.payload.action === 'refresh') {
        debouncedLoadData();
      }
    }
  });
  
  // 通知主应用已加载
  if (window.parent !== window) {
    window.parent.postMessage({
      type: 'plugin-ui-message',
      payload: { action: 'loaded', pluginId: PLUGIN_ID }
    }, '*');
  }
});

// ========== 服务器管理功能 ==========

let selectedServers = new Set();

// 渲染可选择的服务器列表
function renderManageServersList(servers) {
  const container = document.getElementById('manage-servers-list');
  
  if (!servers || servers.length === 0) {
    container.innerHTML = '<div class="empty">暂无配置的服务器</div>';
    return;
  }
  
  container.innerHTML = servers.map(server => {
    // 渲染工具列表
    let toolsHtml = '';
    if (server.connected && server.tools && server.tools.length > 0) {
      toolsHtml = `
        <div class="server-tools">
          <div class="tools-header">🛠️ 可用工具 (${server.tools.length})</div>
          <div class="tools-list-inline">
            ${server.tools.map(t => `
              <div class="tool-item-inline" title="${escapeHtml(t.description || '')}">
                <span class="tool-name">${escapeHtml(t.name)}</span>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }
    
    return `
      <div class="server-card selectable ${selectedServers.has(server.name) ? 'selected' : ''}" 
           data-server="${escapeHtml(server.name)}"
           onclick="toggleServerSelection('${escapeJsString(server.name)}')">
        <div class="server-checkbox">
          <input type="checkbox" ${selectedServers.has(server.name) ? 'checked' : ''} 
                 onclick="event.stopPropagation(); toggleServerSelection('${escapeJsString(server.name)}')" />
        </div>
        <div class="server-info">
          <div class="server-status ${server.connected ? 'connected' : 'disconnected'}"></div>
          <div class="server-details">
            <div class="server-name">${escapeHtml(server.name)}</div>
            <div class="server-transport">${escapeHtml(server.transport || 'unknown')}</div>
            ${server.error ? `<div class="error">${escapeHtml(server.error)}</div>` : ''}
            ${toolsHtml}
          </div>
        </div>
        <div class="server-actions">
          ${server.connected 
            ? `<button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); disconnectServer('${escapeJsString(server.name)}')">断开</button>`
            : `<button class="btn btn-success btn-sm" onclick="event.stopPropagation(); connectServer('${escapeJsString(server.name)}')">连接</button>`
          }
        </div>
      </div>
    `;
  }).join('');
  
  updateRemoveButton();
}

// 切换服务器选择状态
function toggleServerSelection(serverName) {
  if (selectedServers.has(serverName)) {
    selectedServers.delete(serverName);
  } else {
    selectedServers.add(serverName);
  }
  
  // 更新 UI
  const card = document.querySelector(`[data-server="${serverName}"]`);
  if (card) {
    card.classList.toggle('selected', selectedServers.has(serverName));
    const checkbox = card.querySelector('input[type="checkbox"]');
    if (checkbox) checkbox.checked = selectedServers.has(serverName);
  }
  
  updateRemoveButton();
}

// 更新移除按钮状态
function updateRemoveButton() {
  const btn = document.getElementById('remove-selected-btn');
  if (btn) {
    btn.disabled = selectedServers.size === 0;
    btn.textContent = selectedServers.size > 0 
      ? `🗑️ 移除选中 (${selectedServers.size})`
      : '🗑️ 移除选中';
  }
}

// 显示/隐藏添加服务器表单
function toggleAddServerForm(show) {
  const form = document.getElementById('add-server-form');
  if (form) {
    form.style.display = show ? 'block' : 'none';
  }
}

// 根据传输类型切换表单字段
function updateFormFields() {
  const transport = document.getElementById('server-transport').value;
  const commandGroup = document.getElementById('command-group');
  const argsGroup = document.getElementById('args-group');
  const urlGroup = document.getElementById('url-group');
  
  if (transport === 'stdio') {
    commandGroup.style.display = 'block';
    argsGroup.style.display = 'block';
    urlGroup.style.display = 'none';
  } else {
    commandGroup.style.display = 'none';
    argsGroup.style.display = 'none';
    urlGroup.style.display = 'block';
  }
}

// 添加服务器
async function addServer() {
  const name = document.getElementById('server-name').value.trim();
  const transport = document.getElementById('server-transport').value;
  const command = document.getElementById('server-command').value.trim();
  const argsStr = document.getElementById('server-args').value.trim();
  const url = document.getElementById('server-url').value.trim();
  const autoConnect = document.getElementById('server-auto-connect').checked;
  
  if (!name) {
    showMessage('请输入服务器名称');
    return;
  }
  
  const params = {
    name,
    transport,
    auto_connect: autoConnect,
  };
  
  if (transport === 'stdio') {
    if (!command) {
      showMessage('stdio 传输类型需要指定命令');
      return;
    }
    params.command = command;
    if (argsStr) {
      params.args = argsStr.split(',').map(s => s.trim()).filter(s => s);
    }
  } else {
    if (!url) {
      showMessage('SSE/HTTP 传输类型需要指定 URL');
      return;
    }
    params.url = url;
  }
  
  try {
    const result = await callEntry('add_server', params);
    if (result.success) {
      showMessage(result.data.message || '添加成功');
      toggleAddServerForm(false);
      clearAddServerForm();
      debouncedLoadData();
    } else {
      showMessage(`添加失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    showMessage(`添加失败: ${error.message}`);
  }
}

// 清空添加表单
function clearAddServerForm() {
  document.getElementById('server-name').value = '';
  document.getElementById('server-transport').value = 'stdio';
  document.getElementById('server-command').value = '';
  document.getElementById('server-args').value = '';
  document.getElementById('server-url').value = '';
  document.getElementById('server-auto-connect').checked = true;
  updateFormFields();
}

// 批量移除服务器
async function removeSelectedServers() {
  if (selectedServers.size === 0) return;
  
  const names = Array.from(selectedServers);
  if (!await showConfirm(`确定要移除 ${names.length} 个服务器吗？\n\n${names.join('\n')}`)) {
    return;
  }
  
  try {
    const result = await callEntry('remove_servers', { server_names: names });
    if (result.success) {
      showMessage(result.data.message || '移除成功');
      selectedServers.clear();
      debouncedLoadData();
    } else {
      showMessage(`移除失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    showMessage(`移除失败: ${error.message}`);
  }
}

// 加载服务器管理列表
async function loadManageServers() {
  try {
    const result = await callEntry('list_servers');
    if (result.success && result.data) {
      renderManageServersList(result.data.servers || []);
    }
  } catch (error) {
    console.error('Failed to load servers:', error);
  }
}

// ========== JSON 导入功能 ==========

// 显示/隐藏导入 JSON 表单
function toggleImportJsonForm(show) {
  const form = document.getElementById('import-json-form');
  if (form) {
    form.style.display = show ? 'block' : 'none';
  }
  // 隐藏添加表单
  if (show) {
    toggleAddServerForm(false);
  }
}

// 解析 MCP 配置 JSON，自动识别格式
function parseMcpConfig(jsonStr) {
  const data = JSON.parse(jsonStr);
  const servers = [];
  
  // 支持的格式：
  // 1. { "mcpServers": { "name": {...} } }
  // 2. { "name": {...} } (直接是服务器配置)
  
  let serversObj = data.mcpServers || data;
  
  for (const [name, config] of Object.entries(serversObj)) {
    if (typeof config !== 'object' || config === null) continue;
    
    const server = { name };
    
    // 自动识别传输类型
    if (config.type) {
      // 标准化 type 字段
      const typeMap = {
        'stdio': 'stdio',
        'sse': 'sse',
        'streamable_http': 'streamable-http',
        'streamable-http': 'streamable-http',
        'http': 'streamable-http',
      };
      server.transport = typeMap[config.type] || 'stdio';
    } else if (config.url) {
      // 有 URL 说明是 SSE 或 HTTP
      server.transport = config.url.includes('/sse') ? 'sse' : 'streamable-http';
    } else if (config.command) {
      // 有 command 说明是 stdio
      server.transport = 'stdio';
    } else {
      // 默认 stdio
      server.transport = 'stdio';
    }
    
    // 提取配置
    if (config.command) server.command = config.command;
    if (config.args) server.args = config.args;
    if (config.url) server.url = config.url;
    if (config.env) server.env = config.env;
    
    servers.push(server);
  }
  
  return servers;
}

// 导入 JSON 配置
async function importJsonConfig() {
  const jsonInput = document.getElementById('json-input');
  const autoConnect = document.getElementById('import-auto-connect').checked;
  
  const jsonStr = jsonInput.value.trim();
  if (!jsonStr) {
    showMessage('请输入 JSON 配置');
    return;
  }
  
  let servers;
  try {
    servers = parseMcpConfig(jsonStr);
  } catch (e) {
    showMessage(`JSON 解析失败: ${e.message}`);
    return;
  }
  
  if (servers.length === 0) {
    showMessage('未找到有效的服务器配置');
    return;
  }
  
  // 逐个添加服务器
  const results = { success: [], failed: [] };
  
  for (const server of servers) {
    try {
      const params = {
        name: server.name,
        transport: server.transport,
        auto_connect: autoConnect,
      };
      if (server.command) params.command = server.command;
      if (server.args) params.args = server.args;
      if (server.url) params.url = server.url;
      if (server.env) params.env = server.env;
      
      const result = await callEntry('add_server', params);
      if (result.success) {
        results.success.push(server.name);
      } else {
        // 提取错误信息
        let errorMsg = result.error;
        if (result.data && result.data.error) {
          errorMsg = result.data.error.message || result.data.error.code || result.error;
        }
        results.failed.push({ name: server.name, error: errorMsg });
      }
    } catch (e) {
      results.failed.push({ name: server.name, error: e.message });
    }
  }
  
  // 显示结果
  let msg = `导入完成:\n成功: ${results.success.length} 个`;
  if (results.failed.length > 0) {
    msg += `\n失败: ${results.failed.length} 个`;
    for (const f of results.failed) {
      msg += `\n  - ${f.name}: ${f.error}`;
    }
  }
  showMessage(msg);
  
  // 清理并刷新
  toggleImportJsonForm(false);
  jsonInput.value = '';
  debouncedLoadData();
}

// 暴露全局函数供 HTML 调用
window.connectServer = connectServer;
window.disconnectServer = disconnectServer;
window.switchView = switchView;
window.toggleServerSelection = toggleServerSelection;
window.addServer = addServer;
window.removeSelectedServers = removeSelectedServers;
window.importJsonConfig = importJsonConfig;
