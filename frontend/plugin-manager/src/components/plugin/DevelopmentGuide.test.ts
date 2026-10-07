// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import DevelopmentGuide from './DevelopmentGuide.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
const key = 'neko.pluginDevelopment.guide.v1.dismissed'
let cleanup = () => {}
function mountGuide() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(DevelopmentGuide)
  app.component('ElButton', defineComponent({ emits: ['click'], setup: (_, { emit, slots }) => () => h('button', { onClick: () => emit('click') }, slots.default?.()) }))
  app.component('ElIcon', defineComponent({ setup: (_, { slots }) => () => h('span', slots.default?.()) }))
  app.component('ElDialog', defineComponent({
    props: ['modelValue'], emits: ['update:modelValue'],
    setup: (props, { slots, emit }) => () => props.modelValue ? h('div', { role: 'dialog' }, [
      h('button', { onClick: () => emit('update:modelValue', false) }, 'close'),
      slots.default?.(), slots.footer?.(),
    ]) : null,
  }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  return root
}
function click(root: Element, text: string) {
  const button = [...root.querySelectorAll('button')].find((item) => item.textContent === text)
  expect(button).toBeDefined()
  button!.click()
}
beforeEach(() => localStorage.removeItem(key))
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.removeItem(key) })

describe('development mode introduction', () => {
  it('opens on first visit, remembers dismissal, and remains available from the help button', async () => {
    let root = mountGuide()
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).not.toBeNull()
    expect(root.querySelectorAll('li')).toHaveLength(3)
    click(root, 'development.guideDismiss')
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).toBeNull()
    expect(localStorage.getItem(key)).toBe('1')
    cleanup()
    root = mountGuide()
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).toBeNull()
    click(root, 'development.guideButton')
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).not.toBeNull()
  })
  it('remembers dismissal through the dialog close control', async () => {
    const root = mountGuide()
    await nextTick()
    click(root, 'close')
    await nextTick()
    expect(localStorage.getItem(key)).toBe('1')
    expect(root.querySelector('[role="dialog"]')).toBeNull()
  })
  it('remains usable when client storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    const root = mountGuide()
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).not.toBeNull()
    click(root, 'development.guideDismiss')
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).toBeNull()
    click(root, 'development.guideButton')
    await nextTick()
    expect(root.querySelector('[role="dialog"]')).not.toBeNull()
  })
})
