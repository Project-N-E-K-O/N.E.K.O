import type { startPluginDashboardTutorial as StartTutorial } from './yui-guide-runtime'
import { retryableModule } from './utils/retryableModule'

const messagePrefix = 'neko:yui-guide:plugin-dashboard:'
const desktopEvents = ['neko:yui-guide:desktop-interrupt-ack', 'neko:yui-guide:desktop-narration-finished', 'neko:yui-guide:desktop-system-cursor-temporary-reveal']
type Runtime = typeof import('./yui-guide-runtime')
let loading: Promise<Runtime> | null = null
let ready = false
let installed = false
let generation = 0
let handoffAbandoned = false
const importRuntime = retryableModule(() => import('./yui-guide-runtime'), 8000)
const pending: Event[] = []

// Queue original Event objects (including source/origin). The runtime performs
// its existing authorization checks on replay; do not reconstruct MessageEvents.
function receive(event: Event) {
  if (ready || handoffAbandoned) return
  if (event instanceof MessageEvent) {
    if (!window.opener || event.source !== window.opener) return
    if (typeof event.data?.type !== 'string' || !event.data.type.startsWith(messagePrefix)) return
  }
  if (pending.length >= 64) return
  pending.push(event)
  void loadRuntime().catch(error => console.warn('Tutorial runtime failed to load', error))
}
async function loadRuntime(): Promise<Runtime> {
  if (loading) return loading
  const epoch = generation
  loading = importRuntime().then(runtime => {
    if (epoch !== generation) return runtime
    if (handoffAbandoned) runtime.initPluginDashboardYuiGuideRuntime([], { preactivate: false, acceptOpenerMessages: false })
    else runtime.initPluginDashboardYuiGuideRuntime(pending.splice(0))
    ready = true
    return runtime
  }).catch(error => {
    if (epoch === generation) {
      // Release main.ts's mount barrier. Never replay delayed takeover messages
      // into the page after the user has regained control; a local button click
      // may explicitly retry loading the optional tutorial later.
      handoffAbandoned = true
      generation += 1
      pending.length = 0
      loading = null
    }
    throw error
  })
  return loading
}

export function hasPendingTutorialHandoff(): boolean {
  try {
    const token = JSON.parse(localStorage.getItem('neko_yui_guide_handoff_token') || 'null')
    return !!(token && token.token_version === 1 && typeof token.flow_id === 'string'
      && token.flow_id.trim() && token.target_page === 'plugin_dashboard' && token.consumed !== true
      && Number.isFinite(token.expires_at) && token.expires_at > Date.now())
  } catch { return false }
}

/** No heavy tutorial import on ordinary boot (including management popups).
 * Only pending tutorial handoffs preload before mount to preserve the overlay. */
export function initTutorialBootstrap(): Promise<unknown> | undefined {
  if (installed) return
  installed = true
  window.addEventListener('message', receive)
  for (const type of desktopEvents) window.addEventListener(type, receive, true)
  window.addEventListener('pagehide', () => {
    generation++; ready = false; loading = null; pending.length = 0
  }, true)
  window.addEventListener('pageshow', event => {
    if (event.persisted && window.opener && !handoffAbandoned) void loadRuntime().catch(console.warn)
  })
  if (window.opener && !window.opener.closed && hasPendingTutorialHandoff()) return loadRuntime()
}

export async function startPluginDashboardTutorial(...args: Parameters<typeof StartTutorial>) {
  const epoch = generation
  const runtime = await loadRuntime()
  if (epoch === generation) runtime.startPluginDashboardTutorial(...args)
}
