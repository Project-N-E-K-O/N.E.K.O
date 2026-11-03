// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type Component } from 'vue'
import { createI18n } from 'vue-i18n'
import LanguageSwitcher from './LanguageSwitcher.vue'
import StatusIndicator from './StatusIndicator.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import MarketPluginCard from '../plugin/MarketPluginCard.vue'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import { PluginStatus, STATUS_COLORS } from '@/utils/constants'
import type { MarketPlugin } from '@/api/market'

vi.mock('@/i18n', () => ({
  getLocale: () => 'en-US',
  getLocaleSetting: () => 'en-US',
  localeLoadState: { pending: null, error: null },
  setLocale: vi.fn(),
}))

const cleanups: Array<() => void> = []
const passthrough = defineComponent({
  inheritAttrs: false,
  setup(_props, { attrs, slots }) {
    return () => h('div', attrs, [slots.header?.(), slots.default?.(), slots.dropdown?.()])
  },
})

async function mountControl(component: Component, props: Record<string, unknown>, locale = 'en-US') {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(component, props)
  app.use(createI18n({ legacy: false, locale, messages: { 'en-US': enUS, 'zh-CN': zhCN } }))
  for (const tag of ['el-card', 'el-tag', 'el-icon', 'el-tooltip', 'el-dropdown', 'el-dropdown-menu', 'el-dropdown-item']) {
    app.component(tag, passthrough)
  }
  app.component('el-button', defineComponent({
    inheritAttrs: false,
    setup(_props, { attrs, slots }) { return () => h('button', attrs, slots.default?.()) },
  }))
  app.mount(container)
  await nextTick()
  cleanups.push(() => { app.unmount(); container.remove() })
  return container
}

afterEach(() => { while (cleanups.length) cleanups.pop()?.() })

describe('small control accessibility', () => {
  it.each([
    ['en-US', 'Choose language'], ['zh-CN', '选择语言'],
  ])('gives the short language button a localized purpose in %s', async (locale, label) => {
    const container = await mountControl(LanguageSwitcher, {}, locale)
    const button = container.querySelector<HTMLButtonElement>('[data-testid="language-switcher"]')!
    expect(button.getAttribute('aria-label')).toBe(label)
    expect(button.getAttribute('title')).toBe(label)
    expect(button.textContent).toContain('EN')
    expect(button.getAttribute('aria-busy')).toBe('false')
  })

  it.each([
    [PluginStatus.RUNNING, 'success'], [PluginStatus.STOPPED, 'info'],
    [PluginStatus.CRASHED, 'danger'], [PluginStatus.LOADING, 'warning'],
    [PluginStatus.DISABLED, 'info'],
  ])('keeps the %s semantic color mapping', async (status, type) => {
    const container = await mountControl(StatusIndicator, { status })
    const tag = container.querySelector<HTMLElement>('.status-tag')!
    expect(tag.getAttribute('type')).toBe(type)
    expect(tag.style.getPropertyValue('--status-color')).toBe(STATUS_COLORS[status as PluginStatus])
    expect(tag.textContent?.trim()).not.toBe('')
  })

  it('announces loading once with the icon excluded from the accessible content', async () => {
    const container = await mountControl(LoadingSpinner, { loading: true, text: 'Refreshing plugins' })
    expect(container.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('Refreshing plugins')
    expect(container.querySelector('.is-loading')?.getAttribute('aria-hidden')).toBe('true')
  })
})

const marketPlugin: MarketPlugin = {
  id: 'market-fixture', rawId: 1, name: 'Market plugin', description: 'A plugin from the market',
  version: '1.1.0', author: { name: 'Author' }, tags: [], downloads: 0, likes: 0,
  created_at: '', updated_at: '', has_release: true,
}

describe('market card keyboard entry and action isolation', () => {
  it('offers the same detail action from a focusable native title', async () => {
    const onClick = vi.fn()
    const container = await mountControl(MarketPluginCard, { plugin: marketPlugin, onClick })
    const title = container.querySelector<HTMLButtonElement>('[data-testid="plugin-details-button"]')!
    expect(title.type).toBe('button')
    title.focus()
    expect(document.activeElement).toBe(title)
    title.click()
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it.each([
    [false, 'install'], [true, 'upgrade'],
  ] as const)('keeps the %s installed action separate from detail clicks', async (installed, action) => {
    const onClick = vi.fn()
    const onInstall = vi.fn()
    const onUpgrade = vi.fn()
    const container = await mountControl(MarketPluginCard, {
      plugin: marketPlugin, installed, localVersion: installed ? '1.0.0' : undefined,
      onClick, onInstall, onUpgrade,
    })
    container.querySelector<HTMLButtonElement>('.plugin-card-actions button')?.click()
    expect(action === 'install' ? onInstall : onUpgrade).toHaveBeenCalledTimes(1)
    expect(onClick).not.toHaveBeenCalled()
  })
})
