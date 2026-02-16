/**
 * MCP Adapter Frontend
 */

const API_BASE = '/api';

// 获取插件 ID（从 URL 中解析）
function getPluginId() {
  const path = window.location.pathname;
  const match = path.match(/\/plugin\/([^/]+)\/ui/);
  return match ? match[1] : 'mcp_adapter';
}

const PLUGIN_ID = getPluginId();

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
    
    if (data.status === 'succeeded') {
      return { success: true, data: data.result || {} };
    } else if (data.status === 'failed') {
      return { success: false, error: data.error || 'Unknown error' };
    }
    
    // 等待 100ms 后重试
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  
  return { success: false, error: 'Timeout waiting for result' };
}

// 渲染服务器列表
function renderServers(servers) {
  const container = document.getElementById('servers-list');
  
  if (!servers || servers.length === 0) {
    container.innerHTML = '<div class="empty">暂无已连接的服务器</div>';
    return;
  }
  
  container.innerHTML = servers.map(server => `
    <div class="server-card">
      <div class="server-info">
        <div class="server-status ${server.connected ? 'connected' : 'disconnected'}"></div>
        <div>
          <div class="server-name">${escapeHtml(server.name)}</div>
          <div class="server-transport">${escapeHtml(server.transport || 'unknown')}</div>
          ${server.connected ? `<div class="server-tools-count">${server.tools_count || 0} 个工具</div>` : ''}
          ${server.error ? `<div class="error">${escapeHtml(server.error)}</div>` : ''}
        </div>
      </div>
      <div class="server-actions">
        ${server.connected 
          ? `<button class="btn btn-danger" onclick="disconnectServer('${escapeJsString(server.name)}')">断开</button>`
          : `<button class="btn btn-success" onclick="connectServer('${escapeJsString(server.name)}')">连接</button>`
        }
      </div>
    </div>
  `).join('');
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
  try {
    // 加载服务器列表
    const serversResult = await callEntry('list_servers');
    if (serversResult.success && serversResult.data) {
      renderServers(serversResult.data.servers || []);
    } else {
      document.getElementById('servers-list').innerHTML = 
        `<div class="error">${escapeHtml(serversResult.error || '加载失败')}</div>`;
    }
    
    // 加载工具列表
    const toolsResult = await callEntry('list_tools');
    if (toolsResult.success && toolsResult.data) {
      allTools = toolsResult.data.tools || [];
      renderToolsPreview(allTools);
      renderToolsList(allTools);
    } else {
      const errorMsg = `<div class="error">${escapeHtml(toolsResult.error || '加载失败')}</div>`;
      document.getElementById('tools-preview').innerHTML = errorMsg;
      document.getElementById('tools-list').innerHTML = errorMsg;
    }
  } catch (error) {
    console.error('Failed to load data:', error);
    document.getElementById('servers-list').innerHTML = 
      `<div class="error">加载失败: ${escapeHtml(error.message)}</div>`;
  }
}

// 连接服务器
async function connectServer(serverName) {
  try {
    const result = await callEntry('connect_server', { server_name: serverName });
    if (result.success) {
      alert(`已连接到 ${serverName}`);
      loadData();
    } else {
      alert(`连接失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    alert(`连接失败: ${error.message}`);
  }
}

// 断开服务器
async function disconnectServer(serverName) {
  if (!confirm(`确定要断开 ${serverName} 吗？`)) {
    return;
  }
  
  try {
    const result = await callEntry('disconnect_server', { server_name: serverName });
    if (result.success) {
      alert(`已断开 ${serverName}`);
      loadData();
    } else {
      alert(`断开失败: ${result.error || '未知错误'}`);
    }
  } catch (error) {
    alert(`断开失败: ${error.message}`);
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

// 工具搜索
let allTools = [];

function filterTools(keyword) {
  const lower = keyword.toLowerCase();
  const filtered = allTools.filter(tool => 
    tool.name.toLowerCase().includes(lower) ||
    (tool.description || '').toLowerCase().includes(lower) ||
    tool.server.toLowerCase().includes(lower)
  );
  renderToolsList(filtered);
}

// 渲染工具列表（完整版）
function renderToolsList(tools) {
  const container = document.getElementById('tools-list');
  
  if (!tools || tools.length === 0) {
    container.innerHTML = '<div class="empty">暂无可用工具</div>';
    return;
  }
  
  container.innerHTML = tools.map(tool => `
    <div class="tool-card">
      <div class="tool-header">
        <span class="tool-name">${escapeHtml(tool.name)}</span>
        <span class="tool-server">${escapeHtml(tool.server)}</span>
      </div>
      <div class="tool-description">${escapeHtml(tool.description || '无描述')}</div>
      <div class="tool-entry-id">Entry: ${escapeHtml(tool.entry_id)}</div>
    </div>
  `).join('');
}

// 渲染工具预览（首页简化版）
function renderToolsPreview(tools) {
  const container = document.getElementById('tools-preview');
  const countBadge = document.getElementById('tools-count');
  
  if (countBadge) countBadge.textContent = tools ? tools.length : 0;
  
  if (!tools || tools.length === 0) {
    container.innerHTML = '<div class="empty">暂无可用工具</div>';
    return;
  }
  
  // 只显示前 5 个工具
  const preview = tools.slice(0, 5);
  container.innerHTML = preview.map(tool => `
    <div class="tool-card compact">
      <div class="tool-header">
        <span class="tool-name">${escapeHtml(tool.name)}</span>
        <span class="tool-server">${escapeHtml(tool.server)}</span>
      </div>
    </div>
  `).join('');
  
  if (tools.length > 5) {
    container.innerHTML += `<div class="more-hint" onclick="switchView('tools')">还有 ${tools.length - 5} 个工具，点击查看全部 →</div>`;
  }
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  loadData();
  
  // 刷新按钮
  document.getElementById('refresh-btn').addEventListener('click', loadData);
  
  // 导航栏点击
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      switchView(item.dataset.view);
    });
  });
  
  // 工具搜索
  const searchInput = document.getElementById('tool-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      filterTools(e.target.value);
    });
  }
  
  // 与主应用通信
  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'neko-host-message') {
      console.log('Received message from host:', event.data.payload);
      if (event.data.payload.action === 'refresh') {
        loadData();
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

// 暴露全局函数供 HTML 调用
window.connectServer = connectServer;
window.disconnectServer = disconnectServer;
window.switchView = switchView;
