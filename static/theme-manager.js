/**
 * N.E.K.O. 主题管理器
 * 
 * 处理暗色模式的初始化、切换、持久化
 * 支持 Electron IPC 和普通浏览器两种环境
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'neko-dark-mode';

  /**
   * 应用主题到 DOM
   * @param {boolean} isDark - 是否为暗色模式
   */
  function applyTheme(isDark) {
    if (isDark) {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    localStorage.setItem(STORAGE_KEY, isDark ? 'true' : 'false');
    console.log('[ThemeManager] 主题已应用:', isDark ? 'dark' : 'light');
  }

  /**
   * 获取当前主题状态
   * @returns {boolean}
   */
  function isDarkMode() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  }

  /**
   * 切换主题
   */
  function toggleTheme() {
    const newState = !isDarkMode();
    applyTheme(newState);

    // 如果在 Electron 环境中，同步到主进程配置
    if (window.nekoDarkMode && typeof window.nekoDarkMode.set === 'function') {
      window.nekoDarkMode.set(newState).catch(err => {
        console.warn('[ThemeManager] 同步到主进程失败:', err);
      });
    }

    return newState;
  }

  /**
   * 初始化主题
   * 优先级: Electron IPC > localStorage > 默认亮色
   */
  async function initTheme() {
    let isDark = false;

    // 1. 尝试从 Electron IPC 获取主进程的配置
    if (window.nekoDarkMode && typeof window.nekoDarkMode.get === 'function') {
      try {
        isDark = await window.nekoDarkMode.get();
        console.log('[ThemeManager] 从 Electron IPC 获取主题设置:', isDark);
      } catch (err) {
        console.warn('[ThemeManager] 从 Electron IPC 获取失败，降级到 localStorage');
        isDark = localStorage.getItem(STORAGE_KEY) === 'true';
      }
    } else {
      // 2. 非 Electron 环境，从 localStorage 读取
      isDark = localStorage.getItem(STORAGE_KEY) === 'true';
      console.log('[ThemeManager] 从 localStorage 获取主题设置:', isDark);
    }

    applyTheme(isDark);
  }

  /**
   * 监听来自主进程的主题变更事件
   */
  function listenForThemeChanges() {
    window.addEventListener('neko-theme-changed', (event) => {
      if (event.detail && typeof event.detail.darkMode === 'boolean') {
        applyTheme(event.detail.darkMode);
      }
    });
  }

  // 暴露全局 API
  window.nekoTheme = {
    apply: applyTheme,
    isDark: isDarkMode,
    toggle: toggleTheme,
    init: initTheme
  };

  // DOM 准备好后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initTheme();
      listenForThemeChanges();
    });
  } else {
    // DOM 已就绪
    initTheme();
    listenForThemeChanges();
  }

  // 在最早的时机尝试恢复主题（避免闪烁）
  // 这会在 DOMContentLoaded 之前执行
  const savedTheme = localStorage.getItem(STORAGE_KEY);
  if (savedTheme === 'true') {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
