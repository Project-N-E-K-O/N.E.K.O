// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type Component } from 'vue'
import { createI18n } from 'vue-i18n'
import PluginCard from './PluginCard.vue'
import PluginListRow from './PluginListRow.vue'
import en from '@/i18n/locales/en-US'
import es from '@/i18n/locales/es'
import ja from '@/i18n/locales/ja'
import ko from '@/i18n/locales/ko'
import pt from '@/i18n/locales/pt'
import ru from '@/i18n/locales/ru'
import zhCN from '@/i18n/locales/zh-CN'
import zhTW from '@/i18n/locales/zh-TW'
import type { PluginInstallSourceChannel, PluginMeta } from '@/types/api'

vi.mock('@/stores/marketVersions', () => ({
  useMarketVersionsStore: () => ({ latest: () => null }),
}))

const messages = { 'en-US': en, es, ja, ko, pt, ru, 'zh-CN': zhCN, 'zh-TW': zhTW }
const mountedApps: Array<() => void> = []

async function mountEntry(component: Component, extra: Partial<PluginMeta>, locale = 'zh-CN') {
  const plugin: PluginMeta = {
    id: 'demo', name: 'Demo', description: '', version: '0.1.0', status: 'running',
    ...extra,
  }
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(component, { plugin })
  app.use(createI18n({ legacy: false, locale, messages }))
  const passthrough = defineComponent({
    setup(_props, { slots }) {
      return () => h('div', [slots.header?.(), slots.default?.()])
    },
  })
  for (const name of ['el-card', 'el-button', 'el-tag', 'el-icon']) {
    app.component(name, passthrough)
  }
  app.mount(container)
  mountedApps.push(() => {
    app.unmount()
    container.remove()
  })
  await nextTick()
  return container
}

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.()
})

describe.each([
  ['card', PluginCard],
  ['list row', PluginListRow],
])('%s source label', (_name, component) => {
  it.each([
    ['zh-CN', '工作台'],
    ['zh-TW', '工作台'],
    ['en-US', 'Workbench'],
    ['es', 'Banco de trabajo'],
    ['ja', 'ワークベンチ'],
    ['ko', '워크벤치'],
    ['pt', 'Bancada'],
    ['ru', 'Мастерская'],
  ])('shows a workbench project without an installation record in %s', async (locale, label) => {
    const container = await mountEntry(component, {
      source: 'project',
      install_source: { source: 'unknown', reason: null, installed_at: null, source_detail: null },
    }, locale)

    expect(container.querySelector('.source-tag__label')?.textContent).toBe(label)
    expect(container.querySelector('.source-tag__icon svg')).not.toBeNull()
  })

  it('shows a workbench project when installation metadata is absent', async () => {
    const container = await mountEntry(component, { source: 'project' })
    expect(container.querySelector('.source-tag__label')?.textContent).toBe('工作台')
  })

  it.each([
    ['builtin', '内置'], ['manual', '手动'], ['imported', '导入'], ['market', '市场'],
  ] satisfies Array<[PluginInstallSourceChannel, string]>)('preserves the %s installation label', async (source, label) => {
    const container = await mountEntry(component, {
      source: 'user',
      install_source: { source, reason: null, installed_at: null, source_detail: null },
    })
    expect(container.querySelector('.source-tag__label')?.textContent).toBe(label)
  })

  it('keeps unknown non-project sources hidden', async () => {
    const container = await mountEntry(component, {
      source: 'user',
      install_source: { source: 'unknown', reason: null, installed_at: null, source_detail: null },
    })
    expect(container.querySelector('.source-tag-group')).toBeNull()
  })
})
