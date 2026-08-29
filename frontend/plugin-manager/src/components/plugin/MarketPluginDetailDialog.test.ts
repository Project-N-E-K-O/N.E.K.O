// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import MarketPluginDetailDialog from './MarketPluginDetailDialog.vue'
import type { MarketWorkbenchItem } from '@/composables/useMarketWorkbench'

const apiMocks = vi.hoisted(() => ({
  fetchMarketPlugin: vi.fn(),
  fetchMarketPluginComments: vi.fn(),
  fetchMarketPluginReadme: vi.fn(),
  fetchMarketPluginVersions: vi.fn(),
}))

vi.mock('@/api/market', () => apiMocks)
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('dompurify', () => ({ default: { sanitize: (value: string) => value } }))
vi.mock('marked', () => ({ marked: { parse: (value: string) => value } }))
vi.mock('@element-plus/icons-vue', () => ({
  Download: defineComponent(() => () => h('span')),
  Star: defineComponent(() => () => h('span')),
  User: defineComponent(() => () => h('span')),
}))

const plugin = {
  id: '15',
  rawId: 15,
  name: 'Comments plugin',
  description: 'Test plugin',
  author: { name: 'NEKO' },
  tags: [],
  downloads: 0,
  likes: 0,
  version: '1.0.0',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  has_release: true,
} as MarketWorkbenchItem

async function flush(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}

async function mountDialog() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(defineComponent(() => () => h(MarketPluginDetailDialog, {
    visible: true,
    plugin,
    channel: 'stable',
  })))
  const slotStub = defineComponent((_, { slots }) => () => h('div', slots.default?.()))
  app.component('el-dialog', slotStub)
  app.component('el-tab-pane', slotStub)
  app.component('el-tabs', defineComponent({
    emits: ['update:modelValue'],
    setup(_, { emit, slots }) {
      return () => h('div', [
        h('button', { 'data-tab': 'comments', onClick: () => emit('update:modelValue', 'comments') }),
        slots.default?.(),
      ])
    },
  }))
  app.component('el-avatar', slotStub)
  app.component('el-button', slotStub)
  app.component('el-icon', slotStub)
  app.component('el-tag', slotStub)
  app.component('el-alert', slotStub)
  app.component('el-empty', slotStub)
  app.directive('loading', {})
  app.mount(container)
  await flush()
  return {
    container,
    unmount() {
      app.unmount()
      container.remove()
    },
  }
}

describe('MarketPluginDetailDialog comments', () => {
  afterEach(() => vi.restoreAllMocks())

  it('loads comments only after the comments tab is opened', async () => {
    apiMocks.fetchMarketPlugin.mockResolvedValue({ ...plugin, id: 15 })
    apiMocks.fetchMarketPluginVersions.mockResolvedValue([])
    apiMocks.fetchMarketPluginReadme.mockResolvedValue(null)
    apiMocks.fetchMarketPluginComments.mockResolvedValue({ messages: [], next_cursor: null })

    const dialog = await mountDialog()
    expect(apiMocks.fetchMarketPluginComments).not.toHaveBeenCalled()

    ;(dialog.container.querySelector('[data-tab="comments"]') as HTMLButtonElement).click()
    await flush()
    expect(apiMocks.fetchMarketPluginComments).toHaveBeenCalledWith(15)
    dialog.unmount()
  })
})
