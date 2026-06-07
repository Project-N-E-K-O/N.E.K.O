const I18n = {
  _bundle: {},
  _lang: 'zh-CN',

  _ready: false,
  _whenReadyQueue: [],
  _initPromise: null,

  whenReady(callback) {
    if (this._ready) {
      callback(this._lang);
    } else {
      this._whenReadyQueue.push(callback);
    }
  },
  lang() {
    return this._lang;
  },

  _localeCandidates(locale) {
    const raw = String(locale || '').trim() || 'zh-CN';
    const lower = raw.toLowerCase().replace(/[ _]/g, '-');
    const candidates = [];
    const add = (value) => {
      if (value && !candidates.includes(value)) {
        candidates.push(value);
      }
    };

    // 精确匹配优先
    add(raw);

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

    // 兜底
    add('zh-CN');
    return candidates;
  },

  async init(pluginId) {
    if (this._initPromise) {
      return this._initPromise;
    }
    this._initPromise = this._init(pluginId);
    return this._initPromise;
  },

  async _init(pluginId) {
    const encodedPluginId = encodeURIComponent(pluginId || 'bilibili_danmaku');

    // 1. 优先检查 URL 参数 ?locale=
    const urlParams = new URLSearchParams(window.location.search);
    let localeFromUrl = urlParams.get('locale') || '';
    localeFromUrl = String(localeFromUrl).trim();

    // 2. 其次检查 localStorage（插件管理器写入了 'locale'）
    let localeFromStorage = '';
    try {
      localeFromStorage = String(localStorage.getItem('neko:locale') || '').trim();
    } catch {
      // 存储可能受限
    }

    // 3. 从 URL / localStorage / 后端 API 中选取第一个有效值
    let resolved = '';
    if (localeFromUrl && localeFromUrl !== 'auto') {
      resolved = localeFromUrl;
    } else if (localeFromStorage && localeFromStorage !== 'auto') {
      resolved = localeFromStorage;
    } else {
      try {
        const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/locale`, { cache: 'no-store' });
        if (resp.ok) {
          const data = await resp.json();
          resolved = data.locale || 'zh-CN';
        }
      } catch {
        resolved = 'zh-CN';
      }
    }
    this._lang = resolved || 'zh-CN';

    try {
      for (const locale of this._localeCandidates(this._lang)) {
        try {
          const resp = await fetch(`/plugin/${encodedPluginId}/ui-api/i18n/${encodeURIComponent(locale)}.json`, { cache: 'no-store' });
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
      this._ready = true;
      for (const cb of this._whenReadyQueue) {
        cb(this._lang);
      }
      this._whenReadyQueue = [];
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
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
  const pluginId = match ? match[1] : 'bilibili_danmaku';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  });
})();
