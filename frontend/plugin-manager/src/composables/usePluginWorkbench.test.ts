import { describe, expect, it, vi } from 'vitest'

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

  it('does not load the pinyin index for an ASCII query', async () => {
    const workbench = usePluginWorkbench([
      { ...plugins[0]!, name: '插件' },
    ], { scope: 'plugin-workbench-ascii-search-test' })

    workbench.filterText.value = 'plugin'
    await Promise.resolve()
    await Promise.resolve()

    expect(safePinyin).not.toHaveBeenCalled()
  })

  it('loads pinyin only when a CJK query is entered', async () => {
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
