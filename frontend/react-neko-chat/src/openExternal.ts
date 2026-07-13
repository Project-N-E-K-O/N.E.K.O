// Open a URL in the user's system browser when running inside Electron,
// or fall back to plain window.open (new tab) in real browser contexts.
//
// Why this helper exists: target="_blank" / window.open inside Electron
// opens an embedded Chromium webview that has no close affordance —
// users get trapped. The host preload script exposes window.electronShell
// as the bridge to shell.openExternal in the main process; the same
// convention is used by static/app/app-proactive.js for url-card / meme
// links, and by plugin surfaces (e.g. game_agent_minecraft quickstart).
//
// The chat surface renders in three contexts (index.html wide / narrow
// mobile / chat.html in Electron). In the two browser contexts the
// electronShell global is absent and the fallback gives normal new-tab
// behavior; in Electron the IPC bridge dispatches to the system browser.
//
// Two safety steps before handing the URL to either path:
//
// 1. Absolutize relative inputs against window.location.href. Markdown
//    in chat usually carries absolute URLs but the contract should be
//    forgiving — URL() constructor handles both forms uniformly, and
//    shell.openExternal can't resolve relative paths on its own
//    (passes the string straight to ShellExecute / xdg-open).
// 2. Whitelist http / https / mailto. shell.openExternal is a known
//    sharp edge (file:// could open arbitrary local content, javascript:
//    is a non-starter, data: is unsupported by most OS handlers) — only
//    schemes a sane new-tab/browser would accept get through.
export function normalizeExternalUrlHref(url: string): string | null {
  if (!url) return null;
  let normalized: URL;
  try {
    normalized = new URL(url, window.location.href);
  } catch {
    return null;
  }
  if (!['http:', 'https:', 'mailto:'].includes(normalized.protocol)) return null;
  return normalized.toString();
}

export function openExternalUrl(url: string): void {
  const href = normalizeExternalUrlHref(url);
  if (!href) return;
  const shell = (window as unknown as {
    electronShell?: { openExternal?: (u: string) => void | Promise<unknown> };
  }).electronShell;
  if (shell && typeof shell.openExternal === 'function') {
    // The preload bridge may be backed by ipcRenderer.invoke (Promise<void>)
    // or ipcRenderer.send (void) — we don't control which side it's on.
    // Promise.resolve normalizes both; .catch swallows the unhandled
    // rejection that would otherwise fire if invoke rejects. We deliberately
    // do NOT fall back to window.open here: in Electron context window.open
    // is exactly the trapped-inner-webview behavior this helper exists to
    // avoid, so silently failing is the lesser evil than re-triggering the
    // bug.
    Promise.resolve(shell.openExternal(href)).catch((err) => {
      console.warn('[openExternalUrl] electronShell.openExternal failed:', err);
    });
    return;
  }
  window.open(href, '_blank', 'noopener,noreferrer');
}
