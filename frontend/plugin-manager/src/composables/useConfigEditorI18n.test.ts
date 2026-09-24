// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { configEditorMessages } from '@/i18n/config-editor'
import { useConfigEditorI18n } from './useConfigEditorI18n'

describe('configuration editor messages', () => {
  it('registers once per app, preserves global messages and follows language changes', async () => {
    const i18n = createI18n({
      legacy: false,
      locale: 'zh-CN',
      fallbackLocale: 'zh-CN',
      messages: { 'zh-CN': { plugins: { title: '插件列表' } } },
    })
    expect(i18n.global.te('plugins.configUi.saveProfile')).toBe(false)
    const merge = vi.spyOn(i18n.global, 'mergeLocaleMessage')
    const host = document.createElement('div')
    const app = createApp({
      setup() {
        const { t } = useConfigEditorI18n()
        useConfigEditorI18n()
        return () => h('button', t('plugins.configUi.saveProfile'))
      },
    })
    app.use(i18n)
    app.mount(host)
    try {
      expect(merge).toHaveBeenCalledTimes(8)
      expect(i18n.global.t('plugins.title')).toBe('插件列表')
      for (const [locale, messages] of Object.entries(configEditorMessages)) {
        i18n.global.locale.value = locale as typeof i18n.global.locale.value
        await nextTick()
        expect(host.textContent).toBe(messages.saveProfile)
        expect(i18n.global.t('plugins.configUi.unsavedCount', { count: 3 })).toBe(
          messages.unsavedCount.replace('{count}', '3')
        )
      }
    } finally {
      app.unmount()
      merge.mockRestore()
    }
  })
})
