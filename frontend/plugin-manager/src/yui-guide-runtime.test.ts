// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { PluginDashboardGuideRuntime } from './yui-guide-runtime'

describe('PluginDashboardGuideRuntime interrupt coordinates', () => {
  afterEach(() => {
    document.body.className = ''
    delete (window as unknown as { nekoYuiGuideDesktopBridge?: unknown }).nekoYuiGuideDesktopBridge
    vi.restoreAllMocks()
  })

  it('keeps local client coordinates for resistTo while forwarding screenX/screenY and viewport dimensions to home', async () => {
    const runtime = new PluginDashboardGuideRuntime()
    runtime.running = true
    runtime.activeSessionId = 'session-coord-1'
    runtime.interruptsEnabled = true
    runtime.cursorPosition = { x: 100, y: 100 }
    document.body.classList.add('yui-taking-over')

    const resistCalls: Array<{ x: number; y: number }> = []
    runtime.resistTo = vi.fn(async (x: number, y: number) => {
      resistCalls.push({ x, y })
    })

    const sentPayloads: Array<Record<string, unknown>> = []
    ;(window as unknown as {
      nekoYuiGuideDesktopBridge: { requestTutorialInterrupt: (payload: Record<string, unknown>) => boolean }
    }).nekoYuiGuideDesktopBridge = {
      requestTutorialInterrupt(payload) {
        sentPayloads.push(payload)
        const detail = payload.detail as { requestId?: string } | undefined
        if (detail?.requestId) {
          runtime.handleInterruptAckData({
            type: 'neko:yui-guide:plugin-dashboard:interrupt-ack',
            requestId: detail.requestId,
          })
        }
        return true
      },
    }

    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    let now = 1000
    vi.spyOn(Date, 'now').mockImplementation(() => {
      now += 90
      return now
    })

    const buildMove = (clientX: number, clientY: number, screenX: number, screenY: number) => {
      const event = new MouseEvent('mousemove', { clientX, clientY, screenX, screenY })
      Object.defineProperty(event, 'movementX', { value: 200 })
      Object.defineProperty(event, 'movementY', { value: 0 })
      return event
    }

    // Seed initial pointer point
    runtime.handleInterrupt(buildMove(120, 140, 1120, 740))

    // Trigger 8 reversals (shake gesture) across > 600ms and > 1100px/s so handleInterrupt triggers playLightResistance
    let finalClientX = 0
    let finalClientY = 0
    let finalScreenX = 0
    let finalScreenY = 0
    for (let i = 0; i < 9; i += 1) {
      finalClientX = i % 2 === 0 ? 320 : 120
      finalClientY = i % 2 === 0 ? 180 : 140
      finalScreenX = finalClientX + 1000
      finalScreenY = finalClientY + 600
      runtime.handleInterrupt(buildMove(finalClientX, finalClientY, finalScreenX, finalScreenY))
    }

    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(resistCalls).toEqual([{ x: finalClientX, y: finalClientY }])
    expect(sentPayloads).toHaveLength(1)
    const detail = sentPayloads[0]?.detail as Record<string, unknown>
    expect(detail.kind).toBe('interrupt_resist_light')
    expect(detail.x).toBe(finalClientX)
    expect(detail.y).toBe(finalClientY)
    expect(detail.screenX).toBe(finalScreenX)
    expect(detail.screenY).toBe(finalScreenY)
    expect(detail.viewportWidth).toBe(window.innerWidth)
    expect(detail.viewportHeight).toBe(window.innerHeight)
    expect(runtime.pendingInterruptAck).toBeNull()
    expect(runtime.scenePausedForResistance).toBe(false)

    runtime.cleanup()
  })
})
