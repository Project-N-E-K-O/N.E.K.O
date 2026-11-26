// Open a URL in the user's system browser when running inside Electron,
// or fall back to plain window.open (new tab) in real browser contexts.
//
// Why this helper exists: target="_blank" / window.open inside Electron
// opens an embedded Chromium webview that has no close affordance —
// users get trapped. The host preload script exposes window.electronShell
// as the bridge to shell.openExternal in the main process; the same
// convention is used by static/app/app-proactive.js for url-card / meme
// links, and by frontend/react-neko-chat/src/openExternal.ts on the
// chat side. The plugin-manager surface lives in the same Electron host,
// so it needs the same routing.
//
// In browser contexts (e.g. running the manager standalone via `vite dev`)
// the electronShell global is absent and the fallback gives normal
// new-tab behavior; in Electron the IPC bridge dispatches to the system
// browser.
//
// Two safety steps before handing the URL to either path:
//
// 1. Absolutize relative inputs against window.location.href. Callers
//    sometimes pass manager-internal routes (router.resolve().href →
//    "/plugins/foo?tab=ui") expecting "open this in a new tab" — fine
//    for window.open, but shell.openExternal in Electron passes the
//    string straight to ShellExecute / xdg-open which can't resolve
//    relative paths. URL() constructor handles both absolute and
//    relative forms uniformly.
// 2. Whitelist http / https / mailto. shell.openExternal is a known
//    sharp edge (file:// could open arbitrary local content, javascript:
//    is a non-starter, data: is unsupported by most OS handlers) — only
//    schemes a sane new-tab/browser would accept get through.
const CONTROL_CHARS_RE = /[\u0000-\u001f\u007f]/

export function isSafeSameTabUrl(url: string): boolean {
  if (typeof url !== 'string') return false
  const trimmed = url.trim()
  if (!trimmed || CONTROL_CHARS_RE.test(trimmed)) return false
  let parsed: URL
  try {
    parsed = new URL(trimmed, window.location.href)
  } catch {
    return false
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) return false
  return parsed.origin === window.location.origin
}

export function isSafeAppRoute(target: string): boolean {
  if (typeof target !== 'string') return false
  const trimmed = target.trim()
  if (!trimmed || CONTROL_CHARS_RE.test(trimmed)) return false
  return trimmed.startsWith('/') && !trimmed.startsWith('//')
}

function normalizeExternalUrl(url: string): string | null {
  if (!url) return null
  let normalized: URL
  try {
    normalized = new URL(url, window.location.href)
  } catch {
    return null
  }
  if (!['http:', 'https:', 'mailto:'].includes(normalized.protocol)) return null
  return normalized.toString()
}

function externalShell(): { openExternal: (u: string) => void | Promise<unknown> } | null {
  const shell = (window as unknown as {
    electronShell?: { openExternal?: (u: string) => void | Promise<unknown> }
  }).electronShell
  return shell && typeof shell.openExternal === 'function'
    ? (shell as { openExternal: (u: string) => void | Promise<unknown> })
    : null
}

/** Keep native anchor navigation in browsers; route Electron clicks to its system browser. */
export function handleExternalLinkClick(event: MouseEvent, url: string): void {
  if (!externalShell()) return
  event.preventDefault()
  openExternalUrl(url)
}

export function openExternalUrl(url: string): void {
  const href = normalizeExternalUrl(url)
  if (!href) return
  const shell = externalShell()
  if (shell) {
    // The preload bridge may be backed by ipcRenderer.invoke (Promise<void>)
    // or ipcRenderer.send (void) — we don't control which side it's on.
    // Promise.resolve normalizes both; .catch swallows the unhandled
    // rejection that would otherwise fire if invoke rejects. We deliberately
    // do NOT fall back to window.open here: in Electron context window.open
    // is exactly the trapped-inner-webview behavior this helper exists to
    // avoid, so silently failing is the lesser evil than re-triggering the
    // bug.
    Promise.resolve(shell.openExternal(href)).catch((err) => {
      console.warn('[openExternalUrl] electronShell.openExternal failed:', err)
    })
    return
  }
  window.open(href, '_blank', 'noopener,noreferrer')
}

/**
 * For a link only known after an await (e.g. one that needs a server round
 * trip): call this synchronously in the click handler, then `open(url)` once
 * the URL is ready, or `cancel()` if it never is.
 *
 * In a browser, `window.open` after an await is no longer tied to the click
 * and is blocked as a popup, so a blank window is opened up front and pointed
 * at the URL later. `blocked` says the browser refused even that one: a later
 * `open` would be refused too, so the caller should tell the user instead of
 * doing the work behind it. Electron routes to the system browser and has no
 * such restriction, so nothing is opened until `open`.
 */
export function prepareExternalWindow(): {
  blocked: boolean
  open: (url: string) => void
  cancel: () => void
} {
  if (externalShell()) return { blocked: false, open: openExternalUrl, cancel: () => {} }
  const popup = window.open('', '_blank')
  if (popup) popup.opener = null
  return {
    blocked: popup === null,
    open(url: string) {
      const href = normalizeExternalUrl(url)
      if (!href) {
        popup?.close()
        return
      }
      if (popup && !popup.closed) popup.location.href = href
      else window.open(href, '_blank', 'noopener,noreferrer')
    },
    cancel() {
      popup?.close()
    },
  }
}

export function openLocalPath(path: string): Promise<void> {
  return openLocalPathExact(String(path || '').trim())
}

/**
 * Open exactly this path, without trimming it. Use it for a path returned by
 * `resolvePathWithinRoots`, so the bridge opens the string that was checked
 * (POSIX names may start or end with spaces).
 */
export function openVerifiedLocalPath(path: string): Promise<void> {
  return openLocalPathExact(String(path || ''))
}

function openLocalPathExact(raw: string): Promise<void> {
  if (!isLocalPath(raw)) return Promise.reject(new Error('Not a valid local path'))
  const target = normalizeLocalPath(raw)
  const host = (window as unknown as {
    nekoHost?: { openPath?: (payload: { path: string }) => void | Promise<unknown> }
    electronShell?: {
      openPath?: (path: string) => void | Promise<unknown>
      showItemInFolder?: (path: string) => void | Promise<unknown>
      openExternal?: (url: string) => void | Promise<unknown>
    }
  })
  if (host.nekoHost && typeof host.nekoHost.openPath === 'function') {
    return Promise.resolve(host.nekoHost.openPath({ path: target })).then((result) => {
      // nekoHost.openPath 可能返回 {ok: false, error: ...} 结构化失败
      if (result && typeof result === 'object' && 'ok' in result && result.ok === false) {
        const error = 'error' in result && result.error ? String(result.error) : 'Failed to open path'
        throw new Error(error)
      }
      return undefined
    })
  }
  if (host.electronShell && typeof host.electronShell.openPath === 'function') {
    return Promise.resolve(host.electronShell.openPath(target)).then((result) => {
      // Electron shell.openPath 返回 Promise<string>：空字符串表示成功，非空字符串是错误消息
      if (typeof result === 'string' && result !== '') {
        throw new Error(result)
      }
      return undefined
    })
  }
  if (host.electronShell && typeof host.electronShell.showItemInFolder === 'function') {
    return Promise.resolve(host.electronShell.showItemInFolder(target)).then(() => undefined)
  }
  if (host.electronShell && typeof host.electronShell.openExternal === 'function') {
    return Promise.resolve(host.electronShell.openExternal(localPathToFileUrl(target))).then(() => undefined)
  }
  // 纯浏览器环境：没有桌面桥接
  return Promise.reject(Object.assign(new Error('No desktop bridge available'), { code: NO_DESKTOP_BRIDGE }))
}

export const NO_DESKTOP_BRIDGE = 'no_desktop_bridge'

const WINDOWS_DEVICE_NAME_RE = /^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])(\s*\..*)?$/i

type ScopedPathStyle = 'posix' | 'windows' | 'unc'
type ScopedPath = {
  style: ScopedPathStyle
  // Root marker: '' for posix, 'c:' style drive for windows, '//host/share' for UNC.
  root: string
  segments: string[]
}

/**
 * Lexically normalize an absolute local path for scope comparison.
 *
 * Handles `file://` URLs, `/` and `\` separators, `.` / `..` segments,
 * repeated and trailing separators, Windows drive letters and UNC shares.
 * Returns null for anything that is not an absolute path or that cannot be
 * compared safely (home-relative `~`, Windows device / verbatim prefixes,
 * `..` above the root, NUL and other control characters, Windows segments
 * that the OS would silently rewrite).
 *
 * Purely lexical: symbolic links and junctions cannot be resolved here.
 */
function parseScopedPath(input: string): ScopedPath | null {
  // No trimming: spaces are part of POSIX names, and roots and targets must
  // be compared and opened exactly as given.
  const raw = normalizeLocalPath(String(input || ''))
  if (!raw || CONTROL_CHARS_RE.test(raw)) return null
  // A backslash is an ordinary file-name character in POSIX paths; only
  // drive-letter and UNC (`\\host\share`, `//host/share`) inputs treat it
  // as a separator.
  const windowsLike = /^[a-zA-Z]:([\\/]|$)/.test(raw) || /^[\\/][\\/]/.test(raw)
  const posixLiteral = raw.startsWith('/') && !windowsLike
  const slashed = posixLiteral ? raw : raw.replace(/\\/g, '/')
  let style: ScopedPathStyle
  let root: string
  let rest: string
  const drive = /^([a-zA-Z]):(?:\/|$)/.exec(slashed)
  if (drive) {
    style = 'windows'
    root = `${drive[1]!.toUpperCase()}:`
    rest = slashed.slice(2)
  } else if (slashed.startsWith('//')) {
    const parts = slashed.slice(2).split('/')
    const host = parts[0] || ''
    const share = parts[1] || ''
    // `//?/...` and `//./...` are Win32 verbatim / device namespaces.
    if (!host || !share || host === '?' || host === '.') return null
    style = 'unc'
    root = `//${host}/${share}`
    rest = parts.slice(2).join('/')
  } else if (posixLiteral) {
    style = 'posix'
    root = ''
    rest = slashed
  } else {
    return null
  }
  const segments: string[] = []
  for (const segment of rest.split('/')) {
    if (!segment || segment === '.') continue
    if (segment === '..') {
      if (!segments.length) return null
      segments.pop()
      continue
    }
    // Windows drops trailing dots/spaces and treats `:` as a stream
    // separator, so such segments do not name what they spell.
    if (style !== 'posix' && (/[. ]$/.test(segment) || segment.includes(':'))) return null
    // Windows device names (CON, NUL, COM1, ...) open a device, not a file,
    // whatever directory or extension they appear with.
    if (style !== 'posix' && WINDOWS_DEVICE_NAME_RE.test(segment)) return null
    segments.push(segment)
  }
  return { style, root, segments }
}

function scopedPathKey(path: ScopedPath): string[] {
  // Windows and UNC paths compare case-insensitively; POSIX paths exactly.
  const fold = (value: string) => (path.style === 'posix' ? value : value.toLowerCase())
  return [path.style, fold(path.root), ...path.segments.map(fold)]
}

function formatScopedPath(path: ScopedPath): string {
  if (path.style === 'posix') return `/${path.segments.join('/')}`
  const root = path.style === 'unc' ? path.root.replace(/\//g, '\\') : path.root
  return `${root}\\${path.segments.join('\\')}`
}

/**
 * Return the normalized native path when `target` equals or is inside one of
 * `roots`, comparing whole path segments; otherwise null. Roots that are
 * empty or not absolute are ignored.
 */
export function resolvePathWithinRoots(target: string, roots: ReadonlyArray<string | null | undefined>): string | null {
  const parsed = parseScopedPath(target)
  if (!parsed) return null
  const targetKey = scopedPathKey(parsed)
  for (const root of roots) {
    if (typeof root !== 'string' || !root.trim()) continue
    const parsedRoot = parseScopedPath(root)
    if (!parsedRoot) continue
    const rootKey = scopedPathKey(parsedRoot)
    if (rootKey.length > targetKey.length) continue
    if (rootKey.every((part, index) => part === targetKey[index])) return formatScopedPath(parsed)
  }
  return null
}

function isLocalPath(value: string): boolean {
  if (!value) return false
  if (/^[a-zA-Z]:[\\/]/.test(value)) return true
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value)) return value.toLowerCase().startsWith('file://')
  return value.startsWith('/') || value.startsWith('~/') || value.startsWith('\\\\')
}

function localPathToFileUrl(value: string): string {
  if (value.toLowerCase().startsWith('file://')) return value
  let normalized = value
  if (normalized.startsWith('\\\\')) {
    const parts = normalized.replace(/^\\\\+/, '').split(/[\\/]+/).filter(Boolean)
    const host = parts.shift()
    if (!host) return value
    return `file://${host}/${encodeFilePathParts(parts)}`
  }
  // Only drive-letter paths use `\` as a separator. In POSIX paths it is a
  // literal file-name character and must stay encoded (%5C), otherwise the
  // URL could name a different path than the one scope-checked.
  if (/^[a-zA-Z]:[\\/]/.test(normalized)) normalized = `/${normalized.replace(/\\/g, '/')}`
  return `file://${encodeFilePathParts(normalized.split('/'))}`
}

function normalizeLocalPath(value: string): string {
  if (!value.toLowerCase().startsWith('file://')) return value
  try {
    const parsed = new URL(value)
    const pathname = decodeURIComponent(parsed.pathname)
    if (parsed.host && parsed.host !== 'localhost') {
      return `\\\\${parsed.host}${pathname.replace(/\//g, '\\')}`
    }
    if (/^\/[a-zA-Z]:($|\/)/.test(pathname)) {
      return pathname.slice(1).replace(/\//g, '\\')
    }
    return pathname
  } catch {
    return value
  }
}

function encodeFilePathParts(parts: string[]): string {
  return parts.map((part) => (/^[a-zA-Z]:$/.test(part) ? part : encodeURIComponent(part))).join('/')
}
