const SUPPORTED_LOCALES = new Set([
  'en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'
]);

function normalizeLocale(rawLocale) {
  const raw = String(rawLocale || '').trim().replaceAll('_', '-');
  if (!raw) return 'zh-CN';
  if (SUPPORTED_LOCALES.has(raw)) return raw;

  const lower = raw.toLowerCase();
  if (lower === 'zh' || lower.startsWith('zh-hans') || lower.startsWith('zh-cn') || lower.startsWith('zh-sg')) {
    return 'zh-CN';
  }
  if (lower.startsWith('zh-hant') || lower.startsWith('zh-tw') || lower.startsWith('zh-hk') || lower.startsWith('zh-mo')) {
    return 'zh-TW';
  }

  const base = lower.split('-')[0];
  return SUPPORTED_LOCALES.has(base) ? base : 'zh-CN';
}

function detectLocale() {
  let saved = '';
  try {
    saved = localStorage.getItem('i18nextLng') || localStorage.getItem('language') || '';
  } catch (_) { /* Storage is optional. */ }
  return normalizeLocale(saved || navigator.language || 'zh-CN');
}

async function loadLocale(locale) {
  const assetVersion = new URL(import.meta.url).search;
  const response = await fetch(`/static/locales/${locale}.json${assetVersion}`, { cache:'no-store' });
  if (!response.ok) throw new Error(`locale ${locale} returned ${response.status}`);
  const payload = await response.json();
  return payload?.airBasketball && typeof payload.airBasketball === 'object'
    ? payload.airBasketball
    : null;
}

const locale = detectLocale();
let messages = null;
try {
  messages = await loadLocale(locale);
} catch (error) {
  console.warn('[air_basketball] locale load failed', error);
}
if (!messages && locale !== 'zh-CN') {
  try {
    messages = await loadLocale('zh-CN');
  } catch (error) {
    console.warn('[air_basketball] fallback locale load failed', error);
  }
}
messages ||= {};

function scopedKey(key) {
  const value = String(key || '').trim();
  return value.startsWith('airBasketball.') ? value.slice('airBasketball.'.length) : value;
}

function interpolate(value, params = {}) {
  return typeof value === 'string'
    ? value.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`)
    : value;
}

export function t(key, params) {
  const normalized = scopedKey(key);
  const value = messages[normalized];
  return interpolate(typeof value === 'string' ? value : normalized, params);
}

export function voiceLines(key, params) {
  const value = messages[scopedKey(key)];
  const lines = Array.isArray(value) ? value : [value];
  return lines
    .filter(line => typeof line === 'string' && line.trim())
    .map(line => interpolate(line, params));
}

export function voiceLine(key, params) {
  const lines = voiceLines(key, params);
  return lines[Math.floor(Math.random() * lines.length)] || '';
}

export function applyTranslations(root = document) {
  document.documentElement.lang = locale;
  root.querySelectorAll('[data-i18n]').forEach(node => {
    node.textContent = t(node.dataset.i18n);
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(node => {
    node.setAttribute('aria-label', t(node.dataset.i18nAriaLabel));
  });
  root.querySelectorAll('[data-i18n-label]').forEach(node => {
    node.dataset.label = t(node.dataset.i18nLabel);
  });
}
