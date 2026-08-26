// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import {
  HOSTED_PANEL_REFRESH_GAPS_MS,
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

    const expectedCalls = frames.length * HOSTED_PANEL_REFRESH_GAPS_MS.length
    expect(refreshContext).toHaveBeenCalledTimes(expectedCalls)

    vi.useRealTimers()
  })

  it('returns without arming the delay chain when there is nothing to refresh', async () => {
    vi.useFakeTimers()

    // Awaiting directly, without advancing timers: a plugin with no hosted-tsx
    // panel must not sit through HOSTED_PANEL_REFRESH_GAPS_MS. If the
    // short-circuit is removed this await never settles and the test times out.
    await refreshHostedPanelFrames([])

    expect(vi.getTimerCount()).toBe(0)

    vi.useRealTimers()
  })

  it('does not reject when a frame throws synchronously', async () => {
    vi.useFakeTimers()
    const frames = [{
      refreshContext: () => {
        throw new Error('frame disposed')
      },
    }] as unknown as Array<{ refreshContext: () => Promise<void> }>

    const pending = refreshHostedPanelFrames(frames)
    await vi.runAllTimersAsync()

    await expect(pending).resolves.toBeUndefined()

    vi.useRealTimers()
  })

  it('reuses a single-use Map iterator across delayed refresh passes', async () => {
    vi.useFakeTimers()
    const refreshContext = vi.fn(async () => undefined)
    const frames = new Map([
      ['main', { refreshContext }],
      ['guide', { refreshContext }],
    ])

    const pending = refreshHostedPanelFrames(frames.values())
    await vi.runAllTimersAsync()
    await pending

    const expectedCalls = frames.size * HOSTED_PANEL_REFRESH_GAPS_MS.length
    expect(refreshContext).toHaveBeenCalledTimes(expectedCalls)

    vi.useRealTimers()
  })
})
