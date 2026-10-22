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
  props: Record<string, unknown> = {},
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
          ...props,
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

describe('plugin card identity', () => {
  it('shows the logical ID only when the parent identifies a display-name collision', async () => {
    const hidden = await mountEntry(PluginCard, makePlugin(null))
    expect(hidden.container.querySelector('[data-testid="plugin-identity"]')).toBeNull()

    const shown = await mountEntry(PluginCard, makePlugin(null), { showIdentity: true })
    expect(shown.container.querySelector('[data-testid="plugin-identity"]')?.textContent).toContain(
      'generic-plugin',
    )
  })
})

describe('plugin card information order', () => {
  it.each([true, false])('keeps version/source and UI actions aligned without entry counts (UI: %s)', async (hasUi) => {
    const { container } = await mountEntry(PluginCard, makePlugin(
      hasUi ? { id: 'open_ui', kind: 'ui' } : null,
    ))
    const footer = container.querySelector('.plugin-card-footer')!
    const metadata = footer.querySelector('.plugin-card-footer__main')!
    expect(metadata.querySelector('.plugin-version-tag')).not.toBeNull()
    expect(metadata.querySelector('.plugin-entries')).toBeNull()
    expect(container.textContent).not.toContain('plugins.entryPoint')
    // The action is a footer sibling of the metadata, so a card without one
    // keeps no empty action slot.
    expect(Array.from(footer.children, (child) => child.getAttribute('data-testid') || child.className))
      .toEqual(hasUi ? ['plugin-card-footer__main', 'plugin-open-ui'] : ['plugin-card-footer__main'])
  })

  it('keeps name and state in one header above the description, with the UI action in the footer', async () => {
    const action: PluginListAction = { id: 'open_ui', kind: 'ui' }
    const { container, openUi, click } = await mountEntry(PluginCard, {
      ...makePlugin(action, 'stopped'),
      autoStart: false,
    })
    const card = container.querySelector('.plugin-card')!
    expect(Array.from(card.children, (child) => child.className)).toEqual([
      'plugin-card-header', 'plugin-card-body', 'plugin-card-footer',
    ])
    expect(card.querySelector('.plugin-card-header .plugin-name')).not.toBeNull()
    expect(card.querySelector('.plugin-card-header .plugin-statuses')).not.toBeNull()
    expect(card.querySelector('.plugin-start-mode')).toBeNull()
    expect(card.textContent).not.toContain('plugins.manualStart')
    const button = card.querySelector<HTMLButtonElement>('footer [data-testid="plugin-open-ui"]')!
    button.click()
    expect(openUi).toHaveBeenCalledExactlyOnceWith(action)
    expect(click).not.toHaveBeenCalled()
  })


})

describe.each([
  ['card', PluginCard],
  ['list row', PluginListRow],
])('%s startup indicator', (_name, component) => {
  it.each([true, false, undefined])('marks only explicit automatic startup (%s) and hides entry counts', async (autoStart) => {
    const { container } = await mountEntry(component, makePlugin(null), {
      plugin: { ...makePlugin(null), autoStart, entry_count: 123 },
    })
    const badge = container.querySelector('.plugin-start-mode')
    expect(badge !== null).toBe(autoStart === true)
    if (badge) {
      expect(badge.textContent).toContain('plugins.autoStart')
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    }
    expect(container.textContent).not.toContain('plugins.manualStart')
    expect(container.textContent).not.toContain('plugins.entryPoint')
    expect(container.textContent).not.toContain('123')
  })
})

describe.each([
  ['card', PluginCard],
  ['list row', PluginListRow],
])('%s Open UI action', (_name, component) => {
  it('provides a native title button that preserves the card click action exactly once', async () => {
    const mounted = await mountEntry(component, makePlugin(null), { isSelected: true })
    const details = mounted.container.querySelector<HTMLButtonElement>(
      '[data-testid="plugin-details-button"]',
    )

    expect(details).toBeInstanceOf(HTMLButtonElement)
    expect(details?.type).toBe('button')
    expect(details?.textContent).toBe('Generic plugin')
    details?.focus()
    expect(document.activeElement).toBe(details)
    details?.click()
    await nextTick()
    expect(mounted.click).toHaveBeenCalledTimes(1)
    expect(mounted.openUi).not.toHaveBeenCalled()

    mounted.container.querySelector('.plugin-card, .plugin-list-row-card')?.dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    expect(mounted.click).toHaveBeenCalledTimes(2)
  })

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
