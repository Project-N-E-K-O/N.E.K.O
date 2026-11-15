// @vitest-environment happy-dom
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { deferredPanel } from './deferredPanel'

const apps: ReturnType<typeof createApp>[] = []
afterEach(() => { apps.splice(0).forEach(app => app.unmount()) })

function mount(component: ReturnType<typeof defineComponent>) {
  const host = document.createElement('div')
  const app = createApp(component)
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false }))
  apps.push(app)
  app.mount(host)
  return host
}

it('mounts preloaded content in the first patch and keeps attrs and close events live', async () => {
  const content = defineComponent({
    props: { active: Boolean },
    emits: ['close'],
    setup: (props, { emit }) => () => h('button', { onClick: () => emit('close') }, String(props.active)),
  })
  const loader = vi.fn(async () => ({ default: content }))
  const Panel = deferredPanel(loader)
  await Panel.preload()
  const active = ref(true)
  const close = vi.fn()
  const host = mount(defineComponent({ setup: () => () => h(Panel, { active: active.value, onClose: close }) }))
  expect(host.querySelector('button')?.textContent).toBe('true')
  expect(host.querySelector('.deferred-panel')).toBeNull()
  active.value = false
  await nextTick()
  expect(host.querySelector('button')?.textContent).toBe('false')
  host.querySelector('button')!.click()
  expect(close).toHaveBeenCalledOnce()
  expect(loader).toHaveBeenCalledOnce()
})

it('does not cache a rejected preload or block the mounted retry', async () => {
  const content = defineComponent({ setup: () => () => h('span', 'recovered') })
  const loader = vi.fn().mockRejectedValueOnce(new Error('temporary failure')).mockResolvedValue({ default: content })
  const Panel = deferredPanel(loader)
  await expect(Panel.preload()).rejects.toThrow('temporary failure')
  const host = mount(Panel)
  await vi.waitFor(() => expect(host.textContent).toBe('recovered'))
  expect(loader).toHaveBeenCalledTimes(2)
})
