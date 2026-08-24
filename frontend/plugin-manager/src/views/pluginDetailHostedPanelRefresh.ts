/** Inject key: parent PluginDetail refreshes hosted panel iframes after runtime start/stop/reload. */
export const PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY = Symbol('pluginDetailRefreshHostedPanels')

/** Plugin process + UI context may need a moment before actions appear. */
export const HOSTED_PANEL_REFRESH_DELAYS_MS = [0, 600, 1500] as const

export async function refreshHostedPanelFrames(
  frames: Iterable<{ refreshContext: () => Promise<void> }>,
  delaysMs: readonly number[] = HOSTED_PANEL_REFRESH_DELAYS_MS,
): Promise<void> {
  for (const delayMs of delaysMs) {
    if (delayMs > 0) {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, delayMs)
      })
    }
    await Promise.allSettled(Array.from(frames, (frame) => frame.refreshContext()))
  }
}
