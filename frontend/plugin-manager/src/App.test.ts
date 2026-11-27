// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { createApp, defineComponent, getCurrentInstance, h, nextTick, reactive, type Component } from 'vue'
import App from './App.vue'
import { localeLoadState } from './i18n'
import { createI18n } from 'vue-i18n'
import { usePluginConsentState } from './stores/pluginConsentState'

const consentLoader = vi.hoisted(() => ({ load: vi.fn() }))
vi.mock('./components/common/deferredPanel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components/common/deferredPanel')>()
  return {
    ...actual,
    // Keep the real pending/error/recovery boundary; control only module I/O.
    deferredPanel: () => actual.deferredPanel(() => consentLoader.load()),
  }
})

vi.mock('./i18n', async () => {
  const { reactive } = await import('vue')
  return { elementLocale: {}, localeLoadState: reactive({ pending: 'en-US' as string | null }) }
})
vi.mock('./components/common/LocaleLoadNotice.vue', () => ({ default: { render: () => null } }))

const apps: ReturnType<typeof createApp>[] = []
beforeEach(() => {
  consentLoader.load.mockReset()
  localeLoadState.pending = 'en-US'
})
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()) })

function mountConsentApp() {
  localeLoadState.pending = null
  const app = createApp(App)
  const pinia = createPinia()
  app.use(pinia)
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false }))
  app.component('el-config-provider', defineComponent({ setup: (_, { slots }) => () => slots.default?.() }))
  app.component('router-view', defineComponent({ setup: () => () => h('main') }))
  const host = document.createElement('div')
  app.mount(host)
  apps.push(app)
  return { host, store: usePluginConsentState(pinia) }
}

function deferredModule() {
  let resolve!: (value: { default: Component }) => void
  const promise = new Promise<{ default: Component }>((done) => { resolve = done })
  return { promise, resolve }
}

it('gates initial locale loading but preserves route state on later language changes', async () => {
  const counts = reactive({ mounts: 0 })
  const app = createApp(App)
  app.use(createPinia())
  app.config.globalProperties.$t = (key: string) => key
  app.component('el-config-provider', defineComponent({ setup: (_, { slots }) => () => slots.default?.() }))
  app.component('router-view', defineComponent({ setup() { counts.mounts++; return () => h('input') } }))
  const host = document.createElement('div')
  app.mount(host)
  try {
    expect(counts.mounts).toBe(0)
    localeLoadState.pending = null
    await nextTick()
    const input = host.querySelector('input')!
    input.value = 'unsaved draft'
    localeLoadState.pending = 'ja'
    await nextTick()
    expect(host.querySelector('input')).toBe(input)
    expect(input.value).toBe('unsaved draft')
    expect(counts.mounts).toBe(1)
  } finally { app.unmount() }
})

it('loads consent only on request and closes a failed loader without approving', async () => {
  consentLoader.load.mockRejectedValueOnce(new Error('Synthetic module load failure'))
  const { host, store } = mountConsentApp()
  expect(consentLoader.load).not.toHaveBeenCalled()
  const first = store.requestConfirmation([{ id: 'first', name: 'First' }])
  await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Synthetic module load failure'))
  const close = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === 'common.close')!
  close.click()
  await expect(first).resolves.toBe(false)
  await nextTick()
  expect(store.activeRequest).toBeNull()
  expect(host.querySelector('.deferred-panel')).toBeNull()

  const loaded = defineComponent({ emits: ['close'], setup: (_, { emit }) => () => h('button', { onClick: () => emit('close') }, 'loaded consent') })
  consentLoader.load.mockResolvedValueOnce({ default: loaded })
  const second = store.requestConfirmation([{ id: 'second', name: 'Second' }])
  await vi.waitFor(() => expect(host.textContent).toContain('loaded consent'))
  host.querySelector('button')!.click()
  await expect(second).resolves.toBe(false)
  expect(consentLoader.load).toHaveBeenCalledTimes(2)
})

it('ignores a late close from the previous request after the queue advances', async () => {
  const closes: (() => void)[] = []
  const loaded = defineComponent({
    emits: ['close'],
    setup: () => {
      // Retain the delivered host listener, as a late transition callback can;
      // emitting from an already-unmounted Vue stub would be a no-op.
      closes.push(getCurrentInstance()!.parent!.vnode.props!.onClose as () => void)
      return () => h('span', 'loaded consent')
    },
  })
  consentLoader.load.mockResolvedValue({ default: loaded })
  const { host, store } = mountConsentApp()
  const first = store.requestConfirmation([{ id: 'first', name: 'First' }])
  const second = store.requestConfirmation([{ id: 'second', name: 'Second' }])
  await vi.waitFor(() => expect(host.textContent).toContain('loaded consent'))
  store.resolveConfirmation(false)
  await expect(first).resolves.toBe(false)
  await vi.waitFor(() => expect(closes).toHaveLength(2))
  const current = store.activeRequest
  closes[0]!()
  await nextTick()
  expect(store.activeRequest).toBe(current)
  expect(store.activeRequest?.plugins[0]?.id).toBe('second')
  closes[1]!()
  await expect(second).resolves.toBe(false)
})

it('does not mount an old pending loader after its request has been cancelled', async () => {
  const pending = deferredModule()
  consentLoader.load.mockReturnValue(pending.promise)
  const { host, store } = mountConsentApp()
  const first = store.requestConfirmation([{ id: 'first', name: 'First' }])
  const second = store.requestConfirmation([{ id: 'second', name: 'Second' }])
  await vi.waitFor(() => expect(host.querySelector('[role="status"]')).not.toBeNull())
  host.querySelector('button')!.click()
  await expect(first).resolves.toBe(false)
  await vi.waitFor(() => expect(store.activeRequest?.plugins[0]?.id).toBe('second'))
  const mounted: string[] = []
  pending.resolve({ default: defineComponent({
    emits: ['close'],
    setup: (_, { emit }) => {
      mounted.push(store.activeRequest!.plugins[0]!.id)
      return () => h('button', { onClick: () => emit('close') }, 'loaded consent')
    },
  }) })
  await vi.waitFor(() => expect(host.textContent).toContain('loaded consent'))
  expect(mounted).toEqual(['second'])
  expect(store.activeRequest?.plugins[0]?.id).toBe('second')
  host.querySelector('button')!.click()
  await expect(second).resolves.toBe(false)
})
