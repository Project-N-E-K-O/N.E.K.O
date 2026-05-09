const I18n = {
  _bundle: {},
  _lang: 'zh-CN',
  // True once init() has resolved (regardless of fetch success). Pages must
  // gate their first dynamic render on this — `_lang` is initialized to
  // 'zh-CN' synchronously at script-load time, so checking only `_lang`
  // races: it's truthy before the bundle fetch finishes and would let
  // initial calls render Chinese fallbacks under non-default locales.
  _ready: false,

  lang() {
    return this._lang;
  },

  ready() {
    return this._ready;
  },

  whenReady(fn) {
    if (typeof fn !== 'function') return;
    if (this._ready) {
      fn();
    } else {
      window.addEventListener('i18n-ready', () => fn(), { once: true });
    }
  },

  _localeCandidates(locale) {
    const raw = String(locale || '').trim() || 'zh-CN';
    const lower = raw.toLowerCase().replace(/_/g, '-');
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
    // Empty pluginId means the bootstrap regex couldn't extract one from the
    // current URL; bail out instead of fetching another plugin's bundles.
    // The page stays usable because t() returns each element's existing
    // textContent as fallback.
    const cleanPluginId = String(pluginId || '').trim();
    if (!cleanPluginId) {
      this._bundle = {};
      this._ready = true;
      return;
    }
    const encodedPluginId = encodeURIComponent(cleanPluginId);

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

    try {
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
    } finally {
      // Always flip ready, even if every candidate fetch failed — pages still
      // need to render with their data-i18n fallbacks rather than hang.
      this._ready = true;
    }
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
  // Backend registers both `/plugin/{id}/ui` and `/plugin/{id}/ui/`, so the
  // regex must accept either a trailing slash or end-of-path. If pathname
  // somehow doesn't match (e.g. opened from an unexpected route), don't
  // hard-code another plugin's id — leave it empty so I18n.init falls back
  // to the encoded empty string and silently skips bundle fetches instead
  // of pulling translations from the wrong plugin.
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui(?:\/|$)/);
  const pluginId = match ? match[1] : '';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  });
})();
