// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import PluginSelectorPanel from './PluginSelectorPanel.vue'
import type { PluginMeta } from '@/types/api'
import type enUS from '@/i18n/locales/en-US'

vi.mock('@/stores/marketVersions', () => ({
  useMarketVersionsStore: () => ({ latest: () => null }),
}))

const locales = import.meta.glob<{ default: typeof enUS }>(
  '../../i18n/locales/*.ts', { eager: true },
)
const messages = Object.fromEntries(Object.entries(locales).map(([path, module]) => [
  path.match(/\/([^/]+)\.ts$/)![1], module.default,
]))
const cleanups: Array<() => void> = []
const plugin: PluginMeta & { type: 'plugin' } = {
  id: 'selectable', name: 'Selectable plugin', description: 'A local plugin',
  version: '1.0.0', entries: [], type: 'plugin',
}

const passthrough = defineComponent({
  inheritAttrs: false,
  setup(_props, { attrs, slots }) {
    return () => h('div', attrs, [slots.header?.(), slots.default?.()])
  },
})

async function mountPanel(locale = 'en-US', overrides: Record<string, unknown> = {}) {
  const toggle = vi.fn()
  const container = document.createElement('div')
  document.body.appendChild(container)
  const i18n = createI18n({ legacy: false, locale, messages, missingWarn: false, fallbackWarn: false })
  const app = createApp(PluginSelectorPanel, {
    loading: false, totalCount: 1, selectedCount: 0, pluginFilter: '',
    selectedTypes: ['plugin'], layoutMode: 'compact',
    pluginCount: 1, adapterCount: 0, filteredPurePlugins: [plugin], filteredAdapters: [],
    selectedPluginIds: [], onTogglePlugin: toggle, ...overrides,
  })
  app.use(i18n)
  for (const tag of ['el-card', 'el-tag', 'el-radio-group', 'el-radio-button',
    'el-checkbox-group', 'el-checkbox-button', 'el-input', 'el-icon']) {
    app.component(tag, passthrough)
  }
  app.component('el-checkbox', defineComponent({
    inheritAttrs: false,
    props: ['modelValue'],
    emits: ['change'],
    setup(props, { attrs, emit }) {
      return () => h('input', {
        ...attrs, type: 'checkbox', checked: props.modelValue,
        onChange: () => emit('change', !props.modelValue),
      })
    },
  }))
  app.component('el-button', defineComponent({
    inheritAttrs: false,
    setup(_props, { attrs, slots }) { return () => h('button', attrs, slots.default?.()) },
  }))
  app.component('el-empty', defineComponent({
    props: ['description'],
    setup(props) { return () => h('div', { 'data-empty': true }, props.description) },
  }))
  app.mount(container)
  await nextTick()
  cleanups.push(() => { app.unmount(); container.remove() })
  return { container, toggle, t: i18n.global.t }
}

afterEach(() => { while (cleanups.length) cleanups.pop()?.() })

describe('local plugin selector polish', () => {
  it.each(Object.keys(messages))('localizes labels and empty state in %s', async (locale) => {
    const { container, t } = await mountPanel(locale, {
      totalCount: 0, pluginCount: 0, filteredPurePlugins: [],
    })
    for (const key of ['plugins.localPlugins', 'plugins.selectedCount', 'plugins.pluginsSection',
      'plugins.adaptersSection', 'plugins.layoutList', 'plugins.layoutSingle', 'plugins.layoutDouble',
      'plugins.layoutCompact', 'plugins.selectAllVisible', 'plugins.clearSelection', 'common.refresh',
      'plugins.noLocalMatches']) {
      expect(container.textContent).toContain(t(key, { count: 0 }))
    }
    for (const key of ['common.filter', 'plugins.layoutLabel', 'plugins.pluginType']) {
      expect(container.querySelector(`[aria-label="${t(key)}"]`)).not.toBeNull()
    }
  })

  it('keeps both title-button and ordinary card clicks as a single selection action', async () => {
    const { container, toggle } = await mountPanel()
    const title = container.querySelector<HTMLButtonElement>('[data-testid="plugin-details-button"]')!
    title.focus()
    expect(document.activeElement).toBe(title)
    title.click()
    expect(toggle).toHaveBeenCalledExactlyOnceWith('selectable')
    toggle.mockClear()
    container.querySelector('.plugin-card')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(toggle).toHaveBeenCalledExactlyOnceWith('selectable')
  })

  it('offers labeled checkbox and native title controls in list mode without duplicate toggles', async () => {
    const { container, toggle } = await mountPanel('en-US', {
      layoutMode: 'list', selectedPluginIds: ['selectable'], selectedCount: 1,
    })
    const title = container.querySelector<HTMLButtonElement>('.plugin-list-row__name')!
    expect(title.getAttribute('aria-pressed')).toBe('true')
    title.click()
    expect(toggle).toHaveBeenCalledExactlyOnceWith('selectable')
    toggle.mockClear()
    const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    expect(checkbox.getAttribute('aria-label')).toContain('Selectable plugin')
    checkbox.click()
    expect(toggle).toHaveBeenCalledExactlyOnceWith('selectable')
  })

  it('announces first loading without presenting a false empty state', async () => {
    const { container } = await mountPanel('en-US', { loading: true, filteredPurePlugins: [] })
    expect(container.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('Loading...')
    expect(container.querySelector('[data-empty]')).toBeNull()
  })

  it('keeps existing selections visible while a refresh is in progress', async () => {
    const { container } = await mountPanel('en-US', { loading: true })
    expect(container.querySelector('[data-testid="plugin-details-button"]')?.textContent).toBe('Selectable plugin')
    expect(container.querySelector('[data-empty]')).toBeNull()
    expect(container.querySelector('.selector-card')?.getAttribute('aria-busy')).toBe('true')
  })
})
