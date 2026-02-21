/**
 * N.E.K.O. 主题管理器
 * 
 * 处理暗色模式的初始化、切换、持久化
 * 支持 Electron IPC 和普通浏览器两种环境
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'neko-dark-mode';

  // 在最早的时机尝试恢复主题（避免白屏闪烁）
  // 这会在任何函数定义或 DOMContentLoaded 之前执行
  try {
    const savedTheme = localStorage.getItem(STORAGE_KEY);
    if (savedTheme === 'true') {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
  } catch (e) {
    // localStorage 不可用时静默忽略（如隐私模式）
  }

  /**
   * 读取系统暗色模式偏好
   * @returns {boolean}
   */
  function getSystemPrefersDark() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

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
    try {
      localStorage.setItem(STORAGE_KEY, isDark ? 'true' : 'false');
    } catch (e) {
      console.warn('[ThemeManager] localStorage 写入失败:', e);
    }
    console.debug('[ThemeManager] 主题已应用:', isDark ? 'dark' : 'light');
  }

  let themeTransitionTimeout = null;
  const TRANSITION_MS = 300;

  /**
   * 带动画过渡地应用主题（用于主题切换，非初始加载）
   * @param {boolean} isDark
   */
  function applyThemeAnimated(isDark) {
    document.documentElement.classList.add('theme-transitioning');
    applyTheme(isDark);

    if (themeTransitionTimeout !== null) {
      clearTimeout(themeTransitionTimeout);
    }

    themeTransitionTimeout = setTimeout(() => {
      document.documentElement.classList.remove('theme-transitioning');
      themeTransitionTimeout = null;
    }, TRANSITION_MS);
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
    applyThemeAnimated(newState);

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
        console.debug('[ThemeManager] 从 Electron IPC 获取主题设置:', isDark);
      } catch (err) {
        console.warn('[ThemeManager] 从 Electron IPC 获取失败，降级到 localStorage');
        try {
          const stored = localStorage.getItem(STORAGE_KEY);
          isDark = stored !== null ? stored === 'true' : getSystemPrefersDark();
        } catch (e) {
          isDark = getSystemPrefersDark();
        }
      }
    } else {
      // 2. 非 Electron 环境，从 localStorage 读取，无存储值时回退到系统偏好
      try {
        const stored = localStorage.getItem(STORAGE_KEY);
        isDark = stored !== null ? stored === 'true' : getSystemPrefersDark();
      } catch (e) {
        isDark = getSystemPrefersDark();
      }
      console.debug('[ThemeManager] 从 localStorage/系统偏好 获取主题设置:', isDark);
    }

    applyTheme(isDark);
  }

  /**
   * 监听来自主进程和系统的主题变更事件
   */
  function listenForThemeChanges() {
    // 监听来自 Electron 主进程的自定义事件
    window.addEventListener('neko-theme-changed', (event) => {
      if (event.detail && typeof event.detail.darkMode === 'boolean') {
        applyThemeAnimated(event.detail.darkMode);
      }
    });

    // 监听系统 prefers-color-scheme 变化
    // 仅在用户未手动设置主题时响应系统变化
    if (window.matchMedia) {
      const mql = window.matchMedia('(prefers-color-scheme: dark)');
      mql.addEventListener('change', (e) => {
        try {
          if (localStorage.getItem(STORAGE_KEY) === null) {
            applyThemeAnimated(e.matches);
          }
        } catch (err) {
          // localStorage 不可用时，跟随系统变化
          applyThemeAnimated(e.matches);
        }
      });
    }
  }

  /**
   * 完整初始化：应用主题 + 注册事件监听
   * @returns {Promise<void>}
   */
  async function fullInit() {
    await initTheme();
    listenForThemeChanges();
  }

  // 暴露全局 API
  window.nekoTheme = {
    apply: applyTheme,
    applyAnimated: applyThemeAnimated,
    isDark: isDarkMode,
    toggle: toggleTheme,
    init: fullInit
  };

  // DOM 准备好后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      fullInit().catch(err => {
        console.error('[ThemeManager] 初始化失败:', err);
      });
    });
  } else {
    // DOM 已就绪
    fullInit().catch(err => {
      console.error('[ThemeManager] 初始化失败:', err);
    });
  }
})();
