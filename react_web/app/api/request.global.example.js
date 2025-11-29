/**
 * 在外部 JS 文件中使用全局 request 实例的示例
 * 
 * 在 HTML 或外部 JS 文件中，可以直接使用 window.request 来发起请求
 * 
 * request.global.js 会自动初始化并暴露以下全局对象：
 * - window.request: request 实例（支持 get, post, put, delete 等方法）
 * - window.buildApiUrl(path): 构建完整的 API URL
 * - window.buildStaticUrl(path): 构建完整的静态资源 URL
 * - window.buildWebSocketUrl(path): 构建完整的 WebSocket URL
 * - window.API_BASE_URL: API 基础 URL
 * - window.STATIC_SERVER_URL: 静态资源服务器 URL
 */

// ========== 等待 request 初始化 ==========

/**
 * 等待 request.global.js 初始化完成
 * 在需要使用 request 之前调用此函数，确保 request 已初始化
 */
async function waitForRequestInit(maxWait = 5000) {
  // 如果已经初始化，直接返回
  if (window.request || window.buildApiUrl) {
    return;
  }
  
  const startTime = Date.now();
  // 轮询检查，直到 request 或 buildApiUrl 可用
  while (!window.request && !window.buildApiUrl) {
    if (Date.now() - startTime > maxWait) {
      console.warn('等待 request.global.js 初始化超时（' + maxWait + 'ms），继续执行');
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  
  if (window.request || window.buildApiUrl) {
    console.log('request.global.js 初始化完成');
  }
}

// ========== 基础使用 ==========

// GET 请求
async function fetchUsers() {
  try {
    // 确保 request 已初始化
    await waitForRequestInit();
    
    const users = await window.request.get('/api/users');
    console.log('Users:', users);
    return users;
  } catch (error) {
    console.error('Failed to fetch users:', error);
    throw error;
  }
}

// POST 请求
async function createUser(userData) {
  try {
    await waitForRequestInit();
    
    const newUser = await window.request.post('/api/users', userData);
    console.log('Created user:', newUser);
    return newUser;
  } catch (error) {
    console.error('Failed to create user:', error);
    throw error;
  }
}

// PUT 请求
async function updateUser(userId, userData) {
  try {
    await waitForRequestInit();
    
    const updatedUser = await window.request.put(`/api/users/${userId}`, userData);
    console.log('Updated user:', updatedUser);
    return updatedUser;
  } catch (error) {
    console.error('Failed to update user:', error);
    throw error;
  }
}

// DELETE 请求
async function deleteUser(userId) {
  try {
    await waitForRequestInit();
    
    await window.request.delete(`/api/users/${userId}`);
    console.log('User deleted successfully');
  } catch (error) {
    console.error('Failed to delete user:', error);
    throw error;
  }
}

// ========== 带查询参数的请求 ==========

async function searchUsers(params) {
  try {
    await waitForRequestInit();
    
    const users = await window.request.get('/api/users', { params });
    return users;
  } catch (error) {
    console.error('Failed to search users:', error);
    throw error;
  }
}

// ========== 使用工具函数构建 URL ==========

/**
 * 使用 buildApiUrl 构建完整的 API URL
 * 适用于需要手动构建 URL 的场景（如 fetch、WebSocket 等）
 */
async function buildApiUrlExample() {
  await waitForRequestInit();
  
  // 构建 API URL
  const apiUrl = window.buildApiUrl('/api/users');
  console.log('API URL:', apiUrl); // 例如: http://localhost:48911/api/users
  
  // 使用 fetch 发起请求（如果需要）
  fetch(apiUrl)
    .then(response => response.json())
    .then(data => console.log('Data:', data));
}

/**
 * 使用 buildStaticUrl 构建静态资源 URL
 * 适用于需要加载静态资源的场景
 */
async function buildStaticUrlExample() {
  await waitForRequestInit();
  
  // 构建静态资源 URL
  const imageUrl = window.buildStaticUrl('/static/icon.png');
  console.log('Image URL:', imageUrl);
  
  // 使用图片 URL
  const img = document.createElement('img');
  img.src = imageUrl;
  document.body.appendChild(img);
}

/**
 * 使用 buildWebSocketUrl 构建 WebSocket URL
 * 适用于需要建立 WebSocket 连接的场景
 */
async function buildWebSocketUrlExample() {
  await waitForRequestInit();
  
  // 构建 WebSocket URL
  const wsUrl = window.buildWebSocketUrl('/api/ws');
  console.log('WebSocket URL:', wsUrl); // 例如: ws://localhost:48911/api/ws
  
  // 建立 WebSocket 连接
  const ws = new WebSocket(wsUrl);
  ws.onopen = () => console.log('WebSocket connected');
  ws.onmessage = (event) => console.log('Message:', event.data);
  ws.onerror = (error) => console.error('WebSocket error:', error);
}

// ========== 替换原有的 fetchWithBaseUrl ==========

/**
 * 原来的代码：
 * ```javascript
 * const response = await window.fetchWithBaseUrl('/api/users');
 * const data = await response.json();
 * ```
 * 
 * 新的代码（推荐）：
 * ```javascript
 * await waitForRequestInit();
 * const data = await window.request.get('/api/users');
 * ```
 * 
 * 或者使用 buildApiUrl + fetch：
 * ```javascript
 * await waitForRequestInit();
 * const apiUrl = window.buildApiUrl('/api/users');
 * const response = await fetch(apiUrl);
 * const data = await response.json();
 * ```
 */

// ========== 实际使用示例 ==========

// 示例：获取页面配置（来自 index.html 的实际使用场景）
async function loadPageConfig(lanlanName) {
  try {
    // 等待 request 初始化完成
    await waitForRequestInit();
    
    // 优先从 URL 获取 lanlan_name
    const urlParams = new URLSearchParams(window.location.search);
    let lanlanNameFromUrl = urlParams.get('lanlan_name') || lanlanName || "";
    
    // 从路径中提取 lanlan_name (例如 /{lanlan_name})
    if (!lanlanNameFromUrl) {
      const pathParts = window.location.pathname.split('/').filter(Boolean);
      if (pathParts.length > 0 && !['focus', 'api', 'static', 'templates'].includes(pathParts[0])) {
        lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
      }
    }
    
    // 构建 API 路径
    const apiPath = lanlanNameFromUrl 
      ? `/api/config/page_config?lanlan_name=${encodeURIComponent(lanlanNameFromUrl)}`
      : '/api/config/page_config';
    
    // 使用 window.request 发起请求（已配置 baseURL，直接使用相对路径）
    let data;
    if (window.request) {
      try {
        data = await window.request.get(apiPath);
      } catch (error) {
        console.error('使用 request 获取配置失败:', error);
        // 降级到 fetch，需要构建完整 URL
        const apiUrl = window.buildApiUrl ? window.buildApiUrl(apiPath) : apiPath;
        const response = await fetch(apiUrl);
        data = await response.json();
      }
    } else {
      // 使用 fetch 时需要构建完整 URL
      const apiUrl = window.buildApiUrl ? window.buildApiUrl(apiPath) : apiPath;
      const response = await fetch(apiUrl);
      data = await response.json();
    }
    
    if (data && data.success) {
      return data;
    } else {
      throw new Error(data.message || data.error || 'Failed to load page config');
    }
  } catch (error) {
    console.error('加载页面配置时出错:', error);
    throw error;
  }
}

// 示例：在事件处理中使用
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const config = await loadPageConfig();
    console.log('Page config loaded:', config);
  } catch (error) {
    console.error('Failed to load page config:', error);
  }
});

// ========== 错误处理示例 ==========

/**
 * request 实例会自动处理以下情况：
 * - 401 未授权：自动尝试刷新 token
 * - 403 禁止访问：触发错误处理（可能重定向到登录页）
 * - 网络错误：抛出异常
 */
async function handleErrorsExample() {
  try {
    await waitForRequestInit();
    
    const data = await window.request.get('/api/protected');
    return data;
  } catch (error) {
    // 处理不同类型的错误
    if (error.response) {
      // 服务器返回了错误响应
      console.error('Error status:', error.response.status);
      console.error('Error data:', error.response.data);
      
      if (error.response.status === 403) {
        // 403 错误可能已触发重定向
        console.warn('Access forbidden');
      }
    } else if (error.request) {
      // 请求已发出但没有收到响应
      console.error('No response received:', error.request);
    } else {
      // 其他错误
      console.error('Error:', error.message);
    }
    throw error;
  }
}

// ========== 配置 API_BASE_URL ==========

/**
 * 在 HTML 中设置 API_BASE_URL（必须在加载 request.global.js 之前）：
 * ```html
 * <script>
 *   window.API_BASE_URL = window.API_BASE_URL || 'http://localhost:48911';
 *   window.STATIC_SERVER_URL = window.STATIC_SERVER_URL || window.API_BASE_URL;
 * </script>
 * <script type="module" src="/static/request.global.js"></script>
 * ```
 * 
 * 或者在 JS 中动态设置（需要在加载 request.global.js 之前）：
 */
function configureApiBaseUrl() {
  // 设置 API 基础 URL
  window.API_BASE_URL = 'http://localhost:48911';
  window.STATIC_SERVER_URL = window.API_BASE_URL;
  
  // 注意：如果 request.global.js 已经加载，需要重新初始化
  // 通常建议在 HTML 中设置，而不是在 JS 中动态设置
}

// ========== 检查全局对象是否可用 ==========

/**
 * 检查 request 相关全局对象是否已初始化
 */
function checkRequestAvailable() {
  if (window.request) {
    console.log('✓ window.request 可用');
  } else {
    console.warn('✗ window.request 不可用');
  }
  
  if (window.buildApiUrl) {
    console.log('✓ window.buildApiUrl 可用');
  } else {
    console.warn('✗ window.buildApiUrl 不可用');
  }
  
  if (window.buildStaticUrl) {
    console.log('✓ window.buildStaticUrl 可用');
  } else {
    console.warn('✗ window.buildStaticUrl 不可用');
  }
  
  if (window.buildWebSocketUrl) {
    console.log('✓ window.buildWebSocketUrl 可用');
  } else {
    console.warn('✗ window.buildWebSocketUrl 不可用');
  }
  
  if (window.API_BASE_URL) {
    console.log('✓ window.API_BASE_URL:', window.API_BASE_URL);
  } else {
    console.warn('✗ window.API_BASE_URL 不可用');
  }
}

