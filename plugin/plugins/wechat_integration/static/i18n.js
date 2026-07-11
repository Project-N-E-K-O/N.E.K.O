const I18n = {
  _bundle: {},
  _lang: 'zh-CN',
  _pluginId: 'wechat_integration',
  _ready: false,

  lang() {
    return this._lang;
  },

  whenReady(fn) {
    if (this._ready) {
      fn();
      return;
    }
    window.addEventListener('i18n-ready', () => fn(), { once: true });
  },

  _queryLocale() {
    try {
      return new URLSearchParams(location.search).get('locale') || '';
    } catch (err) {
      console.warn('Failed to read query locale', err);
      return '';
    }
  },

  _storageLocale() {
    try {
      const value = String(localStorage.getItem('locale') || '').trim();
      return value || '';
    } catch (err) {
      console.warn('Failed to read stored locale', err);
      return '';
    }
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
    if (lower === 'zh' || lower.startsWith('zh-')) {
      add('zh-CN');
    } else if (lower.startsWith('en')) {
      add('en-US');
      add('en');
    }
    add('zh-CN');
    return candidates;
  },

  async init(pluginId) {
    this._ready = false;
    this._pluginId = pluginId || this._pluginId;
    const encodedPluginId = encodeURIComponent(this._pluginId);
    const queryLocale = this._queryLocale();
    const storageLocale = this._storageLocale();
    let resolved = queryLocale || storageLocale || '';
    if (!resolved) {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/locale`, { cache: 'no-store' });
        if (resp.ok) {
          const data = await resp.json();
          resolved = String(data.locale || 'zh-CN');
        }
      } catch (err) {
        console.warn('Failed to resolve locale from ui-api', err);
      }
    }
    if (!resolved) {
      resolved = 'zh-CN';
    }

    for (const locale of this._localeCandidates(resolved)) {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/i18n/${encodeURIComponent(locale)}.json`, { cache: 'no-store' });
        if (resp.ok) {
          this._bundle = await resp.json();
          this._lang = locale;
          document.documentElement.lang = locale;
          this._ready = true;
          return;
        }
      } catch (err) {
        console.warn('Failed to load locale bundle', { locale, err });
      }
    }

    this._bundle = {};
    this._lang = 'zh-CN';
    document.documentElement.lang = 'zh-CN';
    this._ready = true;
  },

  async refresh() {
    await this.init(this._pluginId);
    this.scanDOM();
    window.dispatchEvent(new CustomEvent('wechat-integration-i18n-refreshed', { detail: { locale: this._lang } }));
  },

  t(key, fallback) {
    const value = this._bundle[String(key || '')];
    return typeof value === 'string' && value ? value : (fallback || key);
  },

  scanDOM(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (key) {
        el.textContent = this.t(key, el.textContent);
      }
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) {
        el.setAttribute('placeholder', this.t(key, el.getAttribute('placeholder') || ''));
      }
    });
  },
};

window.I18n = I18n;

(function bootstrapI18n() {
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
  const pluginId = match ? match[1] : 'wechat_integration';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  }).catch((err) => {
    console.error('[wechat_integration] i18n bootstrap failed', err);
  });
  window.addEventListener('localechange', async () => {
    try {
      await I18n.refresh();
    } catch (err) {
      console.error('[wechat_integration] i18n refresh failed', err);
    }
  });
})();
