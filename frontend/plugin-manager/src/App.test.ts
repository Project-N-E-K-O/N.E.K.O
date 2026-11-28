// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { createApp, defineComponent, getCurrentInstance, h, nextTick, reactive, type Component } from 'vue'
import App from './App.vue'
import { localeLoadState } from './i18n'
import { createI18n } from 'vue-i18n'
import { usePermissionStartDialog } from './stores/permissionStartDialog'
import { panel } from './utils/permissionTab.fixtures'

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
  return { host, store: usePermissionStartDialog(pinia) }
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

const ask = (store: ReturnType<typeof usePermissionStartDialog>, pluginName: string) =>
  store.ask({
    panel: panel(),
    pending: { first_use: [], incomplete: [] },
    request: { kind: 'first', items: ['system.network:connect'] },
    pluginName,
  })
// The dialog module is loaded once per page: every test after the first load sees this one.
const results: Array<(value: { start: boolean; changed: boolean }) => void> = []
const dialogStub = defineComponent({
  props: { panel: Object, pending: Object, request: Object, pluginName: String, start: Boolean },
  emits: ['result', 'close'],
  setup: (props, { emit }) => {
    results.push(getCurrentInstance()!.vnode.props!.onResult as (value: { start: boolean; changed: boolean }) => void)
    return () => [
      h('span', `dialog ${props.pluginName} ${props.start}`),
      h('button', { 'data-test': 'start', onClick: () => emit('result', { start: true, changed: false }) }),
      h('button', { 'data-test': 'skip', onClick: () => emit('result', { start: false, changed: false }) }),
    ]
  },
})

it('starts when the dialog module fails and its box is closed', async () => {
  consentLoader.load.mockRejectedValueOnce(new Error('Synthetic module load failure'))
  const { host, store } = mountConsentApp()
  expect(consentLoader.load).not.toHaveBeenCalled()
  const request = ask(store, 'First')
  await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Synthetic module load failure'))
  const close = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === 'common.close')!
  close.click()
  await expect(request).resolves.toBe(true)
  await nextTick()
  expect(store.active).toBeNull()
})

it('shows the loaded start dialog and answers with the user\'s choice', async () => {
  consentLoader.load.mockResolvedValue({ default: dialogStub })
  const { host, store } = mountConsentApp()
  const first = ask(store, 'First')
  await vi.waitFor(() => expect(host.textContent).toContain('dialog First true'))
  ;(host.querySelector('[data-test="start"]') as HTMLElement).click()
  await expect(first).resolves.toBe(true)
  await nextTick()
  expect(host.textContent).not.toContain('dialog')

  const second = ask(store, 'Second')
  await vi.waitFor(() => expect(host.textContent).toContain('dialog Second'))
  ;(host.querySelector('[data-test="skip"]') as HTMLElement).click()
  await expect(second).resolves.toBe(false)
})

it('ignores a late answer from a dialog that was replaced', async () => {
  consentLoader.load.mockResolvedValue({ default: dialogStub })
  const { host, store } = mountConsentApp()
  results.length = 0
  const first = ask(store, 'First')
  await vi.waitFor(() => expect(host.textContent).toContain('dialog First'))
  const second = ask(store, 'Second')
  await expect(first).resolves.toBe(false)
  await vi.waitFor(() => expect(host.textContent).toContain('dialog Second'))
  results[0]!({ start: true, changed: false })
  await nextTick()
  expect(store.active?.pluginName).toBe('Second')
  results.at(-1)!({ start: true, changed: false })
  await expect(second).resolves.toBe(true)
})
