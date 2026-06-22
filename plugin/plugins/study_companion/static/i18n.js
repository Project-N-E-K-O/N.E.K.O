const I18n = {
  _bundle: {},
  _lang: 'zh-CN',

  lang() {
    return this._lang;
  },

  _syncDocumentLang() {
    if (document?.documentElement) {
      document.documentElement.lang = this._lang || 'zh-CN';
    }
  },

  setLang(locale) {
    this._lang = String(locale || '').trim() || 'zh-CN';
    this._syncDocumentLang();
    if (typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent('i18n-lang-changed', { detail: { locale: this._lang } }));
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
    if (lower.startsWith('zh-hant') || lower.startsWith('zh-hk') || lower.startsWith('zh-tw')) {
      add('zh-TW');
    } else if (lower === 'zh' || lower.startsWith('zh-')) {
      add('zh-CN');
    } else if (lower.startsWith('en')) {
      add('en');
    } else if (lower.startsWith('ja')) {
      add('ja');
    } else if (lower.startsWith('ko')) {
      add('ko');
    } else if (lower.startsWith('ru')) {
      add('ru');
    } else if (lower.startsWith('es')) {
      add('es');
    } else if (lower.startsWith('pt')) {
      add('pt');
    }
    add(raw);
    add('zh-CN');
    add('zh-TW');
    return candidates;
  },

  _queryLocale() {
    try {
      return new URLSearchParams(location.search).get('locale') || '';
    } catch (err) {
      return '';
    }
  },

  _browserLocale() {
    const languages = (navigator.languages && navigator.languages.length)
      ? navigator.languages
      : [navigator.language];
    for (const lang of languages) {
      const raw = String(lang || '').trim();
      const lower = raw.toLowerCase().replace('_', '-');
      if (!lower) continue;
      if (lower.startsWith('zh-hant') || lower.startsWith('zh-hk') || lower.startsWith('zh-tw')) return 'zh-TW';
      if (lower === 'zh' || lower.startsWith('zh-')) return 'zh-CN';
      if (lower.startsWith('en')) return 'en';
      if (lower.startsWith('ja')) return 'ja';
      if (lower.startsWith('ko')) return 'ko';
      if (lower.startsWith('ru')) return 'ru';
      if (lower.startsWith('es')) return 'es';
      if (lower.startsWith('pt')) return 'pt';
    }
    return 'zh-CN';
  },

  _storageLocale() {
    try {
      const value = String(localStorage.getItem('locale') || '').trim();
      if (!value) return '';
      return value === 'auto' ? this._browserLocale() : value;
    } catch (err) {
      return '';
    }
  },

  async init(pluginId) {
    const encodedPluginId = encodeURIComponent(pluginId || 'study_companion');
    const queryLocale = this._queryLocale();
    const storageLocale = this._storageLocale();
    const resolvedLocale = queryLocale || storageLocale || this._browserLocale();
    this.setLang(resolvedLocale);

    for (const locale of this._localeCandidates(this._lang)) {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/i18n/${encodeURIComponent(locale)}.json`, { cache: 'no-store' });
        if (resp.ok) {
          this._bundle = await resp.json();
          this.setLang(locale);
          return;
        }
      } catch (err) {
        // Fallback below keeps the page usable.
      }
    }
    this._bundle = {};
    this._syncDocumentLang();
  },

  t(key, fallback) {
    const value = this._bundle[String(key || '')];
    return typeof value === 'string' && value ? value : (fallback || key);
  },

  tf(key, fallback, values = {}) {
    return this.t(key, fallback).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
    ));
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
    root.querySelectorAll('[data-i18n-alt]').forEach((el) => {
      const key = el.getAttribute('data-i18n-alt');
      if (key) {
        el.setAttribute('alt', this.t(key, el.getAttribute('alt') || ''));
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
