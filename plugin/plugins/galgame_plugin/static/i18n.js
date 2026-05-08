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
    if (lower === 'zh' || lower.startsWith('zh-')) {
      add('zh-CN');
    } else if (lower.startsWith('en')) {
      add('en');
    } else if (lower.startsWith('ja')) {
      add('ja');
    } else if (lower.startsWith('ru')) {
      add('ru');
    } else if (lower.startsWith('ko')) {
      add('ko');
    }
    add(raw);
    add('zh-CN');
    return candidates;
  },

  async init(pluginId) {
    const encodedPluginId = encodeURIComponent(pluginId || 'galgame_plugin');
    try {
      const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/locale`, { cache: 'no-store' });
      if (resp.ok) {
        const data = await resp.json();
        this._lang = data.locale || 'zh-CN';
      }
    } catch {
      this._lang = 'zh-CN';
    }

    for (const locale of this._localeCandidates(this._lang)) {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/i18n/ui/${encodeURIComponent(locale)}.json`, { cache: 'no-store' });
        if (resp.ok) {
          this._bundle = await resp.json();
          this._lang = locale;
          return;
        }
      } catch {
        // Fallback below keeps the page usable.
      }
    }
    this._bundle = {};
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
  const pluginId = match ? match[1] : 'galgame_plugin';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  });
})();
