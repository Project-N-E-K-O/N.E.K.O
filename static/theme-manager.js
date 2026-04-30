(function () {
  'use strict';

  const STORAGE_KEY = 'neko-dark-mode';
  const TRANSITION_MS = 300;
  let themeTransitionTimeout = null;
  let initialized = false;

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
      if (event.detail && typeof event.detail.darkMode === 'boolean') {
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
