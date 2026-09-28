// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { initScrollHoverGuard, SCROLL_IDLE_MS, SCROLLING_ATTRIBUTE } from './scrollHoverGuard'

describe('initScrollHoverGuard', () => {
  let dispose: () => void

  beforeEach(() => {
    vi.useFakeTimers()
    dispose = initScrollHoverGuard()
  })

  afterEach(() => {
    dispose()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('marks the scrolling element until the scroll has been idle', () => {
    const scroller = document.createElement('div')
    document.body.append(scroller)

    scroller.dispatchEvent(new Event('scroll'))
    expect(scroller.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(true)

    vi.advanceTimersByTime(SCROLL_IDLE_MS - 10)
    scroller.dispatchEvent(new Event('scroll'))
    vi.advanceTimersByTime(SCROLL_IDLE_MS - 10)
    expect(scroller.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(true)

    vi.advanceTimersByTime(10)
    expect(scroller.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(false)
  })

  it('maps document scrolling to the root element', () => {
    document.dispatchEvent(new Event('scroll'))
    expect(document.documentElement.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(true)
  })

  it('clears pending marks and stops listening when disposed', () => {
    const scroller = document.createElement('div')
    document.body.append(scroller)
    scroller.dispatchEvent(new Event('scroll'))

    dispose()
    expect(scroller.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(false)
    scroller.dispatchEvent(new Event('scroll'))
    expect(scroller.hasAttribute(SCROLLING_ATTRIBUTE)).toBe(false)
    dispose = () => {}
  })
})
