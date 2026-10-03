// Translations come from the shared static/i18n-i18next.js bootstrap, which owns
// language detection (server uiLanguage, ?lang, Steam, stored choice) and the
// LOCALE_VERSION cache-bust. This module only reads the airBasketball subtree.
const NAMESPACE = 'airBasketball.';
const I18N_READY_TIMEOUT_MS = 8000;

function sharedI18nSettled() {
  // window.t is exported only once the shared bootstrap has initialized or
  // fallen back, right before it dispatches `localechange`.
  return typeof window.t === 'function';
}

await new Promise(resolve => {
  if (sharedI18nSettled()) {
    resolve();
    return;
  }
  const timer = setTimeout(done, I18N_READY_TIMEOUT_MS);
  function done() {
    clearTimeout(timer);
    window.removeEventListener('localechange', done);
    resolve();
  }
  window.addEventListener('localechange', done);
});

function i18nInstance() {
  const instance = window.i18n;
  return instance?.isInitialized && typeof instance.t === 'function' ? instance : null;
}

function scopedKey(key) {
  const value = String(key || '').trim();
  return value.startsWith(NAMESPACE) ? value.slice(NAMESPACE.length) : value;
}

function rawMessage(key) {
  const instance = i18nInstance();
  const fullKey = `${NAMESPACE}${scopedKey(key)}`;
  if (!instance || (typeof instance.exists === 'function' && !instance.exists(fullKey))) return null;
  const value = instance.t(fullKey, { returnObjects:true });
  return value === fullKey ? null : value;
}

// Locale strings use single-brace `{name}` placeholders, which i18next leaves untouched.
function interpolate(value, params = {}) {
  return typeof value === 'string'
    ? value.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`)
    : value;
}

function translatedText(key, params) {
  const value = rawMessage(key);
  return typeof value === 'string' ? interpolate(value, params) : null;
}

export function t(key, params) {
  const normalized = scopedKey(key);
  return translatedText(normalized, params) ?? interpolate(normalized, params);
}

export function voiceLines(key, params) {
  const value = rawMessage(key);
  const lines = Array.isArray(value) ? value : [value];
  return lines
    .filter(line => typeof line === 'string' && line.trim())
    .map(line => interpolate(line, params));
}

export function voiceLine(key, params) {
  const lines = voiceLines(key, params);
  return lines[Math.floor(Math.random() * lines.length)] || '';
}

window.addEventListener('localechange', () => applyTranslations());

export function applyTranslations(root = document) {
  const language = i18nInstance()?.language;
  if (language) document.documentElement.lang = language;
  root.querySelectorAll('[data-i18n]').forEach(node => {
    const value = translatedText(node.dataset.i18n);
    if (value !== null) node.textContent = value;
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(node => {
    const value = translatedText(node.dataset.i18nAriaLabel);
    if (value !== null) node.setAttribute('aria-label', value);
  });
  root.querySelectorAll('[data-i18n-label]').forEach(node => {
    const value = translatedText(node.dataset.i18nLabel);
    if (value !== null) node.dataset.label = value;
  });
}
