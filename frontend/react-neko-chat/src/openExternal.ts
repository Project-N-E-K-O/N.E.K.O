// Open a URL in the user's system browser when running inside Electron,
// or fall back to plain window.open (new tab) in real browser contexts.
//
// Why this helper exists: target="_blank" / window.open inside Electron
// opens an embedded Chromium webview that has no close affordance —
// users get trapped. The host preload script exposes window.electronShell
// as the bridge to shell.openExternal in the main process; the same
// convention is used by static/app-proactive.js for url-card / meme
// links, and by plugin surfaces (e.g. game_agent_minecraft quickstart).
//
// The chat surface renders in three contexts (index.html wide / narrow
// mobile / chat.html in Electron). In the two browser contexts the
// electronShell global is absent and the fallback gives normal new-tab
// behavior; in Electron the IPC bridge dispatches to the system browser.
export function openExternalUrl(url: string): void {
  if (!url) return;
  const shell = (window as unknown as { electronShell?: { openExternal?: (u: string) => void } }).electronShell;
  if (shell && typeof shell.openExternal === 'function') {
    shell.openExternal(url);
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}
