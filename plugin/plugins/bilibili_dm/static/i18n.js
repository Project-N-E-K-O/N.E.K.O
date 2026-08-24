const I18n = {
  _bundle: {},
  _lang: 'zh-CN',
  _pluginId: 'bilibili_dm',
  _ready: false,

  whenReady(fn) {
    if (this._ready) fn();
    else window.addEventListener('i18n-ready', fn, { once: true });
  },

  async init(pluginId) {
    this._pluginId = pluginId || this._pluginId;
    let locale = 'zh-CN';
    try {
      locale = new URLSearchParams(location.search).get('locale') || localStorage.getItem('locale') || '';
      if (!locale) {
        const response = await fetch(`/plugin/${encodeURIComponent(this._pluginId)}/ui-api/locale`, { cache: 'no-store' });
        if (response.ok) locale = String((await response.json()).locale || 'zh-CN');
      }
    } catch (_) {
      locale = 'zh-CN';
    }
    const candidates = [locale, locale.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en', 'zh-CN'];
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
    this._ready = true;
  },

  t(key, fallback) {
    const value = this._bundle[String(key || '')];
    return typeof value === 'string' && value ? value : (fallback || key);
  },

  scanDOM(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((element) => {
      const key = element.getAttribute('data-i18n');
      if (key) element.textContent = this.t(key, element.textContent || '');
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
