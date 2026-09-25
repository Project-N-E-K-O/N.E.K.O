import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { usePluginWorkbench } from './usePluginWorkbench'
import { useGridWorkbench } from './useGridWorkbench'
import type { PluginMeta } from '@/types/api'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ locale: { value: 'zh-CN' } }),
}))

const safePinyin = vi.hoisted(() => vi.fn((value: string, pattern: 'pinyin' | 'first') => {
  if (value === '插件' && pattern === 'pinyin') return 'cha jian'
  if (value === '插件' && pattern === 'first') return 'cj'
  return ''
}))

vi.mock('@/utils/pinyinSearch', () => ({ safePinyin }))

const plugins: PluginMeta[] = [
  {
    id: 'demo_plugin',
    name: 'Demo Plugin',
    description: '',
    version: '0.1.0',
    type: 'plugin',
  },
]

describe('usePluginWorkbench scoped selection state', () => {
  it('reuses normalized and indexed item objects while query state changes', () => {
    const workbench = usePluginWorkbench(plugins, { scope: 'plugin-workbench-index-reuse-test' })
    const first = workbench.items.value[0]

    workbench.filterText.value = 'demo'

    expect(workbench.items.value[0]).toBe(first)
    expect(workbench.filteredItems.value[0]).toBe(first)
  })

  it('rebuilds the normalized item when the source object changes', () => {
    const source = ref([...plugins])
    const workbench = usePluginWorkbench(source, { scope: 'plugin-workbench-index-update-test' })
    const first = workbench.items.value[0]

    source.value[0] = { ...source.value[0]!, description: 'updated' }

    expect(workbench.items.value[0]).not.toBe(first)
    expect(workbench.items.value[0]?.description).toBe('updated')
  })

  it('keeps package manager selection isolated from the main plugin list', () => {
    const mainWorkbench = usePluginWorkbench(plugins)
    const packagePlugin: PluginMeta = {
      ...plugins[0]!,
      id: 'user:demo_plugin',
    }
    const packageWorkbench = usePluginWorkbench(
      [packagePlugin],
      { scope: 'plugin-package-workbench-test' },
    )

    mainWorkbench.setSelectedPluginIds(['demo_plugin'])
    packageWorkbench.setSelectedPluginIds(['user:demo_plugin'])

    expect(mainWorkbench.selectedPluginIds.value).toEqual(['demo_plugin'])
    expect(packageWorkbench.selectedPluginIds.value).toEqual(['user:demo_plugin'])

    packageWorkbench.setSelectedPluginIds([])

    expect(mainWorkbench.selectedPluginIds.value).toEqual(['demo_plugin'])
    expect(packageWorkbench.selectedPluginIds.value).toEqual([])
  })

  it('matches a Chinese name on the first Latin pinyin query', async () => {
    const workbench = usePluginWorkbench([
      { ...plugins[0]!, name: '插件' },
    ], { scope: 'plugin-workbench-ascii-search-test' })

    workbench.filterText.value = 'chajian'
    await vi.waitFor(() => {
      expect(workbench.filteredItems.value.map(item => item.id)).toEqual(['demo_plugin'])
    })
  })

  it('also builds the index for a CJK query', async () => {
    const workbench = useGridWorkbench([{ id: 'plugin' }], {
      scope: 'grid-workbench-cjk-search-test',
      groups: [{ id: 'all', predicate: () => true }],
      buildPinyinSearchIndex: (_item, search) => search('插件', 'pinyin'),
      defaults: { filterText: '插件' },
    })
    await vi.waitFor(() => {
      void workbench.filteredItems.value
      expect(safePinyin).toHaveBeenCalled()
    }, { timeout: 1000 })
  })
})
