// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { useCollapsibleLabels } from './useCollapsibleLabels'

const resize = vi.hoisted(() => ({ callbacks: [] as ResizeObserverCallback[] }))
vi.mock('@vueuse/core', () => ({
  useResizeObserver: (_target: unknown, callback: ResizeObserverCallback) => {
    resize.callbacks.push(callback)
  },
}))

let cleanup = () => {}
async function settle() {
  await nextTick()
  // Deliver MutationObserver callbacks as well as Vue's queued patches.
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
}

async function mount(labelWidth = 700) {
  const root = document.createElement('div')
  document.body.append(root)
  const content = ref(0)
  let available = 600
  let fullWidth = labelWidth
  let layout!: ReturnType<typeof useCollapsibleLabels>
  const app = createApp(defineComponent({
    setup() {
      const host = ref<HTMLElement | null>(null)
      const group = ref<HTMLElement | null>(null)
      layout = useCollapsibleLabels(host, [group], content)
      return () => h('section', [h('div', {
        ref: host, class: { compact: layout.compact.value }, style: { padding: '0px', columnGap: '0px' },
      }, [h('div', { ref: group }, String(content.value))])])
    },
  }))
  app.mount(root)
  const scope = root.querySelector('section')!
  const host = scope.firstElementChild as HTMLElement
  const group = host.firstElementChild as HTMLElement
  const measured: number[] = []
  const width = () => host.classList.contains('compact') ? 40 : fullWidth
  Object.defineProperty(host, 'clientWidth', { get: () => available })
  Object.defineProperty(group, 'scrollWidth', { get: width })
  group.getBoundingClientRect = () => {
    const value = width()
    measured.push(value)
    return new DOMRect(0, 0, value, 32)
  }
  cleanup = () => { app.unmount(); root.remove() }
  layout.relayout()
  await settle()
  measured.length = 0
  return {
    scope, layout, measured,
    async changeContent(width: number) {
      fullWidth = width
      content.value++
      await settle()
    },
    async resize(width: number) {
      available = width
      resize.callbacks[0]!([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
      await settle()
    },
    async changeGroupWidth(width: number) {
      fullWidth = width
      resize.callbacks[1]!([], {} as ResizeObserver)
      await settle()
    },
  }
}

afterEach(() => {
  cleanup()
  cleanup = () => {}
  resize.callbacks.length = 0
})

it('keeps the current compact layout until a pending content change can be measured after motion', async () => {
  const fixture = await mount()
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.changeContent(500)
  expect(fixture.layout.compact.value).toBe(true)
  expect(fixture.measured).toEqual([])
  fixture.scope.removeAttribute('data-motion-running')
  await settle()
  expect(fixture.layout.compact.value).toBe(false)
  expect(fixture.measured).toEqual([500])
})

it('coalesces content changes and caches the latest full width for subsequent resizes', async () => {
  const fixture = await mount()
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.changeContent(500)
  await fixture.changeContent(900)
  expect(fixture.layout.compact.value).toBe(true)
  expect(fixture.measured).toEqual([])
  fixture.scope.removeAttribute('data-motion-running')
  await settle()
  expect(fixture.measured).toEqual([900])
  await fixture.resize(800)
  expect(fixture.layout.compact.value).toBe(true)
  await fixture.resize(950)
  expect(fixture.layout.compact.value).toBe(false)
})

it('waits through a replacement transition before consuming the content invalidation', async () => {
  const fixture = await mount()
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.changeContent(500)
  fixture.scope.removeAttribute('data-motion-running')
  fixture.scope.setAttribute('data-motion-running', '')
  await settle()
  expect(fixture.layout.compact.value).toBe(true)
  expect(fixture.measured).toEqual([])
  fixture.scope.removeAttribute('data-motion-running')
  await settle()
  expect(fixture.layout.compact.value).toBe(false)
})

it('still defers group resize measurements while motion is running', async () => {
  const fixture = await mount(400)
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.changeGroupWidth(900)
  expect(fixture.layout.compact.value).toBe(false)
  expect(fixture.measured).toEqual([])
  fixture.scope.removeAttribute('data-motion-running')
  await settle()
  expect(fixture.layout.compact.value).toBe(true)
})

it('preserves synchronous relayout when the drawer is measuring its target layout', async () => {
  const fixture = await mount(400)
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.resize(200)
  expect(fixture.layout.compact.value).toBe(false)
  fixture.layout.relayout()
  expect(fixture.layout.compact.value).toBe(true)
})

it('remeasures changed content immediately when no transition is running', async () => {
  const fixture = await mount()
  await fixture.changeContent(500)
  expect(fixture.layout.compact.value).toBe(false)
  expect(fixture.measured).toEqual([500])
})

it('drops pending content measurements when the component unmounts', async () => {
  const fixture = await mount()
  fixture.scope.setAttribute('data-motion-running', '')
  await fixture.changeContent(500)
  cleanup()
  cleanup = () => {}
  fixture.scope.removeAttribute('data-motion-running')
  await settle()
  expect(fixture.measured).toEqual([])
})
