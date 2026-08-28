// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import PluginUIFrame from './PluginUIFrame.vue'

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api', () => ({ get: apiMocks.get }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('@element-plus/icons-vue', () => ({
  InfoFilled: defineComponent(() => () => h('span')),
  Loading: defineComponent(() => () => h('span')),
  WarningFilled: defineComponent(() => () => h('span')),
}))

async function mountFrame() {
  const openSurface = vi.fn()
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(defineComponent(() => () => h(PluginUIFrame, {
    pluginId: 'study_companion',
    onOpenSurface: openSurface,
  })))
  const stub = defineComponent(() => () => h('div'))
  app.component('el-icon', stub)
  app.component('el-button', stub)
  app.mount(container)
  await Promise.resolve()
  await nextTick()
  const iframe = container.querySelector('iframe') as HTMLIFrameElement
  iframe.dispatchEvent(new Event('load'))
  return {
    iframe,
    dispatch(payload: Record<string, unknown>) {
      window.dispatchEvent(new MessageEvent('message', {
        data: { type: 'neko-study-open-surface', payload },
        origin: window.location.origin,
        source: iframe.contentWindow,
      }))
    },
    openSurface,
    unmount() {
      app.unmount()
      container.remove()
    },
  }
}

describe('PluginUIFrame open-surface bridge', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    ;(window as unknown as { happyDOM: { settings: { disableIframePageLoading: boolean } } })
      .happyDOM.settings.disableIframePageLoading = true
    apiMocks.get.mockReset()
    apiMocks.get.mockResolvedValue({ has_ui: true })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('preserves native modal dialogs for legacy static plugin UIs', async () => {
    const frame = await mountFrame()

    expect(frame.iframe.getAttribute('sandbox')?.split(/\s+/)).toContain('allow-modals')
    frame.unmount()
  })

  it('passes through only a non-negative safe activation revision', async () => {
    const frame = await mountFrame()

    frame.dispatch({
      surfaceId: 'practice',
      kind: 'panel',
      activationRevision: 7,
      prompt: 'free-form text must not cross this bridge',
    })
    frame.dispatch({ surfaceId: 'practice', activationRevision: '8' })
    frame.dispatch({ surfaceId: 'practice', activationRevision: -1 })

    expect(frame.openSurface).toHaveBeenNthCalledWith(1, {
      pluginId: undefined,
      surfaceId: 'practice',
      kind: 'panel',
      activationRevision: 7,
    })
    expect(frame.openSurface).toHaveBeenNthCalledWith(2, {
      pluginId: undefined,
      surfaceId: 'practice',
      kind: undefined,
    })
    expect(frame.openSurface).toHaveBeenNthCalledWith(3, {
      pluginId: undefined,
      surfaceId: 'practice',
      kind: undefined,
    })
    frame.unmount()
  })
})
