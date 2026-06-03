import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { usePackageManager } from './usePackageManager'
import type { PluginCliPluginRef } from '@/api/pluginCli'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'zh-CN' },
    t: (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
  }),
}))

const pluginRef: PluginCliPluginRef = {
  root_id: 'builtin',
  directory_name: 'demo_plugin',
  plugin_id: 'demo_plugin',
  label: 'Demo Plugin',
}

vi.mock('@/api/pluginCli', () => ({
  getPluginCliPlugins: vi.fn(async () => ({
    plugins: [],
    plugin_refs: [pluginRef],
  })),
  getPluginCliPackages: vi.fn(async () => ({
    packages: [],
    target_dir: '',
  })),
  analyzePluginBundle: vi.fn(),
  inspectPluginPackage: vi.fn(),
  buildPluginCli: vi.fn(),
  installPluginPackage: vi.fn(),
  verifyPluginPackage: vi.fn(),
}))

vi.mock('@/stores/plugin', () => ({
  usePluginStore: () => ({
    pluginsWithStatus: [
      {
        id: 'demo_plugin',
        name: 'Demo Plugin',
        description: '',
        version: '0.1.0',
        type: 'plugin',
      },
    ],
    syncRegistryAndFetch: vi.fn(async () => ({})),
  }),
}))

vi.mock('@/utils/request', () => ({
  formatHttpError: (error: unknown) => String(error),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

describe('usePackageManager external plugin selection', () => {
  it('maps plugin list selections to package build targets', async () => {
    const selectedFromPluginList = ref(['demo_plugin'])
    const manager = usePackageManager({
      externalSelectedPluginIds: computed(() => selectedFromPluginList.value),
    })

    await manager.refreshPluginSources()

    expect(manager.selectedPluginIds.value).toEqual(['builtin:demo_plugin'])
    expect(manager.resolvedBuildTargets.value).toEqual(['builtin:demo_plugin'])
  })
})
