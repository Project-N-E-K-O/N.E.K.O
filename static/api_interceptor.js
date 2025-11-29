/**
 * API Fetch 和 WebSocket 拦截器
 * 自动为所有以 /api/ 或 /ws/ 开头的请求添加 API_BASE_URL
 * 此文件需要在其他 JS 文件加载之前加载
 */

(function() {
  'use strict';
  
  // 从 window.API_BASE_URL / window.STATIC_SERVER_URL 获取配置，如果没有则使用默认值
  const API_BASE_URL = window.API_BASE_URL || 'http://localhost:48911';
  const STATIC_SERVER_URL = window.STATIC_SERVER_URL || API_BASE_URL;
  
  // 保存原始的 fetch 方法
  const originalFetch = window.fetch;
  
  // 保存原始的 WebSocket 构造函数
  const OriginalWebSocket = window.WebSocket;
  
  /**
   * 判断一个 URL 属于哪一类，需要使用哪个 base URL
   * @param {string} url - 请求的 URL
   * @returns {'api' | 'ws' | 'static' | null}
   */
  function getRequestCategory(url) {
    // 先处理相对路径
    if (url.startsWith('/api/')) return 'api';
    if (url.startsWith('/ws/')) return 'ws';
    if (url.startsWith('/static/')) return 'static';

    // 如果是完整 URL，检查是否是当前页面的 host（需要替换）
    if (
      url.startsWith('http://') || url.startsWith('https://') ||
      url.startsWith('ws://') || url.startsWith('wss://')
    ) {
      try {
        const urlObj = new URL(url);
        const currentHost = window.location.host;
        if (urlObj.host === currentHost) {
          if (urlObj.pathname.startsWith('/api/')) return 'api';
          if (urlObj.pathname.startsWith('/ws/')) return 'ws';
          if (urlObj.pathname.startsWith('/static/')) return 'static';
        }
      } catch (e) {
        // URL 解析失败，不处理
      }
    }

    return null;
  }
  
  /**
   * 构建完整的 API URL
   * @param {string} path - API 路径（可能是相对路径如 /api/... 或完整 URL）
   * @returns {string} 完整的 URL
   */
  function buildApiUrl(path) {
    // 如果已经是完整 URL，提取路径部分
    let cleanPath = path;
    try {
      const urlObj = new URL(path);
      cleanPath = urlObj.pathname;
    } catch (e) {
      // 不是完整 URL，使用原路径
    }
    
    // 确保路径以 / 开头
    if (!cleanPath.startsWith('/')) {
      cleanPath = '/' + cleanPath;
    }
    
    const base = API_BASE_URL.replace(/\/$/, '');
    return base + cleanPath;
  }
  
  /**
   * 构建完整的静态资源 URL
   * @param {string} path - 静态资源路径（可能是相对路径如 /static/... 或完整 URL）
   * @returns {string} 完整的 URL
   */
  function buildStaticUrl(path) {
    // 如果已经是完整 URL，提取路径部分
    let cleanPath = path;
    try {
      const urlObj = new URL(path);
      cleanPath = urlObj.pathname;
    } catch (e) {
      // 不是完整 URL，使用原路径
    }

    // 确保路径以 / 开头
    if (!cleanPath.startsWith('/')) {
      cleanPath = '/' + cleanPath;
    }

    const base = STATIC_SERVER_URL.replace(/\/$/, '');
    return base + cleanPath;
  }
  
  /**
   * 将 HTTP URL 转换为 WebSocket URL
   * @param {string} httpUrl - HTTP URL
   * @returns {string} WebSocket URL
   */
  function httpToWebSocketUrl(httpUrl) {
    // 将 http:// 替换为 ws://，https:// 替换为 wss://
    return httpUrl.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
  }
  
  /**
   * 拦截的 fetch 方法
   */
  window.fetch = function(input, init) {
    let url;
    
    // 处理不同类型的 input
    if (typeof input === 'string') {
      url = input;
    } else if (input instanceof URL) {
      url = input.toString();
    } else if (input instanceof Request) {
      url = input.url;
    } else {
      // 未知类型，直接使用原始 fetch
      return originalFetch.apply(this, arguments);
    }
    
    // 判断属于哪一类请求
    const category = getRequestCategory(url);
    if (category) {
      const fullUrl =
        category === 'static'
          ? buildStaticUrl(url)
          : buildApiUrl(url);
      
      // 如果是字符串，直接替换
      if (typeof input === 'string') {
        console.log(`[API Interceptor] fetch ${url} -> ${fullUrl}`);
        return originalFetch.call(this, fullUrl, init);
      }
      
      // 如果是 URL 对象，创建新的 URL
      if (input instanceof URL) {
        const newUrlObj = new URL(fullUrl);
        console.log(`[API Interceptor] fetch ${url} -> ${fullUrl}`);
        return originalFetch.call(this, newUrlObj, init);
      }
      
      // 如果是 Request 对象，创建新的 Request
      if (input instanceof Request) {
        const newRequest = new Request(fullUrl, input);
        console.log(`[API Interceptor] fetch ${url} -> ${fullUrl}`);
        return originalFetch.call(this, newRequest, init);
      }
    }
    
    // 不需要处理，使用原始 fetch
    return originalFetch.apply(this, arguments);
  };
  
  // 保持 fetch 的原始属性（如 fetch.length）
  Object.setPrototypeOf(window.fetch, originalFetch);
  Object.defineProperty(window.fetch, 'name', { value: 'fetch', configurable: true });
  
  /**
   * 拦截的 WebSocket 构造函数
   */
  window.WebSocket = function(url, protocols) {
    let wsUrl = url;
    
    // 判断是否需要添加 base URL（只处理 /api/ 和 /ws/，不处理 /static/）
    const category = getRequestCategory(wsUrl);
    if (category === 'api' || category === 'ws') {
      const httpUrl = buildApiUrl(wsUrl);
      wsUrl = httpToWebSocketUrl(httpUrl);
      console.log(`[API Interceptor] WebSocket ${url} -> ${wsUrl}`);
    }
    
    // 调用原始的 WebSocket 构造函数
    // 注意：WebSocket 构造函数可以接受 protocols 作为第二个参数（字符串或字符串数组）
    if (protocols !== undefined) {
      return new OriginalWebSocket(wsUrl, protocols);
    } else {
      return new OriginalWebSocket(wsUrl);
    }
  };
  
  // 保持 WebSocket 的原始属性和原型链
  // 通过设置原型链，静态属性（如 CONNECTING、OPEN 等）可以通过原型链访问
  Object.setPrototypeOf(window.WebSocket, OriginalWebSocket);
  
  // 设置函数名称
  try {
    Object.defineProperty(window.WebSocket, 'name', { value: 'WebSocket', configurable: true });
  } catch (e) {
    // 如果无法设置 name 属性，忽略（某些环境可能不支持）
  }
  
  // 复制 WebSocket 的静态属性（如 WebSocket.CONNECTING, WebSocket.OPEN 等）
  // 由于原型链已设置，静态属性应该可以通过原型链访问
  // 但为了兼容性，我们尝试复制它们（如果可能）
  Object.getOwnPropertyNames(OriginalWebSocket).forEach(key => {
    if (typeof OriginalWebSocket[key] !== 'function' && key !== 'name' && key !== 'length' && key !== 'prototype') {
      try {
        const descriptor = Object.getOwnPropertyDescriptor(OriginalWebSocket, key);
        if (descriptor && !descriptor.configurable) {
          // 如果属性不可配置（只读），跳过复制，依赖原型链访问
          // 这避免了 "Cannot assign to read only property" 错误
          return;
        }
        
        if (descriptor) {
          // 尝试复制属性，设置为可配置以便后续修改
          Object.defineProperty(window.WebSocket, key, {
            value: descriptor.value,
            writable: true,
            enumerable: descriptor.enumerable !== false,
            configurable: true
          });
        }
      } catch (e) {
        // 如果无法复制属性，依赖原型链访问（静默忽略）
        // 由于已经设置了原型链，这些属性仍然可以通过 window.WebSocket.CONNECTING 等访问
      }
    }
  });

  /**
   * 拦截 XMLHttpRequest，使 /api/、/ws/、/static/ 也走对应的 BASE_URL
   */
  const OriginalXMLHttpRequest = window.XMLHttpRequest;
  if (OriginalXMLHttpRequest && OriginalXMLHttpRequest.prototype) {
    const originalOpen = OriginalXMLHttpRequest.prototype.open;

    OriginalXMLHttpRequest.prototype.open = function() {
      const args = Array.prototype.slice.call(arguments);
      try {
        const urlIndex = 1; // open(method, url, async?, user?, password?)
        const rawUrl = String(args[urlIndex]);
        const category = getRequestCategory(rawUrl);
        if (category) {
          const fullUrl =
            category === 'static'
              ? buildStaticUrl(rawUrl)
              : buildApiUrl(rawUrl);
          console.log(`[API Interceptor] XHR ${args[0]} ${rawUrl} -> ${fullUrl}`);
          args[urlIndex] = fullUrl;
        }
      } catch (e) {
        // 拦截异常时，保持原始行为
      }
      // 使用 apply 保持参数个数与调用方式，避免影响同步/异步等行为
      return originalOpen.apply(this, args);
    };
  }
  
  console.log(
    '[API Interceptor] Fetch / XHR / WebSocket 拦截器已初始化, API_BASE_URL:',
    API_BASE_URL,
    'STATIC_SERVER_URL:',
    STATIC_SERVER_URL
  );
})();

