// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import PluginUIFrame from './PluginUIFrame.vue'
import en from '@/i18n/locales/en-US'
import es from '@/i18n/locales/es'
import ja from '@/i18n/locales/ja'
import ko from '@/i18n/locales/ko'
import pt from '@/i18n/locales/pt'
import ru from '@/i18n/locales/ru'
import zhCN from '@/i18n/locales/zh-CN'
import zhTW from '@/i18n/locales/zh-TW'

const messages = { 'en-US': en, es, ja, ko, pt, ru, 'zh-CN': zhCN, 'zh-TW': zhTW }

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api', () => ({ get: apiMocks.get }))
vi.mock('@element-plus/icons-vue', () => ({
  InfoFilled: defineComponent(() => () => h('span')),
  Loading: defineComponent(() => () => h('span')),
  WarningFilled: defineComponent(() => () => h('span')),
}))

async function mountFrame(locale = 'zh-CN', { dispatchIframeLoad = true } = {}) {
  const openSurface = vi.fn()
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(defineComponent(() => () => h(PluginUIFrame, {
    pluginId: 'study_companion',
    onOpenSurface: openSurface,
  })))
  app.use(createI18n({ legacy: false, locale, messages }))
  const stub = defineComponent({
    setup(_props, { slots }) {
      return () => h('div', [slots.default?.()])
    },
  })
  app.component('el-icon', stub)
  app.component('el-button', stub)
  app.mount(container)
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
  const iframe = container.querySelector('iframe') as HTMLIFrameElement
  if (dispatchIframeLoad) {
    iframe.dispatchEvent(new Event('load'))
  }
  return {
    container,
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

  it.each([
    ['zh-CN', '重试'],
    ['zh-TW', '重試'],
    ['en-US', 'Retry'],
    ['ja', '再試行'],
    ['ko', '다시 시도'],
    ['ru', 'Повторить'],
    ['es', 'Reintentar'],
    ['pt', 'Tentar novamente'],
  ])('renders localized common.retry button when /ui-info fails in %s', async (locale, expectedLabel) => {
    apiMocks.get.mockRejectedValueOnce(new Error('network error'))
    const frame = await mountFrame(locale, { dispatchIframeLoad: false })
    await nextTick()

    const retryText = frame.container.querySelector('.error-overlay')?.textContent || ''
    expect(retryText).toContain(expectedLabel)
    expect(retryText).not.toContain('common.retry')
    frame.unmount()
  })
})
