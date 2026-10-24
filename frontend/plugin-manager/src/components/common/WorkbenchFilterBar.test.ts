// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import WorkbenchFilterBar from './WorkbenchFilterBar.vue'

let cleanup = () => {}
let anchor = { left: 270, top: 200, bottom: 232, right: 310, width: 40, height: 32 }
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
async function mount(mouse = false) {
  const updates: string[] = []
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(WorkbenchFilterBar, {
    filterText: 'demo', rulesTriggerLabel: 'Rules', rulesTitle: 'Filter rules',
    ruleGroups: [{ key: 'state', title: 'State', rules: [{ token: 'is:running', label: 'Running' }, { token: 'is:stopped', label: 'Stopped' }] }],
    'onUpdate:filterText': (value: string) => updates.push(value),
  })
  for (const name of ['ElIcon']) {
    app.component(name, defineComponent({ setup: () => () => h('span') }))
  }
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  const trigger = root.querySelector('.wb-filter-bar__rules-trigger') as HTMLButtonElement
  trigger.focus()
  trigger.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: mouse ? 1 : 0 }))
  await settle()
  return { root, trigger, updates, panel: document.querySelector('.wb-filter-rules-dropdown') as HTMLElement }
}
beforeEach(() => {
  anchor = { left: 270, top: 200, bottom: 232, right: 310, width: 40, height: 32 }
  vi.stubGlobal('innerWidth', 320)
  vi.stubGlobal('innerHeight', 500)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const rect = this.classList.contains('wb-filter-bar__rules-anchor') ? anchor : { left: 0, top: 0, bottom: 600, right: 400, width: 400, height: 600 }
    return { ...rect, x: rect.left, y: rect.top, toJSON: () => ({}) } as DOMRect
  })
})
afterEach(() => {
  cleanup(); cleanup = () => {}
  // Vue may leave a teleported node in its exit transition for one frame.
  document.querySelectorAll('.wb-filter-rules-dropdown').forEach(node => node.remove())
  vi.restoreAllMocks(); vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('rules disclosure', () => {
  it('constrains its width and scrollable height to the viewport and corrects both edges', async () => {
    const { panel } = await mount()
    expect(panel.style.width).toBe('304px')
    expect(panel.style.left).toBe('8px')
    expect(parseFloat(panel.style.top) + parseFloat(panel.style.maxHeight)).toBeLessThanOrEqual(492)
    expect(panel.style.overflowY).toBe('auto')
    anchor = { ...anchor, left: -100, bottom: 490, top: 458 }
    window.dispatchEvent(new Event('resize'))
    await settle()
    expect(panel.style.left).toBe('8px')
    expect(parseFloat(panel.style.top)).toBe(8)
    expect(parseFloat(panel.style.maxHeight)).toBe(442)
  })
  it('connects the disclosure, focuses rules, and closes on Escape with focus restored', async () => {
    const { trigger, panel } = await mount()
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(trigger.getAttribute('aria-controls')).toBe(panel.id)
    expect(panel.getAttribute('aria-label')).toBe('Filter rules')
    expect(document.activeElement).toBe(panel.querySelector('button'))
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(document.activeElement).toBe(trigger)
    await settle()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)
  })
  it('returns to the search field after selecting a token', async () => {
    const { root, panel, updates } = await mount()
    ;(panel.querySelector('button') as HTMLButtonElement).click()
    await settle()
    expect(updates).toEqual(['demo is:running'])
    expect(document.activeElement).toBe(root.querySelector('input'))
  })
  it('preserves the desktop width and flips above a trigger near the bottom', async () => {
    vi.stubGlobal('innerWidth', 1200)
    anchor = { ...anchor, top: 460, bottom: 492 }
    const { panel } = await mount()
    expect(panel.style.width).toBe('400px')
    expect(panel.style.left).toBe('270px')
    expect(panel.style.top).toBe('8px')
    expect(panel.style.maxHeight).toBe('444px')
  })
  it('closes on hover departure but does not interrupt keyboard focus in the rules', async () => {
    vi.useFakeTimers()
    const { trigger, panel } = await mount(true)
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 1000, clientY: 1000 }))
    await vi.advanceTimersByTimeAsync(1000)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await settle()
    const opened = document.getElementById(trigger.getAttribute('aria-controls')!)!
    ;(opened.querySelector('button') as HTMLButtonElement).focus()
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 1000, clientY: 1000 }))
    await vi.advanceTimersByTimeAsync(1000)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(panel).toBeTruthy()
  })
  it('returns through the trigger with Shift+Tab and into search with Tab after the last rule', async () => {
    const { root, trigger, panel } = await mount()
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    await settle()
    expect(document.activeElement).toBe(trigger)
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await settle()
    const opened = document.getElementById(trigger.getAttribute('aria-controls')!)!
    const buttons = opened.querySelectorAll('button')
    ;(buttons[buttons.length - 1] as HTMLButtonElement).focus()
    opened.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await settle()
    expect(document.activeElement).toBe(root.querySelector('input'))
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
  })
  it('does not steal search focus when hover departure closes the rules', async () => {
    vi.useFakeTimers()
    const { root, trigger } = await mount(true)
    const input = root.querySelector('input')!
    input.focus()
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 1000, clientY: 1000 }))
    await vi.advanceTimersByTimeAsync(1000)
    await settle()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(input)
  })
  it('does not steal focus from an outside field when dismissing on an outside click', async () => {
    const { root, trigger } = await mount(true)
    const input = root.querySelector('input')!
    input.focus()
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await settle()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(input)
  })
})
