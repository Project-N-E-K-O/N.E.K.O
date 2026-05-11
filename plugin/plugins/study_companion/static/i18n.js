const I18n = {
  _bundle: {},
  _lang: 'zh-CN',

  lang() {
    return this._lang;
  },

  setLang(locale) {
    this._lang = String(locale || '').trim() || 'zh-CN';
    if (document.documentElement) {
      document.documentElement.lang = this._lang;
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
    if (lower === 'zh' || lower.startsWith('zh-')) {
      add('zh-CN');
      add('zh-TW');
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
    add('en');
    return candidates;
  },

  _browserLocale() {
    const languages = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language];
    return String(languages.find(Boolean) || 'zh-CN');
  },

  async _resolveLocale(pluginId) {
    try {
      const queryLocale = new URLSearchParams(location.search).get('locale');
      if (queryLocale) {
        return queryLocale;
      }
    } catch (error) {
      console.warn('[study_companion] locale query read failed', error);
    }
    try {
      const response = await fetch(`/plugin/${encodeURIComponent(pluginId)}/ui-api/locale`, { cache: 'no-store' });
      if (response.ok) {
        const data = await response.json();
        if (data.locale) {
          return data.locale;
        }
      }
    } catch (error) {
      console.warn('[study_companion] locale api failed', error);
    }
    return this._browserLocale();
  },

  async init(pluginId) {
    const locale = await this._resolveLocale(pluginId);
    this.setLang(locale);
    for (const candidate of this._localeCandidates(locale)) {
      try {
        const response = await fetch(`/plugin/${encodeURIComponent(pluginId)}/ui-api/i18n/${encodeURIComponent(candidate)}.json`, { cache: 'no-store' });
        if (response.ok) {
          this._bundle = await response.json();
          this.setLang(candidate);
          return;
        }
      } catch (error) {
        console.warn('[study_companion] locale bundle failed', candidate, error);
      }
    }
    this._bundle = {};
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
        el.textContent = this.t(key, el.textContent || '');
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
