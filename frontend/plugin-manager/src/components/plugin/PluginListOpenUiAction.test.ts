// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type Component } from 'vue'
import PluginCard from './PluginCard.vue'
import PluginListRow from './PluginListRow.vue'
import type { PluginListAction, PluginMeta } from '@/types/api'

vi.mock('vue-i18n', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-i18n')>()),
  useI18n: () => ({
    locale: { value: 'zh-CN' },
    t: (key: string) => key,
  }),
}))

vi.mock('@/stores/marketVersions', () => ({
  useMarketVersionsStore: () => ({ latest: () => null }),
}))

const mountedApps: Array<() => void> = []

function makePlugin(
  action: PluginListAction | null,
  status = 'running',
): PluginMeta & { status: string; autoStart: boolean } {
  return {
    id: 'generic-plugin',
    name: 'Generic plugin',
    description: 'A generic plugin',
    version: '1.0.0',
    status,
    autoStart: true,
    entries: [],
    list_actions: action ? [action] : [],
  }
}

async function mountEntry(
  component: Component,
  plugin: ReturnType<typeof makePlugin>,
) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const click = vi.fn()
  const openUi = vi.fn()
  const app = createApp(defineComponent({
    setup() {
      return () =>
        h(component, {
          plugin,
          enableUiAction: true,
          onClick: click,
          onOpenUi: openUi,
        })
    },
  }))
  const passthrough = defineComponent({
    inheritAttrs: false,
    setup(_props, { attrs, slots }) {
      return () => h('div', attrs, [slots.header?.(), slots.default?.()])
    },
  })
  const button = defineComponent({
    inheritAttrs: false,
    setup(_props, { attrs, slots }) {
      return () => h('button', attrs, slots.default?.())
    },
  })
  app.component('el-card', passthrough)
  app.component('el-button', button)
  app.component('el-tag', passthrough)
  app.component('StatusIndicator', passthrough)
  app.component('PluginMetricsInline', passthrough)
  app.component('SourceTag', passthrough)
  app.component('SourceDetailRow', passthrough)
  app.mount(container)
  await nextTick()
  mountedApps.push(() => {
    app.unmount()
    container.remove()
  })
  return { container, click, openUi }
}

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.()
  vi.restoreAllMocks()
})

describe.each([
  ['card', PluginCard],
  ['list row', PluginListRow],
])('%s Open UI action', (_name, component) => {
  it('shows the shared i18n action only for an available UI action', async () => {
    const available = await mountEntry(component, makePlugin({
      id: 'open_ui',
      kind: 'ui',
      target: '/plugin/generic/ui/',
    }))
    expect(available.container.querySelector('[data-testid="plugin-open-ui"]')?.textContent).toContain(
      'plugins.ui.open',
    )

    available.container.querySelector('[data-testid="plugin-open-ui"]')?.dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    await nextTick()
    expect(available.openUi).toHaveBeenCalledTimes(1)
    expect(available.openUi).toHaveBeenCalledWith(expect.objectContaining({ kind: 'ui' }))
    expect(available.click).not.toHaveBeenCalled()
  })

  it.each(['url', 'route'] as const)(
    'also exposes URL-backed open_ui actions with kind %s',
    async (kind) => {
      const available = await mountEntry(component, makePlugin({
        id: 'open_ui',
        kind,
        target: '/plugin/generic/ui/',
      }))

      available.container.querySelector('[data-testid="plugin-open-ui"]')?.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      )
      await nextTick()
      expect(available.openUi).toHaveBeenCalledWith(expect.objectContaining({ kind }))
      expect(available.click).not.toHaveBeenCalled()
    },
  )

  it.each([
    ['missing', null, 'running'],
    ['wrong id', { id: 'docs', kind: 'url' } satisfies PluginListAction, 'running'],
    ['wrong kind', { id: 'open_ui', kind: 'builtin' } satisfies PluginListAction, 'running'],
    ['disabled', { id: 'open_ui', kind: 'ui', disabled: true } satisfies PluginListAction, 'running'],
    [
      'requires running',
      { id: 'open_ui', kind: 'ui', requires_running: true } satisfies PluginListAction,
      'stopped',
    ],
  ])('hides the action when it is %s', async (_case, action, status) => {
    const mounted = await mountEntry(component, makePlugin(action, status))
    expect(mounted.container.querySelector('[data-testid="plugin-open-ui"]')).toBeNull()
  })
})

describe('plugin list UI-action wiring contract', () => {
  it('wires the grid event to a dedicated handler without replacing card detail clicks', async () => {
    const gridSource = await import('./PluginGridSection.vue?raw').then((module) => module.default)
    const listSource = await import('@/views/PluginList.vue?raw').then((module) => module.default)

    expect(gridSource).toContain(':enable-ui-action="true"')
    expect(gridSource).toContain("$emit('item-open-ui', item, $event)")
    expect(gridSource).toContain("$emit('item-click', item.id)")
    expect(listSource).toContain('@item-open-ui="handlePluginUiAction"')
    expect(listSource).toContain('await executeAction(resolvedAction, plugin)')
    expect(listSource).toContain("query: { tab: 'ui' }")
    expect(listSource).toContain('await router.push(fallback)')
    expect(listSource).toContain('openExternalUrl(router.resolve(fallback).href)')
    expect(listSource).not.toContain('study_companion')
  })

  it('routes direct hold-confirmed UI actions through the shared danger dialog', async () => {
    const listSource = await import('@/views/PluginList.vue?raw').then((module) => module.default)
    const handler = listSource.slice(
      listSource.indexOf('async function handlePluginUiAction'),
      listSource.indexOf('function toggleMultiSelectMode'),
    )

    expect(handler).toContain('if (shouldUseHoldConfirm(resolvedAction))')
    expect(handler).toContain('openDangerDialog(resolvedAction, plugin)')
    expect(handler.indexOf('openDangerDialog(resolvedAction, plugin)')).toBeLessThan(
      handler.indexOf('await executeAction(resolvedAction, plugin)'),
    )
  })
})
