// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import {
  HOSTED_PANEL_REFRESH_DELAYS_MS,
  refreshHostedPanelFrames,
} from './pluginDetailHostedPanelRefresh'

describe('refreshHostedPanelFrames', () => {
  it('refreshes all frames immediately and after configured delays', async () => {
    vi.useFakeTimers()
    const refreshContext = vi.fn(async () => undefined)
    const frames = [{ refreshContext }, { refreshContext }]

    const pending = refreshHostedPanelFrames(frames)
    await vi.runAllTimersAsync()
    await pending

    const expectedCalls = frames.length * HOSTED_PANEL_REFRESH_DELAYS_MS.length
    expect(refreshContext).toHaveBeenCalledTimes(expectedCalls)

    vi.useRealTimers()
  })
})
