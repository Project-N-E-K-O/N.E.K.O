const I18n = {
  _bundle: {},
  _lang: 'zh-CN',

  lang() {
    return this._lang;
  },

  _localeCandidates(locale) {
    const raw = String(locale || '').trim() || 'zh-CN';
    const lower = raw.toLowerCase().replace('_', '-');
    const candidates = [];
    const add = (value) => {
      if (value && !candidates.includes(value)) {
        candidates.push(value);
      }
    };

    add(raw);
    const primary = lower.split('-')[0];
    if (['en', 'ja', 'ko', 'ru', 'es', 'pt'].includes(primary)) add(primary);
    if (lower === 'zh' || lower.startsWith('zh-')) add('zh-CN');
    add('en');
    add('zh-CN');
    return candidates;
  },

  // Locale source priority:
  //   1. URL ?locale=xx  — plugin manager iframe builder appends this
  //      whenever the user switches language (see staticUiUrl.ts). This is
  //      the only source that tracks plugin-manager UI locale in real time.
  //   2. localStorage 'locale' — set by plugin manager's LanguageSwitcher
  //      so direct iframe loads (no ?locale= in URL) still pick the user's
  //      last choice within the same origin.
  //   3. /ui-api/locale — backend global language (Steam/system); only
  //      meaningful when neither URL nor storage has a value.
  // Each step is best-effort: failures fall through to the next.
  _queryLocale() {
    try {
      return new URLSearchParams(location.search).get('locale') || '';
    } catch {
      return '';
    }
  },

  _storageLocale() {
    try {
      const raw = String(localStorage.getItem('locale') || '').trim();
      // 'auto' is the plugin-manager sentinel meaning "follow the browser";
      // we can't replicate that resolution cheaply in the iframe, so let it
      // fall through to the backend endpoint instead of guessing here.
      return raw && raw !== 'auto' ? raw : '';
    } catch {
      return '';
    }
  },

  async init(pluginId) {
    const encodedPluginId = encodeURIComponent(pluginId || 'bilibili_danmaku');

    const queryLocale = this._queryLocale();
    const storageLocale = this._storageLocale();
    if (queryLocale) {
      this._lang = queryLocale;
    } else if (storageLocale) {
      this._lang = storageLocale;
    } else {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/locale`);
        if (resp.ok) {
          const data = await resp.json();
          this._lang = data.locale || 'zh-CN';
        }
      } catch {
        this._lang = 'zh-CN';
      }
    }

    for (const locale of this._localeCandidates(this._lang)) {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/i18n/${encodeURIComponent(locale)}.json`);
        if (resp.ok) {
          this._bundle = await resp.json();
          this._lang = locale;
          return;
        }
      } catch {
        // fallback keeps page usable
      }
    }
    this._bundle = {};
  },

  t(key, fallback) {
    const value = this._bundle[String(key || '')];
    return typeof value === 'string' && value ? value : (fallback || key);
  },

  scanDOM(root) {
    root = root || document;
    root.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (key) {
        el.textContent = this.t(key, el.textContent);
      }
    });
    root.querySelectorAll('[data-i18n-title]').forEach((el) => {
      const key = el.getAttribute('data-i18n-title');
      if (key) {
        el.setAttribute('title', this.t(key, el.getAttribute('title') || ''));
      }
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) {
        el.setAttribute('placeholder', this.t(key, el.getAttribute('placeholder') || ''));
      }
    });
    root.querySelectorAll('[data-i18n-aria-label]').forEach((el) => {
      const key = el.getAttribute('data-i18n-aria-label');
      if (key) {
        el.setAttribute('aria-label', this.t(key, el.getAttribute('aria-label') || ''));
      }
    });
  },
};

window.I18n = I18n;

(function bootstrapI18n() {
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
  const pluginId = match ? match[1] : 'bilibili_danmaku';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  });
})();
