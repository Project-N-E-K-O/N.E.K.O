// ===== 跨窗口语言切换自动刷新 =====
// i18n-i18next.js 会通过 storage 事件检测其他窗口的语言变更并调用 changeLanguage，
// changeLanguage 触发 languageChanged → localechange 自定义事件。
// 此处监听 localechange，在 Jukebox 已打开时自动刷新 UI 文本。
if (window.__JukeboxLocaleChangeHandler) {
  window.removeEventListener('localechange', window.__JukeboxLocaleChangeHandler);
}
window.__JukeboxLocaleChangeHandler = function() {
  const Jukebox = window.Jukebox;
  if (Jukebox && Jukebox.State && (Jukebox.State.isOpen || Jukebox.State.isHidden)) {
    Jukebox.refreshLocale();
  }
};
window.addEventListener('localechange', window.__JukeboxLocaleChangeHandler);
