/** Inject key: parent PluginDetail refreshes hosted panel iframes after runtime start/stop/reload. */
export const PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY = Symbol('pluginDetailRefreshHostedPanels')

/** Plugin process + UI context may need a moment before actions appear. */
export const HOSTED_PANEL_REFRESH_DELAYS_MS = [0, 600, 1500] as const

/**
 * Best-effort refresh: never rejects, so callers can fire it without awaiting.
 * Returns early when there is nothing to refresh — a plugin with no hosted-tsx
 * panel must not pay for the delay chain.
 */
export async function refreshHostedPanelFrames(
  frames: Iterable<{ refreshContext: () => Promise<void> }>,
  delaysMs: readonly number[] = HOSTED_PANEL_REFRESH_DELAYS_MS,
): Promise<void> {
  const frameList = Array.from(frames)
  if (frameList.length === 0) return
  for (const delayMs of delaysMs) {
    if (delayMs > 0) {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, delayMs)
      })
    }
    // A frame that throws synchronously would escape Promise.allSettled, so
    // route every call through a promise first.
    await Promise.allSettled(
      frameList.map((frame) => Promise.resolve().then(() => frame.refreshContext())),
    )
  }
}
