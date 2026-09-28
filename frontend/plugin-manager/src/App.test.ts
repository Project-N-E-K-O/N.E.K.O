// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import App from './App.vue'
import { localeLoadState } from './i18n'

vi.mock('./i18n', async () => {
  const { reactive } = await import('vue')
  return { elementLocale: {}, localeLoadState: reactive({ pending: 'en-US' as string | null }) }
})
vi.mock('./components/common/LocaleLoadNotice.vue', () => ({ default: { render: () => null } }))

it('gates initial locale loading but preserves route state on later language changes', async () => {
  const counts = reactive({ mounts: 0 })
  const app = createApp(App)
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
