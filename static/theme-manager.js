(function () {
  'use strict';

  const STORAGE_KEY = 'neko-dark-mode';
  const TRANSITION_MS = 300;
  const THEME_EVENT_ORIGIN = 'theme-manager';
  let themeTransitionTimeout = null;
  let initialized = false;

  // 聊天框与悬浮菜单共用同一套 0–100 透明度语义：50 保持原视觉，
  // 100 将主表面推至不透明。放在主题管理器中同步恢复，避免首帧闪烁。
  const DEFAULT_UI_OPACITY = 50;
  const UI_OPACITY_BASELINE = 50;
  const UI_OPACITY_SETTINGS = Object.freeze({
    chat: Object.freeze({
      storageKey: 'nekoChatOpacity',
      cssVariable: '--neko-chat-opacity-factor'
    }),
    floatingMenu: Object.freeze({
      storageKey: 'nekoFloatingMenuOpacity',
      cssVariable: '--neko-floating-menu-opacity-factor'
    })
  });

  function normalizeUiOpacity(value) {
    if (value === null || (typeof value === 'string' && value.trim() === '')) {
      return DEFAULT_UI_OPACITY;
    }
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return DEFAULT_UI_OPACITY;
    return Math.min(100, Math.max(0, Math.round(numericValue)));
  }

  function readStoredUiOpacity(setting) {
    try {
      const storedValue = localStorage.getItem(setting.storageKey);
      return storedValue === null ? DEFAULT_UI_OPACITY : normalizeUiOpacity(storedValue);
    } catch (_) {
      return DEFAULT_UI_OPACITY;
    }
  }

  function applyUiOpacity(name, value) {
    const setting = UI_OPACITY_SETTINGS[name];
    if (!setting || !document || !document.documentElement) return;
    const normalizedValue = normalizeUiOpacity(value);
    document.documentElement.style.setProperty(
      setting.cssVariable,
      String(normalizedValue / UI_OPACITY_BASELINE)
    );
  }

  function getUiOpacity(name) {
    const setting = UI_OPACITY_SETTINGS[name];
    return setting ? readStoredUiOpacity(setting) : DEFAULT_UI_OPACITY;
  }

  function applyRuntimeUiOpacity(name, value) {
    if (!UI_OPACITY_SETTINGS[name]) return DEFAULT_UI_OPACITY;
    const normalizedValue = normalizeUiOpacity(value);
    applyUiOpacity(name, normalizedValue);
    return normalizedValue;
  }

  function syncUiOpacity(name, value) {
    const setting = UI_OPACITY_SETTINGS[name];
    if (!setting) return DEFAULT_UI_OPACITY;
    const normalizedValue = normalizeUiOpacity(value);
    try {
      localStorage.setItem(setting.storageKey, String(normalizedValue));
    } catch (_) {}
    applyUiOpacity(name, normalizedValue);
    return normalizedValue;
  }

  function setUiOpacity(name, value) {
    const normalizedValue = syncUiOpacity(name, value);
    if (!UI_OPACITY_SETTINGS[name]) return normalizedValue;
    window.dispatchEvent(new CustomEvent('neko-ui-opacity-changed', {
      detail: { name, value: normalizedValue }
    }));
    return normalizedValue;
  }

  function refreshUiOpacity() {
    Object.keys(UI_OPACITY_SETTINGS).forEach((name) => applyUiOpacity(name, getUiOpacity(name)));
  }

  window.addEventListener('storage', (event) => {
    if (!event || event.storageArea !== localStorage) return;
    const matchedName = Object.keys(UI_OPACITY_SETTINGS).find(
      (name) => UI_OPACITY_SETTINGS[name].storageKey === event.key
    );
    if (matchedName) {
      applyUiOpacity(matchedName, event.newValue === null ? DEFAULT_UI_OPACITY : event.newValue);
    } else if (event.key === null) {
      refreshUiOpacity();
    }
  });

  window.NekoUiOpacityPreferences = Object.freeze({
    get: getUiOpacity,
    set: setUiOpacity,
    apply: applyRuntimeUiOpacity,
    sync: syncUiOpacity,
    refresh: refreshUiOpacity,
    normalize: normalizeUiOpacity
  });
  refreshUiOpacity();

  try {
    const savedTheme = localStorage.getItem(STORAGE_KEY);
    if (savedTheme === 'true') {
      document.documentElement.setAttribute('data-theme', 'dark');
      document.documentElement.classList.add('dark');
    }
  } catch (_) {}

  function getSystemPrefersDark() {
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function applyTheme(isDark, options = {}) {
    if (isDark) {
      document.documentElement.setAttribute('data-theme', 'dark');
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
      document.documentElement.classList.remove('dark');
    }

    if (options.persist !== false) {
      try {
        localStorage.setItem(STORAGE_KEY, isDark ? 'true' : 'false');
      } catch (error) {
        console.warn('[ThemeManager] localStorage write failed:', error);
      }
    }
    console.debug('[ThemeManager] theme applied:', isDark ? 'dark' : 'light');
  }

  function applyThemeAnimated(isDark, options = {}) {
    document.documentElement.classList.add('theme-transitioning');
    applyTheme(isDark, options);

    if (themeTransitionTimeout !== null) {
      clearTimeout(themeTransitionTimeout);
    }

    themeTransitionTimeout = setTimeout(() => {
      document.documentElement.classList.remove('theme-transitioning');
      themeTransitionTimeout = null;
    }, TRANSITION_MS);
  }

  function isDarkMode() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  }

  function toggleTheme() {
    const newState = !isDarkMode();
    applyThemeAnimated(newState);

    if (window.nekoDarkMode && typeof window.nekoDarkMode.set === 'function') {
      window.nekoDarkMode.set(newState).catch((error) => {
        console.warn('[ThemeManager] Electron theme sync failed:', error);
      });
    }

    window.dispatchEvent(new CustomEvent('neko-theme-changed', {
      detail: { darkMode: newState, origin: THEME_EVENT_ORIGIN }
    }));

    return newState;
  }

  async function initTheme() {
    let isDark = false;
    let shouldPersist = true;

    if (window.nekoDarkMode && typeof window.nekoDarkMode.get === 'function') {
      try {
        isDark = await window.nekoDarkMode.get();
        console.debug('[ThemeManager] loaded theme from Electron:', isDark);
      } catch (_) {
        try {
          const stored = localStorage.getItem(STORAGE_KEY);
          isDark = stored !== null ? stored === 'true' : getSystemPrefersDark();
          shouldPersist = stored !== null;
        } catch (_) {
          isDark = getSystemPrefersDark();
          shouldPersist = false;
        }
      }
    } else {
      try {
        const stored = localStorage.getItem(STORAGE_KEY);
        isDark = stored !== null ? stored === 'true' : getSystemPrefersDark();
        shouldPersist = stored !== null;
      } catch (_) {
        isDark = getSystemPrefersDark();
        shouldPersist = false;
      }
    }

    applyTheme(isDark, { persist: shouldPersist });
  }

  function listenForThemeChanges() {
    window.addEventListener('neko-theme-changed', (event) => {
      if (event.detail && event.detail.origin === THEME_EVENT_ORIGIN) {
        return;
      }
      if (event.detail && typeof event.detail.darkMode === 'boolean') {
        if (event.detail.darkMode === isDarkMode()) {
          return;
        }
        applyThemeAnimated(event.detail.darkMode);
      }
    });

    window.addEventListener('storage', (event) => {
      if (event.key !== STORAGE_KEY) {
        return;
      }
      if (event.newValue === null || event.newValue === '') {
        applyThemeAnimated(getSystemPrefersDark(), { persist: false });
        return;
      }
      applyThemeAnimated(event.newValue === 'true', { persist: false });
    });

    if (window.matchMedia) {
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      media.addEventListener('change', (event) => {
        try {
          if (localStorage.getItem(STORAGE_KEY) === null) {
            applyThemeAnimated(event.matches, { persist: false });
          }
        } catch (_) {
          applyThemeAnimated(event.matches, { persist: false });
        }
      });
    }
  }

  async function fullInit() {
    if (initialized) {
      return;
    }
    initialized = true;
    await initTheme();
    listenForThemeChanges();
  }

  window.nekoTheme = {
    apply: applyTheme,
    applyAnimated: applyThemeAnimated,
    isDark: isDarkMode,
    toggle: toggleTheme,
    init: fullInit,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      fullInit().catch((error) => {
        console.error('[ThemeManager] init failed:', error);
      });
    });
  } else {
    fullInit().catch((error) => {
      console.error('[ThemeManager] init failed:', error);
    });
  }
})();
