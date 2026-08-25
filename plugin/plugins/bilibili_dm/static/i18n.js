const I18n = {
  _bundle: {},
  _fallbackBundle: {},
  _lang: 'zh-CN',
  _pluginId: 'bilibili_dm',
  _ready: false,

  whenReady(fn) {
    if (this._ready) fn();
    else window.addEventListener('i18n-ready', fn, { once: true });
  },

  _browserLocale() {
    try {
      const locales = [
        ...(Array.isArray(navigator.languages) ? navigator.languages : []),
        navigator.language,
      ].map((locale) => String(locale || '').trim()).filter(Boolean);
      const supported = locales.find((locale) => {
        const primary = locale.toLowerCase().replace(/_/g, '-').split('-')[0];
        return ['zh', 'en', 'ja', 'ko', 'ru', 'es', 'pt'].includes(primary);
      });
      return supported || locales[0] || '';
    } catch (_) {
      return '';
    }
  },

  async init(pluginId) {
    this._pluginId = pluginId || this._pluginId;
    let locale = 'zh-CN';
    try {
      const queryLocale = new URLSearchParams(location.search).get('locale') || '';
      let storedLocale = '';
      if (!queryLocale) {
        try {
          storedLocale = localStorage.getItem('locale') || '';
        } catch (_) { /* explicit query locale remains authoritative */ }
      }
      // `auto` means follow the browser/UI language.  The backend endpoint
      // resolves a separate Steam/system language, which can be English even
      // while the plugin manager is correctly rendered in Simplified Chinese.
      const automatic = queryLocale === 'auto'
        || (!queryLocale && storedLocale === 'auto');
      locale = automatic
        ? this._browserLocale()
        : (queryLocale || storedLocale);
      if (!locale) {
        const response = await fetch(`/plugin/${encodeURIComponent(this._pluginId)}/ui-api/locale`, { cache: 'no-store' });
        if (response.ok) locale = String((await response.json()).locale || 'zh-CN');
      }
    } catch (_) {
      locale = 'zh-CN';
    }
    const normalized = String(locale || '').trim().replace(/_/g, '-').toLowerCase();
    const primary = normalized.split('-')[0];
    const isChinese = primary === 'zh';
    const isTraditionalChinese = isChinese && (
      normalized.includes('hant')
      || /(?:^|-)(tw|hk|mo)(?:-|$)/.test(normalized)
    );
    const candidates = [
      locale,
      ['en', 'ja', 'ko', 'ru', 'es', 'pt'].includes(primary) ? primary : '',
      isTraditionalChinese ? 'zh-TW' : '',
      isChinese ? 'zh-CN' : '',
      'en',
      'zh-CN',
    ].filter(Boolean);
    for (const candidate of [...new Set(candidates)]) {
      try {
        const response = await fetch(`/plugin/${encodeURIComponent(this._pluginId)}/ui-api/i18n/${encodeURIComponent(candidate)}.json`, { cache: 'no-store' });
        if (response.ok) {
          this._bundle = await response.json();
          this._lang = candidate;
          break;
        }
      } catch (_) { /* use fallback text */ }
    }
    // English is the shared per-key fallback.  It prevents a newly added UI
    // key from leaking its Chinese source text into a partially translated
    // locale while keeping the selected locale as the first choice.
    if (this._lang !== 'en') {
      try {
        const response = await fetch(`/plugin/${encodeURIComponent(this._pluginId)}/ui-api/i18n/en.json`, { cache: 'no-store' });
        if (response.ok) this._fallbackBundle = await response.json();
      } catch (_) { /* source-text fallback remains available */ }
    }
    this._ready = true;
  },

  t(key, fallback, params = {}) {
    const lookupKey = String(key || '');
    const value = this._bundle[lookupKey] || this._fallbackBundle[lookupKey] || fallback || key;
    return String(value).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
    ));
  },

  scanDOM(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((element) => {
      const key = element.getAttribute('data-i18n');
      if (key) {
        const params = {};
        const count = element.getAttribute('data-i18n-count');
        if (count !== null) params.count = count;
        element.textContent = this.t(key, element.textContent || '', params);
      }
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
      const key = element.getAttribute('data-i18n-placeholder');
      if (key) element.setAttribute('placeholder', this.t(key, element.getAttribute('placeholder') || ''));
    });
    root.querySelectorAll('[data-i18n-alt]').forEach((element) => {
      const key = element.getAttribute('data-i18n-alt');
      if (key) element.setAttribute('alt', this.t(key, element.getAttribute('alt') || ''));
    });
  },
};

window.I18n = I18n;

(async function bootstrapI18n() {
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
  await I18n.init(match ? match[1] : 'bilibili_dm');
  I18n.scanDOM();
  window.dispatchEvent(new CustomEvent('i18n-ready'));
})();
