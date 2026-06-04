import { describe, expect, it, vi } from 'vitest'

import { usePluginWorkbench } from './usePluginWorkbench'
import type { PluginMeta } from '@/types/api'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ locale: { value: 'zh-CN' } }),
}))

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
})
