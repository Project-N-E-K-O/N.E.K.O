// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { configEditorMessages } from '@/i18n/config-editor'
import { useConfigEditorI18n } from './useConfigEditorI18n'

function setup() {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh-CN',
    fallbackLocale: 'zh-CN',
    messages: { 'zh-CN': { plugins: { title: '插件列表' } } },
  })
  const mount = () => {
    const host = document.createElement('div')
    const app = createApp({
      setup() {
        const { t } = useConfigEditorI18n()
        return () => h('button', t('plugins.configUi.saveProfile'))
      },
    })
    app.use(i18n)
    app.mount(host)
    return { host, unmount: () => app.unmount() }
  }
  // What the app's locale loader does: replace the whole dictionary, then switch.
  const applyLocale = async (locale: string, messages: Record<string, unknown>) => {
    i18n.global.setLocaleMessage(locale, messages as never)
    i18n.global.locale.value = locale as never
    await nextTick()
  }
  return { i18n, mount, applyLocale }
}

describe('configuration editor messages', () => {
  it('keeps global messages and resolves the editor bundle', () => {
    const { i18n, mount } = setup()
    expect(i18n.global.te('plugins.configUi.saveProfile')).toBe(false)
    const editor = mount()
    try {
      expect(editor.host.textContent).toBe(configEditorMessages['zh-CN'].saveProfile)
      expect(i18n.global.t('plugins.title')).toBe('插件列表')
    } finally {
      editor.unmount()
    }
  })

  it('survives a locale bundle replacing the dictionary while the editor is mounted', async () => {
    const { mount, applyLocale } = setup()
    const editor = mount()
    try {
      for (const [locale, messages] of Object.entries(configEditorMessages)) {
        await applyLocale(locale, { plugins: { title: locale } })
        expect(editor.host.textContent).toBe(messages.saveProfile)
      }
    } finally {
      editor.unmount()
    }
  })

  it('restores the messages when the same language is reloaded', async () => {
    const { mount, applyLocale } = setup()
    const editor = mount()
    try {
      await applyLocale('en-US', { plugins: { title: 'Plugins' } })
      // Re-applying the current language replaces its dictionary without a switch.
      await applyLocale('en-US', { plugins: { title: 'Plugins' } })
      expect(editor.host.textContent).toBe(configEditorMessages['en-US'].saveProfile)
    } finally {
      editor.unmount()
    }
  })

  it('keeps following replacements after the first editor unmounts', async () => {
    const { mount, applyLocale } = setup()
    mount().unmount()
    const editor = mount()
    try {
      await applyLocale('ja', { plugins: { title: 'プラグイン' } })
      expect(editor.host.textContent).toBe(configEditorMessages.ja.saveProfile)
    } finally {
      editor.unmount()
    }
  })
})
